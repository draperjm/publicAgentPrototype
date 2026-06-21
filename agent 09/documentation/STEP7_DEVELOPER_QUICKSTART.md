# Step 7 Enhancement - Developer Quick Start Guide

**Target Audience:** Development engineers implementing enhancements  
**Time Commitment:** 19 days total (Part-time or parallel work acceptable)  
**Difficulty:** Medium (build on proven patterns from Steps 5 & 6)

---

## What Are We Building?

Step 7 (Extract Asset Spreadsheet) will get **three major enhancements:**

1. **Multi-Run Testing** - Execute 3 times, check consistency
2. **Three Reliability Fixes** - Make output reproducible and accurate
3. **Automated Variance Reporting** - Documentation of improvements

**End Goal:** Zero-variance reproducibility (identical results every time)

---

## Before You Start

### Read These (30 minutes)
1. **STEP7_ENHANCEMENT_PLAN.md** - What we're building and why
2. **STEP7_vs_STEPS_5_6_COMPARISON.md** - See working examples in Steps 5 & 6
3. **This document** - You are here!

### Reference These (Keep handy)
- `agent 05/document_extractor.py` - See how Fix #1, #2, #3 work in practice
- `agent 05/step_validator_agent.py` - See test suite pattern
- `agent 05/STEP5_VARIANCE_ANALYSIS_REPORT.md` - See target results

---

## The 4-Week Implementation Plan

### Week 1: Foundation
**Goal:** Enable multi-run testing and start reporting

#### Day 1: Orchestrator Verification (2 hours)
- [ ] Open `/agent 07/orchestrator.py`
- [ ] Search for `repeat_runs` - should find `repeat_runs: Optional[int] = None`
- [ ] Look at `_run_extractor_step()` method
- [ ] **Action:** Implement loop to run step N times (see Part 1.2 in Technical Guide)
- [ ] **Verify:** Changes compile without syntax errors

**Files to modify:**
- `orchestrator.py` (add repeat_runs handling in _run_extractor_step)

#### Day 2: Test Configuration (1 hour)
- [ ] Create `test_step7_multirun.json` with `"repeat_runs": 3`
- [ ] Point it to a test TAL spreadsheet (50-100 assets)
- [ ] **Action:** Run orchestrator with new config
- [ ] **Verify:** 3 separate test reports are generated

**Files to create:**
- `test_step7_multirun.json`

#### Days 3-5: Variance Report Template & Test Suite (4 days)
- [ ] Study `agent 05/STEP5_VARIANCE_ANALYSIS_REPORT.md` structure
- [ ] Create `STEP7_VARIANCE_ANALYSIS_REPORT.md` template (copy structure, adapt for asset data)
- [ ] Add 6 test cases to `step_validator_agent.py` (TC-001 through TC-006)
- [ ] **Action:** Run tests on multi-run output
- [ ] **Verify:** Tests execute and generate pass/fail results

**Files to modify:**
- `step_validator_agent.py` (add STEP_7_TEST_CASES dict with 6 tests)
- `STEP7_VARIANCE_ANALYSIS_REPORT.md` (created with template)

### Week 2: First Fix - Robust Data Extraction
**Goal:** Implement Fix #1 and Fix #2

#### Days 6-7: Fix #1 - Asset ID Robustness (2 days)
**Problem:** Asset ID extraction fails with different column names  
**Solution:** Intelligent column mapping with fallbacks

- [ ] Open `document_extractor.py`
- [ ] Add function `_normalize_asset_id_extraction()` (reference Part 2.1)
- [ ] Add validation function `_validate_asset_id_format()`
- [ ] Integrate into `_extract_asset_spreadsheet()` before LLM call
- [ ] **Test:** Run extraction with TAL that has "Superior FL" vs "FLOC"
- [ ] **Verify:** Both produce identical results

**Files to modify:**
- `document_extractor.py`
  - Add `_normalize_asset_id_extraction(sheet, headers) → Dict`
  - Add `_extract_asset_id_from_record(record, mapping) → str`
  - Add `_validate_asset_id_format(asset_id) → bool`

#### Days 8-9: Fix #2 - pre-LLM Data Normalization (2 days)
**Problem:** Spreadsheet formatting variations cause different LLM outputs  
**Solution:** Normalize all cells before sending to LLM

