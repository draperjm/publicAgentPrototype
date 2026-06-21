# Plan: Step 5 Cross-Chunk Extraction Completeness

**Date:** 2026-03-22
**File:** `agent 05/document_extractor.py` → `_extract_chunked_file()`
**Problem:** Large drawings split into 12 quadrant chunks lose content that spans chunk boundaries — notably the NOTES section (`sub-step-extract-siteplan-notes`) which can span 4 adjacent chunks, with each chunk only extracting its visible portion.

---

## Root Cause Analysis

The current three-phase pipeline in `_extract_chunked_file`:

```
Phase 1  →  Each chunk processed independently by Gemini vision
             Prompt says: "only report information visible in THIS chunk"
             Result: partial notes, partial text at every boundary

Phase 2  →  Raw text concatenated, data fragments first-value-wins merged
             Problem: no awareness of which chunks are adjacent

Phase 3  →  Single consolidation LLM call with combined_raw[:9000]
             Problem: 9000 char limit may exclude later chunks;
                      no instruction to reassemble spanning content
```

**Effect on notes:** A NOTES block on a large-format drawing split into a 3×4 grid produces 12 chunks. Notes 1–3 may be in the bottom-left chunk, notes 4–6 cut across the bottom-center/bottom-right boundary, notes 7–9 in a different row entirely. Each chunk extracts only what is fully visible in its quadrant, producing 4 partial note lists that never get joined.

---

## Solution Overview — Three Targeted Changes

### Change 1 — Continuation Markers in Per-Chunk Prompts

**Location:** `_extract_chunked_file()` → Phase 1 per-chunk prompt
**What:** Tell Gemini to signal when extracted text is cut off at a chunk boundary.

Add to the per-chunk prompt:
```
Important: This image is one region of a larger drawing split into a grid.
- If text is cut off at the RIGHT or BOTTOM edge of this image (continues in an adjacent chunk),
  append "→" to that line or entry.
- If text starts abruptly at the LEFT or TOP edge (continuing from a previous chunk),
  prepend "←" to that line or entry.
- For the 'raw_text' field: capture ALL legible text, even if partially cut off at edges.
  Do not omit text just because it is partially obscured at the boundary.
```

**Why this helps:** The continuation markers give Phase 3 precise signals about which text fragments need to be stitched together. Extracting partial text (rather than suppressing it) ensures the content reaches the consolidation pass.

---

### Change 2 — Adjacent Chunk Boundary Stitching Pass (New Phase 1.5)

**Location:** New function `_stitch_adjacent_chunks()` called between Phase 1 and Phase 2.
**What:** After individual chunk processing, group chunks into adjacent pairs/triplets per page and run a focused multi-image Gemini call to assemble content that spans the boundary.

#### 2a — Multi-image vision function

Add `_llm_call_vision_multi(prompt, image_bytes_list)`:
```python
def _llm_call_vision_multi(prompt: str, image_bytes_list: list[bytes]) -> Any:
    """Call Gemini with multiple inline PNG images. Used for boundary stitching."""
    model = genai.GenerativeModel(VISION_MODEL)
    parts = [prompt]
    for img_bytes in image_bytes_list:
        img = PIL.Image.open(io.BytesIO(img_bytes))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        parts.append({"mime_type": "image/png", "data": buf.getvalue()})
    response = model.generate_content(parts)
    return _parse_json_robust(response.text)
```

#### 2b — Boundary stitching logic

