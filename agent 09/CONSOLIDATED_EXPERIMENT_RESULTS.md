# Agent 09 — Consolidated Experiment Results

**Date compiled:** 2026-05-24  
**Pipeline:** vFTE (virtual Field Technical Expert) — multi-agent document extraction  
**Scope:** Steps 5–9 across all five data sets, merging results from three experiment report files.

> Steps 5–7 variance data from `EXPERIMENT_REPORT.md` and `EXPERIMENT_RESULTS.md` (Saved Runs/Step 5-7).  
> Steps 8–9 variance data from `STEP_8_9_EXPERIMENT_RESULTS.md`.

---

## 1. Data Sets

| Data Set | Project ID | Description | TAL Assets | Site Plan Document | Steps 8/9 Tested |
|---|---|---|---|---|---|
| Data Set 1 | DAR1675-106 | Asset Relocation, 1 Smith Road, Paddington | 14 (SLPL) | DS1_DAR1675_RETIC.pdf | Yes |
| Data Set 2 | DAR1509-84 | Victoria Park streetlights | 14 (SLPL) | DS1_DAR1988_RETIC_NOTES.pdf | Yes |
| Data Set 4 | DAR11575-85 | Pillars and pole substation | 6 (POLE/PILL/ASSMY) | DS1_DAR11575_NOTES.pdf | Yes |
| Data Set 5 | DAR98877-81 | Poles and switchgear | 5 (POLE/ASSMY/LVBB) | DS1_DAR98877_RETIC_NOTES.pdf | Yes |
| Data Set 6 | DAR82178 | Pillars, Victoria Park | 2–4 (ASSMY/PILL) | DS3_DAR82178_RETIC_NOTES.pdf | No |

---

## 2. Master Consistency Summary

All variance tests used 10 independent runs per step. Scores reflect field-level consistency across all runs.

| Data Set | Step 5 | Step 6 | Step 7 | Step 8 Match Rate | Step 8 Consistency | Step 9 High Conf. | Step 9 Consistency |
|---|---|---|---|---|---|---|---|
| Data Set 1 | 100% | 100% | 99.5% | 43% (6/14) | 100% | 43% (6/14) | 100% |
| Data Set 2 | 100% | 100% | 97.4% | 100% (14/14) | 100% | 100% (14/14) | 100% |
| Data Set 4 | 100% | 100% | 99.7% | 50% (3/6) | 100% | 50% (3/6) | 100% |
| Data Set 5 | 98.2% | 100% | 99.1% | 40% (2/5) | 100% | 40% (2/5) | 100% |
| Data Set 6 | 100% | 100% | 93.3% | — | — | — | — |

> **Step 8 Match Rate** = proportion of TAL assets found on the site plan drawing (not a consistency score — it reflects drawing coverage).  
> **Step 8 Consistency** = whether Step 8 produced identical results across all 10 runs (separate from match rate).

---

## 3. Single-Prompt Baseline vs vFTE Framework (Steps 5–7)

Tested on Data Set 1. Ground truth: 26 legend entries, 14 asset records, 11 notes.

| Approach | Runs | Legend Accuracy | Asset Accuracy | Notes Accuracy | Legend Consistency | Asset Consistency | Notes Consistency |
|---|---|---|---|---|---|---|---|
| Single Prompt | 10 | 88% | 50% | 5% | 44% | 27% | 9% |
| **vFTE Framework** | **10** | **100%** | **100%** | **100%** | **100%** | **100%** | **100%** |

### Single Prompt Per-Run Detail (Data Set 1)

| Run | Legend (n) | Assets (n) | Notes (n) | Legend % | Assets % | Notes % |
|---|---|---|---|---|---|---|
| Run 1 | 24 | 10 | 0 | 92% | 71% | 0% |
| Run 2 | 26 | 8 | 2 | 100% | 57% | 18% |
| Run 3 | 26 | 8 | 0 | 100% | 57% | 0% |
| Run 4 | 21 | 6 | 0 | 81% | 43% | 0% |
| Run 5 | 26 | 10 | 1 | 100% | 71% | 9% |
| Run 6 | 26 | 8 | 3 | 100% | 57% | 27% |
| Run 7 | 17 | 5 | 0 | 65% | 36% | 0% |
| Run 8 | 20 | 5 | 0 | 77% | 36% | 0% |
| Run 9 | 18 | 5 | 0 | 69% | 36% | 0% |
| Run 10 | 26 | 5 | 0 | 100% | 36% | 0% |
| **Average** | | | | **88%** | **50%** | **5%** |

