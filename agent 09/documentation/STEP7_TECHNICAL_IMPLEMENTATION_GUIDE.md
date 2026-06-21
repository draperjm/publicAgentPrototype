# Step 7 Technical Implementation Guide

**Purpose:** Step-by-step technical instructions for implementing Step 7 enhancements  
**Target Audience:** Development engineers  
**Prerequisite Reading:** STEP7_ENHANCEMENT_PLAN.md

---

## Part 1: Orchestrator Verification (Day 1)

### 1.1 Verify repeat_runs Support in Model

**File:** `c:\Code\experiments\agent 07\orchestrator.py`

**Action:** Search for `repeat_runs` in PlanStep model definition
```bash
grep -n "repeat_runs" orchestrator.py
```

**Expected Result:**
```python
class PlanStep(BaseModel):
    ...
    repeat_runs: Optional[int] = None  # Line X
```

If not found:
- Add `repeat_runs: Optional[int] = None` to PlanStep class
- Add docstring explaining: "If > 1, orchestrator will execute this step N times with identical input for variance testing"

### 1.2 Verify _run_extractor_step() Handles repeat_runs

**File:** `c:\Code\experiments\agent 07\orchestrator.py`

**Location:** Find `async def _run_extractor_step()`

**Current Expected Behavior:**
```python
async def _run_extractor_step(self, step: PlanStep, ...):
    # MISSING: repeat_runs logic
    # Currently executes once regardless of step.repeat_runs value
    result = await self._call_document_extractor(...)
    return result
```

**Required Changes:**
```python
async def _run_extractor_step(self, step: PlanStep, ...):
    """Execute extractor step, optionally multiple times for variance testing."""
    
    repeat_count = step.repeat_runs or 1
    all_results = []
    
    for run_num in range(1, repeat_count + 1):
        # Generate unique timestamp for this run
        run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Execute step with identical input
        result = await self._call_document_extractor(
            step=step,
            file_entries=file_entries,
            run_number=run_num,
            timestamp=run_timestamp
        )
        
        # Append to results list
        all_results.append({
            "run_number": run_num,
            "timestamp": run_timestamp,
            "result": result
        })
        
        logger.info(f"Completed run {run_num}/{repeat_count} for {step.name}")
    
    # Return consolidated results
    if repeat_count == 1:
        return all_results[0]["result"]
    else:
        return {
            "multi_run_test": True,
            "repeat_runs": repeat_count,
            "runs": all_results,
            "consolidation_timestamp": datetime.now().isoformat()
        }
```

### 1.3 Create Test Workflow JSON

**File:** `c:\Code\experiments\agent 07\test_step7_multirun.json`

**Template:**
```json
{
  "workflow_name": "Step 7 Multi-Run Variance Test",
  "execution_plan": [
    {
      "step_number": 7,
      "description": "Extract asset spreadsheet with variance testing",
      "name": "ExtractAssetSpreadsheet",
      "assigned_resource_id": "document_extractor_01",
      "repeat_runs": 3,
      "validation": {
        "enabled": true,
        "min_pass_rate": 0.95,
        "checks": ["TC-001", "TC-002", "TC-003", "TC-004", "TC-005", "TC-006"]
      }
    }
  ],
  "input_test_data": {
    "document_path": "path/to/test_tal.xlsx",
    "expected_sheets": ["Assets", "Equipment", "Inventory"],
    "document_type": "tal"
  }
}
```

**Save Location:** `c:\Code\experiments\agent 07\test_step7_multirun.json`

---

## Part 2: Document Extractor Enhancements (Days 2-4)

### 2.1 Implement Asset ID Robustness (Fix #1)

**File:** `c:\Code\experiments\agent 07\document_extractor.py`

**Location:** Add new function after _extract_asset_spreadsheet()

**Implementation:**

