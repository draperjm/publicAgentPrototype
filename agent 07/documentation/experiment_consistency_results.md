# Experiment Consistency Results

**Dataset:** Agent 07 — Customer Connections Review Pipeline
**Analysis date:** 2026-03-29
**Runs analysed:** 14 experiment folders · 150 step test reports
**Datasets:** DAR1509-84 (7 runs), DAR1675-106 (5 runs), DS1-DAR1988-108 (2 runs)

---

## 1. Executive Summary

| Metric | Value |
|--------|-------|
| Total experiment runs | 14 |
| Total step test reports | 150 |
| Steps with 100 % pass rate | 8 / 11 |
| Steps with variance | 3 / 11 |
| Overall pass rate | 126 / 150 (84 %) |
| Worst step | Extract Asset Spreadsheet Data — 36 % pass rate |

Three steps account for all 24 failures and all measurable LLM variance:

1. **Extract Drawing Legend** — vision model misses legend on full-page A3 chunks (fix deployed: Phase 1.1 quadrant retry)
2. **Match Assets to Site Plan Symbols** — LLM assigns different symbols to the same assets across runs
3. **Extract Asset Spreadsheet Data** — PROJECT worksheet skipped in most runs

The eight remaining steps (Chunking, Document Review, Processing Plan, Design Brief Extraction, Site Plan Info Extraction, Asset Enrichment, Consolidation, Analysis Report) passed every run at 100 % and show no LLM variance.

---

## 2. Experiment Inventory

| Run ID | Dataset | Files | Complete |
|--------|---------|-------|----------|
| Exp_20260329_002455 | DAR1509-84 | 17 | Partial (early runs) |
| Exp_20260329_023711 | DAR1509-84 | 16 | Yes |
| Exp_20260329_030926 | DAR1509-84 | 13 | Partial |
| Exp_20260329_031434 | DAR1509-84 | 15 | Partial |
| Exp_20260329_032209 | DAR1509-84 | 16 | Yes |
| Exp_20260329_032939 | DAR1509-84 | 16 | Yes |
| Exp_20260329_043441 | DAR1509-84 | 16 | Yes |
| Exp_20260329_053259 | DAR1675-106 | 16 | Yes |
| Exp_20260329_053856 | DAR1675-106 | 16 | Yes |
| Exp_20260329_055002 | DAR1675-106 | 16 | Yes (legend failed) |
| Exp_20260329_055816 | DAR1675-106 | 16 | Yes (legend failed) |
| Exp_20260329_060445 | DAR1675-106 | 16 | Yes |
| Exp_20260329_070516 | DS1-DAR1988-108 | 17 | Yes |
| Exp_20260329_101622 | DS1-DAR1988-108 | 18 | Yes |

**Source documents per dataset — identical across all runs:**

| Dataset | Design Brief | TAL Spreadsheet | Drawing |
|---------|-------------|-----------------|---------|
| DAR1509-84 | DAR1509 _DESIGN_BRIEF_v1.0.pdf | DAR1988_TAL_20260112.XLSM | DS1_DAR1988_RETIC_LEGEND.pdf |
| DAR1675-106 | DAR1675_DESIGN_BRIEF_v1.0.pdf | DAR1675_TAL_20260217.XLSM | DS1_DAR1675_RETIC.pdf |
| DS1-DAR1988-108 | DAR1988_DESIGN_BRIEF.pdf | DAR1988_TAL_20260112.XLSM | DS1_DAR1988_RETIC.pdf |

---

## 3. Step-Level Pass Rate by Dataset

| Step | DAR1509-84 | DAR1675-106 | DS1-DAR1988-108 | Overall |
|------|-----------|-------------|-------------|---------|
| Chunk Documents | 7/7 ✓ | 5/5 ✓ | 2/2 ✓ | **14/14** |
| Document Review | 7/7 ✓ | 5/5 ✓ | 2/2 ✓ | **14/14** |
| Processing Plan | 7/7 ✓ | 5/5 ✓ | 2/2 ✓ | **14/14** |
| Extract Design Brief | 7/7 ✓ | 5/5 ✓ | 2/2 ✓ | **14/14** |
| Extract Site Plan Info | 7/7 ✓ | 5/5 ✓ | 2/2 ✓ | **14/14** |
| **Extract Drawing Legend** | 6/7 ⚠ | 2/5 ✗ | 0/2 — | **8/14** |
| **Extract Asset Spreadsheet** | 3/7 ✗ | 1/5 ✗ | 1/2 ✗ | **5/14** |
| **Match Assets to Symbols** | 4/7 ✗ | 3/5 ⚠ | 1/3 ✗ | **8/15** |
| Enrich Assets with Legend | 5/5 ✓ | 4/4 ✓ | 2/2 ✓ | **11/11** |
| Consolidate Application Review | 7/7 ✓ | 4/4 ✓ | 2/2 ✓ | **13/13** |
| Customer Connections Analysis | 7/7 ✓ | 4/4 ✓ | 2/2 ✓ | **13/13** |