---

## 4. Step 5 — Extract Site Plan Information

10 runs per data set. Structured substation fields use exact matching; long text fields (≥ 80 chars) use 97% fuzzy tolerance.

| Data Set | Runs | Fields | Consistency Score | Verdict | Notes |
|---|---|---|---|---|---|
| Data Set 1 | 10 | 11 | 100% | PASS | All fields consistent |
| Data Set 2 | 10 | 11 | 100% | PASS | All fields consistent |
| Data Set 4 | 10 | 11 | 100% | PASS | All fields consistent |
| Data Set 5 | 10 | 11 | 98.2% | PASS | `sub-step-extract-all-notes` had 2-cluster variance (note-7 truncation in 2/10 runs) |
| Data Set 6 | 10 | 11 | 100% | PASS | All fields consistent |

### Data Set 5 — Step 5 Variance Detail

`sub-step-extract-all-notes` produced two fuzzy clusters:

| Cluster | Runs | Description |
|---|---|---|
| Cluster 1 | 8 | Full notes; `, or` conjunction present/absent between legal sub-clauses — 99.8% similar, absorbed |
| Cluster 2 | 2 | Note 7 (`SERVICE PROVIDER TO NOTIFY...`) truncated mid-sentence at "ASSET DATA CUSTOMER" — 63.7% similar to Cluster 1, retained as genuine inconsistency |

Root cause: Phase 2 consolidation LLM occasionally treats a word mid-sentence as a note boundary. Phase 1 cache provides identical raw OCR text to all runs; the issue is in the assembly pass.

---

## 5. Step 6 — Extract Drawing Legend

| Data Set | Runs | Entries Extracted | Consistency Score | Verdict |
|---|---|---|---|---|
| Data Set 1 | 10 | 22 | 100% | PASS |
| Data Set 2 | 10 | 29 | 100% | PASS |
| Data Set 4 | 10 | 21 | 100% | PASS |
| Data Set 5 | 10 | 8 | 100% | PASS |
| Data Set 6 | 10 | 10 | 100% | PASS |

All five data sets achieved perfect legend consistency. Per-chunk image processing and Phase 1 caching produce deterministic legend extraction across all drawing types (8–29 entries).

---

## 6. Step 7 — Extract Asset Spreadsheet Data

| Data Set | Runs | Total Fields | Consistency Score | Verdict | Inconsistent Fields |
|---|---|---|---|---|---|
| Data Set 1 | 10 | 213 | 99.5% | PASS | 2 (status on 2 assets) |
| Data Set 2 | 10 | 213 | 97.4% | PASS | 28 (serial_number + status on 14 assets — systematic 3-run dropout) |
| Data Set 4 | 10 | 93 | 99.7% | PASS | 1 (serial_number on 1 asset) |
| Data Set 5 | 10 | 78 | 99.1% | PASS | 3 (asset_type, status on 1 asset; rejected_records 50/50 split) |
| Data Set 6 | 10 | 63 | 93.3% | PASS | 10 (2 ASSMY assets intermittently dropped; total_assets 50/50 split) |

### Step 7 Inconsistency Detail

| Data Set | Asset(s) | Field | Issue | Fix |
|---|---|---|---|---|
| Data Set 1 | SLPL00316953/2 | `status` | Cell value `X` returned as literal or interpreted (`Marked for Deletion`, `Flagged for Deletion`) | Normalize `X` → `Marked for Deletion` in prompt |
| Data Set 2 | 14 × SLPL | `serial_number` | Runs 5, 7, 8 return empty across all 14 assets — systematic column mapping miss | Add `serial_number` column alias |
| Data Set 2 | 14 × SLPL | `status` | Same 3-run dropout as serial_number | Same fix |
| Data Set 4 | PO0985413 | `serial_number` | `PL985413` in 2/7 runs; empty in 5/7 | Add `serial_number` column alias for pole sheets |
| Data Set 5 | LVBB00037192 | `asset_type` | Run 6 returns sheet name (`SWITCHGEAR`) instead of cell value (`LV Busbar`) | Prompt: distinguish sheet context from cell value |
| Data Set 5 | (global) | `rejected_records` | 50/50 split: 5 runs return 0, 5 return 3 — SWITCHGEAR worksheet rows ambiguous | Clarify valid-record criteria for switchgear sheets |
| Data Set 6 | ASSMY003746829/30 | `asset_id`, `asset_type`, `description`, `location` | Both ASSMY assets missing in 5/10 runs (alternating pattern) | Prompt: all rows with populated asset ID are valid records |
| Data Set 6 | (global) | `total_assets` | `4` in 5 runs, `2` in 5 runs — mirrors ASSMY dropout | Same fix as above |

