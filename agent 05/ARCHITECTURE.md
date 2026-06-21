# Agent 05 — Reference Architecture

## Overview

Agent 05 is a **document intelligence pipeline** for processing Customer Connections Applications (CCAs) in an electricity distribution engineering context (domain: ACME Energy project reviews). The system ingests a folder of project documents (PDFs, drawings, spreadsheets), classifies and extracts structured engineering and funding information from each, validates every step's output against quality criteria, and produces a consolidated review report.

The system follows a **multi-agent, microservices architecture** deployed via Docker Compose, where each agent is an independent FastAPI service communicating over HTTP.

---

## System Components

### Service Map

| Service | Port | File | Role |
|---|---|---|---|
| **Responder** | 8000 | `responder.py` | Task intake, plan decomposition, orchestrator handoff |
| **Orchestrator** | 8001 | `orchestrator.py` | Step execution engine, state management, validation coordination |
| **Document Reviewer** | 8089 | `document_reviewer.py` | Document discovery, classification, processing planning |
| **Document Extractor** | 8090 | `document_extractor.py` | Structured content extraction (text, vision, spreadsheet) |
| **Document Chunker** | 8091 | `document_chunker.py` | Large-format PDF rendering into PNG tiles |
| **Step Validator** | 8088 | `step_validator_agent.py` | Independent QA validation of each step's output |
| **Frontend** | 8080 | `frontend/` | Nginx-served UI for task submission and result review |

---

## Architecture Diagram

```
User (Browser)
      │
      ▼
┌─────────────┐
│  Frontend   │  (Nginx, port 8080)
│  index.html │  Process selection, folder upload, step monitoring
└──────┬──────┘
       │ POST /decompose
       ▼
┌─────────────┐       tasks.json         ┌──────────────┐
│  Responder  │──── Task Library ────────│ Task Library │
│  (port 8000)│     (predefined          │  templates + │
│             │      templates)          │  digital      │
│   AI Plan   │     AI fallback          │  workers      │
│ Decomposer  │──── OpenAI GPT ─────────└──────────────┘
└──────┬──────┘
       │ POST /execute
       ▼
┌───────────────────────────────────────────────────────┐
│                    Orchestrator                        │
│                    (port 8001)                         │
│                                                        │
│  Execution State: executions{}                         │
│  Step Loop: sequential + parallel_group support        │
│                                                        │
│  For each step:                                        │
│   1. Resolve agent from registry.json                  │
│   2. Build input from prior step results               │
│   3. Call agent endpoint                               │
│   4. POST to Step Validator                            │
│   5. Store result in state                             │
└──┬────────┬──────────┬──────────────────┬─────────────┘
   │        │          │                  │
   ▼        ▼          ▼                  ▼
┌──────┐ ┌──────┐ ┌──────────┐    ┌─────────────┐
│ Doc  │ │ Doc  │ │   Doc    │    │    Step     │
│Review│ │Extrac│ │ Chunker  │    │  Validator  │
│:8089 │ │:8090 │ │  :8091   │    │    :8088    │
└──────┘ └──────┘ └──────────┘    └─────────────┘
```

---

## Execution Flow (Primary Workflow: Review Customer Connections Application)

The primary 7-step workflow processes a CCA document folder:

```
Step 1: Identify & Review All Application Documents
         └─ Agent: Document Reviewer (/review)
         └─ Tool: tool-list-folder-files
         └─ Output: DocumentReview_{timestamp}.json

Step 2: Plan Document Processing Approach
         └─ Agent: Document Reviewer (/plan_processing)
         └─ Tool: tool-plan-document-processing
         └─ Output: ProcessingPlan_{timestamp}.json
         └─ Key outputs per document: document_category, page_size,
            requires_chunking, chunk_strategy, route_to_step

Step 3: Chunk Documents for Processing
         └─ Agent: Document Chunker (/chunk)
         └─ Only for documents with requires_chunking=true AND route_to_step≠6
         └─ Output: PNG tile manifests per document

        ┌──────────────────────┬──────────────────────┐
        ▼                      ▼                      ▼
Step 4: Extract Design Brief   Step 5: Extract Site   Step 6: Extract Asset
        Information                    Plan Information        Spreadsheet Data
        └─ route_to_step=4             └─ route_to_step=5     └─ route_to_step=6
        └─ Agent: Extractor            └─ Agent: Extractor    └─ Agent: Extractor
        └─ Process: proc-extract-      └─ Process: proc-       └─ Process: proc-
           design-brief-info              extract-site-plan-      extract-asset-
                                          info                    spreadsheet

Step 7: Consolidate Application Review Report
         └─ Agent: Report Consolidator
         └─ Merges Steps 4, 5, 6 into unified ConsolidatedReport_{timestamp}.json
```

