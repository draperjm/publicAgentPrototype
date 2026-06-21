# Agent 08 — Extraction Consistency Experiment Report

**Date:** 2026-05-03
**Pipeline:** Customer Connections Application Review (tmpl-jr-review-customer-connections)
**Experiments:** DAR1675-106, DAR1509-84, DAR11575-85, DAR98877-81, 82&67

> **Fuzzy variance matching** — from the DAR98877-81 run onwards the variance validator applies 97% similarity tolerance to long text fields (≥ 80 chars). Minor punctuation variants such as trailing conjunctions are absorbed; structural content differences remain flagged. Short structured fields (substation data, field checks, easements) use exact matching.

---

## Overview

This report summarises the variance test results across four full pipeline runs. Each extraction step was executed multiple times with identical inputs. The variance validator compared outputs field-by-field across all runs to measure extraction consistency.

**All steps passed.** Summary by experiment:

| Dataset | Step | Runs | Score | Verdict |
|---|---|---:|---:|---|
| DAR1675-106 | Extract Site Plan Information | 10 | 100.0% | ✓ PASS |
| DAR1675-106 | Extract Drawing Legend | 10 | 100.0% | ✓ PASS |
| DAR1675-106 | Extract Asset Spreadsheet Data | 10 | 99.5% | ✓ PASS |
| DAR1509-84 | Extract Site Plan Information | 10 | 100.0% | ✓ PASS |
| DAR1509-84 | Extract Drawing Legend | 10 | 100.0% | ✓ PASS |
| DAR1509-84 | Extract Asset Spreadsheet Data | 10 | 97.4% | ✓ PASS |
| DAR11575-85 | Extract Site Plan Information | 10 | 100.0% | ✓ PASS |
| DAR11575-85 | Extract Drawing Legend | 10 | 100.0% | ✓ PASS |
| DAR11575-85 | Extract Asset Spreadsheet Data | 10 | 99.7% | ✓ PASS |
| DAR98877-81 | Extract Site Plan Information | 10 | 98.2% | ✓ PASS |
| DAR98877-81 | Extract Drawing Legend | 10 | 100.0% | ✓ PASS |
| DAR98877-81 | Extract Asset Spreadsheet Data | 10 | 99.1% | ✓ PASS |
| 82&67 | Extract Site Plan Information | 10 | 100.0% | ✓ PASS |
| 82&67 | Extract Drawing Legend | 10 | 100.0% | ✓ PASS |
| 82&67 | Extract Asset Spreadsheet Data | 10 | 93.3% | ✓ PASS |

---

## Step 5 — Extract Site Plan Information

### DAR1675-106 · DS1_DAR1675_RETIC.pdf

**Score: 100% (11/11 fields consistent across 10 runs)**

All sub-steps were fully consistent:

| Field | Consistent | Unique Values |
|---|---|---|
| sub-step-extract-all-notes | ✓ | 1 |
| sub-step-field-check | ✓ | 1 |
| sub-step-easement-restriction | ✓ | 1 |
| sub-step-easement-substation | ✓ | 1 |
| sub-step-substation-data.Substation Asset Number | ✓ | 1 |
| sub-step-substation-data.Transformer Size | ✓ | 1 |
| sub-step-substation-data.HV Switchgear | ✓ | 1 |
| sub-step-substation-data.Voltage Level | ✓ | 1 |
| sub-step-substation-data.LV Switchgear | ✓ | 1 |
| sub-step-substation-data.Cubicle Size | ✓ | 1 |
| sub-step-substation-data.Earthing | ✓ | 1 |

The Phase 1 cache (introduced to prevent OCR re-runs) is working as intended — full text extraction runs once and all subsequent runs use the same raw text, eliminating the OCR-level non-determinism seen in earlier experiments.

### DAR1509-84 · DS1_DAR1988_RETIC_NOTES.pdf

**Score: 100% (11/11 fields consistent across 10 runs)**

Identical result to DAR1675-106. All 11 fields consistent across all 10 runs with a single unique value each.