```python
def _stitch_adjacent_chunks(chunks, chunk_results, sub_step_ids):
    """
    For each adjacent pair of chunks on the same page, run a focused vision call
    to extract content that spans the boundary between them.
    Returns a list of stitched fragments: [{"chunk_ids": [...], "stitched_text": "...", "data": {...}}]
    """
    # Sort by (page_number, sequence)
    ordered = sorted(
        [(c, cr) for c, cr in zip(chunks, chunk_results) if cr.get("extracted")],
        key=lambda x: (x[0].get("page_number", 1), x[0].get("sequence", 0))
    )

    stitched = []
    for i in range(len(ordered) - 1):
        chunk_a, result_a = ordered[i]
        chunk_b, result_b = ordered[i + 1]

        # Only stitch chunks on the same page
        if chunk_a.get("page_number") != chunk_b.get("page_number"):
            continue

        # Only stitch if either chunk has a continuation marker in its raw_text
        raw_a = (result_a.get("extracted") or {}).get("raw_text", "")
        raw_b = (result_b.get("extracted") or {}).get("raw_text", "")
        if "→" not in raw_a and "←" not in raw_b:
            continue  # No boundary content detected — skip this pair

        fpath_a = chunk_a.get("filepath", "")
        fpath_b = chunk_b.get("filepath", "")
        if not (fpath_a and fpath_b and Path(fpath_a).exists() and Path(fpath_b).exists()):
            continue

        schema = "{\n" + ",\n".join(f'  "{sid}": assembled value or null' for sid in sub_step_ids) + "\n}"
        stitch_prompt = (
            f"These two images are ADJACENT regions of the same engineering drawing page.\n"
            f"Image 1 is region '{chunk_a.get('region')}', Image 2 is region '{chunk_b.get('region')}'.\n\n"
            f"Known partial text from Image 1 (may be cut off):\n{raw_a[-800:]}\n\n"
            f"Known partial text from Image 2 (may continue from Image 1):\n{raw_b[:800]}\n\n"
            f"Task: Identify any text that is split across the boundary between these two regions.\n"
            f"Assemble the complete, uninterrupted text for any entries that span this boundary.\n\n"
            f"Return JSON with two keys:\n"
            f"  'stitched_raw': the complete assembled text for any boundary-spanning content,\n"
            f"  'data': {schema}\n"
            f"Only extract values that you can now see MORE COMPLETELY by having both images.\n"
            f"Return null for fields that are not at this boundary."
        )

        try:
            img_a = Path(fpath_a).read_bytes()
            img_b = Path(fpath_b).read_bytes()
            result = _llm_call_vision_multi(stitch_prompt, [img_a, img_b])
            stitched.append({
                "chunk_ids":    [chunk_a.get("chunk_id"), chunk_b.get("chunk_id")],
                "stitched_raw": result.get("stitched_raw", ""),
                "data":         result.get("data", {}),
            })
        except Exception as e:
            logger.warning(f"Boundary stitch failed for {chunk_a.get('chunk_id')}/{chunk_b.get('chunk_id')}: {e}")

    return stitched
```

#### 2c — Integrate stitched results into Phase 2

After calling `_stitch_adjacent_chunks()`, inject stitched text and data into the existing aggregation:

```python
# After Phase 1, before Phase 2 aggregation:
stitched_results = _stitch_adjacent_chunks(chunks, chunk_results, _sub_step_ids)

# In Phase 2 raw text collection, append stitched text:
for sr in stitched_results:
    if sr.get("stitched_raw"):
        raw_text_parts.append(f"[STITCHED {'-'.join(sr['chunk_ids'])}] {sr['stitched_raw']}")

# In Phase 2 data merge, prefer stitched values over single-chunk values:
for sr in stitched_results:
    for k, v in (sr.get("data") or {}).items():
        if v is not None:
            merged_data[k] = v  # Stitched value overwrites first-chunk-wins
```

**Why this helps:** For the NOTES sub-step, this directly assembles the split content by showing Gemini both sides of each boundary simultaneously. A notes block split across the bottom-center and bottom-right chunks will be completely recovered in the stitch pass.

---

### Change 3 — Improved Consolidation Pass

**Location:** `_extract_chunked_file()` → Phase 3 consolidation prompt
**What:** Three targeted improvements to the consolidation LLM call.

#### 3a — Increase raw text budget and preserve sequence order

Currently: `combined_raw[:9000]` — may silently cut off later chunks.

Change to:
```python
# Sort raw_text_parts by chunk sequence before joining
# (already appended in chunk order, but make it explicit)
combined_raw = "\n\n".join(raw_text_parts)  # remove the [:9000] hard cap

# If combined_raw is very long, summarise the excess rather than truncate
MAX_RAW = 12000
if len(combined_raw) > MAX_RAW:
    head = combined_raw[:8000]
    tail = combined_raw[-3000:]
    combined_raw_for_prompt = (
        head + f"\n\n[... {len(combined_raw) - 11000} characters omitted ...]\n\n" + tail
    )
else:
    combined_raw_for_prompt = combined_raw
```

#### 3b — Add cross-chunk assembly instruction to consolidation prompt