**Steps 4, 5, and 6 use `parallel_group`** — the orchestrator can run them concurrently since they operate on disjoint document sets.

---

## Agent Descriptions

### Responder (`responder.py`)
**Purpose:** Single entry point for user requests. Converts natural language task descriptions into a structured execution plan and hands off to the Orchestrator.

**Design decisions:**
- Checks `tasks.json` for a predefined template match first (fast path, zero AI cost)
- Falls back to OpenAI GPT for dynamic plan generation only if no template matches
- Returns immediately after initialising the plan with the Orchestrator; execution is driven by the Orchestrator/UI
- Loads `AgentRegistry` from `registry.json` to inject available agents into the AI system prompt

---

### Orchestrator (`orchestrator.py`)
**Purpose:** Step execution engine. Iterates the plan steps in order, calls the assigned agent for each step, invokes the Step Validator, manages retry logic, and maintains all execution state in memory.

**Key design decisions:**
- **In-memory state** (`executions{}` dict): each execution is a UUID-keyed record holding the plan, all step results, and status. State is not persisted between restarts.
- **Data threading**: each step's output is stored as `results[step_number]` and injected as context into subsequent steps' input payloads. This allows downstream agents to receive upstream file paths without re-specifying them.
- **Parallel execution**: steps sharing the same `parallel_group` value are submitted concurrently via `ThreadPoolExecutor`.
- **Validation gate**: after each step, the orchestrator calls the Step Validator. If validation fails and `critical_fail=True`, the execution halts. Non-critical failures log a warning and continue.
- **Retry logic**: steps with `retry_on_failure=true` in their validation spec are re-run up to `max_retries` times before being marked failed.
- **Step input construction**: the orchestrator reads the step's `required_resources` and assembles the agent payload, injecting prior step output files (e.g., `step1_output_file`, `step2_output_file`) automatically based on step number conventions.

---

### Document Reviewer (`document_reviewer.py`)
**Purpose:** Scans a document folder and classifies each file. Operates in two modes:

**Mode 1 — `/review`** (Step 1): Discovers all files, assesses each against a `search_context` string using a 4-tool pipeline:
1. `list_folder_files` — enumerate the folder
2. `check_filename_match` — LLM assessment of filename relevance
3. `read_file_content` + `assess_content_quality` — extract text and assess quality
4. `analyse_content_match` — LLM assessment of content relevance
5. `extract_document_metadata` — for matched files, extract title, type, author, date, project number

Confidence scoring: filename (30%) + content (70%) weighted average.

**Mode 2 — `/plan_processing`** (Step 2): Reads Step 1 output and produces a processing plan per document. For each document:
- Detects PDF page dimensions (`_detect_page_size`) to determine if chunking is needed
- Inspects spreadsheet structure (`_inspect_spreadsheet`)
- Uses LLM to assign `processing_tool_id`, `document_category`, chunking parameters
- Enforces hard-coded rules in code (not LLM): spreadsheet extensions always → `document_category=TAL`
- Assigns `route_to_step` (4=Design Brief, 5=Site Plan, 6=TAL) to control downstream routing

**Design decisions:**
- Categorisation rules are loaded from process knowledge files and passed as a prompt section to the LLM; code-level rules override LLM output for deterministic cases (e.g., TAL spreadsheets)
- The LLM retry wrapper (`_llm_call`) retries 3 times with backoff before raising
- Text extraction is capped at 4000 chars for classification (sufficient for type detection without high cost)

---

### Document Extractor (`document_extractor.py`)
**Purpose:** Deep content extraction from classified documents. Operates via `/extract` endpoint.

**Three extraction pathways:**

| Document Type | Extraction Method | LLM Used |
|---|---|---|
| Text PDF (high quality) | `pdfplumber` → section splitting → LLM tagging → structured extraction | GPT-4o-mini (tagging) + GPT-4o (extraction) |
| Image/scanned PDF | PDF → PNG chunks (via Chunker) → Gemini vision analysis per chunk | `gemini-2.0-flash` |
| Spreadsheet (TAL) | `openpyxl`/`xlrd` direct read → LLM structured extraction | `claude-sonnet-4-6` |

**Design decisions:**
- **Multi-model strategy**: different models are selected per task type based on cost/capability tradeoffs. Gemini is used for vision (PDF images); Claude is used for asset spreadsheet extraction; GPT-4o-mini is used for high-volume section tagging.
- **Section tagging**: each document section receives a `relevance_score` (0.0–1.0) and `content_tags` (e.g., `hv`, `lv`, `funding`) before structured extraction, so only relevant sections are passed to the extraction LLM — reducing tokens and improving accuracy.
- **Process knowledge injection**: the `process_step` definition (from `process/*.json` files) is passed to the extractor, which uses it to define what categories to extract and how to structure output.
- **Chunked image processing**: for large-format drawings (A2, A1, A0), the extractor uses chunk manifests from the Chunker to feed PNG tiles to the vision model in sequence, then merges results.
- **Thread-safe parallel extraction**: multiple files are processed concurrently via `ThreadPoolExecutor` with a per-file lock to prevent write conflicts on shared output state.