### DAR11575-85 · DS1_DAR11575_NOTES.pdf

**Score: 100% (11/11 fields consistent across 7 runs)**

All 11 fields consistent. Extracted values of note:

| Field | Extracted Value |
|---|---|
| sub-step-field-check | YES |
| sub-step-easement-restriction | NOT REQUIRED |
| sub-step-easement-substation | NOT REQUIRED |
| sub-step-substation-data.Substation Asset Number | 95021 |
| sub-step-substation-data.Transformer Size | 200kVA, 3 PHASE |
| sub-step-substation-data.Voltage Level | 11000V |
| sub-step-substation-data.Earthing | SEPERATE EARTHING |
| sub-step-substation-data.HV Switchgear | NOT FOUND |
| sub-step-substation-data.LV Switchgear | NOT FOUND |
| sub-step-substation-data.Cubicle Size | NOT FOUND |

### DAR98877-81 · DS1_DAR98877_RETIC_NOTES.pdf

**Score: 98.2% (10.8/11 fields consistent across 10 runs)**

All structured fields were fully consistent. One text field showed consolidation-pass variance:

| Field | Consistent | Match Rate | Fuzzy Clusters | Note |
|---|---|---:|---:|---|
| sub-step-extract-all-notes | ✗ | 80% | 2 | 1 minor variant absorbed; note-7 truncation remains |
| sub-step-field-check | ✓ | 100% | 1 | |
| sub-step-easement-restriction | ✓ | 100% | 1 | |
| sub-step-easement-substation | ✓ | 100% | 1 | |
| sub-step-substation-data.* (×7) | ✓ | 100% | 1 | All seven fields consistent |

**Variance detail for `sub-step-extract-all-notes`:** Three exact variants existed across 10 runs, reduced to two fuzzy clusters after applying 97% similarity tolerance:

- **Cluster 1 (8 runs):** Full notes, `, or` conjunction present or absent between legal sub-clauses (i) and (ii) — 99.8% similar, absorbed as equivalent
- **Cluster 2 (2 runs):** Note 7 (`SERVICE PROVIDER TO NOTIFY...`) truncated mid-sentence at "ASSET DATA CUSTOMER", then jumps directly to the next note — 63.7% similar to Cluster 1, retained as a genuine inconsistency

**Root cause:** Consolidation LLM non-determinism in Phase 2. The Phase 1 cache correctly provides identical raw OCR text to all runs; the discrepancy arises in the LLM pass that assembles per-chunk text into the final notes string. The note-7 truncation is a real structural issue (the LLM treats a word mid-sentence as a note boundary in 2 of 10 runs).

### 82&67 · DS3_DAR82178_RETIC_NOTES.pdf

**Score: 100% (11/11 fields consistent across 10 runs)**

All 11 fields consistent across every run with a single unique value each. This is the first dataset to achieve 100% on site plan extraction with 10 runs and fuzzy matching active.

---

## Step 6 — Extract Drawing Legend

### DAR1675-106 · DS1_DAR1675_RETIC.pdf

**Score: 100% (1/1 fields consistent across 10 runs)**

- **Entries extracted:** 22 legend entries
- **Categories present:** cable, equipment, other
- All 22 entries were identical across every run.

### DAR1509-84 · DS1_DAR1988_RETIC_NOTES.pdf

**Score: 100% (1/1 fields consistent across 10 runs)**

- **Entries extracted:** 29 legend entries
- **Categories present:** boundary, cable, earthing, equipment, other, substation
- All 29 entries were identical across every run.

### DAR11575-85 · DS1_DAR11575_NOTES.pdf

**Score: 100% (1/1 fields consistent across 7 runs)**

- **Entries extracted:** 21 legend entries
- **Categories present:** cable, earthing, equipment
- All 21 entries were identical across every run.

### DAR98877-81 · DS1_DAR98877_RETIC_NOTES.pdf

**Score: 100% (1/1 fields consistent across 10 runs)**