```python
import re
from typing import Dict, List, Tuple

def _normalize_asset_id_extraction(
    sheet: Dict,
    headers: List[str]
) -> Dict[str, int]:
    """
    Pre-extract likely asset ID columns by analyzing sheet structure.
    
    Handles multiple naming conventions:
    - "Superior Functional Location" / "Superior FL"
    - "Functional Location" / "FLOC" / "FL"
    - "Asset ID" / "Asset_ID"
    - "Tag" / "Tag Number"
    - Custom vendor-specific columns
    
    Args:
        sheet: Dictionary with 'headers' and 'data' keys
        headers: List of column names
    
    Returns:
        Dict mapping standard field -> column index
        {
            "asset_id_primary": 2,
            "asset_id_fallback_1": 5,
            "asset_id_fallback_2": 8
        }
    """
    asset_id_patterns = [
        r"^superior\s+functional\s+location$",
        r"^superior\s+fl$",
        r"^superior\s+floc$",
        r"^functional\s+location$",
        r"^floc$",
        r"^fl$",
        r"^asset\s+id$",
        r"^asset_id$",
        r"^tag$",
        r"^tag\s+number$",
    ]
    
    asset_id_columns = {}
    
    for col_idx, header in enumerate(headers):
        header_normalized = header.strip().lower()
        
        for pattern_idx, pattern in enumerate(asset_id_patterns):
            if re.match(pattern, header_normalized):
                field_name = f"asset_id_{'primary' if pattern_idx == 0 else f'fallback_{pattern_idx}'}"
                asset_id_columns[field_name] = col_idx
                logger.info(f"Mapped '{header}' → {field_name}")
                break
    
    return asset_id_columns


def _extract_asset_id_from_record(
    record: Dict,
    asset_id_columns: Dict[str, int]
) -> str:
    """
    Extract asset ID using primary column, with fallback chain.
    
    Args:
        record: Asset record dictionary
        asset_id_columns: Mapping from _normalize_asset_id_extraction
    
    Returns:
        Extracted asset ID or empty string if not found
    """
    priority_order = [
        "asset_id_primary",
        "asset_id_fallback_1",
        "asset_id_fallback_2"
    ]
    
    for field in priority_order:
        if field in asset_id_columns:
            asset_id = record.get(field, "").strip()
            if asset_id:
                return asset_id
    
    return ""


def _validate_asset_id_format(asset_id: str) -> bool:
    """
    Validate asset ID matches expected format patterns.
    
    Accepted patterns:
    - [A-Z0-9]+-[0-9]{4,} (e.g., TX-1234, SW-5678, PUMP-00123)
    - [A-Z]{2,}-[0-9]{3,} (e.g., TX-123, SW-456)
    - Pure alphanumeric with dash (e.g., A123B-5678)
    
    Args:
        asset_id: Asset ID string to validate
    
    Returns:
        True if valid format, False otherwise
    """
    if not asset_id:
        return False
    
    patterns = [
        r"^[A-Z0-9]+-[0-9]{4,}$",     # TX-1234 format
        r"^[A-Z]{2,}-[0-9]{3,}$",     # TX-123 format
        r"^[A-Z0-9]{2,}-[0-9]{3,}$",  # General format
    ]
    
    return any(re.match(pattern, asset_id) for pattern in patterns)
```

**Integration Point in _extract_asset_spreadsheet():**

```python
# After identifying target_sheets and before LLM extraction
asset_id_columns = _normalize_asset_id_extraction(
    sheet=target_sheets[0],
    headers=target_sheets[0]["headers"]
)

# Store in context for validation later
extraction_context = {
    "asset_id_columns": asset_id_columns,
    "source_sheet": target_sheets[0]["sheet_name"],
    "total_rows": len(target_sheets[0]["data"])
}
```

---

### 2.2 Implement Pre-LLM Data Normalization (Fix #2)

**File:** `c:\Code\experiments\agent 07\document_extractor.py`

**Add new function:**