---

### Document Chunker (`document_chunker.py`)
**Purpose:** Renders PDF pages to PNG images and splits them into spatial grid tiles for vision model processing.

**Grid strategies:**

| Page Size | Strategy | Grid | Chunks/Page |
|---|---|---|---|
| A4, A3 | No chunking | — | 1 |
| A2 | quadrant-split | 2×2 | 4 |
| A1 | quadrant-split | 3×2 | 6 |
| A0 | quadrant-split | 3×3 | 9 |
| large-format | quadrant-split | 4×3 | 12 |

**Design decisions:**
- Edge tiles absorb remainder pixels (no clipping) ensuring full page coverage
- Returns a structured manifest JSON with chunk metadata (sequence, page, region, dimensions, filepath)
- Job-ID-based output directories allow idempotent retrieval via `/chunks/{job_id}/manifest`
- DPI is configurable (default 150; 200–300 recommended for OCR-quality output)

---

### Step Validator (`step_validator_agent.py`)
**Purpose:** Independent QA agent that generates and executes structured test cases against any step's input/output. Produces a formal test report with pass/fail verdicts.

**Design decisions:**
- **Completely stateless and generic**: receives step name, description, input data, output data, and optionally file contents — works for any step type
- **Full file ingestion**: input and output files are read and included in the prompt as evidence, enabling cross-reference validation (not just schema checks)
- **Domain-specific test directives**: the system prompt includes mandatory extraction-specific test cases (TC-HV, TC-LV, TC-FUNDING, TC-NO-HALLUCINATION, etc.) that activate when the step involves document extraction
- **Dual-model fallback**: tries GPT-4o first, falls back to Gemini 2.0 Flash if OpenAI fails
- **Structured test report**: outputs a JSON report with individual test cases (category, input examined, expected, actual, execution notes, result) plus an overall PASS/FAIL verdict and score percentage
- **Backward-compatible response**: returns both the full test report and a simplified `validation` response for the orchestrator's validation gate

---

## Process Knowledge System

Process definitions in `process/*.json` encode domain knowledge about what to extract and how to categorise documents:

| File | Process ID | Domain |
|---|---|---|
| `process_extract_design_brief_information.json` | `proc-extract-design-brief-info` | Engineering supply requirements, funding determination |
| `process_extract_site_plan_information.json` | `proc-extract-site-plan-info` | Substation assets, HV/LV switchgear, earthing |
| `process_extract_asset_spreadsheet.json` | `proc-extract-asset-spreadsheet` | TAL asset register extraction |

Each process file defines:
- `search_context`: what documents to target and how to identify them
- `document_categorisation`: positive/negative indicators for classification rules
- `step_definitions`: what to extract at each sub-step with expected output schema
- `worker_id`: which digital worker persona executes this process

This separation of domain knowledge from agent logic allows new processes to be added by creating a new JSON file without modifying agent code.

---

## Registry (`registry.json`)

The registry is the system's service catalogue. It defines:
- **Agents**: capabilities, endpoints, response time, cost tier
- **Tools**: internal functions and HTTP endpoints with input/output schemas

The registry serves two purposes:
1. **Responder**: injects agent names into the AI plan decomposition prompt so the LLM can assign `assigned_resource_id` values
2. **Document Reviewer (Step 2)**: passes tool descriptions to the LLM planner so it can select the appropriate `processing_tool_id` for each document

---

## Task Library (`tasks.json`)

Defines two entity types:

**Digital Workers** (`digital_workers[]`): Personas with assigned jobs and tasks. Currently: `dw-junior-engineer` handling routine engineering review tasks.

**Templates** (`templates[]`): Pre-composed multi-step execution plans with:
- Trigger phrases for natural language matching
- Step definitions with `required_resources`, validation criteria, and retry policy
- `parallel_group` labels for concurrent step execution

Templates are matched against user input by the Responder before attempting AI decomposition — providing fast, deterministic execution for known workflows.

---

## Key Design Patterns

### 1. Separation of Execution and Validation
Every agent step is immediately validated by an independent Step Validator service. Agents produce outputs; they do not self-assess quality. The validator can halt execution on critical failures or log warnings and continue.

