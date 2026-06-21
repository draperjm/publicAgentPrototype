# STEP 7 WORKSHEET FILTERING FIX - APPLIED

## Change Summary
**File**: `c:\Code\experiments\agent 07\document_extractor.py`  
**Lines**: 2988-2999  
**Function**: `_select_target_worksheets()`

## What Was Fixed
Replaced 54-line LLM-based worksheet filtering function with a 3-line deterministic filter:

### BEFORE (Problematic)
```python
# Claude-based filtering that incorrectly classified PROJECT as "non-asset"
# and skipped it despite being a valid worksheet with 25 rows × 13 columns
result = _llm_call(prompt)  # Ask Claude which sheets to process
targets = result.get("target_sheets") or []
return [t for t in targets if t in valid_names]
# RESULT: Only returned ['STREETLIGHT'], skipped ['PROJECT']
```

### AFTER (Fixed)
```python
def _select_target_worksheets(sheets: list, filename: str) -> list:
    """
    Process all worksheets with data rows.
    The orchestrator (Step 2) has already determined which sheets are worth processing
    via worksheets_to_analyse in the processing plan. We should not re-filter with
    unreliable LLM heuristics that incorrectly classify valid asset sheets as summaries.
    Returns list of all non-empty worksheet names.
    """
    target_names = [s["sheet_name"] for s in sheets if len(s.get("rows", [])) > 0]
    logger.info(f"Processing {len(target_names)} non-empty worksheets from '{filename}': {target_names}")
    return target_names
# RESULT: Returns ['PROJECT', 'STREETLIGHT'] - both non-empty worksheets
```

## Why This Fix Works
1. **Removes unreliable LLM heuristics**: Claude was making subjective decisions about which worksheets looked "asset-like"
2. **Respects orchestrator pre-filtering**: Step 2 already identified valid worksheets via `worksheets_to_analyse`
3. **Simple and deterministic**: Only filters out completely empty worksheets (0 rows), which is objective
4. **Matches user expectations**: "Analyze these 2 worksheets" means analyze both worksheets

## Expected Improvement
**Previous behavior**:
- Input: 2 worksheets (PROJECT: 25 rows, STREETLIGHT: 55 rows)
- Output: Only 1 worksheet → 18 asset records
- Validation score: 71% (TC-002 and TC-007 fail)

**After fix**:
- Input: 2 worksheets (PROJECT: 25 rows, STREETLIGHT: 55 rows)  
- Output: Both worksheets → ~70+ asset records
- Expected validation score: 100% (all tests pass)

## Testing Status
- ✓ Code change applied and verified
- ⏳ Pending validation test run to confirm both worksheets now process
- ⏳ Pending multi-run variance test with corrected output

## Next Steps
1. Run Step 7 through orchestrator to generate new validation test report
2. Verify `sheets_processed` now includes both `['PROJECT', 'STREETLIGHT']`
3. Confirm validation score improves from 71% to 100%
4. Run full multi-run test to capture variance analysis data

---

## REMAINING ISSUES

### Step 6: Extract Drawing Legend (Still Broken)
- **Status**: DATA ISSUE - not a code bug
- **Root Cause**: Test PDF doesn't contain an electrical symbol legend
- **Fix Required**: Replace test PDF with actual engineering drawing containing symbol legend
- **Evidence**: All 5 image chunks process successfully, but return empty legend array because no symbols are present

See: [DEBUG_STEP6_STEP7_FAILURES.md](DEBUG_STEP6_STEP7_FAILURES.md) for full analysis.
