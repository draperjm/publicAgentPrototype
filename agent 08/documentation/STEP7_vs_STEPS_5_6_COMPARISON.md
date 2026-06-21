# Step 7 vs Steps 5 & 6: Feature Parity Roadmap

**Purpose:** Show what Step 7 will have after enhancements by comparing with proven implementations  
**Document Type:** Reference guide for developers

---

## Executive Summary

Steps 5 and 6 have proven reliability features that Step 7 currently lacks. This guide maps the gaps and shows exactly what Step 7 will gain.

| Capability | Step 5 | Step 6 | Step 7 Current | Step 7 After Enhancements |
|-----------|--------|--------|---|---|
| Single-pass extraction | ✅ | ✅ | ✅ | ✅ |
| Multi-run variance testing | ✅ | ✅ | ❌ | ✅ |
| Variance analysis reporting | ✅ | ✅ | ❌ | ✅ |
| Three-layer consistency fixes | ✅ | ✅ | ❌ | ✅ |
| Automated test suite | ✅ | ✅ | ⚠️ Basic | ✅ Comprehensive |
| Zero-variance reproducibility | ✅ | ✅ | ❌ | ✅ |

---

## Detailed Feature Comparison

### 1. Multi-Run Testing Infrastructure

#### Step 5 (Extract Site Plan Notes) - Reference Implementation

**Orchestrator:** `agent 05/orchestrator.py` (line ~150)
```python
async def _run_extractor_step(self, step: PlanStep, ...):
    repeat_count = step.repeat_runs or 1
    all_results = []
    
    for run_num in range(1, repeat_count + 1):
        result = await self.document_extractor.extract_site_plan_notes_from_document(
            file_entries=file_entries,
            step_params=step.model_dump()
        )
        all_results.append({
            "run_number": run_num,
            "timestamp": datetime.now().isoformat(),
            "result": result
        })
    
    return {
        "multi_run_test": True if repeat_count > 1 else False,
        "repeat_runs": repeat_count,
        "runs": all_results
    }
```

#### Step 6 (Extract Drawing Legend) - Reference Implementation
Similar structure to Step 5, proven reliable.

#### Step 7 (Extract Asset Spreadsheet) - Gap
Currently does NOT check `step.repeat_runs` parameter.
Will inherit orchestrator loop structure from Steps 5 & 6 (See STEP7_TECHNICAL_IMPLEMENTATION_GUIDE.md Part 1.2)

---

### 2. Variance Reporting

#### Step 5 Variance Report
**File:** `agent 05/STEP5_VARIANCE_ANALYSIS_REPORT.md` (2,400 lines, comprehensive)

**Sections:**
1. Executive summary with metrics
2. Test run history table
3. Run-by-run analysis (Run 1, Run 2, Run 3, Run 4, Run 5)
4. Consistency analysis matrix
5. Field-level consensus tracking
6. Fix verification results
7. Variance report checks (6 checks)
8. Appendix with metadata

**Key Metrics Tracked:**
- Character count consistency across runs (target: 0%)
- Note count consistency (target: 0%)
- Field-level agreement percentages
- Null field patterns
- Data type consistency

#### Step 6 Variance Report
**File:** `agent 06/STEP6_VARIANCE_ANALYSIS_REPORT.md`
Similar structure to Step 5, proves pattern is replicable.

#### Step 7 Variance Report (Post-Enhancement)
**File:** `agent 07/STEP7_VARIANCE_ANALYSIS_REPORT.md` (to be created)

Will follow same template with Step 7-specific metrics:
- Asset record count consistency
- Asset ID format consistency
- Field structure consistency
- Data type consistency across runs
- Null field patterns
- Validation acceptance rate consistency

---

### 3. Three-Layer Consistency Fixes

#### Step 5 Implementation: Planning Notes Extraction

**Fix #1: Robust Section Identification (Regex-based boundary detection)**
- **Problem:** Site plan notes scattered across pages with varying section headers
- **Solution:** Use regex to detect "SITE PLAN" / "NOTES" / "LEGEND" section boundaries instead of pure LLM parsing
- **Result:** Prevents scope creep, ensures consistent content boundaries

**Code Location:** `agent 05/document_extractor.py` → `_identify_site_plan_sections()`
```python
def _identify_site_plan_sections(text: str) -> List[Tuple[int, int]]:
    # Regex patterns for section boundaries
    patterns = [
        r"(?i)(^|\n)SITE\s+PLAN.*?(?=\n[A-Z]{3,}|$)",
        r"(?i)(^|\n)NOTES.*?(?=\n[A-Z]{3,}|$)",
        r"(?i)(^|\n)LEGEND.*?(?=\n[A-Z]{3,}|$)",
    ]
    # Extract positions and return span tuples
    return section_spans
```

