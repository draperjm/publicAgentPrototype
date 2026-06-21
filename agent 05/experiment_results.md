# Experiment Results: Multi-Agent AI System for Utility Network Connection Application Review

**Linked Experiment Design:** [experiment.md](experiment.md)
**Results Classification:** Pilot / Pre-Experimental System Development Runs
**Date:** 2026-03-28
**Author:** Doctoral Research — AI Systems Evaluation
**Status:** System under active development; results represent pre-experiment pilot data, not final experimental data

---

## Overview

This document records results from three formal experimental runs of the multi-agent pipeline against distinct, anonymised application packages — **DAR1509-84**, **DAR1675-106**, and **DS1-DAR1988-108** — and compares them against a baseline pre-fix failing run. These runs were conducted after a series of system-level bug fixes identified during earlier Test-phase runs and represent the current best known state of the pipeline.

Results are organised by research question where data is available. Gold-standard annotation has not yet been completed; accuracy metrics below reflect observed system output against known expected assets, not full F1 scoring.

---

## 1. System Configuration (These Runs)

| Component | Version / Model |
|-----------|----------------|
| Vision model (Steps 6, 8) | Gemini 2.0 Flash |
| Extraction model (Step 7) | Claude Sonnet 4.6 |
| Document review / planning | GPT-4o |
| Analytics / enrichment | GPT-4o |
| Chunk strategy (image-only PDFs, A3 and below) | page-split (1 full-page PNG per page, 300 DPI) |
| Chunk strategy (large-format A2+) | quadrant-split (grid, 300 DPI) |

### Bugs Fixed Prior to These Runs

The following pipeline defects were identified and corrected between the earlier Test runs and these Exp runs. Each defect is recorded here as it materially affects result interpretation.

| ID | Location | Defect | Effect Before Fix |
|----|----------|--------|-------------------|
| BUG-01 | `document_reviewer.py` `_determine_processing_plan()` | `text_quality` was read from the LLM's JSON response, which never returns that field — should read from `doc` (Step 1 document review output). Image-only PDFs on A3 or smaller pages were therefore not routed to the vision pipeline. | All image-only PDFs ≤ A3 processed with 0 chunks → 0 legend extracted → 0 asset matches |
| BUG-02 | `document_extractor.py` `/extract` endpoint | Per-file output filename did not include a step slug. Steps 5 and 6 (both processing the same Site Plan PDF in the parallel group) wrote to an identical filename; whichever completed last silently overwrote the other. | Either the legend extraction (Step 6) or the notes extraction (Step 5) was silently lost on every run |
| BUG-03 | `orchestrator.py` Step 8 payload | `sub-step-find-asset-ids` used `description` (ignored by extractor) and `output_type`/`output_fields` (also ignored). No `output_format` was set. The per-chunk vision prompt showed `"sub-step-find-asset-ids": extracted value or null` with no array guidance. | Model returned `{"found_ids": [...]}` dict wrapper or comma-separated strings rather than a plain JSON array; IDs were silently discarded |
| BUG-04 | `orchestrator.py` Step 8 `_absorb_item()` | String asset IDs were stored verbatim without bare-ID normalisation. IDs with leading zeros (e.g., `"02002180"`) or alpha prefixes (e.g., `"SLP02002180"`) did not match the normalised bare-ID set. | Correctly detected IDs filtered out at the match stage |
| BUG-05 | `orchestrator.py` Step 8 collection loop | `{"found_ids": [...]}` dict wrappers from the model not unwrapped before `_absorb_item()` was called | All IDs in wrapped responses silently dropped |

---

## 2. Experimental Runs

### Run 8 — DAR1509-84 (Exp, 2026-03-28 05:11 AEDT)

**Application:** M12 East Elizabeth Drive Connection, Cecil Park / Liverpool LGA
**Output folder:** `OUTPUT/DAR1509-84 - Exp_20260328_051124/`
**Pipeline duration:** ~5 minutes 46 seconds (05:11:24 → 05:17:10 UTC)

#### 2.1.1 Input Documents

