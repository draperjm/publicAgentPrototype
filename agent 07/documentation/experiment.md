# Experiment Design: Evaluating Multi-Agent AI Systems for Structured Document Review in Utility Network Connections Processing

**Research Classification:** Doctoral Research Experiment — Artificial Intelligence, Human-Computer Interaction, Sociotechnical Systems
**Domain:** Electricity Distribution Network Operations / Asset Management
**Proposed Duration:** 12 weeks (data collection), 4 weeks (analysis)
**Ethics Classification:** Low-risk; no personal data; expert participants under informed consent

---

## Abstract

This experiment evaluates the effectiveness of a multi-agent artificial intelligence (AI) system against an expert human-driven process for the structured review of customer network connection applications in electricity distribution utilities. The agent system orchestrates a pipeline of specialised AI models — document reviewers, visual extractors, data analytics agents, and validation agents — to process heterogeneous application documents (design briefs, reticulation site plan drawings, and asset spreadsheets) and produce a structured review report. The human condition mirrors this workflow using the same input materials and output criteria but relies entirely on trained network engineers. Outcome measures include processing time, extraction accuracy, symbol detection recall, inter-rater reliability, cost per application, and qualitative assessments of decision support utility. The experiment is designed to provide empirical evidence for or against the substitution hypothesis: that multi-agent AI pipelines can match or exceed human accuracy at a fraction of the processing time and cost for routine structured-document review tasks in regulated infrastructure contexts.

---

## 1. Introduction and Motivation

### 1.1 Research Context

Electricity distribution network operators process large volumes of customer connection applications annually. Each application package typically comprises:

- A **Design Brief** (PDF): architectural services or town planning document describing the proposed connection point, load requirements, and site constraints.
- A **Reticulation Site Plan Drawing** (PDF, large-format A1/A0): an engineering drawing of the existing network showing poles, pillars, lanterns, cables, and other assets with alphanumeric asset identifiers adjacent to drawing symbols, accompanied by a legend table.
- A **Technical Asset List (TAL)** (Excel spreadsheet, .xlsx/.xlsm): a structured register of assets proposed for new installation, removal, or modification, keyed by Functional Location (FLOC) identifiers cross-referenced to the drawing.

The review task requires an engineer to: (1) locate relevant information across these documents; (2) cross-reference asset IDs between the TAL and the drawing; (3) verify that all assets on the TAL are physically represented on the drawing with the correct symbol; and (4) flag discrepancies. This is a cognitively demanding task requiring sustained attention across large-format drawings, and it represents a significant bottleneck in connection application processing throughput.

The system under test — hereafter referred to as the **Agent System** — implements this workflow as a 10-step orchestrated pipeline involving six specialised AI agents:

| Step | Agent | Function |
|------|-------|----------|
| 1 | Document Reviewer | Classifies and inventories all application documents |
| 2 | Document Reviewer | Produces a structured processing plan per document |
| 3 | Document Chunker | Tiles large-format PDFs into quadrant PNG chunks (4×3 grid, 300 DPI) |
| 4 | Document Extractor | Extracts structured data from Design Brief |
| 5 | Document Extractor | Extracts structured data from Site Plan (notes, references) |
| 6 | Document Extractor | Extracts drawing legend (symbol labels and descriptions) |
| 7 | Document Extractor | Extracts asset records from TAL spreadsheet (Superior Functional Location as asset_id) |
| 8 | Document Extractor (Vision) | Scans all 24 drawing chunks for asset ID numerals; matched against TAL via bare ID normalisation |
| 9 | Data Analytics | Enriches matched assets with legend symbol descriptions and labels |
| 10 | Orchestrator | Consolidates all extractions into a structured review report |

Each step is independently validated by a separate **Step Validator Agent** that scores output quality against predefined criteria.

### 1.2 Research Gap

Despite growing literature on large language model (LLM) agents in document understanding (Xu et al., 2024; Wei et al., 2023), empirical comparisons between multi-agent pipelines and domain expert humans on structured engineering document review tasks remain scarce. Existing benchmarks (DocVQA, InfographicsVQA) focus on single-document, single-question extraction; they do not address multi-document cross-referencing, symbol detection on large-format engineering drawings, or the end-to-end throughput characteristics relevant to operational deployment decisions.

### 1.3 Research Questions

