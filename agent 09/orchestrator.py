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
    repeat_runs: Optional[int] = None  # If > 1, step executes N times with identical input for variance testing

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
ANALYTICS_URL           = os.getenv("ANALYTICS_URL",           "http://data-analytics:8092/analyse")
VARIANCE_VALIDATOR_URL  = os.getenv("VARIANCE_VALIDATOR_URL",  "http://variance-validator:8093/validate_variance")
DOCUMENTS_FOLDER    = os.getenv("DOCUMENTS_FOLDER",     "/documents")
MAX_STEP_RETRIES    = 2   # automatic retries per step before marking as failed
RETRY_DELAY_SECS    = 3   # seconds between retries


async def _async_post(url: str, **kwargs):
    """Run requests.post in a thread-pool executor so blocking I/O never stalls the event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: requests.post(url, **kwargs))


async def _async_sleep(secs: float):
    """Async-friendly sleep — use instead of time.sleep inside async functions."""
    await asyncio.sleep(secs)


_LEGEND_KEYS = frozenset({'page', 'symbol_description', 'label', 'category'})

def _strip_legend_extra_fields(result: dict) -> dict:
    """Strip non-permitted keys from sub-step-extract-legend entries in an extractor result.

    The model frequently adds extra metadata fields (label_text, label_position, bounding_box,
    etc.) despite prompt-level prohibition. This enforces the schema in code by removing any
    key that is not in {page, symbol_description, label, category}.

    Mutates result['data'] in-place and rewrites the saved output file so that the variance
    validator compares clean, normalized data.
    """
    data = result.get("data")
    if not isinstance(data, dict):
        return result

    changed = False
    for extraction in (data.get("extractions") or []):
        sse = extraction.get("sub_step_extractions") or {}
        for key, val in list(sse.items()):
            if "legend" not in key.lower():
                continue
            if isinstance(val, list):
                cleaned = [
                    {k: v for k, v in e.items() if k in _LEGEND_KEYS}
                    for e in val if isinstance(e, dict)
                ]
                if cleaned != val:
                    sse[key] = cleaned
                    changed = True
            elif isinstance(val, dict) and "value" in val:
                entries = val["value"]
                if isinstance(entries, list):
                    cleaned = [
                        {k: v for k, v in e.items() if k in _LEGEND_KEYS}
                        for e in entries if isinstance(e, dict)
                    ]
                    if cleaned != entries:
                        val["value"] = cleaned
                        changed = True

    if changed:
        out_file = result.get("output_file", "")
        if out_file:
            try:
                p = Path(out_file)
                if p.exists():
                    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass  # best-effort; variance will use in-memory data either way

    return result


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

# ── Session persistence helpers ────────────────────────────────────────────

def _serialize_results_for_session(results: dict) -> dict:
    """Serialize state["results"] for session storage.
    Large extraction blobs are replaced with file-path references so the session
    file stays manageable; they are reloaded from disk on restore.
    """
    out: dict = {}
    for k, v in results.items():
        if callable(v):
            continue
        if k.startswith("extraction") and isinstance(v, dict) and v.get("output_file"):
            out[k] = {"__file_ref__": v["output_file"]}
        else:
            try:
                json.dumps(v, default=str)  # quick serializability check
                out[k] = v
            except Exception:
                out[k] = str(v)
    return out


def _restore_results_from_session(results_ctx: dict) -> dict:
    """Restore state["results"] from a session context.
    __file_ref__ entries are loaded from disk; missing files yield an empty dict.
    """
    out: dict = {}
    for k, v in results_ctx.items():
        if isinstance(v, dict) and "__file_ref__" in v:
            ref = Path(v["__file_ref__"])
            if ref.exists():
                try:
                    out[k] = json.loads(ref.read_text(encoding="utf-8"))
                    continue
                except Exception:
                    pass
            out[k] = {}
        else:
            out[k] = v
    return out


def _save_session(plan_id: str, state: dict) -> None:
    """Persist the current execution to OUTPUT/{job_key}/session.json.
    Called after each step completes so the file always reflects the latest state.
    No-op until the job directory has been created (after step 1).
    """
    job_key = state["results"].get("job_key")
    if not job_key:
        return
    out_dir = OUTPUT_DIR / job_key
    out_dir.mkdir(parents=True, exist_ok=True)
    session = {
        "session_id":      plan_id,
        "job_key":         job_key,
        "created_at":      state.get("_created_at", datetime.utcnow().isoformat() + "Z"),
        "updated_at":      datetime.utcnow().isoformat() + "Z",
        "is_complete":     state.get("is_complete", False),
        "steps_completed": state["current_step_index"],
        "total_steps":     state["total_steps"],
        "plan_overview":   state["plan"].plan_overview,
        "plan_steps":      [s.dict() for s in state["plan"].steps],
        "folder_path":     state["results"].get("document_review", {}).get("folder_path", ""),
        "per_step_results": state.get("_per_step_results", {}),
        "results_context": _serialize_results_for_session(state["results"]),
    }
    try:
        (out_dir / "session.json").write_text(
            json.dumps(session, indent=2, default=str), encoding="utf-8"
        )
    except Exception as exc:
        print(f"[ORCHESTRATOR] Session save failed: {exc}")


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

def _call_variance_validator(
    step_name: str,
    run_output_files: List[str],
    output_dir: str,
    step_description: str = "",
) -> Optional[dict]:
    """Call the variance validator after a step has been executed multiple times.

    Args:
        run_output_files: List of ExtractionReport file paths, one per run.
        output_dir: Directory where the variance report will be saved.
    """
    try:
        payload = {
            "step_name": step_name,
            "step_description": step_description,
            "run_output_files": run_output_files,
            "output_dir": output_dir,
        }
        resp = requests.post(VARIANCE_VALIDATOR_URL, json=payload, timeout=120)
        if resp.status_code == 200:
            return resp.json()
        print(f"[ORCHESTRATOR] Variance validator HTTP {resp.status_code}: {resp.text[:300]}")
    except Exception as e:
        print(f"[ORCHESTRATOR] Variance validator call failed: {e}")
    return None


def _run_variance_validation(
    group_steps: list,
    parallel_result_map: dict,
    output_dir: Optional[str],
) -> tuple:
    """Blocking helper: call variance validator for any parallel step with repeat_runs > 1.
    Runs in an executor thread to keep the event loop free.

    Returns (log_str: str, variance_by_step_number: dict).
    """
    log_parts: List[str] = []
    variance_by_step: dict = {}

    for ps in group_steps:
        repeat_runs = ps.repeat_runs or 1
        if repeat_runs <= 1:
            continue
        ps_result = parallel_result_map.get(ps.step_number, {})
        run_files = ps_result.get("repeat_run_output_files", [])
        if len(run_files) < 2:
            log_parts.append(
                f"   [VARIANCE] {ps.name}: skipped — fewer than 2 successful run outputs.\n"
            )
            continue
        variance = _call_variance_validator(
            step_name=ps.name or f"Step {ps.step_number}",
            run_output_files=run_files,
            output_dir=output_dir or "",
            step_description=ps.description,
        )
        if variance:
            verdict = variance.get("verdict", "?")
            score   = variance.get("consistency_score", 0)
            summary = variance.get("summary", "")
            log_parts.append(
                f"   [VARIANCE-{verdict}] {ps.name}: {summary} "
                f"(score {score:.0%} across {len(run_files)} runs)\n"
            )
            variance_by_step[ps.step_number] = variance

    return "".join(log_parts), variance_by_step


def _merge_extraction_results(res1: dict, res2: dict) -> dict:
    """Merge two extractor responses from dual-split passes into a single result.
    Notes text is merged by note number (longer version wins). Structured fields prefer
    the most complete (non-NOT-FOUND) value from either pass."""
    import re as _re

    def _merge_notes(t1: str, t2: str) -> str:
        if not t1: return t2 or ""
        if not t2: return t1 or ""
        def _parse(text):
            notes = {}
            # Split on a REQUIRED newline followed by a note number — the \n must
            # be present so "10. " doesn't also split at "0. " inside the same line.
            for part in _re.split(r'(?=\n\d{1,2}\.\s)', "\n" + text.strip()):
                part = part.strip()
                if not part:
                    continue
                m = _re.match(r'^(\d{1,2})\.\s', part)
                if m:
                    notes[int(m.group(1))] = part
            return notes
        d1, d2 = _parse(t1), _parse(t2)
        merged = {**d1}
        for k, v in d2.items():
            if k not in merged or len(v) > len(merged[k]):
                merged[k] = v
        return "\n".join(merged[k] for k in sorted(merged))

    def _better(v1, v2):
        # Use tuple not set — set membership requires hashing, which fails for list values
        # (e.g. legend sub-step returns a list of entries, not a scalar)
        _empty = (None, "", "NOT FOUND", "NOT COMPLETED", "NOT REQUIRED")
        if isinstance(v1, list) and isinstance(v2, list):
            # For list values (legend entries etc.) prefer the longer list
            return v1 if len(v1) >= len(v2) else v2
        if isinstance(v1, list):
            return v1 if v1 else v2
        if isinstance(v2, list):
            return v2 if v2 else v1
        if v1 in _empty: return v2 if v2 not in _empty else v1
        if v2 in _empty: return v1
        return v1 if len(str(v1)) >= len(str(v2)) else v2

    def _merge_extractions(exs1: list, exs2: list) -> list:
        by_name2 = {e.get("filename"): e for e in (exs2 or [])}
        merged = []
        for ex1 in (exs1 or []):
            ex2 = by_name2.get(ex1.get("filename"), {})
            se1 = ex1.get("sub_step_extractions") or {}
            se2 = ex2.get("sub_step_extractions") or {}
            merged_se = dict(se1)
            for key, entry2 in se2.items():
                if key not in merged_se:
                    merged_se[key] = entry2
                    continue
                v1 = (merged_se[key] or {}).get("value")
                v2 = (entry2 or {}).get("value")
                if key == "sub-step-extract-all-notes":
                    new_val = _merge_notes(str(v1 or ""), str(v2 or ""))
                elif isinstance(v1, dict) and isinstance(v2, dict):
                    new_val = {k: _better(v1.get(k), v2.get(k)) for k in set(v1) | set(v2)}
                else:
                    new_val = _better(v1, v2)
                merged_se[key] = {**(merged_se[key] or {}), "value": new_val}
            merged.append({**ex1, "sub_step_extractions": merged_se, "dual_split_merged": True})
        return merged

    if not res1: return res2 or {}
    if not res2: return res1
    merged_exs = _merge_extractions(res1.get("extractions", []), res2.get("extractions", []))
    return {**res1, "extractions": merged_exs, "dual_split_pass_results": [res1, res2]}


def _run_extractor_step(state: dict, step: "PlanStep", phase1_cache: Optional[dict] = None) -> dict:
    """Build payload and call the document extractor. Returns a dict with log, data, output_file, agent_files.
    phase1_cache: optional {filename: chunk_details} from a prior run; passed to the extractor so it
    skips Phase 1 vision OCR and only re-runs Phase 2 text analysis."""
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
            "phase1_cache":          (phase1_cache or {}).get(entry.get("filename")),
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

    # Dual-split: if any file has a dual-split chunk manifest, run two extraction passes
    # (pass 1 = top/bottom chunks, pass 2 = left/right chunks) and merge the results.
    dual_split_files = [
        f for f in files_payload
        if (f.get("chunk_manifest") or {}).get("chunk_strategy") == "dual-split"
    ]
    if dual_split_files:
        def _filter_manifest(manifest: dict, pass_num: int) -> dict:
            filtered = [c for c in manifest.get("chunks", []) if c.get("split_pass") == pass_num]
            return {**manifest, "chunks": filtered, "total_chunks": len(filtered)}

        def _payload_for_pass(pass_num: int) -> dict:
            pass_files = []
            for f in files_payload:
                m = f.get("chunk_manifest") or {}
                if m.get("chunk_strategy") == "dual-split":
                    pass_files.append({**f, "chunk_manifest": _filter_manifest(m, pass_num)})
                else:
                    pass_files.append(f)
            return {**payload, "files": pass_files}

        pass_labels = {1: "top/bottom", 2: "left/right"}
        pass_results = []
        for pass_num in (1, 2):
            p_payload = _payload_for_pass(pass_num)
            for _try in range(MAX_STEP_RETRIES + 1):
                try:
                    resp = requests.post(DOC_EXTRACTOR_URL, json=p_payload, timeout=600)
                    if resp.status_code == 200:
                        rj = resp.json()
                        log += f"   Dual-split pass {pass_num} ({pass_labels[pass_num]}): {rj.get('total_files', 0)} file(s) extracted.\n"
                        pass_results.append(rj)
                        break
                    else:
                        log += f"   Pass {pass_num} error (attempt {_try+1}): {resp.text}\n"
                except Exception as e:
                    log += f"   Pass {pass_num} exception (attempt {_try+1}): {e}\n"
                if _try < MAX_STEP_RETRIES:
                    log += f"   Retrying pass {pass_num} in {RETRY_DELAY_SECS}s...\n"; time.sleep(RETRY_DELAY_SECS)
                else:
                    log += f"   Pass {pass_num} failed after all retries.\n"

        if not pass_results:
            return {"log": log, "data": None, "output_file": "", "agent_files": None, "input_data": {}, "failed": True}
        merged = _merge_extraction_results(pass_results[0], pass_results[1] if len(pass_results) > 1 else None)
        log += f"   Dual-split merge complete ({len(pass_results)} pass(es) combined).\n"
        return {
            "log":         log,
            "data":        merged,
            "output_file": merged.get("output_file", ""),
            "agent_files": merged.get("files"),
            "input_data":  {"files": files_payload, "process_step": process_step_payload},
            "failed":      False,
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
                        step_index: Optional[int] = Form(None),
                        repeat_runs_override: Optional[int] = Form(None)):
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
        _run_step_impl(plan_id, file_bytes, file_name, file_content_type, folder_path, repeat_runs_override)
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


def _run_analytics_g1_step(state: dict, step: "PlanStep") -> dict:
    """Sync helper: G1 Asset-to-Drawing Symbol Matching.

    Reads asset records and legend entries from state, calls the document
    extractor and analytics agent synchronously (no await), builds sym_matches
    and updated_assets, saves AssetSymbolMatches_*.json and an
    ExtractionReport_Step{N}_*.json for variance validation.

    Returns:
        log                    - execution log string
        data                   - {updated_assets, asset_symbol_matches, match_summary}
        output_file            - path to AssetSymbolMatches_*.json
        extraction_report_file - path to ExtractionReport_*.json (for variance)
        sym_matches            - raw match list
        updated_assets         - merged asset list
        match_sum              - {total, found, not_found}
        failed                 - True on unrecoverable error
    """
    import time as _time
    log = f"   [Step {step.step_number}] {step.name}: G1 run...\n"

    # Pull legend entries from step 6
    legend_extraction = (
        state["results"].get("extraction_proc_extract_site_plan_info_step_6")
        or state["results"].get("extraction_step_6")
        or {}
    )
    legend_entries: list = []
    for _ex in (legend_extraction.get("extractions") or []):
        _sse = _ex.get("sub_step_extractions") or {}
        for _v in _sse.values():
            _arr = _v if isinstance(_v, list) else (_v.get("value") if isinstance(_v, dict) else None)
            if isinstance(_arr, list):
                legend_entries.extend(_arr)

    # Pull asset records from step 7
    asset_extraction = (
        state["results"].get("extraction_proc_extract_asset_spreadsheet")
        or state["results"].get("extraction_step_7")
        or {}
    )
    asset_records: list = []
    for _ex in (asset_extraction.get("extractions") or []):
        _se = _ex.get("step_extraction") or _ex.get("sub_step_extractions") or {}
        _recs = _se.get("asset_records") or []
        asset_records.extend(_recs)

    if not asset_records:
        log += "   WARNING: No asset records found — G1 cannot run.\n"
        return {"log": log, "data": None, "output_file": "", "extraction_report_file": "", "failed": True}

    # Build bare_id → asset_id lookup
    bare_id_map: dict = {}
    for _rec in asset_records:
        _bid = _compute_bare_id(_rec.get("asset_id", ""))
        bare_id_map[_bid] = _rec.get("asset_id", "")
    bare_ids_sorted = sorted(bare_id_map.keys(), key=lambda x: x.zfill(10))
    log += f"   {len(bare_ids_sorted)} bare IDs to search (sample: {bare_ids_sorted[:5]})\n"

    # Find site plan chunk manifest
    chunk_manifests      = state["results"].get("chunk_manifests", {})
    processing_plan_data = state["results"].get("processing_plan", {})
    plan_entries_all     = processing_plan_data.get("processing_plan", [])
    _SP_KW               = ("retic", "reticulation", "drawing", "diagram", "network")
    site_plan_entry = None
    for _e in plan_entries_all:
        _cat   = (_e.get("document_category") or "").strip()
        _dtype = (_e.get("document_type") or "").lower()
        if _cat == "Site Plan" or any(_k in _dtype for _k in _SP_KW):
            site_plan_entry = _e
            break
    site_plan_manifest = (
        chunk_manifests.get(site_plan_entry.get("filename", ""))
        if site_plan_entry else None
    )

    sym_matches: list = []

    if site_plan_manifest:
        total_chunks = site_plan_manifest.get("total_chunks", 0)
        log += (
            f"   Chunk manifest found — {total_chunks} chunks from "
            f"'{site_plan_entry.get('filename', '')}'. Using visual extraction.\n"
        )

        legend_compact = json.dumps([
            {"label": _le.get("label"), "description": _le.get("description") or _le.get("symbol_description")}
            for _le in legend_entries[:60]
        ])

        doc_review    = state["results"].get("document_review", {})
        docs_folder   = doc_review.get("folder_path", DOCUMENTS_FOLDER)
        job_key_g1    = state["results"].get("job_key", "")
        output_dir_g1 = str(OUTPUT_DIR / job_key_g1) if job_key_g1 else None

        extractor_payload = {
            "files": [{
                "filename":           site_plan_entry.get("filename", ""),
                "processing_tool_id": "tool-extract-pdf-content",
                "document_type":      site_plan_entry.get("document_type"),
                "document_category":  "Site Plan",
                "content_type":       "visual",
                "requires_chunking":  True,
                "chunk_strategy":     site_plan_manifest.get("chunk_strategy", "quadrant-split"),
                "chunk_manifest":     site_plan_manifest,
            }],
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
                    "fields": {"found_ids": "array of all numeric label strings visible in this chunk"},
                },
                "sub_steps": [{
                    "sub_step_id":   "sub-step-find-asset-ids",
                    "sub_step_name": "Find Asset IDs in Drawing Chunk",
                    "details": (
                        "Scan this drawing chunk image and extract EVERY numeric label you can see. "
                        "Asset IDs are standalone multi-digit numbers (4-10 digits) positioned "
                        "adjacent to drawing symbols (poles, lanterns, pillars, cables, etc.) "
                        "on the reticulation site plan. "
                        "Do NOT filter by any expected list — report every number you can read."
                    ),
                    "output_format": (
                        '[\"302876\", \"2002180\", \"402679\"]'
                        " — plain JSON array of numeric ID strings, no wrapper object"
                    ),
                }],
            },
            "documents_folder": docs_folder,
            "output_dir":       output_dir_g1,
        }

        _total_chunks_manifest = total_chunks

        def _describe_chunk_loc(cd: dict) -> str:
            _page   = cd.get("page_number") or cd.get("page") or 1
            _region = (cd.get("region") or cd.get("quadrant") or "").lower().strip()
            _seq    = cd.get("sequence") or cd.get("chunk_number")
            _rl = {
                "top": "upper section", "bottom": "lower section",
                "left": "left half", "right": "right half",
                "top-left": "upper-left area", "top-right": "upper-right area",
                "bottom-left": "lower-left area", "bottom-right": "lower-right area",
                "q1": "upper-left quadrant", "q2": "upper-right quadrant",
                "q3": "lower-left quadrant", "q4": "lower-right quadrant",
            }
            _region_label = _rl.get(_region, _region.replace("-", " ") if _region else "")
            _parts = [f"Page {_page}"]
            if _region_label:
                _parts.append(_region_label)
            if _seq and _total_chunks_manifest:
                _parts.append(f"chunk {_seq} of {_total_chunks_manifest}")
            elif _seq:
                _parts.append(f"chunk {_seq}")
            return ", ".join(_parts)

        found_map:    dict = {}
        location_map: dict = {}

        def _absorb_g1(item, chunk_loc: str = ""):
            if isinstance(item, str):
                _bid = _compute_bare_id(item.strip()) or item.strip()
                if _bid and _bid not in found_map:
                    found_map[_bid] = {"symbol_description": None, "label": None}
                    if chunk_loc:
                        location_map[_bid] = chunk_loc
            elif isinstance(item, dict):
                _raw = str(item.get("bare_id") or item.get("id") or item.get("asset_id") or "").strip()
                _bid = _compute_bare_id(_raw) if _raw else ""
                if _bid and _bid not in found_map:
                    found_map[_bid] = {
                        "symbol_description": item.get("symbol_description"),
                        "label":              item.get("label"),
                    }
                    if chunk_loc:
                        location_map[_bid] = chunk_loc

        step_failed_g1 = False
        for _try in range(MAX_STEP_RETRIES + 1):
            try:
                _resp = requests.post(DOC_EXTRACTOR_URL, json=extractor_payload, timeout=600)
                if _resp.status_code == 200:
                    res_json = _resp.json()
                    _all_ext = [res_json] + (res_json.get("extractions") or [])

                    for _eo in _all_ext:
                        for _cd in (_eo.get("chunk_details") or []):
                            _cd_data = (_cd.get("extracted") or {}).get("data") or {}
                            _cloc    = _describe_chunk_loc(_cd)
                            for _cdv in _cd_data.values():
                                if isinstance(_cdv, dict) and "found_ids" in _cdv:
                                    _cdv = _cdv.get("found_ids") or []
                                _items = _cdv if isinstance(_cdv, list) else [_cdv]
                                for _item in _items:
                                    _absorb_g1(_item, _cloc)

                    for _eo in _all_ext:
                        for _sv in (_eo.get("sub_step_extractions") or {}).values():
                            _agg = _sv.get("value") if isinstance(_sv, dict) else (
                                _sv if isinstance(_sv, list) else None)
                            for _item in (_agg or []):
                                _absorb_g1(_item)

                    _bare_id_set = set(bare_ids_sorted)
                    found_map = {k: v for k, v in found_map.items() if k in _bare_id_set}
                    log += (
                        f"   Visual extraction: {len(found_map)}/{len(bare_ids_sorted)} "
                        f"asset IDs located in drawing chunks.\n"
                    )

                    # Legend correlation for found IDs missing symbol/label
                    needs_legend = {bid for bid, v in found_map.items()
                                    if not v.get("symbol_description") and not v.get("label")}
                    if needs_legend and legend_entries:
                        context_map: dict = {}
                        for _eo2 in _all_ext:
                            for _cd2 in (_eo2.get("chunk_details") or []):
                                _raw2     = (_cd2.get("extracted") or {}).get("raw_text", "")[:600]
                                _cd2_data = (_cd2.get("extracted") or {}).get("data") or {}
                                _bids2: set = set()
                                for _cdv2 in _cd2_data.values():
                                    for _ci2 in (_cdv2 if isinstance(_cdv2, list) else [_cdv2]):
                                        if isinstance(_ci2, str):
                                            _cb2 = _ci2.strip()
                                        elif isinstance(_ci2, dict):
                                            _cb2 = str(_ci2.get("bare_id", "") or "").strip()
                                        else:
                                            continue
                                        if _cb2 in needs_legend:
                                            _bids2.add(_cb2)
                                for _cb2 in _bids2:
                                    context_map.setdefault(_cb2, [])
                                    if len(context_map[_cb2]) < 2:
                                        context_map[_cb2].append(_raw2)

                        legend_payload = {
                            "context": (
                                "Drawing chunk text contexts for found asset IDs on a reticulation site plan. "
                                "Match each asset's surrounding text to the most appropriate legend entry."
                            ),
                            "data": [
                                {"bare_id": _bid2, "chunk_context": " | ".join(context_map.get(_bid2, []))}
                                for _bid2 in needs_legend
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
                                _leg_res   = _leg_resp.json()
                                _leg_task  = (_leg_res.get("analytics_results") or {}).get("task-legend-correlation") or {}
                                _leg_result  = _leg_task.get("result") if isinstance(_leg_task, dict) else None
                                _leg_matches = (_leg_result.get("matches") if isinstance(_leg_result, dict) else None) or []
                                for _lm in _leg_matches:
                                    if not isinstance(_lm, dict):
                                        continue
                                    _lbid = str(_lm.get("bare_id", "") or "").strip()
                                    if _lbid in found_map:
                                        found_map[_lbid]["symbol_description"] = _lm.get("symbol_description")
                                        found_map[_lbid]["label"]              = _lm.get("label")
                                log += f"   Legend correlation: matched {len(_leg_matches)} found IDs to legend entries.\n"
                            else:
                                log += f"   Legend correlation skipped (analytics {_leg_resp.status_code}).\n"
                        except Exception as _leg_err:
                            log += f"   Legend correlation error (non-fatal): {_leg_err}\n"

                    # Build sym_matches
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
                                "diagram_location":   location_map.get(_bid, ""),
                            })
                        else:
                            sym_matches.append({
                                "asset_id":           _aid,
                                "bare_id":            _bid,
                                "symbol_description": None,
                                "label":              None,
                                "match_status":       "not_found",
                                "diagram_location":   None,
                            })
                    break
                else:
                    log += f"   Extractor error (attempt {_try+1}/{MAX_STEP_RETRIES+1}): {_resp.text[:300]}\n"
            except Exception as _ex_err:
                log += f"   Network error (attempt {_try+1}/{MAX_STEP_RETRIES+1}): {_ex_err}\n"
            if _try < MAX_STEP_RETRIES:
                log += f"   Retrying in {RETRY_DELAY_SECS}s...\n"
                _time.sleep(RETRY_DELAY_SECS)
            else:
                step_failed_g1 = True
                log += "   Step 8 failed after all retries (visual chunk search).\n"
    else:
        step_failed_g1 = False
        log += "   No site plan chunk manifest found — all assets marked not_found.\n"
        for _rec in asset_records:
            _aid = _rec.get("asset_id", "")
            sym_matches.append({
                "asset_id":           _aid,
                "bare_id":            _compute_bare_id(_aid),
                "symbol_description": None,
                "label":              None,
                "match_status":       "not_found",
            })

    if step_failed_g1:
        return {"log": log, "data": None, "output_file": "", "extraction_report_file": "", "failed": True}

    # Build updated_assets
    match_lookup   = {m.get("asset_id"): m for m in sym_matches if m.get("asset_id")}
    updated_assets = []
    for _rec in asset_records:
        _aid = _rec.get("asset_id", "")
        _m   = match_lookup.get(_aid, {})
        _upd = dict(_rec)
        _upd["bare_id"]            = _compute_bare_id(_aid)
        _upd["symbol_description"] = _m.get("symbol_description")
        _upd["label"]              = _m.get("label")
        _upd["match_status"]       = _m.get("match_status", "not_found")
        _upd["diagram_location"]   = _m.get("diagram_location") or None
        updated_assets.append(_upd)

    found_n     = sum(1 for r in updated_assets if r["match_status"] == "found")
    not_found_n = sum(1 for r in updated_assets if r["match_status"] == "not_found")
    match_sum   = {"total": len(updated_assets), "found": found_n, "not_found": not_found_n}
    log += (
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
    ts       = datetime.utcnow().strftime("%Y%m%dT%H%M%S_%f")[:-3]
    out_path = out_dir / f"AssetSymbolMatches_{ts}.json"
    with open(out_path, "w", encoding="utf-8") as _f:
        json.dump(step_output, _f, indent=2, default=str)
    log += f"   Saved: {out_path.name}\n"

    # ExtractionReport for variance validation (step_extraction format)
    er_path = out_dir / f"ExtractionReport_Step{step.step_number}_{ts}.json"
    extraction_report = {
        "total_files": 1,
        "output_file": str(er_path),
        "extractions": [{
            "filename":          er_path.name,
            "document_type":     "Asset Symbol Matches",
            "document_category": "TAL",
            "process_step_name": step.name or "Match Assets to Site Plan Drawing Symbols",
            "total_sections":    len(updated_assets),
            "relevant_sections": found_n,
            "step_extraction": {
                "total_assets":     len(updated_assets),
                "sheets_processed": ["site_plan_visual_match"],
                "rejected_records": not_found_n,
                "asset_records": [
                    {
                        "asset_id":           _a["asset_id"],
                        "match_status":       _a["match_status"],
                        "label":              _a.get("label"),
                        "symbol_description": _a.get("symbol_description"),
                        "diagram_location":   _a.get("diagram_location"),
                    }
                    for _a in updated_assets
                ],
            },
        }],
    }
    with open(er_path, "w", encoding="utf-8") as _f:
        json.dump(extraction_report, _f, indent=2, default=str)
    log += f"   ExtractionReport saved: {er_path.name}\n"

    return {
        "log":                    log,
        "data":                   step_output,
        "output_file":            str(out_path),
        "extraction_report_file": str(er_path),
        "sym_matches":            sym_matches,
        "updated_assets":         updated_assets,
        "match_sum":              match_sum,
        "failed":                 False,
    }


def _run_analytics_g2_step(state: dict, step: "PlanStep") -> dict:
    """Sync helper: G2 Asset Enrichment via SFL Linkage.

    Reads asset records, legend entries, and Step 8 updated_assets from state,
    runs Phase A (SFL resolution) and Phase B (chunk search for unresolved SFLs)
    synchronously, builds enriched_assets, saves EnrichedAssets_*.json and an
    ExtractionReport_Step{N}_*.json for variance validation.

    Returns:
        log                    - execution log string
        data                   - {enriched_assets, match_summary}
        output_file            - path to EnrichedAssets_*.json
        extraction_report_file - path to ExtractionReport_*.json (for variance)
        enriched_assets        - list of enriched asset dicts
        match_sum              - summary counts
        failed                 - True on unrecoverable error
    """
    import re as _re
    import time as _time
    log = f"   [Step {step.step_number}] {step.name}: G2 run...\n"

    # Pull legend entries from step 6
    legend_extraction = (
        state["results"].get("extraction_proc_extract_site_plan_info_step_6")
        or state["results"].get("extraction_step_6")
        or {}
    )
    legend_entries: list = []
    for _ex in (legend_extraction.get("extractions") or []):
        _sse = _ex.get("sub_step_extractions") or {}
        for _v in _sse.values():
            _arr = _v if isinstance(_v, list) else (_v.get("value") if isinstance(_v, dict) else None)
            if isinstance(_arr, list):
                legend_entries.extend(_arr)

    # Pull asset records from step 7
    asset_extraction = (
        state["results"].get("extraction_proc_extract_asset_spreadsheet")
        or state["results"].get("extraction_step_7")
        or {}
    )
    asset_records: list = []
    for _ex in (asset_extraction.get("extractions") or []):
        _se = _ex.get("step_extraction") or _ex.get("sub_step_extractions") or {}
        asset_records.extend(_se.get("asset_records") or [])

    # ── 1. Reconstruct updated_assets_from_8 ─────────────────────────────────
    updated_assets_from_8 = state["results"].get("updated_assets") or []
    if not updated_assets_from_8:
        raw_sym = state["results"].get("asset_symbol_matches") or []
        sym_map = {m.get("asset_id"): m for m in raw_sym if m.get("asset_id")}
        updated_assets_from_8 = []
        for _rec in asset_records:
            _aid = _rec.get("asset_id", "")
            _m   = sym_map.get(_aid, {})
            updated_assets_from_8.append({
                **_rec,
                "bare_id":            _compute_bare_id(_aid),
                "match_status":       _m.get("match_status", "not_found"),
                "label":              _m.get("label"),
                "symbol_description": _m.get("symbol_description"),
                "diagram_location":   _m.get("diagram_location"),
            })

    # ── 2. Build bare_id → match data from Step 8 found assets ───────────────
    found_bare_map: dict = {}
    for _a8 in updated_assets_from_8:
        if _a8.get("match_status") == "found":
            _bid8 = _a8.get("bare_id") or _compute_bare_id(_a8.get("asset_id", ""))
            if _bid8:
                found_bare_map[_bid8] = {
                    "asset_id":           _a8.get("asset_id"),
                    "symbol_description": _a8.get("symbol_description"),
                    "label":              _a8.get("label"),
                    "diagram_location":   _a8.get("diagram_location"),
                }

    found_direct = [_a for _a in updated_assets_from_8 if _a.get("match_status") == "found"]
    not_found    = [_a for _a in updated_assets_from_8 if _a.get("match_status") != "found"]
    log += (
        f"   Asset enrichment: {len(found_direct)} directly matched, "
        f"{len(not_found)} not matched — attempting SFL linkage.\n"
        f"   Step 8 found_bare_map: {list(found_bare_map.keys())[:8]}\n"
    )

    # ── 3. SFL helpers ────────────────────────────────────────────────────────
    _SFL_KEYS = (
        "superior_functional_location", "superior_fl",
        "superior_function", "functional_location", "floc",
    )

    def _parse_sfl(sfl: str):
        if not sfl:
            return ("", "")
        sfl = sfl.strip()
        _pm = _re.match(r'^([A-Za-z][\w\-]*)0*(\d+)$', sfl)
        if _pm:
            return (_pm.group(1).upper(), _pm.group(2))
        return ("", _re.sub(r'\D', '', sfl))

    # ── 4. Phase A: resolve not-found assets via SFL → found_bare_map ────────
    sfl_resolved:    list = []
    sfl_need_search: list = []
    no_sfl_list:     list = []

    for _asset in not_found:
        _sfl_raw = next((_asset.get(_k) for _k in _SFL_KEYS if _asset.get(_k)), None)
        if not _sfl_raw:
            no_sfl_list.append(_asset)
            continue
        _, _sfl_bare = _parse_sfl(_sfl_raw)
        if _sfl_bare and _sfl_bare in found_bare_map:
            sfl_resolved.append((_asset, _sfl_raw, _sfl_bare, found_bare_map[_sfl_bare]))
            log += (
                f"   SFL hit: {_asset.get('asset_id')} → {_sfl_raw} "
                f"(bare {_sfl_bare}) found with label "
                f"'{found_bare_map[_sfl_bare].get('label')}'\n"
            )
        else:
            sfl_need_search.append((_asset, _sfl_raw, _sfl_bare or ""))

    log += (
        f"   Phase A: {len(sfl_resolved)} resolved via SFL lookup, "
        f"{len(sfl_need_search)} need chunk search, "
        f"{len(no_sfl_list)} have no SFL.\n"
    )

    # ── 5. Phase B: visual chunk search for SFL IDs not in found_bare_map ────
    sfl_chunk_hits: dict = {}
    _sfl_search_ids = [_sb for (_, _, _sb) in sfl_need_search if _sb]

    if _sfl_search_ids:
        _cm_g2      = state["results"].get("chunk_manifests", {})
        _plan_g2    = (state["results"].get("processing_plan") or {}).get("processing_plan", [])
        _SP_KW_G2   = ("retic", "reticulation", "drawing", "diagram", "network")
        _sp_entry_g2 = next(
            (_e for _e in _plan_g2
             if (_e.get("document_category") or "").strip() == "Site Plan"
             or any(_k in (_e.get("document_type") or "").lower() for _k in _SP_KW_G2)),
            None,
        )
        _sp_manifest_g2 = (
            _cm_g2.get(_sp_entry_g2.get("filename", "")) if _sp_entry_g2 else None
        )

        if _sp_manifest_g2:
            log += (
                f"   Phase B: searching {_sp_manifest_g2.get('total_chunks', 0)} chunks "
                f"for {len(_sfl_search_ids)} SFL bare IDs: {_sfl_search_ids[:8]}\n"
            )
            _legend_compact_g2 = json.dumps([
                {"label": _le.get("label"),
                 "description": _le.get("description") or _le.get("symbol_description")}
                for _le in legend_entries[:60]
            ])
            _sfl_payload = {
                "files": [{
                    "filename":           _sp_entry_g2.get("filename", ""),
                    "processing_tool_id": "tool-extract-pdf-content",
                    "document_type":      _sp_entry_g2.get("document_type"),
                    "document_category":  "Site Plan",
                    "content_type":       "visual",
                    "requires_chunking":  True,
                    "chunk_strategy":     _sp_manifest_g2.get("chunk_strategy", "quadrant-split"),
                    "chunk_manifest":     _sp_manifest_g2,
                }],
                "process_step": {
                    "step_id":   "enrich-assets-sfl-search",
                    "step_name": "Find Superior Functional Location Numbers in Drawing",
                    "details": (
                        "Scan each drawing chunk and extract EVERY numeric label visible. "
                        "These are Superior Functional Location (SFL) numbers — "
                        "multi-digit numbers (typically 6–8 digits) appearing adjacent to "
                        "drawing symbols (poles, pillars, substations, etc.). "
                        f"Legend entries for symbol matching: {_legend_compact_g2}"
                    ),
                    "expected_output": {
                        "description": "All numeric labels in this chunk with their adjacent symbol info",
                        "fields": {"found_ids": "array of numeric label objects"},
                    },
                    "sub_steps": [{
                        "sub_step_id":   "sub-step-find-sfl-numbers",
                        "sub_step_name": "Find SFL Numbers in Drawing Chunk",
                        "details": (
                            "Scan this drawing chunk image and extract EVERY numeric label visible. "
                            "These numbers identify electrical network assets — poles, pillars, "
                            "substations. Report every multi-digit number visible adjacent to a "
                            "drawing symbol."
                        ),
                        "output_format": (
                            '{"found_ids": ['
                            '  {"id": "<numeric label as shown>", '
                            '   "symbol_description": "<visual description of the adjacent symbol>", '
                            '   "label": "<best matching legend label or null>", '
                            '   "location": "<chunk region>"}'
                            ']}'
                        ),
                    }],
                },
            }
            _dr_g2 = state["results"].get("document_review", {})
            _df_g2 = _dr_g2.get("folder_path", DOCUMENTS_FOLDER)
            _jk_g2 = state["results"].get("job_key", "")
            if _df_g2:
                _sfl_payload["documents_folder"] = _df_g2
            if _jk_g2:
                _sfl_payload["output_directory"] = str(OUTPUT_DIR / _jk_g2)

            try:
                _sfl_resp = requests.post(DOC_EXTRACTOR_URL, json=_sfl_payload, timeout=300)
                if _sfl_resp.status_code == 200:
                    _sfl_data  = _sfl_resp.json()
                    _sfl_id_set = set(_sfl_search_ids)
                    _all_ext_g2 = [_sfl_data] + (_sfl_data.get("extractions") or [])
                    for _eo_g2 in _all_ext_g2:
                        for _cd_g2 in (_eo_g2.get("chunk_details") or []):
                            _chunk_data_g2 = (_cd_g2.get("extracted") or {}).get("data") or {}
                            _chunk_loc_g2  = (
                                (_cd_g2.get("region") or _cd_g2.get("quadrant") or "")
                                + (f" p{_cd_g2.get('page_number','')}"
                                   if _cd_g2.get("page_number") else "")
                            ).strip()
                            for _cv_g2 in _chunk_data_g2.values():
                                if isinstance(_cv_g2, dict) and "found_ids" in _cv_g2:
                                    _cv_g2 = _cv_g2.get("found_ids") or []
                                for _item_g2 in (_cv_g2 if isinstance(_cv_g2, list) else [_cv_g2]):
                                    if isinstance(_item_g2, dict):
                                        _raw_g2 = str(
                                            _item_g2.get("id") or _item_g2.get("bare_id") or ""
                                        ).strip()
                                    elif isinstance(_item_g2, str):
                                        _raw_g2 = _item_g2.strip()
                                    else:
                                        continue
                                    _bid_g2 = _compute_bare_id(_raw_g2) or _raw_g2
                                    if _bid_g2 and _bid_g2 in _sfl_id_set and _bid_g2 not in sfl_chunk_hits:
                                        sfl_chunk_hits[_bid_g2] = {
                                            "symbol_description": (
                                                _item_g2.get("symbol_description")
                                                if isinstance(_item_g2, dict) else None
                                            ),
                                            "label": (
                                                _item_g2.get("label")
                                                if isinstance(_item_g2, dict) else None
                                            ),
                                            "diagram_location": (
                                                _item_g2.get("location")
                                                if isinstance(_item_g2, dict) else _chunk_loc_g2
                                            ),
                                        }
                    log += (
                        f"   Phase B: found {len(sfl_chunk_hits)} of "
                        f"{len(_sfl_search_ids)} SFL IDs in chunks.\n"
                    )
                else:
                    log += f"   Phase B extractor error {_sfl_resp.status_code}.\n"
            except Exception as _sfl_err:
                log += f"   Phase B chunk search failed: {_sfl_err}\n"
        else:
            log += "   Phase B: no site plan chunk manifest — skipping chunk search.\n"

    # ── 6. Build enriched_assets list ─────────────────────────────────────────
    def _find_leg_category(label: str):
        return next(
            (_le.get("category") for _le in legend_entries
             if (_le.get("label") or "").strip().upper() == (label or "").strip().upper()),
            None,
        )

    enriched_assets: list = []

    for _a in found_direct:
        _e = dict(_a)
        _lbl = _a.get("label") or ""
        _e["legend_label"]     = _lbl or None
        _e["legend_category"]  = _find_leg_category(_lbl)
        _e["match_confidence"] = "high"
        _e["match_method"]     = "direct"
        enriched_assets.append(_e)

    for (_a, _sfl_raw, _sfl_bare, _sfl_match) in sfl_resolved:
        _e = dict(_a)
        _lbl = _sfl_match.get("label") or ""
        _e["legend_label"]       = _lbl or None
        _e["legend_category"]    = _find_leg_category(_lbl)
        _e["symbol_description"] = _sfl_match.get("symbol_description")
        _e["diagram_location"]   = _sfl_match.get("diagram_location")
        _e["match_confidence"]   = "high"
        _e["match_method"]       = "sfl_lookup"
        _e["sfl_bare_id"]        = _sfl_bare
        enriched_assets.append(_e)

    for (_a, _sfl_raw, _sfl_bare) in sfl_need_search:
        _e = dict(_a)
        if _sfl_bare and _sfl_bare in sfl_chunk_hits:
            _hit = sfl_chunk_hits[_sfl_bare]
            _lbl = _hit.get("label") or ""
            _e["legend_label"]       = _lbl or None
            _e["legend_category"]    = _find_leg_category(_lbl)
            _e["symbol_description"] = _hit.get("symbol_description")
            _e["diagram_location"]   = _hit.get("diagram_location")
            _e["match_confidence"]   = "high"
            _e["match_method"]       = "sfl_chunk_search"
            _e["sfl_bare_id"]        = _sfl_bare
        else:
            _e["legend_label"]     = None
            _e["legend_category"]  = None
            _e["match_confidence"] = "none"
            _e["match_method"]     = "sfl_not_found"
        enriched_assets.append(_e)

    for _a in no_sfl_list:
        _e = dict(_a)
        _e["legend_label"]     = None
        _e["legend_category"]  = None
        _e["match_confidence"] = "none"
        _e["match_method"]     = "no_sfl"
        enriched_assets.append(_e)

    # ── 7. Match summary and output ───────────────────────────────────────────
    _n_direct = sum(1 for _a in enriched_assets if _a.get("match_method") == "direct")
    _n_sfl    = sum(1 for _a in enriched_assets
                   if "sfl" in (_a.get("match_method") or "")
                   and _a.get("match_confidence") == "high")
    _n_none   = sum(1 for _a in enriched_assets if _a.get("match_confidence") == "none")
    match_sum = {
        "total":            len(enriched_assets),
        "found_direct":     _n_direct,
        "found_via_sfl":    _n_sfl,
        "found_on_drawing": _n_direct + _n_sfl,
        "not_found":        _n_none,
    }
    log += (
        f"   Enrichment complete: {_n_direct} direct, {_n_sfl} via SFL, "
        f"{_n_none} not found of {len(enriched_assets)} total.\n"
    )

    enriched_output = {"enriched_assets": enriched_assets, "match_summary": match_sum}

    job_key  = state["results"].get("job_key", "")
    out_dir  = OUTPUT_DIR / job_key if job_key else OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    ts       = datetime.utcnow().strftime("%Y%m%dT%H%M%S_%f")[:-3]
    out_path = out_dir / f"EnrichedAssets_{ts}.json"
    with open(out_path, "w", encoding="utf-8") as _f:
        json.dump(enriched_output, _f, indent=2, default=str)
    log += f"   Saved: {out_path.name}\n"

    # ExtractionReport for variance validation (step_extraction format)
    er_path = out_dir / f"ExtractionReport_Step{step.step_number}_{ts}.json"
    extraction_report = {
        "total_files": 1,
        "output_file": str(er_path),
        "extractions": [{
            "filename":          er_path.name,
            "document_type":     "Enriched Asset Register",
            "document_category": "TAL",
            "process_step_name": step.name or "Enrich Assets with Legend Data",
            "total_sections":    len(enriched_assets),
            "relevant_sections": _n_direct + _n_sfl,
            "step_extraction": {
                "total_assets":     len(enriched_assets),
                "sheets_processed": ["asset_enrichment_sfl"],
                "rejected_records": _n_none,
                "asset_records": [
                    {
                        "asset_id":           _a.get("asset_id"),
                        "match_confidence":   _a.get("match_confidence"),
                        "legend_label":       _a.get("legend_label"),
                        "match_method":       _a.get("match_method"),
                        "symbol_description": _a.get("symbol_description"),
                    }
                    for _a in enriched_assets
                ],
            },
        }],
    }
    with open(er_path, "w", encoding="utf-8") as _f:
        json.dump(extraction_report, _f, indent=2, default=str)
    log += f"   ExtractionReport saved: {er_path.name}\n"

    return {
        "log":                    log,
        "data":                   enriched_output,
        "output_file":            str(out_path),
        "extraction_report_file": str(er_path),
        "enriched_assets":        enriched_assets,
        "match_sum":              match_sum,
        "failed":                 False,
    }


async def _run_step_impl(
    plan_id: str,
    file_bytes: Optional[bytes],
    file_name: Optional[str],
    file_content_type: Optional[str],
    folder_path: Optional[str],
    repeat_runs_override: Optional[int] = None,
) -> dict:
    """All step-execution logic. Called as an asyncio Task by run_next_step."""
    state = executions[plan_id]
    idx = state["current_step_index"]
    step: PlanStep = state["plan"].steps[idx]

    # Apply user-specified repeat_runs override (sent from the frontend runs control)
    if repeat_runs_override is not None and repeat_runs_override >= 1:
        step = step.copy(update={"repeat_runs": repeat_runs_override})

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
    parallel_step_variance = None      # {0-based-idx: variance_report} for repeat_runs steps
    parallel_step_run_results = None   # {0-based-idx: [{run_number, data, output_file, failed}]} per repeat step

    # --- ROUTING LOGIC ---

    # A. ASSET OPS AGENT (Port 8084)
    if "asset" in resource_key and "ops" in resource_key:
        if not file_bytes:
            return {"status": "error", "log": log_message + "   ERROR: File required!", "step_index": idx}

        log_message += f"   Sending to Asset Ops (Port 8084)...\n"
        step_input_data = {"file": file_name}
        for _try in range(MAX_STEP_RETRIES + 1):
            try:
                files = {'file': (file_name, BytesIO(file_bytes), file_content_type)}
                resp = await _async_post(ASSET_OPS_URL, files=files, timeout=30)
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
                log_message += f"   Retrying in {RETRY_DELAY_SECS}s...\n"; await asyncio.sleep(RETRY_DELAY_SECS)
            else:
                step_failed = True; log_message += "   Step failed after all retries.\n"

    # B. CONTENT REVIEWER AGENT (Port 8085)
    elif "content" in resource_key and "reviewer" in resource_key:
        if not file_bytes:
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
                resp = await _async_post(REVIEWER_URL, files=files, data=form_data, timeout=60)
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
                log_message += f"   Retrying in {RETRY_DELAY_SECS}s...\n"; await asyncio.sleep(RETRY_DELAY_SECS)
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
                        resp = await _async_post(
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
                        log_message += f"   Retrying in {RETRY_DELAY_SECS}s...\n"; await asyncio.sleep(RETRY_DELAY_SECS)
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
                    resp = await _async_post(
                        DOC_REVIEWER_URL,
                        json={"folder_path": active_folder, "search_context": search_context,
                              "output_dir": output_dir},
                        timeout=300,
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
                    log_message += f"   Retrying in {RETRY_DELAY_SECS}s...\n"; await asyncio.sleep(RETRY_DELAY_SECS)
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
                resp = await _async_post(MAPPER_URL, data=form_data, timeout=30)
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
                log_message += f"   Retrying in {RETRY_DELAY_SECS}s...\n"; await asyncio.sleep(RETRY_DELAY_SECS)
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
                    resp = await _async_post(VERIF_URL, data=payload_data, files=files, timeout=180)
                else:
                    resp = await _async_post(VERIF_URL, data=payload_data, timeout=10)

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
                log_message += f"   Retrying in {RETRY_DELAY_SECS}s...\n"; await asyncio.sleep(RETRY_DELAY_SECS)
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
            # Use the strategy exactly as planned — do NOT override to dual-split.
            # dual-split was previously forced for all A3 drawings to support legend
            # extraction, but it creates unusable panoramas for notes extraction (step 5).
            # The processing plan assigns the correct strategy per drawing type.
            log_message += f"   Chunking: {filename} | strategy={strategy}, page_size={psize}\n"

            chunk_job_key   = state.get("results", {}).get("job_key")
            chunk_form_data = {"chunk_strategy": strategy, "page_size": psize, "dpi": "300"}
            if chunk_job_key:
                chunk_form_data["output_base"] = f"/app/OUTPUT/{chunk_job_key}/chunks"

            for _try in range(MAX_STEP_RETRIES + 1):
                try:
                    with open(filepath, "rb") as fh:
                        file_bytes_chunk = fh.read()
                    resp = await _async_post(
                        CHUNKER_URL,
                        files={"file": (filename, file_bytes_chunk, "application/pdf")},
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
                    log_message += f"   Retrying in {RETRY_DELAY_SECS}s...\n"; await asyncio.sleep(RETRY_DELAY_SECS)
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
                repeat_runs = ps.repeat_runs or 1
                if repeat_runs <= 1:
                    return ps.step_number, _strip_legend_extra_fields(_run_extractor_step(state, ps))
                # Multi-run mode: Phase 1 (vision OCR) runs once; Phase 2 (text analysis) repeats.
                # This eliminates OCR noise from the consistency check so variance only reflects
                # true differences in the extraction logic, not character-level Gemini non-determinism.
                all_run_results = []
                phase1_cache: dict = {}  # filename -> chunk_details from run 1
                for run_idx in range(repeat_runs):
                    result = _strip_legend_extra_fields(_run_extractor_step(state, ps, phase1_cache=phase1_cache if run_idx > 0 else None))
                    if run_idx == 0:
                        # Capture Phase 1 chunk_details per file so subsequent runs reuse them
                        for ex in (result.get("data") or {}).get("extractions", []):
                            fname = ex.get("filename", "")
                            if fname and ex.get("chunk_details"):
                                phase1_cache[fname] = ex["chunk_details"]
                    result["run_number"] = run_idx + 1
                    all_run_results.append(result)
                # Primary result = last run; downstream steps use this as normal
                primary = all_run_results[-1]
                primary["repeat_run_results"] = all_run_results
                primary["repeat_run_output_files"] = [
                    r.get("output_file", "") for r in all_run_results if r.get("output_file")
                ]
                return ps.step_number, primary

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

            # Variance validation — for any step in this group with repeat_runs > 1
            _job_key_var    = state.get("results", {}).get("job_key")
            _output_dir_var = str(OUTPUT_DIR / _job_key_var) if _job_key_var else None
            variance_log, variance_by_step = await loop.run_in_executor(
                None,
                lambda: _run_variance_validation(group_steps, parallel_result_map, _output_dir_var),
            )
            log_message += variance_log
            # Store variance results in state and build per-index map for the frontend
            parallel_step_variance: Optional[dict] = None
            if variance_by_step:
                parallel_step_variance = {}
                for i, gs in enumerate(group_steps):
                    if gs.step_number in variance_by_step:
                        report = variance_by_step[gs.step_number]
                        state["results"][f"variance_step_{gs.step_number}"] = report
                        parallel_step_variance[idx + i] = report

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

            # Build per-run result list for repeat_runs steps (for the variance report view)
            for i, gs in enumerate(group_steps):
                ps_result = parallel_result_map.get(gs.step_number, {})
                run_results = ps_result.get("repeat_run_results")
                if run_results:
                    if parallel_step_run_results is None:
                        parallel_step_run_results = {}
                    parallel_step_run_results[idx + i] = [
                        {
                            "run_number":  r.get("run_number", ri + 1),
                            "data":        r.get("data"),
                            "output_file": r.get("output_file", ""),
                            "failed":      r.get("failed", False),
                        }
                        for ri, r in enumerate(run_results)
                    ]

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
            
            # Handle multi-run mode for single steps (variance testing)
            repeat_runs = int(_step.repeat_runs) if _step.repeat_runs else 1
            if repeat_runs > 1:
                # Multi-run mode: Phase 1 (vision OCR) runs once; Phase 2 repeats.
                # Same cache strategy as parallel-group repeat_runs — eliminates OCR
                # noise so variance only reflects extraction-logic differences.
                log_message += f"   Multi-run mode: executing {repeat_runs} runs (Phase 1 cached after run 1)...\n"
                all_run_results = []
                _single_phase1_cache: dict = {}  # filename -> chunk_details, populated after run 1

                async def _execute_run(run_number: int, p1_cache: dict) -> dict:
                    """Execute a single run of the extractor step."""
                    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
                    run_log = f"   Run {run_number}/{repeat_runs} (timestamp: {run_timestamp})...\n"

                    loop = asyncio.get_event_loop()
                    run_result = await loop.run_in_executor(
                        None, _run_extractor_step, state, _step,
                        p1_cache if p1_cache else None
                    )

                    run_result = _strip_legend_extra_fields(run_result)
                    run_result["run_number"] = run_number
                    run_result["run_timestamp"] = run_timestamp
                    run_log += run_result.get("log", "")

                    return {"result": run_result, "log": run_log}

                # Execute all runs sequentially; cache Phase 1 after first run
                for run_idx in range(repeat_runs):
                    run_data = await _execute_run(run_idx + 1, _single_phase1_cache if run_idx > 0 else {})
                    all_run_results.append(run_data["result"])
                    log_message += run_data["log"]
                    if run_idx == 0:
                        for ex in (run_data["result"].get("data") or {}).get("extractions", []):
                            fname = ex.get("filename", "")
                            if fname and ex.get("chunk_details"):
                                _single_phase1_cache[fname] = ex["chunk_details"]
                
                # Use last run as primary result for downstream steps
                result = all_run_results[-1]
                result["repeat_run_results"] = all_run_results
                result["repeat_run_count"] = repeat_runs
                result["repeat_run_output_files"] = [
                    r.get("output_file", "") for r in all_run_results if r.get("output_file")
                ]
                log_message += f"   Completed {repeat_runs} runs. Using final run as primary result.\n"

                # ── Package results for the frontend Runs & Variance tab ──────
                # The frontend reads parallel_step_run_results / parallel_step_variance
                # from the response regardless of whether the step ran in a parallel group.
                # Key is the 0-based step index (idx) — same convention as parallel groups.
                #
                # IMPORTANT: result["repeat_run_results"] = all_run_results creates a
                # circular reference (result IS all_run_results[-1]).  Passing that list
                # directly to FastAPI causes a RecursionError in jsonable_encoder.
                # Strip the back-reference keys before handing off to the serializer.
                _frontend_runs = [
                    {k: v for k, v in _r.items()
                     if k not in ("repeat_run_results", "repeat_run_count", "repeat_run_output_files")}
                    for _r in all_run_results
                ]
                parallel_step_run_results = {idx: _frontend_runs}

                # Call the variance validator when we have ≥2 output files
                _run_files = result.get("repeat_run_output_files", [])
                if len(_run_files) >= 2:
                    _job_key_var    = state.get("results", {}).get("job_key")
                    _output_dir_var = str(OUTPUT_DIR / _job_key_var) if _job_key_var else None
                    try:
                        _var_resp = requests.post(
                            VARIANCE_VALIDATOR_URL,
                            json={
                                "step_name": _step.name or f"Step {_step.step_number}",
                                "step_description": _step.description or "",
                                "run_output_files": _run_files,
                                "output_dir": _output_dir_var,
                            },
                            timeout=120,
                        )
                        if _var_resp.status_code == 200:
                            _vr = _var_resp.json()
                            parallel_step_variance = {idx: _vr}
                            log_message += (
                                f"   Variance report: {_vr.get('verdict', '?')} "
                                f"(score={_vr.get('consistency_score', 0):.0%}, "
                                f"files={_vr.get('files_analysed', 0)})\n"
                            )
                        else:
                            log_message += f"   Variance validator returned HTTP {_var_resp.status_code}\n"
                    except Exception as _ve:
                        log_message += f"   Variance validator error: {_ve}\n"
                else:
                    log_message += (
                        f"   Variance check skipped — only {len(_run_files)} output file(s) "
                        f"available (need ≥2).\n"
                    )

            else:
                # Single-run mode (normal case)
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None, _run_extractor_step, state, _step
                )
                result = _strip_legend_extra_fields(result)
                log_message += result.get("log", "")
            
            if result.get("failed"):
                step_failed = True
            if result.get("data"):
                state["results"]["extraction"] = result["data"]
                # Store under extraction_step_N so the consolidator finds it alongside parallel steps
                state["results"][f"extraction_step_{step.step_number}"] = result["data"]
                
                # Store multi-run metadata if present (for variance analysis)
                if result.get("repeat_run_results"):
                    state["results"][f"extraction_step_{step.step_number}_multirun"] = {
                        "repeat_run_count": result.get("repeat_run_count", 0),
                        "repeat_run_output_files": result.get("repeat_run_output_files", []),
                        "runs": result.get("repeat_run_results", [])
                    }
                    log_message += (
                        f"   Multi-run results stored under 'extraction_step_{step.step_number}_multirun' "
                        f"({result.get('repeat_run_count')} runs).\n"
                    )
                
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
            step_input_data = {"asset_count": len(asset_records), "legend_count": len(legend_entries)}
            _repeat_g1 = int(step.repeat_runs) if step.repeat_runs else 1
            if _repeat_g1 > 1:
                log_message += f"   Multi-run mode: executing {_repeat_g1} runs for variance testing...\n"
            else:
                log_message += (
                    f"   Matching {len(asset_records)} assets to site plan symbols "
                    f"using {len(legend_entries)} legend entries...\n"
                )

            _g1_loop = asyncio.get_event_loop()
            _all_g1_runs: list = []
            for _g1_run_idx in range(_repeat_g1):
                _g1_ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
                if _repeat_g1 > 1:
                    log_message += f"   Run {_g1_run_idx + 1}/{_repeat_g1} (timestamp: {_g1_ts})...\n"
                _g1_result = await _g1_loop.run_in_executor(None, _run_analytics_g1_step, state, step)
                _g1_result["run_number"]    = _g1_run_idx + 1
                _g1_result["run_timestamp"] = _g1_ts
                _all_g1_runs.append(_g1_result)
                log_message += _g1_result.get("log", "")
                if _g1_result.get("failed"):
                    step_failed = True
                    log_message += f"   G1 run {_g1_run_idx + 1} failed — aborting.\n"
                    break

            if not step_failed:
                _g1_primary = _all_g1_runs[-1]
                updated_assets = _g1_primary["updated_assets"]
                sym_matches    = _g1_primary["sym_matches"]
                match_sum      = _g1_primary["match_sum"]
                step_output    = _g1_primary["data"]
                _g1_out_path   = _g1_primary["output_file"]

                symbol_match_extraction = {
                    "total_files": 1,
                    "output_file": _g1_out_path,
                    "extractions": [{
                        "filename":          Path(_g1_out_path).name if _g1_out_path else "",
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
                    }],
                }
                state["results"]["updated_assets"]                      = updated_assets
                state["results"]["asset_symbol_matches"]                = sym_matches
                state["results"][f"extraction_step_{step.step_number}"] = symbol_match_extraction
                step_output_data      = step_output
                step_output_file_path = _g1_out_path

                if _repeat_g1 > 1:
                    # Populate frontend run data (strip large data payload to keep response lean)
                    _g1_frontend_runs = [
                        {k: v for k, v in _r.items() if k not in ("data", "updated_assets", "sym_matches")}
                        for _r in _all_g1_runs
                    ]
                    parallel_step_run_results = {idx: _g1_frontend_runs}

                    # Call variance validator using ExtractionReport files
                    _g1_er_files = [
                        _r.get("extraction_report_file", "")
                        for _r in _all_g1_runs
                        if _r.get("extraction_report_file")
                    ]
                    if len(_g1_er_files) >= 2:
                        _g1_job_key = state.get("results", {}).get("job_key")
                        _g1_out_dir = str(OUTPUT_DIR / _g1_job_key) if _g1_job_key else None
                        try:
                            _g1_var_resp = requests.post(
                                VARIANCE_VALIDATOR_URL,
                                json={
                                    "step_name":        step.name or f"Step {step.step_number}",
                                    "step_description": step.description or "",
                                    "run_output_files": _g1_er_files,
                                    "output_dir":       _g1_out_dir,
                                },
                                timeout=120,
                            )
                            if _g1_var_resp.status_code == 200:
                                _g1_vr = _g1_var_resp.json()
                                parallel_step_variance = {idx: _g1_vr}
                                log_message += (
                                    f"   Variance report: {_g1_vr.get('verdict', '?')} "
                                    f"(score={_g1_vr.get('consistency_score', 0):.0%}, "
                                    f"files={_g1_vr.get('files_analysed', 0)})\n"
                                )
                            else:
                                log_message += f"   Variance validator returned HTTP {_g1_var_resp.status_code}\n"
                        except Exception as _g1_ve:
                            log_message += f"   Variance validator error: {_g1_ve}\n"
                    else:
                        log_message += (
                            f"   Variance check skipped — only {len(_g1_er_files)} ExtractionReport "
                            f"file(s) available (need ≥2).\n"
                        )

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
                    resp = await _async_post(ANALYTICS_URL, json=payload, timeout=300)
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
                    await asyncio.sleep(RETRY_DELAY_SECS)
                else:
                    step_failed = True
                    log_message += "   Step failed after all retries.\n"

        # ── G2. Asset Enrichment via SFL Linkage ─────────────────────────────────
        elif not step_failed:
            _repeat_g2 = int(step.repeat_runs) if step.repeat_runs else 1
            if _repeat_g2 > 1:
                log_message += f"   Multi-run mode: executing {_repeat_g2} runs for variance testing...\n"
            else:
                log_message += "   Enriching assets via SFL linkage...\n"

            _g2_loop = asyncio.get_event_loop()
            _all_g2_runs: list = []
            for _g2_run_idx in range(_repeat_g2):
                _g2_ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
                if _repeat_g2 > 1:
                    log_message += f"   Run {_g2_run_idx + 1}/{_repeat_g2} (timestamp: {_g2_ts})...\n"
                _g2_result = await _g2_loop.run_in_executor(None, _run_analytics_g2_step, state, step)
                _g2_result["run_number"]    = _g2_run_idx + 1
                _g2_result["run_timestamp"] = _g2_ts
                _all_g2_runs.append(_g2_result)
                log_message += _g2_result.get("log", "")
                if _g2_result.get("failed"):
                    step_failed = True
                    log_message += f"   G2 run {_g2_run_idx + 1} failed — aborting.\n"
                    break

            if not step_failed:
                _g2_primary     = _all_g2_runs[-1]
                enriched_assets = _g2_primary["enriched_assets"]
                match_sum       = _g2_primary["match_sum"]
                enriched_output = _g2_primary["data"]
                _g2_out_path    = _g2_primary["output_file"]
                _n_direct       = sum(1 for _a in enriched_assets if _a.get("match_method") == "direct")
                _n_sfl          = sum(1 for _a in enriched_assets
                                     if "sfl" in (_a.get("match_method") or "")
                                     and _a.get("match_confidence") == "high")

                enrichment_extraction = {
                    "total_files": 1,
                    "output_file": _g2_out_path,
                    "extractions": [{
                        "filename":          Path(_g2_out_path).name if _g2_out_path else "",
                        "document_type":     "Enriched Asset Register",
                        "document_category": "TAL",
                        "process_step_name": step.name or "Enrich Assets with Legend Data",
                        "total_sections":    len(enriched_assets),
                        "relevant_sections": _n_direct + _n_sfl,
                        "sub_step_extractions": {
                            "sub-step-asset-enrichment": {
                                "enriched_assets": enriched_assets,
                                "match_summary":   match_sum,
                            }
                        },
                    }],
                }
                state["results"]["enriched_assets"]                     = enriched_output
                state["results"][f"extraction_step_{step.step_number}"] = enrichment_extraction
                step_output_data      = enriched_output
                step_output_file_path = _g2_out_path

                if _repeat_g2 > 1:
                    # Frontend run data (strip large payload)
                    _g2_frontend_runs = [
                        {k: v for k, v in _r.items() if k not in ("data", "enriched_assets")}
                        for _r in _all_g2_runs
                    ]
                    parallel_step_run_results = {idx: _g2_frontend_runs}

                    # Call variance validator using ExtractionReport files
                    _g2_er_files = [
                        _r.get("extraction_report_file", "")
                        for _r in _all_g2_runs
                        if _r.get("extraction_report_file")
                    ]
                    if len(_g2_er_files) >= 2:
                        _g2_job_key = state.get("results", {}).get("job_key")
                        _g2_out_dir = str(OUTPUT_DIR / _g2_job_key) if _g2_job_key else None
                        try:
                            _g2_var_resp = requests.post(
                                VARIANCE_VALIDATOR_URL,
                                json={
                                    "step_name":        step.name or f"Step {step.step_number}",
                                    "step_description": step.description or "",
                                    "run_output_files": _g2_er_files,
                                    "output_dir":       _g2_out_dir,
                                },
                                timeout=120,
                            )
                            if _g2_var_resp.status_code == 200:
                                _g2_vr = _g2_var_resp.json()
                                parallel_step_variance = {idx: _g2_vr}
                                log_message += (
                                    f"   Variance report: {_g2_vr.get('verdict', '?')} "
                                    f"(score={_g2_vr.get('consistency_score', 0):.0%}, "
                                    f"files={_g2_vr.get('files_analysed', 0)})\n"
                                )
                            else:
                                log_message += f"   Variance validator returned HTTP {_g2_var_resp.status_code}\n"
                        except Exception as _g2_ve:
                            log_message += f"   Variance validator error: {_g2_ve}\n"
                    else:
                        log_message += (
                            f"   Variance check skipped — only {len(_g2_er_files)} ExtractionReport "
                            f"file(s) available (need ≥2).\n"
                        )


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
        await asyncio.sleep(1)
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

    # Abort on critical_fail: if the step is marked critical and validation failed, halt immediately
    if (validation_result
            and not validation_result.get("is_valid", False)
            and step.validation
            and step.validation.critical_fail):
        log_message += "   [ABORT] critical_fail=True and validation did not pass — execution halted.\n"
        state["is_complete"] = True
        return {
            "status": "failed",
            "step_index": state["current_step_index"],
            "log": log_message,
            "remaining_steps": state["total_steps"] - state["current_step_index"],
            "step_output": None,
            "validation": validation_result,
            "error": f"Critical step failed validation: {validation_result.get('summary', '')}",
        }

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
    elif "analytics" in resource_key:
        step_output = step_output_data
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
    if parallel_step_variance is not None:
        response["parallel_step_variance"] = parallel_step_variance
    if parallel_step_run_results is not None:
        response["parallel_step_run_results"] = parallel_step_run_results

    # ── Persist session for history / rerun ───────────────────────────────
    if "_created_at" not in state:
        state["_created_at"] = datetime.utcnow().isoformat() + "Z"
    if "_per_step_results" not in state:
        state["_per_step_results"] = {}

    _sr: dict = {
        "log":        log_message,
        "step_output": step_output,
        "validation":  validation_result,
        "status":      "error" if step_failed else "completed",
    }
    if parallel_completed_indices is not None:
        _sr["parallel_completed_indices"] = parallel_completed_indices
    if parallel_step_validations is not None:
        _sr["parallel_step_validations"]  = parallel_step_validations
    if parallel_step_outputs is not None:
        _sr["parallel_step_outputs"]      = parallel_step_outputs
    if parallel_step_variance is not None:
        _sr["parallel_step_variance"]     = parallel_step_variance
    if parallel_step_run_results is not None:
        _sr["parallel_step_run_results"]  = parallel_step_run_results
    state["_per_step_results"][idx] = _sr

    # For parallel groups, store each sibling step's own output individually
    # so the session viewer can populate them without unpacking the lead result.
    if parallel_completed_indices:
        for _pi in parallel_completed_indices:
            if _pi != idx and _pi not in state["_per_step_results"]:
                state["_per_step_results"][_pi] = {
                    "log":        "",
                    "step_output": (parallel_step_outputs or {}).get(_pi),
                    "validation":  (parallel_step_validations or {}).get(_pi),
                    "variance":    (parallel_step_variance or {}).get(_pi),
                    "status":      "completed",
                }

    _save_session(plan_id, state)
    return response

PROCESS_DIR = Path(os.getenv("PROCESS_DIR",  str(Path(__file__).parent / "process")))
TASKS_FILE  = Path(os.getenv("TASKS_FILE",   str(Path(__file__).parent / "tasks.json")))
OUTPUT_DIR  = Path(os.getenv("OUTPUT_DIR",   str(Path(__file__).parent / "OUTPUT")))

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


@app.get("/variance_reports")
def list_variance_reports():
    """Return all VarianceReport_*.json files grouped by execution folder with summary metadata."""
    if not OUTPUT_DIR.exists():
        return {"executions": {}}

    executions: dict = {}
    for vf in sorted(OUTPUT_DIR.rglob("VarianceReport_*.json")):
        rel = vf.relative_to(OUTPUT_DIR)
        parts = rel.parts
        if len(parts) < 2:
            continue
        exec_folder = parts[0]
        try:
            data = json.loads(vf.read_text(encoding="utf-8"))
        except Exception:
            continue
        entry = {
            "path": str(rel).replace("\\", "/"),
            "step_name": data.get("step_name", ""),
            "verdict": data.get("verdict", ""),
            "consistency_score": data.get("consistency_score"),
            "num_runs": data.get("num_runs", 0),
            "files_analysed": data.get("files_analysed", 0),
            "timestamp": data.get("timestamp", ""),
            "consistent_fields": data.get("consistent_fields", 0),
            "total_fields": data.get("total_fields", 0),
            "summary": data.get("summary", ""),
        }
        executions.setdefault(exec_folder, []).append(entry)

    return {"executions": executions}


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
#  SESSION HISTORY
# ═══════════════════════════════════════════

@app.get("/sessions")
def list_sessions():
    """List all saved sessions, newest-first."""
    if not OUTPUT_DIR.exists():
        return {"sessions": []}
    sessions = []
    for f in sorted(OUTPUT_DIR.rglob("session.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            sessions.append({
                "session_id":      data.get("session_id", ""),
                "job_key":         data.get("job_key", ""),
                "created_at":      data.get("created_at", ""),
                "updated_at":      data.get("updated_at", ""),
                "is_complete":     data.get("is_complete", False),
                "steps_completed": data.get("steps_completed", 0),
                "total_steps":     data.get("total_steps", 0),
                "plan_overview":   (data.get("plan_overview") or "")[:140],
                "folder_path":     data.get("folder_path", ""),
                "session_file":    str(f.relative_to(OUTPUT_DIR)).replace("\\", "/"),
            })
        except Exception:
            pass
    return {"sessions": sessions}


@app.get("/sessions/{session_id}")
def get_session(session_id: str):
    """Return the full session payload for a given session_id."""
    if not OUTPUT_DIR.exists():
        raise HTTPException(status_code=404, detail="No output directory")
    for f in OUTPUT_DIR.rglob("session.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("session_id") == session_id:
                return data
        except Exception:
            pass
    raise HTTPException(status_code=404, detail="Session not found")


@app.post("/sessions/{session_id}/restore")
def restore_session(session_id: str):
    """Restore a session into a fresh in-memory execution and return its plan_id.
    The caller can then use /run_next/{plan_id}?step_index=N to rerun any step
    with the original session's intermediate results as context.
    """
    if not OUTPUT_DIR.exists():
        raise HTTPException(status_code=404, detail="No output directory")
    session_data = None
    for f in OUTPUT_DIR.rglob("session.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if d.get("session_id") == session_id:
                session_data = d
                break
        except Exception:
            pass
    if not session_data:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        plan_steps = [PlanStep(**s) for s in session_data.get("plan_steps", [])]
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid plan steps: {exc}")

    plan = ExecutionRequest(
        plan_overview=session_data.get("plan_overview", ""),
        steps=plan_steps,
    )
    new_plan_id      = str(uuid.uuid4())
    restored_results = _restore_results_from_session(session_data.get("results_context", {}))
    per_step_raw     = session_data.get("per_step_results", {})

    executions[new_plan_id] = {
        "plan":               plan,
        "current_step_index": session_data.get("steps_completed", 0),
        "total_steps":        len(plan_steps),
        "is_complete":        False,
        "results":            restored_results,
        "_per_step_results":  {int(k): v for k, v in per_step_raw.items()},
        "_created_at":        session_data.get("created_at", datetime.utcnow().isoformat() + "Z"),
        "_restored_from":     session_id,
    }
    return {
        "plan_id":         new_plan_id,
        "session_id":      session_id,
        "total_steps":     len(plan_steps),
        "steps_completed": session_data.get("steps_completed", 0),
    }


# ═══════════════════════════════════════════
#  SERVICE MANAGEMENT
# ═══════════════════════════════════════════

_MANAGED_SERVICES = [
    {"name": "agent09-responder",          "label": "Responder",         "health": "http://responder:8000/tasks"},
    {"name": "agent09-orchestrator",       "label": "Orchestrator",      "health": None},
    {"name": "agent09-document-reviewer",  "label": "Document Reviewer", "health": "http://document-reviewer:8089/health"},
    {"name": "agent09-document-extractor", "label": "Document Extractor","health": "http://document-extractor:8090/health"},
    {"name": "agent09-step-validator",     "label": "Step Validator",    "health": "http://step-validator:8088/health"},
    {"name": "agent09-document-chunker",   "label": "Document Chunker",  "health": "http://document-chunker:8091/health"},
    {"name": "agent09-frontend",           "label": "Frontend",          "health": None},
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
    client = _docker.from_env(timeout=30)
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
            fut.result(timeout=35)
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
            fut.result(timeout=40)
        return {"container": container_name, "action": "restarted"}
    except concurrent.futures.TimeoutError:
        raise HTTPException(status_code=504, detail="Docker operation timed out")
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("orchestrator:app", host="0.0.0.0", port=8001, reload=True)
