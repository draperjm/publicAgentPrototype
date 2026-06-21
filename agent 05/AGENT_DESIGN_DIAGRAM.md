# Standard Agent Design — Diagrams

---

## 1. Standard Agent Internal Structure

Every specialist agent is assembled from the same five building blocks. Domain logic is the only part that varies between agents.

```mermaid
flowchart TB
    subgraph AGENT["Specialist Agent  (e.g. document_reviewer.py)"]
        direction TB

        subgraph FRAMEWORK["  common/  —  Framework Layer  "]
            direction LR
            APP["<b>agent.py</b><br/>create_app()<br/>─────────────<br/>• CORS middleware<br/>• Request logger<br/>• /health endpoint"]
            CFG["<b>config.py</b><br/>settings<br/>─────────────<br/>• Model names<br/>• Service URLs<br/>• Storage paths"]
            LLM["<b>llm.py</b><br/>LLMClient<br/>─────────────<br/>• OpenAI / Anthropic<br/>• Gemini (vision)<br/>• Retry + fallback<br/>• JSON mode"]
            OUT["<b>output.py</b><br/>OutputManager<br/>─────────────<br/>• Timestamped writes<br/>• FilesManifest<br/>• read_json()"]
            KNO["<b>knowledge.py</b><br/>KnowledgeRegistry<br/>─────────────<br/>• route_for_category()<br/>• model_for_process()<br/>• tagging_rules_prompt()"]
        end

        subgraph LOGIC["  Agent Logic  —  Domain Layer  "]
            direction TB
            REQ["<b>Request Model</b><br/>(Pydantic BaseModel)<br/>input fields + output_dir"]
            EP["<b>@app.post('/endpoint')</b><br/>Orchestrates the call sequence"]
            FN["<b>Core Function(s)</b><br/>Domain-specific logic<br/>(pure Python, testable)"]
            RES["<b>Standard Response</b><br/>{...payload...<br/> output_file: str<br/> files: FilesManifest}"]
        end

        APP -->|"app instance"| EP
        CFG -->|"model names<br/>& URLs"| FN
        LLM -->|"LLM calls"| FN
        OUT -->|"write artefacts<br/>& track files"| FN
        KNO -->|"routing rules<br/>& process config"| FN

        REQ -->|"validated input"| EP
        EP  -->|"calls"| FN
        FN  -->|"returns"| RES
    end

    subgraph EXTERNAL["  External Inputs  "]
        direction TB
        PROC["process/*.json<br/>(extraction categories,<br/>routing, validation directives,<br/>conditional rules)"]
        PRIOR["Prior step output files<br/>(injected by Orchestrator<br/>into request payload)"]
        ENV["Environment variables<br/>(.env / Docker Compose)"]
    end

    PROC  -->|"loaded by"| KNO
    ENV   -->|"loaded by"| CFG
    PRIOR -->|"via output_dir<br/>or file path fields"| REQ
```

---

## 2. Request Lifecycle Through a Standard Agent

```mermaid
sequenceDiagram
    actor Orch as Orchestrator
    participant EP as Endpoint<br/>FastAPI
    participant FN as Core Logic
    participant KNO as KnowledgeRegistry
    participant LLM as LLMClient
    participant OUT as OutputManager
    participant FS as Filesystem / Storage

    Orch->>EP: POST /endpoint {payload, output_dir}
    Note over EP: Pydantic validates request

    EP->>FN: call core function(request)

    FN->>KNO: route_for_category(cat)<br/>model_for_process(proc_id)<br/>tagging_rules_prompt_block()
    KNO-->>FN: routing + config from process JSON

    loop For each document / item
        FN->>LLM: json(prompt, model)
        Note over LLM: Select provider by model name<br/>Retry with backoff on failure<br/>Fallback to secondary model
        LLM-->>FN: parsed JSON result
    end

    FN->>OUT: write(result, prefix, role)
    OUT->>FS: save {prefix}_{timestamp}.json
    FS-->>OUT: file path
    OUT-->>FN: Path object

    FN->>OUT: manifest()
    OUT-->>FN: FilesManifest {files_read, files_output}

    FN-->>EP: {payload, output_file, files}
    EP-->>Orch: HTTP 200 {payload, output_file, files}

    Note over Orch: Stores output_file in execution state<br/>Passes path to Step Validator<br/>Injects into next step's input
```

---

## 3. Process-Driven Configuration Flow

