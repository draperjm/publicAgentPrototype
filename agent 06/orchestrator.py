import asyncio
import re
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
ANALYTICS_URL       = os.getenv("ANALYTICS_URL",         "http://data-analytics:8092/analyse")
DOCUMENTS_FOLDER    = os.getenv("DOCUMENTS_FOLDER",     "/documents")
MAX_STEP_RETRIES    = 2   # automatic retries per step before marking as failed
RETRY_DELAY_SECS    = 3   # seconds between retries


def _compute_bare_id(asset_id: str) -> str:
    """Strip leading alpha prefix and leading zeros from an asset ID.
    e.g. SLPL00302876 → 302876, CB00045 → 45, TX0001234 → 1234, PO1 → 1"""
    stripped = re.sub(r'^[A-Za-z\-_]+', '', asset_id or "")
    numeric = stripped.lstrip("0")
    return numeric if numeric else (stripped or asset_id or "")


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
    state["pending_task"] = task

    return {"status": "running"}


@app.get("/step_result/{plan_id}")
async def get_step_result(plan_id: str):
    """Poll this endpoint after run_next returns {status: running}.
    Returns {status: running} until the task completes, then the step result."""
    if plan_id not in executions:
        raise HTTPException(status_code=404, detail="Plan ID not found")

    state = executions[plan_id]
    task: Optional[asyncio.Task] = state.get("pending_task")

    if task is None:
        raise HTTPException(status_code=400, detail="No pending task for this plan")

    if not task.done():
        return {"status": "running"}

    # Task is done — clear it and return the result
    state["pending_task"] = None
    try:
        result = task.result()
    except Exception as exc:
        result = {"status": "error", "log": f"Unexpected error: {exc}"}
    return result


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
    step_name_lower = (step.name or "").lower()
    validation_result = None  # Set by validation logic below
    parallel_completed_indices = None  # Filled when a parallel group runs
    parallel_step_validations = None   # {0-based-idx: validation} for each parallel step
    parallel_step_outputs = None       # {0-based-idx: step_output} for each parallel step

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
                            timeout=3600,  # 1 hour — plan_processing inspects all spreadsheet sheets
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
                    "filename":                  e.get("filename"),
                    "page_size":                 e.get("page_size"),
                    "chunk_strategy":            e.get("chunk_strategy"),
                    "estimated_chunks_per_page": e.get("estimated_chunks"),
                    "note":                      "estimated_chunks_per_page is per PDF page; total_chunks = estimated_chunks_per_page × total_pages",
                }
                for e in plan_entries if e.get("requires_chunking")
            ],
            "documents_not_requiring_chunking": [
                e.get("filename") for e in plan_entries if not e.get("requires_chunking")
            ],
        }

        for entry in plan_entries:
            filename = entry.get("filename", "")
            # TAL files (route_to_step=6) must never be chunked regardless of requires_chunking flag
            if not entry.get("requires_chunking") or entry.get("route_to_step") == 6:
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
            chunk_form_data = {"chunk_strategy": strategy, "page_size": psize, "dpi": "300"}
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

            # Build per-step output map so the frontend can show each step's own data
            parallel_step_outputs = {
                idx + i: parallel_result_map[gs.step_number].get("data")
                for i, gs in enumerate(group_steps)
                if gs.step_number in parallel_result_map and parallel_result_map[gs.step_number].get("data")
            }

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
                # Store under extraction_step_N so the consolidator finds it alongside parallel steps
                state["results"][f"extraction_step_{step.step_number}"] = result["data"]
                # Store under a knowledge+step-scoped key so parallel steps with the same
                # knowledge_id (e.g. steps 5 and 6 both use proc-extract-site-plan-info)
                # don't overwrite each other.
                if step_knowledge_id:
                    base_key = f"extraction_{step_knowledge_id.replace('-', '_')}"
                    state["results"][f"{base_key}_step_{step.step_number}"] = result["data"]
                    # Also keep the plain key for backwards-compatible lookups (last writer wins,
                    # but extraction_step_N keys are the authoritative source).
                    state["results"][base_key] = result["data"]
                # Store dedicated asset extraction result for downstream use (Steps 8 & 9)
                if step_knowledge_id == "proc-extract-asset-spreadsheet":
                    asset_ext = result["data"].get("asset_extract")
                    if asset_ext:
                        state["results"]["asset_extraction"] = asset_ext
                    else:
                        log_message += (
                            "   WARNING: Step 7 agent response missing 'asset_extract' key — "
                            "asset_extraction not populated. Steps 8 and 9 will fail to find asset records.\n"
                        )
                step_output_data      = result["data"]
                step_output_file_path = result.get("output_file", "")
                agent_files           = result.get("agent_files")
                step_input_data       = result.get("input_data", {})

    # G. DATA ANALYTICS AGENT (Port 8092)
    elif "analytics" in resource_key:
        log_message += f"   Routing to Data Analytics Agent (Port 8092)...\n"

        # Determine analytics sub-route from knowledge_id
        _analytics_knowledge_id = ""
        if step.required_resources and hasattr(step.required_resources, "knowledge_id"):
            _analytics_knowledge_id = getattr(step.required_resources, "knowledge_id", "") or ""
        _is_cc_review = "customer-connections" in _analytics_knowledge_id

        # ── Shared: pull legend entries from step 6 ───────────────────────────
        legend_extraction = (
            state["results"].get("extraction_proc_extract_site_plan_info_step_6")
            or state["results"].get("extraction_step_6")
            or {}
        )
        legend_entries: list = []
        for ex in (legend_extraction.get("extractions") or []):
            sse = ex.get("sub_step_extractions") or {}
            for v in sse.values():
                arr = v if isinstance(v, list) else (v.get("value") if isinstance(v, dict) else None)
                if isinstance(arr, list):
                    legend_entries.extend(arr)

        # ── Shared: pull asset records from step 7 ────────────────────────────
        asset_extraction = (
            state["results"].get("extraction_proc_extract_asset_spreadsheet")
            or state["results"].get("extraction_step_7")
            or {}
        )
        asset_records: list = []
        for ex in (asset_extraction.get("extractions") or []):
            se = ex.get("step_extraction") or ex.get("sub_step_extractions") or {}
            recs = se.get("asset_records") or []
            asset_records.extend(recs)

        if not asset_records and not _is_cc_review:
            log_message += "   WARNING: No asset records found in state — skipping.\n"
            step_failed = True

        # ── G1. Asset-to-Drawing Symbol Matching ─────────────────────────────
        if not step_failed and "match" in step_name_lower and "symbol" in step_name_lower:
            log_message += (
                f"   Matching {len(asset_records)} assets to site plan symbols "
                f"using {len(legend_entries)} legend entries...\n"
            )
            step_input_data = {"asset_count": len(asset_records), "legend_count": len(legend_entries)}

            # Build bare_id → asset_id lookup
            bare_id_map: dict = {}
            for rec in asset_records:
                bid = _compute_bare_id(rec.get("asset_id", ""))
                bare_id_map[bid] = rec.get("asset_id", "")
            bare_ids_sorted = sorted(bare_id_map.keys(), key=lambda x: x.zfill(10))
            log_message += f"   {len(bare_ids_sorted)} bare IDs to search (sample: {bare_ids_sorted[:5]})\n"

            # Find site plan chunk manifest from step 3
            chunk_manifests = state["results"].get("chunk_manifests", {})
            processing_plan_data = state["results"].get("processing_plan", {})
            plan_entries_all = processing_plan_data.get("processing_plan", [])
            _SP_KW = ("retic", "reticulation", "drawing", "diagram", "network")
            site_plan_entry = None
            for _e in plan_entries_all:
                _cat   = (_e.get("document_category") or "").strip()
                _dtype = (_e.get("document_type") or "").lower()
                if _cat == "Site Plan" or any(k in _dtype for k in _SP_KW):
                    site_plan_entry = _e
                    break
            site_plan_manifest = (
                chunk_manifests.get(site_plan_entry.get("filename", ""))
                if site_plan_entry else None
            )

            sym_matches: list = []  # populated by whichever approach succeeds

            if site_plan_manifest:
                # ── Approach A: Visual chunk search via Document Extractor ──────
                total_chunks = site_plan_manifest.get("total_chunks", 0)
                log_message += (
                    f"   Chunk manifest found — {total_chunks} chunks from "
                    f"'{site_plan_entry.get('filename', '')}'. Using visual extraction.\n"
                )

                legend_compact = json.dumps([
                    {
                        "label":       _le.get("label"),
                        "description": _le.get("description") or _le.get("symbol_description"),
                    }
                    for _le in legend_entries[:60]
                ])

                doc_review  = state["results"].get("document_review", {})
                docs_folder = doc_review.get("folder_path", DOCUMENTS_FOLDER)
                job_key_g1  = state["results"].get("job_key", "")
                output_dir_g1 = str(OUTPUT_DIR / job_key_g1) if job_key_g1 else None

                extractor_payload = {
                    "files": [
                        {
                            "filename":           site_plan_entry.get("filename", ""),
                            "processing_tool_id": "tool-extract-pdf-content",
                            "document_type":      site_plan_entry.get("document_type"),
                            "document_category":  "Site Plan",
                            "content_type":       "visual",
                            "requires_chunking":  True,
                            "chunk_strategy":     site_plan_manifest.get("chunk_strategy", "quadrant-split"),
                            "chunk_manifest":     site_plan_manifest,
                        }
                    ],
                    "process_step": {
                        "step_id":   "match-assets-to-drawing-symbols",
                        "step_name": "Match Assets to Site Plan Drawing Symbols",
                        "details": (
                            "Extract ALL numeric labels visible in each drawing chunk. "
                            "Asset IDs appear as standalone multi-digit numbers adjacent to drawing symbols. "
                            f"Legend entries for symbol matching: {legend_compact}"
                        ),
                        "expected_output": {
                            "description": "All numeric labels visible in this chunk with adjacent symbol info",
                            "fields": {
                                "found_ids": "array of all numeric label strings visible in this chunk",
                            },
                        },
                        "sub_steps": [
                            {
                                "sub_step_id":   "sub-step-find-asset-ids",
                                "sub_step_name": "Find Asset IDs in Drawing Chunk",
                                # "details" (not "description") is read by _extract_chunked_file
                                # for the schema instruction line in the per-chunk vision prompt.
                                "details": (
                                    "Scan this drawing chunk image and extract EVERY numeric label you can see. "
                                    "Asset IDs are standalone multi-digit numbers (4-10 digits) positioned "
                                    "adjacent to drawing symbols (poles, lanterns, pillars, cables, etc.) "
                                    "on the reticulation site plan. "
                                    "Do NOT filter by any expected list — report every number you can read."
                                ),
                                # output_format is used by _extract_chunked_file as the inline schema hint
                                # in the vision prompt so the model knows to return a plain JSON array.
                                # Must NOT start with "JSON array" — that would trigger legend-presence
                                # filtering which is wrong for asset-ID scanning.
                                "output_format": (
                                    '[\"302876\", \"2002180\", \"402679\"]'
                                    " — plain JSON array of numeric ID strings, no wrapper object"
                                ),
                            }
                        ],
                    },
                    "documents_folder": docs_folder,
                    "output_dir":       output_dir_g1,
                }

                for _try in range(MAX_STEP_RETRIES + 1):
                    try:
                        resp = requests.post(DOC_EXTRACTOR_URL, json=extractor_payload, timeout=600)
                        if resp.status_code == 200:
                            res_json = resp.json()
                            # Aggregate found bare IDs across chunk_details (per-chunk results).
                            # The extractor returns a single top-level object with chunk_details[].
                            # Each chunk has extracted.data["sub-step-find-asset-ids"] which may be:
                            #   - list of bare_id strings    e.g. ["302876", "302877"]
                            #   - list of dicts              e.g. [{bare_id, symbol_description, label}]
                            #   - dict wrapper               e.g. {"found_ids": ["302876", ...]}
                            # First occurrence of a bare_id across chunks wins.
                            found_map: dict = {}

                            def _absorb_item(item):
                                if isinstance(item, str):
                                    # Normalize: strip alpha prefix and leading zeros so
                                    # "SLP02002180" and "02002180" both resolve to "2002180"
                                    bid = _compute_bare_id(item.strip()) or item.strip()
                                    if bid and bid not in found_map:
                                        found_map[bid] = {"symbol_description": None, "label": None}
                                elif isinstance(item, dict):
                                    raw = str(
                                        item.get("bare_id") or item.get("id") or item.get("asset_id") or ""
                                    ).strip()
                                    bid = _compute_bare_id(raw) if raw else ""
                                    if bid and bid not in found_map:
                                        found_map[bid] = {
                                            "symbol_description": item.get("symbol_description"),
                                            "label":              item.get("label"),
                                        }

                            # The extractor returns {"extractions": [per_file_result], ...}
                            # chunk_details lives inside extractions[0], not at the top level.
                            # Collect all extraction objects to search: top-level + each extractions[i].
                            _all_ext_objs = [res_json] + (res_json.get("extractions") or [])

                            # Primary: per-chunk data in chunk_details
                            for _eo in _all_ext_objs:
                                for _cd in (_eo.get("chunk_details") or []):
                                    _chunk_data = (_cd.get("extracted") or {}).get("data") or {}
                                    for _cd_val in _chunk_data.values():
                                        # Unwrap {"found_ids": [...]} if model returned a dict wrapper
                                        if isinstance(_cd_val, dict) and "found_ids" in _cd_val:
                                            _cd_val = _cd_val.get("found_ids") or []
                                        _items = _cd_val if isinstance(_cd_val, list) else [_cd_val]
                                        for _item in _items:
                                            _absorb_item(_item)

                            # Secondary: sub_step_extractions.value (aggregated by extractor)
                            for _eo in _all_ext_objs:
                                for _sv in (_eo.get("sub_step_extractions") or {}).values():
                                    _agg_val = _sv.get("value") if isinstance(_sv, dict) else (
                                        _sv if isinstance(_sv, list) else None)
                                    for _item in (_agg_val or []):
                                        _absorb_item(_item)

                            # Filter found_map to only bare IDs that belong to our asset list.
                            # Since we now ask the extractor for ALL visible numbers (not just
                            # the target list), filter out any numbers that aren't asset IDs.
                            _bare_id_set = set(bare_ids_sorted)
                            found_map = {k: v for k, v in found_map.items() if k in _bare_id_set}

                            log_message += (
                                f"   Visual extraction: {len(found_map)}/{len(bare_ids_sorted)} "
                                f"asset IDs located in drawing chunks.\n"
                            )

                            # ── Legend correlation: for found IDs missing symbol/label,
                            # collect raw text context from each chunk and use the
                            # analytics agent to match to the best legend entry. ──────
                            needs_legend = {bid for bid, v in found_map.items()
                                            if not v.get("symbol_description") and not v.get("label")}
                            if needs_legend and legend_entries:
                                # Build bid → list of raw_text snippets (max 2 chunks per ID)
                                context_map: dict = {}
                                for _eo2 in _all_ext_objs:
                                    for _cd in (_eo2.get("chunk_details") or []):
                                        _raw = (_cd.get("extracted") or {}).get("raw_text", "")[:600]
                                        _chunk_data = (_cd.get("extracted") or {}).get("data") or {}
                                        _chunk_bids: set = set()
                                        for _cdv in _chunk_data.values():
                                            for _ci in (_cdv if isinstance(_cdv, list) else [_cdv]):
                                                if isinstance(_ci, str):
                                                    _cb = _ci.strip()
                                                elif isinstance(_ci, dict):
                                                    _cb = str(_ci.get("bare_id", "") or "").strip()
                                                else:
                                                    continue
                                                if _cb in needs_legend:
                                                    _chunk_bids.add(_cb)
                                        for _cb in _chunk_bids:
                                            context_map.setdefault(_cb, [])
                                            if len(context_map[_cb]) < 2:
                                                context_map[_cb].append(_raw)

                                legend_payload = {
                                    "context": (
                                        "Drawing chunk text contexts for found asset IDs on a reticulation site plan. "
                                        "Match each asset's surrounding text to the most appropriate legend entry."
                                    ),
                                    "data": [
                                        {"bare_id": _bid, "chunk_context": " | ".join(context_map.get(_bid, []))}
                                        for _bid in needs_legend
                                    ],
                                    "reference_data": legend_entries,
                                    "tasks": [{
                                        "task_id":   "task-legend-correlation",
                                        "task_name": "Legend Symbol Correlation",
                                        "task_type": "comparison",
                                        "description": (
                                            "For each item in 'data', read the chunk_context text from the site plan "
                                            "drawing and identify which legend entry from 'reference_data' best describes "
                                            "the asset symbol associated with this bare_id. "
                                            "Look for symbol letters, equipment descriptions, or action words "
                                            "(e.g. NEW POLE, EXISTING LANTERN, REMOVE) near the ID in the context. "
                                            "Return one match per bare_id."
                                        ),
                                        "output_format": (
                                            '{"matches": [{"bare_id": "...", '
                                            '"symbol_description": "matching legend symbol description or null", '
                                            '"label": "matching legend label text or null"}]}'
                                        ),
                                    }],
                                }
                                try:
                                    _leg_resp = requests.post(ANALYTICS_URL, json=legend_payload, timeout=120)
                                    if _leg_resp.status_code == 200:
                                        _leg_res = _leg_resp.json()
                                        _leg_task = (_leg_res.get("analytics_results") or {}).get("task-legend-correlation") or {}
                                        _leg_result = _leg_task.get("result") if isinstance(_leg_task, dict) else None
                                        _leg_matches = (_leg_result.get("matches") if isinstance(_leg_result, dict) else None) or []
                                        for _lm in _leg_matches:
                                            if not isinstance(_lm, dict):
                                                continue
                                            _lbid = str(_lm.get("bare_id", "") or "").strip()
                                            if _lbid in found_map:
                                                found_map[_lbid]["symbol_description"] = _lm.get("symbol_description")
                                                found_map[_lbid]["label"]              = _lm.get("label")
                                        log_message += f"   Legend correlation: matched {len(_leg_matches)} found IDs to legend entries.\n"
                                    else:
                                        log_message += f"   Legend correlation skipped (analytics {_leg_resp.status_code}).\n"
                                except Exception as _leg_err:
                                    log_message += f"   Legend correlation error (non-fatal): {_leg_err}\n"

                            # Build sym_matches — one entry per asset record
                            for _rec in asset_records:
                                _aid = _rec.get("asset_id", "")
                                _bid = _compute_bare_id(_aid)
                                if _bid in found_map:
                                    sym_matches.append({
                                        "asset_id":           _aid,
                                        "bare_id":            _bid,
                                        "symbol_description": found_map[_bid]["symbol_description"],
                                        "label":              found_map[_bid]["label"],
                                        "match_status":       "found",
                                    })
                                else:
                                    sym_matches.append({
                                        "asset_id":           _aid,
                                        "bare_id":            _bid,
                                        "symbol_description": None,
                                        "label":              None,
                                        "match_status":       "not_found",
                                    })
                            break
                        else:
                            log_message += f"   Extractor error (attempt {_try+1}/{MAX_STEP_RETRIES+1}): {resp.text[:300]}\n"
                    except Exception as _ex_err:
                        log_message += f"   Network error (attempt {_try+1}/{MAX_STEP_RETRIES+1}): {_ex_err}\n"
                    if _try < MAX_STEP_RETRIES:
                        log_message += f"   Retrying in {RETRY_DELAY_SECS}s...\n"
                        time.sleep(RETRY_DELAY_SECS)
                    else:
                        step_failed = True
                        log_message += "   Step 8 failed after all retries (visual chunk search).\n"

            else:
                # No chunk manifest — mark all assets not_found without calling any agent
                log_message += "   No site plan chunk manifest found — all assets marked not_found.\n"
                for _rec in asset_records:
                    _aid = _rec.get("asset_id", "")
                    sym_matches.append({
                        "asset_id":           _aid,
                        "bare_id":            _compute_bare_id(_aid),
                        "symbol_description": None,
                        "label":              None,
                        "match_status":       "not_found",
                    })

            if not step_failed:
                # ── Shared: merge sym_matches into updated_assets ─────────────
                match_lookup = {m.get("asset_id"): m for m in sym_matches if m.get("asset_id")}
                updated_assets = []
                for rec in asset_records:
                    aid = rec.get("asset_id", "")
                    m = match_lookup.get(aid, {})
                    updated = dict(rec)
                    updated["bare_id"]            = _compute_bare_id(aid)
                    updated["symbol_description"] = m.get("symbol_description")
                    updated["label"]              = m.get("label")
                    updated["match_status"]       = m.get("match_status", "not_found")
                    updated_assets.append(updated)
                log_message += f"   Merged into {len(updated_assets)} updated asset records.\n"

                found_n     = sum(1 for r in updated_assets if r["match_status"] == "found")
                not_found_n = sum(1 for r in updated_assets if r["match_status"] == "not_found")
                match_sum = {
                    "total":     len(updated_assets),
                    "found":     found_n,
                    "not_found": not_found_n,
                }
                log_message += (
                    f"   Symbol match complete — {found_n} found, {not_found_n} not_found "
                    f"of {len(updated_assets)} total.\n"
                )

                step_output = {
                    "updated_assets":       updated_assets,
                    "asset_symbol_matches": sym_matches,
                    "match_summary":        match_sum,
                }

                job_key  = state["results"].get("job_key", "")
                out_dir  = OUTPUT_DIR / job_key if job_key else OUTPUT_DIR
                out_dir.mkdir(parents=True, exist_ok=True)
                ts       = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
                out_path = out_dir / f"AssetSymbolMatches_{ts}.json"
                with open(out_path, "w", encoding="utf-8") as _f:
                    json.dump(step_output, _f, indent=2, default=str)
                log_message += f"   Saved: {out_path.name}\n"

                symbol_match_extraction = {
                    "total_files": 1,
                    "output_file": str(out_path),
                    "extractions": [
                        {
                            "filename":          out_path.name,
                            "document_type":     "Asset Symbol Matches",
                            "document_category": "TAL",
                            "process_step_name": step.name or "Match Assets to Site Plan Drawing Symbols",
                            "total_sections":    len(updated_assets),
                            "relevant_sections": match_sum.get("found", 0),
                            "sub_step_extractions": {
                                "sub-step-asset-symbol-match": {
                                    "updated_assets": updated_assets,
                                    "match_summary":  match_sum,
                                }
                            },
                        }
                    ],
                }
                state["results"]["updated_assets"]                      = updated_assets
                state["results"]["asset_symbol_matches"]                = sym_matches
                state["results"][f"extraction_step_{step.step_number}"] = symbol_match_extraction
                step_output_data      = step_output
                step_output_file_path = str(out_path)

        # ── G3. Customer Connections Review Analysis ──────────────────────────
        elif not step_failed and _is_cc_review:
            # ── Extract structured data from previous steps ───────────────────
            # Step 4: Design Brief — pull specific sub-step sections rather than
            # the raw blob so the LLM receives deterministic, pre-structured inputs.
            db_extraction = (
                state["results"].get("extraction_proc_extract_design_brief_info_step_4")
                or state["results"].get("extraction_proc_extract_design_brief_info")
                or state["results"].get("extraction_step_4")
                or {}
            )
            db_funding_items: list      = []   # from sub-step-extract-funding-determination
            db_payment_instructions: dict = {}  # from sub-step-extract-payment-instructions
            db_supply_requirements: list = []   # from LV / HV / SL sub-steps

            for _ex in (db_extraction.get("extractions") or []):
                _sse = _ex.get("sub_step_extractions") or {}

                # Funding determination — pre-extracted line items with category/amount
                _fund_det = _sse.get("sub-step-extract-funding-determination") or {}
                if isinstance(_fund_det, dict):
                    for _item in (_fund_det.get("items") or []):
                        if isinstance(_item, dict):
                            db_funding_items.append(_item)

                # Payment instructions — pre-extracted payment fields
                _payment = _sse.get("sub-step-extract-payment-instructions") or {}
                if isinstance(_payment, dict) and _payment:
                    db_payment_instructions = _payment

                # LV works / service connections
                _lv = _sse.get("sub-step-extract-lv") or {}
                if isinstance(_lv, dict):
                    for _lv_key in ("LV Works", "Compliance Standards", "Service Connections"):
                        for _item in (_lv.get(_lv_key) or []):
                            _desc = (_item.get("description") if isinstance(_item, dict)
                                     else str(_item))
                            if _desc:
                                db_supply_requirements.append({
                                    "requirement":    _desc,
                                    "source_section": _lv_key,
                                    "source_page":    _item.get("source_page") if isinstance(_item, dict) else None,
                                })

                # HV works — Primary HV Method of Supply + Infrastructure
                _hv = _sse.get("sub-step-extract-hv") or {}
                if isinstance(_hv, dict):
                    _mos = _hv.get("Primary HV Method of Supply")
                    for _item in (_mos if isinstance(_mos, list) else ([_mos] if isinstance(_mos, dict) else [])):
                        _desc = _item.get("description") or str(_item)
                        if _desc:
                            db_supply_requirements.append({
                                "requirement":    _desc,
                                "source_section": "HV Method of Supply",
                                "source_page":    _item.get("source_page"),
                            })
                    for _item in (_hv.get("HV Infrastructure Funding and Materials") or []):
                        _desc = _item.get("description") if isinstance(_item, dict) else str(_item)
                        if _desc:
                            db_supply_requirements.append({
                                "requirement":    _desc,
                                "source_section": "HV Infrastructure",
                                "source_page":    _item.get("source_page") if isinstance(_item, dict) else None,
                            })

                # Street Lighting works
                _sl = _sse.get("sub-step-extract-sl") or {}
                if isinstance(_sl, dict):
                    for _sl_key in ("SL_Works_Section", "SL_Material_Funding"):
                        for _item in (_sl.get(_sl_key) or []):
                            _desc = _item.get("description") if isinstance(_item, dict) else str(_item)
                            if _desc:
                                db_supply_requirements.append({
                                    "requirement":    _desc,
                                    "source_section": _sl_key,
                                    "source_page":    _item.get("source_page") if isinstance(_item, dict) else None,
                                })

            # Step 9: Enriched assets — canonical TAL list with legend matching
            enriched_from_9 = state["results"].get("enriched_assets") or {}
            enriched_assets_list: list = (
                enriched_from_9.get("enriched_assets")
                if isinstance(enriched_from_9, dict) else []
            ) or []
            if not enriched_assets_list:
                enriched_assets_list = state["results"].get("updated_assets") or []

            # Step 10
            consolidated_report_path = state["results"].get("consolidated_report_path", "")

            # ── Build asset register in Python (deterministic — no LLM) ──────
            # The enriched_assets list already has every field needed.
            # action_status is derived from legend_label by simple string matching.
            def _derive_action_status(label: str) -> str:
                ul = (label or "").upper()
                if "NEW"      in ul: return "new"
                if "REMOVE"   in ul: return "remove"
                if "REPLACE"  in ul: return "replace"
                if "EXISTING" in ul: return "existing"
                return "unknown"

            asset_register_built: list = []
            _reg_counts: dict = {
                "total": 0, "new": 0, "remove": 0, "replace": 0,
                "existing": 0, "unknown": 0,
                "found_on_diagram": 0, "not_found": 0,
            }
            for _a in enriched_assets_list:
                _lbl    = _a.get("legend_label") or _a.get("label") or ""
                _conf   = _a.get("match_confidence") or "none"
                _found  = _conf in ("high", "medium")
                _action = _derive_action_status(_lbl)
                asset_register_built.append({
                    "asset_id":          _a.get("asset_id", ""),
                    "tal_asset_id":      _a.get("asset_id", ""),
                    "bare_id":           _a.get("bare_id", ""),
                    "serial_number":     _a.get("serial_number", ""),
                    "asset_type":        _a.get("asset_type", ""),
                    "description":       _a.get("description", ""),
                    "model":             _a.get("model", ""),
                    "source_sheet":      _a.get("source_sheet", ""),
                    "source_document":   _a.get("source_document", ""),
                    "legend_label":      _lbl or None,
                    "legend_category":   _a.get("legend_category"),
                    "symbol_description": _a.get("symbol_description"),
                    "action_status":     _action,
                    "match_confidence":  _conf,
                    "found_on_diagram":  _found,
                })
                _reg_counts["total"]  += 1
                _reg_counts[_action]   = _reg_counts.get(_action, 0) + 1
                _reg_counts["found_on_diagram" if _found else "not_found"] += 1

            asset_register_data = {
                "asset_register":    asset_register_built,
                "register_summary":  _reg_counts,
                "_source":           "enriched_assets_step_9_python_built",
            }

            log_message += (
                f"   CC Review: {len(db_funding_items)} funding items (step 4), "
                f"{len(db_supply_requirements)} supply requirements (step 4), "
                f"{len(legend_entries)} legend entries (step 6), "
                f"{len(enriched_assets_list)} assets in register (built from step 9)...\n"
            )
            step_input_data = {
                "funding_items":          len(db_funding_items),
                "supply_requirements":    len(db_supply_requirements),
                "legend_entries":         len(legend_entries),
                "enriched_assets":        len(enriched_assets_list),
                "asset_register_source":  "python_built_from_enriched_assets_step_9",
            }

            # ── LLM tasks — only comparison/formatting; no re-extraction ─────
            # task-asset-register is now Python-built above (removed from LLM).
            # The 3 remaining tasks receive pre-structured, deterministic inputs.
            payload = {
                "context": (
                    "Customer connections electricity network application review. "
                    "funding_items contains pre-extracted funding line items directly "
                    "from the Design Brief Determination of Funding Requirements section "
                    "(step 4 extraction). "
                    "supply_requirements contains pre-extracted method-of-supply and works "
                    "items from the Design Brief LV, HV and SL sections (step 4 extraction). "
                    "payment_instructions contains pre-extracted payment details from the "
                    "Design Brief (step 4 extraction). "
                    "reference_data contains all legend symbols extracted from the drawing "
                    "(step 6 extraction). "
                    "Each task must only use the specific fields named in its description — "
                    "do not invent, infer or add items beyond the provided lists."
                ),
                "data":                db_funding_items,   # required field; funding items are the primary data
                "funding_items":       db_funding_items,
                "supply_requirements": db_supply_requirements,
                "payment_instructions": db_payment_instructions,
                "reference_data":      legend_entries,
                "tasks": [
                    {
                        "task_id":   "task-funding-requirements",
                        "task_name": "Funding Requirements Consolidation",
                        "task_type": "extraction",
                        "description": (
                            "From the funding_items list (pre-extracted from the Design Brief "
                            "Determination of Funding Requirements section), group each item by "
                            "its funding_category value into: contestable_works, "
                            "non_contestable_works, ancillary_costs, or other_funding_items. "
                            "Use the item_description field as the item name, "
                            "amount_or_formula as the amount, and responsible_party for notes. "
                            "Use payment_instructions for payment method, BSB, account, and "
                            "reference fields. "
                            "Only include items that are present in funding_items — do not "
                            "invent or infer additional items."
                        ),
                        "output_format": (
                            '{"funding_details": {'
                            '  "total_capital_contribution": "string or null",'
                            '  "contestable_works": [{"item": "string", "description": "string", "amount": "string or null", "responsible_party": "string or null"}],'
                            '  "non_contestable_works": [{"item": "string", "description": "string", "amount": "string or null", "responsible_party": "string or null"}],'
                            '  "ancillary_costs": [{"item": "string", "description": "string", "amount": "string or null", "responsible_party": "string or null"}],'
                            '  "other_funding_items": [{"item": "string", "description": "string", "amount": "string or null", "responsible_party": "string or null"}],'
                            '  "payment_instructions": {"method": "string or null", "bsb": "string or null", "account": "string or null", "reference": "string or null"},'
                            '  "notes": ["string"]}}'
                        ),
                    },
                    {
                        "task_id":   "task-supply-scope-comparison",
                        "task_name": "Method of Supply Scope Comparison",
                        "task_type": "comparison",
                        "description": (
                            "Compare each item in supply_requirements (pre-extracted from the "
                            "Design Brief LV, HV and SL sections) against the legend symbols "
                            "in reference_data. "
                            "For each requirement, check whether a corresponding legend symbol "
                            "or label in reference_data would represent that work on a site plan. "
                            "Populate design_brief_requirements with the 'requirement' field of "
                            "each supply_requirements item. "
                            "Populate site_plan_scope_found with entries in the format "
                            "'<requirement>: <matching legend label>' for requirements that have "
                            "a matching legend entry. "
                            "Populate missing_from_site_plan with requirements that have no "
                            "corresponding legend symbol; use 'source_section' and 'source_page' "
                            "as the design_brief_reference. "
                            "Only use items from supply_requirements — do not add requirements "
                            "from other sources."
                        ),
                        "output_format": (
                            '{"method_of_supply_comparison": {'
                            '  "design_brief_requirements": ["string"],'
                            '  "site_plan_scope_found": ["string"],'
                            '  "missing_from_site_plan": [{"item": "string", "design_brief_reference": "string", "note": "string"}],'
                            '  "summary": "string"}}'
                        ),
                    },
                    {
                        "task_id":   "task-funding-arrangement-comparison",
                        "task_name": "Funding Arrangement Scope Comparison",
                        "task_type": "comparison",
                        "description": (
                            "Compare each item in funding_items (pre-extracted from the Design "
                            "Brief Determination of Funding Requirements section) against the "
                            "legend symbols in reference_data. "
                            "For each funding item, check whether a corresponding legend symbol "
                            "or label exists in reference_data that would represent that funded "
                            "work in the drawing. "
                            "Populate design_brief_funded_works with the item_description of "
                            "each funding_items entry. "
                            "Populate drawing_scope_found with entries in the format "
                            "'<item_description>: <matching legend label>' for items that have "
                            "a matching legend entry. "
                            "Populate missing_from_drawing with funding items that have no "
                            "corresponding symbol; use source_section and source_page as the "
                            "design_brief_reference. "
                            "Only use items from funding_items — do not add items from other "
                            "sources."
                        ),
                        "output_format": (
                            '{"funding_arrangement_comparison": {'
                            '  "design_brief_funded_works": ["string"],'
                            '  "drawing_scope_found": ["string"],'
                            '  "missing_from_drawing": [{"item": "string", "design_brief_reference": "string", "note": "string"}],'
                            '  "summary": "string"}}'
                        ),
                    },
                ],
            }

            for _try in range(MAX_STEP_RETRIES + 1):
                try:
                    resp = requests.post(ANALYTICS_URL, json=payload, timeout=300)
                    if resp.status_code == 200:
                        res_json     = resp.json()
                        task_results = res_json.get("analytics_results") or {}

                        funding_details      = (task_results.get("task-funding-requirements") or {}).get("result") or {}
                        supply_comparison    = (task_results.get("task-supply-scope-comparison") or {}).get("result") or {}
                        funding_comparison   = (task_results.get("task-funding-arrangement-comparison") or {}).get("result") or {}
                        # asset_register_data is Python-built above — not from LLM

                        register_summary     = _reg_counts
                        missing_supply       = (supply_comparison.get("method_of_supply_comparison") or {}).get("missing_from_site_plan") or []
                        missing_drawing      = (funding_comparison.get("funding_arrangement_comparison") or {}).get("missing_from_drawing") or []

                        log_message += (
                            f"   CC Review complete — "
                            f"{_reg_counts['total']} assets in register (Python-built), "
                            f"{len(missing_supply)} supply scope gaps, "
                            f"{len(missing_drawing)} drawing scope gaps.\n"
                        )

                        analysis_report = {
                            "report_type":              "Customer Connections Review Analysis Report",
                            "generated_at":             datetime.utcnow().isoformat() + "Z",
                            "consolidated_report_path": consolidated_report_path,
                            "data_sources": {
                                "funding_items":       (
                                    f"{len(db_funding_items)} items — "
                                    "Design Brief sub-step-extract-funding-determination (step 4)"
                                ),
                                "supply_requirements": (
                                    f"{len(db_supply_requirements)} items — "
                                    "Design Brief LV/HV/SL sub-steps (step 4)"
                                ),
                                "legend_symbols":      (
                                    f"{len(legend_entries)} symbols — "
                                    "Drawing legend extraction (step 6)"
                                ),
                                "asset_register":      (
                                    f"{_reg_counts['total']} assets — "
                                    "Enriched TAL spreadsheet (step 9, Python-built)"
                                ),
                            },
                            "analysis_report": {
                                "funding_details":               funding_details,
                                "method_of_supply_comparison":   supply_comparison,
                                "funding_arrangement_comparison": funding_comparison,
                                "asset_register":                asset_register_data,
                            },
                            "summary": {
                                "total_assets":     _reg_counts["total"],
                                "found_on_diagram": _reg_counts["found_on_diagram"],
                                "not_found":        _reg_counts["not_found"],
                                "supply_scope_gaps":  len(missing_supply),
                                "drawing_scope_gaps": len(missing_drawing),
                            },
                        }

                        # ── Content Review Step ──────────────────────────────
                        # Normalize text fields and validate each section before
                        # saving so the frontend can render varied LLM outputs.
                        _review_issues: list[str] = []
                        _section_status: dict[str, str] = {}

                        def _normalize_text_fields(obj: object, path: str) -> None:
                            """Recursively normalize 'summary' fields to plain strings."""
                            if isinstance(obj, dict):
                                for k, v in obj.items():
                                    if k == "summary":
                                        if isinstance(v, list):
                                            obj[k] = " ".join(str(s) for s in v)
                                            _review_issues.append(
                                                f"{path}.summary: list→string"
                                            )
                                        elif v is None:
                                            obj[k] = ""
                                    elif isinstance(v, (dict, list)):
                                        _normalize_text_fields(v, f"{path}.{k}")
                            elif isinstance(obj, list):
                                for i, item in enumerate(obj):
                                    if isinstance(item, (dict, list)):
                                        _normalize_text_fields(item, f"{path}[{i}]")

                        _ar = analysis_report["analysis_report"]
                        for _sec in ["funding_details", "method_of_supply_comparison",
                                     "funding_arrangement_comparison", "asset_register"]:
                            _normalize_text_fields(_ar.get(_sec, {}), _sec)

                        # Section completeness checks
                        _fd = _ar.get("funding_details") or {}
                        _fd_i = _fd.get("funding_details", _fd)
                        _section_status["funding"] = (
                            "ok" if isinstance(_fd_i, dict) and any(
                                _fd_i.get(k) for k in [
                                    "contestable_works", "non_contestable_works",
                                    "ancillary_costs", "other_funding_items",
                                ]
                            ) else "empty"
                        )

                        _sc = _ar.get("method_of_supply_comparison") or {}
                        _sc_i = _sc.get("method_of_supply_comparison", _sc)
                        _section_status["supply_comparison"] = (
                            "ok" if isinstance(_sc_i, dict) and _sc_i.get("summary")
                            else "empty"
                        )

                        _fc = _ar.get("funding_arrangement_comparison") or {}
                        _fc_i = _fc.get("funding_arrangement_comparison", _fc)
                        _section_status["funding_comparison"] = (
                            "ok" if isinstance(_fc_i, dict) and _fc_i.get("summary")
                            else "empty"
                        )

                        _arl = _ar.get("asset_register") or {}
                        _section_status["asset_register"] = (
                            "ok" if isinstance(_arl, dict) and _arl.get("asset_register")
                            else "empty"
                        )

                        analysis_report["_content_review"] = {
                            "reviewed_at":    datetime.utcnow().isoformat() + "Z",
                            "issues":         _review_issues,
                            "section_status": _section_status,
                            "style_complete": all(
                                v == "ok" for v in _section_status.values()
                            ),
                        }
                        if _review_issues:
                            log_message += (
                                f"   Content review: {len(_review_issues)} "
                                f"normalisation(s) applied — {', '.join(_review_issues)}\n"
                            )
                        else:
                            log_message += (
                                f"   Content review: all sections OK "
                                f"({', '.join(f'{k}:{v}' for k,v in _section_status.items())})\n"
                            )
                        # ── End Content Review ────────────────────────────────

                        job_key  = state["results"].get("job_key", "")
                        out_dir  = OUTPUT_DIR / job_key if job_key else OUTPUT_DIR
                        out_dir.mkdir(parents=True, exist_ok=True)
                        ts       = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
                        out_path = out_dir / f"AnalysisReport_{ts}.json"
                        with open(out_path, "w", encoding="utf-8") as _f:
                            json.dump(analysis_report, _f, indent=2, default=str)
                        log_message += f"   Saved: {out_path.name}\n"

                        cc_extraction = {
                            "total_files": 1,
                            "output_file": str(out_path),
                            "extractions": [
                                {
                                    "filename":          out_path.name,
                                    "document_type":     "Customer Connections Review Analysis Report",
                                    "document_category": "Analysis",
                                    "process_step_name": step.name or "Customer Connections Review Analysis",
                                    "total_sections":    4,
                                    "relevant_sections": sum(1 for t in [funding_details, supply_comparison, funding_comparison, asset_register_data] if t),
                                    "sub_step_extractions": {
                                        "task-funding-requirements":           {"value": funding_details},
                                        "task-supply-scope-comparison":        {"value": supply_comparison},
                                        "task-funding-arrangement-comparison": {"value": funding_comparison},
                                        "task-asset-register":                 {"value": asset_register_data, "_source": "python_built"},
                                    },
                                }
                            ],
                        }
                        state["results"]["analysis_report"]                          = analysis_report
                        state["results"]["analysis_report_path"]                     = str(out_path)
                        state["results"][f"extraction_step_{step.step_number}"]      = cc_extraction
                        step_output_data      = analysis_report
                        step_output_file_path = str(out_path)
                        break
                    else:
                        log_message += f"   Agent Error (attempt {_try+1}/{MAX_STEP_RETRIES+1}): {resp.text[:300]}\n"
                except Exception as e:
                    log_message += f"   Network Error (attempt {_try+1}/{MAX_STEP_RETRIES+1}): {e}\n"
                if _try < MAX_STEP_RETRIES:
                    log_message += f"   Retrying in {RETRY_DELAY_SECS}s...\n"
                    time.sleep(RETRY_DELAY_SECS)
                else:
                    step_failed = True
                    log_message += "   Step failed after all retries.\n"

        # ── G2. Asset-Legend Enrichment ───────────────────────────────────────
        elif not step_failed:
            # Use updated_assets from step 8 (already merged with match data).
            # Fall back to asset_records + raw matches if step 8 didn't produce updated_assets.
            updated_assets_from_8 = state["results"].get("updated_assets") or []
            if updated_assets_from_8:
                enhanced_records = [
                    {**rec,
                     "_drawing_symbol":       rec.get("symbol_description"),
                     "_drawing_label":        rec.get("label"),
                     "_drawing_match_status": rec.get("match_status", "not_found")}
                    for rec in updated_assets_from_8
                ]
            else:
                # Fallback: rebuild from raw matches
                raw_symbol_matches = state["results"].get("asset_symbol_matches") or []
                symbol_match_map = {
                    m.get("asset_id"): m for m in raw_symbol_matches if m.get("asset_id")
                }
                enhanced_records = []
                for rec in asset_records:
                    aid = rec.get("asset_id") or ""
                    m = symbol_match_map.get(aid, {})
                    enhanced = dict(rec)
                    enhanced["_drawing_symbol"]       = m.get("symbol_description")
                    enhanced["_drawing_label"]        = m.get("label")
                    enhanced["_drawing_match_status"] = m.get("match_status", "not_found")
                    enhanced_records.append(enhanced)

            found_count = sum(
                1 for rec in enhanced_records if rec.get("_drawing_match_status") == "found"
            )
            log_message += (
                f"   Enriching {len(asset_records)} assets against {len(legend_entries)} legend entries "
                f"({found_count} pre-matched from drawing, "
                f"{len(asset_records) - found_count} require text inference)...\n"
            )
            step_input_data = {
                "asset_count":     len(asset_records),
                "legend_count":    len(legend_entries),
                "drawing_matched": found_count,
                "text_fallback":   len(asset_records) - found_count,
            }

            payload = {
                "context": (
                    "Electricity network asset register combined with symbol matches from a site plan drawing. "
                    "Each asset record has _drawing_symbol, _drawing_label, and _drawing_match_status fields "
                    "from the previous drawing-search step. "
                    "Assets with _drawing_match_status='found' already have their symbol identified from the drawing. "
                    "Assets with _drawing_match_status='not_found' need text-based legend matching as fallback."
                ),
                "data": enhanced_records,
                "reference_data": legend_entries,
                "tasks": [
                    {
                        "task_id":   "task-asset-legend-enrich",
                        "task_name": "Asset-Legend Enrichment",
                        "task_type": "comparison",
                        "description": (
                            "For each asset record in the primary data:\n"
                            "1. If _drawing_match_status is 'found': use _drawing_symbol as symbol_description, "
                            "_drawing_label as label, set match_confidence to 'high'. "
                            "Look up the legend entry whose label matches _drawing_label to get legend_category.\n"
                            "2. If _drawing_match_status is 'not_found': find the best-matching legend entry "
                            "from reference_data by comparing the asset's asset_type and description fields "
                            "against the legend's label field (use fuzzy/partial string matching). "
                            "Set match_confidence to 'medium' for a good text match, 'low' for a weak match, "
                            "or 'none' if no match is found.\n"
                            "Remove the _drawing_symbol, _drawing_label, and _drawing_match_status helper fields "
                            "from the output. Preserve all other original asset fields in every enriched record."
                        ),
                        "output_format": (
                            '{"enriched_assets": ['
                            '  {<all original asset fields minus _drawing_* helpers>, '
                            '   "symbol_description": "string or null", '
                            '   "label": "string or null", '
                            '   "legend_label": "string or null", '
                            '   "legend_category": "string or null", '
                            '   "match_confidence": "high|medium|low|none"}'
                            '], '
                            '"match_summary": {'
                            '  "total": N, "found_on_drawing": N, "inferred": N, "not_found": N'
                            '}}'
                        ),
                    }
                ],
            }

            for _try in range(MAX_STEP_RETRIES + 1):
                try:
                    resp = requests.post(ANALYTICS_URL, json=payload, timeout=300)
                    if resp.status_code == 200:
                        res_json      = resp.json()
                        task_res      = (res_json.get("analytics_results") or {}).get("task-asset-legend-enrich", {})
                        enriched      = task_res.get("result") or {}
                        enriched_list = enriched.get("enriched_assets") or []
                        match_summary = enriched.get("match_summary") or {}
                        log_message += (
                            f"   Enrichment complete — {len(enriched_list)} assets. "
                            f"Summary: {match_summary}\n"
                        )

                        job_key  = state["results"].get("job_key", "")
                        out_dir  = OUTPUT_DIR / job_key if job_key else OUTPUT_DIR
                        out_dir.mkdir(parents=True, exist_ok=True)
                        ts       = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
                        out_path = out_dir / f"AssetLegendEnrichment_{ts}.json"
                        with open(out_path, "w", encoding="utf-8") as _f:
                            json.dump(enriched, _f, indent=2, default=str)
                        log_message += f"   Saved: {out_path.name}\n"

                        enrichment_extraction = {
                            "total_files": 1,
                            "output_file": str(out_path),
                            "extractions": [
                                {
                                    "filename":          out_path.name,
                                    "document_type":     "Asset-Legend Enrichment",
                                    "document_category": "Site Plan",
                                    "process_step_name": step.name or "Enrich Assets with Legend Data",
                                    "total_sections":    len(enriched_list),
                                    "relevant_sections": len(enriched_list),
                                    "sub_step_extractions": {
                                        "sub-step-asset-legend-enrichment": {
                                            "value":         enriched_list,
                                            "match_summary": match_summary,
                                        }
                                    },
                                }
                            ],
                        }
                        state["results"]["enriched_assets"]                          = enriched
                        state["results"][f"extraction_step_{step.step_number}"]      = enrichment_extraction
                        step_output_data      = enriched
                        step_output_file_path = str(out_path)
                        break
                    else:
                        log_message += f"   Agent Error (attempt {_try+1}/{MAX_STEP_RETRIES+1}): {resp.text[:300]}\n"
                except Exception as e:
                    log_message += f"   Network Error (attempt {_try+1}/{MAX_STEP_RETRIES+1}): {e}\n"
                if _try < MAX_STEP_RETRIES:
                    log_message += f"   Retrying in {RETRY_DELAY_SECS}s...\n"
                    time.sleep(RETRY_DELAY_SECS)
                else:
                    step_failed = True
                    log_message += "   Step failed after all retries.\n"

    # H. REPORT CONSOLIDATOR
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
            entry = {
                "total_files":  ext_data.get("total_files", 0),
                "output_file":  ext_data.get("output_file", ""),
                "extractions":  ext_data.get("extractions", []),
            }
            # Include asset extract summary for TAL/spreadsheet steps
            if ext_data.get("asset_extract_file"):
                entry["asset_extract_file"] = ext_data.get("asset_extract_file", "")
                ae = ext_data.get("asset_extract", {})
                entry["asset_extract"] = {
                    "total_assets":     ae.get("total_assets", 0),
                    "source_documents": ae.get("source_documents", []),
                    "output_file":      ae.get("output_file", ""),
                }
            report["source_extractions"][key] = entry
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

        # Trim oversized fields from extractor step data before sending to validator.
        # sub_steps and full asset_records bloat the prompt beyond LLM context limits.
        val_input_data  = step_input_data
        val_output_data = step_output_data
        val_output_file = step_output_file_path
        is_asset_spreadsheet = "extractor" in resource_key and isinstance(val_output_data, dict) and "asset_extract" in val_output_data
        if "extractor" in resource_key:
            # Strip sub_steps from process_step (verbose instructions not needed for validation)
            if isinstance(val_input_data, dict) and "process_step" in val_input_data:
                ps = val_input_data["process_step"]
                if ps.get("sub_steps"):
                    val_input_data = {**val_input_data, "process_step": {k: v for k, v in ps.items() if k != "sub_steps"}}
            if is_asset_spreadsheet:
                # Strip asset_records from output data — keep only counts and metadata.
                # Validator needs to verify structure and totals, not all individual records.
                def _slim_extractions(exts):
                    slimmed = []
                    for ex in exts:
                        se = ex.get("step_extraction", {})
                        records = se.get("asset_records", [])
                        slim_se = {**se, "asset_records": records[:3], "total_assets": len(records), "_note": f"{len(records)} records total, showing first 3"}
                        slimmed.append({**ex, "step_extraction": slim_se})
                    return slimmed
                ae = val_output_data["asset_extract"]
                slim_ae = {k: v for k, v in ae.items() if k != "asset_records"}
                slim_ae["total_assets"] = ae.get("total_assets", 0)
                val_output_data = {
                    **{k: v for k, v in val_output_data.items() if k not in ("extractions", "asset_extract", "process_step")},
                    "total_files": val_output_data.get("total_files"),
                    "asset_extract": slim_ae,
                    "asset_extract_file": val_output_data.get("asset_extract_file", ""),
                    "extractions": _slim_extractions(val_output_data.get("extractions", [])),
                }
                # Don't pass the raw ExtractionReport file — it contains all records and the
                # full process_step blob, which pushes the prompt over the LLM context limit.
                val_output_file = ae.get("output_file", "")  # pass AssetExtract file instead

        _val_kwargs = dict(
            step_name=step.name or f"Step {step.step_number}",
            step_description=step.description,
            input_data=val_input_data,
            output_data=val_output_data,
            validation_criteria=step.validation.criteria if step.validation else "",
            input_file_tuple=input_file_tuple,
            output_file_path=val_output_file,
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

            # ── Step 8: inject asset drawing match visualisation into TestReport ──
            if "match" in step_name_lower and "symbol" in step_name_lower:
                _report_file = validation_result.get("test_report_file")
                _updated = (step_output_data or {}).get("updated_assets") or []
                if _report_file and _updated:
                    try:
                        with open(_report_file, encoding="utf-8") as _rf:
                            _tr = json.load(_rf)
                        _viz_rows = [
                            {
                                "asset_id":          _r.get("asset_id", ""),
                                "asset_type":        _r.get("asset_type", ""),
                                "description":       _r.get("description", ""),
                                "label":             _r.get("label"),
                                "symbol_description": _r.get("symbol_description"),
                                "found":             _r.get("match_status") == "found",
                            }
                            for _r in _updated
                        ]
                        _ms = (step_output_data or {}).get("match_summary", {})
                        _tr["asset_drawing_match"] = {
                            "total":     _ms.get("total", len(_viz_rows)),
                            "found":     _ms.get("found", 0),
                            "not_found": _ms.get("not_found", len(_viz_rows)),
                            "assets":    _viz_rows,
                        }
                        with open(_report_file, "w", encoding="utf-8") as _rf:
                            json.dump(_tr, _rf, indent=2, ensure_ascii=False)
                        log_message += f"   Asset drawing match visualisation added to TestReport.\n"
                    except Exception as _ve:
                        log_message += f"   WARNING: Could not augment TestReport with visualisation: {_ve}\n"
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
    elif "chunker" in resource_key:
        raw_manifests = state["results"].get("chunk_manifests", {})
        step_output = {
            "chunk_summary": True,
            "total_documents_chunked": len(raw_manifests),
            "documents": [
                {
                    "filename":      fname,
                    "page_size":     m.get("page_size", ""),
                    "chunk_strategy": m.get("chunk_strategy", ""),
                    "total_pages":   m.get("total_pages", 0),
                    "total_chunks":  m.get("total_chunks", 0),
                    "output_directory": m.get("output_directory", ""),
                    "chunks": [
                        {
                            "sequence": c.get("sequence"),
                            "page_number": c.get("page_number"),
                            "region":    c.get("region", ""),
                            "filename":  c.get("filename", ""),
                            "filepath":  c.get("filepath", ""),
                            "width_px":  c.get("width_px"),
                            "height_px": c.get("height_px"),
                        }
                        for c in m.get("chunks", [])
                    ],
                }
                for fname, m in raw_manifests.items()
            ],
        }
    elif "extractor" in resource_key:
        # For parallel groups, step_output_data was set to the current (leader) step's data,
        # not the last-write winner in state["results"]["extraction"]. Use it directly.
        step_output = step_output_data if step_output_data else state["results"].get("extraction")
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
    if parallel_step_outputs is not None:
        response["parallel_step_outputs"] = parallel_step_outputs
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
    """List all JSON files in the OUTPUT directory, including job subdirectories."""
    if not OUTPUT_DIR.exists():
        return {"files": []}
    files = []
    for f in OUTPUT_DIR.rglob("*.json"):
        stat = f.stat()
        # Return path relative to OUTPUT_DIR so the frontend can request it
        rel = f.relative_to(OUTPUT_DIR)
        files.append({
            "filename": str(rel),
            "size_bytes": stat.st_size,
            "modified_ts": stat.st_mtime,
        })
    files.sort(key=lambda x: x["modified_ts"], reverse=True)
    return {"files": files}


@app.get("/output_file/{filename:path}")
def get_output_file(filename: str):
    """Return the parsed JSON content of a specific OUTPUT file."""
    # Resolve relative path under OUTPUT_DIR, preventing traversal outside it
    path = (OUTPUT_DIR / filename).resolve()
    if not str(path).startswith(str(OUTPUT_DIR.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")
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
    {"name": "agent06-responder",          "label": "Responder",         "health": "http://responder:8000/tasks"},
    {"name": "agent06-orchestrator",       "label": "Orchestrator",      "health": None},
    {"name": "agent06-document-reviewer",  "label": "Document Reviewer", "health": "http://document-reviewer:8089/health"},
    {"name": "agent06-document-extractor", "label": "Document Extractor","health": "http://document-extractor:8090/health"},
    {"name": "agent06-step-validator",     "label": "Step Validator",    "health": "http://step-validator:8088/health"},
    {"name": "agent06-document-chunker",   "label": "Document Chunker",  "health": "http://document-chunker:8091/health"},
    {"name": "agent06-frontend",           "label": "Frontend",          "health": None},
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