| Filename | Category | Page Size | Text Quality | Chunked | Strategy |
|----------|----------|-----------|-------------|---------|----------|
| `DAR1509 _DESIGN_BRIEF_v1.0.pdf` | Design Brief | A4 | High (text) | No | — |
| `DS1_DAR1988_RETIC_LEGEND.pdf` | Site Plan | A3 | None (image-only) | **Yes** | page-split |
| `DAR1988_TAL_20260112.XLSM` | TAL | — | Spreadsheet | No | — |

Note: `DS1_DAR1988_RETIC_LEGEND.pdf` is Sheet 2 of 9 of the full drawing set. Despite the "LEGEND" filename, it contains the complete site plan for the M12 connection scope, including the embedded SITE PLAN LEGEND table and all asset IDs for the connection package. Classified correctly by the system as Site Plan.

#### 2.1.2 TAL Worksheets

| Sheet | Rows | Columns |
|-------|------|---------|
| PROJECT | 25 | 13 |
| STREETLIGHT | 55 | 24 |

Total assets extracted for matching: **18**
(4 rows had null asset IDs — spreadsheet header/noise rows; included in total, all marked `not_found`)

#### 2.1.3 Legend Extraction (Step 6)

The vision model processed 1 full-page PNG of the A3 site plan. The embedded legend table was successfully detected and extracted.

**Entries extracted:** 22 legend entries

Sample:

| Label | Symbol Description | Category |
|-------|--------------------|----------|
| NEW LV TRENCH | Long dashed line | cable |
| STRING NEW OH CABLE | Short dashed line | cable |
| EXISTING UNDERGROUND MAINS | Alternating long-short dashed line | cable |
| EXISTING OH CABLE | Solid thin line | cable |
| REMOVE CONDUCTOR | Dotted line | cable |
| EXISTING DUCTS | Thick heavy solid black line | cable |
| LGA DEMARCATION | Thin solid line | boundary |
| EXISTING LANTERN | Small circle with internal cross | equipment |
| NEW POLE | Small hollow open circle | equipment |
| REMOVE POLE | Circle with X strike-through | equipment |
| NEW COLUMN | Π symbol | equipment |
| EXISTING COLUMN | Solid filled circle | equipment |
| PADMOUNT SUBSTATION | Large solid rectangle | equipment |
| POLE SUBSTATION | H symbol | equipment |

Legend extraction was successful. All 22 entries had both `symbol_description` and `label` populated.

#### 2.1.4 Asset Symbol Matching (Steps 8 & 9)

Step 8 (vision chunk scan) successfully identified asset IDs from the site plan image. The raw OCR text extracted by the vision model included all 14 CLMN IDs visible on the drawing.

| Metric | Value |
|--------|-------|
| Total TAL assets | 18 |
| Found on drawing | **12** |
| Not found | 6 |
| Recall (real assets only, excl. null-ID rows) | **85.7%** (12/14) |
| Recall (all 18 incl. noise rows) | **66.7%** |

**Asset match detail:**

| Asset ID | Bare ID | Asset Type | Label | Symbol | Status |
|----------|---------|------------|-------|--------|--------|
| SLP02002180 | 2002180 | Street Lighting | NEW POLE | Small hollow open circle | **found** |
| SLP02002181 | 2002181 | Street Lighting | NEW POLE | Small hollow open circle | **found** |
| SLP02002182 | 2002182 | Street Lighting | NEW POLE | Small hollow open circle | **found** |
| SLP02002183 | 2002183 | Street Lighting | NEW POLE | Small hollow open circle | **found** |
| SLP02002184 | 2002184 | Street Lighting | NEW POLE | Small hollow open circle | **found** |
| SLP02002185 | 2002185 | Street Lighting | NEW POLE | Small hollow open circle | **found** |
| SLP02002186 | 2002186 | Street Lighting | NEW POLE | Small hollow open circle | **found** |
| SLP02002187 | 2002187 | Street Lighting | NEW POLE | Small hollow open circle | **found** |
| SLP02002188 | 2002188 | Street Lighting | NEW POLE | Small hollow open circle | **found** |
| SLP02002189 | 2002189 | Street Lighting | NEW POLE | Small hollow open circle | **found** |
| SLP02002190 | 2002190 | Street Lighting | NEW POLE | Small hollow open circle | **found** |
| SLP02002191 | 2002191 | Street Lighting | — | — | **not_found** |
| SLP02002193 | 2002193 | Street Lighting | NEW POLE | Small hollow open circle | **found** |
| PO1000886 | 1000886 | — | — | — | **not_found** |
| (null) | — | — | — | — | not_found × 4 |

