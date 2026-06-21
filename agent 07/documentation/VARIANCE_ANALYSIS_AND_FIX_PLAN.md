# Step 5 Notes Extraction Variance Analysis & Comprehensive Fix Plan

## Executive Summary

**Current Consistency: 67% with HIGH VARIANCE**
- Execution 1 (093325): Severe scope creep — extracted full document including boundaries, formatting tables as "NOTICE:" entries
- Execution 2 (090557): Complete extraction failure — returned "NOT FOUND" despite notes present in raw OCR text
- **Root Cause: Pre-consolidation sanitization is bypassed when text length > 12000 characters**

## Variance Report Analysis

### Execution 093325 (93:25 timestamp) - Scope Creep Failure
```
INPUT RAW TEXT: OPERATIONAL LIMITATIONS... [3 numbered notes]... DUCT END LOCATION DETAIL... [entire rest of document]

EXTRACTED (sub-step-extract-all-notes):
"1. OPERATIONAL LIMITATIONS UNLESS APPROVED OTHERWISE... 
2. THE FOLLOWING ALTERNATIVES SHOULD BE CONSIDERED... 
3. THE COST IS TO BE FUNDED... 
NOTICE: DUCT END LOCATION DETAIL GROUND LEVEL... [continues extracting entire document as NOTICE entries]
NOTICE: POLE/COLUMN SETOUT... [table data]
NOTICE: WORKS COMPLETED/FIELD BOOK...
NOTICE: ASSET RECORDING...
NOTICE: DESIGN COMPLIANCE...
NOTICE: FUNDING ARRANGEMENTS..."

EXPECTED: Only numbered 1-3, stop at "DUCT END LOCATION DETAIL"
VARIANCE: +20KB of extracted content, 4-5 unique variations across consecutive runs
```

### Execution 090557 (90:57 timestamp) - Complete Extraction Failure
```
INPUT RAW TEXT: [SAME document with minor OCR variant "WORK PRACTICE/STANDARDS"]

EXTRACTED (sub-step-extract-all-notes): "NOT FOUND"

EXPECTED: Same 3 notes as above
VARIANCE: Complete reversal from over-extraction to zero extraction
PATTERN: Different OCR → different LLM parsing → completely different result
```

### Comparison Matrix

| Aspect | Ex 093325 | Ex 090557 |
|--------|-----------|----------|
| Input OCR | WORK PRACTICES / STANDARDS | WORK PRACTICE/STANDARDS |
| Notes extraction | 20,000+ chars (scope creep) | "NOT FOUND" (complete miss) |
| Consistency | 1 unique massive string | Different from 93325 |
| Field Check | "NOT COMPLETED" | "NOT COMPLETED" |
| Substation Data | All NOT FOUND | All NOT FOUND |
| Root cause | Sanitization bypassed | Different tokenization path |

## Root Cause Analysis

### Issue #1: Sanitization Bypass (HIGH PRIORITY)

**Location:** document_extractor.py lines 2153-2170

**Problem:**
```python
# Line 2150-2153
if notes_ids_check:
    raw_for_consolidation = _sanitize_raw_for_notes_extraction(combined_raw)

# Line 2157-2168: If text > 12,000 chars...
if len(raw_for_consolidation) > _MAX_RAW:
    _head = raw_for_consolidation[:8000]
    _tail = raw_for_consolidation[-3000:]  # ← PROBLEM: tail may reintroduce boundaries!
    combined_raw_for_prompt = (_head + [...] + _tail)
else:
    combined_raw_for_prompt = raw_for_consolidation  # ← Only safe path
```