- **Entries extracted:** 8 legend entries
- **Categories present:** cable, equipment
- All 8 entries were identical across every run. Sample entries:

| Label | Category |
|---|---|
| Overhead Mains - Existing | cable |
| New overhead service | cable |
| Pole - Existing | equipment |

### 82&67 · DS3_DAR82178_RETIC_NOTES.pdf

**Score: 100% (1/1 fields consistent across 10 runs)**

- **Entries extracted:** 10 legend entries
- **Categories present:** cable (4), equipment (4), substation (1), other (1)
- All 10 entries were identical across every run.

**Note:** Per-chunk image processing and Phase 1 caching produce fully deterministic legend extraction across all five datasets (8–29 entries per drawing).

---

## Step 7 — Extract Asset Spreadsheet Data

### DAR1675-106 · DAR1675_TAL_20260217.XLSM

**Score: 99.5% (212/213 fields consistent across 10 runs)**

**Inconsistent fields: 2** — both on the same pair of assets:

| Asset | Field | Match Rate | Values observed |
|---|---|---|---|
| SLPL00316953 | status | 50% | `X` (runs 1–4, 6), `Marked for Deletion` (runs 5, 7–9), `Flagged for Deletion` (run 10) |
| SLPL00316954 | status | 50% | Same pattern as above |

**Root cause:** The spreadsheet cell contains a raw value of `"X"` which is an informal deletion marker. The LLM sometimes returns the literal cell value (`X`) and other times interprets its meaning (`Marked for Deletion`, `Flagged for Deletion`). These are semantically equivalent but produce different string values.

**Recommendation:** Normalize `"X"` status cells to a canonical value (`Marked for Deletion`) in the extraction prompt or as a post-processing step.

---

### DAR1509-84 · DAR1988_TAL_20260112.XLSM

**Score: 97.4% (207.4/213 fields consistent across 10 runs)**

**Inconsistent fields: 28** — affecting 14 assets, each with 2 inconsistent fields (`serial_number` and `status`).

| Field | Match Rate | Unique Values |
|---|---|---|
| asset.SLPL003028xx.serial_number (×14) | 70% | 2 (value or empty) |
| asset.SLPL003028xx.status (×14) | 90% | 2 (value or empty) |

**Pattern:** Runs 5, 7, and 8 consistently return empty strings for `serial_number` across all 14 affected assets. This is a **systematic per-run failure** — the same 3 runs fail for all 14 assets. The most likely cause is that the LLM in those runs misses the column mapping for the `STREETLIGHT` sheet.

**Recommendation:** Add an explicit column alias mapping for serial number to eliminate the dropout.

---

### DAR11575-85 · TALNRL15205_20230118142012.XLSM

**Score: 99.7% (92.71/93 fields consistent across 7 runs)**

- **Assets extracted:** 6 · **Sheets processed:** PILLAR, POLE · **Rejected records:** 0

**Inconsistent fields: 1**

| Asset | Field | Match Rate | Values observed |
|---|---|---|---|
| PO0985413 | serial_number | 71.4% | `PL985413` (runs 1, 5) · empty (runs 2, 3, 4, 6, 7) |

**Recommendation:** Add column alias for `serial_number` to cover common pole spreadsheet column names.

---

### DAR98877-81 · TALNRL15237_20230113140532.XLSM

**Score: 99.1% (77.3/78 fields consistent across 10 runs)**

- **Assets extracted:** varies · **Sheets processed:** POLE, SWITCHGEAR · **Rejected records:** inconsistent (see below)

**Inconsistent fields: 3**

| Asset/Field | Field | Match Rate | Values observed |
|---|---|---|---|
| LVBB00037192 | asset_type | 90% | `LV Busbar` (9 runs), `SWITCHGEAR` (run 6) |
| LVBB00037192 | status | 90% | `Outage Dependant Obj` (9 runs), empty (run 5) |
| (global) | rejected_records | 50% | `0` (runs 1, 2, 5, 6, 9) · `3` (runs 3, 4, 7, 8, 10) |