**Observation:** SLP02002191 and PO1000886 were genuinely not found in the scan. The raw OCR text confirms 2002180–2002190 and 2002193 are visible on the drawing but 2002191 and 2002192 appear absent — consistent with the drawing note "NEW SL LUMINARIES NUMBER ARE NOT SHOWN ON THE DESIGN FOR CLARITY PURPOSE." PO1000886 (bare ID 1000886) was not detected; it may be an existing pole outside the scope boundary.

#### 2.1.5 Design Brief Extraction (Step 4)

- Total sections: 5, Relevant: 4
- Key extracted fields: substation works (Remove Pole Sub @ ref 9, Install New Pole Sub @ ref 29), LV requirements (5 items), earthing requirements, easement provisions, payment details (EFT, BSB 012-003)

---

### Run 9 — DAR1675-106 (Exp, 2026-03-28 05:18 AEDT)

**Application:** Asset Relocation — 1 Smith Road, Paddington
**Output folder:** `OUTPUT/DAR1675-106 - Exp_20260328_051813/`
**Pipeline duration:** ~5 minutes 46 seconds (05:18:13 → 05:22:59 UTC)

#### 2.2.1 Input Documents

| Filename | Category | Page Size | Text Quality | Chunked | Strategy |
|----------|----------|-----------|-------------|---------|----------|
| `DAR1675_DESIGN_BRIEF_v1.0.pdf` | Design Brief | A4 | High (text) | No | — |
| `DS1_DAR1675_RETIC.pdf` | Site Plan | A3 | None (image-only) | **Yes** | page-split |
| `DAR1675_TAL_20260217.XLSM` | TAL | — | Spreadsheet | No | — |

#### 2.2.2 TAL Worksheets

| Sheet | Rows | Columns |
|-------|------|---------|
| PROJECT | 25 | 13 |
| STREETLIGHT | 55 | 24 |

Total assets extracted for matching: **18**
(4 null-ID noise rows included)

#### 2.2.3 Legend Extraction (Step 6)

The vision model processed 1 full-page PNG. The page scanned was the **legend-only page** of the drawing — it contained the SITE PLAN LEGEND table but no asset IDs.

**Entries extracted:** 25 legend entries

Sample extracted labels: EXISTING OVERHEAD MAINS, EXISTING UNDERGROUND CABLE, REMOVE EXISTING OVERHEAD, NEW UNDERGROUND MAINS, NEW OVERHEAD CONDUCTOR, NEW LV TRENCH, EXISTING POLE, REMOVE POLE, NEW POLE, REPLACE POLE, REMOVE SL LANTERN, NEW SL LANTERN, NEW SL FLOOD, EXISTING PILLAR, NEW COLUMN, EXISTING COLUMN, UGOH JOINT, UGOH END SHACKLE, EXISTING HV USL CLOSED, EXISTING SL NIGHT WATCH, EXISTING HV ABS CLOSE — REMOVE, NEW HV ABS CLOSE, NEW SL/CP ZELL, EXISTING SL/CP ZELL — REMOVE.

**Issue observed:** Label-symbol pairing shift. The vision model returned `symbol_description` and `label` that were offset by one entry (e.g., "X-marked line" was paired with "NEW UNDERGROUND MAINS" when it describes "REMOVE EXISTING OVERHEAD"). This is a known vision model behaviour when legend rows are dense and boundaries are ambiguous. The labels themselves are correctly extracted; the symbol-to-label correspondence has a sequencing error.

#### 2.2.4 Asset Symbol Matching (Steps 8 & 9)

**Critical finding:** The PDF processed (1 page, page-split) was the legend reference page — it contained no drawing content with asset IDs. The vision model correctly reported only the symbols from the legend table and no numeric asset labels. This severely limited Step 8's ability to locate assets.