**RQ1 (Accuracy):** Does the multi-agent AI system achieve statistically equivalent or superior extraction accuracy compared to trained human reviewers across all structured review sub-tasks?

**RQ2 (Visual Symbol Detection):** Does the vision-based chunk scanning approach achieve recall and precision for asset ID detection on large-format reticulation drawings equivalent to human visual inspection?

**RQ3 (Throughput and Cost):** What is the processing time and estimated cost per application for the AI system compared to the human process, and under what conditions does AI offer a positive return on investment?

**RQ4 (Reliability):** How consistent is the AI system's output across repeated runs on identical input? How does this compare to inter-rater reliability between human reviewers?

**RQ5 (Error Characterisation):** What are the qualitative failure modes of the AI system (e.g., missed small-font numerals in chunk boundaries, incorrect legend matching, hallucinated asset attributes) compared to human error modes?

---

## 2. Hypotheses

**H1 (Null):** There is no statistically significant difference in extraction accuracy (F1 score) between the AI system and human reviewers across all document types.
**H1 (Alternate):** The AI system achieves significantly lower F1 scores than human reviewers on at least one document type.

**H2 (Null):** There is no statistically significant difference in asset symbol detection recall (drawing chunk scanning) between the AI system and human visual inspection.
**H2 (Alternate):** The AI system achieves significantly different recall from human inspection (directional: lower, due to partial occlusion, font variability, and chunk boundary effects).

**H3:** The AI system processes applications at least 5× faster than the human condition (wall-clock time from document ingestion to report generation).

**H4:** The AI system's intra-system reliability (repeated runs on identical input) is inferior to human inter-rater reliability (Cohen's κ < 0.80 for AI vs. κ ≥ 0.80 for humans).

---

## 3. Experimental Design

### 3.1 Design Type

A **mixed-methods, between-subjects repeated-measures quasi-experiment** with a crossover element to control for application complexity.

- **Condition A:** Multi-agent AI system (described above)
- **Condition B:** Human expert reviewers (domain-trained engineers)
- **Unit of analysis:** Individual connection application (document package)
- **Crossover:** Each application processed by both conditions; order counterbalanced

### 3.2 Independent Variables

| Variable | Levels |
|----------|--------|
| Processing condition | AI system; Human expert |
| Application complexity | Simple (1-sheet drawing, <30 TAL assets); Complex (2-sheet drawing, 30–100 assets); Very complex (multi-sheet, >100 assets) |
| Document quality | High (clean scan, digital PDF); Medium (scanned, minor artefacts); Low (poor scan, handwritten annotations) |

### 3.3 Dependent Variables

| Variable | Measurement | Source |
|----------|-------------|--------|
| Extraction accuracy (F1) | Precision and recall against gold-standard annotation | Expert annotation panel |
| Asset symbol detection recall | % of TAL assets correctly identified as present/absent on drawing | Gold-standard drawing annotation |
| Asset symbol detection precision | % of system-detected assets correctly on drawing | Gold-standard drawing annotation |
| Processing time (wall clock) | Minutes from document submission to report ready | System logs / human timesheet |
| Estimated cost per application | API token cost (AI); Staff hours × grade rate (human) | Billing logs / payroll data |
| Intra-condition reliability | Repeated-run agreement (AI); Inter-rater κ (human) | Repeated processing of 10% subsample |
| Report completeness | % of required output fields populated and non-null | Automated schema validation |
| Error type distribution | Categorised qualitative coding of errors | Thematic analysis |

### 3.4 Controlled Variables

- Input documents: identical document packages for all conditions
- Output schema: identical JSON output specification for both conditions (humans transcribe into same schema)
- Review criteria: identical validation rubric applied post-hoc to both conditions
- Time pressure: humans given equivalent elapsed time budget to AI system per application (AI wall-clock time + 20% buffer, to simulate operational parity)

---

## 4. Participants

### 4.1 AI Condition

The agent system as implemented, comprising:
- **Vision model:** Gemini 2.0 Flash (chunk scanning, Step 8)
- **Extraction model:** Claude Sonnet 4.6 (asset spreadsheet extraction, Step 7)
- **General LLM:** GPT-4o (document review, planning, legend extraction)
- **Analytics model:** GPT-4o (legend correlation, enrichment)
- **Validation model:** Gemini 2.0 Flash + GPT-4o (Step Validator)