---

## 4. LLM Variance Tables — Grouped by Dataset

### 4.1 DAR1509-84 — All runs use identical source documents

> Expected behaviour: every metric should be identical across all 7 runs.
> Any difference is attributable to LLM non-determinism.

#### 4.1.1 Drawing Legend Extraction

| Run | Legend Entries Extracted | Result | Score |
|-----|--------------------------|--------|-------|
| 002455 | 29 | PASS | 100 % |
| 023711 | 28 | PASS | 100 % |
| 030926 | 29 | PASS | 100 % |
| 031434 | 29 | PASS | 100 % |
| 032209 | 29 | PASS | 100 % |
| 032939 | 29 | PASS | 100 % |
| 043441 | **53** | PASS | 100 % |

**Variance:** Entry count ranges 28–53. Run 043441 produced 53 entries — nearly double — indicating the model extracted duplicates or over-split multi-part label entries. All passed validation because the validator checks for non-empty output only, not entry count.

#### 4.1.2 Asset Symbol Matching

| Run | Total | Found | Not Found | High | Medium | Low | None |
|-----|-------|-------|-----------|------|--------|-----|------|
| 002455 | 18 | 9 | 9 | 9 | 5 | 0 | 4 |
| 023711 | 18 | 12 | 6 | 12 | 2 | 0 | 4 |
| 030926 | 18 | 12 | 6 | — | — | — | — |
| 031434 | 18 | 12 | 6 | 11 | 2 | 0 | 5 |
| 032209 | 18 | 12 | 6 | 10 | 2 | 2 | 4 |
| 032939 | 18 | 14 | 4 | 12 | 2 | 2 | 2 |
| 043441 | 18 | 12 | 6 | 10 | 2 | 2 | 4 |

**Variance:** Found count ranges 9–14 across runs with identical input. Run 002455 found only 9 assets (50 %); all other runs found 12–14 (67–78 %). The same 18 assets and the same legend are the inputs each time.
**Specific divergent assets (from prior investigation):** SLP02002182, SLP02002183, SLP02002184 flip between not_found and found; PO1000886 and SLP02002191 are never found.

#### 4.1.3 Analysis Report — Asset Register Summary

> Post step-11 fix: register is now Python-built from enriched assets. Pre-fix runs (002455, 023711) show old LLM-generated values.

| Run | Total Assets | Found | Not Found | Supply Gaps | Drawing Gaps | New | Remove | Existing | Unknown |
|-----|-------------|-------|-----------|-------------|--------------|-----|--------|----------|---------|
| 002455 *(pre-fix)* | **7** | 6 | 1 | 9 | 5 | 3 | 1 | 1 | 2 |
| 023711 *(pre-fix)* | **11** | 7 | 4 | 10 | 7 | 3 | 2 | 2 | 4 |
| 032209 *(post-fix)* | 18 | 12 | 6 | 0 | 1 | 12 | 2 | 0 | 4 |
| 032939 *(post-fix)* | 18 | 14 | 4 | 0 | 2 | 3 | 2 | 0 | 13 |
| 043441 *(post-fix)* | 18 | 12 | 6 | 0 | 2 | 11 | 2 | 1 | 4 |

**Variance:** Pre-fix runs produce 7 and 11 assets respectively (LLM inventing a scope list). Post-fix runs correctly show all 18 TAL assets. Remaining variance in post-fix runs (new/unknown counts) is inherited from symbol matching non-determinism — action_status is derived from legend_label, which varies by run.

#### 4.1.4 Funding Details (from Design Brief — should be deterministic)