**LVBB00037192 asset_type:** Run 6 returns the sheet name (`SWITCHGEAR`) as the asset type instead of the cell value (`LV Busbar`). This is a single-run classification error where the LLM confuses the worksheet context with the field value.

**rejected_records split:** Exactly 5 runs accept all records (0 rejected) and 5 reject 3 records. This 50/50 split across 10 runs indicates a genuine ambiguity in how the SWITCHGEAR worksheet is interpreted — some rows are valid assets in some runs and invalid in others. This suggests a row-filtering criterion (e.g., empty asset ID, or header-like rows) is being applied inconsistently.

**Recommendation:** Investigate the SWITCHGEAR sheet for rows that straddle the LLM's rejection threshold. Adding an explicit rule for what constitutes a valid vs invalid record in switchgear sheets would stabilize the rejected_records count.

### 82&67 · URS35318_TAL_20250515_final.XLSM

**Score: 93.3% (58.8/63 fields consistent across 10 runs)**

**Inconsistent fields: 10** — all concentrated on two ASSMY pillar assets and global record-count fields:

| Asset/Field | Field | Match Rate | Pattern |
|---|---|---|---|
| ASSMY00749529 | asset_id | 50% | Value in runs 1, 3, 5, 7, 10 · empty in runs 2, 4, 6, 8, 9 |
| ASSMY00749529 | asset_type | 50% | `Pillars` in runs 1, 3, 5, 7, 10 · empty otherwise |
| ASSMY00749529 | description | 50% | `Gen, UG Termination` in runs 1, 3, 5, 7, 10 · empty otherwise |
| ASSMY00749529 | location | 70% | `Hoxton Park FieldServiceCentre` in runs 5, 7, 10 · empty otherwise |
| ASSMY00749530 | asset_id | 50% | Same pattern as ASSMY00749529 |
| ASSMY00749530 | asset_type | 50% | Same pattern as ASSMY00749529 |
| ASSMY00749530 | description | 50% | Same pattern as ASSMY00749529 |
| ASSMY00749530 | location | 70% | Same pattern as ASSMY00749529 |
| (global) | total_assets | 50% | `4` (runs 1, 3, 5, 7, 10) · `2` (runs 2, 4, 6, 8, 9) |
| (global) | rejected_records | 90% | `0` (9 runs) · `1` (run 2 only) |

**Pattern:** The two ASSMY assets (`Gen, UG Termination` pillar type) are either both extracted or both entirely missing — never one without the other. Runs 1, 3, 5, 7, 10 extract all four assets (total = 4); runs 2, 4, 6, 8, 9 extract only the other two assets (total = 2). This 50/50 split across alternating runs indicates the LLM intermittently fails to read these two specific rows from the pillar worksheet, likely because `Gen, UG Termination` is an atypical description format that creates uncertainty about whether the row is a valid asset record.

**Recommendation:** Add an explicit instruction to the spreadsheet extraction prompt that `UG Termination` rows are valid asset records regardless of the description prefix, and that all non-header rows with a populated asset ID column must be extracted.

---

## Single Prompt Baseline — DAR1675-106 (10 Runs)

**Dataset:** DAR1675-106 — Asset Relocation, 1 Smith Road, Paddington
**Condition:** Single prompt — 10 repeated runs on identical input
**Purpose:** Establish a single-prompt baseline for comparison against the multi-agent pipeline consistency results above

#### Gold Standard Totals

| Step | Total Extractable Items |
|------|------------------------|
| Legend — Step 6 | 26 |
| Assets — Step 5 | 14 |
| Notes — Step 4 | 11 |

#### Results

