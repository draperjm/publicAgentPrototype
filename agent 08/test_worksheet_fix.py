#!/usr/bin/env python3
"""
Test script to verify the Step 7 worksheet fix.
This directly tests the _select_target_worksheets function to ensure
both PROJECT and STREETLIGHT worksheets are selected.
"""

from pathlib import Path
from document_extractor import _read_spreadsheet_as_tables, _select_target_worksheets

# Test the fixed function
filepath = Path('documents/DAR1509-84 - Exp/DAR1988_TAL_20260112.XLSM')

if not filepath.exists():
    print(f"ERROR: Test file not found at {filepath}")
    exit(1)

print("=" * 80)
print("STEP 7 WORKSHEET FIX VERIFICATION")
print("=" * 80)
print()

sheets = _read_spreadsheet_as_tables(str(filepath))

print("Worksheets found in spreadsheet:")
for s in sheets:
    row_count = len(s.get("rows", []))
    col_count = len(s.get("headers", []))
    print(f"  - {s['sheet_name']:20s} : {row_count:3d} rows × {col_count:2d} cols")

print()
print("Worksheets selected by _select_target_worksheets():")
targets = _select_target_worksheets(sheets, filepath.name)
for t in targets:
    print(f"  - {t}")

print()
print("=" * 80)
print("VALIDATION RESULT:")
print("=" * 80)

if len(targets) == 2 and 'PROJECT' in targets and 'STREETLIGHT' in targets:
    print("✓ SUCCESS: Both PROJECT and STREETLIGHT are selected")
    print("  The fix correctly removes aggressive LLM-based filtering")
    print("  Step 7 will now process both worksheets")
    exit(0)
elif len(targets) == 1:
    print(f"✗ FAILED: Only {targets[0]} selected")
    print(f"  The aggressive filtering is still active")
    exit(1)
else:
    print(f"✗ FAILED: Unexpected result")
    print(f"  Expected: ['PROJECT', 'STREETLIGHT']")
    print(f"  Got: {targets}")
    exit(1)
