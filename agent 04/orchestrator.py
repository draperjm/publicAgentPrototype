import asyncio
import time
import uuid
import requests
import os
import json
import concurrent.futures
from io import BytesIO
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="Orchestrator Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- State ---
# Modified to store "results" so we can pass data between steps
executions: Dict[str, dict] = {}

# --- Models ---
class StepValidation(BaseModel):
    criteria: Optional[str] = None
    critical_fail: Optional[bool] = False

class StepResources(BaseModel):
    agent_id: Optional[str] = None
    tool_id: Optional[str] = None
    knowledge_id: Optional[str] = None

class PlanStep(BaseModel):
    step_number: int
    description: str
    name: Optional[str] = None
    assigned_resource_id: Optional[str] = None
    required_resources: Optional[StepResources] = None
    validation: Optional[StepValidation] = None
    parallel_group: Optional[str] = None

class ExecutionRequest(BaseModel):
    plan_overview: str
    steps: List[PlanStep]

# --- Service URLs (configurable for Azure Container Apps) ---
VALIDATOR_URL       = os.getenv("VALIDATOR_URL",        "http://step-validator:8088/validate_step")
ASSET_OPS_URL       = os.getenv("ASSET_OPS_URL",        "http://asset-ops:8084/extract_assets")
REVIEWER_URL        = os.getenv("REVIEWER_URL",         "http://content-reviewer:8085/review_content")
MAPPER_URL          = os.getenv("MAPPER_URL",           "http://agent-mapper:8087/create_mapping")
VERIF_URL           = os.getenv("VERIF_URL",            "http://agent-verification:8086/verify_assets")
DOC_REVIEWER_URL    = os.getenv("DOC_REVIEWER_URL",     "http://document-reviewer:8089/review")
DOC_PLAN_URL        = os.getenv("DOC_PLAN_URL",         "http://document-reviewer:8089/plan_processing")
DOC_EXTRACTOR_URL   = os.getenv("DOC_EXTRACTOR_URL",    "http://document-extractor:8090/extract")
DOC_RAW_TEXT_URL    = os.getenv("DOC_RAW_TEXT_URL",     "http://document-extractor:8090/raw_text")
CHUNKER_URL         = os.getenv("CHUNKER_URL",           "http://document-chunker:8091/chunk")
DOCUMENTS_FOLDER    = os.getenv("DOCUMENTS_FOLDER",     "/documents")
MAX_STEP_RETRIES    = 2   # automatic retries per step before marking as failed
RETRY_DELAY_SECS    = 3   # seconds between retries


def _make_job_key(folder_path: str) -> str:
    """Derive a unique job directory name from the application folder + timestamp.
    E.g. '/documents/DS1' → 'DS1_20260312_104858'
    """
    folder_base = Path(folder_path).name or "job"
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return f"{folder_base}_{ts}"

def _call_validator(step_name: str, step_description: str,
                    input_data: Any, output_data: Any,
                    validation_criteria: str = "",
                    input_file_tuple: tuple = None,
                    output_file_path: str = "",
                    agent_files: dict = None) -> Optional[dict]:
    """Call the independent step validator agent after each step.

    Args:
        input_file_tuple: (filename, bytes, content_type) of the original input file
        output_file_path: Path to the output file saved by the agent on disk
        agent_files: Files metadata from the agent (files_read, files_output)
    """
    try:
        form_data = {
            "step_name": step_name or "Unknown Step",
            "step_description": step_description or "",
            "input_data_json": json.dumps(input_data, default=str) if input_data else "{}",
            "output_data_json": json.dumps(output_data, default=str) if output_data else "{}",
            "validation_criteria": validation_criteria or "",
            "output_file_path": output_file_path or "",
            "agent_files_json": json.dumps(agent_files, default=str) if agent_files else "{}",
        }

        files = {}

        # Attach input file if available
        if input_file_tuple:
            fname, fbytes, ftype = input_file_tuple
            files["input_file"] = (fname, BytesIO(fbytes), ftype)
            print(f"[ORCHESTRATOR] Forwarding input file to validator: {fname} ({len(fbytes)} bytes)")

        # Read and attach output file from disk if path provided
        if output_file_path and os.path.exists(output_file_path):
            with open(output_file_path, "rb") as f:
                out_bytes = f.read()
            out_fname = os.path.basename(output_file_path)
            files["output_file"] = (out_fname, BytesIO(out_bytes), "application/json")
            print(f"[ORCHESTRATOR] Forwarding output file to validator: {out_fname} ({len(out_bytes)} bytes)")

        resp = requests.post(VALIDATOR_URL, data=form_data, files=files if files else None, timeout=300)
        if resp.status_code == 200:
            result = resp.json()
            # Return validation even if the LLM errored — caller can inspect is_valid/summary
            return result.get("validation")
        else:
            print(f"[ORCHESTRATOR] Validator returned HTTP {resp.status_code}: {resp.text[:300]}")
    except Exception as e:
        print(f"[ORCHESTRATOR] Validator call failed: {e}")
    return None