```python
def _normalize_spreadsheet_data_pre_llm(
    sheet: Dict,
    sheet_name: str
) -> Dict:
    """
    Normalize all cells in sheet before sending to LLM.
    
    Normalizations applied:
    1. Whitespace: trim leading/trailing spaces in all cells
    2. Missing values: standardize to explicit null
    3. Voltage notation: normalize to canonical form (e.g., "11kV")
    4. Date formats: detect and standardize to ISO-8601
    5. Manufacturer names: trim spaces and standardize case
    
    Args:
        sheet: Original sheet dictionary with headers and data
        sheet_name: Name of sheet for logging
    
    Returns:
        Normalized sheet dictionary with same structure
    """
    normalization_log = {
        "sheet_name": sheet_name,
        "whitespace_fixes": 0,
        "null_fixes": 0,
        "voltage_fixes": 0,
        "date_fixes": 0,
        "manufacturer_fixes": 0
    }
    
    normalized_data = []
    
    for row_idx, row in enumerate(sheet["data"]):
        normalized_row = {}
        
        for col_name, col_value in row.items():
            value = col_value
            
            # Fix 1: Whitespace normalization
            if isinstance(value, str):
                original = value
                value = value.strip()
                if original != value:
                    normalization_log["whitespace_fixes"] += 1
            
            # Fix 2: Null standardization
            if value in ["", "N/A", "n/a", "NA", "—", "–", None, "null", "NULL"]:
                value = None
                normalization_log["null_fixes"] += 1
            
            # Fix 3: Voltage notation normalization
            # Pattern: "11kV" ↔ "11 kV" ↔ "11kv" → "11kV"
            if isinstance(value, str) and re.search(r"\d+\s*k[vV]", value):
                original = value
                value = re.sub(r"(\d+)\s*k[vV]", r"\1kV", value)
                if original != value:
                    normalization_log["voltage_fixes"] += 1
            
            # Fix 4: Date format standardization
            # Detect DD/MM/YYYY, MM/DD/YYYY, YYYY-MM-DD patterns
            if isinstance(value, str) and _is_date_string(value):
                original = value
                value = _standardize_date(value)
                if original != value:
                    normalization_log["date_fixes"] += 1
            
            # Fix 5: Manufacturer name normalization
            # Common manufacturer column names
            if col_name.lower() in ["manufacturer", "mfg", "vendor", "supplier"]:
                if isinstance(value, str):
                    original = value
                    value = value.strip()
                    # Standardize case: "abb" → "ABB", but preserve "schneider" → "Schneider"
                    manufacturers = {
                        "abb": "ABB",
                        "ge": "GE",
                        "siemens": "Siemens",
                        "schneider": "Schneider",
                    }
                    value_lower = value.lower()
                    if value_lower in manufacturers:
                        value = manufacturers[value_lower]
                    
                    if original != value:
                        normalization_log["manufacturer_fixes"] += 1
            
            normalized_row[col_name] = value
        
        normalized_data.append(normalized_row)
    
    logger.info(f"Spreadsheet normalization for '{sheet_name}': {normalization_log}")
    
    return {
        "headers": sheet["headers"],
        "data": normalized_data,
        "normalization_log": normalization_log
    }


def _is_date_string(value: str) -> bool:
    """Detect if string looks like a date."""
    date_patterns = [
        r"^\d{1,2}/\d{1,2}/\d{2,4}$",   # DD/MM/YYYY or MM/DD/YYYY
        r"^\d{4}-\d{1,2}-\d{1,2}$",     # YYYY-MM-DD
        r"^\d{1,2}-\d{1,2}-\d{2,4}$",   # DD-MM-YYYY
    ]
    return any(re.match(pattern, value) for pattern in date_patterns)


def _standardize_date(value: str) -> str:
    """Standardize date to ISO-8601 format (YYYY-MM-DD)."""
    # Try common patterns
    patterns = [
        (r"^(\d{2})/(\d{2})/(\d{4})$", lambda m: f"{m.group(3)}-{m.group(1)}-{m.group(2)}"),  # DD/MM/YYYY
        (r"^(\d{4})-(\d{1,2})-(\d{1,2})$", lambda m: f"{m.group(1)}-{m.group(2):0>2}-{m.group(3):0>2}"),  # YYYY-MM-DD
    ]
    
    for pattern, transformer in patterns:
        match = re.match(pattern, value)
        if match:
            return transformer(match)
    
    return value
```

**Integration in _extract_asset_spreadsheet():**

```python
# After selecting target_sheets, normalize data before LLM call
normalized_sheets = []
for sheet in target_sheets:
    normalized = _normalize_spreadsheet_data_pre_llm(
        sheet=sheet,
        sheet_name=sheet["sheet_name"]
    )
    normalized_sheets.append(normalized)

# Pass normalized sheets to LLM instead of raw sheets
raw_records = _call_llm_for_asset_extraction(
    sheets=normalized_sheets,  # Use normalized data
    pass_number=2
)
```