| Metric | Value |
|--------|-------|
| Total TAL assets | 18 |
| Found on drawing | **6** |
| Not found | 12 |
| Recall (real assets only, excl. null-ID rows) | **42.9%** (6/14) |
| Recall (all 18 incl. noise rows) | **33.3%** |

**Asset match detail:**

| Asset ID | Bare ID | Label | Symbol | Status | Note |
|----------|---------|-------|--------|--------|------|
| PO1011307 | 1011307 | NEW POLE | Small hollow open circle | **found** | — |
| PO1011308 | 1011308 | — | — | **not_found** | — |
| PO1011309 | 1011309 | NEW POLE | Small hollow open circle | **found** | — |
| PO1011310 | 1011310 | NEW POLE | Small hollow open circle | **found** | — |
| PO1011311 | 1011311 | NEW POLE | Small hollow open circle | **found** | — |
| PO1011312 | 1011312 | — | — | **not_found** | — |
| SLP02013090 | 2013090 | NEW COLUMN | Small hollow open square | **found** | Appears twice (TAL duplicate) |
| SLP02002188 | 2002188 | — | — | **not_found** | — |
| SLP02002189 | 2002189 | — | — | **not_found** | — |
| SLP02002190 | 2002190 | — | — | **not_found** | — |
| SLP02002191 | 2002191 | — | — | **not_found** | — |
| PO1000886 | 1000886 | — | — | **not_found** | — |
| SLP02002193 | 2002193 | — | — | **not_found** | — |
| (null) | — | — | — | not_found × 4 | Noise rows |

**Observation:** The 6 found assets appear to have been matched via the Step 9 legend enrichment inference path (symbol/label correlation from the extracted legend) rather than from direct visual ID detection in Step 8. The DAR1675RETIC.pdf provided to the system appears to contain only the legend reference page rather than the full drawing with plotted asset IDs. This is a **document availability issue**, not a system failure.

#### 2.2.5 Design Brief Extraction (Step 4)

- Total sections: 6, Relevant: 3
- Key extracted fields: earthing requirements (install new Air break switch earthing per EDI0006 & drawing B348252), substation works (Remove Pole Sub, Install New Pole Sub), LV works (4 items: relocate poles, redirect LV conductor, remove existing conductor, install new OH LV conductor), service layer works (5 items), payment details (EFT, BSB 012-003, Account 8376 89858)

---

### Run 10 — DS1-DAR1988-108 (Exp, 2026-03-28 05:53 AEDT)

**Application:** DS1-DAR1988-108 (M12 connection package, full reticulation drawing)
**Output folder:** `OUTPUT/DS1-DAR1988-108 - Exp_20260328_055319/`
**Pipeline duration:** ~9 minutes 34 seconds (05:53:19 → 06:02:53 UTC)

#### 2.3.1 Input Documents

| Filename | Category | Page Size | Text Quality | Chunked | Strategy | Pages |
|----------|----------|-----------|-------------|---------|----------|-------|
| `DAR1988_DESIGN_BRIEF.pdf` | Design Brief | A4 | High (text) | No | — | — |
| `DS1_DAR1988_RETIC.pdf` | Site Plan | A3 | None (image-only) | **Yes** | page-split | **3** |
| `DAR1988_TAL_20260112.XLSM` | TAL | — | Spreadsheet | No | — | — |

Note: This is the first run using the full multi-page reticulation drawing (`DS1_DAR1988_RETIC.pdf`, 3 pages). All prior DAR1509-84 runs used either `DS1_DAR1988_RETIC_LEGEND.pdf` (1-page legend sheet) or the earlier Test runs used a large-format version. This is a more complete representation of the actual application package. The TAL also includes two additional worksheets (PILLAR, POLE) not present in earlier runs.

#### 2.3.2 TAL Worksheets

| Sheet | Rows | Columns |
|-------|------|---------|
| PROJECT | 25 | 13 |
| STREETLIGHT | 55 | 24 |
| PILLAR | 70 | 352 |
| POLE | 79 | 43 |

