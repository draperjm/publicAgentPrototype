# Step 7 Enhancement Plan: Multi-Run Testing, Variance Reporting, and Consistency Improvements

**Objective:** Update Step 7 (Extract Asset Spreadsheet) with capabilities matching Steps 5 and 6, including multi-run testing, variance reporting, consistency improvements, and deployment of proven fixes.

**Date:** 2026-04-11  
**Status:** PLANNING

---

## 1. Current State Assessment

### Step 7 Current Capabilities
- ✅ Single-pass asset spreadsheet extraction
- ✅ LLM-based worksheet selection (Pass 1)
- ✅ Structured asset record extraction (Pass 2)
- ✅ Basic validation in step_validator_agent.py
- ❌ No multi-run testing framework
- ❌ No variance reporting
- ❌ No consistency/reproducibility monitoring
- ❌ No applied fixes for accuracy improvement

### Steps 5 & 6 Capabilities (Model)
- ✅ Multi-run testing support (`repeat_runs` in orchestrator)
- ✅ Variance analysis reporting
- ✅ Three-layer consistency enforcement:
  - **Fix #1:** Regex-based boundary detection (prevents scope creep)
  - **Fix #2:** Pre-consolidation OCR normalization (ensures consistent tokenization)
  - **Fix #3:** Post-extraction validation (rejects invalid content)
- ✅ Comprehensive test suites
- ✅ Zero-variance reproducibility

---

## 2. Enhancement Scope

### Phase 1: Multi-Run Testing Infrastructure (Week 1)
**Goal:** Enable Step 7 to execute N times with identical input for variance testing

**Tasks:**
1. **Orchestrator Support** ✓ Already Present
   - `repeat_runs: Optional[int]` is already in PlanStep model
   - Need to verify _run_extractor_step handles repeat_runs correctly
   
2. **Test Configuration**
   - Create test workflow JSON with `"repeat_runs": 3` for Step 7
   - Set up test ASL spreadsheet (vendor TAL with 50-200 asset rows)
   - Document execution sequence and expected filenames

3. **Output Management**
   - Timestamps per run (e.g., TestReport_Extract_Asset_Spreadsheet_20260411_123456.json)
   - Consolidated results tracking
   - File organization for multi-run comparison

### Phase 2: Variance Reporting Framework (Week 1-2)
**Goal:** Create comprehensive variance analysis for Step 7 extraction consistency

**Deliverables:**

1. **Variance Report Template** (c:\Code\experiments\agent 07\STEP7_VARIANCE_ANALYSIS_REPORT.md)
   - Executive summary of consistency metrics
   - Run-by-run analysis tables
   - Consistency analysis matrices
   - Field-level consensus tracking
   - Issue status documentation
   - Fix verification results

2. **Variance Metrics to Track**
   - Character count variance across runs
   - Asset record count consistency
   - Field-level agreement (asset_id, description, etc.)
   - Null field consistency
   - Data type consistency per field
   - Deployment-specific fields (source_sheet, source_document)

3. **Variance Check Suite**
   - Check #1: Record Count Consistency (total assets extracted)
   - Check #2: Field Structure Consistency (all records have same fields)
   - Check #3: Asset ID Validity (extracted IDs match expected patterns)
   - Check #4: Description Completeness (no truncation, field count matches)
   - Check #5: Data Type Consistency (dates as strings, numbers as numbers)
   - Check #6: Test Pass Rate (validation suite results)

### Phase 3: Consistency Improvements (Week 2-3)
**Goal:** Implement three-layer consistency framework analogous to Steps 5 & 6