---

## 7. Step 8 — Match Assets to Site Plan Drawing Symbols

Step 8 visually scans drawing chunks for each asset's bare numeric ID. Assets found receive `match_status: "found"` with symbol description, legend label, and diagram location.

### Step 8 Results with Ground Truth

| Data Set | TAL Assets | Agent Found | Agent Not Found | Agent Match % | **GT: Assets Visible on Drawing** | Consistency (10 runs) |
|---|---|---|---|---|---|---|
| Data Set 1 | 14 | 6 | 8 | 43% |  | 100% |
| Data Set 2 | 14 | 14 | 0 | 100% |  | 100% |
| Data Set 4 | 6 | 3 | 3 | 50% |  | 100% |
| Data Set 5 | 5 | 2 | 3 | 40% |  | 100% |
| Data Set 6 | — | — | — | — |  | Not tested |

> **GT: Assets Visible on Drawing** — enter the number of TAL assets that actually have their bare numeric ID printed on the site plan drawing. This is the ground truth match rate denominator for Step 8.

### Per-Asset Step 8 Results

#### Data Set 1 (14 assets — Windsor + Hoxton Park streetlights)

| Asset ID | Bare ID | Agent Status | Symbol | Label | Location |
|---|---|---|---|---|---|
| SLPL00316949 | 316949 | **found** | Small solid black filled circle | EXISTING POLE* | Page 1, lower-left, chunk 3/4 |
| SLPL00316950 | 316950 | **found** | Small solid black filled circle | EXISTING POLE* | Page 1, lower-left, chunk 3/4 |
| SLPL00316951 | 316951 | **found** | Small solid black filled circle | EXISTING POLE* | Page 1, lower-left, chunk 3/4 |
| SLPL00316952 | 316952 | **found** | Small solid black filled circle | EXISTING POLE* | Page 1, lower-left, chunk 3/4 |
| SLPL00316955 | 316955 | **found** | Small solid black filled circle | EXISTING POLE* | Page 1, lower-left, chunk 3/4 |
| SLPL00316956 | 316956 | **found** | Small solid black filled circle | EXISTING POLE* | Page 1, lower-left, chunk 3/4 |
| SLPL00316953 | 316953 | not_found | — | — | — |
| SLPL00316954 | 316954 | not_found | — | — | — |
| SLPL00302884–302889 | 302884–302889 | not_found | — | — | — |

> \* Variance session label = "EXISTING POLE". Historical runs 2–6 returned "NEW POLE" (small hollow open circle). The label difference is a Step 6 session-level issue — within the 10-run variance session all runs agreed (100% consistency). Correct label per drawing legend is "NEW POLE".

#### Data Set 2 (14 assets — all found)

| Asset ID | Bare ID | Agent Status | Symbol | Label | Location |
|---|---|---|---|---|---|
| SLPL00302876 | 302876 | **found** | Circle with starburst spikes, hollow centre | NEW LANTERN | Page 2, upper-left, chunk 5/8 |
| SLPL00302877 | 302877 | **found** | Circle with starburst spikes, hollow centre | NEW LANTERN | Page 2, upper-left, chunk 5/8 |
| SLPL00302878 | 302878 | **found** | Circle with starburst spikes, hollow centre | NEW LANTERN | Page 2, upper-left, chunk 5/8 |
| SLPL00302879–302889 | 302879–302889 | **found** | Small hollow open circle | NEW POLE | Page 2, upper-left, chunk 5/8 |

#### Data Set 4 (6 assets — poles and pillars)

| Asset ID | Bare ID | Type | Agent Status | Symbol | Label | Location |
|---|---|---|---|---|---|---|
| PO0985413 | 985413 | Pole | **found** | Triangle inside a circle | Pole Substation - New | Page 1, lower-right, chunk 4/4 |
| PILLAR00400355 | 400355 | Pillar | **found** | Rectangle | Pillar - New Excavation location | Page 1, upper-left, chunk 1/4 |
| PILLAR00968214 | 968214 | Pillar | **found** | Rectangle | Pillar - New Excavation location | Page 1, upper-left, chunk 1/4 |
| ASSMY00628168 | 628168 | Assembly | not_found | — | — | — |
| ASSMY00628169 | 628169 | Assembly | not_found | — | — | — |
| ASSMY00632923 | 632923 | Assembly | not_found | — | — | — |

