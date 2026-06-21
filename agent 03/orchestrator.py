import time
import uuid
import requests
import os
import json
from io import BytesIO
from datetime import datetime
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
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

class PlanStep(BaseModel):
    step_number: int
    description: str
    name: Optional[str] = None
    assigned_resource_id: Optional[str] = None
    required_resources: Optional[StepResources] = None
    validation: Optional[StepValidation] = None

class ExecutionRequest(BaseModel):
    plan_overview: str
    steps: List[PlanStep]

# --- Service URLs (configurable for Azure Container Apps) ---
VALIDATOR_URL = os.getenv("VALIDATOR_URL",  "http://step-validator:8088/validate_step")
ASSET_OPS_URL = os.getenv("ASSET_OPS_URL",  "http://asset-ops:8084/extract_assets")
REVIEWER_URL  = os.getenv("REVIEWER_URL",   "http://content-reviewer:8085/review_content")
MAPPER_URL    = os.getenv("MAPPER_URL",     "http://agent-mapper:8087/create_mapping")
VERIF_URL     = os.getenv("VERIF_URL",      "http://agent-verification:8086/verify_assets")

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

        resp = requests.post(VALIDATOR_URL, data=form_data, files=files if files else None, timeout=120)
        if resp.status_code == 200:
            return resp.json().get("validation")
    except Exception as e:
        print(f"[ORCHESTRATOR] Validator call failed: {e}")
    return None

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
async def run_next_step(plan_id: str, file: UploadFile = File(None)):
    if plan_id not in executions:
        raise HTTPException(status_code=404, detail="Plan ID not found")

    state = executions[plan_id]
    if state["is_complete"]:
        return {"status": "completed", "message": "Plan is already finished."}

    idx = state["current_step_index"]
    step: PlanStep = state["plan"].steps[idx]

    # --- Capture file bytes early (before any agent consumes the stream) ---
    file_bytes = None
    file_name = None
    file_content_type = None
    if file:
        file_bytes = await file.read()
        file_name = file.filename
        file_content_type = file.content_type
        await file.seek(0)  # Reset for agent to read

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

    # --- ROUTING LOGIC ---

    # A. ASSET OPS AGENT (Port 8084)
    if "asset" in resource_key and "ops" in resource_key:
        if not file:
            return {"status": "error", "log": log_message + "   ERROR: File required!", "step_index": idx}

        log_message += f"   Sending to Asset Ops (Port 8084)...\n"
        step_input_data = {"file": file_name}
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
                # Include source content for validator
                if data.get("extracted_text"):
                    step_input_data["source_content"] = data["extracted_text"]
            else:
                log_message += f"   Agent Error: {resp.text}\n"
        except Exception as e:
            log_message += f"   Network Error: {e}\n"

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

        try:
            files = {'file': (file_name, BytesIO(file_bytes), file_content_type)}

            resp = requests.post(
                REVIEWER_URL,
                files=files,
                data=form_data,
                timeout=60
            )

            if resp.status_code == 200:
                res_json = resp.json()
                log_message += f"   Success (Model: {res_json.get('used_model', res_json.get('model'))})\n"
                log_message += f"   Result: {str(res_json.get('result'))[:100]}...\n"
                state["results"]["legend"] = res_json.get('result')
                step_output_data = res_json.get('result')
                step_output_file_path = res_json.get("output_file", "")
                agent_files = res_json.get("files")
                # Include source content for validator
                if res_json.get("extracted_text"):
                    step_input_data["source_content"] = res_json["extracted_text"]
            else:
                log_message += f"   Validation Failed or Error: {resp.text}\n"
        except Exception as e:
            log_message += f"   Network Error: {e}\n"

    # C. MAPPING AGENT (Port 8087)
    elif "mapper" in resource_key:
        log_message += f"   Sending to Mapping Agent (Port 8087)...\n"

        asset_ctx = state["results"].get("asset_list", {})
        legend_ctx = state["results"].get("legend", {})
        step_input_data = {"asset_list": asset_ctx, "legend": legend_ctx}

        form_data = {
            "asset_list_json": json.dumps(asset_ctx),
            "legend_json": json.dumps(legend_ctx)
        }

        try:
            resp = requests.post(
                MAPPER_URL,
                data=form_data,
                timeout=30
            )

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
            else:
                log_message += f"   Agent Error: {resp.text}\n"
        except Exception as e:
            log_message += f"   Network Error: {e}\n"

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

        try:
            url = VERIF_URL

            if file_bytes:
                files = {'drawing': (file_name, BytesIO(file_bytes), file_content_type)}
                resp = requests.post(url, data=payload_data, files=files, timeout=180)
            else:
                resp = requests.post(url, data=payload_data, timeout=10)

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
            else:
                log_message += f"   Agent Error: {resp.text}\n"

        except Exception as e:
            log_message += f"   Network Error: {e}\n"

    # E. STANDARD SIMULATION
    else:
        time.sleep(1)
        log_message += "   Step Simulated (No external call).\n"

    # --- INDEPENDENT VALIDATION ---
    validation_result = None
    if step_output_data:
        log_message += f"   Running independent validation...\n"

        # Build file tuple for the original input file (if one was uploaded)
        input_file_tuple = None
        if file_bytes and file_name:
            input_file_tuple = (file_name, file_bytes, file_content_type)

        validation_result = _call_validator(
            step_name=step.name or f"Step {step.step_number}",
            step_description=step.description,
            input_data=step_input_data,
            output_data=step_output_data,
            validation_criteria=step.validation.criteria if step.validation else "",
            input_file_tuple=input_file_tuple,
            output_file_path=step_output_file_path,
            agent_files=agent_files,
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
    elif "content" in resource_key and "reviewer" in resource_key:
        step_output = state["results"].get("legend")
    elif "mapper" in resource_key:
        step_output = state["results"].get("asset_map")
    elif "verification" in resource_key:
        step_output = state["results"].get("verification_report")

    return {
        "status": "completed" if state["is_complete"] else "waiting",
        "step_index": idx + 1,
        "log": log_message,
        "remaining_steps": remaining,
        "step_output": step_output,
        "validation": validation_result
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("orchestrator:app", host="0.0.0.0", port=8001, reload=True)