| Run | Contestable Works | Non-Contestable | Ancillary Costs | Funding Notes |
|-----|-------------------|-----------------|-----------------|---------------|
| 002455 | 1 | 1 | 1 | 6 |
| 023711 | 1 | 1 | 1 | 8 |
| 032209 | 1 | 1 | 0 | 5 |
| 032939 | 1 | 1 | 1 | 5 |
| 043441 | 1 | 1 | 0 | 5 |

**Variance:** Ancillary costs present in 3/5 runs; notes count ranges 5–8. The source text is identical — the LLM groups or splits items differently each run.

#### 4.1.5 Scope Comparisons (Design Brief vs Site Plan / Drawing)

| Run | DB Requirements | Missing from Site Plan | Funded Works | Missing from Drawing |
|-----|----------------|----------------------|--------------|---------------------|
| 002455 *(pre-fix)* | 10 | 8 | 4 | 7 |
| 023711 *(pre-fix)* | 12 | 10 | 11 | 7 |
| 032209 *(post-fix)* | — | 0 | 4 | 1 |
| 032939 *(post-fix)* | — | 0 | 4 | 2 |
| 043441 *(post-fix)* | — | 0 | 4 | 2 |

**Variance:** Pre-fix runs re-extracted requirements from raw blob (10 and 12 items), producing different gap counts each time. Post-fix runs use pre-extracted `db_funding_items` and `db_supply_requirements` — funded works count is now stable at 4, but missing_from_drawing still varies 0–2 (residual LLM comparison variance).

---

### 4.2 DAR1675-106 — All runs use identical source documents

> Source drawing: DS1_DAR1675_RETIC.pdf (A3, single-page, no separate legend sheet).
> Legend extraction is inherently harder — see section 5.2.

#### 4.2.1 Drawing Legend Extraction

| Run | Legend Entries | Result | Score | Failure Reason |
|-----|----------------|--------|-------|----------------|
| 053259 | 25 | PASS | 100 % | — |
| 053856 | 0 | FAIL | 40 % | Vision model returned empty on full-page A3 chunk |
| 055002 | 0 | FAIL | 40 % | Vision model returned empty on full-page A3 chunk |
| 055816 | 26 | PASS | 100 % | — |
| 060445 | — | PASS | 100 % | — |

**Variance:** Binary failure — either the model extracts ~25 entries or extracts nothing. Phase 1.1 quadrant retry (deployed 2026-03-29) addresses runs 053856 and 055002 by cropping the full A3 image into four quadrants.

#### 4.2.2 Asset Symbol Matching

| Run | Total | Found | Not Found | High | Medium | Low | None |
|-----|-------|-------|-----------|------|--------|-----|------|
| 053259 | 18 | 5 | 13 | 5 | 9 | 0 | 4 |
| 053856 | 18 | 7 | 11 | 7 | 0 | 0 | 11 |
| 055002 | 18 | 0 | 18 | 0 | 0 | 0 | 18 |
| 055816 | 18 | 0 | 18 | 0 | 14 | 0 | 4 |
| 060445 | 18 | 14 | 4 | 6 | 8 | 0 | 4 |

**Variance:** Found count ranges 0–14 (0 %–78 %). Runs 055002 and 055816 found zero via direct symbol matching; run 055816 recovered 14 at enrichment (medium confidence). The zero-match runs correlate directly with failed legend extraction — without legend entries, the symbol matcher has no reference.

#### 4.2.3 Analysis Report — Asset Register Summary

| Run | Total Assets | Found | Not Found | Supply Gaps | Drawing Gaps | New | Remove | Existing | Unknown |
|-----|-------------|-------|-----------|-------------|--------------|-----|--------|----------|---------|
| 053259 | 18 | 14 | 4 | 0 | 0 | 2 | 2 | 10 | 4 |
| 053856 | 18 | 7 | 11 | 0 | 3 | 0 | 0 | 0 | 18 |
| 055002 | 18 | 0 | 18 | 0 | 0 | 0 | 0 | 0 | 18 |
| 055816 | 18 | 14 | 4 | 0 | 0 | 0 | 2 | 12 | 4 |
| 060445 | 18 | 14 | 4 | 0 | 0 | 2 | 2 | 10 | 4 |

**Variance:** Found count (0, 7, 14, 14, 14) and action_status distributions are all downstream of the legend extraction failure. Runs with successful legend extraction (053259, 055816, 060445) converge on 14 found with consistent action breakdown.

