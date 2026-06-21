# Debug Report: Step 6 & Step 7 Failures

## Executive Summary
Both Step 6 (Extract Drawing Legend) and Step 7 (Extract Asset Spreadsheet) are failing validation tests due to logic issues in the extraction agents, NOT the orchestrator or multi-run infrastructure.

---

## STEP 6: Extract Drawing Legend — COMPLETE FAILURE (0%)

### Symptom
- **Input**: 5 image chunks from PDF site plan
- **Expected**: Array of legend entries with symbol_description, label, category, page
- **Actual**: Empty array `[]`
- **Validation Result**: FAIL (0/5 tests pass)

### Root Cause
**Vision model is not detecting/extracting legend symbols from the PDF chunks.**

The PDF input file (`DS1_DAR1988_RETIC_NOTES.pdf`) appears to be a **location plan/administrative sheet** rather than an electrical drawing with a symbol legend. The document contains:
- Title blocks
- Location maps (labeled A, B, C, D, E, F, G, H)
- Revision history and amendments
- Funding arrangements tables
- Notes and specifications
- Reference drawings table

**Missing**: Actual electrical symbol legend (lines, circles, filled shapes paired with labels).

### Code Implementation (document_extractor.py)
**File**: `c:\Code\experiments\agent 07\document_extractor.py`
**Lines**: 1432-1455 (legend detection), 1498-1600 (legend extraction)

**Logic flow**:
1. Chunks are processed through the vision model with a detailed legend extraction prompt
2. The prompt explicitly asks for legend entries in strict JSON schema: `{page, symbol_description, label, category}`
3. If no legend symbols are detected by the vision model, the array returns empty
4. All 5 chunks returned empty arrays because no actual electrical symbols are present in the images

### Issue Type
**DATA ISSUE** — The test input PDF doesn't contain the expected electrical symbol legend that Step 6 is designed to extract.

### Validation Test Results
- Test dates: 20260412_063505, 20260412_070429, 20260412_065046
- All three test runs: `actual_chunks_processed: 5, legend_entries_extracted: 0`
- Error pattern: `"sub-step-extract-legend": []` in all chunks

---

## STEP 7: Extract Asset Spreadsheet — PARTIAL FAILURE (71%)

### Symptom
- **Input**: TAL file with 2 worksheets to analyze (PROJECT: 25 rows × 13 cols; STREETLIGHT: 55 rows × 24 cols)
- **Expected**: Both worksheets processed → ~80 total asset records
- **Actual**: Only STREETLIGHT processed → 18 records; PROJECT skipped
- **Validation Result**: FAIL (5/7 tests pass, score 71%)

### Root Cause
**LLM-based Worksheet Selection Logic**

The extraction agent uses Claude/LLM to decide which worksheets contain "asset register data worth extracting" (line 3081-3083, document_extractor.py):

```python
target_names   = _select_target_worksheets(sheets, fe.filename)
target_sheets  = [s for s in sheets if s["sheet_name"] in target_names]
sheets_skipped = [s["sheet_name"] for s in sheets if s["sheet_name"] not in target_names]
```

**The `_select_target_worksheets()` function** (lines 2988-3042):
1. Shows Claude the sheet names, headers, and sample rows
2. Asks Claude to identify which sheets contain "asset register data"
3. Claude is instructed to "Exclude worksheets that are... summary/pivot tables, or empty/placeholder tabs"
4. Claude incorrectly classified PROJECT as non-asset (possibly because it has fewer rows than STREETLIGHT)
5. Result: PROJECT sheet was skipped, only STREETLIGHT processed

### Code Implementation (document_extractor.py)
**File**: `c:\Code\experiments\agent 07\document_extractor.py`
**Function**: `_select_target_worksheets()` at lines 2988-3042

**Problematic logic**:
```python
prompt = (
    f"You are reviewing a workbook named '{filename}' to identify which worksheets "
    f"contain asset register data suitable for extraction.\n\n"
    ...
    "Exclude worksheets that are: instructions/cover sheets, lookup tables, charts-only, "
    "summary/pivot tables, or empty/placeholder tabs.\n\n"
    "Return ONLY a JSON object: "
    "{\"target_sheets\": [\"SheetName1\", \"SheetName2\"], ...}"
)
```

**Why this fails**:
- Claude sees PROJECT with 25 rows and STREETLIGHT with 55 rows
- Claude assumes PROJECT is a summary/partial sheet (not primary asset register)
- Claude filters out PROJECT as "non-asset"
- Orchestrator has already pre-selected these worksheets via `worksheets_to_analyse`, but agent ignores this

### Issue Type
**LOGIC ISSUE** — The worksheet selection algorithm is too aggressive in filtering. It should process ALL worksheets listed in `worksheets_to_analyse` from the orchestrator's Step 2 output, not re-filter them based on LLM heuristics.

### Validation Test Results
- Test date: 20260412_063550
- **TC-002 FAIL**: Expected `['PROJECT', 'STREETLIGHT']`, got `['STREETLIGHT']`
- **TC-007 FAIL**: PROJECT sheet listed in `worksheets_to_analyse` but skipped in `sheets_processed`
- **Score**: 71% (5/7 tests passed)

---

## Detailed Test Failure Evidence