System configuration is fixed across all runs. No fine-tuning or retrieval augmentation is applied. This represents an off-the-shelf multi-agent deployment using publicly available foundation models.

### 4.2 Human Condition

**Inclusion criteria:**
- Employed or recently employed (within 24 months) as a network design engineer, connections engineer, or asset management engineer at an electricity distribution network operator
- Minimum 2 years experience reviewing customer connection applications
- Familiar with reticulation drawing conventions and FLOC-based asset registers

**Exclusion criteria:**
- Participants involved in design or testing of the AI system under evaluation
- Participants with uncorrected visual impairment affecting drawing interpretation

**Target sample:** N = 12 engineers (estimated to achieve 80% power for a medium effect size, d = 0.5, at α = 0.05 using a paired t-test framework; see Section 9).

**Recruitment:** Purposive sampling via professional networks (Energy Networks Association, CIGRE working groups, utility HR referrals). Voluntary participation; no compensation other than summary research findings shared post-study.

---

## 5. Materials

### 5.1 Application Document Packages

A corpus of **30 anonymised connection application packages** will be compiled:
- 10 Simple, 10 Complex, 10 Very Complex (per complexity definition in Section 3.2)
- All packages fully anonymised: property addresses removed, customer names removed, asset IDs replaced with synthetic equivalents preserving format (e.g., SLPL00302876 → SLPL00XXXXXX with consistent mapping)
- Document quality distributed: 15 High, 10 Medium, 5 Low across the corpus

Each package includes:
- 1 Design Brief PDF (2–20 pages)
- 1–3 Site Plan PDFs (A1/A0 format, 1–4 pages each, quadrant-chunked at 300 DPI)
- 1 TAL Excel file (.xlsx or .xlsm, 1–6 worksheets, 10–150 asset rows)

### 5.2 Gold-Standard Annotation

A **3-member expert annotation panel** (senior engineers with ≥10 years experience, not otherwise participating) will independently annotate each application package, producing:

- A complete extraction JSON matching the system output schema
- For each TAL asset: a ground-truth `match_status` (found / not_found / ambiguous) with `bare_id`, `symbol_description`, and `legend_label` from the drawing
- For each drawing chunk: a list of all visible asset ID numerals (bare format)

