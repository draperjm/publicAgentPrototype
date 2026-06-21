# Agent 03 - Architecture Design Document

## System Overview

A multi-agent workflow system for automated infrastructure asset verification.
Eight Docker-containerised microservices communicate over a shared bridge network,
orchestrated by a central step executor with independent QA validation.

---

## Architecture Diagram

```mermaid
graph TB
    subgraph "User Interface"
        UI["Frontend (nginx:alpine)<br/>Port 8080<br/>Single-page HTML/CSS/JS"]
    end

    subgraph "Control Plane"
        DEC["Decomposer<br/>Port 8000<br/>decomposer.py"]
        ORC["Orchestrator<br/>Port 8001<br/>orchestrator.py"]
    end

    subgraph "Specialist Agents"
        AO["Asset Ops<br/>Port 8084<br/>asset_ops.py"]
        CR["Content Reviewer<br/>Port 8085<br/>content_reviewer.py"]
        MA["Mapping Agent<br/>Port 8087<br/>mapping_agent.py"]
        VA["Verification Agent<br/>Port 8086<br/>verification_agent.py"]
    end

    subgraph "Quality Assurance"
        SV["Step Validator<br/>Port 8088<br/>step_validator_agent.py"]
    end

    subgraph "External LLM Providers"
        OAI["OpenAI<br/>GPT-3.5-turbo / GPT-4o"]
        GEM["Google Gemini<br/>2.5 Flash"]
        ANT["Anthropic<br/>Claude 3 Opus"]
    end

    subgraph "Shared Storage"
        OUT[("OUTPUT/<br/>Timestamped JSON files")]
        LOG[("Logs<br/>llm_traffic.log<br/>validation_traffic.log")]
    end

    UI -->|"GET /tasks<br/>POST /decompose"| DEC
    UI -->|"POST /run_next/{plan_id}"| ORC
    DEC -->|"POST /execute"| ORC

    ORC -->|"Step 1: POST /extract_assets"| AO
    ORC -->|"Step 2: POST /review_content"| CR
    ORC -->|"Step 3: POST /create_mapping"| MA
    ORC -->|"Step 4: POST /verify_assets"| VA
    ORC -->|"POST /validate_step<br/>(after every step)"| SV

    AO -->|"LLM extraction"| OAI
    CR -->|"PDF (multimodal)"| GEM
    CR -->|"XLS/CSV"| ANT
    CR -->|"DOCX/fallback"| OAI
    VA -->|"Visual verification<br/>Gemini File API"| GEM
    SV -->|"Test generation"| GEM
    SV -.->|"Fallback"| OAI
    DEC -->|"Plan generation"| OAI

    AO --> OUT
    CR --> OUT
    MA --> OUT
    VA --> OUT
    SV --> OUT
    CR --> LOG
    SV --> LOG

    style UI fill:#dbeafe,stroke:#2563eb,color:#1e293b
    style DEC fill:#f0fdf4,stroke:#16a34a,color:#1e293b
    style ORC fill:#f0fdf4,stroke:#16a34a,color:#1e293b
    style AO fill:#fef3c7,stroke:#f59e0b,color:#1e293b
    style CR fill:#fef3c7,stroke:#f59e0b,color:#1e293b
    style MA fill:#fef3c7,stroke:#f59e0b,color:#1e293b
    style VA fill:#fef3c7,stroke:#f59e0b,color:#1e293b
    style SV fill:#fce7f3,stroke:#ec4899,color:#1e293b
    style OAI fill:#f1f5f9,stroke:#64748b,color:#1e293b
    style GEM fill:#f1f5f9,stroke:#64748b,color:#1e293b
    style ANT fill:#f1f5f9,stroke:#64748b,color:#1e293b
    style OUT fill:#e0e7ff,stroke:#6366f1,color:#1e293b
    style LOG fill:#e0e7ff,stroke:#6366f1,color:#1e293b
```