**Fix #1: Robust Asset ID Extraction**
- **Problem:** Asset ID mapping rules may vary across spreadsheet formats (column names, positions)
- **Current Behavior:** Uses LLM to map "Superior Functional Location" → asset_id
- **Enhancement:**
  - Add prefix regex validation: `[A-Z0-9]+-[0-9]{4,}` pattern recognition
  - Implement intelligent fallback: Superior FL → Functional Location → Asset ID → Tag
  - Normalize variant spellings: "FLOC" ↔ "FL", "SuperiorFL" ↔ "Superior Functional Location"
  - Validate extracted IDs against common patterns (e.g., Tx-1234, Sw-5678)

**Fix #2: Pre-LLM Data Normalization**
- **Problem:** Spreadsheet cells with inconsistent formatting cause tokenization variance
- **Current Behavior:** Sends raw tab-separated data directly to LLM
- **Enhancement:**
  - Normalize whitespace: trim trailing/leading spaces in all cells
  - Standardize missing values: replace blank cells, "N/A", "n/a", "—", "–" with explicit null
  - Normalize voltage notation: "11kV" ↔ "11 kV" ↔ "11kv" → standardized "11kV"
  - Normalize date formats: apply common patterns for DD/MM/YYYY, MM/DD/YYYY, ISO format
  - Clean manufacturer names: remove trailing spaces, standardize case (e.g., "ABB" vs "abb")

**Fix #3: Post-Extraction Validation**
- **Problem:** LLM can hallucinate or incorrectly map data; no validation before output
- **Current Behavior:** Accepts all LLM-generated records without filtering
- **Enhancement:**
  - Validate required fields: asset_id must be non-null and match pattern
  - Validate optional fields: when present, must match expected data type
  - Detect hallucination: cross-check extracted values against original spreadsheet cells
  - Reject invalid records: flag and remove records with missing asset_id
  - Generate validation report: log rejected records and reasons

---

## 3. Implementation Details

### 3.1 Orchestrator Integration

**File:** `c:\Code\experiments\agent 07\orchestrator.py`

**Current Code (line ~45):**
```python
class PlanStep(BaseModel):
    step_number: int
    description: str
    name: Optional[str] = None
    assigned_resource_id: Optional[str] = None
    required_resources: Optional[StepResources] = None
    validation: Optional[StepValidation] = None
    parallel_group: Optional[str] = None
    repeat_runs: Optional[int] = None  # If > 1, executes N times for variance testing
```

**Verify in _run_extractor_step():**
- [ ] Check if `step.repeat_runs` is read from input
- [ ] Confirm step execution loops N times with identical input
- [ ] Verify output files saved with unique timestamps per run
- [ ] Confirm state["results"] accumulates results from all runs

---

### 3.2 Document Extractor Enhancements

**File:** `c:\Code\experiments\agent 07\document_extractor.py`

**Function: _extract_asset_spreadsheet() Enhancement**

Add three new helper functions:

**A) Asset ID Extraction Robustness**
```python
def _normalize_asset_id_extraction(fe: FileEntry, sheets: list) -> dict:
    """
    Pre-extract likely asset ID columns by analyzing sheet structure.
    Returns mapping of sheet_name → asset_id_column_index.
    """
    # Scan headers for asset ID patterns
    # Apply regex: [A-Z0-9]+-[0-9]{4,}
    # Return confidence scoring
```

**B) Pre-LLM Data Normalization**
```python
def _normalize_spreadsheet_data_pre_llm(sheet: dict) -> dict:
    """
    Normalize all cells in sheet before sending to LLM.
    
    Normalizations:
    - Whitespace: trim leading/trailing spaces
    - Missing values: standardize to null
    - Voltage notation: normalize to canonical form (e.g., "11kV")
    - Date formats: detect and standardize
    - Manufacturer names: trim and standardize case
    """
```

**C) Post-LLM Validation**
```python
def _validate_asset_records(records: list, sheet_headers: list) -> tuple[list, list]:
    """
    Validate extracted asset records and return (valid_records, rejected_records).
    
    Validation checks:
    - Required fields (asset_id) present and non-null
    - Asset ID matches pattern [A-Z0-9]+-[0-9]{4,}
    - Optional fields match expected types
    - Cross-check against original spreadsheet
    
    Returns: (validated_records, rejected_with_reasons)
    """
```