def _run_extractor_step(state: dict, step: "PlanStep") -> dict:
    """Build payload and call the document extractor. Returns a dict with log, data, output_file, agent_files."""
    log = f"   [Step {step.step_number}] {step.name}: Routing to Document Extractor (Port 8090)...\n"

    processing_plan = state["results"].get("processing_plan", {})
    plan_entries    = processing_plan.get("processing_plan", [])
    doc_review      = state["results"].get("document_review", {})
    docs_folder     = doc_review.get("folder_path", DOCUMENTS_FOLDER)

    knowledge_id = ""
    if step.required_resources and hasattr(step.required_resources, "knowledge_id"):
        knowledge_id = getattr(step.required_resources, "knowledge_id", "") or ""

    # Determine which document_category this extractor step targets.
    # Primary source: knowledge_id. Fallback: infer from step name so filtering
    # works even when knowledge_id is absent.
    target_category = None
    if knowledge_id == "proc-extract-design-brief-info":
        target_category = "Design Brief"
    elif knowledge_id == "proc-extract-site-plan-info":
        target_category = "Site Plan"
    elif knowledge_id == "proc-extract-asset-spreadsheet":
        target_category = "TAL"
    if target_category is None:
        step_name_lower = (step.name or "").lower()
        if "design brief" in step_name_lower:
            target_category = "Design Brief"
        elif "site plan" in step_name_lower:
            target_category = "Site Plan"
        elif "asset" in step_name_lower or "spreadsheet" in step_name_lower or "tal" in step_name_lower:
            target_category = "TAL"

    _DRAWING_KEYWORDS   = ("retic", "reticulation", "drawing", "diagram", "network")
    _DESIGN_BR_KEYWORDS = ("brief", "architectural", "services", "town planning")

    def _matches_category(entry: dict) -> bool:
        """Return True if this plan entry should be processed by this step."""
        cat   = (entry.get("document_category") or "").strip()
        dtype = (entry.get("document_type") or "").lower()

        if target_category == "Design Brief":
            # Explicitly exclude Site Plan and drawing documents regardless of how
            # category was determined — these must never enter Design Brief extraction.
            if cat == "Site Plan":
                return False
            if any(k in dtype for k in _DRAWING_KEYWORDS):
                return False
            if cat == "Design Brief":
                return True
            # No explicit category — infer from document_type
            return any(k in dtype for k in _DESIGN_BR_KEYWORDS)

        if target_category == "Site Plan":
            # Explicitly exclude Design Brief documents
            if cat == "Design Brief":
                return False
            if cat == "Site Plan":
                return True
            return any(k in dtype for k in _DRAWING_KEYWORDS)

        if target_category == "TAL":
            if cat == "TAL":
                return True
            # Fallback: match spreadsheet extensions directly
            import pathlib
            ext = pathlib.Path(entry.get("filename", "")).suffix.lower()
            return ext in {".xlsx", ".xls", ".xlsm", ".xlsb"}

        return True  # No filter if target category still unknown

    filtered_entries = [e for e in plan_entries if _matches_category(e)]
    skipped = len(plan_entries) - len(filtered_entries)
    if skipped:
        log += f"   Filtered to {len(filtered_entries)}/{len(plan_entries)} files for '{target_category}' (skipped {skipped}).\n"
    elif target_category:
        log += f"   All {len(filtered_entries)} file(s) match target category '{target_category}'.\n"

    chunk_manifests = state.get("results", {}).get("chunk_manifests", {})
    files_payload = [
        {
            "filename":           entry.get("filename", ""),
            "processing_tool_id": entry.get("processing_tool_id", "tool-extract-pdf-content"),
            "document_type":      entry.get("document_type"),
            "document_category":  entry.get("document_category"),
            "content_type":       entry.get("content_type", "text"),
            "text_quality":       entry.get("text_quality", "high"),
            "page_size":          entry.get("page_size", "A4"),
            "requires_chunking":  entry.get("requires_chunking", False),
            "chunk_strategy":     entry.get("chunk_strategy", "none"),
            "estimated_chunks":   entry.get("estimated_chunks", 1),
            "chunk_manifest":        chunk_manifests.get(entry.get("filename")),
            "worksheet_count":       entry.get("worksheet_count"),
            "worksheets_to_analyse": entry.get("worksheets_to_analyse"),
            "worksheets":            entry.get("worksheets"),
        }
        for entry in filtered_entries
    ]

    expected_output = None
    all_sub_steps   = []
    if knowledge_id:
        try:
            proc_file = PROCESS_DIR / f"{knowledge_id}.json"
            if not proc_file.exists():
                for pf in PROCESS_DIR.glob("*.json"):
                    with open(pf) as _f:
                        _d = json.load(_f)
                    if _d.get("process_id") == knowledge_id:
                        proc_file = pf
                        break
            if proc_file.exists():
                with open(proc_file) as _f:
                    proc_data = json.load(_f)
                proc_steps = proc_data.get("steps", [])
                combined_fields     = {}
                combined_desc_parts = []
                task_step_name_lower = (step.name or "").lower()
                matched_proc_steps = [
                    ps for ps in proc_steps
                    if task_step_name_lower in (ps.get("step_name") or "").lower()
                    or task_step_name_lower in (ps.get("step_id") or "").lower()
                ]
                target_proc_steps = matched_proc_steps if matched_proc_steps else proc_steps
                for ps in target_proc_steps:
                    eo = ps.get("expected_output", {})
                    if eo:
                        combined_desc_parts.append(
                            f"Step {ps.get('step_number')}: {ps.get('step_name')} — "
                            f"{eo.get('description', '')}"
                        )
                        if isinstance(eo.get("fields"), dict):
                            combined_fields.update(eo["fields"])
                    if ps.get("sub_steps"):
                        all_sub_steps.extend(ps["sub_steps"])
                if combined_fields:
                    expected_output = {
                        "description": " | ".join(combined_desc_parts),
                        "fields":      combined_fields,
                    }
        except Exception:
            pass

    process_step_payload = {
        "step_id":        step.name,
        "step_name":      step.name or f"Step {step.step_number}",
        "details":        step.description,
        "expected_output": expected_output,
        "sub_steps":      all_sub_steps if all_sub_steps else None,
    }

    job_key    = state.get("results", {}).get("job_key")
    output_dir = str(OUTPUT_DIR / job_key) if job_key else None

    payload = {
        "files":            files_payload,
        "process_step":     process_step_payload,
        "documents_folder": docs_folder,
        "output_dir":       output_dir,
        "process_id":       knowledge_id or None,
    }

    for _try in range(MAX_STEP_RETRIES + 1):
        try:
            resp = requests.post(DOC_EXTRACTOR_URL, json=payload, timeout=600)
            if resp.status_code == 200:
                res_json = resp.json()
                n_files = res_json.get("total_files", 0)
                log += f"   Extracted {n_files} file(s).\n"
                return {
                    "log":         log,
                    "data":        res_json,
                    "output_file": res_json.get("output_file", ""),
                    "agent_files": res_json.get("files"),
                    "input_data":  {"files": files_payload, "process_step": process_step_payload},
                    "failed":      False,
                }
            else:
                log += f"   Agent Error (attempt {_try+1}/{MAX_STEP_RETRIES+1}): {resp.text}\n"
        except Exception as e:
            log += f"   Network Error (attempt {_try+1}/{MAX_STEP_RETRIES+1}): {e}\n"
        if _try < MAX_STEP_RETRIES:
            log += f"   Retrying in {RETRY_DELAY_SECS}s...\n"; time.sleep(RETRY_DELAY_SECS)
        else:
            log += "   Step failed after all retries.\n"

    return {"log": log, "data": None, "output_file": "", "agent_files": None, "input_data": {}, "failed": True}


def _validate_parallel_steps(group_steps, parallel_result_map):
    """
    Blocking helper: fetch raw PDF text and run the validator for each step in a
    parallel extraction group. Runs in a thread so the event loop stays free.

    Returns (parallel_validations: dict, log_additions: str)
    """
    parallel_validations = {}
    log_parts = []
    for ps in group_steps:
        ps_result = parallel_result_map.get(ps.step_number, {})
        ps_data   = ps_result.get("data")
        if not ps_data:
            continue

        ps_input = ps_result.get("input_data", {})

        ps_paths = [ex.get("filepath") for ex in ps_data.get("extractions", []) if ex.get("filepath")]
        if ps_paths:
            try:
                raw_resp = requests.post(DOC_RAW_TEXT_URL, json={"filepaths": ps_paths}, timeout=60)
                if raw_resp.status_code == 200:
                    ps_input = {**ps_input, "source_pdf_raw_text": raw_resp.json().get("documents", {})}
            except Exception:
                pass

        ps_val = _call_validator(
            step_name=ps.name or f"Step {ps.step_number}",
            step_description=ps.description,
            input_data=ps_input,
            output_data=ps_data,
            validation_criteria=ps.validation.criteria if ps.validation else "",
            input_file_tuple=None,
            output_file_path=ps_result.get("output_file", ""),
            agent_files=ps_result.get("agent_files"),
        )
        if ps_val:
            emoji = "PASS" if ps_val.get("is_valid") else "WARN"
            log_parts.append(
                f"   [{emoji}] {ps.name} validation: {ps_val.get('summary','')} ({ps_val.get('score',0)}%)\n"
            )
            parallel_validations[ps.step_number] = ps_val

    return parallel_validations, "".join(log_parts)


