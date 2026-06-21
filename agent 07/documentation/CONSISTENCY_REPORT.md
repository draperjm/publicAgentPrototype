# LLM Extraction Consistency Report — Steps 5, 6 & 7

**Project:** Agent 07 — Technical Asset List (TAL) Extraction Pipeline  
**Report Date:** 2026-04-14  
**Test Documents:** DAR1509-84 (Exp), DAR1675-106 (Exp)  
**Runs per execution:** 3 independent LLM calls per step  
**Consistency threshold:** PASS ≥ 90% · WARN ≥ 70% · FAIL < 70%

---

## Summary

| Step | Description | Total Executions | Avg Score (pre-fix) | Avg Score (post-fix) | Status |
|------|-------------|-----------------|--------------------|--------------------|--------|
| **5** | Extract Site Plan Information | 27 valid runs | 98.9% | 98.9% (no change needed) | ✅ PASS throughout |
| **6** | Extract Drawing Legend | 43 runs across 2 eras | 85.3% (41% FAIL rate) | **100%** (9/9 PASS) | ✅ Fixed Apr 13 |
| **7** | Extract Asset Spreadsheet Data | 10 meaningful runs | 67% → 83% (4-field metric) | **99–100%** (213-field metric) | ✅ Fixed Apr 13–14 |

---

## Step 5 — Extract Site Plan Information

**Extractor:** Document Extractor (Claude)  
**Comparison fields:** 17 sub-step fields (substation name, voltage, earthing, feeder count, etc.)

Step 5 was consistently high throughout all testing. No fixes were required.

### All Runs

| Date | Job | Project | Score | Verdict | Fields |
|------|-----|---------|-------|---------|--------|
| 2026-04-11 | 044942 | DAR1509-84 | 98% | PASS | 17 |
| 2026-04-11 | 045859 | DAR1509-84 | 98% | PASS | 17 |
| 2026-04-11 | 050641 | DAR1509-84 | **100%** | PASS | 17 |
| 2026-04-11 | 051311 | DAR1509-84 | 98% | PASS | 17 |
| 2026-04-11 | 081146 | DAR1509-84 | 98% | PASS | 17 |
| 2026-04-11 | 100200 | DAR1509-84 | 98% | PASS | 17 |
| 2026-04-11 | 104133 | DAR1509-84 | 98% | PASS | 17 |
| 2026-04-11 | 105953 | DAR1509-84 | **100%** | PASS | 17 |
| 2026-04-11 | 051904 | DAR1675-106 | 96% | PASS | 17 |
| 2026-04-11 | 053445 | DAR1675-106 | 98% | PASS | 17 |
| 2026-04-11 | 090235 | DAR1675-106 | **100%** | PASS | 17 |
| 2026-04-11 | 091559 | DAR1675-106 | 98% | PASS | 17 |
| 2026-04-11 | 093057 | DAR1675-106 | 96% | PASS | 17 |
| 2026-04-11 | 110543 | DAR1675-106 | **100%** | PASS | 17 |
| 2026-04-12 | 072311 | DAR1509-84 | 98% | PASS | 17 |
| 2026-04-12 | 102215 | DAR1509-84 | **100%** | PASS | 17 |
| 2026-04-12 | 102723 | DAR1509-84 | 98% | PASS | 17 |
| 2026-04-12 | 105054 | DAR1509-84 | 98% | PASS | 17 |
| 2026-04-13 | 094223 | DAR1509-84 | **100%** | PASS | 17 |
| 2026-04-13 | 100324 | DAR1509-84 | **100%** | PASS | 17 |
| 2026-04-13 | 114423 | DAR1675-106 | **100%** | PASS | 17 |
| 2026-04-13 | 221215 | DAR1675-106 | **100%** | PASS | 17 |
| 2026-04-13 | 233201 | DAR1675-106 | **100%** | PASS | 17 |
| 2026-04-14 | 124147 | DAR1675-106 | **100%** | PASS | 17 |
| 2026-04-14 | 125415 | DAR1675-106 | **100%** | PASS | 17 |
| 2026-04-14 | 130550 | DAR1675-106 | **100%** | PASS | 17 |
| 2026-04-14 | 131637 | DAR1675-106 | **100%** | PASS | 17 |

