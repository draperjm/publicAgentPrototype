# Step 7 Variance Analysis Report

**Test Type:** Asset Spreadsheet Extraction Multi-Run Consistency  
**Document Type:** TAL (Technical Asset List)  
**Report Generated:** (To be populated during testing)

---

## Executive Summary

| Metric | Value | Target | Status |
|--------|-------|--------|--------|
| **Repeat Runs** | TBD | 3 | ⏳ |
| **Total Assets Extracted** | TBD | Consistent | ⏳ |
| **Character Count Variance** | TBD | 0% | ⏳ |
| **Field Structure Consistency** | TBD | 100% | ⏳ |
| **Test Pass Rate** | TBD | 100% | ⏳ |
| **Overall Status** | ⏳ TESTING IN PROGRESS | FIXED & VALIDATED | ⏳ |

---

## Test Configuration

| Parameter | Value |
|-----------|-------|
| Test Document | (to be specified) |
| Test Data Category | TAL (Asset Spreadsheet) |
| Orchestrator Config | test_step7_multirun.json |
| Extractor Route | Port 8090 (Document Extractor) |
| Knowledge ID | proc-extract-asset-spreadsheet |
| Repeat Runs | 3 |

---

## Complete Test Run History

(To be populated as tests execute)

| Run # | Timestamp | Assets Extracted | Output File | Status | Notes |
|-------|-----------|------------------|-------------|--------|-------|
| 1 | TBD | TBD | TBD | ⏳ | Collecting baseline |
| 2 | TBD | TBD | TBD | ⏳ | Consistency check |
| 3 | TBD | TBD | TBD | ⏳ | Final validation |

---

## Run-by-Run Analysis

### Run 1: Baseline Extraction

**Execution Timeline:**
- Start: TBD
- Complete: TBD
- Duration: TBD

**Results:**
- Total Assets: TBD
- Worksheets Processed: TBD
- Fields Extracted: TBD
- Output File: TBD

**Issues/Notes:**
- (To be documented)

---

### Run 2: Consistency Check

**Execution Timeline:**
- Start: TBD
- Complete: TBD
- Duration: TBD

**Results:**
- Total Assets: TBD
- Worksheets Processed: TBD
- Fields Extracted: TBD
- Output File: TBD

**Variance from Run 1:**
- Asset count difference: TBD
- Character count variance: TBD %
- Field inconsistencies: TBD

---

### Run 3: Final Validation

**Execution Timeline:**
- Start: TBD
- Complete: TBD
- Duration: TBD

**Results:**
- Total Assets: TBD
- Worksheets Processed: TBD
- Fields Extracted: TBD
- Output File: TBD

**Variance from Run 1:**
- Asset count difference: TBD
- Character count variance: TBD %
- Field inconsistencies: TBD

---

## Consistency Analysis Matrix

### Asset Record Count Consistency

| Run | Count | Status |
|-----|-------|--------|
| Run 1 | TBD | ⏳ |
| Run 2 | TBD | ⏳ |
| Run 3 | TBD | ⏳ |
| **Consistency** | **TBD** | **⏳** |

**Analysis:** (To be populated)

---

### Field-Level Consensus

| Field | Run 1 | Run 2 | Run 3 | Agreement % | Consistency Status |
|-------|-------|-------|-------|-------------|-------------------|
| asset_id | TBD | TBD | TBD | TBD % | ⏳ |
| description | TBD | TBD | TBD | TBD % | ⏳ |
| quantity | TBD | TBD | TBD | TBD % | ⏳ |
| manufacturer | TBD | TBD | TBD | TBD % | ⏳ |
| voltage_rating | TBD | TBD | TBD | TBD % | ⏳ |
| location | TBD | TBD | TBD | TBD % | ⏳ |

**Analysis:** (To be populated)

---

## Variance Report Checks

### Check #1: Asset Record Count Consistency

**Description:** Verify all runs extract the same total number of asset records

**Expected Result:** All 3 runs report identical asset count

**Pre-Fix Result:** TBD  
**Post-Fix Result:** TBD  
**Status:** ⏳ TESTING

**Improvement:** TBD %

---

### Check #2: Asset ID Format Validity

**Description:** Verify extracted asset IDs match expected format pattern

**Pattern:** `[A-Z0-9]+-[0-9]{3,}` (e.g., TX-1234, SW-5678)