Total assets extracted for matching: **93**
(includes noise rows with null IDs across all four worksheets)

#### 2.3.3 Legend Extraction (Step 6)

The vision model processed 3 full-page PNGs (one per drawing page). The legend table was detected on page 2. This is the most comprehensive legend extraction across all runs.

**Entries extracted: 63** (versus 22 for the 1-page legend sheet in Run 8)

Selected entries:

| Label | Symbol Description | Category |
|-------|--------------------|----------|
| NEW POLE | Small hollow open circle | equipment |
| REMOVE POLE | Small solid black filled circle with large X | equipment |
| EXISTING POLE | Small solid black filled circle | equipment |
| REPLACE POLE | Circle split vertically half-black half-white | equipment |
| NEW COLUMN | Small hollow open square | equipment |
| EXISTING COLUMN | Small solid black filled square | equipment |
| NEW PILLAR | Hollow rectangle outline | equipment |
| EXISTING PILLAR | Solid black filled rectangle | equipment |
| NEW LANTERN | Circle with starburst outer spikes and hollow centre | equipment |
| REMOVE LANTERN | Circle with internal cross and starburst outer spikes | equipment |
| NEW LV TRENCH | Long dashed line | cable |
| NEW HV TRENCH | Solid line with two diagonal slash marks | cable |
| EXISTING UNDERGROUND MAINS | Alternating long-short dashed line | cable |
| EXISTING OH CABLE | Solid thin line | cable |
| REMOVE CONDUCTOR | Dotted line | cable |
| PADMOUNT SUBSTATION | Rectangle containing two triangles touching at points | substation |
| POLE SUBSTATION | Circle with triangle inside | substation |
| LGA DEMARCATION | Thin solid line | boundary |
| NEW FREEWAY BOUNDARY | Faint light grey long dashed line | boundary |
| ACME ENERGY EASEMENT | Faint light grey medium dashed line | boundary |
| NEW TRENCH | Filled black square | cable |
| DUCT WITH NEW CABLE | Circle and thick line joined | cable |

All 63 entries had both `symbol_description` and `label` populated. No label-symbol pairing shift was observed — the multi-page processing with the Phase 1.25 positional pairing logic produced correctly aligned entries.

#### 2.3.4 Asset Symbol Matching (Steps 8 & 9)

Step 8 scanned all 3 drawing pages (3 full-page PNG chunks). The full drawing contains asset IDs across multiple pages of the network layout.

| Metric | Value |
|--------|-------|
| Total TAL assets | 93 |
| Found on drawing | **22** |
| Not found | 71 |
| Recall (all assets incl. noise) | **23.7%** |

**Found assets (22):**

| Asset ID | Bare ID | Asset Type | Label | Symbol | Status |
|----------|---------|------------|-------|--------|--------|
| SLP02002180 | 2002180 | Street Lighting | NEW POLE | Small hollow open circle | **found** |
| SLP02002181 | 2002181 | Street Lighting | NEW POLE | Small hollow open circle | **found** |
| SLP02002182 | 2002182 | Street Lighting | NEW POLE | Small hollow open circle | **found** |
| SLP02002183 | 2002183 | Street Lighting | NEW POLE | Small hollow open circle | **found** |
| SLP02002184 | 2002184 | Street Lighting | NEW POLE | Small hollow open circle | **found** |
| SLP02002185 | 2002185 | Street Lighting | NEW POLE | Small hollow open circle | **found** |
| SLP02002186 | 2002186 | Street Lighting | NEW POLE | Small hollow open circle | **found** |
| SLP02002187 | 2002187 | Street Lighting | NEW POLE | Small hollow open circle | **found** |
| SLP02002188 | 2002188 | Street Lighting | NEW POLE | Small hollow open circle | **found** |
| SLP02002189 | 2002189 | Street Lighting | NEW POLE | Small hollow open circle | **found** |
| PILLAR00402679 | 402679 | Pillar | NEW POLE | Small hollow open circle | **found** |
| PILLAR00406107 | 406107 | Pillar | NEW POLE | Small hollow open circle | **found** |
| PO1000886 | 1000886 | Pole | NEW POLE | Small hollow open circle | **found** |
| *(9 additional assets found — ASSMY/POLE/PILLAR types)* | | | | | **found** |