**Average: 98.9% · Min: 96% · Max: 100% · FAIL rate: 0%**

The 2% variance observed in some runs is explained by minor wording differences in free-text fields (e.g. substation description) which are genuinely ambiguous in the source document. All runs PASSed the 90% threshold.

---

## Step 6 — Extract Drawing Legend

**Extractor:** Document Extractor (Claude)  
**Comparison field:** 1 composite field — `sub-step-extract-legend` (full legend entry array, order-normalised)

Step 6 was the most volatile step early in testing, with a 41% FAIL rate across DAR1509-84. The root cause was missing legend entries in the reference table causing the LLM to produce different counts across runs.

### All Runs — Before Fix (Apr 11–12)

| Date | Job | Project | Score | Verdict | Root Cause |
|------|-----|---------|-------|---------|------------|
| 2026-04-11 | 010841 | DAR1509-84 | 100% | PASS | — |
| 2026-04-11 | 042806 | DAR1509-84 | 100% | PASS | — |
| 2026-04-11 | 044942 | DAR1509-84 | 100% | PASS | — |
| 2026-04-11 | 045859 | DAR1509-84 | 67% | **FAIL** | Incomplete reference table |
| 2026-04-11 | 050641 | DAR1509-84 | 100% | PASS | — |
| 2026-04-11 | 051311 | DAR1509-84 | 67% | **FAIL** | Incomplete reference table |
| 2026-04-11 | 081146 | DAR1509-84 | 100% | PASS | — |
| 2026-04-11 | 100200 | DAR1509-84 | 67% | **FAIL** | Incomplete reference table |
| 2026-04-11 | 104133 | DAR1509-84 | 100% | PASS | — |
| 2026-04-11 | 105953 | DAR1509-84 | 100% | PASS | — |
| 2026-04-11 | 011334 | DAR1675-106 | 100% | PASS | — |
| 2026-04-11 | 012128 | DAR1675-106 | 67% | **FAIL** | Incomplete reference table |
| 2026-04-11 | 013012 | DAR1675-106 | 33% | **FAIL** | Incomplete reference table |
| 2026-04-11 | 015338 | DAR1675-106 | 67% | **FAIL** | Incomplete reference table |
| 2026-04-11 | 021026 | DAR1675-106 | 67% | **FAIL** | Incomplete reference table |
| 2026-04-11 | 023334 | DAR1675-106 | 67% | **FAIL** | Incomplete reference table |
| 2026-04-11 | 024153 | DAR1675-106 | 100% | PASS | — |
| 2026-04-11 | 041715 | DAR1675-106 | 100% | PASS | — |
| 2026-04-11 | 042420 | DAR1675-106 | 100% | PASS | — |
| 2026-04-11 | 051904 | DAR1675-106 | 100% | PASS | — |
| 2026-04-11 | 053445 | DAR1675-106 | 100% | PASS | — |
| 2026-04-11 | 084724 | DAR1675-106 | 100% | PASS | — |
| 2026-04-11 | 090235 | DAR1675-106 | 67% | **FAIL** | Incomplete reference table |
| 2026-04-11 | 091559 | DAR1675-106 | 100% | PASS | — |
| 2026-04-11 | 093057 | DAR1675-106 | 100% | PASS | — |
| 2026-04-11 | 110543 | DAR1675-106 | 100% | PASS | — |
| 2026-04-12 | 063104 | DAR1509-84 | 100% | PASS | — |
| 2026-04-12 | 064635 | DAR1509-84 | 67% | **FAIL** | Incomplete reference table |
| 2026-04-12 | 065819 | DAR1509-84 | 100% | PASS | — |
| 2026-04-12 | 072311 | DAR1509-84 | 67% | **FAIL** | Incomplete reference table |
| 2026-04-12 | 102215 | DAR1509-84 | 67% | **FAIL** | Incomplete reference table |
| 2026-04-12 | 102723 | DAR1509-84 | 67% | **FAIL** | Incomplete reference table |
| 2026-04-12 | 105054 | DAR1509-84 | 67% | **FAIL** | Incomplete reference table |

**Pre-fix average: 85.3% · FAIL rate: 41%**

### Fix Applied — Apr 13