**Fix #2: Pre-Processing OCR Normalization (Consistent tokenization)**
- **Problem:** OCR artifacts and inconsistent spacing cause LLM to tokenize differently on each run
- **Solution:** Normalize OCR output before sending to LLM (fix spaces, standardize line breaks)
- **Result:** Same input → same output guaranteed

**Code Location:** `agent 05/document_extractor.py` → `_normalize_ocr_output()`
```python
def _normalize_ocr_output(ocr_text: str) -> str:
    # Remove extra spaces: "text  with   spaces" → "text with spaces"
    text = re.sub(r' +', ' ', ocr_text)
    # Normalize line breaks: multiple newlines → single newline
    text = re.sub(r'\n+', '\n', text)
    # Trim lines: remove leading/trailing spaces per line
    text = '\n'.join(line.strip() for line in text.split('\n'))
    return text
```

**Fix #3: Post-Processing Validation (Reject hallucinations)**
- **Problem:** LLM may hallucinate content not in source or truncate fields
- **Solution:** Validate each extracted note against original source and schema
- **Result:** Removes invalid data, increasing consistency

**Code Location:** `agent 05/document_extractor.py` → `_validate_extracted_notes()`
```python
def _validate_extracted_notes(notes: List[Dict], source_text: str) -> List[Dict]:
    validated = []
    for note in notes:
        # Check: Note text appears in source
        if note["content"] in source_text:
            # Check: Title and section are reasonable
            if len(note["title"]) < 200 and len(note["content"]) < 5000:
                validated.append(note)
    return validated
```

---

#### Step 6 Implementation: Drawing Legend Extraction

**Fix #1: Legend Symbol Detection (Pattern recognition)**
- **Problem:** Legend symbols vary by document type (electrical: ▬, mechanical: ⊗, etc.)
- **Implementation:** Pre-scan legend section for symbol candidates before LLM processing

**Fix #2: OCR Noise Removal (Data cleaning)**
- **Problem:** Legend often has small text with OCR errors
- **Implementation:** Apply correction dictionary for common legend terms (e.g., "Bx" → "Box")

**Fix #3: Symbol-Description Validation (Cross-reference)**
- **Problem:** Extracted legend might separate symbols from descriptions
- **Implementation:** Validate each symbol has matching description in original legend grid

---

#### Step 7 Implementation: Asset Spreadsheet Extraction

Will apply analogous fixes:

**Fix #1: Asset ID Robust Extraction (🎯 FOCUS)**
- **Problem:** Column name conventions vary (Superior FL, FLOC, Asset ID, Tag)
- **Solution:** Implement intelligent column mapping with fallback chain
- **Implementation:** `_normalize_asset_id_extraction()` in STEP7_TECHNICAL_IMPLEMENTATION_GUIDE.md Part 2.1

**Fix #2: Pre-LLM Data Normalization (🎯 FOCUS)**
- **Problem:** Spreadsheet cells with inconsistent formatting (spacing, null values, units)
- **Solution:** Normalize all cells before LLM extraction (whitespace, voltage "11kV" vs "11 kV", dates)
- **Implementation:** `_normalize_spreadsheet_data_pre_llm()` in STEP7_TECHNICAL_IMPLEMENTATION_GUIDE.md Part 2.2

**Fix #3: Post-LLM Validation (🎯 FOCUS)**
- **Problem:** LLM may hallucinate asset records or incorrectly map columns
- **Solution:** Validate each record against source spreadsheet and schema
- **Implementation:** `_validate_asset_records()` in STEP7_TECHNICAL_IMPLEMENTATION_GUIDE.md Part 2.3

---

### 4. Automated Test Suites

#### Step 5 Test Cases (Reference)

**File:** `agent 05/step_validator_agent.py` → TEST_SUITE

| Test ID | Category | Purpose | Threshold |
|---------|----------|---------|-----------|
| TC-001 | COMPLETENESS | Note count consistency | 100% |
| TC-002 | CORRECTNESS | Section identification accuracy | 100% |
| TC-003 | CONSISTENCY | Character count variance | <1% |
| TC-004 | DATA_INTEGRITY | Null field consistency | 95% |
| TC-005 | FORMAT | Data type consistency | 100% |
| TC-006 | CONSISTENCY | Field structure match | 100% |

#### Step 6 Test Cases (Reference)
Similar to Step 5, adapted for legends.

#### Step 7 Test Cases (New)

**File:** `agent 07/step_validator_agent.py` → STEP_7_TEST_CASES

Will have 6 tests analogous to Steps 5 & 6:

| Test ID | Category | Purpose | Threshold |
|---------|----------|---------|-----------|
| TC-001 | COMPLETENESS | Asset record count consistency | 100% |
| TC-002 | CORRECTNESS | Asset ID format validation | 100% |
| TC-003 | CONSISTENCY | Field structure consistency | 100% |
| TC-004 | DATA_INTEGRITY | Null field consistency | 95% |
| TC-005 | FORMAT | Data type consistency | 100% |
| TC-006 | CONSISTENCY | Post-validation pass rate | 100% (>98% acceptance) |