**Integration Point:**
```python
# In _extract_asset_spreadsheet(), after Pass 2 extraction:
all_asset_records = []
for sheet in target_sheets:
    raw_records = _extract_records_from_sheet(sheet)  # current Pass 2
    
    # NEW: Apply validation
    valid_records, rejected = _validate_asset_records(
        raw_records, sheet["headers"]
    )
    all_asset_records.extend(valid_records)
    
    # Log rejections for debugging
    if rejected:
        logger.warning(f"Rejected {len(rejected)} records from {sheet['sheet_name']}: {rejected}")
```

---

### 3.3 Test Suite Enhancements

**File:** `c:\Code\experiments\agent 07\step_validator_agent.py`

**New Test Cases for Step 7:**

```python
TEST_CASE_SUITE = [
    # TC-001: COMPLETENESS – Total Record Count
    {
        "test_id": "TC-001",
        "test_name": "Asset Record Count Consistency",
        "category": "COMPLETENESS",
        "description": "Verify all runs extract same number of asset records",
        "validation": "compare_field_across_runs('step_extraction.total_assets')"
    },
    
    # TC-002: CORRECTNESS – Asset ID Validity
    {
        "test_id": "TC-002",
        "test_name": "Asset ID Format Validation",
        "category": "CORRECTNESS",
        "description": "Verify extracted asset IDs match pattern [A-Z0-9]+-[0-9]{4,}",
        "validation": "regex_test_all_records('asset_id', ASSET_ID_PATTERN)"
    },
    
    # TC-003: CONSISTENCY – Field Structure
    {
        "test_id": "TC-003",
        "test_name": "Asset Record Field Consistency",
        "category": "CONSISTENCY",
        "description": "Verify all records have identical field sets",
        "validation": "compare_field_set_all_records()"
    },
    
    # TC-004: DATA_INTEGRITY – Null Field Consistency
    {
        "test_id": "TC-004",
        "test_name": "Null Field Consistency",
        "category": "DATA_INTEGRITY",
        "description": "Verify same fields are null across all runs for given asset",
        "validation": "compare_null_fields_across_runs()"
    },
    
    # TC-005: FORMAT – Data Type Consistency
    {
        "test_id": "TC-005",
        "test_name": "Data Type Consistency",
        "category": "FORMAT",
        "description": "Verify field data types consistent across runs",
        "validation": "validate_data_types(EXPECTED_TYPES)"
    },
    
    # TC-006: CONSISTENCY – Test Pass Rate
    {
        "test_id": "TC-006",
        "test_name": "Validation Test Pass Rate",
        "category": "CONSISTENCY",
        "description": "Verify all runs pass validation tests",
        "validation": "test_run_summary.passed == test_run_summary.total_tests"
    },
]
```

---

### 3.4 Variance Report Template

**File:** `c:\Code\experiments\agent 07\STEP7_VARIANCE_ANALYSIS_REPORT.md`

**Structure (mimics STEP5_VARIANCE_ANALYSIS_REPORT.md):**

1. **Executive Summary**
   - Status: FIXED & VALIDATED (post-implementation)
   - Key metrics: Record count consistency, field agreement %
   - Overall variance assessment

2. **Complete Test Run History**
   - All runs in execution order (pre-fix and post-fix)
   - Metrics: Record count, field consistency, test pass rate
   - Pre/post improvement comparison table

3. **Run-by-Run Analysis**
   - Run 1: X asset records, Y% field agreement, Z/10 tests pass
   - Run 2: X asset records, Y% field agreement, Z/10 tests pass
   - Run 3: X asset records, Y% field agreement, Z/10 tests pass
   - (Analysis of variance and completeness per run)

