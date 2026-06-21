import os
import json
from difflib import SequenceMatcher
from datetime import datetime
from fastapi import FastAPI, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware

# Minimum similarity ratio (0-1) for fuzzy matching asset types to legend names
FUZZY_THRESHOLD = 0.75

app = FastAPI(title="Mapping Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _normalise(text: str) -> str:
    """Normalise text for comparison: lowercase, collapse whitespace, strip."""
    return " ".join(text.lower().split())


def _legend_asset_type(entry):
    """Extract the asset type label from a legend entry (the key we match against).
    e.g. 'NEW PILLAR', 'NEW COLUMN' - this is what assets reference."""
    return (entry.get("asset_type") or entry.get("icon_description") or
            entry.get("description") or "").strip()


def _legend_icon_desc(entry):
    """Extract the visual icon description from a legend entry (the value we return).
    e.g. 'Small hollow rectangle', 'Small hollow square'."""
    return (entry.get("Icon") or entry.get("icon_text") or
            entry.get("text") or entry.get("name") or "").strip()


def _fuzzy_find(needle: str, lookup: dict):
    """
    Try exact match first (case-insensitive), then fall back to fuzzy matching.
    Returns (matched_key, description) or (None, None).
    """
    norm_needle = _normalise(needle)
    # Exact match (normalised)
    for key, desc in lookup.items():
        if _normalise(key) == norm_needle:
            return key, desc
    # Fuzzy fallback
    best_key, best_desc, best_ratio = None, None, 0.0
    for key, desc in lookup.items():
        ratio = SequenceMatcher(None, norm_needle, _normalise(key)).ratio()
        if ratio > best_ratio:
            best_key, best_desc, best_ratio = key, desc, ratio
    if best_ratio >= FUZZY_THRESHOLD:
        return best_key, best_desc
    return None, None


@app.post("/create_mapping")
async def create_mapping(
    asset_list_json: str = Form(...),
    legend_json: str = Form(...)
):
    """
    Join extracted asset data with legend icon data.
    Produces a unified mapping where each asset is enriched with its
    expected icon description from the legend.
    """
    print("\n[MAPPER] 📥 Received mapping request")

    # 1. Parse inputs
    try:
        asset_data = json.loads(asset_list_json)
        legend_data = json.loads(legend_json)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON input: {e}")

    # Normalise: asset_data may be a dict with "assets" key or a raw list
    raw_assets = asset_data.get("assets", []) if isinstance(asset_data, dict) else (asset_data if isinstance(asset_data, list) else [])
    legend_list = legend_data.get("asset_types", []) if isinstance(legend_data, dict) else (legend_data if isinstance(legend_data, list) else [])

    print(f"[MAPPER] 📋 Assets: {len(raw_assets)}, Legend entries: {len(legend_list)}")

    # 2. Build legend lookup: asset type label -> icon visual description
    #    e.g. "NEW PILLAR" -> "Small hollow rectangle"
    legend_lookup = {
        _legend_asset_type(entry): _legend_icon_desc(entry)
        for entry in legend_list
        if _legend_asset_type(entry)
    }

    print(f"[MAPPER] 📖 Legend keys: {list(legend_lookup.keys())}")

    # 3. Enrich each asset with its expected icon description (fuzzy matching)
    mapped_assets = []
    fuzzy_matches = []
    for asset in raw_assets:
        asset_type = asset.get("asset_type", "").strip()
        matched_key, expected_visual = _fuzzy_find(asset_type, legend_lookup)

        match_method = None
        if matched_key is not None:
            if _normalise(matched_key) == _normalise(asset_type):
                match_method = "exact"
            else:
                match_method = "fuzzy"
                fuzzy_matches.append(f"'{asset_type}' -> '{matched_key}'")
                print(f"[MAPPER] 🔍 Fuzzy: '{asset_type}' matched to '{matched_key}'")

        mapped_assets.append({
            "asset_number": asset.get("asset_number"),
            "asset_type": asset.get("asset_type"),
            "expected_icon_description": expected_visual if expected_visual else "NOT FOUND IN LEGEND",
            "match_method": match_method or "none"
        })

    unmapped = [a["asset_number"] for a in mapped_assets if a["expected_icon_description"] == "NOT FOUND IN LEGEND"]
    exact_count = sum(1 for a in mapped_assets if a["match_method"] == "exact")
    fuzzy_count = sum(1 for a in mapped_assets if a["match_method"] == "fuzzy")
    matched = exact_count + fuzzy_count

    if unmapped:
        print(f"[MAPPER] ⚠️ {len(unmapped)} asset(s) have no legend match: {unmapped}")
    if fuzzy_matches:
        print(f"[MAPPER] 🔍 Fuzzy matches applied: {fuzzy_matches}")
    print(f"[MAPPER] 🔗 Mapped {matched}/{len(mapped_assets)} assets ({exact_count} exact, {fuzzy_count} fuzzy).")

    # 4. Save output file
    output_dir = "OUTPUT"
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_filename = os.path.join(output_dir, f"AssetMap_{timestamp}.json")
    asset_map_data = {"asset_map": mapped_assets}
    with open(output_filename, 'w', encoding='utf-8') as f:
        json.dump(asset_map_data, f, indent=2)
    print(f"[MAPPER] 💾 Output saved to {output_filename}")

    return {
        "status": "success",
        "result": asset_map_data,
        "output_file": output_filename,
        "summary": {
            "total_assets": len(mapped_assets),
            "matched": matched,
            "exact_matches": exact_count,
            "fuzzy_matches": fuzzy_count,
            "fuzzy_details": fuzzy_matches,
            "unmatched": len(unmapped),
            "unmatched_assets": unmapped
        },
        "files": {
            "files_read": [
                {"filename": "asset_list (from Step 1)", "role": "input", "description": "Asset list JSON from previous extraction step"},
                {"filename": "legend (from Step 2)", "role": "input", "description": "Legend data JSON from content review step"}
            ],
            "files_output": [
                {"filename": os.path.basename(output_filename), "path": output_filename, "role": "output", "description": "Asset-to-legend mapping (JSON)"}
            ]
        }
    }