**Pre-Fix Pass Rate:** TBD %  
**Post-Fix Pass Rate:** TBD %  
**Status:** ⏳ TESTING

---

### Check #3: Asset Record Field Consistency

**Description:** Verify all records have identical field structures

**Expected:** All records contain same fields (asset_id, description, etc.)

**Pre-Fix Consistency:** TBD %  
**Post-Fix Consistency:** TBD %  
**Status:** ⏳ TESTING

---

### Check #4: Null Field Consistency

**Description:** Verify same fields are null/missing across all runs

**Expected:** If asset_id is null in Run 1, it's null in Runs 2 & 3 for same asset

**Pre-Fix Consistency:** TBD %  
**Post-Fix Consistency:** TBD %  
**Status:** ⏳ TESTING

---

### Check #5: Data Type Consistency

**Description:** Verify field data types are consistent across runs

**Expected:** asset_id always string, quantity always number, etc.

**Pre-Fix Pass Rate:** TBD %  
**Post-Fix Pass Rate:** TBD %  
**Status:** ⏳ TESTING

---

### Check #6: Extraction Validation Pass Rate

**Description:** Verify post-extraction validation accepts consistent records

**Expected:** >98% of extracted records pass validation across all runs

**Pre-Fix Pass Rate:** TBD %  
**Post-Fix Pass Rate:** TBD %  
**Status:** ⏳ TESTING

---

## Fix Verification Results

### Fix #1: Robust Asset ID Extraction

**Status:** ⏳ DEPLOYMENT PENDING  
**Implementation:** Intelligent column mapping with fallback chain  
**Expected Impact:** Handle 5+ column name variants (Superior FL, FLOC, Asset ID, Tag, etc.)

**Verification Results:**
- Column variants handled: TBD / 5
- Extraction success rate: TBD %
- Fallback chain tested: TBD

---

### Fix #2: Pre-LLM Data Normalization

**Status:** ⏳ DEPLOYMENT PENDING  
**Implementation:** Normalize whitespace, nulls, voltage notation, date formats  
**Expected Impact:** Consistent tokenization regardless of input formatting

**Verification Results:**
- Normalization operations logged: TBD
- Character count variance: TBD %
- Consistent output guaranteed: TBD

---

### Fix #3: Post-LLM Validation

**Status:** ⏳ DEPLOYMENT PENDING  
**Implementation:** Validate records against source and reject hallucinations  
**Expected Impact:** Remove invalid/impossible records

**Verification Results:**
- Records validated: TBD
- Rejection rate: TBD %
- False rejection rate: TBD %

---

## Summary of Improvements

| Metric | Pre-Fix | Post-Fix | Improvement |
|--------|---------|----------|-------------|
| Character Count Variance | TBD % | TBD % | TBD |
| Asset Count Variance | TBD % | TBD % | TBD |
| Field Consistency | TBD % | TBD % | TBD |
| Test Pass Rate | TBD % | TBD % | TBD |
| Overall Reproducibility | TBD | TBD | TBD |

---

## Issues & Findings

(To be documented during testing)

### High-Priority Issues
- (None identified yet)

### Medium-Priority Issues
- (None identified yet)

### Low-Priority Issues
- (None identified yet)

---

## Appendix: Test Metadata

### Document Information
- Document Name: TBD
- File Size: TBD KB
- Estimated Rows: TBD
- Worksheets: TBD

### Extraction Configuration
- Knowledge ID: proc-extract-asset-spreadsheet
- Target Category: TAL
- Extraction passes: 2 (worksheet selection + record extraction)
- Timeout per run: 600 seconds

### Validation Configuration
- Validation enabled: Yes
- Min pass rate target: 95%
- Test cases: 6 (TC-001 through TC-006)

### Test Environment
- Orchestrator: Port 8001
- Document Extractor: Port 8090
- Test Date: TBD
- Execution Duration: TBD

---

## Sign-Off

| Role | Status | Date | Notes |
|------|--------|------|-------|
| QA Lead | ⏳ Testing | TBD | Awaiting test execution |
| Tech Lead | ⏳ Pending | TBD | Awaiting results review |
| Project Manager | ⏳ Pending | TBD | Awaiting completion |

---

**Report Version:** 1.0  
**Last Updated:** TBD  
**Status:** TESTING IN PROGRESS  
**Next Review:** Upon completion of Phase 1 testing