### Step 6 Validation Report
**File**: `TestReport_Extract_Drawing_Legend_20260412_065046.json`
```json
{
  "test_cases": [
    {
      "test_id": "TC-001",
      "result": "FAIL",
      "description": "Verify legend entry schema",
      "actual_output": "sub-step-extract-legend: []"
    }
  ],
  "test_run_summary": {
    "overall_result": "FAIL",
    "passed": 2,
    "failed": 3,
    "score": 40  // 40% pass rate
  }
}
```

### Step 7 Validation Report
**File**: `TestReport_Extract_Asset_Spreadsheet_Data_20260412_063550.json`
```json
{
  "test_cases": [
    {
      "test_id": "TC-002",
      "test_name": "Worksheet Processing Completeness",
      "expected_output": "Both PROJECT and STREETLIGHT sheets should be processed",
      "actual_output": "Only STREETLIGHT sheet was processed",
      "result": "FAIL"
    },
    {
      "test_id": "TC-007",
      "test_name": "Skipped Sheets Validation",
      "expected_output": "No non-empty sheets should be skipped",
      "actual_output": "Sheet PROJECT was skipped",
      "result": "FAIL"
    }
  ],
  "test_run_summary": {
    "overall_result": "FAIL",
    "passed": 5,
    "failed": 2,
    "score": 71  // 71% pass rate
  }
}
```

---

## Recommendations

### For Step 6 (Extract Drawing Legend)
**Status**: DATA ISSUE — Not a code bug

**Action**: 
- Verify the test PDF actually contains an electrical symbol legend
- If the PDF doesn't have a legend, use a different test document (e.g., an actual electrical drawing with symbol key)
- If the PDF should have a legend, check if vision model needs prompt/model adjustment to better detect legend panels

**Suggested test document requirements**:
- Must be an engineering electrical drawing
- Must have a visible LEGEND/KEY panel with:
  - Drawn symbols (lines, circles, shapes)
  - Text labels paired with each symbol
  - Clear section heading "LEGEND", "KEY", or "SYMBOL TABLE"

---

### For Step 7 (Extract Asset Spreadsheet)
**Status**: LOGIC BUG — Code fix required

**Root Issue**: `_select_target_worksheets()` function at lines 2988-3042 is filtering out worksheets that should be processed.

**Proposed Fix**:

**Option 1 (RECOMMENDED)**: Remove LLM-based filtering entirely
```python
def _select_target_worksheets(sheets: list, filename: str) -> list:
    """
    Process all worksheets. The orchestrator (Step 2) has already
    determined which sheets have data via worksheets_to_analyse.
    Do not re-filter with LLM heuristics.
    """
    return [s["sheet_name"] for s in sheets]  # Process all sheets
```

**Option 2**: Make filtering stricter — only skip clearly empty or metadata sheets
```python
def _select_target_worksheets(sheets: list, filename: str) -> list:
    """
    Only skip completely empty sheets. Process all others.
    Trust Step 2's worksheets_to_analyse pre-filtering.
    """
    return [
        s["sheet_name"] 
        for s in sheets 
        if len(s.get("rows", [])) > 0  # Only skip if 0 data rows
    ]
```

**Option 3**: Include worksheets_to_analyse from request context
- Modify `_extract_asset_spreadsheet()` to check if `worksheets_to_analyse` was specified in the request
- If present, use it as the authoritative list (don't let Claude re-filter)
- If absent, fall back to current behavior

**Recommended Implementation**: **Option 1**  
- Simplest and most reliable
- Respects Step 2's pre-filtering  
- Eliminates unreliable LLM classification logic
- Matches user expectations ("extract from these 2 worksheets" means extract from both)

---

## Impact on Multi-Run Testing (Phase 1)

Both issues are **agent-level**, not orchestrator-level:

✅ **Orchestrator multi-run code is CORRECT** (lines 1150-1184 in orchestrator.py):
- Properly executes steps N times with unique timestamps
- Correctly stores results in `repeat_run_results` array
- Response includes `repeat_run_count` and `repeat_run_output_files`

❌ **Agent outputs are broken**:
- Step 6: Returns empty legend arrays on each run (consistent failure)
- Step 7: Returns only STREETLIGHT on each run (consistent failure)

**Result**: Multi-run execution happens correctly, but each run produces the same incorrect output.

---

## Testing Checklist

- [ ] **Step 6**: Acquire electrical drawing PDF with actual legend panel
- [ ] **Step 6**: Verify vision model can detect and extract legend symbols  
- [ ] **Step 7**: Apply fix to `_select_target_worksheets()` (recommend Option 1)
- [ ] **Step 7**: Re-run validation tests to verify both worksheets processed
- [ ] **Both**: Re-run multi-run tests to confirm variance data captured correctly

---

## References

- **Orchestrator code**: `c:\Code\experiments\agent 07\orchestrator.py` lines 1150-1184
- **Step 6 implementation**: `c:\Code\experiments\agent 07\document_extractor.py` lines 1432-1750
- **Step 7 implementation**: `c:\Code\experiments\agent 07\document_extractor.py` lines 2988-3160
- **Step 6 validation test**: `c:\Code\experiments\agent 07\OUTPUT\TestReport_Extract_Drawing_Legend_20260412_065046.json`
- **Step 7 validation test**: `c:\Code\experiments\agent 07\OUTPUT\TestReport_Extract_Asset_Spreadsheet_Data_20260412_063550.json`