Showing how a single process JSON file drives behaviour across three different agents — eliminating hardcoded domain logic from each.

```mermaid
flowchart LR
    subgraph PROCJSON["process/process_extract_design_brief_information.json"]
        direction TB
        F1["route_to_step: 4"]
        F2["preferred_model: null"]
        F3["force_category_for_extensions: null"]
        F4["extraction_categories:<br/>  hv  (threshold 0.4, keywords: HV 11kV...)<br/>  lv  (threshold 0.4, keywords: LV 415V...)<br/>  funding  (threshold 0.25, behavior: low)<br/>  easement (threshold 0.25, behavior: checklist)"]
        F5["validation_directives:<br/>  TC-HV  TC-LV  TC-SL  TC-FUNDING"]
        F6["conditional_rules: []"]
        F7["document_categorisation:<br/>  positive / negative patterns"]
        F8["search_context: (text)"]
    end

    subgraph REG["KnowledgeRegistry"]
        KR["Loads all process/*.json<br/>on startup<br/>Provides typed accessors"]
    end

    PROCJSON -->|"read once"| REG

    subgraph DR["Document Reviewer"]
        DR1["category_for_extension()<br/>→ forced TAL for .xlsx"]
        DR2["route_for_category()<br/>→ route_to_step per category"]
        DR3["categorisation_rules_string()<br/>→ LLM planning prompt"]
    end

    subgraph DE["Document Extractor"]
        DE1["model_for_process()<br/>→ preferred_model override"]
        DE2["tagging_rules_prompt_block()<br/>→ keyword rules injected into prompt"]
        DE3["low_threshold_categories()<br/>checklist_categories()<br/>→ relevance thresholds"]
        DE4["apply_conditional_rules()<br/>→ deterministic post-LLM overrides"]
    end

    subgraph SV["Step Validator"]
        SV1["validation_directives_prompt_block()<br/>→ TC-HV TC-LV etc injected at call time"]
    end

    REG -->|"F1 F3"| DR1
    REG -->|"F1"| DR2
    REG -->|"F7"| DR3
    REG -->|"F2"| DE1
    REG -->|"F4"| DE2
    REG -->|"F4"| DE3
    REG -->|"F6"| DE4
    REG -->|"F5"| SV1
```

---

## 4. Agent Pipeline — System Context

```mermaid
flowchart TD
    USER(["User / Browser"])

    subgraph PIPELINE["Agent Pipeline  (Docker Compose)"]
        direction TB

        FE["Frontend :8080<br/>nginx + HTML/JS<br/>Task selection, status monitoring"]

        RESP["Responder :8000<br/>Task intake & plan decomposition<br/>tasks.json template matching → OpenAI fallback"]

        ORCH["Orchestrator :8001<br/>Step execution engine<br/>State machine · Validation gate · Retry logic"]

        subgraph SPECIALISTS["Specialist Agents"]
            direction LR
            DREV["Document Reviewer :8089<br/>/review — classify documents<br/>/plan_processing — route + tool plan"]
            DEXT["Document Extractor :8090<br/>/extract — deep content extraction<br/>text · vision (chunks) · spreadsheet"]
            DCHK["Document Chunker :8091<br/>/chunk — PDF → PNG tiles<br/>quadrant-split for A1/A0 drawings"]
        end

        SVAL["Step Validator :8088<br/>Independent QA agent<br/>Generates + runs structured test cases"]
    end

    subgraph DATA["Process Knowledge"]
        direction LR
        PROC["process/*.json<br/>extraction categories<br/>routing · validation directives<br/>conditional rules"]
        TASK["tasks.json<br/>Digital workers + task templates<br/>Step definitions + validation criteria"]
        REGY["registry.json<br/>Agent + tool catalogue<br/>Capabilities + endpoints"]
    end

    subgraph OUTPUT["Artefacts  /app/OUTPUT/"]
        direction LR
        A1["DocumentReview_{ts}.json"]
        A2["ProcessingPlan_{ts}.json"]
        A3["ExtractionReport_{ts}.json"]
        A4["AssetExtract_{ts}.json"]
        A5["ConsolidatedReport_{ts}.json"]
        A6["TestReport_{step}_{ts}.json"]
    end

    USER <-->|"HTTP :8080"| FE
    FE   -->|"POST /decompose"| RESP
    RESP -->|"POST /execute"| ORCH
    ORCH -->|"step dispatch"| SPECIALISTS
    ORCH -->|"POST /validate_step\n(after every step)"| SVAL
    SPECIALISTS -->|"JSON artefacts"| OUTPUT
    SVAL        -->|"test reports"| OUTPUT

    PROC -->|"KnowledgeRegistry"| SPECIALISTS
    TASK -->|"TaskLibrary"| RESP
    REGY -->|"AgentRegistry"| RESP
    REGY -->|"tool selection"| DREV

    style ORCH fill:#dbeafe,stroke:#3b82f6
    style SVAL fill:#fef3c7,stroke:#f59e0b
    style PROC fill:#dcfce7,stroke:#22c55e
```