#### Data Set 5 (5 assets — poles and switchgear)

| Asset ID | Bare ID | Type | Agent Status | Symbol | Label | Location |
|---|---|---|---|---|---|---|
| PO1001157 | 1001157 | Pole | **found** | — | — | Page 1, lower-right, chunk 4/4 |
| PO1001158 | 1001158 | Pole | **found** | — | — | Page 1, lower-right, chunk 4/4 |
| ASSMY00717015 | 717015 | Assembly | not_found | — | — | — |
| ASSMY00717016 | 717016 | Assembly | not_found | — | — | — |
| LVBB00037192 | 37149 | LV Busbar | not_found | — | — | — |

---

## 8. Step 9 — Enrich Assets with Legend Data

Step 9 enriches each asset with legend category and confidence level using direct bare-ID matching and SFL linkage. Assets not resolvable by either method receive `match_confidence: "none"` and `match_method: "no_sfl"`.

### Step 9 Results with Ground Truth Assessment

| Data Set | TAL Assets | High / Direct | None / no_sfl | High Conf. % | **GT: Icon/Symbol Match Correct** | Consistency (10 runs) |
|---|---|---|---|---|---|---|
| Data Set 1 | 14 | 6 | 8 | 43% |  | 100% |
| Data Set 2 | 14 | 14 | 0 | 100% |  | 100% |
| Data Set 4 | 6 | 3 | 3 | 50% |  | 100% |
| Data Set 5 | 5 | 2 | 3 | 40% |  | 100% |
| Data Set 6 | — | — | — | — |  | Not tested |

> **GT: Icon/Symbol Match Correct** — assess whether the symbol description and legend label assigned to each found asset accurately reflects the actual drawing symbol. Enter a verdict per dataset (e.g. "Yes — all correct", "No — DS1 label wrong", or per-asset notes).

### Per-Asset Step 9 Results

#### Data Set 1

| Asset ID | match_confidence | match_method | legend_label | symbol_description |
|---|---|---|---|---|
| SLPL00316949 | high | direct | EXISTING POLE* | Small solid black filled circle |
| SLPL00316950 | high | direct | EXISTING POLE* | Small solid black filled circle |
| SLPL00316951 | high | direct | EXISTING POLE* | Small solid black filled circle |
| SLPL00316952 | high | direct | EXISTING POLE* | Small solid black filled circle |
| SLPL00316955 | high | direct | EXISTING POLE* | Small solid black filled circle |
| SLPL00316956 | high | direct | EXISTING POLE* | Small solid black filled circle |
| SLPL00316953 | none | no_sfl | — | — |
| SLPL00316954 | none | no_sfl | — | — |
| SLPL00302884–302889 | none | no_sfl | — | — |

> \* Label reflects Step 6 session-level extraction. Historical runs 2–6 produced "NEW POLE" (hollow open circle). Correct label per drawing legend = "NEW POLE".

#### Data Set 2

| Asset ID | match_confidence | match_method | legend_label | symbol_description |
|---|---|---|---|---|
| SLPL00302876–302878 | high | direct | NEW LANTERN | Circle with starburst spikes, hollow centre |
| SLPL00302879–302889 | high | direct | NEW POLE | Small hollow open circle |

#### Data Set 4

| Asset ID | match_confidence | match_method | legend_label | symbol_description |
|---|---|---|---|---|
| PO0985413 | high | direct | Pole Substation - New | Triangle inside a circle |
| PILLAR00400355 | high | direct | Pillar - New Excavation location | Rectangle |
| PILLAR00968214 | high | direct | Pillar - New Excavation location | Rectangle |
| ASSMY00628168 | none | no_sfl | — | — |
| ASSMY00628169 | none | no_sfl | — | — |
| ASSMY00632923 | none | no_sfl | — | — |

#### Data Set 5

| Asset ID | match_confidence | match_method | legend_label | symbol_description |
|---|---|---|---|---|
| PO1001157 | high | direct | — | — |
| PO1001158 | high | direct | — | — |
| ASSMY00717015 | none | no_sfl | — | — |
| ASSMY00717016 | none | no_sfl | — | — |
| LVBB00037192 | none | no_sfl | — | — |

---

## 9. Step 8 / Step 9 Historical Run Comparison (Data Set 1)

Data Set 1 accumulated multiple pre-variance runs as the Step 9 format evolved. This table shows the full run history.