**Root cause:** The drawing legend reference table in the process definition was missing 3 symbol entries. When these symbols appeared in the site plan, 1 of the 3 runs would find them (via context inference) while the others would not, producing a 67% match rate (2/3 runs agree).

**Fix:** Added the 3 missing legend entries to the reference table in `process_extract_site_plan_information.json`.

### All Runs — After Fix (Apr 13–14)

| Date | Job | Project | Score | Verdict |
|------|-----|---------|-------|---------|
| 2026-04-13 | 094223 | DAR1509-84 | **100%** | PASS |
| 2026-04-13 | 100324 | DAR1509-84 | **100%** | PASS |
| 2026-04-13 | 114423 | DAR1675-106 | **100%** | PASS |
| 2026-04-13 | 221215 | DAR1675-106 | **100%** | PASS |
| 2026-04-13 | 233201 | DAR1675-106 | **100%** | PASS |
| 2026-04-14 | 124147 | DAR1675-106 | **100%** | PASS |
| 2026-04-14 | 125415 | DAR1675-106 | **100%** | PASS |
| 2026-04-14 | 130550 | DAR1675-106 | **100%** | PASS |
| 2026-04-14 | 131637 | DAR1675-106 | **100%** | PASS |

**Post-fix average: 100% · FAIL rate: 0%**

---

## Step 7 — Extract Asset Spreadsheet Data

**Extractor:** Document Extractor (Claude)  
**Source document:** TAL spreadsheet (XLSM) in transposed layout — each column is one asset  
**Test data:** DAR1675-106 STREETLIGHT sheet, 14 assets (SLPL IDs)

Step 7 had the most complex variance journey, involving both measurement defects (the metric itself was wrong) and extraction defects (the LLM was reading the wrong rows). Both were fixed across two iterations.

### All Runs

| Date | Job | Score | Verdict | Fields Compared | Notes |
|------|-----|-------|---------|----------------|-------|
| 2026-04-13 | 094223 (DAR1509) | 100% | PASS | 0 | Vacuous — `step_extraction` not read by validator |
| 2026-04-13 | 114423 | 100% | PASS | 0 | Vacuous — `step_extraction` not read by validator |
| 2026-04-13 | 221215 | 67% | **FAIL** | 4 | First real result; SUPFLOC used as asset_id |
| 2026-04-13 | 233201 | 83% | **WARN** | 4 | FLOC fix applied; description still varies |
| 2026-04-14 | 005541 | 100% | PASS | 0 | Vacuous — orchestrator restarted mid-job, state lost |
| 2026-04-14 | 105512 | 100% | PASS | 0 | Vacuous — OpenAI quota exhausted in Step 1/2 |
| 2026-04-14 | 124147 | **99%** | PASS | 213 | All fixes applied; per-field metric active |
| 2026-04-14 | 125415 | **100%** | PASS | 213 | — |
| 2026-04-14 | 130550 | **100%** | PASS | 213 | — |
| 2026-04-14 | 131637 | **100%** | PASS | 213 | — |

### Defects Found and Fixes Applied

#### Defect 1 — Variance validator could not read Step 7 output format
**When found:** Apr 13 (114423) — `total_fields: 0`, vacuous PASS  
**Root cause:** `variance_validator.py` `_compute_variance` only read `sub_step_extractions` (Steps 5/6 format). Step 7 stores results in `step_extraction.asset_records`, which was silently ignored.  
**Fix:** Added fallback in `_compute_variance` to map `step_extraction` fields into the comparison dict when `sub_step_extractions` is absent.  
**Effect:** Score changed from vacuous 100% (0 fields) → real 67% FAIL (4 fields).

---

#### Defect 2 — Metric compared entire asset list as one opaque blob (4 fields)
**When found:** Apr 13–14 (221215, 233201)  
**Root cause:** The 4-field metric (`total_assets`, `sheets_processed`, `asset_records`, `rejected_records`) treated all 14 assets as a single serialised string. A one-word difference in `description` for any asset made the entire `asset_records` field register as DIFF, masking agreement in 16 other fields per asset.  
**Fix (`variance_validator.py`):** Expanded `asset_records` into per-asset per-field keys: `asset.<asset_id>.<field>` (e.g. `asset.SLPL00316949.description`). Metric granularity: 4 fields → 213 fields.  
**Effect:** Consistency score became meaningful — 92.4% on the same data that showed 83% under the old metric.

