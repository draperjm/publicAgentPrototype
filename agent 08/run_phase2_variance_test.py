#!/usr/bin/env python3
"""
Phase 2: Variance Testing & Reporting Execution
Runs Step 7 multiple times and analyzes output consistency
"""

import io
import json
import sys
import time
from datetime import datetime
from pathlib import Path
import requests

# Force UTF-8 output on Windows so orchestrator log messages with Unicode
# characters (e.g. → arrows) don't crash the polling loop.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ORCHESTRATOR_URL = "http://localhost:8001"
VARIANCE_TEST_CONFIG = "phase2_complete_test.json"
OUTPUT_DIR = Path("OUTPUT")
PHASE2_RUNS = 3
POLL_INTERVAL = 2
MAX_POLLS = 300

def load_test_config(config_file: str) -> dict:
    """Load test configuration from JSON file."""
    with open(config_file, 'r') as f:
        return json.load(f)

def submit_plan(repeat_runs: int = 3) -> str:
    """Submit execution plan to orchestrator. Returns the plan_id."""
    config = load_test_config(VARIANCE_TEST_CONFIG)
    
    # Update repeat_runs in config
    for step in config.get("steps", []):
        if step.get("step_id") == "proc-extract-asset-spreadsheet" or \
           step.get("name") == "ExtractAssetSpreadsheet":
            step["repeat_runs"] = repeat_runs
    
    print(f"  Submitting test with repeat_runs={repeat_runs}...")
    
    try:
        response = requests.post(f"{ORCHESTRATOR_URL}/execute", json=config, timeout=10)
        data = response.json()
        if "plan_id" in data:
            return data["plan_id"]
        else:
            print(f"  ERROR: No plan_id in response: {data}")
            return None
    except Exception as e:
        print(f"  ERROR submitting plan: {e}")
        return None

def run_plan(plan_id: str) -> dict:
    """Execute the plan by calling run_next."""
    print(f"  Executing plan {plan_id}...")
    
    try:
        response = requests.post(
            f"{ORCHESTRATOR_URL}/run_next/{plan_id}",
            data={"folder_path": "documents"},
            timeout=10
        )
        return response.json()
    except Exception as e:
        print(f"  ERROR executing plan: {e}")
        return None

def poll_results(plan_id: str, max_polls: int = MAX_POLLS) -> dict:
    """Poll the orchestrator for step results."""
    print(f"  Polling for results...")
    
    for poll_num in range(max_polls):
        try:
            response = requests.get(
                f"{ORCHESTRATOR_URL}/step_result/{plan_id}",
                timeout=10
            )
            result = response.json()
            
            if result.get("status") == "completed":
                print(f"  Results ready (poll #{poll_num+1})")
                return result
            else:
                if poll_num % 10 == 0:
                    print(f"  > Still processing... (poll #{poll_num+1})")
                time.sleep(POLL_INTERVAL)
        except Exception as e:
            print(f"  ! Poll error: {e}")
            time.sleep(POLL_INTERVAL)
    
    print(f"  ERROR: Timeout waiting for results")
    return None