---

## Data Flow Diagram

```mermaid
sequenceDiagram
    participant U as User (Browser)
    participant F as Frontend :8080
    participant D as Decomposer :8000
    participant O as Orchestrator :8001
    participant A as Asset Ops :8084
    participant C as Content Reviewer :8085
    participant M as Mapping Agent :8087
    participant V as Verification Agent :8086
    participant S as Step Validator :8088

    U->>F: Select task from dropdown
    F->>D: POST /decompose {task}
    D->>D: Match template or LLM plan
    D->>O: POST /execute {plan_overview, steps[]}
    O-->>D: {plan_id}
    D-->>F: {plan, plan_id}
    F-->>U: Render workflow steps

    Note over U,S: Step 1 - Asset Extraction

    U->>F: Click "Run Step 1" + upload asset file
    F->>O: POST /run_next/{plan_id} + file
    O->>A: POST /extract_assets + file
    A->>A: Parse file, call GPT-3.5-turbo
    A-->>O: {result, output_file, files}
    O->>S: POST /validate_step + input_file + output_file
    S->>S: Generate test cases via Gemini
    S-->>O: {validation: {test_cases, score, files}}
    O-->>F: {step_output, validation, log}

    Note over U,S: Step 2 - Legend Extraction

    U->>F: Click "Run Step 2" + upload PDF
    F->>O: POST /run_next/{plan_id} + file
    O->>C: POST /review_content + file + instruction
    C->>C: Route to Gemini (PDF multimodal)
    C->>C: Up to 3 retry attempts
    C-->>O: {result, output_file, files}
    O->>S: POST /validate_step
    S-->>O: {validation}
    O-->>F: {step_output, validation, log}

    Note over U,S: Step 3 - Asset-to-Legend Mapping

    U->>F: Click "Run Step 3" (no file needed)
    F->>O: POST /run_next/{plan_id}
    O->>M: POST /create_mapping {asset_list, legend}
    M->>M: Exact + fuzzy match (SequenceMatcher)
    M-->>O: {result, output_file, summary, files}
    O->>S: POST /validate_step
    S-->>O: {validation}
    O-->>F: {step_output, validation, log}

    Note over U,S: Step 4 - Visual Verification

    U->>F: Click "Run Step 4"
    F->>O: POST /run_next/{plan_id}
    O->>V: POST /verify_assets (no drawing)
    V-->>O: {status: "interaction_required"}
    O-->>F: {status: "paused", action: "request_file_upload"}
    F-->>U: Show file upload modal

    U->>F: Upload drawing + confirm
    F->>O: POST /run_next/{plan_id} + drawing
    O->>V: POST /verify_assets + drawing + asset_map + legend
    V->>V: PDF->PNG (300 DPI), upload to Gemini File API
    V->>V: Gemini fills verification skeleton
    V-->>O: {report, output_file, files}
    O->>S: POST /validate_step
    S-->>O: {validation}
    O-->>F: {step_output, validation, log}
    F-->>U: Workflow Complete
```

---

## Service Inventory

| Service | Port | File | Framework | Role |
|---------|------|------|-----------|------|
| Frontend | 8080 | index.html | nginx + vanilla JS | User interface |
| Decomposer | 8000 | decomposer.py | FastAPI | Task planning & template matching |
| Orchestrator | 8001 | orchestrator.py | FastAPI | Step execution, routing, state management |
| Asset Ops | 8084 | asset_ops.py | FastAPI | Data extraction from CSV/XLSX/JSON/DOCX |
| Content Reviewer | 8085 | content_reviewer.py | FastAPI | Legend extraction with multi-LLM routing |
| Verification Agent | 8086 | verification_agent.py | FastAPI | Visual drawing cross-reference |
| Mapping Agent | 8087 | mapping_agent.py | FastAPI | Asset-to-legend fuzzy join |
| Step Validator | 8088 | step_validator_agent.py | FastAPI | Independent QA test case generation |