---

### 2.3 Implement Post-LLM Validation (Fix #3)

**File:** `c:\Code\experiments\agent 07\document_extractor.py`

**Add new function:**

```python
def _validate_asset_records(
    records: List[Dict],
    sheet_headers: List[str],
    source_data: List[Dict]
) -> Tuple[List[Dict], List[Dict]]:
    """
    Validate extracted asset records against source data and expected formats.
    
    Validation checks:
    1. Required fields (asset_id) must be non-null
    2. asset_id must match expected format
    3. Optional fields must match expected data types
    4. Cross-check: extracted values must exist in source data
    5. No truncation: field count matches source
    6. Hallucination detection: filter impossible values
    
    Args:
        records: List of asset records extracted by LLM
        sheet_headers: Original sheet column names
        source_data: Original sheet data for cross-checking
    
    Returns:
        Tuple of (validated_records, rejected_records_with_reasons)
    """
    validated_records = []
    rejected_records = []
    
    # Build lookup table from source data for cross-checking
    source_lookup = {}
    for row in source_data:
        for header, value in row.items():
            if header not in source_lookup:
                source_lookup[header] = set()
            if value is not None and isinstance(value, str):
                source_lookup[header].add(value.strip().lower())
    
    for record_idx, record in enumerate(records):
        rejection_reasons = []
        
        # Check 1: asset_id required and non-null
        asset_id = record.get("asset_id", "").strip()
        if not asset_id:
            rejection_reasons.append("Missing asset_id")
        elif not _validate_asset_id_format(asset_id):
            rejection_reasons.append(f"Invalid asset_id format: '{asset_id}'")
        
        # Check 2: Detect hallucination in common fields
        hallucination_fields = ["description", "manufacturer", "voltage_rating"]
        for field in hallucination_fields:
            if field in record and field in source_lookup:
                field_value = record[field]
                if isinstance(field_value, str):
                    # Check if extracted value seems to exist in source
                    field_lower = field_value.lower().strip()
                    if field_lower and field_lower not in source_lookup[field]:
                        rejection_reasons.append(
                            f"Possible hallucination in {field}: '{field_value}' not found in source"
                        )
        
        # Check 3: Data type validation
        expected_types = {
            "asset_id": str,
            "description": str,
            "quantity": (int, float),
            "voltage_rating": str,
            "manufacturer": str
        }
        
        for field, expected_type in expected_types.items():
            if field in record and record[field] is not None:
                if not isinstance(record[field], expected_type):
                    rejection_reasons.append(
                        f"Type mismatch in {field}: expected {expected_type.__name__}, got {type(record[field]).__name__}"
                    )
        
        # Decision: Accept or reject
        if not rejection_reasons:
            validated_records.append(record)
        else:
            rejected_records.append({
                "record_index": record_idx,
                "record": record,
                "rejection_reasons": rejection_reasons
            })
            logger.warning(f"Record {record_idx} rejected: {rejection_reasons}")
    
    return validated_records, rejected_records
```

**Integration in _extract_asset_spreadsheet():**

```python
# After Pass 2 extraction (LLM-generated records)
raw_records = _call_llm_for_asset_extraction(
    sheets=normalized_sheets,
    pass_number=2
)

# Apply validation
valid_records, rejected = _validate_asset_records(
    records=raw_records,
    sheet_headers=normalized_sheets[0]["headers"],
    source_data=normalized_sheets[0]["data"]
)

# Log rejections
if rejected:
    rejection_summary = {
        "total_rejected": len(rejected),
        "rejection_rate": len(rejected) / (len(valid_records) + len(rejected)),
        "samples": rejected[:5]  # First 5 rejections
    }
    logger.warning(f"Asset extraction validation: {rejection_summary}")
    extraction_context["validation_summary"] = rejection_summary

# Use valid records as final output
all_asset_records = valid_records
```

---

## Part 3: Test Suite Definition (Day 5)

### 3.1 Update step_validator_agent.py

**File:** `c:\Code\experiments\agent 07\step_validator_agent.py`

**Location:** Add to TEST_SUITE definition

