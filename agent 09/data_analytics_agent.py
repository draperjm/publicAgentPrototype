import os
import json
import logging
import statistics
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Data Analytics Agent")

# --- Logging ---
logger = logging.getLogger("DataAnalytics")
logger.setLevel(logging.INFO)
if not logger.handlers:
    fh = logging.FileHandler("data_analytics.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(fh)

# --- AI Clients ---
anthropic_client = None
anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
if anthropic_key:
    import anthropic as _anthropic
    anthropic_client = _anthropic.Anthropic(api_key=anthropic_key)

openai_client = None
openai_key = os.environ.get("OPENAI_API_KEY")
if openai_key:
    from openai import OpenAI
    openai_client = OpenAI(api_key=openai_key)

ANALYTICS_MODEL = os.environ.get("ANALYTICS_MODEL", "gpt-4o")
ANTHROPIC_MODEL = "claude-sonnet-4-6"

SAMPLE_THRESHOLD = 200
SAMPLE_SIZE = 50


# --- Request / Response Models ---

class AnalyticsTask(BaseModel):
    task_id: str
    task_name: str
    description: str
    task_type: Optional[str] = None
    data_fields: Optional[List[str]] = None
    output_format: Optional[str] = None


class AnalyticsRequest(BaseModel):
    data: Any
    tasks: List[AnalyticsTask]
    context: Optional[str] = None
    reference_data: Optional[Any] = None


# --- Helper: LLM call ---

def _call_anthropic(prompt: str) -> str:
    message = anthropic_client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=16000,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def _call_openai(prompt: str) -> str:
    resp = openai_client.chat.completions.create(
        model=ANALYTICS_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
    )
    return resp.choices[0].message.content


def _call_llm(prompt: str) -> tuple:
    """Try Anthropic first, fall back to OpenAI. Returns (response_text, model_used)."""
    last_error = None

    if anthropic_client:
        try:
            return _call_anthropic(prompt), ANTHROPIC_MODEL
        except Exception as e:
            last_error = e
            logger.warning(f"Anthropic call failed: {e}, falling back to OpenAI...")

    if openai_client:
        try:
            return _call_openai(prompt), ANALYTICS_MODEL
        except Exception as e:
            last_error = e
            logger.warning(f"OpenAI call failed: {e}")

    raise RuntimeError(f"No LLM provider available. Last error: {last_error}")


# --- Helper: data context preparation ---

def _compute_field_stats(records: List[Dict]) -> Dict[str, Any]:
    """Compute per-field statistics: unique value counts, min/max for numerics."""
    if not records:
        return {}

    # Collect all field names across records
    all_fields: set = set()
    for rec in records:
        if isinstance(rec, dict):
            all_fields.update(rec.keys())

    stats: Dict[str, Any] = {}
    for field in sorted(all_fields):
        values = [rec[field] for rec in records if isinstance(rec, dict) and field in rec]
        if not values:
            continue

        field_stat: Dict[str, Any] = {"total_non_null": len([v for v in values if v is not None])}

        # Numeric stats
        numeric_vals = []
        for v in values:
            if isinstance(v, (int, float)) and v is not None:
                numeric_vals.append(v)

        if numeric_vals:
            field_stat["min"] = min(numeric_vals)
            field_stat["max"] = max(numeric_vals)
            field_stat["mean"] = round(statistics.mean(numeric_vals), 4)
            if len(numeric_vals) > 1:
                field_stat["stdev"] = round(statistics.stdev(numeric_vals), 4)

        # Unique value counts (for non-numeric or mixed)
        if not numeric_vals or len(numeric_vals) < len(values):
            str_values = [str(v) for v in values if v is not None]
            unique_vals = list(dict.fromkeys(str_values))  # preserve order, dedupe
            field_stat["unique_count"] = len(unique_vals)
            field_stat["sample_values"] = unique_vals[:10]

        stats[field] = field_stat

    return stats


def _prepare_data_context(
    data: Any,
    reference_data: Optional[Any],
    data_fields: Optional[List[str]],
) -> Dict[str, Any]:
    """
    Prepare data context for the LLM prompt.

    Returns dict with keys:
      records_sample, total_records, field_stats, is_sampled, reference_sample
    """
    # Count records
    if isinstance(data, list):
        total_records = len(data)
    else:
        total_records = 1

    is_sampled = False
    field_stats: Dict[str, Any] = {}

    if isinstance(data, list) and total_records > SAMPLE_THRESHOLD:
        is_sampled = True
        records_sample = data[:SAMPLE_SIZE]

        # Filter to requested fields if specified
        if data_fields:
            records_sample = [
                {k: v for k, v in rec.items() if k in data_fields}
                if isinstance(rec, dict) else rec
                for rec in records_sample
            ]
            all_records_for_stats = [
                {k: v for k, v in rec.items() if k in data_fields}
                if isinstance(rec, dict) else rec
                for rec in data
            ]
        else:
            all_records_for_stats = data

        # Compute field-level stats over the full dataset
        if all_records_for_stats and isinstance(all_records_for_stats[0], dict):
            field_stats = _compute_field_stats(all_records_for_stats)

    else:
        records_sample = data
        # Filter to requested fields if specified
        if data_fields and isinstance(data, list):
            records_sample = [
                {k: v for k, v in rec.items() if k in data_fields}
                if isinstance(rec, dict) else rec
                for rec in data
            ]

    # Prepare reference data sample
    reference_sample = None
    if reference_data is not None:
        if isinstance(reference_data, list) and len(reference_data) > SAMPLE_THRESHOLD:
            reference_sample = reference_data[:SAMPLE_SIZE]
        else:
            reference_sample = reference_data

    return {
        "records_sample": records_sample,
        "total_records": total_records,
        "field_stats": field_stats,
        "is_sampled": is_sampled,
        "reference_sample": reference_sample,
    }


# --- Helper: build task prompt ---

def _build_task_prompt(
    task: AnalyticsTask,
    data_context: Dict[str, Any],
    context_str: Optional[str],
) -> str:
    total = data_context["total_records"]
    is_sampled = data_context["is_sampled"]
    records_sample = data_context["records_sample"]
    field_stats = data_context["field_stats"]
    reference_sample = data_context["reference_sample"]

    sampled_note = f" (showing first {SAMPLE_SIZE} of {total})" if is_sampled else ""

    lines = [
        "You are a data analytics expert. Analyse the following data and perform the requested task.",
        "",
    ]

    if context_str:
        lines += [f"CONTEXT: {context_str}", ""]

    lines += [
        f"DATA ({total} records{sampled_note}):",
        json.dumps(records_sample, indent=2, default=str),
        "",
    ]

    if field_stats:
        lines += [
            "FIELD STATISTICS:",
            json.dumps(field_stats, indent=2, default=str),
            "",
        ]

    if reference_sample is not None:
        lines += [
            "REFERENCE/COMPARISON DATA:",
            json.dumps(reference_sample, indent=2, default=str),
            "",
        ]

    fields_note = ", ".join(task.data_fields) if task.data_fields else "all fields"
    output_format_instruction = (
        task.output_format
        or "Return a JSON object with your analysis results. Structure the output logically "
        "for the task type. Include counts, percentages, lists, and key findings as appropriate."
    )

    lines += [
        f"TASK: {task.task_name}",
        f"TYPE: {task.task_type or 'custom'}",
        f"FIELDS TO ANALYSE: {fields_note}",
        f"INSTRUCTION: {task.description}",
        "",
        f"OUTPUT FORMAT: {output_format_instruction}",
        "",
        "Return ONLY valid JSON. No markdown, no explanation outside the JSON.",
    ]

    return "\n".join(lines)


# --- Helper: parse LLM JSON response ---

def _parse_llm_json(raw: str) -> Any:
    """Strip markdown fences and parse JSON. Falls back to extracting the outermost
    JSON object/array when the response contains surrounding prose."""
    clean = raw.strip()

    # Strip ```json ... ``` or ``` ... ``` fences
    if clean.startswith("```"):
        lines = clean.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        clean = "\n".join(lines).strip()

    # Attempt 1: direct parse
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass

    # Attempt 2: extract outermost JSON object { ... } or array [ ... ]
    for start_ch, end_ch in [('{', '}'), ('[', ']')]:
        start = clean.find(start_ch)
        end   = clean.rfind(end_ch)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(clean[start:end + 1])
            except json.JSONDecodeError:
                pass

    raise json.JSONDecodeError("Failed to parse LLM response as JSON", clean, 0)


# --- Core analytics runner ---

def _run_analytics(request: AnalyticsRequest) -> Dict[str, Any]:
    tasks = request.tasks
    total_tasks = len(tasks)
    completed = 0
    failed = 0
    model_used = "unknown"
    analytics_results: Dict[str, Any] = {}

    # Determine record count for metadata
    if isinstance(request.data, list):
        records_analysed = len(request.data)
    else:
        records_analysed = 1

    for task in tasks:
        logger.info(f"Running task: {task.task_id} ({task.task_name})")

        # Prepare data context (may differ per task due to data_fields filter)
        data_context = _prepare_data_context(
            request.data,
            request.reference_data,
            task.data_fields,
        )

        # Build prompt
        prompt = _build_task_prompt(task, data_context, request.context)

        logger.info(
            f"\n{'='*60} TASK PROMPT: {task.task_id} {'='*60}\n"
            f"{prompt}\n"
            f"{'='*120}\n"
        )

        # Call LLM
        try:
            raw_response, model_used = _call_llm(prompt)
        except Exception as llm_err:
            logger.error(f"LLM call failed for task {task.task_id}: {llm_err}")
            analytics_results[task.task_id] = {
                "task_name": task.task_name,
                "task_type": task.task_type or "custom",
                "status": "failed",
                "result": None,
                "summary": f"LLM call failed: {llm_err}",
                "record_count": data_context["total_records"],
                "error": str(llm_err),
            }
            failed += 1
            continue

        logger.info(
            f"\n{'='*60} LLM RESPONSE: {task.task_id} (model: {model_used}) {'='*60}\n"
            f"{raw_response}\n"
            f"{'='*120}\n"
        )

        # Parse JSON result
        try:
            result = _parse_llm_json(raw_response)
            parse_error = None
        except (json.JSONDecodeError, ValueError) as parse_err:
            logger.warning(f"JSON parse failed for task {task.task_id}: {parse_err}")
            result = None
            parse_error = f"JSON parse error: {parse_err}. Raw response (first 500 chars): {raw_response[:500]}"

        if parse_error:
            analytics_results[task.task_id] = {
                "task_name": task.task_name,
                "task_type": task.task_type or "custom",
                "status": "failed",
                "result": None,
                "summary": "LLM response could not be parsed as JSON.",
                "record_count": data_context["total_records"],
                "error": parse_error,
            }
            failed += 1
            continue

        # Extract a plain-English summary from the result if possible
        summary = ""
        if isinstance(result, dict):
            summary = result.get("summary") or result.get("description") or ""
        if not summary:
            summary = f"Task '{task.task_name}' completed successfully."

        analytics_results[task.task_id] = {
            "task_name": task.task_name,
            "task_type": task.task_type or "custom",
            "status": "completed",
            "result": result,
            "summary": summary,
            "record_count": data_context["total_records"],
            "error": None,
        }
        completed += 1

    # Determine overall status
    if failed == 0:
        overall_status = "success"
    elif completed == 0:
        overall_status = "error"
    else:
        overall_status = "partial"

    return {
        "status": overall_status,
        "analytics_results": analytics_results,
        "metadata": {
            "total_tasks": total_tasks,
            "completed": completed,
            "failed": failed,
            "records_analysed": records_analysed,
            "model_used": model_used,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }


# --- Endpoints ---

@app.post("/analyse")
async def analyse(request: AnalyticsRequest) -> Dict[str, Any]:
    """Run one or more analytics tasks against the supplied data."""
    logger.info(
        f"[ANALYTICS] /analyse called | tasks={len(request.tasks)} | "
        f"records={len(request.data) if isinstance(request.data, list) else 1}"
    )
    try:
        result = _run_analytics(request)
    except Exception as exc:
        logger.error(f"[ANALYTICS] Unhandled error in /analyse: {exc}", exc_info=True)
        return {
            "status": "error",
            "analytics_results": {},
            "metadata": {
                "total_tasks": len(request.tasks),
                "completed": 0,
                "failed": len(request.tasks),
                "records_analysed": 0,
                "model_used": "unknown",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }
    logger.info(
        f"[ANALYTICS] /analyse finished | status={result['status']} | "
        f"completed={result['metadata']['completed']}/{result['metadata']['total_tasks']}"
    )
    return result


@app.post("/analyse/batch")
async def analyse_batch(requests: List[AnalyticsRequest]) -> List[Dict[str, Any]]:
    """Run multiple AnalyticsRequest objects sequentially and return a list of results."""
    logger.info(f"[ANALYTICS] /analyse/batch called | batch_size={len(requests)}")
    results = []
    for idx, req in enumerate(requests):
        logger.info(f"[ANALYTICS] Batch item {idx + 1}/{len(requests)}")
        try:
            result = _run_analytics(req)
        except Exception as exc:
            logger.error(f"[ANALYTICS] Batch item {idx + 1} failed: {exc}", exc_info=True)
            result = {
                "status": "error",
                "analytics_results": {},
                "metadata": {
                    "total_tasks": len(req.tasks),
                    "completed": 0,
                    "failed": len(req.tasks),
                    "records_analysed": 0,
                    "model_used": "unknown",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            }
        results.append(result)
    logger.info(f"[ANALYTICS] /analyse/batch finished | {len(results)} results returned")
    return results


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok", "service": "data-analytics-agent"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("data_analytics_agent:app", host="0.0.0.0", port=8092, reload=True)
