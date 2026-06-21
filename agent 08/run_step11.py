import json, requests, datetime, pathlib

RUN_DIR = pathlib.Path("c:/Code/agentExperiments/agent 05/OUTPUT/DS1-DAR1988-108 - Exp_20260328_055319")
ANALYTICS_URL = "http://localhost:8092/analyse"

# Load all source files
with open(RUN_DIR / "Extraction_ATTENTION_pdf_Extract_Design_Brief_Informati_20260328_055458.json") as f:
    db_extraction = json.load(f)

with open(RUN_DIR / "Extraction_DS1_DAR1988_RETIC_pdf_Extract_Drawing_Legend_20260328_055458.json") as f:
    leg_extraction = json.load(f)

with open(RUN_DIR / "Extraction_DS1_DAR1988_RETIC_pdf_Extract_Site_Plan_Information_20260328_055458.json") as f:
    sp_extraction = json.load(f)

with open(RUN_DIR / "AssetSymbolMatches_20260328T060039.json") as f:
    sm_data = json.load(f)

CONSOLIDATED_PATH = str(RUN_DIR / "ConsolidatedReport_20260328T060253.json")

# Build design brief data (flatten all sub-step extractions)
design_brief_data = {}
sse_db = db_extraction.get("sub_step_extractions", {})
for k, v in sse_db.items():
    if v:
        design_brief_data[k] = v

# Build legend entries
legend_entries = []
sse_leg = leg_extraction.get("sub_step_extractions", {})
for k, v in sse_leg.items():
    val = v.get("value") if isinstance(v, dict) else v
    if isinstance(val, list):
        legend_entries.extend(val)

# Build site plan data
site_plan_data = {}
sse_sp = sp_extraction.get("sub_step_extractions", {})
for k, v in sse_sp.items():
    if v and str(v).strip():
        site_plan_data[k] = v

# Enriched assets (updated_assets from step 8)
updated_assets = sm_data.get("updated_assets", [])

print(f"Design brief sub-steps: {list(design_brief_data.keys())}")
print(f"Legend entries: {len(legend_entries)}")
print(f"Site plan fields: {list(site_plan_data.keys())}")
print(f"Updated assets: {len(updated_assets)} ({sm_data.get('match_summary',{})})")

payload = {
    "context": (
        "Customer connections electricity network application review for project DAR1509, drawing DS1. "
        "Primary data contains structured extractions from the Design Brief document (DAR1988_DESIGN_BRIEF.pdf). "
        "site_plan contains structured extractions from the Site Plan/Reticulation Drawing. "
        "enriched_assets is the asset register from the TAL spreadsheet with each asset matched to its drawing symbol. "
        "reference_data lists all symbols and labels from the drawing legend."
    ),
    "data": design_brief_data,
    "site_plan": site_plan_data,
    "enriched_assets": updated_assets,
    "reference_data": legend_entries,
    "tasks": [
        {
            "task_id":   "task-funding-requirements",
            "task_name": "Funding Requirements Consolidation",
            "task_type": "extraction",
            "description": (
                "From the Design Brief data (primary data), consolidate ALL items related to "
                "the Determination of Funding Requirements. "
                "Look in: sub-step-extract-funding-determination (items array), "
                "sub-step-extract-funding-ancillary-fees (fees array), "
                "sub-step-extract-hv (HV Infrastructure Funding and Materials), "
                "sub-step-extract-sl (SL_Material_Funding), "
                "sub-step-extract-payment-instructions (banking/payment details). "
                "Group items into: contestable_works (works funded by customer), "
                "non_contestable_works (works funded by ACME Energy), "
                "ancillary_costs (duct usage, inspection, fees), other_funding_items. "
                "Capture total_capital_contribution if stated. "
                "Preserve all amounts and descriptions exactly as written."
            ),
            "output_format": (
                '{"funding_details": {'
                '  "total_capital_contribution": "string or null",'
                '  "contestable_works": [{"item": "string", "description": "string", "amount": "string or null", "responsible_party": "string or null"}],'
                '  "non_contestable_works": [{"item": "string", "description": "string", "amount": "string or null", "responsible_party": "string or null"}],'
                '  "ancillary_costs": [{"item": "string", "description": "string", "amount": "string or null"}],'
                '  "other_funding_items": [{"item": "string", "description": "string", "amount": "string or null"}],'
                '  "payment_instructions": {"method": "string or null", "bsb": "string or null", "account": "string or null", "reference": "string or null"},'
                '  "notes": ["string"]}}'
            ),
        },
        {
            "task_id":   "task-supply-scope-comparison",
            "task_name": "Method of Supply Scope Comparison",
            "task_type": "comparison",
            "description": (
                "Compare the method of supply requirements in the Design Brief "
                "(primary data, especially sub-step-extract-hv and sub-step-extract-lv) "
                "against the Site Plan (site_plan field, notes in sub-step-extract-all-notes). "
                "The legend (reference_data) shows what work types are depicted on the drawing. "
                "List every supply requirement from the Design Brief. "
                "For each, check whether it appears in the Site Plan. "
                "Identify items in the Design Brief absent or insufficiently shown in the Site Plan."
            ),
            "output_format": (
                '{"method_of_supply_comparison": {'
                '  "design_brief_requirements": [{"item": "string", "category": "HV|LV|SL|other", "description": "string"}],'
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
                "Compare the funded works scope from the Design Brief "
                "(primary data, sub-step-extract-funding-determination and sub-step-extract-hv) "
                "against what is depicted in the Design Drawing via Site Plan (site_plan) "
                "and drawing legend (reference_data). "
                "For each funded work item, determine whether a corresponding element "
                "is shown in the drawing or covered by legend entries. "
                "List items funded per Design Brief but not represented in the drawing."
            ),
            "output_format": (
                '{"funding_arrangement_comparison": {'
                '  "design_brief_funded_works": [{"item": "string", "funding_category": "string", "responsible_party": "string or null"}],'
                '  "drawing_scope_found": ["string"],'
                '  "missing_from_drawing": [{"item": "string", "design_brief_reference": "string", "note": "string"}],'
                '  "summary": "string"}}'
            ),
        },
        {
            "task_id":   "task-asset-register",
            "task_name": "Asset Register Summary",
            "task_type": "summary",
            "description": (
                "Using the enriched_assets list, produce a complete asset register. "
                "Each asset has: asset_id, asset_type, description, match_status (found/not_found), "
                "and if found: symbol_description and label. "
                "For each asset output: asset_id, asset_type, description, "
                "legend_label (the label field or null), "
                "legend_category (look up in reference_data by label, get category), "
                "match_confidence (high if found, none if not_found), "
                "found_on_diagram (true if found). "
                "Derive action_status from legend_label: "
                "NEW -> new, REMOVE -> remove, REPLACE -> replace, EXISTING -> existing, else unknown. "
                "Include register_summary with counts."
            ),
            "output_format": (
                '{"asset_register": ['
                '  {"asset_id": "string", "asset_type": "string", "description": "string",'
                '   "legend_label": "string or null", "legend_category": "string or null",'
                '   "action_status": "new|remove|replace|existing|unknown",'
                '   "match_confidence": "high|medium|low|none", "found_on_diagram": true}'
                '], "register_summary": {'
                '  "total": 0, "new": 0, "remove": 0, "replace": 0, "existing": 0,'
                '  "found_on_diagram": 0, "not_found": 0}}'
            ),
        },
    ],
}