```python
STEP_7_TEST_CASES = {
    "TC-001": {
        "step": 7,
        "test_id": "TC-001",
        "test_name": "Asset Record Count Consistency",
        "category": "COMPLETENESS",
        "description": "Verify all runs extract same number of asset records",
        "validation_logic": lambda results: (
            len(set(r["extracted_asset_count"] for r in results["runs"])) == 1
        ),
        "success_threshold": 1.0  # 100% consistency required
    },
    
    "TC-002": {
        "step": 7,
        "test_id": "TC-002",
        "test_name": "Asset ID Format Validation",
        "category": "CORRECTNESS",
        "description": "Verify extracted asset IDs match pattern",
        "validation_logic": lambda results: (
            all(
                all(
                    re.match(r"^[A-Z0-9]+-[0-9]{3,}$", aid)
                    for aid in run["asset_ids"]
                )
                for run in results["runs"]
            )
        ),
        "success_threshold": 1.0
    },
    
    "TC-003": {
        "step": 7,
        "test_id": "TC-003",
        "test_name": "Asset Record Field Consistency",
        "category": "CONSISTENCY",
        "description": "All records have identical field sets",
        "validation_logic": lambda results: (
            len(set(
                tuple(sorted(run["record_fields"]))
                for run in results["runs"]
            )) == 1
        ),
        "success_threshold": 1.0
    },
    
    "TC-004": {
        "step": 7,
        "test_id": "TC-004",
        "test_name": "Null Field Consistency",
        "category": "DATA_INTEGRITY",
        "description": "Same fields are null across all runs",
        "validation_logic": lambda results: (
            _validate_null_field_consistency(results["runs"])
        ),
        "success_threshold": 0.95  # 95% of assets have consistent null fields
    },
    
    "TC-005": {
        "step": 7,
        "test_id": "TC-005",
        "test_name": "Data Type Consistency",
        "category": "FORMAT",
        "description": "Field data types consistent across runs",
        "validation_logic": lambda results: (
            _validate_data_type_consistency(results["runs"])
        ),
        "success_threshold": 1.0
    },
    
    "TC-006": {
        "step": 7,
        "test_id": "TC-006",
        "test_name": "Extraction Validation Pass Rate",
        "category": "CONSISTENCY",
        "description": "Post-extraction validation accepts >98% of records",
        "validation_logic": lambda results: (
            all(
                (run["validation_accepted"] / (run["validation_accepted"] + run["validation_rejected"]))
                >= 0.98
                for run in results["runs"]
            )
        ),
        "success_threshold": 1.0
    }
}
```

**Helper functions:**

```python
def _validate_null_field_consistency(runs: List[Dict]) -> bool:
    """Check if null fields are consistent across runs."""
    if not runs:
        return True
    
    # For each asset across runs, check null field consistency
    consistency_score = 0
    total_assets = 0
    
    for asset_idx in range(len(runs[0].get("assets", []))):
        null_fields_sets = []
        
        for run in runs:
            if asset_idx < len(run.get("assets", [])):
                asset = run["assets"][asset_idx]
                null_fields = set(
                    k for k, v in asset.items() if v is None
                )
                null_fields_sets.append(null_fields)
        
        # Check if all runs have same null fields for this asset
        if null_fields_sets and all(nf == null_fields_sets[0] for nf in null_fields_sets):
            consistency_score += 1
        
        total_assets += 1
    
    return (consistency_score / total_assets) >= 0.95 if total_assets > 0 else True


def _validate_data_type_consistency(runs: List[Dict]) -> bool:
    """Check if data types are consistent across runs for same fields."""
    if not runs:
        return True
    
    # Sample first 10 assets and first run
    first_run = runs[0]
    sample_size = min(10, len(first_run.get("assets", [])))
    
    for asset_idx in range(sample_size):
        first_asset = first_run["assets"][asset_idx]
        
        for run in runs[1:]:
            if asset_idx < len(run.get("assets", [])):
                other_asset = run["assets"][asset_idx]
                
                for field in first_asset:
                    if field in other_asset:
                        if type(first_asset[field]) != type(other_asset[field]):
                            logger.warning(
                                f"Type mismatch in {field}: "
                                f"{type(first_asset[field]).__name__} vs "
                                f"{type(other_asset[field]).__name__}"
                            )
                            return False
    
    return True
```

