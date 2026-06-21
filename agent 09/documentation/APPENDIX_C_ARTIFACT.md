# Appendix C — Artifact Description

## C.1 Repository Access and Citation

**Repository URL:** `https://github.com/[org]/agent-experiments`
**Tag for paper results:** `v1.0-paper-submission`

To cite an immutable version of this artifact, push the tagged release to Zenodo or FigShare before submission. Live GitHub links alone do not satisfy ACM "Artifacts Available" badge requirements.

**Archival DOI:** `https://doi.org/10.5281/zenodo.[XXXXXX]` *(assign after upload)*

**Licence:**
- Code: MIT
- Documentation: CC-BY-4.0

**BibTeX citation for this artifact** (separate from the paper):

```bibtex
@software{draper2026agentexperiments,
  author    = {Draper, John},
  title     = {{Agent Experiments: A Multi-Agent Document Intelligence Pipeline for Customer Connections Review}},
  year      = {2026},
  publisher = {Zenodo},
  version   = {v1.0-paper-submission},
  doi       = {10.5281/zenodo.[XXXXXX]},
  url       = {https://github.com/[org]/agent-experiments}
}
```

---

## C.2 Repository Structure

```
agent 09/
├── AGENT_TEMPLATE.py               # Agent wrapper module (§3 "Agent Template core component") —
│                                   #   copy-and-implement base for all specialist agents
├── orchestrator.py                 # 10-step pipeline orchestration engine; manages step loop,
│                                   #   parallel groups, repeat_runs variance protocol, and retry
├── document_extractor.py           # Cognitive extraction agent (Steps 5–9); contains:
│                                   #   multi-phase legend extraction, Quadrant Retry logic,
│                                   #   OCR cache, _SYMBOL_REF canonicalisation table,
│                                   #   _TAL_DESCRIPTION_SOURCE_CODES priority list,
│                                   #   _METADATA_SHEET_NAMES blocklist
├── document_reviewer.py            # Document discovery and classification (Steps 1–2)
├── document_chunker.py             # Large-format PDF → PNG tile renderer for vision pipeline
├── step_validator_agent.py         # Non-LLM JSON schema validator; QA gate after each step
├── variance_validator.py           # Variance analysis service (PASS/WARN/FAIL verdict);
│                                   #   produces kappa and PABAK source figures
├── data_analytics_agent.py         # Asset enrichment and CCA report generation
├── responder.py                    # Task intake and plan decomposition entry point
├── run_phase1_test.py              # Evaluation harness — launches the 10-run repeated protocol
├── run_phase2_variance_test.py     # Variance analysis runner — submits runs and collects reports
├── run_step11.py                   # Final consolidation step runner
│
├── common/
│   ├── agent.py                    # FastAPI app factory (create_app), CORS, /health endpoint
│   ├── llm.py                      # Multi-provider LLM client (OpenAI / Anthropic / Gemini)
│   │                               #   with retry, fallback chain, and JSON-mode handling
│   ├── config.py                   # Centralised environment-variable configuration
│   ├── knowledge.py                # KnowledgeRegistry — process JSON loader and router
│   ├── models.py                   # Shared Pydantic models (FilesManifest, AgentError, …)
│   └── output.py                   # OutputManager — timestamped JSON file writer
│
├── process/
│   ├── process_extract_site_plan_information.json    # Extraction config for site-plan step
│   ├── process_extract_design_brief_information.json # Extraction config for design brief step
│   ├── process_extract_asset_spreadsheet.json        # Extraction config for TAL spreadsheet step
│   └── proc-customer-connections-review.json         # End-to-end CCA review process definition
│
├── tools/
│   ├── tool-list-folder-files/     # Lists files in a documents folder
│   └── tool-extract-pdf-content/   # Deep PDF section/table/image extractor
│
├── frontend/                       # Nginx-served UI (task submission, step monitoring, reports)
├── documents/                      # Input document store (real datasets excluded — see C.5)
├── registry.json                   # Agent and tool registry (IDs, endpoints, schemas)
├── requirements.txt                # Core Python dependencies
├── requirements_*.txt              # Per-service pinned dependency files
├── Dockerfile.*                    # Per-service Docker build files
└── documentation/                  # Architecture, experiment notes, variance analysis reports
```