- [ ] Open `document_extractor.py`
- [ ] Add function `_normalize_spreadsheet_data_pre_llm()` (reference Part 2.2)
- [ ] Add helper `_is_date_string()` and `_standardize_date()`
- [ ] Integrate into extraction pipeline BEFORE Pass 2 LLM call
- [ ] **Test:** Run with TAL containing variations:
  -  Trailing spaces in cells
  -  "11 kV" vs "11kV"
  -  "N/A", "n/a", "", "—" as null values
- [ ] **Verify:** Output is consistent regardless of input formatting

**Files to modify:**
- `document_extractor.py`
  - Add `_normalize_spreadsheet_data_pre_llm(sheet, name) → Dict`
  - Add `_is_date_string(value) → bool`
  - Add `_standardize_date(value) → str`

#### Days 10: Integration & Testing (2 days)
- [ ] Verify orchestrator + document_extractor work together
- [ ] Run multi-run test with fixes deployed
- [ ] **Check:** Do 3 runs return identical results? (target: 100%)
- [ ] Log results in variance report

**Files to update:**
- `STEP7_VARIANCE_ANALYSIS_REPORT.md` (Run 1, Run 2, Run 3 sections)

### Week 3: Second Fix & Comprehensive Testing
**Goal:** Implement Fix #3, complete test suite, validate all fixes

#### Days 11-13: Fix #3 - Post-LLM Validation (3 days)
**Problem:** LLM may hallucinate records or lose data  
**Solution:** Validate each record against source

- [ ] Open `document_extractor.py`
- [ ] Add function `_validate_asset_records()` (reference Part 2.3)
- [ ] Add helper functions for validation checks
- [ ] Integrate AFTER LLM extraction, BEFORE final output
- [ ] **Test:** Provide TAL and verify:
  -  Records with missing asset_id are rejected
  -  Records with invalid formats are rejected
  -  Valid records match source data
- [ ] **Verify:** Validation report shows <2% rejection rate on clean data

**Files to modify:**
- `document_extractor.py`
  - Add `_validate_asset_records(records, headers, source) → (valid, rejected)`
  - Add helper validation functions

#### Days 14-15: Complete Test Suite (2 days)
- [ ] Implement all 6 test checks in `step_validator_agent.py`
- [ ] Add variance analysis functions
- [ ] Run full test suite on multi-run output
- [ ] **Check:** All 6 tests pass with fixes deployed

**Files to modify:**
- `step_validator_agent.py` (implement test validation logic)

#### Days 16-17: Validation & Documentation (2 days)
- [ ] Run manual test of complete pipeline
- [ ] Populate `STEP7_VARIANCE_ANALYSIS_REPORT.md` with results
- [ ] Create summary table showing pre-fix vs post-fix improvements
- [ ] **Document:** Which fixes had most impact

**Files to update:**
- `STEP7_VARIANCE_ANALYSIS_REPORT.md` (complete with results)

### Week 4: Finalization & Deployment
**Goal:** Deploy to production, documentation complete

#### Days 18-19: Review, Polish, Deploy (2 days)
- [ ] Code review: check all new functions are tested
- [ ] Update orchestrator logging if needed
- [ ] Deploy changes to integration test environment
- [ ] Run production TAL through enhanced Step 7
- [ ] **Verify:** Multi-run test shows 0% variance
- [ ] Update `STEP7_ENHANCEMENT_PLAN.md` with "COMPLETE" status
- [ ] Tag code release: `step7_enhancements_v1.0`

**Files to update:**
- All modified files (orchestrator, document_extractor, step_validator)
- Create tag in git

---

## Day-by-Day Checklist

### Week 1
- [ ] **Day 1 (Thu):** Orchestrator multi-run loop implemented
- [ ] **Day 2 (Fri):** Test config created, multi-run execution verified
- [ ] **Day 3-5 (Mon-Wed):** Variance report template + test suite created and working

### Week 2
- [ ] **Day 6-7 (Thu-Fri):** Fix #1 (Asset ID robustness) implemented and tested
- [ ] **Day 8-9 (Mon-Tue):** Fix #2 (Data normalization) implemented and tested
- [ ] **Day 10 (Wed):** Integration testing, pre-fix variance report populated

### Week 3
- [ ] **Day 11-13 (Thu-Fri-Mon):** Fix #3 (Post-validation) implemented and tested
- [ ] **Day 14-15 (Tue-Wed):** Test suite complete, all 6 tests passing
- [ ] **Day 16-17 (Thu-Fri):** Validation complete, final variance report with results

### Week 4
- [ ] **Day 18-19 (Mon-Tue):** Code review, final deployment, documentation complete

---

## Success Criteria Checklist