@app.post("/execute")
async def initialize_plan(request: ExecutionRequest):
    plan_id = str(uuid.uuid4())
    executions[plan_id] = {
        "plan": request,
        "current_step_index": 0,
        "total_steps": len(request.steps),
        "is_complete": False,
        "results": {} # Store output context here (e.g., assets, legend)
    }
    return {"status": "ready", "plan_id": plan_id}

@app.post("/run_next/{plan_id}")
async def run_next_step(plan_id: str, file: UploadFile = File(None), folder_path: str = Form(None),
                        step_index: Optional[int] = Form(None)):
    """Thin wrapper: reads file bytes, launches step as a background task, and streams
    periodic keepalive bytes back to the browser while work runs.  This prevents the
    WSL2 NAT TCP reset that fires after ~97 s of connection inactivity."""
    if plan_id not in executions:
        raise HTTPException(status_code=404, detail="Plan ID not found")

    state = executions[plan_id]
    if state["is_complete"]:
        return {"status": "completed", "message": "Plan is already finished."}

    # If the frontend tells us which step to run, sync the server counter to match.
    # This keeps the server in sync when the browser skips disabled steps.
    if step_index is not None and 0 <= step_index < state["total_steps"]:
        state["current_step_index"] = step_index

    # Read the upload once here — UploadFile cannot be passed to a background task.
    file_bytes: Optional[bytes] = await file.read() if file else None
    file_name: Optional[str]    = file.filename      if file else None
    file_content_type: Optional[str] = file.content_type if file else None

    task: asyncio.Task = asyncio.ensure_future(
        _run_step_impl(plan_id, file_bytes, file_name, file_content_type, folder_path)
    )

    async def _stream():
        # Send a space every 5 s so the WSL2 NAT never sees an idle connection.
        while not task.done():
            yield b" "
            await asyncio.sleep(5)
        try:
            result = task.result()
        except Exception as exc:
            result = {"status": "error", "log": f"Unexpected error: {exc}"}
        yield json.dumps(result).encode()

    return StreamingResponse(
        _stream(),
        media_type="application/json",
        headers={"X-Accel-Buffering": "no"},
    )