**Implementation:** STEP7_TECHNICAL_IMPLEMENTATION_GUIDE.md Part 3

---

### 5. Goal: Zero-Variance Reproducibility

#### Step 5 Achievement
From `STEP5_VARIANCE_ANALYSIS_REPORT.md`:
- **5 test runs** with identical input TAL
- **Result:** 0% character count variance, 0% section count variance
- **Interpretation:** After fixes, Step 5 produces identical output given identical input

#### Step 6 Achievement
Similar zero-variance results.

#### Step 7 Goal (Post-Enhancement)
Target the same: **0% variance across 3 test runs**

**Success Metrics:**
- Run 1 asset count = Run 2 asset count = Run 3 asset count
- All runs extract identical asset_id values
- All runs produce identical field structures
- All runs reject invalid records consistently

---

## Implementation Roadmap

### Timeline Aligned with Steps 5 & 6

| Phase | Step 5 | Step 6 | Step 7 | Duration |
|-------|--------|--------|--------|----------|
| Multi-run infra | ✅ Done | ✅ Done | ⏳ Week 1 | 2 days |
| Variance reporting | ✅ Done | ✅ Done | ⏳ Week 1-2 | 3 days |
| Fix #1 deployment | ✅ Done | ✅ Done | ⏳ Week 2 | 2 days |
| Fix #2 deployment | ✅ Done | ✅ Done | ⏳ Week 2 | 2 days |
| Fix #3 deployment | ✅ Done | ✅ Done | ⏳ Week 3 | 3 days |
| Test suites | ✅ Done | ✅ Done | ⏳ Week 2 | 2 days |
| Validation | ✅ Done | ✅ Done | ⏳ Week 3-4 | 2 days |

---

## Code Organization Pattern

### Directory Structure (Proven in Steps 5 & 6)

```
agent 07/
├── orchestrator.py              # Multi-run orchestration
├── document_extractor.py        # Extraction with 3 fixes
├── step_validator_agent.py      # Tests + variance reporting
├── STEP7_VARIANCE_ANALYSIS_REPORT.md  # Results documentation
├── STEP7_ENHANCEMENT_PLAN.md    # Requirements
├── STEP7_TECHNICAL_IMPLEMENTATION_GUIDE.md  # Dev guide
└── test_step7_multirun.json    # Test workflow config
```

This mirrors:
```
agent 05/
├── orchestrator.py
├── document_extractor.py
├── step_validator_agent.py
├── STEP5_VARIANCE_ANALYSIS_REPORT.md  ← Success model
└── ...
```

---

## Risk Mitigation Strategies

### Lessons Learned from Steps 5 & 6

| Risk | Step 5 Mitigation | Step 7 Strategy |
|------|---------|---|
| Fix #2 too aggressive (data loss) | Started with whitespace normalization, gradually expanded | Same approach: start loose, tighten iteratively |
| Post-validation rejects valid data | Implemented with 98% acceptance threshold, monitored rejections | Same: 98% initial threshold, manual review of rejections |
| LLM cost for multi-run tests | Used small test TALs (50-100 rows) | Same: test with small TAL first, scale to full later |
| Incompatibility with existing workflows | Maintained backward compatibility in orchestrator | Same: fixes are optional enhancements, don't break existing |

---

## Validation Criteria by Comparison

### Pre-Enhancement: Step 7 Current State
- ❌ Manual testing only (no multi-run automation)
- ❌ No variance metrics
- ❌ Asset ID extraction brittleness (fails with column name variations)
- ❌ No OCR normalization
- ❌ No post-validation filtering

### Post-Enhancement: Step 7 Parity with Steps 5 & 6
- ✅ Automated multi-run testing (3+ runs)
- ✅ Comprehensive variance reporting
- ✅ Robust asset ID extraction (handles 5+ column name variants)
- ✅ Pre-LLM OCR/data normalization
- ✅ Post-LLM validation with rejection tracking
- ✅ Zero-variance test results
- ✅ 100% test pass rate

---

## Files Created for Step 7 Enhancements

| File | Purpose | Reference |
|------|---------|-----------|
| STEP7_ENHANCEMENT_PLAN.md | Strategic requirements | Executive overview |
| STEP7_TECHNICAL_IMPLEMENTATION_GUIDE.md | Developer implementation | Tactical guide |
| STEP7_vs_STEPS_5_6_COMPARISON.md | This file | Feature alignment |
| STEP7_VARIANCE_ANALYSIS_REPORT.md | Test results | To be populated post-implementation |
| test_step7_multirun.json | Test workflow | Configuration for multi-run tests |

---

**Document Version:** 1.0  
**Created:** 2026-04-11  
**Purpose:** Developer reference for implementing feature parity with Steps 5 & 6