---

#### Defect 3 — SUPFLOC used as asset_id (wrong row selected)
**When found:** Apr 13 (221215) — `asset_records` match_rate 33%  
**Root cause:** `_SAP_FLOC_SOURCE_CODES_PRIORITY = ["SUPFLOC", "SUP_FLOC", "FLOC", "EQUNR"]` — `SUPFLOC` (Superior Functional Location = the parent pole/column) was priority 0. The hint told the LLM "use the SUPFLOC row as asset_id." All 3 runs extracted different mixes of POLE/CLMN parent IDs (`PO1011307`, `SLP02002188`) instead of the streetlight's own SLPL IDs.  
**Fix (`document_extractor.py`):**
- Reordered: `_SAP_FLOC_SOURCE_CODES_PRIORITY = ["FLOC", "EQUNR"]` — SUPFLOC removed entirely.
- Same reorder applied to `_ASSET_ID_ALIASES_ORDERED` (conventional layout).
- Updated hint text: *"asset's own Functional Location codes — use as asset_id … SUPFLOC is the PARENT asset — do NOT use it as asset_id."*  
- Updated fallback chain in prompt: removed "Superior Functional Location" from the chain.  
**Effect:** All runs now consistently extract correct SLPL IDs (`SLPL00316949`–`SLPL00316956`). `asset_id` consistency: 100%.

---

#### Defect 4 — PROJECT metadata sheet processed as an asset sheet
**When found:** Apr 13 (221215) — Run 1 had 15 assets vs 13 in Runs 2/3  
**Root cause:** `_select_target_worksheets` returned all non-empty sheets. The `PROJECT` sheet contains only project metadata (`CAP No.: DAR1509`, `Drawing No.: 526458`), but Run 1's LLM extracted these as fake "assets".  
**Fix (`document_extractor.py`):** Added `_METADATA_SHEET_NAMES` blocklist (`project`, `sapjob`, `formulae`, `error log`, etc.) checked at the start of the Pass 2 loop. Also added skip for sheets with unrecognised layout (`format == "unknown"`).  
**Effect:** `total_assets` consistent at 14 across all runs. No spurious PROJECT records.

---

#### Defect 5 — Description field mapped from different source rows each run
**When found:** Apr 13 (233201) — 16 fields varying, all in `description`  
**Root cause:** The STREETLIGHT TAL sheet has three description-like rows per asset column: `FLOC_CONST_D` ("Gen, Street Lighting"), `REF_EQ_D` ("Gen, Streetlight Bracket"), `EQ_TOT_D` ("Streetlight Bracket"). Without explicit guidance, each run chose a different row — producing 3 different descriptions for the same asset.  
**Fix (`document_extractor.py`):** Added `_TAL_DESCRIPTION_SOURCE_CODES = ["FLOC_CONST_D", "FLOC_TOT_D", "DESCRIPT", "SHORTTEXT"]`. The `_analyze_sheet_for_asset_id` function detects which row is present (first priority match) and injects into the hint: *"Map description from the 'FLOC_CONST_D' row only (ignore REF_EQ_D / EQ_TOT_D rows — they are equipment-component descriptions, not the asset description)."*  
**Effect:** `description` consistency: 0% → 100% across 14 assets.

---

#### Defect 6 — `asset_type` varies between sub-component labels
**When found:** Apr 13 (221215, 233201) — Runs using "Street Lighting" vs "Streetlight Bracket" vs "Streetlight Outreach"  
**Root cause:** The TAL `Asset Type` column contains SAP technical codes ("FLOC", "EQ"), not readable labels. The LLM inferred the type from context, sometimes picking sub-component names.  
**Fix (`document_extractor.py`):** `_analyze_sheet_for_asset_id` now reads the canonical sheet header label (column 10, row 1 — e.g., "Streetlights") and injects *"Use asset_type='Streetlights' for every record in this sheet."*  
**Effect:** `asset_type` consistency: 100% across all runs.

---

### Step 7 Final State — Per-Field Breakdown (213 fields, 14 assets × 15 fields + 3 summary)

