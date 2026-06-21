#!/usr/bin/env python3
"""
Phase 1 Test Runner for Step 7 Multi-Run Variance Testing
Executes the test_step7_multirun.json workflow and captures results
Supports user input for number of runs via repeat_runs_override
"""

import json
import requests
import sys
import time
from pathlib import Path
from datetime import datetime

def get_user_repeat_runs() -> int:
    """Prompt user for number of runs (like Steps 5 & 6)."""
    print("\n" + "="*80)
    print("STEP 7 MULTI-RUN VARIANCE TEST - USER INPUT")
    print("="*80)
    print("\nHow many times should Step 7 run with identical input?")
    print("(This tests reproducibility and consistency)")
    print("\nOptions:")
    print("  2 = Quick test (minimum for variance checking)")
    print("  3 = Standard test (recommended)")
    print("  5+ = Extended test (comprehensive variance analysis)")
    
    while True:
        try:
            user_input = input("\nEnter number of runs (default=2): ").strip()
            if not user_input:
                return 2  # Default to 2
            
            num_runs = int(user_input)
            if num_runs < 1:
                print("ERROR: Must be at least 1 run")
                continue
            if num_runs > 20:
                print("WARNING: More than 20 runs may take a very long time")
                confirm = input("Continue anyway? (y/n): ").strip().lower()
                if confirm != 'y':
                    continue
            
            return num_runs
        except ValueError:
            print(f"ERROR: '{user_input}' is not a valid number")