By end of Week 4, you should have:

### Capability Checklist
- [ ] Multi-run testing: Step 7 executes N times with identical input
- [ ] Variance reporting: STEP7_VARIANCE_ANALYSIS_REPORT.md populated with metrics
- [ ] Fix #1 deployed: Asset ID extraction handles 5+ column name variants
- [ ] Fix #2 deployed: Pre-LLM normalization handles whitespace, nulls, units, dates
- [ ] Fix #3 deployed: Post-validation rejects invalid records with clear reasons
- [ ] Test suite: All 6 tests implemented (TC-001 through TC-006)

### Quality Checklist
- [ ] Character count variance across 3 runs: **0%** (target met)
- [ ] Asset record count variance: **0%** (target met)
- [ ] Field structure consistency: **100%** (target met)
- [ ] Test pass rate: **100%** (all 6 tests pass)
- [ ] Code review: Approved by team lead
- [ ] Production validation: Works on real TAL data

### Documentation Checklist
- [ ] Code changes documented with docstrings
- [ ] Test results captured in variance report
- [ ] Improvement metrics documented
- [ ] Fix deployment order documented
- [ ] git tag created: `step7_enhancements_v1.0`

---

## Common Pitfalls & How to Avoid Them

| Pitfall | What Happens | Prevention |
|---------|---|---|
| Fix #2 too aggressive | Valid data gets normalized away | Start loose (only whitespace), expand slowly, log all changes |
| Fix #3 rejects too much | Valid records rejected | Set to 98% acceptance threshold initially, review rejections |
| Tests too strict | False failures | Use 95%+ thresholds initially, tighten post-validation |
| Copy-paste errors from Steps 5/6 | Code doesn't work for Step 7 | Understand the pattern, adapt intentionally (don't blind copy) |
| Forget to handle nullable fields | Crashes on None values | Test with incomplete TALs, verify None handling |

---

## Getting Help

### Questions About Approach?
→ See **STEP7_ENHANCEMENT_PLAN.md** (strategic overview)

### Questions About Implementation Details?
→ See **STEP7_TECHNICAL_IMPLEMENTATION_GUIDE.md** (code examples)

### Need a Real Example?
→ Read **agent 05/document_extractor.py** (working implementation of all 3 fixes)

### Unsure if Your Fix Works?
→ Check against **STEP7_vs_STEPS_5_6_COMPARISON.md** (feature parity criteria)

---

## Key Files Overview

| File | Purpose | You Will |
|------|---------|----------|
| **orchestrator.py** | Orchestrate multi-run execution | Modify lines ~150-180 to add repeat_runs loop |
| **document_extractor.py** | Extract assets with 3 fixes | Add 3 new functions, integrate into extraction flow |
| **step_validator_agent.py** | Test and validate results | Add TEST_SUITE dict with 6 tests |
| **STEP7_VARIANCE_ANALYSIS_REPORT.md** | Document improvements | Create template, populate with results |
| **STEP7_ENHANCEMENT_PLAN.md** | Strategic requirements | Reference (don't modify - already complete) |
| **STEP7_TECHNICAL_IMPLEMENTATION_GUIDE.md** | Code examples | Reference (don't modify - already complete) |
| **STEP7_vs_STEPS_5_6_COMPARISON.md** | Feature alignment | Reference (don't modify - already complete) |
| **test_step7_multirun.json** | Test configuration | Create with `"repeat_runs": 3` |

---

## Timeline Summary

```
Week 1: Foundation (Days 1-5)
  Orchestrator + Variance Reporting
  
Week 2: Fixes #1 & #2 (Days 6-10)
  Asset ID Robustness + Data Normalization
  
Week 3: Fix #3 + Testing (Days 11-17)
  Post-Validation + Complete Tests
  
Week 4: Finalization (Days 18-19)
  Code Review + Production Deployment
```

**Estimated Total Effort:** 19 days development + 2-3 days for code review/deployment buffer

---

## After You're Done

1. **Monitor Performance**
   - Run weekly variance tests on diverse TALs
   - Alert if variance exceeds 2%
   - Update variance report monthly

2. **Maintain Fixes**
   - Log any edge cases where fixes fail
   - Quarterly review for continued effectiveness

3. **Pass Knowledge**
   - Document lessons learned
   - Use as template for future step enhancements (Step 8, etc.)

---

**Version:** 1.0  
**Created:** 2026-04-11  
**Start Date:** Week of April 13, 2026  
**Target Completion:** Week of April 27, 2026