async def _run_step_impl(
    plan_id: str,
    file_bytes: Optional[bytes],
    file_name: Optional[str],
    file_content_type: Optional[str],
    folder_path: Optional[str],
) -> dict:
    """All step-execution logic. Called as an asyncio Task by run_next_step."""
    state = executions[plan_id]
    idx = state["current_step_index"]
    step: PlanStep = state["plan"].steps[idx]

    # Identify Resource
    resource = step.assigned_resource_id
    if step.required_resources and step.required_resources.agent_id:
        resource = step.required_resources.agent_id

    resource_key = (resource or "").lower()
    log_message = f"[STEP {step.step_number}] Action: {step.name}\n"

    # Track input/output for validator
    step_input_data = None
    step_output_data = None
    step_output_file_path = ""  # Track where agent saved its output file
    agent_files = None  # Track files the agent ingested/output
    step_failed = False  # Set True when all retries are exhausted
    validation_result = None  # Set by validation logic below
    parallel_completed_indices = None  # Filled when a parallel group runs
    parallel_step_validations = None   # {0-based-idx: validation} for each parallel step

    # --- ROUTING LOGIC ---

    # A. ASSET OPS AGENT (Port 8084)
    if "asset" in resource_key and "ops" in resource_key:
        if not file:
            return {"status": "error", "log": log_message + "   ERROR: File required!", "step_index": idx}

        log_message += f"   Sending to Asset Ops (Port 8084)...\n"
        step_input_data = {"file": file_name}
        for _try in range(MAX_STEP_RETRIES + 1):
            try:
                files = {'file': (file_name, BytesIO(file_bytes), file_content_type)}
                resp = requests.post(ASSET_OPS_URL, files=files, timeout=30)
                if resp.status_code == 200:
                    data = resp.json()
                    asset_data = data.get("result", data)
                    count = len(asset_data.get('assets', []))
                    log_message += f"   Extracted {count} assets.\n"
                    state["results"]["asset_list"] = asset_data
                    step_output_data = asset_data
                    step_output_file_path = data.get("output_file", "")
                    agent_files = data.get("files")
                    if data.get("extracted_text"):
                        step_input_data["source_content"] = data["extracted_text"]
                    break
                else:
                    log_message += f"   Agent Error (attempt {_try+1}/{MAX_STEP_RETRIES+1}): {resp.text}\n"
            except Exception as e:
                log_message += f"   Network Error (attempt {_try+1}/{MAX_STEP_RETRIES+1}): {e}\n"
            if _try < MAX_STEP_RETRIES:
                log_message += f"   Retrying in {RETRY_DELAY_SECS}s...\n"; time.sleep(RETRY_DELAY_SECS)
            else:
                step_failed = True; log_message += "   Step failed after all retries.\n"

    # B. CONTENT REVIEWER AGENT (Port 8085)
    elif "content" in resource_key and "reviewer" in resource_key:
        if not file:
            return {"status": "error", "log": log_message + "   ERROR: File required!", "step_index": idx}

        log_message += f"   Sending to Content Reviewer (Port 8085)...\n"
        step_input_data = {"file": file_name, "instruction": step.description}

        form_data = {
            "instruction": step.description,
            "validation_criteria": step.validation.criteria if step.validation else ""
        }

        for _try in range(MAX_STEP_RETRIES + 1):
            try:
                files = {'file': (file_name, BytesIO(file_bytes), file_content_type)}
                resp = requests.post(REVIEWER_URL, files=files, data=form_data, timeout=60)
                if resp.status_code == 200:
                    res_json = resp.json()
                    log_message += f"   Success (Model: {res_json.get('used_model', res_json.get('model'))})\n"
                    log_message += f"   Result: {str(res_json.get('result'))[:100]}...\n"
                    state["results"]["legend"] = res_json.get('result')
                    step_output_data = res_json.get('result')
                    step_output_file_path = res_json.get("output_file", "")
                    agent_files = res_json.get("files")
                    if res_json.get("extracted_text"):
                        step_input_data["source_content"] = res_json["extracted_text"]
                    break
                else:
                    log_message += f"   Agent Error (attempt {_try+1}/{MAX_STEP_RETRIES+1}): {resp.text}\n"
            except Exception as e:
                log_message += f"   Network Error (attempt {_try+1}/{MAX_STEP_RETRIES+1}): {e}\n"
            if _try < MAX_STEP_RETRIES:
                log_message += f"   Retrying in {RETRY_DELAY_SECS}s...\n"; time.sleep(RETRY_DELAY_SECS)
            else:
                step_failed = True; log_message += "   Step failed after all retries.\n"

    # C. DOCUMENT REVIEWER AGENT (Port 8089)
    elif "document" in resource_key and "reviewer" in resource_key:
        tool_id = ""
        if step.required_resources and step.required_resources.tool_id:
            tool_id = step.required_resources.tool_id.lower()

        # C1. Processing Plan step — uses /plan_processing
        if "plan" in tool_id:
            log_message += f"   Sending to Document Reviewer /plan_processing (Port 8089)...\n"

            doc_review = state["results"].get("document_review", {})
            step1_output_file = doc_review.get("output_file", "")
            step_input_data = {"step1_output_file": step1_output_file}

            if not step1_output_file:
                log_message += "   ERROR: No Step 1 output file found in state. Run Step 1 first.\n"
                step_failed = True
            else:
                # Pass process contexts loaded in prior steps so the planner can apply categorisation rules
                proc_contexts = state["results"].get("process_contexts", {})
                if proc_contexts:
                    log_message += f"   Passing {len(proc_contexts)} process context(s) to planner.\n"

                plan_job_key    = state["results"].get("job_key")
                plan_output_dir = str(OUTPUT_DIR / plan_job_key) if plan_job_key else None

                for _try in range(MAX_STEP_RETRIES + 1):
                    try:
                        resp = requests.post(
                            DOC_PLAN_URL,
                            json={
                                "step1_output_file": step1_output_file,
                                "process_contexts": proc_contexts,
                                "output_dir": plan_output_dir,
                            },
                            timeout=120,
                        )
                        if resp.status_code == 200:
                            res_json = resp.json()
                            plan = res_json.get("processing_plan", [])
                            log_message += f"   Processing plan created for {len(plan)} document(s).\n"
                            for entry in plan:
                                conv = " [CONVERSION REQUIRED: " + entry.get("conversion_type", "") + "]" if entry.get("conversion_required") else ""
                                log_message += (
                                    f"   · {entry.get('filename')} → tool: {entry.get('processing_tool_id')}"
                                    f" | method: {entry.get('extraction_method')}{conv}\n"
                                )
                            state["results"]["processing_plan"] = res_json
                            step_output_data = res_json
                            step_output_file_path = res_json.get("output_file", "")
                            agent_files = res_json.get("files")
                            break
                        else:
                            log_message += f"   Agent Error (attempt {_try+1}/{MAX_STEP_RETRIES+1}): {resp.text}\n"
                    except Exception as e:
                        log_message += f"   Network Error (attempt {_try+1}/{MAX_STEP_RETRIES+1}): {e}\n"
                    if _try < MAX_STEP_RETRIES:
                        log_message += f"   Retrying in {RETRY_DELAY_SECS}s...\n"; time.sleep(RETRY_DELAY_SECS)
                    else:
                        step_failed = True; log_message += "   Step failed after all retries.\n"

        # C2. Document Review step — uses /review
        else:
            log_message += f"   Sending to Document Reviewer /review (Port 8089)...\n"

            active_folder = folder_path or DOCUMENTS_FOLDER
            search_context = step.description

            # Enrich search_context from process knowledge files (comma-separated knowledge_id)
            review_knowledge_id = ""
            if step.required_resources and hasattr(step.required_resources, "knowledge_id"):
                review_knowledge_id = getattr(step.required_resources, "knowledge_id", "") or ""
            if review_knowledge_id:
                for kid in [k.strip() for k in review_knowledge_id.split(",") if k.strip()]:
                    try:
                        proc_file = PROCESS_DIR / f"{kid}.json"
                        if not proc_file.exists():
                            for pf in PROCESS_DIR.glob("*.json"):
                                with open(pf) as _f:
                                    _d = json.load(_f)
                                if _d.get("process_id") == kid:
                                    proc_file = pf
                                    break
                        if proc_file.exists():
                            with open(proc_file) as _f:
                                proc_data = json.load(_f)
                            proc_name = proc_data.get("process_name", kid)
                            sc = proc_data.get("search_context", "")
                            dc = proc_data.get("document_categorisation", {})
                            if sc:
                                search_context += f"\n\n=== Knowledge: {proc_name} ===\nSearch Context: {sc}"
                            if dc:
                                category = dc.get("category", "")
                                pos_fn = dc.get("positive_indicators", {}).get("filename_patterns", [])
                                neg_fn = dc.get("negative_indicators", {}).get("filename_patterns", [])
                                doc_types = dc.get("positive_indicators", {}).get("document_type_values", [])
                                search_context += (
                                    f"\nDocument Category '{category}': "
                                    f"Positive filename patterns: {pos_fn}. "
                                    f"Document type values: {doc_types}. "
                                    f"Negative filename patterns (exclude from this category): {neg_fn}."
                                )
                            log_message += f"   Loaded search context from knowledge: {proc_name}\n"
                            # Store process doc in state so later steps (e.g. plan_processing) can use it
                            state["results"].setdefault("process_contexts", {})[kid] = proc_data
                    except Exception as e:
                        log_message += f"   Warning: Could not load knowledge {kid}: {e}\n"

            step_input_data = {"folder_path": active_folder, "search_context": search_context}

            # Create a shared job directory scoped to this application folder + timestamp
            if "job_key" not in state["results"]:
                job_key = _make_job_key(active_folder)
                state["results"]["job_key"] = job_key
                job_out_dir = OUTPUT_DIR / job_key
                try:
                    job_out_dir.mkdir(parents=True, exist_ok=True)
                    log_message += f"   Job directory: {job_out_dir}\n"
                except Exception as e:
                    log_message += f"   Warning: Could not create job directory {job_out_dir}: {e}\n"

            job_key    = state["results"].get("job_key")
            output_dir = str(OUTPUT_DIR / job_key) if job_key else None

            for _try in range(MAX_STEP_RETRIES + 1):
                try:
                    resp = requests.post(
                        DOC_REVIEWER_URL,
                        json={"folder_path": active_folder, "search_context": search_context,
                              "output_dir": output_dir},
                        timeout=120,
                    )
                    if resp.status_code == 200:
                        res_json = resp.json()
                        total   = res_json.get("total_files_scanned", 0)
                        matched = res_json.get("matched_files", 0)
                        docs    = res_json.get("documents", [])
                        log_message += f"   Scanned {total} files, {matched} matched, {len(docs)} documents identified.\n"
                        for doc in docs:
                            log_message += f"   · {doc.get('filename')} → {doc.get('document_type')} (confidence: {doc.get('confidence')})\n"
                        state["results"]["document_review"] = res_json
                        state["results"]["documents"] = docs
                        step_output_data = res_json
                        step_output_file_path = res_json.get("output_file", "")
                        agent_files = res_json.get("files")
                        break
                    else:
                        log_message += f"   Agent Error (attempt {_try+1}/{MAX_STEP_RETRIES+1}): {resp.text}\n"
                except Exception as e:
                    log_message += f"   Network Error (attempt {_try+1}/{MAX_STEP_RETRIES+1}): {e}\n"
                if _try < MAX_STEP_RETRIES:
                    log_message += f"   Retrying in {RETRY_DELAY_SECS}s...\n"; time.sleep(RETRY_DELAY_SECS)
                else:
                    step_failed = True; log_message += "   Step failed after all retries.\n"

    # D. MAPPING AGENT (Port 8087)
    elif "mapper" in resource_key:
        log_message += f"   Sending to Mapping Agent (Port 8087)...\n"

        asset_ctx = state["results"].get("asset_list", {})
        legend_ctx = state["results"].get("legend", {})
        step_input_data = {"asset_list": asset_ctx, "legend": legend_ctx}

        form_data = {
            "asset_list_json": json.dumps(asset_ctx),
            "legend_json": json.dumps(legend_ctx)
        }

        for _try in range(MAX_STEP_RETRIES + 1):
            try:
                resp = requests.post(MAPPER_URL, data=form_data, timeout=30)
                if resp.status_code == 200:
                    res_json = resp.json()
                    asset_map_data = res_json.get("result", {})
                    summary = res_json.get("summary", {})
                    matched = summary.get("matched", 0)
                    total = summary.get("total_assets", 0)
                    exact = summary.get("exact_matches", 0)
                    fuzzy = summary.get("fuzzy_matches", 0)
                    fuzzy_details = summary.get("fuzzy_details", [])
                    unmatched = summary.get("unmatched_assets", [])
                    if fuzzy_details:
                        log_message += f"   Fuzzy matches: {fuzzy_details}\n"
                    if unmatched:
                        log_message += f"   {len(unmatched)} asset(s) have no legend match: {unmatched}\n"
                    log_message += f"   Mapped {matched}/{total} assets ({exact} exact, {fuzzy} fuzzy).\n"
                    log_message += f"   Asset map saved: {res_json.get('output_file')}\n"
                    log_message += f"   Mapping complete. {total} assets processed.\n"
                    state["results"]["asset_map"] = asset_map_data
                    step_output_data = asset_map_data
                    step_output_file_path = res_json.get("output_file", "")
                    agent_files = res_json.get("files")
                    break
                else:
                    log_message += f"   Agent Error (attempt {_try+1}/{MAX_STEP_RETRIES+1}): {resp.text}\n"
            except Exception as e:
                log_message += f"   Network Error (attempt {_try+1}/{MAX_STEP_RETRIES+1}): {e}\n"
            if _try < MAX_STEP_RETRIES:
                log_message += f"   Retrying in {RETRY_DELAY_SECS}s...\n"; time.sleep(RETRY_DELAY_SECS)
            else:
                step_failed = True; log_message += "   Step failed after all retries.\n"

    # D. VERIFICATION AGENT (Port 8086)
    elif "verification" in resource_key:
        log_message += f"   Routing to Verification Agent (Port 8086)...\n"

        asset_map_data = state["results"].get("asset_map", {})
        legend_ctx = state["results"].get("legend", {})
        mapped_assets = asset_map_data.get("asset_map", [])
        step_input_data = {"asset_map": mapped_assets, "legend": legend_ctx}

        payload_data = {
            "asset_list_json": json.dumps(mapped_assets),
            "legend_json": json.dumps(legend_ctx)
        }

        for _try in range(MAX_STEP_RETRIES + 1):
            try:
                if file_bytes:
                    files = {'drawing': (file_name, BytesIO(file_bytes), file_content_type)}
                    resp = requests.post(VERIF_URL, data=payload_data, files=files, timeout=180)
                else:
                    resp = requests.post(VERIF_URL, data=payload_data, timeout=10)

                if resp.status_code == 200:
                    res_json = resp.json()
                    if res_json.get("status") == "interaction_required":
                        log_message += f"   Paused: {res_json.get('message')}\n"
                        return {
                            "status": "paused",
                            "action": "request_file_upload",
                            "message": res_json.get("message"),
                            "log": log_message,
                            "step_index": idx
                        }
                    log_message += f"   Verification Complete. Report Generated.\n"
                    state["results"]["verification_report"] = res_json
                    step_output_data = res_json
                    step_output_file_path = res_json.get("output_file", "")
                    agent_files = res_json.get("files")
                    break
                else:
                    log_message += f"   Agent Error (attempt {_try+1}/{MAX_STEP_RETRIES+1}): {resp.text}\n"
            except Exception as e:
                log_message += f"   Network Error (attempt {_try+1}/{MAX_STEP_RETRIES+1}): {e}\n"
            if _try < MAX_STEP_RETRIES:
                log_message += f"   Retrying in {RETRY_DELAY_SECS}s...\n"; time.sleep(RETRY_DELAY_SECS)
            else:
                step_failed = True; log_message += "   Step failed after all retries.\n"

    # E. DOCUMENT CHUNKER AGENT (Port 8091)
    elif "chunker" in resource_key:
        log_message += f"   Routing to Document Chunker (Port 8091)...\n"

        processing_plan = state["results"].get("processing_plan", {})
        plan_entries    = processing_plan.get("processing_plan", [])
        doc_review      = state["results"].get("document_review", {})
        docs_folder     = doc_review.get("folder_path", DOCUMENTS_FOLDER)

        chunk_results    = {}
        docs_not_chunked = []
        # Include the full plan content so the validator can verify chunking requirements
        step_input_data  = {
            "processing_plan_file":    processing_plan.get("output_file", ""),
            "total_documents_in_plan": len(plan_entries),
            "documents_requiring_chunking": [
                {
                    "filename":       e.get("filename"),
                    "page_size":      e.get("page_size"),
                    "chunk_strategy": e.get("chunk_strategy"),
                    "estimated_chunks": e.get("estimated_chunks"),
                }
                for e in plan_entries if e.get("requires_chunking")
            ],
            "documents_not_requiring_chunking": [
                e.get("filename") for e in plan_entries if not e.get("requires_chunking")
            ],
        }

        for entry in plan_entries:
            filename = entry.get("filename", "")
            if not entry.get("requires_chunking"):
                docs_not_chunked.append(filename)
                continue

            filepath = str(Path(docs_folder) / filename)
            if not os.path.exists(filepath):
                log_message += f"   WARNING: File not found for chunking: {filename}\n"
                docs_not_chunked.append(filename)
                continue

            strategy = entry.get("chunk_strategy", "page-split")
            psize    = entry.get("page_size", "A4")
            log_message += f"   Chunking: {filename} | strategy={strategy}, page_size={psize}\n"

            chunk_job_key   = state.get("results", {}).get("job_key")
            chunk_form_data = {"chunk_strategy": strategy, "page_size": psize, "dpi": "200"}
            if chunk_job_key:
                chunk_form_data["output_base"] = f"/app/OUTPUT/{chunk_job_key}/chunks"

            for _try in range(MAX_STEP_RETRIES + 1):
                try:
                    with open(filepath, "rb") as fh:
                        resp = requests.post(
                            CHUNKER_URL,
                            files={"file": (filename, fh, "application/pdf")},
                            data=chunk_form_data,
                            timeout=180,
                        )
                    if resp.status_code == 200:
                        rj = resp.json()
                        chunk_results[filename] = rj.get("manifest", {})
                        log_message += f"   {filename} → {rj.get('total_chunks', 0)} chunk(s)\n"
                        break
                    else:
                        log_message += f"   Chunker error (attempt {_try+1}): {resp.text}\n"
                except Exception as e:
                    log_message += f"   Chunker exception (attempt {_try+1}): {e}\n"
                if _try < MAX_STEP_RETRIES:
                    log_message += f"   Retrying in {RETRY_DELAY_SECS}s...\n"; time.sleep(RETRY_DELAY_SECS)
                else:
                    log_message += f"   Chunker failed for {filename}, proceeding without chunks.\n"
                    docs_not_chunked.append(filename)

        state["results"]["chunk_manifests"] = chunk_results
        step_output_data = {
            "total_documents_chunked": len(chunk_results),
            "documents_not_chunked":   docs_not_chunked,
            "chunked_documents":       chunk_results,
        }
        log_message += (
            f"   Chunking complete: {len(chunk_results)} document(s) chunked, "
            f"{len(docs_not_chunked)} not required.\n"
        )

    # F. DOCUMENT EXTRACTOR AGENT (Port 8090)
    elif "extractor" in resource_key:

        # E1. Parallel group — run all same-group steps concurrently
        if step.parallel_group:
            all_plan_steps = state["plan"].steps
            group_steps = [s for s in all_plan_steps[idx:] if s.parallel_group == step.parallel_group]
            log_message += f"   Parallel group '{step.parallel_group}': running {len(group_steps)} steps concurrently...\n"

            def _run_group_step(ps):
                return ps.step_number, _run_extractor_step(state, ps)

            loop = asyncio.get_event_loop()
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(group_steps)) as executor:
                futures = [executor.submit(_run_group_step, ps) for ps in group_steps]
                # Use asyncio.wrap_future so the event loop stays responsive (avoids 499s from WSL2)
                parallel_results = await asyncio.gather(*[asyncio.wrap_future(f, loop=loop) for f in futures])

            # Map step_number → result so we can look up per-step data
            parallel_result_map = {}
            for step_num, result in parallel_results:
                log_message += result.get("log", "")
                result_key = f"extraction_step_{step_num}"
                state["results"][result_key] = result.get("data")
                if result.get("failed"):
                    step_failed = True
                if result.get("data"):
                    state["results"]["extraction"] = result["data"]
                parallel_result_map[step_num] = result

            # Validate each parallel step — runs in executor to keep event loop free
            # (each call can block for up to 300s; running inline would cause WSL2 TCP reset)
            loop = asyncio.get_event_loop()
            parallel_validations, val_log = await loop.run_in_executor(
                None, lambda: _validate_parallel_steps(group_steps, parallel_result_map)
            )
            log_message += val_log
            for ps_val in parallel_validations.values():
                state["results"].setdefault("validations", []).append(ps_val)

            # Surface the current (first) parallel step's validation to the frontend response
            validation_result = parallel_validations.get(step.step_number)

            # Build per-step validation map keyed by 0-based step index for the frontend
            if parallel_validations:
                parallel_step_validations = {
                    idx + i: parallel_validations[gs.step_number]
                    for i, gs in enumerate(group_steps)
                    if gs.step_number in parallel_validations
                }

            # Use the current step's result for the outer response (not a random last result)
            current_ps_result = parallel_result_map.get(step.step_number, {})
            if current_ps_result.get("data"):
                step_output_data      = current_ps_result["data"]
                step_output_file_path = current_ps_result.get("output_file", "")
                agent_files           = current_ps_result.get("agent_files")
                step_input_data       = current_ps_result.get("input_data", {})

            # Record which 0-based indices completed so the frontend can mark them all
            parallel_completed_indices = list(range(idx, idx + len(group_steps)))

            # Advance index past all parallel steps (skip step 0 — main loop advances one)
            extra_advance = len(group_steps) - 1
            state["current_step_index"] += extra_advance

        # E2. Single extractor step
        else:
            _step = step
            step_knowledge_id = ""
            if step.required_resources and hasattr(step.required_resources, "knowledge_id"):
                step_knowledge_id = getattr(step.required_resources, "knowledge_id", "") or ""
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: _run_extractor_step(state, _step)
            )
            log_message += result.get("log", "")
            if result.get("failed"):
                step_failed = True
            if result.get("data"):
                state["results"]["extraction"] = result["data"]
                # Also store under a knowledge-specific key so steps don't overwrite each other
                if step_knowledge_id:
                    state["results"][f"extraction_{step_knowledge_id.replace('-', '_')}"] = result["data"]
                # Store dedicated asset extraction result for downstream use
                if step_knowledge_id == "proc-extract-asset-spreadsheet":
                    asset_ext = result["data"].get("asset_extract")
                    if asset_ext:
                        state["results"]["asset_extraction"] = asset_ext
                step_output_data      = result["data"]
                step_output_file_path = result.get("output_file", "")
                agent_files           = result.get("agent_files")
                step_input_data       = result.get("input_data", {})

    # G. REPORT CONSOLIDATOR
    elif "consolidat" in resource_key:
        log_message += f"   Consolidating parallel extraction results...\n"

        # Collect all extraction_step_* results from state
        extraction_results = {
            k: v for k, v in state["results"].items()
            if k.startswith("extraction_step_") and v is not None
        }

        if not extraction_results:
            log_message += "   WARNING: No parallel extraction results found in state.\n"
        else:
            log_message += f"   Found {len(extraction_results)} extraction result(s) to consolidate.\n"

        # Build unified report
        report = {
            "report_type":        "Customer Connections Application Review",
            "generated_at":       datetime.utcnow().isoformat() + "Z",
            "source_extractions": {},
            "summary":            {},
        }

        all_extractions = []
        for key, ext_data in extraction_results.items():
            if not ext_data:
                continue
            step_num = key.replace("extraction_step_", "")
            report["source_extractions"][key] = {
                "total_files":  ext_data.get("total_files", 0),
                "output_file":  ext_data.get("output_file", ""),
                "extractions":  ext_data.get("extractions", []),
            }
            all_extractions.extend(ext_data.get("extractions", []))

        report["summary"]["total_documents_processed"] = len(all_extractions)
        report["summary"]["extraction_keys"]           = list(extraction_results.keys())

        # Save report to job-scoped directory (falls back to OUTPUT_DIR)
        consolidate_job_key = state.get("results", {}).get("job_key")
        consolidate_dir = OUTPUT_DIR / consolidate_job_key if consolidate_job_key else OUTPUT_DIR
        try:
            consolidate_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            consolidate_dir = OUTPUT_DIR
            consolidate_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        report_path = consolidate_dir / f"ConsolidatedReport_{ts}.json"
        with open(report_path, "w") as _f:
            json.dump(report, _f, indent=2, default=str)

        log_message += f"   Consolidated report saved: {report_path.name}\n"
        state["results"]["consolidated_report"] = report
        state["results"]["consolidated_report_path"] = str(report_path)
        step_output_data      = report
        step_output_file_path = str(report_path)

    # H. STANDARD SIMULATION
    else:
        time.sleep(1)
        log_message += "   Step Simulated (No external call).\n"

    # --- EARLY RETURN ON STEP FAILURE (all retries exhausted) ---
    if step_failed:
        stepStatuses_key = f"step_{idx}_retries"
        retry_count = state.setdefault("retry_counts", {}).get(idx, 0)
        state["retry_counts"][idx] = retry_count + 1
        log_message += f"\n[FAILED] Step {step.step_number} failed after {MAX_STEP_RETRIES} retries. You may retry manually.\n"
        return {
            "status":          "error",
            "step_index":      idx,
            "log":             log_message,
            "remaining_steps": state["total_steps"] - idx,
            "step_output":     None,
            "validation":      None,
        }

    # --- FETCH RAW PDF TEXT FOR EXTRACTION VALIDATION ---
    # For extractor steps, pull full page-by-page text from the source PDFs so the
    # validator can do line-by-line cross-referencing against the extraction output.
    # Skip when parallel steps were already individually validated inside the loop.
    if "extractor" in resource_key and step_output_data and not step.parallel_group:
        source_paths = [
            ex.get("filepath") for ex in step_output_data.get("extractions", [])
            if ex.get("filepath")
        ]
        if source_paths:
            try:
                _sp = source_paths  # capture for lambda
                raw_resp = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: requests.post(DOC_RAW_TEXT_URL, json={"filepaths": _sp}, timeout=60),
                )
                if raw_resp.status_code == 200:
                    raw_docs = raw_resp.json().get("documents", {})
                    log_message += f"   Raw text fetched for {len(raw_docs)} source PDF(s) for validation.\n"
                    step_input_data = {**(step_input_data or {}), "source_pdf_raw_text": raw_docs}
                else:
                    log_message += f"   Raw text fetch failed: {raw_resp.status_code}\n"
            except Exception as re:
                log_message += f"   Raw text fetch error: {re}\n"

    # --- INDEPENDENT VALIDATION ---
    # Skip for parallel extractor groups — each step was already validated individually above
    # and validation_result was already set from the parallel loop.
    if validation_result is None and step_output_data and not step.parallel_group:
        log_message += f"   Running independent validation...\n"

        # Build file tuple for the original input file (if one was uploaded)
        input_file_tuple = None
        if file_bytes and file_name:
            input_file_tuple = (file_name, file_bytes, file_content_type)

        _val_kwargs = dict(
            step_name=step.name or f"Step {step.step_number}",
            step_description=step.description,
            input_data=step_input_data,
            output_data=step_output_data,
            validation_criteria=step.validation.criteria if step.validation else "",
            input_file_tuple=input_file_tuple,
            output_file_path=step_output_file_path,
            agent_files=agent_files,
        )
        validation_result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _call_validator(**_val_kwargs)
        )
        if validation_result:
            is_valid = validation_result.get("is_valid", False)
            score = validation_result.get("score", 0)
            summary = validation_result.get("summary", "")
            emoji = "PASS" if is_valid else "WARN"
            log_message += f"   [{emoji}] Validation: {summary} ({score}%)\n"
            files_info = validation_result.get("files_ingested", {})
            if files_info:
                log_message += f"   Files validated: input={files_info.get('input_file', 'None')}, output={files_info.get('output_file', 'None')}\n"
            state["results"].setdefault("validations", []).append(validation_result)
        else:
            log_message += f"   Validator unavailable, skipping.\n"

    # Advance State (Only if we didn't return 'paused' above)
    state["current_step_index"] += 1
    remaining = state["total_steps"] - state["current_step_index"]

    if remaining == 0:
        state["is_complete"] = True
        log_message += "\nPLAN COMPLETED."

    # Determine step output data to send back to the frontend
    step_output = None
    if "asset" in resource_key and "ops" in resource_key:
        step_output = state["results"].get("asset_list")
    elif "document" in resource_key and "reviewer" in resource_key:
        tool_id_check = ""
        if step.required_resources and step.required_resources.tool_id:
            tool_id_check = step.required_resources.tool_id.lower()
        if "plan" in tool_id_check:
            step_output = state["results"].get("processing_plan")
        else:
            step_output = state["results"].get("document_review")
    elif "content" in resource_key and "reviewer" in resource_key:
        step_output = state["results"].get("legend")
    elif "mapper" in resource_key:
        step_output = state["results"].get("asset_map")
    elif "verification" in resource_key:
        step_output = state["results"].get("verification_report")
    elif "extractor" in resource_key:
        step_output = state["results"].get("extraction")
    elif "consolidat" in resource_key:
        step_output = state["results"].get("consolidated_report")

    response = {
        "status": "completed" if state["is_complete"] else "waiting",
        "step_index": state["current_step_index"],
        "log": log_message,
        "remaining_steps": remaining,
        "step_output": step_output,
        "validation": validation_result
    }
    if parallel_completed_indices is not None:
        response["parallel_completed_indices"] = parallel_completed_indices
    if parallel_step_validations is not None:
        response["parallel_step_validations"] = parallel_step_validations
    return response