---

## 5. Standard Agent — Anatomy at a Glance

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         SPECIALIST AGENT                                    │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  FRAMEWORK LAYER  (common/)  — identical in every agent             │  │
│  │                                                                      │  │
│  │  create_app(title, model)   settings          LLMClient             │  │
│  │  ├─ CORS middleware         ├─ LLM model names ├─ OpenAI (json mode) │  │
│  │  ├─ Request logger          ├─ Service URLs    ├─ Anthropic          │  │
│  │  └─ GET /health             └─ Storage dirs    ├─ Gemini (vision)    │  │
│  │                                                └─ Retry + fallback   │  │
│  │  OutputManager              KnowledgeRegistry                        │  │
│  │  ├─ write(data, prefix)     ├─ route_for_category()                 │  │
│  │  ├─ register_read()         ├─ model_for_process()                  │  │
│  │  └─ manifest() → files{}   ├─ tagging_rules_prompt_block()          │  │
│  │                             └─ apply_conditional_rules()             │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  DOMAIN LAYER  — unique per agent                                   │  │
│  │                                                                      │  │
│  │  Request Model               Core Logic Functions                    │  │
│  │  ├─ input fields             ├─ call LLM with process-driven prompt  │  │
│  │  ├─ output_dir (optional)    ├─ read/parse documents                 │  │
│  │  └─ job_id (optional)        └─ apply conditional rules              │  │
│  │                                                                      │  │
│  │  Endpoint  POST /my-endpoint                                        │  │
│  │  ├─ validate request (Pydantic)                                      │  │
│  │  ├─ initialise OutputManager(job_dir)                               │  │
│  │  ├─ call core function(s)                                           │  │
│  │  ├─ out.write(result, prefix)                                        │  │
│  │  └─ return {**result, output_file, files: manifest()}               │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  Standard Response Contract (required by Orchestrator)                      │
│  ├─ output_file : str        path to primary JSON artefact                 │
│  └─ files       : dict       {files_read: [...], files_output: [...]}      │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘

  External inputs injected by Orchestrator into every request:
  ├─ output_dir   →  OutputManager(job_dir=output_dir)  isolates per execution
  ├─ job_id       →  correlation ID for logging
  └─ *_file paths →  paths to prior step artefacts (step1_output_file etc.)
```

---

## 6. New Agent Checklist

```mermaid
flowchart TD
    A(["Start: new document type\nor workflow step needed"]) --> B

    B["1 · Write process/*.json\n────────────────────────\nroute_to_step\nforce_category_for_extensions\nextraction_categories\nvalidation_directives\nconditional_rules\ndocument_categorisation"] --> C

    C["2 · Add task template to tasks.json\n────────────────────────\nstep definitions\nvalidation criteria\nrequired_resources (agent_id)"] --> D

    D{"Does a new\nspecialist agent\nneed to be built?"}

    D -- "No — existing agent\ncan handle it" --> G
    D -- "Yes" --> E

    E["3 · Copy AGENT_TEMPLATE.py\n────────────────────────\nfrom common.agent import create_app\nfrom common.llm import LLMClient\nfrom common.output import OutputManager\nfrom common.knowledge import KnowledgeRegistry\nImplement core logic function(s)\nDefine Request model"] --> F

    F["4 · Add infrastructure\n────────────────────────\nDockerfile.<agent_name>\ndocker-compose.yml service entry\nregistry.json agent + tool entries"] --> G

    G(["Done — pipeline handles\nnew document type\nwith zero changes to\nexisting agent code"])

    style B fill:#dcfce7,stroke:#22c55e
    style C fill:#dcfce7,stroke:#22c55e
    style E fill:#dbeafe,stroke:#3b82f6
    style F fill:#dbeafe,stroke:#3b82f6
    style G fill:#f0fdf4,stroke:#16a34a
```