Add to the consolidation prompt (after the existing task/details lines):
```
IMPORTANT — CROSS-CHUNK ASSEMBLY:
The raw text above was extracted from {len(chunks)} separate image chunks of a large drawing.
Text entries marked with → were cut off at a chunk boundary (continues in the next chunk).
Text entries marked with ← continue from the previous chunk.
Sections labelled [STITCHED ...] are pre-assembled boundary fragments — treat these as authoritative.

For list-type fields (especially notes, earthing requirements, compliance standards):
- Assemble ALL partial entries from adjacent chunks into a single complete list.
- Do NOT stop at the first chunk that mentions a field — read ALL chunks for that field.
- If the same entry appears in multiple chunks (due to overlap), de-duplicate it.
```

#### 3c — Notes-specific assembly instruction

Because `sub-step-extract-siteplan-notes` is explicitly a list extraction task, add a field-level hint:
```
For the field "sub-step-extract-siteplan-notes":
  This is a NOTES section on the drawing. Notes are numbered (e.g., 1., 2., 3., ...)
  and may span 3–6 adjacent chunks. Collect EVERY note number and its full text.
  The complete list should be returned as a single string with each note on a new line,
  e.g.: "1. All HV cable...\n2. Earthing to comply...\n3. New sub by ARP..."
```

---

## Summary of Changes per File

| File | Change |
|------|--------|
| `document_extractor.py` | Add `_llm_call_vision_multi()` function |
| `document_extractor.py` | Add `_stitch_adjacent_chunks()` function |
| `document_extractor.py` | Phase 1 prompt: add continuation marker instructions |
| `document_extractor.py` | Phase 1.5: call `_stitch_adjacent_chunks()` and merge results |
| `document_extractor.py` | Phase 2: append stitched raw text; prefer stitched data values |
| `document_extractor.py` | Phase 3: increase raw text budget (head+tail instead of hard cap) |
| `document_extractor.py` | Phase 3 prompt: add cross-chunk assembly and notes-specific instructions |

---

## Execution Sequence

```
Phase 1     Individual chunk vision calls (existing — add continuation markers to prompt)
                ↓
Phase 1.5   Adjacent boundary stitch calls (NEW — only for pairs with → markers)
                ↓
Phase 2     Aggregate raw text (sorted by sequence) + merge data (stitched values win)
                ↓
Phase 3     Consolidation LLM call (text-only, improved prompt with assembly instructions)
```

---

## LLM Call Budget

| Phase | Calls | Type | Model |
|-------|-------|------|-------|
| Phase 1 | N chunks (e.g. 12) | Vision | Gemini 2.0 Flash |
| Phase 1.5 | 0–(N-1) boundary pairs with `→` markers (typically 2–4) | Vision multi-image | Gemini 2.0 Flash |
| Phase 3 | 1 | Text only | GPT-4o (via `_llm_call`) |

Worst case for a 12-chunk drawing: 12 + 11 + 1 = 24 calls (vs. current 12 + 1 = 13).
Typical case with continuation filtering: 12 + 3 + 1 = 16 calls.

---

## Risk and Mitigations

| Risk | Mitigation |
|------|------------|
| Gemini multi-image API not supported | `_llm_call_vision_multi` wraps in try/except; stitching is additive (failure just skips that pair) |
| Continuation markers (`→`/`←`) not reliably produced by vision LLM | Consolidation prompt still assembles from full raw text corpus; markers are a hint only |
| Stitched values overwrite correct single-chunk values | Only overwrite when `v is not None` — stitched null won't overwrite a good value |
| Raw text budget increase increases consolidation cost | Head+tail strategy keeps prompt bounded; Phase 3 uses cheap text model not vision |
| Notes are still incomplete if spread across 5+ chunks | Phase 3 consolidation sees all raw text including all stitched fragments — should recover |

---

## Implementation Order

1. Add `_llm_call_vision_multi()` (self-contained, no dependencies)
2. Update Phase 1 per-chunk prompt (one-line addition)
3. Add `_stitch_adjacent_chunks()` function
4. Wire Phase 1.5 between Phase 1 and Phase 2 in `_extract_chunked_file()`
5. Update Phase 2 aggregation to include stitched raw and data
6. Update Phase 3 consolidation prompt (raw budget + assembly instructions)
7. Rebuild `document-extractor` Docker image and test