PROCESS_DIR = Path(__file__).parent / "process"
TASKS_FILE  = Path(__file__).parent / "tasks.json"
OUTPUT_DIR  = Path(__file__).parent / "OUTPUT"

@app.get("/agent_models")
def agent_models():
    """Query each agent's health endpoint and return the model each one is using."""
    endpoints = {
        "agent-document-reviewer":  "http://document-reviewer:8089/health",
        "agent-document-extractor": "http://document-extractor:8090/health",
        "agent-step-validator":     "http://step-validator:8088/health",
    }
    result = {}
    for agent_id, url in endpoints.items():
        try:
            r = requests.get(url, timeout=5)
            data = r.json()
            model = data.get("model") or data.get("model_used") or "unknown"
            result[agent_id] = model
        except Exception:
            result[agent_id] = "unavailable"
    return result


@app.get("/processes")
def list_processes():
    """List all process definitions grouped by digital worker."""
    # Build worker map from tasks.json
    workers_map = {}
    if TASKS_FILE.exists():
        try:
            with open(TASKS_FILE) as f:
                tasks_data = json.load(f)
            for w in tasks_data.get("digital_workers", []):
                workers_map[w["worker_id"]] = {
                    "worker_id":   w["worker_id"],
                    "worker_name": w["name"],
                    "description": w.get("description", ""),
                    "processes":   []
                }
        except Exception:
            pass

    ungrouped = []
    if PROCESS_DIR.exists():
        for f in sorted(PROCESS_DIR.glob("*.json")):
            try:
                with open(f) as fh:
                    data = json.load(fh)
                proc = {
                    "process_id":   data.get("process_id", f.stem),
                    "process_name": data.get("process_name", f.stem),
                    "summary":      data.get("summary", ""),
                    "step_count":   len(data.get("steps", [])),
                    "version":      data.get("version", "1.0"),
                    "filename":     f.name,
                    "worker_id":    data.get("worker_id"),
                    "worker_name":  data.get("worker_name"),
                }
                wid = data.get("worker_id")
                if wid and wid in workers_map:
                    workers_map[wid]["processes"].append(proc)
                else:
                    ungrouped.append(proc)
            except Exception:
                pass

    return {"workers": list(workers_map.values()), "ungrouped": ungrouped}