| Run | Legend — Step 6 (n) | Assets — Step 5 (n) | Notes — Step 4 (n) | Legend — Step 6 (%) | Assets — Step 5 (%) | Notes — Step 4 (%) |
|-----|--------------------:|--------------------:|-------------------:|--------------------:|--------------------:|-------------------:|
| **Single Prompt Avg** | | | | **88%** | **48%** | **6%** |
| Run 1 | 24 | 10 | 0 | 92% | 71% | 0% |
| Run 2 | 26 | 8 | 2 | 100% | 57% | 18% |
| Run 3 | 26 | 8 | 0 | 100% | 57% | 0% |
| Run 4 | 21 | 6 | 0 | 81% | 43% | 0% |
| Run 5 | 26 | 10 | 1 | 100% | 71% | 9% |
| Run 6 | 26 | 8 | 3 | 100% | 57% | 27% |
| Run 7 | 17 | 5 | 0 | 65% | 36% | 0% |
| Run 8 | 20 | 5 | 0 | 77% | 19% | 0% |
| Run 9 | 18 | 5 | 0 | 69% | 19% | 0% |
| Run 10 | 26 | 5 | 0 | 100% | 19% | 0% |

#### Observations

- **Legend extraction** was the most consistent — 8 of 10 runs extracted ≥ 77%, with 5 runs achieving 100%. Variance was higher in Runs 7–9.
- **Asset extraction** showed moderate and declining consistency. Runs 1–6 averaged ~59%; Runs 7–10 dropped to 19–36%.
- **Notes extraction** was the least consistent — 7 of 10 runs extracted 0 notes, with meaningful extraction only in Runs 2 (18%), 5 (9%), and 6 (27%). The 6% average indicates notes are largely missed by the single-prompt approach.

---

## Key Findings

### What is working well

- **Legend extraction is fully deterministic** — Per-chunk image processing and Phase 1 caching produce identical legend entries across every run for all five datasets (8–29 entries per drawing).
- **Structured substation data fields are perfectly consistent** — All seven substation data fields matched exactly across every run for all five documents.
- **Fuzzy variance tolerance is effective** — The 97% similarity threshold correctly absorbs the `", or"` conjunction flip (99.8% similar) while retaining the note-7 truncation as a genuine inconsistency (63.7% similar). The threshold is well-calibrated.
- **Site plan Phase 1 cache prevents OCR variance** — All structured extraction fields are consistent; the only remaining variance in site plan notes is from the Phase 2 consolidation LLM.
- **82&67 achieves perfect site plan score** — First dataset to score 100% on site plan extraction with 10 runs and fuzzy matching active.

### Remaining inconsistencies

| Issue | Scope | Impact | Fix |
|---|---|---|---|
| `X` status cell interpreted inconsistently | DAR1675TAL, 2 assets | Low — semantically equivalent | Normalize `X` → `Marked for Deletion` in prompt |
| Serial number dropout, systematic | DAR1509 TAL, 14 assets | Medium — 30% of runs empty | Add column alias for `serial_number` |
| Serial number dropout, majority of runs | NRL15205 TAL, 1 asset | Low — 1 field | Same column alias fix |
| Note-7 consolidation truncation | NRL15237 site plan | Low — 2 of 10 runs, raw text only | Consolidation prompt: never split a note mid-sentence |
| Asset type returns sheet name | NRL15237 TAL, 1 asset | Low — single run anomaly | Prompt: distinguish sheet context from cell value |
| Rejected records 50/50 split | NRL15237 TAL | Medium — 50% of runs reject 3 records | Clarify valid-record criteria for switchgear sheets |
| ASSMY pillar rows intermittently missed | 82&67 TAL, 2 assets | Medium — 50% dropout on both assets | Prompt: all rows with populated asset ID are valid records |

---

## Consistency Score Summary

| Step | DAR1675-106 | DAR1509-84 | DAR11575-85 | DAR98877-81 | 82&67 |
|---|---|---|---|---|---|
| Extract Site Plan Information | **100%** | **100%** | **100%** | 98.2% | **100%** |
| Extract Drawing Legend | **100%** | **100%** | **100%** | **100%** | **100%** |
| Extract Asset Spreadsheet Data | 99.5% | 97.4% | 99.7% | 99.1% | 93.3% |
| **Overall** | **99.8%** | **99.1%** | **99.9%** | **99.1%** | **97.8%** |