---

## Common Elements Across All Agents

### 1. Framework Pattern
Every agent follows the same structural pattern:
```python
from fastapi import FastAPI
from dotenv import load_dotenv

load_dotenv()
app = FastAPI(title="Agent Name")

# Endpoint(s)
@app.post("/action")
async def action(...):
    # 1. Parse input
    # 2. Process (LLM call or logic)
    # 3. Save output to OUTPUT/
    # 4. Return {status, result, output_file, files}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=XXXX)
```

### 2. Output File Convention
All agents write timestamped JSON to a shared `OUTPUT/` directory:
```
OUTPUT/{StepType}_{YYYYMMDD_HHMMSS}.json
```
| Agent | File Pattern |
|-------|-------------|
| Asset Ops | `Step1_*.json` |
| Content Reviewer | `Step2_*.json` |
| Mapping Agent | `AssetMap_*.json` |
| Verification Agent | `Verification_Report_*.json` |
| Step Validator | `TestReport_*.json` |

### 3. Response Envelope
All agents return a standard response structure:
```json
{
    "status": "success",
    "result": { ... },
    "output_file": "OUTPUT/StepN_20260215_123456.json",
    "files": {
        "files_read": [
            {"filename": "...", "role": "input", "description": "..."}
        ],
        "files_output": [
            {"filename": "...", "path": "...", "role": "output", "description": "..."}
        ]
    }
}
```

### 4. LLM Integration Patterns
| Pattern | Used By |
|---------|---------|
| Single provider (OpenAI) | Decomposer, Asset Ops |
| Multi-provider dispatch (file-type routing) | Content Reviewer |
| Primary + fallback (Gemini -> OpenAI) | Step Validator |
| Gemini File API (visual/multimodal) | Verification Agent, Content Reviewer (PDF) |
| Retry with self-correction | Content Reviewer (up to 3 attempts) |

### 5. Docker Pattern
All Python services use the same Dockerfile structure:
```dockerfile
FROM python:3.9-slim
WORKDIR /app
COPY requirements_xxx.txt .
RUN pip install -r requirements_xxx.txt
COPY agent_file.py .
CMD ["uvicorn", "agent_file:app", "--host", "0.0.0.0", "--port", "XXXX"]
```

### 6. Networking
- All containers on `agent-network` (Docker bridge)
- Inter-service calls use container DNS names: `http://service-name:port`
- Frontend calls use `localhost:port` (browser -> host -> container)

### 7. State Management
The Orchestrator maintains in-memory state per execution:
```python
executions[plan_id] = {
    "plan": ExecutionRequest,
    "current_step_index": 0,
    "total_steps": N,
    "is_complete": False,
    "results": {
        "asset_list": {},    # From Step 1
        "legend": {},        # From Step 2
        "asset_map": {},     # From Step 3
        "verification_report": {},  # From Step 4
        "validations": []    # From Step Validator
    }
}
```

### 8. Validation Pipeline
After every specialist agent completes, the Orchestrator automatically:
1. Captures input data, output data, and file references
2. Reads the agent's output file from shared disk
3. Forwards everything to the Step Validator via multipart POST
4. Step Validator generates structured test cases (TC-001, TC-002, ...)
5. Test results are stored in state and returned to the frontend

---

## LLM Provider Usage Map

```mermaid
graph LR
    subgraph "OpenAI"
        GPT35["GPT-3.5-turbo"]
        GPT4O["GPT-4o"]
    end

    subgraph "Google"
        GEMF["Gemini 2.5 Flash"]
    end

    subgraph "Anthropic"
        CLAUDE["Claude 3 Opus"]
    end

    DEC2["Decomposer"] --> GPT35
    AO2["Asset Ops"] --> GPT35
    CR2["Content Reviewer"] --> GPT4O
    CR2 --> GEMF
    CR2 --> CLAUDE
    VA2["Verification Agent"] --> GEMF
    SV2["Step Validator"] --> GEMF
    SV2 -.->|fallback| GPT4O

    style GPT35 fill:#dcfce7,stroke:#16a34a
    style GPT4O fill:#dcfce7,stroke:#16a34a
    style GEMF fill:#dbeafe,stroke:#2563eb
    style CLAUDE fill:#fef3c7,stroke:#f59e0b
```