---

## C.3 System Requirements and Dependencies

### Hardware

The system is API-bound; no GPU is required. Any modern laptop or cloud VM with a stable internet connection is sufficient. RAM: 4 GB minimum (8 GB recommended for parallel step execution).

### Operating System

Linux, macOS, and Windows 11 (via WSL2 or native Docker Desktop). Docker Compose is required.

### Python Version

Python 3.9 or later. The production environment uses Python 3.9 (as reflected in `__pycache__` artefacts).

### Key library versions (see `requirements.txt` and `requirements_*.txt`)

| Package | Purpose |
|---|---|
| `fastapi` | HTTP service framework for all agents |
| `uvicorn[standard]` | ASGI server |
| `pdfplumber` | Text PDF extraction |
| `Pillow` | Image processing for vision pipeline |
| `openpyxl` | TAL `.xlsm` / `.xlsx` spreadsheet reading |
| `anthropic` | Claude API client |
| `google-genai` | Gemini API client |
| `openai` | OpenAI API client |
| `pydantic` | Request/response schema validation |
| `python-dotenv` | `.env` file loading |
| `requests` | Inter-service HTTP calls |
| `pandas` | Tabular data handling |

### API access — load-bearing model versions

The following specific model versions are required for exact replication of the paper's results. Substituting a different model or version will alter outputs:

| Role | Model ID |
|---|---|
| Vision / legend extraction | `gemini-2.0-flash` |
| Asset extraction (Phase 2) | `claude-sonnet-4-6` |
| Section tagging | `gpt-4o-mini` |
| Structured extraction | `gpt-4o` |

### Estimated API cost

| Scope | Approx. cost (USD) |
|---|---|
| Single 10-run protocol, one dataset (~3 documents) | $0.50 – $2.00 |
| Full 50-run validation across all datasets | $5 – $20 |

Costs are dominated by Gemini vision calls (legend quadrant retry) and Claude Sonnet calls (Phase 2 asset extraction). The Gemini free tier (15 RPM) is usable for small runs; the throttle in `document_extractor.py` (`_GEMINI_MIN_GAP = 4.0 s`) enforces this limit automatically.

---

## C.4 Installation and Setup

```bash
# 1. Clone the repository at the paper tag
git clone https://github.com/[org]/agent-experiments.git
cd "agent-experiments/agent 09"
git checkout v1.0-paper-submission

# 2. Configure API keys
cp .env.example .env
# Edit .env and set:
#   OPENAI_API_KEY=sk-...
#   ANTHROPIC_API_KEY=sk-ant-...
#   GOOGLE_API_KEY=AIza...

# 3. Build and start all services
docker compose up --build -d

# 4. Smoke test — confirm all services are healthy
curl http://localhost:8001/health   # Orchestrator
curl http://localhost:8090/health   # Document Extractor
curl http://localhost:8088/health   # Step Validator
curl http://localhost:8093/health   # Variance Validator
```

**Minimal "Hello World" invocation** (confirms the environment is working before any expensive pipeline run):

```bash
# POST a single-step document review to the orchestrator
curl -s -X POST http://localhost:8001/execute \
  -H "Content-Type: application/json" \
  -d '{
    "plan_overview": "Smoke test: review documents folder",
    "steps": [{
      "step_number": 1,
      "name": "DocumentReview",
      "description": "List and classify documents in the test folder",
      "assigned_resource_id": "agent-document-reviewer"
    }]
  }' | python -m json.tool
```

Expected: a JSON response with `plan_id` and `status: "running"` within 5 seconds. If you see `status: "failed"`, check the Docker logs (`docker compose logs document-reviewer`).

---

## C.5 Data Availability