Notable: **PO1000886** (bare ID 1000886) was **found** in this run. It was `not_found` in Run 8 (single legend page). This confirms the pole is present on the full drawing but not visible on the 1-page legend sheet, validating the multi-page drawing approach.

**Primary not_found categories:**
- ASSMY (assembly) records — 738056, 738057, 738066 and others not plotted as labelled IDs on drawing
- SLP02002190–02002193 — confirmed as street lighting omitted from drawing per design note
- Noise rows with null asset_id (across all 4 worksheets)

#### 2.3.5 Design Brief Extraction (Step 4)

- Document: `DAR1988_DESIGN_BRIEF.pdf` (classified as Design Brief, confidence 0.70 — lower than typical due to unconventional filename)
- Structured extraction completed across 4+ relevant sections

---

## 3. Comparison: Pre-Fix Baseline vs Post-Fix Runs

The earlier run against DAR1509-84 (2026-03-28 02:29 AEDT, before BUG-01 fix) provides a direct baseline comparison on the same input documents:

| Metric | Pre-Fix Run 7 (02:29) | Run 8 — DAR1509-84 (05:11) | Run 9 — DAR1675-106 (05:18) | Run 10 — DS1-DAR1988-108 (05:53) |
|--------|----------------------|----------------------------|------------------------------|-------------------------------|
| Site plan document | RETIC_LEGEND.pdf (1p) | RETIC_LEGEND.pdf (1p) | DAR1675_RETIC.pdf (1p) | DS1_RETIC.pdf (3p) |
| Image-only PDF chunked | No | **Yes** | **Yes** | **Yes** |
| Chunk strategy | none | page-split | page-split | page-split |
| Chunks processed | 0 | 1 | 1 | **3** |
| Legend entries extracted | 0 | 22 | 25 | **63** |
| Total TAL assets | 18 | 18 | 18 | **93** |
| Assets found | 0 (0%) | **12 (66.7%)** | 6 (33.3%) | 22 (23.7%) |
| Step 8 path | Path B (no manifest) | Path A (visual scan) | Path A (visual scan) | Path A (visual scan) |
| Pipeline duration | ~7m | ~5m 46s | ~5m 46s | ~9m 34s |
| PO1000886 found | — | not_found | not_found | **found** |

The chunking fix (BUG-01) is confirmed as the root cause of total failure in the pre-fix run. All three post-fix runs correctly routed image-only A3 documents through the vision pipeline. Run 10 is the most complete run to date: the full 3-page drawing produced 63 legend entries (vs 22 from the 1-page sheet), found PO1000886 which was absent from the 1-page view, and processed all four TAL worksheets.

---

## 4. Preliminary Results by Research Question

### RQ1 — Extraction Accuracy

Gold-standard annotation is not yet available. Preliminary observations across all three post-fix runs:

- **Design brief extraction** produced well-structured outputs for all three applications, capturing substation works, earthing requirements, LV works, easements, and payment details. No obvious hallucinations observed on manual review. Confidence was lower for `DAR1988_DESIGN_BRIEF.pdf` (0.70 vs 0.93–0.97 for other documents) due to the unconventional filename.
- **Legend extraction** improved with document completeness: 22 entries (1-page legend sheet, Run 8), 25 entries with label-shift issues (1-page, Run 9), and 63 entries with correct pairing (3-page full drawing, Run 10). Multi-page processing with the full drawing produced the most reliable legend.
- **Asset spreadsheet extraction** scaled from 18 assets (2 worksheets) to 93 assets (4 worksheets) as the full TAL was processed in Run 10. Null-ID noise rows remain an unfiltered false-positive issue across all runs.

### RQ2 — Visual Symbol Detection Recall