def extract_variance_metrics(results: dict) -> dict:
    """Extract variance metrics from multi-run results."""
    metrics = {
        "total_runs": 0,
        "successful_runs": 0,
        "failed_runs": 0,
        "asset_counts": [],
        "worksheets_processed": [],
        "variance": {
            "asset_count_variance": None,
            "sheet_coverage_consistency": None,
            "timestamp_distribution": [],
        }
    }
    
    # Find multirun data in results
    multirun_key = None
    for key in results.keys():
        if "multirun" in key.lower():
            multirun_key = key
            break
    
    if not multirun_key:
        print(f"  WARNING: No multirun data found. Available keys: {list(results.keys())}")
        return metrics
    
    multirun_data = results.get(multirun_key, {})
    runs = multirun_data.get("runs", [])
    
    metrics["total_runs"] = len(runs)
    
    for i, run in enumerate(runs):
        try:
            if run.get("data"):
                metrics["successful_runs"] += 1
                
                # Count assets extracted
                asset_extract = run.get("data", {}).get("asset_extract", {})
                total_assets = asset_extract.get("total_assets", 0)
                metrics["asset_counts"].append(total_assets)
                
                # Track worksheets processed
                worksheets = []
                extractions = run.get("data", {}).get("extractions", [])
                if isinstance(extractions, list):
                    for extraction in extractions:
                        sheets_processed = extraction.get("sheets_processed", [])
                        worksheets.extend(sheets_processed)
                metrics["worksheets_processed"].append(sorted(set(worksheets)))
                
                # Track timestamps
                timestamp = run.get("run_timestamp", f"run_{i+1}")
                metrics["variance"]["timestamp_distribution"].append({
                    "run": i + 1,
                    "timestamp": timestamp
                })
            else:
                metrics["failed_runs"] += 1
        except Exception as e:
            print(f"  WARNING: Error parsing run {i+1}: {e}")
            metrics["failed_runs"] += 1
    
    # Calculate variance metrics
    if metrics["asset_counts"]:
        min_assets = min(metrics["asset_counts"])
        max_assets = max(metrics["asset_counts"])
        avg_assets = sum(metrics["asset_counts"]) / len(metrics["asset_counts"])
        metrics["variance"]["asset_count_variance"] = {
            "min": min_assets,
            "max": max_assets,
            "average": avg_assets,
            "range": max_assets - min_assets,
            "consistency": "PERFECT" if min_assets == max_assets else "VARIABLE"
        }
    
    # Check worksheet coverage consistency
    if metrics["worksheets_processed"]:
        all_same = all(ws == metrics["worksheets_processed"][0] for ws in metrics["worksheets_processed"])
        metrics["variance"]["sheet_coverage_consistency"] = {
            "all_runs_identical": all_same,
            "unique_sheet_sets": list(set(tuple(sorted(ws)) for ws in metrics["worksheets_processed"])),
            "coverage": "CONSISTENT" if all_same else "INCONSISTENT"
        }
    
    return metrics

def generate_variance_report(metrics: dict, output_file: Path):
    """Generate comprehensive variance analysis report."""
    report = f"""# Phase 2: Variance Testing & Analysis Report

**Generated:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**Test Type:** Step 7 (Extract Asset Spreadsheet) Multi-Run Variance Analysis

---

## Executive Summary

| Metric | Value |
|--------|-------|
| **Total Runs** | {metrics['total_runs']} |
| **Successful Runs** | {metrics['successful_runs']} |
| **Failed Runs** | {metrics['failed_runs']} |
| **Success Rate** | {100 * metrics['successful_runs'] / max(1, metrics['total_runs']):.1f}% |

---

## Asset Extraction Consistency

### Asset Counts Per Run

| Run | Assets Extracted | Status |
|-----|------------------|--------|
"""
    
    for i, count in enumerate(metrics["asset_counts"], 1):
        report += f"| {i} | {count} | Success |\n"
    
    # Variance analysis
    if metrics["variance"]["asset_count_variance"]:
        variance = metrics["variance"]["asset_count_variance"]
        report += f"""

### Variance Analysis

- **Minimum Assets:** {variance['min']}
- **Maximum Assets:** {variance['max']}
- **Average Assets:** {variance['average']:.1f}
- **Range:** {variance['range']}
- **Consistency:** {variance['consistency']}

"""
        if variance['range'] == 0:
            report += "**Result:** PERFECT CONSISTENCY - All runs extracted identical assets\n"
        elif variance['range'] <= 2:
            report += "**Result:** NEAR-PERFECT - Minimal variance between runs\n"
        else:
            report += f"**Result:** VARIABLE - Asset counts vary by {variance['range']} across runs\n"
    
    # Worksheet coverage
    report += f"""

---

## Worksheet Processing Coverage

"""
    
    if metrics["worksheets_processed"]:
        report += "### Worksheets Processed Per Run\n\n"
        report += "| Run | Worksheets | Status |\n"
        report += "|-----|-----------|--------|\n"
        for i, sheets in enumerate(metrics["worksheets_processed"], 1):
            sheet_names = ", ".join(sheets) if sheets else "(none)"
            report += f"| {i} | {sheet_names} | OK |\n"
        
        if metrics["variance"]["sheet_coverage_consistency"]:
            coverage = metrics["variance"]["sheet_coverage_consistency"]
            report += f"""

### Coverage Consistency

- **All Runs Identical:** {'Yes' if coverage['all_runs_identical'] else 'No'}
- **Unique Sheet Sets:** {len(coverage['unique_sheet_sets'])}
- **Coverage Status:** {coverage['coverage']}

"""
    
    # Execution timeline
    report += f"""---

## Execution Timeline

| Run | Timestamp |
|-----|-----------|
"""
    for ts_info in metrics["variance"]["timestamp_distribution"]:
        report += f"| {ts_info['run']} | {ts_info['timestamp']} |\n"
    
    # Conclusions
    report += f"""

---

## Conclusions & Next Steps

### Overall Assessment

"""
    success_rate = 100 * metrics['successful_runs'] / max(1, metrics['total_runs'])
    
    if success_rate == 100:
        report += "SUCCESS - All runs completed successfully\n\n"
    else:
        report += f"PARTIAL SUCCESS - {success_rate:.0f}% completion rate\n\n"
    
    asset_consistency = metrics["variance"]["asset_count_variance"]
    if asset_consistency and asset_consistency['range'] == 0:
        report += "Data Consistency: Perfect - Zero variance across runs\n"
    elif asset_consistency:
        report += f"Data Consistency: {asset_consistency['consistency']} (range: {asset_consistency['range']})\n"
    
    worksheet_consistency = metrics["variance"]["sheet_coverage_consistency"]
    if worksheet_consistency and worksheet_consistency['all_runs_identical']:
        report += "Worksheet Coverage: Identical across all runs (both PROJECT and STREETLIGHT)\n"
    elif worksheet_consistency:
        report += "Worksheet Coverage: Inconsistent - Different sheets in different runs\n"
    
    report += f"""

### Phase 3 Recommendations

1. If asset variance detected: Implement deterministic extraction validation
2. If worksheet coverage varies: Debug worksheet selection logic  
3. Target: Achieve zero-variance reproducibility (April 25-May 1, 2026)

---

## Test Artifacts

- **Test Date:** {datetime.now().strftime("%Y-%m-%d")}
- **Runs Executed:** {metrics['total_runs']}
- **Report Generated:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

---

*Generated by Phase 2 Variance Testing Framework*
"""
    
    # Write with UTF-8 encoding
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(report)
    
    return report