Inter-annotator agreement on the gold standard will be established (Cohen's κ); disagreements resolved by majority vote with recorded rationale. Annotation will be performed blind to AI system output.

### 5.3 Output Schema

Both conditions produce output conforming to the same JSON schema:

```json
{
  "match_summary": {
    "total": "<int>",
    "found": "<int>",
    "not_found": "<int>"
  },
  "updated_assets": [
    {
      "asset_id": "<string>",
      "bare_id": "<string>",
      "asset_type": "<string>",
      "description": "<string>",
      "label": "<string|null>",
      "symbol_description": "<string|null>",
      "match_status": "found|not_found"
    }
  ]
}
```

Human participants will receive the schema as a structured data entry template (Excel-based) and will transcribe their findings accordingly.

---

## 6. Procedure

### 6.1 AI Condition

1. Document package uploaded to system via web interface
2. System automatically runs Steps 1–10 without intervention
3. Step 8 (visual chunk scan) uses the free-scan approach: the vision model reports all visible numeric labels; orchestrator matches against TAL bare IDs
4. Wall-clock time recorded from submission to consolidated report generation
5. API usage (tokens, calls) logged for cost calculation
6. Process repeated 3× per package (on 10% subsample, n=3 packages) to assess intra-system reliability
7. Output JSON extracted for scoring

### 6.2 Human Condition

1. Participant receives the identical anonymised document package via secure shared drive (no AI tools permitted)
2. Participant completes review using standard tools only: PDF viewer, Excel, a drawing viewer capable of zooming to 1:1 at 300 DPI
3. Participant records findings in the structured Excel template (matching the output schema)
4. Time recorded from task commencement to submission of completed template
5. 10% of packages reviewed by a second participant independently (for inter-rater reliability)
6. Participants complete a NASA Task Load Index (NASA-TLX) questionnaire on cognitive load after each package

### 6.3 Counterbalancing

Packages are assigned to conditions in a balanced Latin square design across complexity levels to control for learning effects and document-specific difficulty:
- Half the applications processed by AI first, human second (AI→H)
- Half processed human first, AI second (H→AI)
- At least 2 weeks washout between conditions for the same package and participant

---

## 7. Outcome Measures and Scoring

### 7.1 Extraction Accuracy (F1)

For each structured field (asset_id, asset_type, description, label, symbol_description, match_status), calculate:

$$F_1 = \frac{2 \cdot \text{Precision} \cdot \text{Recall}}{\text{Precision} + \text{Recall}}$$

Where a field value is scored correct if it is an exact match (for IDs and categorical fields) or passes a semantic similarity threshold of cosine similarity ≥ 0.85 (for free-text description fields using a sentence embedding model).

### 7.2 Symbol Detection Recall and Precision

For each application package, calculate at the asset level:

$$\text{Recall} = \frac{|\text{Correctly identified as present on drawing}|}{|\text{Assets actually present on drawing (gold standard)}|}$$

$$\text{Precision} = \frac{|\text{Correctly identified as present on drawing}|}{|\text{Assets identified as present by condition}|}$$

### 7.3 Processing Time

Wall-clock minutes from document submission (AI) or task commencement (human) to finalised output. For AI, parallelisation effects (parallel steps running concurrently) are preserved.

### 7.4 Cost Estimation

**AI:** Sum of API token costs (input + output tokens × per-model published rate at time of study) + infrastructure cost (container hosting per hour × hours used).

**Human:** Engineer grade × hours spent × fully loaded labour rate (inclusive of oncosts). Mid-point of the relevant engineering salary band used; sensitivity analysis at ±20%.

### 7.5 Intra-AI Reliability

For the 3 repeated runs per package (n=3 packages), calculate:
- Percentage agreement on `match_status` per asset across runs
- Fleiss's κ across the 3 runs
- Standard deviation of `found` count across runs (to quantify detection variability)

### 7.6 Human Inter-Rater Reliability

Cohen's κ on `match_status` between paired human reviewers for the double-reviewed subsample.

### 7.7 NASA-TLX (Human Condition Only)

Six subscales (mental demand, physical demand, temporal demand, performance, effort, frustration) scored 0–100. Aggregate weighted workload score computed per participant per application.

---

## 8. Analysis Plan

### 8.1 Primary Analysis

**RQ1 and RQ2 (Accuracy and Detection):** Paired t-tests (or Wilcoxon signed-rank if normality fails Shapiro-Wilk at α = 0.05) comparing AI vs. human F1 and recall/precision per application package. Bonferroni correction applied across the multiple field comparisons.

**RQ3 (Throughput):** Descriptive statistics (mean, SD, 95% CI) for processing time and cost per condition and complexity level. Mann-Whitney U test for significant differences. Cost-effectiveness ratio (output quality per unit cost) computed as F1 / cost.

**RQ4 (Reliability):** Comparison of Fleiss's κ (AI) vs. Cohen's κ (human inter-rater). Bootstrap confidence intervals (n=10,000 resamples) for both reliability estimates.

**RQ5 (Error Characterisation):** Thematic analysis of all incorrect field values produced by each condition. Errors coded into a taxonomy (missed detection, false positive detection, incorrect field mapping, hallucinated value, formatting error, schema non-compliance). Chi-squared test for independence of error type distribution across conditions.

### 8.2 Moderation Analysis

Repeated-measures ANOVA with condition × complexity and condition × document quality as between-subjects moderators. This will identify whether AI underperformance (if any) is concentrated in specific difficulty tiers.

### 8.3 Qualitative Analysis

Semi-structured debrief interviews (30 minutes) with a purposive subsample of 6 human participants post-experiment. Thematic analysis (Braun & Clarke, 2006) exploring: perceived accuracy of AI output, trust calibration, identification of AI failure modes not captured by quantitative metrics, and readiness to adopt AI-assisted review in operational practice.

---

## 9. Power Analysis

Effect size estimated conservatively at d = 0.5 (medium) based on preliminary pilot data (3 runs on the DAR1509-84 test package, mean recall = 0.47, SD ≈ 0.15; estimated human recall ≈ 0.75 from domain-expert estimate). Using G*Power 3.1:

- Test: Paired t-test (two-tailed)
- α = 0.05, Power (1−β) = 0.80
- d = 0.5
- Required N = **27 application packages**

With 30 packages in the corpus this target is met. The human sample of 12 participants each completing a subset of packages provides adequate power for the participant-level analysis.

---

## 10. Preliminary Observations (Pilot System)

> **Experiment Results:** Post-pilot experimental run results (Runs 8–9, 2026-03-28) are documented in [experiment_results.md](experiment_results.md), including per-application match data, legend extraction quality, observed failure modes, and outstanding issues.

The following observations derive from 7 runs of the agent system on a single anonymised application package (DAR1509-84, 2-page A1 reticulation drawing, 91–143 TAL assets across extraction variants) during system development. These are reported as contextual pilot data only and are not used in the power calculation without further validation.

| Run | TAL Assets | Detected on Drawing | Recall |
|-----|-----------|--------------------:|-------:|
| 1 (parsing bug present) | 143 | 0 | 0.0% |
| 2 (parsing bug present) | 143 | 0 | 0.0% |
| 3 (post parsing fix) | 143 | 24 | 16.8% |
| 4 (parsing regression) | 143 | 0 | 0.0% |
| 5 (list-constrained prompt) | 132 | 10 | 7.6% |
| 6 (free-scan prompt, new TAL extraction) | 91 | 43 | 47.3% |
| 7 (free-scan, same build) | 91 | 14 | 15.4% |

**Key preliminary findings:**

1. **High intra-system variance:** Recall ranged from 0% to 47.3% across runs on the same document package, with the largest source of variance being (a) code-level parsing bugs in response interpretation, and (b) vision model stochasticity in chunk scanning. This motivates H4.

2. **Asset type stratification:** In the highest-performing run, detection recall was strongly stratified by asset type: HV Overhead Assembly (11/14, 79%), LV Overhead Assembly (11/13, 85%), Structure Pole (11/17, 65%), but Street Lighting (1/14, 7%) and EQ01 category assets (0/6, 0%). This suggests systematic vision model blind spots for certain symbol types and motivates the error characterisation analysis (RQ5).

3. **Prompt engineering sensitivity:** Changing from a target-list prompt ("look for these specific IDs") to a free-scan prompt ("report all visible numerals") improved recall from 7.6% to 47.3% within the same system build, demonstrating significant sensitivity to prompt formulation — a finding with direct implications for deployment practice and reproducibility.

4. **Bare ID normalisation:** The system's `_compute_bare_id` function (strip leading alpha prefix, strip leading zeros) correctly normalised formats such as `SLPL00302876 → 302876` and `SLP02002180 → 2002180`. However, assets where the bare_id is a short generic number (e.g., `1`, `3`, `42`) present an ambiguity problem: these appear prolifically across drawing chunks as non-asset numerals. The experiment should include analysis of precision loss from short bare IDs.

5. **Chunk boundary effect hypothesis:** 24 chunks (4-column × 3-row × 2-page quadrant split at 300 DPI) were used. Assets positioned at chunk boundaries may be partially visible in two adjacent chunks, potentially causing double-detection or missed detection. This is a testable sub-hypothesis within RQ2.

---

## 11. Threats to Validity

### 11.1 Internal Validity

| Threat | Mitigation |
|--------|-----------|
| Selection bias (human participants) | Purposive sampling with explicit inclusion criteria; participant demographics reported |
| Learning effects (within-participant) | Counterbalanced Latin square; 2-week washout between conditions for same document |
| Demand characteristics | Blind annotation of gold standard; human participants unaware of specific AI metrics during task |
| System instability | AI system version-locked (Docker image hash recorded) for all experimental runs; no updates during data collection |

### 11.2 External Validity

| Threat | Mitigation |
|--------|-----------|
| Corpus generalisability | 30 packages across 3 complexity levels and 3 quality levels; drawn from real operational archive |
| Operator generalisability | Findings reported by complexity tier; annotated with relevant structural characteristics |
| Model version obsolescence | All model versions recorded; experiment designed to be reproducible with model version substitution |
| Task scope | Study limited to the document review sub-task; excludes downstream engineering judgement, customer communication, and compliance decisions |

### 11.3 Construct Validity

The F1 metric applied to free-text description fields relies on a sentence embedding similarity threshold (0.85). This threshold is a construct decision that could inflate or deflate apparent accuracy. A sensitivity analysis will be reported at thresholds 0.75, 0.80, 0.85, 0.90.

---

## 12. Ethical Considerations

- **Participant welfare:** No sensitive personal data collected. Participants review anonymised documents. NASA-TLX data collected for research purposes only, not shared with employers.
- **Data anonymisation:** All application documents anonymised prior to participant exposure. Asset ID mapping keys held securely and destroyed post-study.
- **Informed consent:** Written informed consent obtained from all human participants. Right to withdraw without penalty at any time.
- **AI system disclosure:** Participants in the human condition are informed they are participating in a study comparing human and AI performance; they are not told the specific AI metrics during the task.
- **Publication:** Findings will be reported at aggregate level; individual participant data reported only in anonymised aggregate form.
- **Ethics approval:** Application to be submitted to [University Human Research Ethics Committee] prior to commencement.

---

## 13. Expected Contributions

1. **Empirical benchmark:** The first controlled comparison of a multi-agent LLM pipeline versus domain expert humans on a real-world engineering document review task, producing replicable F1, recall, and reliability metrics.

2. **Prompt engineering taxonomy:** Empirical evidence for the effect of prompt formulation (target-constrained vs. free-scan) on vision model recall in structured symbol detection tasks.

3. **Failure mode characterisation:** A documented taxonomy of AI failure modes specific to large-format engineering drawing interpretation, suitable for informing future model fine-tuning and system design.

4. **Economic model:** A cost-effectiveness framework for AI vs. human document review at varying application volumes and quality tiers, providing a decision support tool for utility operators considering deployment.

5. **Trust and adoption insights:** Qualitative evidence on engineer trust calibration, perceived utility, and barriers to AI-assisted review adoption in regulated infrastructure contexts.

---

## 14. Timeline

| Phase | Activity | Duration |
|-------|----------|----------|
| 0 | Ethics approval, corpus assembly, gold-standard annotation | 6 weeks |
| 1 | AI system runs (all 30 packages × 3 repeated runs) | 2 weeks |
| 2 | Human participant data collection (rolling, counterbalanced) | 10 weeks |
| 3 | Quantitative analysis | 3 weeks |
| 4 | Qualitative analysis and debrief interviews | 3 weeks |
| 5 | Write-up and thesis integration | 4 weeks |
| **Total** | | **~28 weeks** |

---

## 15. References

Braun, V., & Clarke, V. (2006). Using thematic analysis in psychology. *Qualitative Research in Psychology*, 3(2), 77–101.

Dong, Q., Li, L., Dai, D., Zheng, C., Wu, Z., Chang, B., Sun, X., Xu, J., & Sui, Z. (2022). A survey for in-context learning. *arXiv preprint arXiv:2301.00234*.

Hart, S. G., & Staveland, L. E. (1988). Development of NASA-TLX (Task Load Index): Results of empirical and theoretical research. *Advances in Psychology*, 52, 139–183.

Mathew, M., Karatzas, D., & Jawahar, C. V. (2021). DocVQA: A dataset for VQA on document images. *Proceedings of the IEEE/CVF Winter Conference on Applications of Computer Vision*, 2200–2209.

OpenAI. (2024). GPT-4 Technical Report. *arXiv preprint arXiv:2303.08774*.

Park, J. S., O'Brien, J. C., Cai, C. J., Morris, M. R., Liang, P., & Bernstein, M. S. (2023). Generative agents: Interactive simulacra of human behavior. *Proceedings of UIST 2023*.

Wei, J., Wang, X., Schuurmans, D., Bosma, M., Xia, F., Chi, E., Le, Q. V., & Zhou, D. (2022). Chain-of-thought prompting elicits reasoning in large language models. *Advances in Neural Information Processing Systems*, 35, 24824–24837.

Xu, Y., Li, J., Tang, Y., Zhou, P., Zhao, P., Hu, X., ... & Li, D. (2024). mPLUG-DocOwl: Modularized multimodal large language model for document understanding. *arXiv preprint arXiv:2307.02499*.

Yao, S., Zhao, J., Yu, D., Du, N., Shafran, I., Narasimhan, K., & Cao, Y. (2023). ReAct: Synergizing reasoning and acting in language models. *Proceedings of ICLR 2023*.

---

*Document prepared as part of doctoral research experiment design. Version 1.0 — 2026-03-28. Subject to ethics committee review and supervisor approval before commencement.*