4. **Consistency Analysis Matrix**
   - By metric: character count, record count, null field patterns
   - Field-level consensus table (asset_id, description, etc.)
   - Consistency scoring

5. **Fix Verification Results**
   - Fix #1 (Asset ID Robustness): Status DEPLOYED & OPERATIONAL
   - Fix #2 (Data Normalization): Status DEPLOYED & OPERATIONAL
   - Fix #3 (Post-Validation): Status DEPLOYED & OPERATIONAL

6. **Variance Report Checks**
   - Check #1-6 documented with pre-fix vs post-fix results
   - Improvement metrics table

7. **Appendix: Test Metadata**
   - Document metadata (spreadsheet size, sheet count)
   - Extraction parameters used
   - Validation thresholds applied

---

## 4. Success Criteria

### Phase 1 Completion (Multi-Run Testing)
- [ ] Step 7 execution with `repeat_runs: 3` produces 3 unique test reports
- [ ] Each report has unique timestamp in filename
- [ ] All reports captured in consolidated results
- [ ] Orchestrator logs show all 3 runs completed

### Phase 2 Completion (Variance Reporting)
- [ ] STEP7_VARIANCE_ANALYSIS_REPORT.md created and populated
- [ ] All 6 variance checks implemented
- [ ] Pre-fix and post-fix results documented
- [ ] Improvement metrics table shows reduction from >5% to <1% variance

### Phase 3 Completion (Consistency Improvements)
- [ ] Fix #1 code deployed: Asset ID extraction handles 5+ column name variants
- [ ] Fix #2 code deployed: Pre-LLM normalization handles whitespace, nulls, units, dates
- [ ] Fix #3 code deployed: Post-LLM validation rejects <2% of records with clear reasons
- [ ] Test suite confirms: 100% consistency across 3-run test with fixes deployed
- [ ] Character count variance: 0% (all runs extract identical fields)
- [ ] Record count variance: 0% (all runs extract same number of assets)
- [ ] Field agreement: 100% (all fields consistent across runs)

---

## 5. Implementation Timeline

| Phase | Task | Duration | Target Date |
|-------|------|----------|-------------|
| Phase 1 | Multi-run testing setup | 2 days | 2026-04-13 |
| Phase 1 | Orchestrator verification | 1 day | 2026-04-14 |
| Phase 2 | Variance reporting framework | 3 days | 2026-04-17 |
| Phase 2 | Test suite definition | 2 days | 2026-04-19 |
| Phase 3 | Fix #1 implementation | 2 days | 2026-04-21 |
| Phase 3 | Fix #2 implementation | 2 days | 2026-04-23 |
| Phase 3 | Fix #3 implementation | 3 days | 2026-04-26 |
| Phase 3 | Validation & testing | 2 days | 2026-04-28 |
| Final | Documentation & deployment | 2 days | 2026-04-30 |

**Total Estimated Effort:** 19 days

---

## 6. Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|-----------|
| LLM cost for multi-run testing | Medium | Implement with small test TAL (50-100 rows) |
| Pre-normalization may mask real data issues | Medium | Log all normalization operations for audit |
| Post-validation too aggressive (false rejections) | High | Implement with loose thresholds first, tighten iteratively |
| Incompatibility with existing Step 7 workflows | Low | Maintain backward compatibility; add fixes as optional |

---

## 7. Dependencies

- ✓ Orchestrator already supports `repeat_runs`
- ✓ Step validator infrastructure already in place
- ✓ Document extractor framework supports new helper functions
- ✓ Google AI / Claude available for LLM calls

**No external dependencies**

---

## 8. Post-Implementation Monitoring

- Monthly variance testing on diverse TAL files
- Alert if variance exceeds 2% on any metric
- Quarterly review of applied fixes for continued effectiveness
- Maintain STEP7_VARIANCE_ANALYSIS_REPORT.md with latest results

---

**Document Version:** 1.0  
**Last Updated:** 2026-04-11  
**Owner:** Development Team
