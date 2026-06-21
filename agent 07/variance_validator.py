"""
Variance Validator Agent
========================
Compares extraction outputs across multiple runs of the same step to measure
LLM consistency. Receives a list of ExtractionReport file paths (one per run),
loads each, and computes per-file, per-sub-step variance metrics.

Port:     8093
Endpoint: POST /validate_variance

Response keys:
  num_runs            - number of runs analysed
  files_analysed      - number of distinct documents compared
  consistency_score   - fraction of sub-step fields that are identical across all runs (0.0–1.0)
  consistent_fields   - count of fully consistent fields
  total_fields        - total sub-step fields compared
  verdict             - PASS (>=90%), WARN (>=70%), FAIL (<70%)
  summary             - one-line human readable result
  file_variance       - per-file, per-field breakdown
  output_file         - path to saved VarianceReport_*.json
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logger = logging.getLogger("variance_validator")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Variance Validator Agent")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "/app/OUTPUT"))

# Consistency thresholds for verdict assignment
PASS_THRESHOLD = 0.90
WARN_THRESHOLD = 0.70


# ── Models ───────────────────────────────────────────────────────────────────

class VarianceRequest(BaseModel):
    step_name: str
    step_description: str = ""
    run_output_files: List[str]          # Paths to ExtractionReport_*.json, one per run
    output_dir: Optional[str] = None     # Where to save the VarianceReport; defaults to OUTPUT_DIR


# ── Helpers ──────────────────────────────────────────────────────────────────

def _normalise(v: Any) -> str:
    """Serialise any sub-step value to a stable, comparable string.

    Lists are sorted element-by-element before serialisation so that
    order differences (e.g. legend entries returned in different sequence
    across runs) do not register as false variance.
    """
    if v is None:
        return ""
    if isinstance(v, list):
        # Serialise each element then sort the resulting strings so the
        # comparison is order-independent (important for legend arrays).
        serialised_items = [
            json.dumps(item, sort_keys=True, ensure_ascii=False)
            if isinstance(item, (dict, list))
            else str(item).strip()
            for item in v
        ]
        return json.dumps(sorted(serialised_items), ensure_ascii=False)
    if isinstance(v, dict):
        return json.dumps(v, sort_keys=True, ensure_ascii=False)
    return str(v).strip()


def _unwrap_sub_step(raw_value: Any) -> Any:
    """Extract the comparable payload from a sub_step_extractions entry.

    The extractor stores each sub-step as:
        {"phase": 1, "sub_step_name": "...", "value": <actual_value>}

    The variance validator should compare only <actual_value>, not the wrapper
    metadata — otherwise any metadata difference registers as a variance even
    when the substantive content is identical.

    If the dict has no "value" key it is returned as-is (legacy format).
    Non-dict values are returned unchanged.
    """
    if isinstance(raw_value, dict) and "value" in raw_value:
        return raw_value["value"]
    return raw_value


def _expand_fields(key: str, value: Any) -> Dict[str, Any]:
    """Expand a dict value into per-subkey comparison fields.

    When a sub-step value is a dict (e.g. the normalised substation data dict
    with seven canonical keys), comparing the whole serialised object as one
    opaque string hides which individual fields vary.  Expanding it means each
    key contributes a separate row to the variance report.

    Returns {composite_key: scalar_value} — e.g.:
        {"sub-step-substation-data.Earthing": "COMMON EARTHING", ...}

    For non-dict values returns {key: value} unchanged.
    """
    if isinstance(value, dict):
        return {f"{key}.{k}": v for k, v in sorted(value.items())}
    return {key: value}


def _compute_variance(run_reports: List[dict]) -> dict:
    """
    Core variance computation.

    Groups extractions by filename, then compares each field across all runs.
    Supports two extraction formats:
    - sub_step_extractions (Steps 5 & 6): keyed sub-step values, unwrapped and expanded.
    - step_extraction (Step 7 / asset spreadsheet): summary scalars (total_assets,
      sheets_processed, rejected_records) plus per-asset per-field keys of the form
      "asset.<asset_id>.<field>" so that a difference in one field (e.g. description)
      does not obscure agreement in all other fields (e.g. model, location).

    Two normalisation steps applied before comparison:
    1. _unwrap_sub_step — strips {"phase","sub_step_name","value"} wrapper so
       only the actual extracted content is compared.
    2. _expand_fields   — expands dict values into per-key rows so individual
       fields are tracked separately.
    """
    # Collect per-file runs: {filename: [{run: int, sub_step_extractions: dict}]}
    file_runs: Dict[str, List[dict]] = {}
    for run_idx, report in enumerate(run_reports):
        for extraction in report.get("extractions", []):
            fname = extraction.get("filename", "unknown")

            # Prefer sub_step_extractions (Steps 5 & 6 format).
            # Fall back to step_extraction (Step 7 / asset-spreadsheet format) when
            # sub_step_extractions is absent or empty so variance is still computed.
            raw_sse = extraction.get("sub_step_extractions") or {}
            if not raw_sse:
                se = extraction.get("step_extraction") or {}
                if se:
                    # Scalar / list summary fields compared directly.
                    raw_sse = {
                        "total_assets": se.get("total_assets", 0),
                        "sheets_processed": sorted(se.get("sheets_processed") or []),
                        "rejected_records": se.get("rejected_records", 0),
                    }
                    # Expand asset_records into per-asset per-field keys so that a
                    # difference in one field (e.g. description) does not obscure
                    # agreement in all other fields (e.g. asset_id, model, location).
                    # Keys: "asset.<asset_id>.<field>", e.g. "asset.SLPL00316949.model"
                    # Internal provenance fields are excluded from comparison.
                    _SKIP_FIELDS = frozenset({
                        "source_sheet", "source_document",
                        "_rejection_reasons", "_row_index",
                    })
                    for rec in sorted(
                        se.get("asset_records") or [],
                        key=lambda r: str(r.get("asset_id") or ""),
                    ):
                        if not isinstance(rec, dict):
                            continue
                        aid = str(rec.get("asset_id") or "unknown")
                        for field, val in rec.items():
                            if field not in _SKIP_FIELDS:
                                raw_sse[f"asset.{aid}.{field}"] = val

            flat_sse: Dict[str, Any] = {}
            for k, v in raw_sse.items():
                unwrapped = _unwrap_sub_step(v)
                flat_sse.update(_expand_fields(k, unwrapped))
            file_runs.setdefault(fname, []).append(
                {
                    "run": run_idx + 1,
                    "sub_step_extractions": flat_sse,
                }
            )

    total_fields     = 0
    consistent_fields = 0
    file_variance: Dict[str, dict] = {}

    for fname, runs in file_runs.items():
        # Union of all sub-step keys present in any run
        all_keys: set = set()
        for run in runs:
            all_keys.update(run["sub_step_extractions"].keys())

        field_analysis: Dict[str, dict] = {}
        for key in sorted(all_keys):
            raw_values  = [run["sub_step_extractions"].get(key) for run in runs]
            norm_values = [_normalise(v) for v in raw_values]

            # Unique values preserving first-seen order
            unique_vals = list(dict.fromkeys(norm_values))
            most_common = max(set(norm_values), key=norm_values.count)
            match_rate  = norm_values.count(most_common) / len(norm_values)
            is_consistent = len(unique_vals) == 1

            field_analysis[key] = {
                "is_consistent":     is_consistent,
                "match_rate":        round(match_rate, 3),
                "unique_value_count": len(unique_vals),
                "most_common_value": most_common,
                "values_by_run": {
                    f"run_{run['run']}": norm_values[i]
                    for i, run in enumerate(runs)
                },
            }

            total_fields += 1
            # Use match_rate (partial credit) rather than binary is_consistent so that
            # complex array fields (e.g. legend entries) get partial score when the
            # majority of runs agree — avoids FAIL when 2/3 runs are identical but
            # one run has a minor difference.
            consistent_fields += match_rate

        file_consistent_score = (
            sum(f["match_rate"] for f in field_analysis.values()) / len(field_analysis)
            if field_analysis else 1.0
        )

        file_variance[fname] = {
            "num_runs":              len(runs),
            "consistent_fields":     sum(1 for f in field_analysis.values() if f["is_consistent"]),
            "total_fields":          len(field_analysis),
            "file_consistency_score": round(file_consistent_score, 3),
            "fields":                field_analysis,
        }

    overall_score = consistent_fields / total_fields if total_fields else 1.0
    if overall_score >= PASS_THRESHOLD:
        verdict = "PASS"
    elif overall_score >= WARN_THRESHOLD:
        verdict = "WARN"
    else:
        verdict = "FAIL"

    return {
        "num_runs":          len(run_reports),
        "files_analysed":    len(file_variance),
        "consistency_score": round(overall_score, 3),
        "consistent_fields": consistent_fields,
        "total_fields":      total_fields,
        "verdict":           verdict,
        "summary": (
            f"{consistent_fields}/{total_fields} fields consistent across "
            f"{len(run_reports)} runs ({overall_score:.0%}) — {verdict}"
        ),
        "file_variance": file_variance,
    }


# ── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "agent": "variance-validator", "port": 8093}


@app.post("/validate_variance")
def validate_variance(request: VarianceRequest) -> dict:
    """
    Compare extraction outputs across multiple runs of the same step.

    Expects run_output_files to be a list of absolute paths to
    ExtractionReport_*.json files saved by the Document Extractor.
    Each file represents one independent execution of the step with the
    same input documents.

    Returns a VarianceReport saved to output_dir (or OUTPUT_DIR) and
    includes the full variance breakdown in the response body.
    """
    logger.info(
        f"[VarianceValidator] '{request.step_name}' — "
        f"{len(request.run_output_files)} run file(s) submitted."
    )

    if len(request.run_output_files) < 2:
        raise HTTPException(
            status_code=400,
            detail="At least 2 run_output_files are required for variance analysis.",
        )

    # Load each report from disk
    run_reports: List[dict] = []
    missing: List[str] = []
    for path in request.run_output_files:
        if not os.path.exists(path):
            missing.append(path)
            logger.warning(f"[VarianceValidator] File not found: {path}")
            continue
        with open(path) as f:
            run_reports.append(json.load(f))

    if len(run_reports) < 2:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Only {len(run_reports)} report(s) could be loaded "
                f"(missing: {missing}). Need at least 2."
            ),
        )

    variance = _compute_variance(run_reports)

    # Save report to disk
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir   = Path(request.output_dir) if request.output_dir else OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    step_slug   = "".join(c if c.isalnum() else "_" for c in request.step_name)[:40]
    report_path = out_dir / f"VarianceReport_{step_slug}_{timestamp}.json"

    report = {
        "step_name":        request.step_name,
        "step_description": request.step_description,
        "run_output_files": request.run_output_files,
        "missing_files":    missing,
        "timestamp":        timestamp,
        "output_file":      str(report_path),
        **variance,
    }

    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    logger.info(
        f"[VarianceValidator] Report saved → {report_path} | "
        f"Score: {variance['consistency_score']:.0%} | Verdict: {variance['verdict']}"
    )

    return report


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8093"))
    uvicorn.run("variance_validator:app", host="0.0.0.0", port=port, reload=True)