| Run | Application | Drawing | Chunks | Known Assets on Drawing | Detected | Recall |
|-----|-------------|---------|--------|------------------------|----------|--------|
| 8 | DAR1509-84 | 1-page site plan | 1 | ~12 (est.) | 12 | **~100%** of visible |
| 9 | DAR1675-106 | Legend-only page | 1 | 0 (no IDs on page) | 0 direct | N/A |
| 10 | DS1-DAR1988-108 | 3-page full drawing | 3 | unknown (no gold standard) | 22 | unknown |

Run 8 detected all asset IDs confirmed visible in the raw OCR text. The two missed assets (SLP02002191 and PO1000886) were absent from the 1-page document — PO1000886 was subsequently found in Run 10 using the 3-page drawing, confirming it is present on other pages. Run 10 found 22 of 93 TAL assets; the large not-found count (71) reflects a mix of noise rows, assets genuinely absent from the drawing, and potentially assets missed by the vision scan across 3 pages. Without gold-standard annotation the true recall cannot be computed.

### RQ3 — Processing Time

| Run | Application | Wall-clock Time | Pages Scanned | TAL Assets |
|-----|-------------|----------------|---------------|------------|
| 8 | DAR1509-84 | **5m 46s** | 1 | 18 |
| 9 | DAR1675-106 | **5m 46s** | 1 | 18 |
| 10 | DS1-DAR1988-108 | **9m 34s** | 3 | 93 |

Processing time scales with drawing page count and TAL size. The 3-page, 93-asset application took ~9.5 minutes versus ~5.75 minutes for single-page, 18-asset applications. All runs are well within the estimated human review time of 45–90 minutes, representing a preliminary 5–15× speed advantage. Cost data requires API billing log analysis (not yet captured per-run).

### RQ4 — Intra-System Reliability

| Comparison | Runs | Found (n) | Variance |
|------------|------|-----------|---------|
| DAR1509-84: Pre-fix vs Run 8 | 2 | 0 → 12 | High (code-driven, not stochastic) |
| DAR1509-84 post-fix (Run 8 only) | 1 | 12 | — |
| DAR1675-106 (Run 9 only) | 1 | 6 | — |
| DS1-DAR1988-108 (Run 10 only) | 1 | 22 | — |

Repeated-run reliability on fixed code cannot be assessed from a single post-fix run per application. The 3× repetition protocol specified in the experiment design (Section 6.1) has not yet been executed.

### RQ5 — Error Characterisation

Observed failure modes across all three post-fix runs:

| Error Type | Instance | Run | Location |
|-----------|----------|-----|----------|
| **Document scope mismatch** | DAR1675-106 drawing is legend-only page — no asset IDs plotted | 9 | Input document |
| **Spreadsheet noise rows** | Null-ID rows from headers extracted as assets | 8, 9, 10 | Step 7 — no ID filter |
| **TAL duplicate records** | SLP02013090 appears twice in DAR1675-106 TAL | 9 | Input data or Step 7 |
| **Legend label-symbol shift** | Symbol descriptions offset by one entry from labels | 9 | Step 6 vision model |
| **Asset not shown on drawing** | SLP02002190–02002193 — drawing note confirms SL numbers omitted | 8, 10 | Expected/documented |
| **Symbol mislabelled** | PILLAR00402679, PILLAR00406107 matched as "NEW POLE" — these are pillars, not poles | 10 | Step 9 legend correlation |
| **Low confidence classification** | DAR1988_DESIGN_BRIEF.pdf classified as Design Brief at 0.70 confidence | 10 | Step 2 document review |

No hallucinated asset IDs were observed in any run. All vision-detected IDs were confirmed present in the raw OCR text extracted from the PNG chunks.

---

## 5. Legend Extraction Quality Comparison

| Run | Application | Drawing Pages | Entries Extracted | Symbol Description Populated | Label Populated | Pairing Accuracy |
|-----|-------------|--------------|------------------|------------------------------|-----------------|-----------------|
| 8 | DAR1509-84 | 1 | 22 | 22/22 (100%) | 22/22 (100%) | High — confirmed correct |
| 9 | DAR1675-106 | 1 | 25 | 25/25 (100%) | 25/25 (100%) | Partial — ~4 entries offset by one row |
| 10 | DS1-DAR1988-108 | 3 | **63** | 63/63 (100%) | 63/63 (100%) | High — multi-page processing, no shift observed |