@app.get("/process/{process_id:path}")
def get_process(process_id: str):
    """Get a specific process definition by process_id or filename stem."""
    if not PROCESS_DIR.exists():
        raise HTTPException(status_code=404, detail="Process directory not found")
    for f in PROCESS_DIR.glob("*.json"):
        try:
            with open(f) as fh:
                data = json.load(fh)
            if data.get("process_id") == process_id or f.stem == process_id:
                return data
        except Exception:
            pass
    raise HTTPException(status_code=404, detail=f"Process '{process_id}' not found")


@app.get("/output_files")
def list_output_files():
    """List all JSON files in the OUTPUT directory, grouped by date."""
    if not OUTPUT_DIR.exists():
        return {"files": []}
    files = []
    for f in OUTPUT_DIR.iterdir():
        if f.suffix == ".json":
            stat = f.stat()
            files.append({
                "filename": f.name,
                "size_bytes": stat.st_size,
                "modified_ts": stat.st_mtime,
            })
    files.sort(key=lambda x: x["filename"], reverse=True)
    return {"files": files}


@app.get("/output_file/{filename}")
def get_output_file(filename: str):
    """Return the parsed JSON content of a specific OUTPUT file."""
    safe = Path(filename).name  # prevent path traversal
    path = OUTPUT_DIR / safe
    if not path.exists() or path.suffix != ".json":
        raise HTTPException(status_code=404, detail="File not found")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/serve_document/{filepath:path}")