| Field | Consistency | Notes |
|-------|-------------|-------|
| `asset.<id>.asset_id` | 100% | SLPL IDs correctly extracted |
| `asset.<id>.asset_type` | 100% | Fixed: canonical sheet header used |
| `asset.<id>.description` | 100% | Fixed: `FLOC_CONST_D` row pinned |
| `asset.<id>.model` | 100% | Correctly mapped from `SL_LUMINAIRE` |
| `asset.<id>.location` | 100% | Correctly mapped from `MAINTLOC` |
| `asset.<id>.status` (SLPL316953, 316954) | **33%** | Remaining variance: `DELETIONFLAG='X'` row detected inconsistently |
| `total_assets` | 100% | Fixed: PROJECT sheet excluded |
| `sheets_processed` | 100% | — |
| `rejected_records` | 100% | Fixed: consistent after PROJECT exclusion |

Overall Step 7 score after all fixes: **99%** (211/213 fields consistent).  
Remaining 1%: `DELETIONFLAG` interpretation for 2 assets marked for deletion — the LLM maps this to `status = "Marked for Deletion"` in some runs and `null` in others. This is a real ambiguity in the source data.

---

## Fix Timeline

| Date | Fix | Component | Step(s) Affected | Impact |
|------|-----|-----------|-----------------|--------|
| 2026-04-13 | Add 3 missing legend entries to reference table | `process_extract_site_plan_information.json` | Step 6 | FAIL rate 41% → 0% |
| 2026-04-13 | Add `step_extraction` fallback in variance validator | `variance_validator.py` | Step 7 | Vacuous 100% (0 fields) → real 67% FAIL (4 fields) |
| 2026-04-13 | Add `repeat_runs: 3` to Step 7 task definition | `tasks.json` | Step 7 | Step 7 now pauses for user run-count input, same as Steps 5/6 |
| 2026-04-13 | Fix orchestrator E2 path to call variance validator | `orchestrator.py` | Step 7 | Variance report now shown in Runs & Variance UI tab |
| 2026-04-13 | Reorder `_SAP_FLOC_SOURCE_CODES_PRIORITY` (FLOC before SUPFLOC) | `document_extractor.py` | Step 7 | asset_id consistency: 33% → 100% |
| 2026-04-13 | Add `_METADATA_SHEET_NAMES` blocklist (skip PROJECT sheet) | `document_extractor.py` | Step 7 | total_assets consistent: 15 vs 13 → 14/14 |
| 2026-04-14 | Expand asset_records to per-asset per-field metric (213 fields) | `variance_validator.py` | Step 7 | Metric granularity: 4 blobs → 213 fields |
| 2026-04-14 | Add `_TAL_DESCRIPTION_SOURCE_CODES` (pin to FLOC_CONST_D) | `document_extractor.py` | Step 7 | description consistency: 0% → 100% |
| 2026-04-14 | Add canonical asset_type from sheet header | `document_extractor.py` | Step 7 | asset_type consistency: inconsistent → 100% |
| 2026-04-14 | Fix fallback chain (remove SUPFLOC) and add SUPFLOC warning in prompt | `document_extractor.py` | Step 7 | Belt-and-suspenders for asset_id fix |

---

## Notes on Invalid Runs

Three Step 7 runs showed vacuous PASS (100%, 0 fields) for infrastructure reasons unrelated to extraction quality:

| Job | Cause |
|-----|-------|
| 20260414_005541 | Orchestrator container restarted mid-job (08:02, 09:17). In-memory state cleared. Step 7 ran at 10:20 with empty plan. |
| 20260414_105512 | OpenAI quota exhausted (`insufficient_quota` 429). Document reviewer (GPT-4o) failed all LLM calls. Steps 1/2 returned empty — no files for Step 7. |
| 20260413_114423 / 094223 | Pre-fix: variance validator not reading `step_extraction` format. |

These are excluded from the post-fix averages above.

---

## Conclusion

All three steps now consistently PASS with the fixes applied:

- **Step 5:** Was already robust (98.9% avg). No changes needed.
- **Step 6:** Fixed by completing the reference table. Now 100% on every run.
- **Step 7:** Required the most work — 6 separate defects fixed across 2 days. Final state is 99% consistency (213 fields compared), with 1% residual variance from a genuine source data ambiguity (`DELETIONFLAG` on 2 assets).
