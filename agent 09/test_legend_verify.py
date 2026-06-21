"""
Test Phase 1.4 legend label verification against chunks from the last execution.

Run from the agent 09 directory:
    python test_legend_verify.py
"""

import base64
import io
import json
import os
import sys
from pathlib import Path

import PIL.Image
from google import genai
from google.genai import types as genai_types

# ── Config ────────────────────────────────────────────────────────────────────

CHUNK_DIR = Path(
    "/app/OUTPUT/DAR11575-85 - Exp_20260516_040005/chunks"
    "/67e4e5a0-25ac-4e32-82d7-5ae8438fa6d7"
)

LAST_EXTRACTION = Path(
    "/app/OUTPUT/DAR11575-85 - Exp_20260516_040005"
    "/Extraction_A524058_NRL15205_RETIC_NOTES_pdf_Extract_Drawing_Legend_20260516_040312_798919.json"
)

VISION_MODEL = os.getenv("VISION_MODEL", "gemini-2.0-flash")

# ── Gemini client ─────────────────────────────────────────────────────────────

_client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])

_GEMINI_CFG = genai_types.GenerateContentConfig(
    temperature=0.0,
    top_p=1.0,
    top_k=1,
    response_mime_type="application/json",
    automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True),
    safety_settings=[
        genai_types.SafetySetting(category="HARM_CATEGORY_HARASSMYENT",        threshold="BLOCK_NONE"),
        genai_types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH",       threshold="BLOCK_NONE"),
        genai_types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
        genai_types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
    ],
)

VERIFY_PROMPT = (
    "You are reviewing an engineering drawing image.\n\n"
    "Find the LEGEND or KEY panel — a boxed or clearly delimited area that shows "
    "drawn graphical symbols (lines, shapes, icons) each paired with a short text "
    "label identifying what that symbol represents on the drawing.\n\n"
    "List EVERY text label that appears next to a symbol in that legend panel. "
    "Copy each label exactly as it is written on the drawing — do not paraphrase, "
    "abbreviate, or add extra labels that are not visible.\n\n"
    "Return ONLY this JSON (no other text):\n"
    "{\"has_legend\": true, \"labels\": [\"Overhead Mains - Existing\", \"Remove Existing Overhead Mains\", ...]}\n"
    "or {\"has_legend\": false, \"labels\": []} if no legend panel is visible in these images."
)


def stitch_images(paths: list[Path]) -> bytes:
    """Stitch images horizontally into a single PNG."""
    imgs = [PIL.Image.open(p).convert("RGB") for p in paths]
    canvas = PIL.Image.new(
        "RGB",
        (sum(i.width for i in imgs), max(i.height for i in imgs)),
        (255, 255, 255),
    )
    x = 0
    for img in imgs:
        canvas.paste(img, (x, 0))
        x += img.width
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def call_vision(image_bytes_list: list[bytes]) -> dict:
    parts = []
    for img in image_bytes_list:
        parts.append(genai_types.Part.from_bytes(data=img, mime_type="image/png"))
    parts.append(genai_types.Part.from_text(text=VERIFY_PROMPT))
    resp = _client.models.generate_content(
        model=VISION_MODEL,
        contents=[genai_types.Content(parts=parts, role="user")],
        config=_GEMINI_CFG,
    )
    try:
        return json.loads(resp.text)
    except Exception:
        return {"has_legend": False, "labels": [], "raw": resp.text}


def label_matches(extracted: str, confirmed: set) -> bool:
    """Exact match only — substring matching causes false positives.
    e.g. 'EXISTING POLE' (gap fill) would pass as substring of
    'EXISTING POLE TO BE REPLACED', producing a spurious short entry."""
    return extracted.strip().upper() in confirmed