def serve_document(filepath: str):
    """Serve a document file (PDF / image) for in-browser preview."""
    path = Path("/" + filepath.lstrip("/"))
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    suffix = path.suffix.lower()
    media_map = {
        ".pdf":  "application/pdf",
        ".png":  "image/png",
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif":  "image/gif",
    }
    if suffix not in media_map:
        raise HTTPException(status_code=400, detail="Preview not supported for this file type")
    return FileResponse(str(path), media_type=media_map[suffix])


# ═══════════════════════════════════════════
#  SERVICE MANAGEMENT
# ═══════════════════════════════════════════

_MANAGED_SERVICES = [
    {"name": "agent04-responder",          "label": "Responder",         "health": "http://responder:8000/tasks"},
    {"name": "agent04-orchestrator",       "label": "Orchestrator",      "health": None},
    {"name": "agent04-document-reviewer",  "label": "Document Reviewer", "health": "http://document-reviewer:8089/health"},
    {"name": "agent04-document-extractor", "label": "Document Extractor","health": "http://document-extractor:8090/health"},
    {"name": "agent04-step-validator",     "label": "Step Validator",    "health": "http://step-validator:8088/health"},
    {"name": "agent04-document-chunker",   "label": "Document Chunker",  "health": "http://document-chunker:8091/health"},
    {"name": "agent04-frontend",           "label": "Frontend",          "health": None},
]


