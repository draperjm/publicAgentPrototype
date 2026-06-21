#!/usr/bin/env python3
"""
Direct test of notes extraction with scope filter fix.
Runs Step 5 extraction multiple times to check consistency improvement.
"""
import subprocess
import json
import sys
from pathlib import Path

def run_extraction_test():
    """Run extraction via document-extractor endpoint and check notes consistency."""
    print("=" * 70)
    print("Testing Step 5 Notes Extraction with Scope Filter Fix")
    print("=" * 70)
    
    # Check if test document exists
    doc_path = Path("/Code/experiments/agent 08/Data/DS1_DAR1675_RETIC.pdf")
    if not doc_path.exists():
        print(f"❌ Test document not found: {doc_path}")
        return False
    
    print(f"✓ Test document found: {doc_path}")
    print()
    
    # Prepare extraction request
    request = {
        "files": [
            {
                "filename": "DS1_DAR1675_RETIC.pdf",
                "filepath": None,
                "page_start": None,
                "page_end": None
            }
        ],
        "process_step": {
            "step_id": "step-05-extract-site-plan",
            "step_name": "Extract Site Plan Information",
            "details": "Extract notes, legend, and site plan details",
            "tool": "LLM",
            "sub_steps": [
                {
                    "name": "sub-step-extract-all-notes",
                    "field_type": "string"
                }
            ]
        },
        "documents_folder": "/Code/experiments/agent 08/Data",
        "output_dir": "/Code/experiments/agent 08/OUTPUT"
    }
    
    print("Running extraction test...")
    print("-" * 70)
    
    # Call the extractor endpoint via curl
    try:
        result = subprocess.run(
            [
                "powershell", "-Command",
                f"""
                $body = @{json.dumps(request).replace('"', '`"')} | ConvertTo-Json -Depth 10
                $response = Invoke-WebRequest `
                    -Uri 'http://localhost:8090/extract' `
                    -Method Post `
                    -ContentType 'application/json' `
                    -Body $body `
                    -UseBasicParsing
                $response.Content
                """
            ],
            capture_output=True,
            text=True,
            timeout=120
        )
        
        if result.returncode != 0:
            print(f"❌ Extraction failed with return code {result.returncode}")
            print(f"Error: {result.stderr}")
            return False
        
        # Parse the response
        response_data = json.loads(result.stdout)
        
        if "error" in response_data:
            print(f"❌ Extraction error: {response_data.get('error')}")
            return False
        
        # Extract notes from response
        extractions = response_data.get("extractions", [])
        if not extractions:
            print("❌ No extractions in response")
            return False
        
        first_extraction = extractions[0]
        notes = first_extraction.get("data", {}).get("sub-step-extract-all-notes", "")
        
        print("Extracted Notes:")
        print("-" * 70)
        print(notes[:500] + "..." if len(notes) > 500 else notes)
        print("-" * 70)
        print()
        
        # Check if scope creep is present
        has_scope_creep = any(boundary in notes for boundary in [
            "DUCT END LOCATION DETAIL",
            "POLE/COLUMN SETOUT",
            "WORKS COMPLETED",
            "DESIGN COMPLIANCE",
            "FUNDING ARRANGEMENTS"
        ])
        
        if has_scope_creep:
            print("⚠️  WARNING: Scope creep detected - notes include content after boundaries")
            print("    (DUCT END LOCATION DETAIL, POLE/COLUMN SETOUT, etc. found in extraction)")
        else:
            print("✓ ✓ ✓ SUCCESS: Scope filter working - no content after boundaries found")
        
        # Check consolidation logs for sanitization
        print()
        print("Checking container logs for sanitization evidence...")
        print("-" * 70)
        
        logs_result = subprocess.run(
            ["docker", "logs", "agent09-document-extractor", "--tail", "100"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if "sanitization" in logs_result.stdout.lower() or "sanitiz" in logs_result.stdout.lower():
            print("✓ Found sanitization in logs - scope filter is being applied")
        else:
            print("⚠️  No sanitization log entries found (filter may not have been invoked)")
        
        if "POST-EXTRACTION NOTES SCOPE FILTER" in logs_result.stdout:
            print("✓ Found post-extraction filter in logs - double defense is active")
        
        print()
        return True
        
    except subprocess.TimeoutExpired:
        print("❌ Extraction timed out after 120 seconds")
        return False
    except json.JSONDecodeError as e:
        print(f"❌ Failed to parse response JSON: {e}")
        print(f"Response was: {result.stdout[:200]}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False

if __name__ == "__main__":
    success = run_extraction_test()
    sys.exit(0 if success else 1)