def main():
    manifest_path = CHUNK_DIR / "manifest.json"
    with open(manifest_path) as f:
        manifest = json.load(f)

    chunks = manifest["chunks"]
    print(f"Drawing: {manifest['source_file']}")
    print(f"Strategy: {manifest['chunk_strategy']}, chunks: {manifest['total_chunks']}\n")

    # ── Load the previous extraction ─────────────────────────────────────────
    with open(LAST_EXTRACTION) as f:
        ext_data = json.load(f)
    sse = ext_data.get("sub_step_extractions", {})
    legend_raw = sse.get("sub-step-extract-legend", {})
    prev_entries = legend_raw.get("value", legend_raw) if isinstance(legend_raw, dict) else legend_raw
    if not isinstance(prev_entries, list):
        prev_entries = []

    print(f"Previously extracted entries ({len(prev_entries)}):")
    for i, e in enumerate(prev_entries, 1):
        print(f"  {i:2}. [{e.get('category','?'):10}]  {e.get('label','?')}")
    print()

    # ── Phase 1: probe each chunk individually ───────────────────────────────
    # Sending the full-stitched page risks the vision model finding a different
    # legend section (e.g. services legend vs electrical legend). Instead, probe
    # each chunk tile independently and union the confirmed labels across all chunks.
    print("Probing each chunk individually...")
    all_confirmed: set = set()
    chunk_results_summary = []

    for chunk in sorted(chunks, key=lambda c: c["sequence"]):
        fp = CHUNK_DIR / chunk["filename"]
        if not fp.exists():
            print(f"  chunk {chunk['chunk_id']} ({chunk['region']}): file not found, skipping")
            continue

        img_bytes = fp.read_bytes()
        result = call_vision([img_bytes])
        has_leg = result.get("has_legend", False)
        labels = result.get("labels", []) if has_leg else []
        norm_labels = {str(lbl).strip().upper() for lbl in labels if str(lbl).strip()}
        all_confirmed |= norm_labels

        chunk_results_summary.append({
            "chunk": chunk["chunk_id"],
            "region": chunk["region"],
            "pass": chunk["split_pass"],
            "has_legend": has_leg,
            "label_count": len(labels),
            "labels": labels,
        })
        status = f"✓ {len(labels)} labels" if has_leg else "✗ no legend"
        print(f"  {chunk['chunk_id']} [{chunk['region']:6} pass{chunk['split_pass']}]: {status}")
        if labels:
            for lbl in labels:
                print(f"      - {lbl}")

    if not all_confirmed:
        print("\nNo legend confirmed in any chunk — all entries would be kept (safe fallback).")
        return

    # ── Exclude low-yield chunks (secondary diagrams, not the main legend) ────
    max_count = max((r["label_count"] for r in chunk_results_summary if r["has_legend"]), default=0)
    min_threshold = max(1, int(max_count * 0.5))
    print(f"\nMax labels from any single chunk: {max_count}  →  threshold: {min_threshold}")
    qualified_confirmed: set = set()
    for r in chunk_results_summary:
        if not r["has_legend"]:
            continue
        if r["label_count"] >= min_threshold:
            qualified_confirmed |= {str(lbl).strip().upper() for lbl in r["labels"]}
            print(f"  ✓ {r['chunk']} [{r['region']}]: {r['label_count']} labels — included")
        else:
            print(f"  ✗ {r['chunk']} [{r['region']}]: {r['label_count']} labels — excluded (secondary diagram)")

    print(f"\nQualified confirmed labels ({len(qualified_confirmed)}):")
    for lbl in sorted(qualified_confirmed):
        print(f"  - {lbl}")

    # ── Filter ────────────────────────────────────────────────────────────────
    kept, removed = [], []
    for entry in prev_entries:
        if label_matches(entry.get("label", ""), qualified_confirmed):
            kept.append(entry)
        else:
            removed.append(entry)

    print(f"\n{'─'*60}")
    print(f"RESULT: {len(prev_entries)} extracted  →  {len(kept)} kept, {len(removed)} removed\n")

    if kept:
        print(f"✓ Kept ({len(kept)}):")
        for e in kept:
            print(f"    [{e.get('category','?'):10}]  {e.get('label','?')}")

    if removed:
        print(f"\n✗ Removed ({len(removed)}) — not confirmed in any chunk:")
        for e in removed:
            print(f"    [{e.get('category','?'):10}]  {e.get('label','?')}")


if __name__ == "__main__":
    main()
