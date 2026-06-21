"""
Test the notes-merge fix against the last execution's per-chunk extraction data.

Root cause:
  1. Chunk merge logic picks the chunk with the MOST note numbers (chunk_2, notes 1-16)
     and discards chunk_4 (notes 15-17), so note 17 is lost.
  2. Phase 3 consolidation has a stop-boundary hint ("STOP at ASSET RECORDING") which
     causes the LLM to output only notes 1-11 even though merged_data has notes 1-16.

Fix verified here:
  - Combine notes from all chunks using note-number union
  - Result should be notes 1-17

Run: python test_notes_recovery.py
"""

import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

EXTRACTION_FILE = Path(
    r"c:\Code\experiments\agent 09\OUTPUT"
    r"\DAR11575-85 - Exp_20260516_120604"
    r"\Extraction_A524058_NRL15205_RETIC_NOTES_pdf_Extract_Site_Plan_Information_20260516_120839_025652.json"
)

# ── Note parsing ───────────────────────────────────────────────────────────────
# Handles three formats used on ACME Energy drawings:
#   "1. Text..."      (with period)
#   "1 Text..."       (number + space + text on same line)
#   "1\nText..."      (bare number, text on next line)

def _parse_notes(text: str) -> dict:
    """Return {note_num: full_text_block} parsed from a notes string."""
    notes = {}
    if not text:
        return notes

    # Split on any of the three note-number patterns
    # Use a lookahead so the delimiter stays with the following note
    pattern = re.compile(
        r'(?:^|\n)[ \t]*(\d{1,2})'   # note number
        r'(?:'
        r'\.[ \t]+'                    # "1. "
        r'|[ \t]+(?=[A-Z\w])'         # "1 T" (space + uppercase)
        r'|[ \t]*\n[ \t]*(?=[A-Z\w])' # "1\nT" (bare number, text next line)
        r')',
        re.MULTILINE,
    )

    # Find all note starts and their positions
    starts = [(m.start(), int(m.group(1)), m.end()) for m in pattern.finditer("\n" + text)]
    # Filter to plausible note numbers (1-20)
    starts = [(pos, num, end) for pos, num, end in starts if 1 <= num <= 20]

    for i, (pos, num, content_start) in enumerate(starts):
        # Content runs until the next note start (or end of text)
        next_pos = starts[i + 1][0] if i + 1 < len(starts) else len(text) + 1
        block = text[content_start - 1 : next_pos].strip()
        # Sanity: at least 4 real words
        words = [w for w in block.split() if re.search(r'[A-Za-z]{3,}', w)]
        if len(words) >= 4:
            # Keep longer block if this note number was seen before
            if num not in notes or len(block) > len(notes[num]):
                notes[num] = block
    return notes


def combine_chunk_notes(chunks_notes: list) -> str:
    """Merge notes from multiple per-chunk extractions into a single ordered string.

    Takes the union of note numbers across all chunks, keeping the longest
    block for each number (most complete content wins).
    """
    combined = {}
    for text in chunks_notes:
        if not text or not isinstance(text, str):
            continue
        for num, block in _parse_notes(text).items():
            if num not in combined or len(block) > len(combined[num]):
                combined[num] = block

    if not combined:
        return ""

    lines = []
    for num in sorted(combined):
        block = combined[num].strip()
        # Ensure the block starts with the note number
        if not re.match(r'^\d{1,2}[\s\.]', block):
            block = f"{num} {block}"
        lines.append(block)
    return "\n".join(lines)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    with open(EXTRACTION_FILE, encoding="utf-8") as f:
        d = json.load(f)

    cd = d.get("chunk_details", [])
    print(f"Chunks: {len(cd)}")
    print()

    # Collect per-chunk notes
    all_chunk_notes = []
    for c in cd:
        ext = c.get("extracted") or {}
        data = ext.get("data", {}) if isinstance(ext, dict) else {}
        notes_text = str((data.get("sub-step-extract-all-notes") or "") if isinstance(data, dict) else "")
        parsed = _parse_notes(notes_text)
        nums = sorted(parsed.keys())
        all_chunk_notes.append(notes_text)
        print(f"  {c.get('chunk_id')} {c.get('region'):15} → notes {nums}")

    print()

    # Current merge result (Phase 3 output)
    sse = d.get("sub_step_extractions", {})
    current = str((sse.get("sub-step-extract-all-notes", {}) or {}).get("value", ""))
    current_nums = sorted(_parse_notes(current).keys())
    print(f"Current Phase 3 output: notes {current_nums}")
    print()

    # Apply fix: combine all chunks
    merged = combine_chunk_notes(all_chunk_notes)
    merged_nums = sorted(_parse_notes(merged).keys())
    print(f"After combine_chunk_notes: notes {merged_nums}")

    missing_after = set(range(1, max(merged_nums) + 1)) - set(merged_nums) if merged_nums else set()
    if missing_after:
        print(f"  Still missing: {sorted(missing_after)}")
    else:
        print("  ✓ Consecutive sequence — no gaps")

    print()
    print("=" * 60)
    print("MERGED NOTES OUTPUT")
    print("=" * 60)
    print(merged)


if __name__ == "__main__":
    main()