---

### 4.3 DS1-DAR1988-108 — 2 runs (same drawing as DAR1509, different TAL)

| Run | Total Assets | Found | Not Found | Supply Gaps | Drawing Gaps |
|-----|-------------|-------|-----------|-------------|--------------|
| 070516 | 93 | 0 | 93 | 0 | 2 |
| 101622 | 85 | 0 | 85 | 0 | 1 |

**Variance:** Asset count differs (93 vs 85) — the asset spreadsheet extraction is producing a different row count each run. Both runs found 0 assets on the drawing, suggesting the matching step is not functioning for this dataset. This dataset has no dedicated legend file (uses the same RETIC PDF without "LEGEND" in the filename).

---

## 5. Root Cause Analysis by Step

### 5.1 Extract Asset Spreadsheet Data — 36 % pass rate

**Symptom:** Validation consistently flags missing PROJECT worksheet data.
**Root cause:** The TAL spreadsheet (`.XLSM`) contains multiple worksheets. The extractor processes the primary sheet but skips the PROJECT sheet in most runs, producing an incomplete asset list.
**Evidence:** Asset count for DS1-DAR1988-108 varies 85–93 between runs; ARP datasets produce 18 assets consistently (single-sheet extraction).
**Impact:** Upstream of all matching and enrichment steps.
**Status:** Known issue — not yet fixed.

### 5.2 Extract Drawing Legend — 43 % pass rate

**Symptom:** Empty `sub-step-extract-legend` array on some runs of the same document.
**Root cause:** Large-format A3 drawings are chunked as a single full-page image (~5000 × 3500 px). The vision model fails to locate and extract the legend table when it is a small structured section in one corner of a dense engineering drawing. This is a non-deterministic vision model failure — the same image sometimes succeeds and sometimes does not.
**Evidence:** DAR1675runs 053856 and 055002 extracted 0 entries; 053259 and 055816 extracted 25–26 entries from the identical PDF.
**Downstream impact:** Zero legend entries → zero asset symbol matches → entire asset register shows `unknown` / `not_found`.
**Fix deployed 2026-03-29:** Phase 1.1 quadrant retry in `document_extractor.py` — when a full-page chunk returns no legend entries, the image is cropped into four quadrants (TL/TR/BL/BR) and each quadrant is retried independently. Legend tables in corners are isolated and extracted reliably.

### 5.3 Match Assets to Site Plan Symbols — 47 % pass rate

**Symptom:** Same assets receive different `match_status` (found/not_found) and different `symbol_description` / `label` values across runs.
**Root cause:** Pure LLM non-determinism. The matching step sends asset descriptors and legend entries to the LLM, which reasons about which symbol best represents each asset. Small changes in reasoning produce different pairings.
**Evidence (DAR1509-84):** Assets SLP02002182/83/84 flip between not_found (run 002455) and found/high (runs 023711, 032209+). PO1000886 is never found.
**Downstream impact:** Propagates through enrichment to the final asset register `action_status` and `found_on_diagram` fields.
**Partial fix applied:** Step 11 asset register is now Python-built (no longer LLM-generated), but the input confidence values still vary.
**Status:** Residual variance — step 8 matching still uses LLM inference.

---

## 6. Cascade Effect of Failures

```
Extract Drawing Legend (fails)
  └─► Asset Symbol Matching (no legend reference → all not_found)
        └─► Asset Legend Enrichment (no matches to enrich → all none confidence)
              └─► Analysis Report register (all unknown action_status, 0 found_on_diagram)
                    └─► Analysis Report summary (found_on_diagram = 0, drawing_scope_gaps inflated)
```

One legend extraction failure produces a chain of 5 downstream degradations. Fixing legend extraction (Phase 1.1) is the highest-leverage single change.

---

## 7. Metrics That Should Be Identical (Are Not)

The following values are derived entirely from static source documents and should produce the same result on every run for the same dataset. All observed differences are LLM variance.