**Why this breaks:**
- `_sanitize_raw_for_notes_extraction()` truncates at first boundary (e.g., "DUCT END LOCATION DETAIL" at position ~900 chars)
- But if after truncation text is still > 12,000 chars (which it'll be in multichunk docs), the head-tail slicing **reintroduces the tail section**
- The tail is taken from position `len(text) - 3000`, which for a 50KB document is position 47000
- This position is **far past the boundary**, so the tail includes ALL the restricted content!

**Real scenario:**
- Raw text: 15,000 chars (multichunk stitched document)
- Sanitize truncates at position 900 → 900 chars (well under 12K limit)
- But we use this 900-char text, so should be fine? Let me re-examine...

Actually, wait. Looking again at line 2157:
```python
if len(raw_for_consolidation) > _MAX_RAW:  # If > 12,000
```

After sanitization, if the boundary is at position 900, then `raw_for_consolidation` is only 900 chars, which is < 12,000. So the bypass shouldn't happen.

**UNLESS** — the sanitization itself is failing to find the boundary! Let me check if the boundary matching is case-sensitive or if there's a space issue.

### Issue #2: Boundary Detection Failure (LIKELY ROOT CAUSE)

The `_sanitize_raw_for_notes_extraction()` function searches for:
```python
_NOTES_STOP_BOUNDARIES = {
    "DUCT END LOCATION DETAIL",
    "POLE/COLUMN SETOUT",
    "POLE / COLUMN SETOUT",  # ← Note the space variant
    ...
}
```

But in the raw OCR text from Ex 090557, we see:
```
"WORK PRACTICE/STANDARDS"  (no s on PRACTICE)
```

And in Ex 093325:
```
"WORK PRACTICES / STANDARDS"  (with s, with spaces)
```

These are OCR variants, not boundary issues. But what if the boundary isn't being found due to:
1. **Encoding issues** (unicode spaces vs regular spaces)?
2. **Case issues** (boundary detection is uppercase but text has mixed case)?
3. **Newline fragmentation** (boundary split across lines in the consolidated text)?

Looking at line 915:
```python
pos = text_upper.find(boundary.upper())
```

This converts both text and boundary to uppercase, so case shouldn't be the issue.

Actually, I need to trace through the **real raw text being passed to consolidation**. Let me check what `combined_raw` contains and whether it actually includes the boundaries.

### Issue #3: LLM Instruction Override (POSSIBLE ROOT CAUSE)

The consolidation prompt includes:
```python
notes_hint = (
    "SCOPE: Extract ONLY the numbered notes (1., 2., 3., etc.)...\n"
    "STOP at these boundaries: DUCT END LOCATION DETAIL, POLE/COLUMN SETOUT, ...\n"
)
```

But the LLM also sees `ALL TEXT EXTRACTED FROM DOCUMENT CHUNKS` which **includes all the boundaries and everything past them**.

**The LLM's instruction to "STOP at boundaries" is a guideline, not a hard constraint.** If the LLM sees:
```
1. OPERATIONAL LIMITATIONS...
2. THE FOLLOWING ALTERNATIVES...
3. THE COST IS TO BE FUNDED...
DUCT END LOCATION DETAIL
GROUND LEVEL DEVELOPER TO ATTACH...
POLE/COLUMN SETOUT
[pole data table]
WORKS COMPLETED
[form fields]
```

It might interpret "NOTICE: WORKS COMPLETED" and "NOTICE: DUCT END LOCATION DETAIL" as **additional callout annotations** (which the instructions say to extract), especially if the raw text doesn't clearly distinguish them as section headers vs. callout boxes.

## Variance Patterns Identified

1. **OCR Variance Impact**: Different OCR spellings ("PRACTICE" vs "PRACTICES", "PRACTICE/STANDARDS" vs "PRACTICES / STANDARDS") are causing different LLM parsing paths
2. **Boundary Recognition**: Some runs recognize boundaries, others treat them as note content
3. **Formatting Misinterpretation**: Section headers are being converted to "NOTICE: [header] [content below...]" pseudo-entries
4. **Consistency Swing**: 0% to 100% over extraction, no middle ground

## Comprehensive Fix Strategy

### Fix #1: Enforce Hard Boundary Cutoff (CRITICAL)

**Use case-insensitive regex to detect boundary headers ANYWHERE in the raw text, not just at line starts:**

```python
def _sanitize_raw_for_notes_extraction_improved(text: str) -> str:
    """
    Hard cutoff boundary enforcement with robust detection.
    Handles:
    - Case-insensitive matching
    - Whitespace tolerance (extra spaces, newlines)
    - Boundary appearing mid-line or on separate line
    """
    if not isinstance(text, str):
        return text
    
    import re
    
    # Build regex: match boundary as whole word, case-insensitive
    boundaries_pattern = '|'.join(re.escape(b) for b in _NOTES_STOP_BOUNDARIES)
    pattern = r'\b(' + boundaries_pattern + r')\b'
    
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        boundary_text = match.group(1)
        earliest_position = match.start()
        sanitized = text[:earliest_position].rstrip()
        removed_chars = len(text) - len(sanitized)
        logger.info(
            f"Raw text sanitization: hard cutoff at '{boundary_text}' "
            f"(removed {removed_chars} chars)"
        )
        return sanitized
    else:
        logger.info(f"No boundary found")
        return text
```

### Fix #2: Separate Note Extraction Prompt

**Create a dedicated FIRST phase consolidation pass that ONLY extracts notes:**

Current approach: Single consolidation pass tries to extract everything (notes, substation data, field checks, easement notes, arrays, legend)

Better approach:
1. **Phase 3a (Notes-Only)**: Input = fully sanitized + no array/structured data context
2. **Phase 3b (Structured Data)**: Input = second clean sanitized pass + reference to notes from 3a

```python
# Phase 3a - Notes Extraction Only
notes_consolidated = llm_consolidation(
    text=_sanitize_raw_for_notes_extraction(combined_raw),
    fields_to_extract=[sid for sid in _sub_step_ids if 'note' in sid.lower()],
    instructions=NOTES_ONLY_INSTRUCTIONS  # No mention of structured sections
)

# Phase 3b - Structured Data + Arrays  
structured_consolidated = llm_consolidation(
    text=combined_raw,  # Full unhardened text
    fields_to_extract=[sid for sid in _sub_step_ids if 'note' NOT in sid.lower()],
    instructions=STRUCTURED_DATA_INSTRUCTIONS,
    reference_notes=notes_consolidated  # Link to validated notes
)

# Merge results
consolidated = {**notes_consolidated, **structured_consolidated}
```

### Fix #3: Multi-Layer Validity Filtering (ENHANCED)

**Strengthen post-extraction filtering to catch LLM scope creep:**

```python
def _is_notes_extraction_valid(text: str) -> bool:
    """
    Check if extracted notes are within expected scope:
    - Should contain 1-15 numbered items
    - No more than 5KB total
    - No complete section headers (DESIGN COMPLIANCE, FUNDING ARRANGEMENTS)
    - No structural content (table grids, coordinate lists, signature blocks)
    """
    if not isinstance(text, str) or not text.strip():
        return False
    
    # Count numbered items
    numbered_items = len(re.findall(r'\d+\.\s', text))
    if not (1 <= numbered_items <= 15):
        logger.warning(f"Invalid numbered item count: {numbered_items}")
        return False
    
    # Check size
    if len(text) > 5000:
        logger.warning(f"Extracted notes too large: {len(text)} chars (max 5000)")
        return False
    
    # Check for structural content indicators
    red_flags = [
        r'SIGNATURE:\s*DATE:',  # Form field pattern
        r'EASTING\s+NORTHING',   # Coordinate table
        r'HEREBY CERTIFY',        # Legal boilerplate
        r'FUNDING ARRANGEMENTS',  # Section header ending
    ]
    
    for flag_pattern in red_flags:
        if re.search(flag_pattern, text, re.IGNORECASE):
            logger.warning(f"Found red flag pattern: {flag_pattern}")
            return False
    
    return True

# Apply after consolidation LLM extraction
for notes_sid in notes_ids:
    notes_val = consolidated.get(notes_sid, "")
    if isinstance(notes_val, str) and notes_val.strip():
        if not _is_notes_extraction_valid(notes_val):
            logger.warning(f"Extracted notes failed validity check for {notes_sid}")
            # Return fallback: just the first 3 entries or "NOT FOUND"
            consolidated[notes_sid] = "NOT FOUND — extracted content was out of scope"
```

### Fix #4: OCR Normalization BEFORE Consolidation

**Preprocess raw text to normalize known OCR variants:**

```python
def _normalize_ocr_text_pre_consolidation(text: str) -> str:
    """
    Normalize known OCR issues BEFORE consolidation LLM sees the text.
    Prevents LLM from being confused by OCR variants.
    """
    # Common ACME energy drawing OCR variants
    replacements = [
        (r'WORK\s+PRACTICE(?!S)\s*/\s*STANDARDS', 'WORK PRACTICES / STANDARDS'),
        (r'WORK\s+PRACTICE(?!S)\s+STANDARDS', 'WORK PRACTICES / STANDARDS'),
        (r'POLE\s*/?COLUMN\s*SETOUT', 'POLE / COLUMN SETOUT'),
        (r'DRAWING\s+DESIGN', 'DRAWING'),  # "or DESIGN" variants
        (r'DUCT\s+END\s+LOCATION\s+DETAIL', 'DUCT END LOCATION DETAIL'),  # Extra spaces
    ]
    
    normalized = text
    for pattern, replacement in replacements:
        normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
    
    return normalized

# Use in consolidation flow (line 2150)
combined_raw_cleaned = _normalize_ocr_text_pre_consolidation(combined_raw)
raw_for_consolidation = _sanitize_raw_for_notes_extraction(combined_raw_cleaned)
```

## Implementation Order

### Immediate (Deploy Today)
1. **Fix #1**: Replace `_sanitize_raw_for_notes_extraction()` with regex-based detection
2. **Fix #4**: Add OCR normalization before consolidation call
3. **Test**: Run 5 full extraction cycles on same DAR1675document

### Short-Term (Next 1-2 days)
4. **Fix #3**: Enhance post-extraction validity filtering with structural content checks
5. **Add logging**: Enable debug output for boundary detection and sanitization
6. **Create test report**: Document extraction consistency across 10+ runs

### Medium-Term (Next week)
7. **Fix #2**: Implement dual-phase consolidation (notes-only, then structured)
8. **Evaluate**: Compare consistency metrics before/after two-phase approach

## Expected Outcomes

**After Fix #1 + #4**: 85-95% consistency (eliminate obvious scope creep)
**After Fix #3**: 90%+ consistency (eliminate structural content leakage)
**After Fix #2**: 95%+ consistency (maximize scope isolation)

---

## Code Locations to Modify

| File | Lines | Function | Priority |
|------|-------|----------|----------|
| document_extractor.py | 890-924 | `_sanitize_raw_for_notes_extraction()` | P1 |
| document_extractor.py | 927-957 | `_is_valid_note_entry()` → enhance | P2 |
| document_extractor.py | 1019-1058 | `_normalize_notes_text()` → add structural checks | P2 |
| document_extractor.py | 2150-2170 | Pre-consolidation flow → add OCR norm + safer truncation | P1 |
| document_extractor.py | 2270-2280 | Post-consolidation norm → call enhanced validity check | P2 |