def run_phase1_test(config_file: str = "test_step7_multirun.json", 
                   repeat_runs: int = None):
    """
    Execute Phase 1 multi-run test for Step 7
    
    Args:
        config_file: Path to test configuration JSON
        repeat_runs: Number of times to run (prompted if None)
    
    Returns:
        Test result dictionary
    """
    
    # Get user input if not specified
    if repeat_runs is None:
        repeat_runs = get_user_repeat_runs()
    
    print("\n" + "="*80)
    print("PHASE 1: STEP 7 MULTI-RUN VARIANCE TEST")
    print("="*80)
    print(f"Start Time: {datetime.now().isoformat()}")
    print(f"Test Config: {config_file}")
    print(f"Repeat Runs (repeat_runs_override): {repeat_runs}")
    print("="*80 + "\n")
    
    # Load test configuration
    config_path = Path(config_file)
    if not config_path.exists():
        print(f"ERROR: Test config file not found: {config_file}")
        return {"status": "ERROR", "error": "Config file not found"}
    
    with open(config_path) as f:
        test_payload = json.load(f)
    
    print(f"Test Configuration:")
    print(f"  Steps: {len(test_payload.get('steps', []))}")
    for step in test_payload.get('steps', []):
        print(f"    - Step {step.get('step_number')}: {step.get('name')}")
        print(f"      Base Repeat Runs: {step.get('repeat_runs', 1)}")
        print(f"      User Override: {repeat_runs}")
        print(f"      Knowledge ID: {step.get('required_resources', {}).get('knowledge_id', 'N/A')}")
    print()
    
    # Submit test to orchestrator using user input approach (like Steps 5 & 6)
    print("Submitting test to orchestrator...")
    print(f"Using repeat_runs_override={repeat_runs} (user input)\n")
    
    try:
        # Step 1: Initialize plan with /execute endpoint
        print("[1/2] Initializing plan...")
        init_response = requests.post(
            "http://localhost:8001/execute",
            json=test_payload,
            timeout=30
        )
        
        if init_response.status_code != 200:
            print(f"ERROR: Failed to initialize plan: {init_response.text}")
            return {
                "status": "ERROR",
                "http_status": init_response.status_code,
                "error": init_response.text
            }
        
        init_data = init_response.json()
        plan_id = init_data.get("plan_id")
        print(f"✓ Plan initialized: {plan_id}\n")
        
        # Step 2: Execute step with repeat_runs_override parameter
        print("[2/2] Executing Step 7 with user-specified runs...")
        start_time = time.time()
        
        # Use /run_next endpoint with repeat_runs_override as form parameter
        response = requests.post(
            f"http://localhost:8001/run_next/{plan_id}",
            data={
                "repeat_runs_override": repeat_runs,
                "folder_path": None,
                "step_index": None
            },
            timeout=1800  # 30 minute timeout for multi-run test
        )
        
        elapsed = time.time() - start_time
        
        print(f"Response Status: {response.status_code}")
        print(f"Elapsed Time: {elapsed:.2f} seconds ({elapsed/60:.2f} minutes)\n")
        
        if response.status_code != 200:
            print(f"ERROR: {response.text}")
            return {
                "status": "ERROR",
                "http_status": response.status_code,
                "error": response.text
            }
        
        result = response.json()
        
        # Extract multi-run results if present
        print("Test Results:")
        print("-" * 80)
        
        # Check for execution ID
        if "task_id" in result:
            print(f"Task ID: {result['task_id']}")
        if "execution_id" in result:
            print(f"Execution ID: {result['execution_id']}")
        
        # Look for extraction results
        if "results" in result:
            results = result["results"]
            
            # Check for multi-run data
            for key in results:
                if "extraction_step_7" in key:
                    print(f"\n{key}:")
                    
                    if "_multirun" in key:
                        multirun_data = results[key]
                        print(f"  Repeat Runs: {multirun_data.get('repeat_run_count', 0)}")
                        print(f"  Output Files: {len(multirun_data.get('repeat_run_output_files', []))}")
                        
                        runs = multirun_data.get('runs', [])
                        print(f"  Individual Runs: {len(runs)}")
                        
                        for run_idx, run in enumerate(runs, 1):
                            print(f"\n    Run {run_idx}:")
                            print(f"      Timestamp: {run.get('run_timestamp', 'N/A')}")
                            if run.get('data'):
                                print(f"      Files Processed: {run['data'].get('total_files', '?')}")
                                extractions = run['data'].get('extractions', [])
                                if extractions:
                                    print(f"      Extractions: {len(extractions)}")
                            print(f"      Output: {run.get('output_file', 'N/A')}")
                    else:
                        # Primary result
                        data = results[key]
                        print(f"  Total Files: {data.get('total_files', 'N/A')}")
                        print(f"  Extractions: {len(data.get('extractions', []))}")
        
        # Check execution log
        if "log" in result:
            print(f"\nExecution Log (last 1000 chars):")
            print("-" * 80)
            log = result["log"]
            if len(log) > 1000:
                print("...(truncated)...")
                print(log[-1000:])
            else:
                print(log)
        
        # Check for errors
        if "error" in result:
            print(f"\nError: {result['error']}")
        
        print("\n" + "="*80)
        print(f"End Time: {datetime.now().isoformat()}")
        print("="*80 + "\n")
        
        return {
            "status": "SUCCESS",
            "task_id": result.get("task_id"),
            "plan_id": plan_id,
            "repeat_runs": repeat_runs,
            "elapsed_seconds": elapsed,
            "result": result
        }
        
    except requests.exceptions.Timeout:
        print(f"ERROR: Request timeout after 1800 seconds")
        return {
            "status": "ERROR",
            "error": "Orchestrator request timeout"
        }
    except requests.exceptions.ConnectionError as e:
        print(f"ERROR: Connection failed - {e}")
        print("Is the orchestrator running on port 8001?")
        return {
            "status": "ERROR",
            "error": f"Connection failed: {e}"
        }
    except Exception as e:
        print(f"ERROR: {e}")
        return {
            "status": "ERROR",
            "error": str(e)
        }


if __name__ == "__main__":
    # Get repeat_runs from command line arg or prompt user
    repeat_runs = None
    if len(sys.argv) > 1:
        try:
            repeat_runs = int(sys.argv[1])
            print(f"Using command-line argument: repeat_runs={repeat_runs}")
        except ValueError:
            print(f"Invalid argument: {sys.argv[1]} is not a number")
            print("Usage: python run_phase1_test.py [repeat_runs]")
            sys.exit(1)
    
    # Run the test
    result = run_phase1_test(repeat_runs=repeat_runs)
    
    # Save results to file
    output_file = f"phase1_test_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, 'w') as f:
        json.dump(result, f, indent=2, default=str)
    
    print(f"Results saved to: {output_file}")
    
    # Exit with appropriate code
    sys.exit(0 if result["status"] == "SUCCESS" else 1)