print("\nCalling analytics agent...")
resp = requests.post(ANALYTICS_URL, json=payload, timeout=300)
print(f"Status: {resp.status_code}")

if resp.status_code == 200:
    result = resp.json()
    task_results = result.get("analytics_results") or {}
    print("Task results:", list(task_results.keys()))

    funding = (task_results.get("task-funding-requirements") or {}).get("result") or {}
    supply  = (task_results.get("task-supply-scope-comparison") or {}).get("result") or {}
    funding_cmp = (task_results.get("task-funding-arrangement-comparison") or {}).get("result") or {}
    asset_reg = (task_results.get("task-asset-register") or {}).get("result") or {}

    asset_list = asset_reg.get("asset_register") or []
    reg_summary = asset_reg.get("register_summary") or {}
    missing_supply = (supply.get("method_of_supply_comparison") or {}).get("missing_from_site_plan") or []
    missing_drawing = (funding_cmp.get("funding_arrangement_comparison") or {}).get("missing_from_drawing") or []

    print(f"  Funding items: {len((funding.get('funding_details') or {}).get('contestable_works', []))} contestable, "
          f"{len((funding.get('funding_details') or {}).get('non_contestable_works', []))} non-contestable")
    print(f"  Supply gaps: {len(missing_supply)}")
    print(f"  Drawing scope gaps: {len(missing_drawing)}")
    print(f"  Asset register: {len(asset_list)} assets, summary: {reg_summary}")

    analysis_report = {
        "report_type":              "Customer Connections Review Analysis Report",
        "generated_at":             datetime.datetime.utcnow().isoformat() + "Z",
        "consolidated_report_path": CONSOLIDATED_PATH,
        "source_run":               str(RUN_DIR.name),
        "analysis_report": {
            "funding_details":                funding,
            "method_of_supply_comparison":    supply,
            "funding_arrangement_comparison": funding_cmp,
            "asset_register":                 asset_reg,
        },
        "summary": {
            "total_assets":       len(asset_list),
            "found_on_diagram":   reg_summary.get("found_on_diagram", 0),
            "not_found":          reg_summary.get("not_found", 0),
            "supply_scope_gaps":  len(missing_supply),
            "drawing_scope_gaps": len(missing_drawing),
        },
    }

    out_path = RUN_DIR / f"AnalysisReport_{datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%S')}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(analysis_report, f, indent=2, default=str)
    print(f"\nSaved: {out_path}")
else:
    print("ERROR:", resp.text[:1000])