def _ping_health(url: str, timeout: int = 4) -> str:
    """Return 'running' or 'unreachable' by hitting a /health endpoint."""
    try:
        r = requests.get(url, timeout=timeout)
        return "running" if r.status_code == 200 else "error"
    except Exception:
        return "unreachable"


def _docker_action(container_name: str, action: str) -> str:
    """Run docker stop/restart via the SDK inside a thread with a hard timeout.
    Returns a status string or raises on failure."""
    import docker as _docker
    client = _docker.from_env(timeout=8)
    c = client.containers.get(container_name)
    if action == "stop":
        c.stop(timeout=10)
    else:
        c.restart(timeout=10)
    return action + "ed"


@app.get("/services/status")
def services_status():
    """Ping each service health endpoint and return running/unreachable status."""
    result = []
    for svc in _MANAGED_SERVICES:
        health_url = svc.get("health")
        if health_url:
            status = _ping_health(health_url)
        else:
            # orchestrator is self — always running if this endpoint responds
            status = "running"
        result.append({"name": svc["name"], "label": svc["label"], "status": status})
    return {"services": result}


@app.post("/services/{container_name}/stop")
def service_stop(container_name: str):
    """Stop a managed container via Docker SDK."""
    allowed = {s["name"] for s in _MANAGED_SERVICES}
    if container_name not in allowed:
        raise HTTPException(status_code=400, detail="Unknown container")
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_docker_action, container_name, "stop")
            fut.result(timeout=20)
        return {"container": container_name, "action": "stopped"}
    except concurrent.futures.TimeoutError:
        raise HTTPException(status_code=504, detail="Docker operation timed out")
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/services/{container_name}/restart")
def service_restart(container_name: str):
    """Restart a managed container via Docker SDK."""
    allowed = {s["name"] for s in _MANAGED_SERVICES}
    if container_name not in allowed:
        raise HTTPException(status_code=400, detail="Unknown container")
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_docker_action, container_name, "restart")
            fut.result(timeout=25)
        return {"container": container_name, "action": "restarted"}
    except concurrent.futures.TimeoutError:
        raise HTTPException(status_code=504, detail="Docker operation timed out")
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("orchestrator:app", host="0.0.0.0", port=8001, reload=True)