The datasets used in the paper fall into three tiers:

### Tier 1 — Included in repository

- `documents/Test Data/Project_Design_Brief_Synthesised.pdf` — synthetic Design Brief exercising the full extraction pipeline
- `documents/Test Data/Sample TAL Sheet.xlsx` — synthetic TAL spreadsheet with representative structure
- Gold-standard reference JSONs used by the evaluation harness (see `OUTPUT/` artefacts in the tagged release)
- Variance analysis outputs and consistency reports (see `documentation/experiment_consistency_results.md`)

### Tier 2 — Available on request

The real DNSP (Distribution Network Service Provider) project datasets used in Section 5 may be made available under a data-use agreement for credentialled reviewers. Contact the corresponding author. These datasets contain commercially sensitive engineering information and cannot be posted publicly.

### Tier 3 — Not available

Source documents provided directly by ACME Energy under their standard project workflow are not available for redistribution. This is a contractual constraint, not a technical one.

**Synthetic stand-in for full pipeline reproduction:** Reviewers who do not have access to Tier 2/3 data can run the complete pipeline end-to-end on the Tier 1 synthetic dataset. All code paths — including multi-phase legend extraction, Quadrant Retry, OCR cache, `_SYMBOL_REF` canonicalisation, and the variance protocol — are exercised by the synthetic data. Numerical results will differ from Section 5 figures, but the pipeline behaviour is identical.

---

## C.6 Running the Pipeline

### Mode 1 — End-to-end (single dataset, all 10 steps)

```bash
# Submit the standard CCA review plan against the synthetic dataset
curl -s -X POST http://localhost:8001/execute \
  -H "Content-Type: application/json" \
  -d @process/proc-customer-connections-review.json | python -m json.tool
```

- **Expected runtime:** 3–8 minutes for the synthetic dataset (dominated by Gemini vision calls and Claude extraction)
- **Outputs:** `OUTPUT/ExtractionReport_*.json`, `OUTPUT/VarianceReport_*.json`, `OUTPUT/CCAReport_*.json`

### Mode 2 — Per-step invocation (Steps 5–9 — the cognitive nodes)

Each cognitive step can be invoked independently by posting directly to its agent endpoint. Example for Step 5 (site plan extraction):

```bash
curl -s -X POST http://localhost:8090/extract \
  -H "Content-Type: application/json" \
  -d '{
    "files": [{
      "filename": "DS1_DAR1988_RETIC.pdf",
      "filepath": "/documents/DAR1509-84/DS1_DAR1988_RETIC.pdf",
      "processing_tool_id": "tool-extract-pdf-content",
      "content_type": "image",
      "text_quality": "low"
    }],
    "process_step": {
      "step_name": "ExtractSitePlanInformation",
      "summary": "Extract legend symbols and asset locations from retic site plan drawing"
    }
  }' | python -m json.tool
```

- **Expected runtime:** 1–4 minutes per document (vision pipeline rate-limited to 4 s/call)
- **Output:** `OUTPUT/ExtractionReport_<timestamp>.json`

### Mode 3 — Repeated consistency protocol (produces Section 5 results)

```bash
# Phase 1: run the target step N times with identical input
python run_phase1_test.py
# Prompts: "Enter number of runs (default=2):" — enter 10 for the paper protocol

# Phase 2: collect all run outputs and compute variance metrics
python run_phase2_variance_test.py
```

- **Expected runtime:** ~45 minutes for a 10-run protocol on one dataset (wall-clock; Gemini throttle is the bottleneck)
- **Output:** `OUTPUT/VarianceReport_<timestamp>.json` containing `consistency_score`, `verdict` (PASS/WARN/FAIL), and the per-field breakdown used to compute Cohen's kappa and PABAK in Section 5
- **Variance analysis scripts** that produce the kappa/PABAK figures are in `run_phase2_variance_test.py` and `variance_validator.py`; the rendered reports are in `documentation/experiment_consistency_results.md`