---

## Part 4: Variance Report Implementation (Day 6)

### 4.1 Create Variance Report Template

**File:** `c:\Code\experiments\agent 07\STEP7_VARIANCE_ANALYSIS_REPORT.md`

**See accompanying STEP7_VARIANCE_ANALYSIS_REPORT.md file for template structure**

### 4.2 Implement Report Generation

**File:** `c:\Code\experiments\agent 07\step_validator_agent.py`

**Add function to generate variance report:**

```python
async def generate_variance_report(
    step_number: int,
    test_results: List[Dict],
    output_path: str
) -> str:
    """
    Generate comprehensive variance analysis report.
    
    Args:
        step_number: Step number (e.g., 7)
        test_results: List of test run results
        output_path: Where to save report
    
    Returns:
        Path to generated report
    """
    report_lines = []
    
    # Header
    report_lines.append(f"# Step {step_number} Variance Analysis Report")
    report_lines.append(f"\n**Generated:** {datetime.now().isoformat()}")
    report_lines.append(f"**Test Runs:** {len(test_results)}")
    
    # Executive Summary
    report_lines.append("\n## Executive Summary\n")
    
    # Calculate key metrics
    all_record_counts = [r.get("asset_count", 0) for r in test_results]
    record_count_variance = max(all_record_counts) - min(all_record_counts) if all_record_counts else 0
    
    report_lines.append(f"- **Status:** {'FIXED & VALIDATED' if record_count_variance == 0 else 'REQUIRES FIXES'}")
    report_lines.append(f"- **Asset Record Consistency:** {100 - (record_count_variance / max(all_record_counts) * 100 if max(all_record_counts) > 0 else 0):.1f}%")
    report_lines.append(f"- **Test Pass Rate:** {sum(1 for r in test_results if r.get('test_pass_rate', 0) >= 0.95) / len(test_results) * 100:.1f}%")
    
    # Run-by-Run Analysis
    report_lines.append("\n## Run-by-Run Analysis\n")
    report_lines.append("| Run | Assets | Tests Passed | Pass Rate | Notes |")
    report_lines.append("|-----|--------|--------------|-----------|-------|")
    
    for idx, result in enumerate(test_results, 1):
        passed_tests = result.get("tests_passed", 0)
        total_tests = result.get("tests_total", 0)
        pass_rate = (passed_tests / total_tests * 100) if total_tests > 0 else 0
        
        report_lines.append(
            f"| {idx} | {result.get('asset_count', 'N/A')} | "
            f"{passed_tests}/{total_tests} | {pass_rate:.1f}% | "
            f"{result.get('notes', '')} |"
        )
    
    # Write report
    report_content = "\n".join(report_lines)
    
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        f.write(report_content)
    
    logger.info(f"Variance report generated: {output_path}")
    return output_path
```

---

## Part 5: Deployment Checklist

### Step 5.1: Code Review
- [ ] Confirm all Fix #1, Fix #2, Fix #3 code additions are syntactically correct
- [ ] Verify all new functions have complete docstrings
- [ ] Check logger statements for appropriate level (info vs warning vs error)
- [ ] Verify imports are added (re, Path, etc.)

### Step 5.2: Testing
- [ ] Run unit tests on normalization functions with sample data
- [ ] Test validation functions with edge cases (null values, special chars)
- [ ] Execute orchestrator with test_step7_multirun.json
- [ ] Verify 3-run execution produces 3 results
- [ ] Confirm variance report is generated

### Step 5.3: Deployment
- [ ] Merge code to main branch with tag "step7_enhancements_v1.0"
- [ ] Deploy updated document_extractor, orchestrator, step_validator_agent
-[ ] Run variance test on production TAL (50+ assets)
- [ ] Compare pre-fix vs post-fix results
- [ ] Document improvement metrics in STEP7_VARIANCE_ANALYSIS_REPORT.md

### Step 5.4: Monitoring
- [ ] Set up weekly variance tests
- [ ] Alert if variance exceeds 2%
- [ ] Update report monthly with latest results

---

**Document Version:** 1.0  
**Created:** 2026-04-11  
**Owner:** Development Engineering Team