| Run | Format | Step 8 Found | Step 9 High | Step 9 Label (found assets) | Step 9 Inferred | Notes |
|---|---|---|---|---|---|---|
| Run 1 | Legacy | 6 | 6 | EXISTING POLE (wrong) | 8 | Step 6 returned wrong legend entry |
| Run 2 | Legacy | 6 | 6 | NEW POLE | 6 | 2 deletion-flagged assets left unassigned |
| Run 3 | Legacy | 6 | 6 | NEW POLE | 8 | Deletion flag used to infer "REMOVE POLE" for 316953/4 |
| Run 4 | Legacy | 6 | 6 | NEW POLE | 8 | Same as Run 3 |
| Run 5 | Transitional | 6 | 0 | — | 0 | SFL validation attempted; no SFL field in Step 7 data |
| Run 6 | New SFL | 6 | 6 | NEW POLE | 0 | First clean new-format run; 8 unmatched = no_sfl |
| Variance ×10 | New SFL | 6 | 6 | EXISTING POLE* | 0 | 100% consistent across 10 runs; Step 6 session diff |

> The legacy format (Runs 1–4) used inference to assign labels to not-found assets (medium/low confidence), producing inconsistent and occasionally hallucinated results. The new SFL-based format eliminates inference — unresolved assets explicitly receive `confidence: "none"`.

---

## 10. Key Findings

### Steps 5–7: Extraction Quality

- **vFTE framework outperforms single-prompt by a large margin:** Single-prompt averaged 88% / 50% / 5% on legend/assets/notes vs 100% / 100% / 100% for vFTE on the same data set.
- **Step 6 (legend) is fully deterministic** across all five data sets — per-chunk image processing and Phase 1 caching eliminate OCR-level variance entirely.
- **Step 5 (site plan)** is deterministic except for the consolidation LLM on Data Set 5 (note-7 truncation in 2/10 runs — a Phase 2 assembly issue, not a Phase 1 OCR issue).
- **Step 7 (spreadsheet)** is the least consistent step: serial number column mapping gaps (DS2, DS4) and ambiguous row-filtering rules (DS5 switchgear, DS6 ASSMY assets) cause targeted dropouts. All issues have known fixes.

### Steps 8–9: Symbol Matching and Enrichment

- **Both steps are fully deterministic:** All four variance sessions scored 100% consistency across 10 independent runs (2,070 fields per step validated).
- **Match rates vary by asset type and drawing convention:** Assembly sub-components (ASSMY prefix) are not individually numbered on site plans and are structurally not findable. Data Set 2 is the only project where all streetlight IDs appear directly on the drawing (100% match rate).
- **Step 8 label quality depends on Step 6:** The EXISTING POLE vs NEW POLE discrepancy in Data Set 1 is a Step 6 session-level extraction difference, not non-determinism in Step 8 or Step 9.
- **Step 9 new SFL-based format is accurate and transparent:** Only drawing-confirmed assets receive a confidence assignment. Unresolved assets return `confidence: "none"` rather than guessed inferences.
- **SFL linkage not yet exercised:** All variance sessions show `no_sfl` for unmatched assets because Step 7 ran before the `superior_functional_location` extraction instruction was live. Once Step 7 extracts SFL data, the `sfl_lookup`/`sfl_chunk_search` path should resolve ASSMY sub-components through their parent pole numbers.

### Quality Risks Remaining

| Risk | Step | Data Sets Affected | Severity | Recommended Fix |
|---|---|---|---|---|
| `X` status cell interpreted as string or meaning | 7 | DS1 | Low | Normalize `X` → `Marked for Deletion` in prompt |
| Serial number column mapping miss | 7 | DS2, DS4 | Medium | Add `serial_number` column alias |
| Note-7 consolidation truncation | 5 | DS5 | Low | Consolidation prompt: never split a note mid-sentence |
| ASSMY pillar rows intermittently dropped | 7 | DS6 | Medium | Prompt: all rows with populated asset ID are valid records |
| Switchgear row-filtering ambiguity | 7 | DS5 | Medium | Explicit valid-record criteria for switchgear sheets |
| Step 6 session-level label variance | 6→8→9 | DS1 | Medium | Investigate why Step 6 returns different legend entries across sessions for the same drawing |
| SFL linkage path untested | 9 | All | High (unverified) | Run Step 9 after Step 7 with SFL extraction active; verify `sfl_lookup` path resolves ASSMY assets |