def main():
    """Execute Phase 2 variance testing."""
    print("\n" + "="*80)
    print("PHASE 2: VARIANCE TESTING & REPORTING - EXECUTION")
    print("="*80)
    print(f"\nStart Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Configured Runs: {PHASE2_RUNS}")
    print(f"Orchestrator: {ORCHESTRATOR_URL}")
    
    # Step 1: Submit
    print(f"\n[1/4] Submitting execution plan...")
    plan_id = submit_plan(PHASE2_RUNS)
    if not plan_id:
        print("ERROR: Failed to submit plan")
        sys.exit(1)
    print(f"  Plan ID: {plan_id}")
    
    # Step 2: Execute
    print(f"\n[2/4] Executing plan...")
    exec_result = run_plan(plan_id)
    if not exec_result:
        print("ERROR: Failed to execute plan")
        sys.exit(1)
    
    # Step 3: Poll
    print(f"\n[3/4] Waiting for execution to complete...")
    test_response = poll_results(plan_id)
    if not test_response:
        print("ERROR: Failed to retrieve results")
        sys.exit(1)
    
    # Step 4: Report
    print(f"\n[4/4] Generating variance analysis report...")
    metrics = extract_variance_metrics(test_response)
    
    print(f"\nVARIANCE METRICS:")
    print(f"  Total Runs: {metrics['total_runs']}")
    print(f"  Successful: {metrics['successful_runs']}/{metrics['total_runs']}")
    print(f"  Asset Counts: {metrics['asset_counts']}")
    if metrics["variance"]["asset_count_variance"]:
        vc = metrics["variance"]["asset_count_variance"]
        print(f"  Asset Variance: {vc['range']} ({vc['consistency']})")
    if metrics["variance"]["sheet_coverage_consistency"]:
        sc = metrics["variance"]["sheet_coverage_consistency"]
        print(f"  Sheet Coverage: {'CONSISTENT' if sc['all_runs_identical'] else 'INCONSISTENT'}")
    
    # Save report
    OUTPUT_DIR.mkdir(exist_ok=True)
    report_file = OUTPUT_DIR / f"PHASE2_VARIANCE_REPORT_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    report_text = generate_variance_report(metrics, report_file)
    
    print(f"\nPhase 2 Complete!")
    print(f"  Report: {report_file}")
    
    # Save JSON results
    results_file = OUTPUT_DIR / f"phase2_variance_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(results_file, 'w', encoding='utf-8') as f:
        json.dump({
            "phase": 2,
            "timestamp": datetime.now().isoformat(),
            "metrics": metrics,
            "full_response": test_response
        }, f, indent=2, ensure_ascii=False)
    
    print(f"  Results: {results_file}")
    print(f"\nFinished at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

if __name__ == "__main__":
    main()