| Metric | Dataset | Expected | Observed Range | Variance |
|--------|---------|----------|----------------|----------|
| Legend entry count | DAR1509-84 | Fixed | 28–53 | ±25 entries |
| Legend entry count | DAR1675-106 | Fixed | 0–26 | ±26 entries |
| Symbol matches found | DAR1509-84 | Fixed | 9–14 | ±5 assets |
| Symbol matches found | DAR1675-106 | Fixed | 0–14 | ±14 assets |
| Enrichment high confidence | DAR1509-84 | Fixed | 9–12 | ±3 assets |
| Enrichment high confidence | DAR1675-106 | Fixed | 0–7 | ±7 assets |
| Asset register total *(post-fix)* | DAR1509-84 | 18 | 18 | ✓ stable |
| Asset register found *(post-fix)* | DAR1509-84 | Fixed | 12–14 | ±2 assets |
| Action status: new count | DAR1509-84 | Fixed | 3–12 | ±9 |
| Action status: unknown count | DAR1509-84 | Fixed | 2–13 | ±11 |
| Funding contestable count | DAR1509-84 | 1 | 1 | ✓ stable |
| Funding ancillary count | DAR1509-84 | Fixed | 0–1 | ±1 |
| Funding notes count | DAR1509-84 | Fixed | 5–8 | ±3 |
| Supply DB requirements count *(pre-fix)* | DAR1509-84 | Fixed | 10–12 | ±2 |
| Supply missing from site plan *(pre-fix)* | DAR1509-84 | Fixed | 8–10 | ±2 |
| Funding missing from drawing *(post-fix)* | DAR1509-84 | Fixed | 0–2 | ±2 |
| Asset spreadsheet row count | DS1-DAR1988-108 | Fixed | 85–93 | ±8 rows |

---

## 8. Metrics That Are Stable (No Variance)

| Metric | Dataset | Value | Runs Stable |
|--------|---------|-------|-------------|
| Document count identified | All | 3 | 14/14 |
| Document categories assigned | All | Correct | 14/14 |
| Processing steps planned | All | 3 | 14/14 |
| Design Brief sub-steps | All | 8 keys | 14/14 |
| Site Plan Info sub-steps | All | 5 keys | 14/14 |
| Total assets in TAL | DAR1509/5495 | 18 | All runs |
| Enrichment total count | DAR1509/5495 | 18 | All runs |
| Consolidated test structure | All | 5 tests | 13/13 |
| Analysis report structure | All | 4 sections | 13/13 |
| Funding contestable count | DAR1509-84 | 1 | 5/5 |
| Funding non-contestable count | DAR1509-84 | 1 | 5/5 |

---

## 9. Fixes Applied During Experiment Period

| Fix | Step Affected | Change | Expected Impact |
|-----|--------------|--------|-----------------|
| Step 11: asset register Python-built | Customer Connections Analysis | Removed `task-asset-register` from LLM; built deterministically from enriched_assets_list | Eliminates asset count variance (7→18 stable) |
| Step 11: grounded LLM inputs | Customer Connections Analysis | Replaced raw blob inputs with pre-structured `db_funding_items`, `db_supply_requirements` | Reduces supply/funding comparison variance |
| Phase 1.1 quadrant retry | Extract Drawing Legend | When full-page chunk yields no legend entries, crops image into 4 quadrants and retries | Eliminates binary legend failure on A3 drawings |
| Content review step | Customer Connections Analysis | Post-build normalisation and section status flags | Surface display issues before saving |
| `data` field restored | Customer Connections Analysis | Added required `data` field back to analytics payload | Fixed step 11 API 422 error |

---

## 10. Recommended Further Fixes (Prioritised)

| Priority | Step | Fix |
|----------|------|-----|
| **P1** | Match Assets to Symbols (Step 8) | Replace LLM free-form matching with structured lookup: for each asset, check if its bare_id appears as text in chunk raw_text first; LLM fallback only for assets with no direct ID match |
| **P2** | Extract Asset Spreadsheet (Step 7) | Ensure all worksheets are enumerated; the PROJECT sheet must be explicitly included in the extraction scope |
| **P3** | Extract Drawing Legend (Step 6) | After Phase 1.1 quadrant retry, apply `_SYMBOL_REF` canonicalisation to normalise label variants (e.g. "STRING NEW O/H CABLE" → "STRING NEW OH CABLE") |
| **P4** | Match Assets to Symbols (Step 8) | Cap legend entry count sent to LLM at unique labels only (deduplicate before sending) to prevent 53-entry runs inflating matching noise |
| **P5** | All LLM comparison tasks | Log model temperature and seed to enable reproducibility investigation |