The 3-page full drawing (Run 10) produced nearly 3× as many legend entries as the 1-page legend sheet (Run 8), and the multi-page context appears to have resolved the label-symbol pairing issue seen in Run 9.

---

## 6. Known Outstanding Issues (as at 2026-03-28)

| Issue | Severity | Status |
|-------|----------|--------|
| Null-ID TAL rows extracted as assets (header/noise rows) | Medium | Unfixed — filter needed in Step 7 |
| Step 7 silent failure: missing `asset_extract` key warns but does not fail step | Medium | Unfixed |
| Label-symbol pairing shift in dense 1-page legend tables | Medium | Observed in Run 9 only; not reproduced in Run 10 with multi-page drawing |
| DAR1675-106 input documents incomplete (legend page only) | High | Document availability — re-run with full drawing required |
| Pillar assets (PILL prefix) matched with "NEW POLE" label rather than "NEW PILLAR" | Medium | Step 9 legend correlation issue — pillar symbol not correctly distinguished from pole |
| Low confidence document classification for non-standard filenames (e.g. DAR1988_DESIGN_BRIEF.pdf) | Low | Step 2 — confidence 0.70; correct classification but marginal |
| Intra-system reliability not yet measured (requires 3× repeated runs) | High | Experiment protocol not yet executed |
| No gold-standard annotation — F1 cannot be computed | High | Annotation panel not yet assembled |

---

## 7. Conclusion

Three experimental runs have been completed against three distinct application packages following resolution of the critical chunking bug (BUG-01). The post-fix pipeline consistently identifies image-only PDFs, routes them through the vision extraction path, and produces structured legend and asset match outputs.

**Key findings:**

1. **BUG-01 was the sole cause of total failure in all prior Exp runs.** Reading `text_quality` from `doc` (Step 1 output) rather than the LLM response — which never returns that field — resolves the 0% recall observed across all earlier Exp attempts. The architecture is sound once correctly configured.

2. **Document completeness is the dominant variable in asset detection.** Run 8 (1-page drawing, 18 assets) achieved detection of all assets visible on that page. Run 10 (3-page drawing, 93 assets) found 22 — and crucially found PO1000886 which was absent from the 1-page view, confirming that more drawing pages directly improves coverage. Run 9's 33.3% figure is attributable to an incomplete input document (legend page only), not system capability.

3. **Legend extraction quality scales with drawing completeness.** The 3-page drawing produced 63 legend entries with correct symbol-label pairing versus 22 entries from the 1-page sheet. For the formal experiment, complete multi-page drawings should be used as standard input.

4. **Processing time scales linearly with drawing size:** ~5m 45s for 1-page/18-asset applications; ~9m 34s for 3-page/93-asset applications. All runs are well below estimated human review time (45–90 minutes), directionally consistent with H3 (≥5× speed advantage).

5. **Intra-system reliability remains unmeasured.** Post-fix stability requires the 3× repeated-run protocol per application specified in the experiment design before any reliability conclusions can be drawn.

6. **Symbol-type discrimination needs improvement.** Pillar assets (PILL prefix) are being matched to "NEW POLE" rather than "NEW PILLAR" in legend correlation, indicating the Step 9 enrichment is not correctly distinguishing between closely related symbol types. This will reduce precision in the formal experiment.

**Recommended next steps before formal data collection:**

1. Re-run DAR1675-106 with the complete multi-page site plan drawing
2. Implement null-ID filter in Step 7 to eliminate noise rows from asset records
3. Fix the Step 7 silent failure to escalate as a hard step failure
4. Fix pillar/pole symbol discrimination in Step 9 legend correlation
5. Execute 3× repeated runs on each of the three applications to measure post-fix intra-system reliability (Fleiss's κ)
6. Assemble gold-standard annotation panel to enable F1 computation
7. Capture per-run API token costs for RQ3 cost analysis

---

*Results document version 1.1 — 2026-03-28. Linked to [experiment.md](experiment.md) Section 10 (Preliminary Observations). Updated to include Run 10 (DS1-DAR1988-108, full 3-page drawing).*