### 2. Content-Aware Routing
Documents are not all treated identically. Step 2 explicitly assigns a `route_to_step` value to each document, so Steps 4, 5, and 6 each filter their input to only their document category. This prevents a site plan drawing from being processed by the design brief extractor and vice versa.

### 3. Chunking as a First-Class Concern
Large-format engineering drawings (A1, A0) cannot be processed by LLMs as single images. The architecture explicitly measures page dimensions in Step 2, flags documents for chunking, executes the chunking step before extraction, and passes chunk manifests to the extractor — treating image tiling as a deliberate pipeline stage rather than an ad-hoc workaround.

### 4. Multi-Model Composition
No single LLM is used for all tasks. The system selects models based on modality and cost:
- GPT-4o-mini: high-volume section tagging (cost-sensitive, text-only)
- GPT-4o: structured extraction and validation (quality-sensitive)
- Gemini 2.0 Flash: vision processing of PDF image chunks (multimodal, cost-effective)
- Claude Sonnet 4.6: asset spreadsheet extraction (strong structured data reasoning)

### 5. File-Based State Threading
Each agent writes output to a timestamped JSON file and returns its path. The Orchestrator stores this path in step state and injects it into the next step's input. This decouples agents: each agent reads a prior agent's output file directly rather than receiving all data through the orchestrator's memory.

### 6. Hard Rules Override LLM Classification
Where classification must be deterministic (e.g., all `.xlsx`/`.xlsm` files are TAL regardless of content), the code enforces this after the LLM call. LLM output is used for ambiguous cases; code rules handle clear-cut cases. This prevents hallucinated misclassification from breaking routing logic.

---

## Common Agent Framework (`common/`)

The `common/` package provides shared building blocks extracted from the recurring patterns across all agents. New agents should use this framework instead of reimplementing these concerns.

| Module | Purpose | Key export |
|---|---|---|
| [common/agent.py](common/agent.py) | FastAPI app factory, CORS, health endpoint, request logging | `create_app(title, model)` |
| [common/llm.py](common/llm.py) | Unified multi-provider LLM client with retry and fallback | `LLMClient` |
| [common/output.py](common/output.py) | Timestamped JSON file writing, `FilesManifest` accumulation | `OutputManager` |
| [common/models.py](common/models.py) | Shared Pydantic models for plans, validation, file provenance | `FilesManifest`, `PlanStep`, `ValidationReport` |
| [common/config.py](common/config.py) | Centralised config from environment variables | `settings` |

**Minimal new agent using the framework:**
```python
from common.agent import create_app
from common.llm import LLMClient
from common.output import OutputManager
from common.config import settings

app = create_app(title="My Specialist Agent", model=settings.llm_model)
llm = LLMClient()

@app.post("/process")
def process(request: MyRequest):
    out = OutputManager(job_dir=request.output_dir)
    result = llm.json(prompt=build_prompt(request), model=settings.llm_model)
    path = out.write(result, prefix="MyOutput", role="agent_output")
    return {**result, "output_file": str(path), "files": out.manifest().to_dict()}
```

See [AGENT_TEMPLATE.py](AGENT_TEMPLATE.py) for a full annotated starting point.

For scalability improvements and proposed architectural changes, see [SCALABILITY.md](SCALABILITY.md).

---

## Technology Stack

| Concern | Technology |
|---|---|
| API framework | FastAPI (all services) |
| Container orchestration | Docker Compose |
| Text PDF extraction | pdfplumber |
| PDF rendering | pdf2image (poppler) |
| Spreadsheet reading | openpyxl, xlrd |
| Vision processing | Gemini 2.0 Flash (via google-generativeai) |
| Text LLM | OpenAI GPT-4o / GPT-4o-mini |
| Structured extraction | Claude Sonnet 4.6 (via anthropic SDK) |
| Frontend | Nginx + vanilla HTML/JS |
| Shared storage | Docker named volume (`chunk_output`) |

---

## Output Artefacts

Each workflow execution produces a structured set of JSON files in `/app/OUTPUT/{job_folder}/`:

| File | Produced By | Contents |
|---|---|---|
| `DocumentReview_{ts}.json` | Document Reviewer | File inventory, classification, metadata |
| `ProcessingPlan_{ts}.json` | Document Reviewer | Per-document routing, tool selection, chunking plan |
| `Extraction_{filename}_{ts}.json` | Document Extractor | Per-file section extractions with tags and relevance scores |
| `ExtractionReport_{ts}.json` | Document Extractor | Consolidated extraction across all files for a step |
| `AssetExtract_{ts}.json` | Document Extractor | Flat asset record array from all TAL spreadsheets |
| `ConsolidatedReport_{ts}.json` | Report Consolidator | Unified CCA review report |
| `TestReport_{step}_{ts}.json` | Step Validator | Structured test case results per step |