---

## File I/O Flow

```mermaid
graph LR
    subgraph "User Uploads"
        F1["assetFile.json<br/>(CSV/XLSX/JSON/DOCX)"]
        F2["legend_v1.pdf<br/>(PDF/Image)"]
        F3["diagram_v1.pdf<br/>(PDF/Image)"]
    end

    subgraph "Agent Outputs"
        O1["Step1_*.json<br/>Asset list"]
        O2["Step2_*.json<br/>Legend data"]
        O3["AssetMap_*.json<br/>Mapping"]
        O4["Verification_Report_*.json"]
    end

    subgraph "Validator Outputs"
        T1["TestReport_Step1_*.json"]
        T2["TestReport_Step2_*.json"]
        T3["TestReport_Step3_*.json"]
        T4["TestReport_Step4_*.json"]
    end

    F1 -->|Step 1| O1
    F2 -->|Step 2| O2
    O1 -->|Step 3| O3
    O2 -->|Step 3| O3
    O3 -->|Step 4| O4
    O2 -->|Step 4| O4
    F3 -->|Step 4| O4

    O1 -.->|validated by| T1
    O2 -.->|validated by| T2
    O3 -.->|validated by| T3
    O4 -.->|validated by| T4

    style F1 fill:#eff6ff,stroke:#2563eb
    style F2 fill:#eff6ff,stroke:#2563eb
    style F3 fill:#eff6ff,stroke:#2563eb
    style O1 fill:#f0fdf4,stroke:#16a34a
    style O2 fill:#f0fdf4,stroke:#16a34a
    style O3 fill:#f0fdf4,stroke:#16a34a
    style O4 fill:#f0fdf4,stroke:#16a34a
    style T1 fill:#fce7f3,stroke:#ec4899
    style T2 fill:#fce7f3,stroke:#ec4899
    style T3 fill:#fce7f3,stroke:#ec4899
    style T4 fill:#fce7f3,stroke:#ec4899
```

---

## Infrastructure Diagram

```mermaid
graph TB
    subgraph "Docker Host (Windows 11)"
        subgraph "agent-network (bridge)"
            D["agent-decomposer<br/>:8000"]
            O["agent-orchestrator<br/>:8001"]
            A["agent-asset-ops<br/>:8084"]
            C["agent-content-reviewer<br/>:8085"]
            V["agent-verification<br/>:8086"]
            M["agent-mapper<br/>:8087"]
            S["agent-step-validator<br/>:8088"]
            F["agent-frontend<br/>:8080->80"]
        end

        subgraph "Shared Volume (.:/app)"
            ENV[".env<br/>API Keys"]
            DATA["Data/<br/>Input files"]
            OUTPUT["OUTPUT/<br/>All agent outputs"]
            LOGS["*.log<br/>Traffic logs"]
        end
    end

    subgraph "External APIs"
        OPENAI["api.openai.com"]
        GOOGLE["generativelanguage.googleapis.com"]
        ANTHROPIC["api.anthropic.com"]
    end

    D --- ENV
    O --- OUTPUT
    A --- OUTPUT
    C --- OUTPUT
    V --- OUTPUT
    M --- OUTPUT
    S --- OUTPUT

    A --> OPENAI
    C --> OPENAI
    C --> GOOGLE
    C --> ANTHROPIC
    V --> GOOGLE
    S --> GOOGLE
    S --> OPENAI
    D --> OPENAI
```
