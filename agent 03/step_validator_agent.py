import os
import json
import logging
from datetime import datetime
from difflib import SequenceMatcher
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, Form, UploadFile, File

app = FastAPI(title="Step Validator Agent")

# --- Logging ---
logger = logging.getLogger("StepValidator")
logger.setLevel(logging.INFO)
if not logger.handlers:
    fh = logging.FileHandler("validation_traffic.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(fh)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tc(test_id: str, name: str, category: str, description: str,
        input_data: str, expected: str, actual: str,
        passed: bool, notes: str, reasoning: str) -> dict:
    """Build a structured test-case dict."""
    return {
        "test_id": test_id,
        "test_name": name,
        "category": category,
        "description": description,
        "input_data": input_data,
        "expected_output": expected,
        "actual_output": actual,
        "result": "PASS" if passed else "FAIL",
        "execution_notes": notes,
        "reasoning": reasoning,
    }


def _norm(text: str) -> str:
    """Normalise a string for comparison: lowercase + collapsed whitespace."""
    return " ".join(str(text).lower().split())


def _legend_lookup(legend: Any) -> Dict[str, str]:
    """
    Build a normalised {asset_type_label -> icon_visual_description} dict.
    Handles both {"asset_types": [...]} and bare list forms.
    In the legend: "asset_type" is the text label, "Icon" is the visual description.
    """
    lookup: Dict[str, str] = {}
    entries: list = []
    if isinstance(legend, dict):
        entries = legend.get("asset_types", legend.get("legend", []))
    elif isinstance(legend, list):
        entries = legend
    for entry in entries:
        if isinstance(entry, dict):
            label = str(entry.get("asset_type", "")).strip()
            icon  = str(entry.get("Icon", "")).strip()
            if label:
                lookup[_norm(label)] = icon
    return lookup


def _fuzzy_icon(asset_type: str, lookup: Dict[str, str]) -> Optional[str]:
    """Return icon description for asset_type via exact then fuzzy match (≥0.75)."""
    key = _norm(asset_type)
    if key in lookup:
        return lookup[key]
    best_ratio, best_icon = 0.0, None
    for k, v in lookup.items():
        r = SequenceMatcher(None, key, k).ratio()
        if r > best_ratio:
            best_ratio, best_icon = r, v
    return best_icon if best_ratio >= 0.75 else None


def _read_file_content(file_bytes: bytes, filename: str) -> Optional[str]:
    ext = filename.rsplit(".", 1)[-1].lower()
    if ext in ("png", "jpg", "jpeg", "gif", "bmp", "tiff", "webp"):
        return f"[BINARY IMAGE: {filename} ({len(file_bytes)} bytes)]"
    if ext == "pdf":
        return f"[PDF: {filename} ({len(file_bytes)} bytes)]"
    try:
        if ext == "json":
            return json.dumps(json.loads(file_bytes.decode("utf-8")), indent=2)
        return file_bytes.decode("utf-8")
    except Exception as e:
        return f"[UNREADABLE: {filename} – {e}]"


def _read_file_from_path(file_path: str) -> Optional[str]:
    if not file_path or not os.path.exists(file_path):
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        print(f"[VALIDATOR] Failed to read {file_path}: {e}")
        return None


# ---------------------------------------------------------------------------
# Step 1 – Asset Extraction  (structural tests; no structured source to diff)
# ---------------------------------------------------------------------------

def _validate_step1(output_data: Any) -> List[dict]:
    """
    Structural accuracy tests for Step 1 (Asset Ops extraction).
    Checks: format, completeness of records, required fields per asset,
    no duplicate asset_numbers.
    """
    tests: List[dict] = []
    n = 1

    assets = None
    has_key = isinstance(output_data, dict) and "assets" in output_data
    if has_key:
        assets = output_data["assets"]

    # TC-001  FORMAT
    tests.append(_tc(
        f"TC-{n:03d}", "Output Format: 'assets' key is present and is a list",
        "FORMAT",
        "Verify the extraction output has an 'assets' key containing a list.",
        f"output keys: {list(output_data.keys()) if isinstance(output_data, dict) else type(output_data).__name__}",
        "Output must have an 'assets' key whose value is a list.",
        f"'assets' present: {has_key}, type: {type(assets).__name__ if assets is not None else 'N/A'}",
        has_key and isinstance(assets, list),
        "1. Check output_data is a dict. 2. Check 'assets' key exists. 3. Check value type is list.",
        "PASS – output has 'assets' list." if (has_key and isinstance(assets, list))
        else "FAIL – 'assets' key missing or value is not a list.",
    ))
    n += 1

    if not (has_key and isinstance(assets, list)):
        return tests

    # TC-002  COMPLETENESS – at least one asset
    tests.append(_tc(
        f"TC-{n:03d}", "Asset Count: At least one asset was extracted",
        "COMPLETENESS",
        "Verify the extraction produced at least one asset record.",
        f"assets list length: {len(assets)}",
        "assets list must contain ≥ 1 item.",
        f"{len(assets)} asset(s) found.",
        len(assets) > 0,
        "1. Count items in assets list.",
        "PASS – assets list is non-empty." if len(assets) > 0 else "FAIL – assets list is empty.",
    ))
    n += 1

    # TC-003  COMPLETENESS – required fields on every record
    bad: List[str] = []
    for i, a in enumerate(assets):
        if not isinstance(a, dict):
            bad.append(f"[{i}] not a dict ({type(a).__name__})")
            continue
        for field in ("asset_number", "asset_type"):
            if not str(a.get(field, "")).strip():
                bad.append(f"[{i}] missing/empty '{field}'")

    tests.append(_tc(
        f"TC-{n:03d}", "Required Fields: Every asset has 'asset_number' and 'asset_type'",
        "COMPLETENESS",
        "Verify every extracted asset record has non-empty 'asset_number' and 'asset_type'.",
        f"Checked {len(assets)} asset records.",
        "All records must have non-empty 'asset_number' and 'asset_type'.",
        "All OK" if not bad else f"{len(bad)} issue(s): " + "; ".join(bad[:5]),
        not bad,
        f"1. Iterate all {len(assets)} assets. 2. Check 'asset_number' and 'asset_type' are present and non-empty.",
        "PASS – all assets have required fields." if not bad
        else f"FAIL – {len(bad)} field issue(s) found.",
    ))
    n += 1

    # TC-004  CONSISTENCY – no duplicate asset_numbers
    numbers = [a.get("asset_number", "") for a in assets if isinstance(a, dict)]
    seen: set = set()
    dups: List[str] = []
    for num in numbers:
        if num in seen:
            dups.append(num)
        seen.add(num)

    tests.append(_tc(
        f"TC-{n:03d}", "No Duplicate Asset Numbers",
        "CONSISTENCY",
        "Verify all asset_number values in the extraction are unique.",
        f"asset_numbers (first 10): {numbers[:10]}",
        "All asset_number values must be unique.",
        "No duplicates." if not dups else f"Duplicates: {dups[:5]}",
        not dups,
        "1. Collect all asset_number values. 2. Detect duplicates.",
        "PASS – all asset_numbers are unique." if not dups
        else f"FAIL – duplicate asset_numbers: {dups[:5]}",
    ))
    n += 1

    # TC-005+  CORRECTNESS – per-asset spot check (first 5)
    for i, a in enumerate(assets[:min(5, len(assets))]):
        if not isinstance(a, dict):
            continue
        an = a.get("asset_number", "")
        at = a.get("asset_type", "")
        ok = bool(an and at)
        tests.append(_tc(
            f"TC-{n:03d}", f"Step {i+1} Accuracy: asset '{an}' has valid field values",
            "CORRECTNESS",
            f"Verify asset at index {i} has non-empty asset_number and asset_type.",
            f"assets[{i}]: {json.dumps(a)}",
            "Non-empty asset_number and asset_type.",
            f"asset_number='{an}', asset_type='{at}'",
            ok,
            f"1. Read assets[{i}]. 2. Read asset_number and asset_type. 3. Verify both non-empty.",
            f"PASS – '{an}' / '{at}' both present." if ok
            else f"FAIL – asset_number='{an}' or asset_type='{at}' is empty.",
        ))
        n += 1

    return tests


# ---------------------------------------------------------------------------
# Step 2 – Legend Extraction  (structural tests)
# ---------------------------------------------------------------------------

def _validate_step2(output_data: Any) -> List[dict]:
    """
    Structural accuracy tests for Step 2 (Content Reviewer legend extraction).
    Checks: format, required fields per entry, unique Icon descriptions.
    """
    tests: List[dict] = []
    n = 1

    has_key = isinstance(output_data, dict) and "asset_types" in output_data
    entries: list = []
    if has_key:
        entries = output_data["asset_types"]
    elif isinstance(output_data, list):
        entries = output_data

    # TC-001  FORMAT
    tests.append(_tc(
        f"TC-{n:03d}", "Output Format: 'asset_types' key is present and is a list",
        "FORMAT",
        "Verify the legend extraction output has an 'asset_types' key containing a list.",
        f"output keys: {list(output_data.keys()) if isinstance(output_data, dict) else type(output_data).__name__}",
        "Output must have an 'asset_types' key whose value is a list.",
        f"'asset_types' present: {has_key}, entry count: {len(entries) if isinstance(entries, list) else 'N/A'}",
        has_key and isinstance(entries, list),
        "1. Check output has 'asset_types' key. 2. Check value is a list.",
        "PASS." if (has_key and isinstance(entries, list)) else "FAIL – missing 'asset_types' or not a list.",
    ))
    n += 1

    if not isinstance(entries, list):
        return tests

    # TC-002  COMPLETENESS – at least one entry
    tests.append(_tc(
        f"TC-{n:03d}", "Legend Count: At least one legend entry extracted",
        "COMPLETENESS",
        "Verify the legend extraction produced at least one entry.",
        f"asset_types length: {len(entries)}",
        "Must contain ≥ 1 legend entry.",
        f"{len(entries)} entries found.",
        len(entries) > 0,
        "1. Count items in asset_types list.",
        "PASS." if len(entries) > 0 else "FAIL – legend is empty.",
    ))
    n += 1

    # TC-003  COMPLETENESS – required fields on every entry
    bad: List[str] = []
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            bad.append(f"[{i}] not a dict")
            continue
        for field in ("Icon", "asset_type"):
            if not str(e.get(field, "")).strip():
                bad.append(f"[{i}] missing/empty '{field}'")

    tests.append(_tc(
        f"TC-{n:03d}", "Required Fields: Every entry has 'Icon' and 'asset_type'",
        "COMPLETENESS",
        "Verify every legend entry has non-empty 'Icon' (visual description) and 'asset_type' (text label).",
        f"Checked {len(entries)} legend entries.",
        "All entries must have non-empty 'Icon' and 'asset_type'.",
        "All OK" if not bad else f"{len(bad)} issue(s): " + "; ".join(bad[:5]),
        not bad,
        f"1. Iterate all {len(entries)} entries. 2. Check 'Icon' and 'asset_type' are present and non-empty.",
        "PASS." if not bad else f"FAIL – {len(bad)} field issue(s).",
    ))
    n += 1

    # TC-004  CONSISTENCY – unique Icon descriptions
    icons = [e.get("Icon", "") for e in entries if isinstance(e, dict)]
    seen: set = set()
    dup_icons: List[str] = []
    for icon in icons:
        if icon in seen:
            dup_icons.append(icon)
        seen.add(icon)

    tests.append(_tc(
        f"TC-{n:03d}", "Unique Icons: No duplicate Icon visual descriptions",
        "CONSISTENCY",
        "Verify all Icon visual descriptions are unique (each icon must have a distinct description).",
        f"Icon values (first 5): {icons[:5]}",
        "All Icon values must be unique.",
        "No duplicates." if not dup_icons else f"Duplicates: {dup_icons[:3]}",
        not dup_icons,
        "1. Extract all Icon values. 2. Check for duplicates.",
        "PASS – all Icon descriptions are unique." if not dup_icons
        else f"FAIL – duplicate Icon descriptions: {dup_icons[:3]}",
    ))
    n += 1

    # TC-005+  CORRECTNESS – per-entry spot check (first 5)
    for i, e in enumerate(entries[:min(5, len(entries))]):
        if not isinstance(e, dict):
            continue
        icon = e.get("Icon", "")
        at   = e.get("asset_type", "")
        ok   = bool(icon and at)
        tests.append(_tc(
            f"TC-{n:03d}", f"Step {i+1} Accuracy: legend entry '{at}' has valid field values",
            "CORRECTNESS",
            f"Verify legend entry at index {i} has non-empty Icon and asset_type.",
            f"asset_types[{i}]: {json.dumps(e)[:200]}",
            "Non-empty Icon and asset_type.",
            f"Icon='{icon[:60]}', asset_type='{at}'",
            ok,
            f"1. Read asset_types[{i}]. 2. Read Icon and asset_type. 3. Verify both non-empty.",
            "PASS." if ok else f"FAIL – Icon='{icon[:40]}' or asset_type='{at}' is empty.",
        ))
        n += 1

    return tests


# ---------------------------------------------------------------------------
# Step 3 – Asset-to-Legend Mapping  (full deterministic tests)
# ---------------------------------------------------------------------------

def _validate_step3(input_data: Any, output_data: Any) -> List[dict]:
    """
    Deterministic accuracy tests for Step 3 (Mapping Agent).

    For each process step (asset) and sub-step (field check) verifies:
      • Completeness  – every source asset appears in the mapping
      • Sequence      – order of assets is preserved from Step 1 source
      • Accuracy      – asset_number and asset_type match source exactly
      • Data Integrity– expected_icon_description is consistent with legend
    """
    tests: List[dict] = []
    n = 1

    # ── Parse inputs ──────────────────────────────────────────────────────
    source_assets: list = []
    legend: Any = {}
    if isinstance(input_data, dict):
        al = input_data.get("asset_list", {})
        source_assets = al.get("assets", al) if isinstance(al, dict) else (al if isinstance(al, list) else [])
        legend = input_data.get("legend", {})

    legend_lookup = _legend_lookup(legend)

    # ── Parse output ──────────────────────────────────────────────────────
    asset_map: list = []
    has_map_key = isinstance(output_data, dict) and "asset_map" in output_data
    if has_map_key:
        asset_map = output_data["asset_map"]

    out_by_num: Dict[str, dict] = {
        e.get("asset_number", ""): e
        for e in asset_map if isinstance(e, dict)
    }

    # ══════════════════════════════════════════════════════════════════════
    # GLOBAL TESTS
    # ══════════════════════════════════════════════════════════════════════

    # TC-001  FORMAT
    tests.append(_tc(
        f"TC-{n:03d}", "Output Format: 'asset_map' key is present and is a list",
        "FORMAT",
        "Verify the mapping output has the required top-level structure.",
        f"output keys: {list(output_data.keys()) if isinstance(output_data, dict) else type(output_data).__name__}",
        "Output must have an 'asset_map' key whose value is a list.",
        f"'asset_map' present: {has_map_key}, is list: {isinstance(asset_map, list)}, count: {len(asset_map)}",
        has_map_key and isinstance(asset_map, list),
        "1. Check output_data is a dict. 2. Check 'asset_map' key exists. 3. Check value is a list.",
        "PASS – output has 'asset_map' list." if (has_map_key and isinstance(asset_map, list))
        else "FAIL – 'asset_map' key missing or not a list.",
    ))
    n += 1

    # TC-002  COMPLETENESS – total count
    src_count = len(source_assets)
    out_count  = len(asset_map)
    tests.append(_tc(
        f"TC-{n:03d}", "Completeness: Mapping entry count matches source asset count",
        "COMPLETENESS",
        f"Verify the mapping contains exactly {src_count} entries (one per source asset).",
        f"Source (Step 1) contains {src_count} assets.",
        f"Mapping must contain exactly {src_count} entries.",
        f"Mapping contains {out_count} entries.",
        src_count == out_count,
        f"1. Count source assets: {src_count}. 2. Count asset_map entries: {out_count}. 3. Compare.",
        f"PASS – both have {src_count} entries." if src_count == out_count
        else f"FAIL – source has {src_count} assets but mapping has {out_count} entries "
             f"({'missing' if out_count < src_count else 'extra'}: {abs(src_count - out_count)}).",
    ))
    n += 1

    # TC-003  SEQUENCE – order preserved
    src_nums = [a.get("asset_number", "") for a in source_assets if isinstance(a, dict)]
    out_nums  = [e.get("asset_number", "") for e in asset_map      if isinstance(e, dict)]
    out_set   = set(out_nums)
    src_in_out = [x for x in src_nums if x in out_set]
    seq_ok = src_in_out == [x for x in out_nums if x in set(src_nums)]
    tests.append(_tc(
        f"TC-{n:03d}", "Sequence: Asset order preserved from source (Step 1) to mapping",
        "DATA_INTEGRITY",
        "Verify the mapping lists assets in the same order as they appear in the Step 1 source.",
        f"Source order (first 5): {src_nums[:5]}",
        "Mapping must list assets in the same sequence as the source.",
        f"Mapping order (first 5): {out_nums[:5]}",
        seq_ok,
        "1. Extract asset_number sequence from source. "
        "2. Extract sequence from mapping. "
        "3. Compare relative order of common items.",
        "PASS – asset sequence is preserved." if seq_ok
        else f"FAIL – order mismatch. Source: {src_nums[:5]}, Mapping: {out_nums[:5]}",
    ))
    n += 1

    # TC-004  CONSISTENCY – required fields on all entries
    bad_fields: List[str] = []
    for i, entry in enumerate(asset_map):
        if not isinstance(entry, dict):
            bad_fields.append(f"[{i}] not a dict")
            continue
        for field in ("asset_number", "asset_type", "expected_icon_description"):
            if field not in entry:
                bad_fields.append(f"[{i}] missing '{field}'")

    tests.append(_tc(
        f"TC-{n:03d}", "Required Fields: All mapping entries have required fields",
        "CONSISTENCY",
        "Verify every mapping entry has 'asset_number', 'asset_type', and 'expected_icon_description'.",
        f"Checked all {len(asset_map)} mapping entries.",
        "Every entry must have all 3 required fields.",
        "All OK" if not bad_fields else f"{len(bad_fields)} issue(s): " + "; ".join(bad_fields[:3]),
        not bad_fields,
        f"1. Iterate all {len(asset_map)} entries. 2. Check each for 3 required keys.",
        "PASS – all entries have required fields." if not bad_fields
        else f"FAIL – {len(bad_fields)} entries missing required fields.",
    ))
    n += 1

    # TC-005  CONSISTENCY – no duplicate asset_numbers
    seen_set: set = set()
    dup_nums: List[str] = []
    for entry in asset_map:
        if isinstance(entry, dict):
            num = entry.get("asset_number", "")
            if num in seen_set:
                dup_nums.append(num)
            seen_set.add(num)

    tests.append(_tc(
        f"TC-{n:03d}", "No Duplicates: asset_number values are unique in mapping",
        "CONSISTENCY",
        "Verify no asset_number appears more than once in the mapping output.",
        f"asset_numbers in mapping (first 10): {out_nums[:10]}",
        "All asset_number values must be unique.",
        "No duplicates." if not dup_nums else f"Duplicates: {dup_nums}",
        not dup_nums,
        "1. Collect all asset_number values from mapping. 2. Detect duplicates.",
        "PASS – all asset_numbers are unique." if not dup_nums
        else f"FAIL – duplicate asset_numbers found: {dup_nums}",
    ))
    n += 1

    # ══════════════════════════════════════════════════════════════════════
    # PER-ASSET TESTS  (one set per source asset = one test per sub-step)
    # ══════════════════════════════════════════════════════════════════════

    for i, src in enumerate(source_assets):
        if not isinstance(src, dict):
            continue

        src_num  = src.get("asset_number", "")
        src_type = src.get("asset_type",   "")
        mapped   = out_by_num.get(src_num)
        present  = mapped is not None

        step_label = f"Step {i+1}"

        # Sub-test A  COMPLETENESS – asset present in mapping
        tests.append(_tc(
            f"TC-{n:03d}", f"{step_label} Completeness: asset '{src_num}' is present in mapping",
            "COMPLETENESS",
            f"Verify source asset '{src_num}' ({src_type}) from Step 1 appears in the mapping.",
            f"Source asset[{i}]: asset_number='{src_num}', asset_type='{src_type}'",
            f"Mapping must contain an entry with asset_number='{src_num}'.",
            "Found in mapping." if present else "NOT FOUND in mapping.",
            present,
            f"1. Read source asset[{i}]: asset_number='{src_num}'. "
            f"2. Search asset_map for matching asset_number. 3. Check entry exists.",
            f"PASS – '{src_num}' found in mapping." if present
            else f"FAIL – '{src_num}' is in source (index {i}) but absent from mapping.",
        ))
        n += 1

        if not present:
            continue

        # Sub-test B  CORRECTNESS – asset_number exact match
        mapped_num = mapped.get("asset_number", "")
        num_ok = mapped_num == src_num
        tests.append(_tc(
            f"TC-{n:03d}", f"{step_label} Accuracy: asset_number exact match for '{src_num}'",
            "CORRECTNESS",
            f"Verify the asset_number in the mapping entry matches the source exactly.",
            f"Source asset_number: '{src_num}'",
            f"Mapped asset_number must equal '{src_num}'.",
            f"Mapped asset_number: '{mapped_num}'",
            num_ok,
            f"1. Read source asset_number='{src_num}'. 2. Read mapped asset_number='{mapped_num}'. 3. Exact string compare.",
            f"PASS – asset_number matches: '{src_num}'." if num_ok
            else f"FAIL – source='{src_num}', mapped='{mapped_num}'.",
        ))
        n += 1

        # Sub-test C  CORRECTNESS – asset_type matches source
        mapped_type = mapped.get("asset_type", "")
        type_ok = _norm(mapped_type) == _norm(src_type)
        tests.append(_tc(
            f"TC-{n:03d}", f"{step_label} Accuracy: asset_type matches source for '{src_num}'",
            "CORRECTNESS",
            f"Verify the asset_type in the mapping matches the original source asset_type.",
            f"Source asset_type: '{src_type}'",
            f"Mapped asset_type must equal '{src_type}' (normalised comparison).",
            f"Mapped asset_type: '{mapped_type}'",
            type_ok,
            f"1. Read source asset_type='{src_type}'. 2. Read mapped asset_type='{mapped_type}'. 3. Normalised compare.",
            f"PASS – asset_type matches: '{mapped_type}'." if type_ok
            else f"FAIL – source='{src_type}', mapped='{mapped_type}'.",
        ))
        n += 1

        # Sub-test D  DATA_INTEGRITY – icon description consistent with legend
        mapped_icon  = mapped.get("expected_icon_description", "")
        match_method = mapped.get("match_method", "none")
        legend_icon  = _fuzzy_icon(src_type, legend_lookup)

        if legend_icon is not None:
            icon_ok = _norm(mapped_icon) == _norm(legend_icon)
            tests.append(_tc(
                f"TC-{n:03d}",
                f"{step_label} Integrity: icon description verified against legend for '{src_num}'",
                "DATA_INTEGRITY",
                f"Verify the expected_icon_description for '{src_num}' matches the legend entry for "
                f"asset_type '{src_type}'.",
                f"Legend icon for '{src_type}': '{legend_icon[:80]}'",
                f"expected_icon_description should be '{legend_icon[:80]}'",
                f"Mapping has: '{mapped_icon[:80]}' (match_method: {match_method})",
                icon_ok,
                f"1. Look up asset_type '{src_type}' in legend → '{legend_icon[:60]}'. "
                f"2. Compare with mapped icon '{mapped_icon[:60]}'.",
                f"PASS – icon matches legend for '{src_num}'." if icon_ok
                else f"FAIL – '{src_num}' icon mismatch. "
                     f"Legend='{legend_icon[:60]}', Mapped='{mapped_icon[:60]}'.",
            ))
        else:
            # Asset type not in legend – mapping should flag it
            not_found_ok = "NOT FOUND" in mapped_icon.upper()
            tests.append(_tc(
                f"TC-{n:03d}",
                f"{step_label} Integrity: unmatched asset '{src_num}' is correctly flagged",
                "DATA_INTEGRITY",
                f"Verify asset '{src_num}' (type: '{src_type}') has no legend match and is "
                f"correctly flagged as unmatched.",
                f"asset_type '{src_type}' not found in legend (no exact or fuzzy match ≥ 0.75).",
                "expected_icon_description must contain 'NOT FOUND IN LEGEND'.",
                f"Mapping has: '{mapped_icon}'",
                not_found_ok,
                f"1. Lookup '{src_type}' in legend – no match found. "
                f"2. Check mapped icon for 'NOT FOUND' flag.",
                "PASS – correctly flagged as not found in legend." if not_found_ok
                else f"FAIL – '{src_num}' has no legend match but icon description "
                     f"('{mapped_icon}') does not contain 'NOT FOUND IN LEGEND'.",
            ))
        n += 1

    return tests


# ---------------------------------------------------------------------------
# Step 4 – Verification  (completeness tests)
# ---------------------------------------------------------------------------

def _validate_step4(input_data: Any, output_data: Any) -> List[dict]:
    """
    Completeness tests for Step 4 (Verification Agent).
    Checks that every mapped asset from Step 3 has a verification report entry
    with a status field.
    """
    tests: List[dict] = []
    n = 1

    mapped_assets: list = []
    if isinstance(input_data, dict):
        mapped_assets = input_data.get("asset_map", [])

    has_report = isinstance(output_data, dict) and "verification_report" in output_data
    report: list = output_data.get("verification_report", []) if has_report else []

    # TC-001  FORMAT
    tests.append(_tc(
        f"TC-{n:03d}", "Output Format: 'verification_report' key is present and is a list",
        "FORMAT",
        "Verify the verification output has the required top-level structure.",
        f"output keys: {list(output_data.keys()) if isinstance(output_data, dict) else type(output_data).__name__}",
        "Output must have a 'verification_report' key containing a list.",
        f"'verification_report' present: {has_report}, count: {len(report)}",
        has_report and isinstance(report, list),
        "1. Check output has 'verification_report' key. 2. Check value is a list.",
        "PASS." if (has_report and isinstance(report, list)) else "FAIL.",
    ))
    n += 1

    if not isinstance(report, list):
        return tests

    # TC-002  COMPLETENESS – count matches mapped assets
    mapped_count = len(mapped_assets)
    report_count = len(report)
    tests.append(_tc(
        f"TC-{n:03d}", "Completeness: Report entry count matches mapped asset count",
        "COMPLETENESS",
        f"Verify the verification report covers all {mapped_count} mapped assets.",
        f"Mapped assets (Step 3): {mapped_count}",
        f"Report must have {mapped_count} entries.",
        f"Report has {report_count} entries.",
        mapped_count == report_count,
        f"1. Count mapped assets: {mapped_count}. 2. Count report entries: {report_count}. 3. Compare.",
        f"PASS." if mapped_count == report_count
        else f"FAIL – {mapped_count} mapped assets but {report_count} report entries.",
    ))
    n += 1

    # TC-003  COMPLETENESS – status field on every entry
    missing_status = [i for i, e in enumerate(report)
                      if isinstance(e, dict) and "status" not in e]
    tests.append(_tc(
        f"TC-{n:03d}", "Required Fields: Every report entry has a 'status' field",
        "COMPLETENESS",
        "Verify every verification entry includes a 'status' field.",
        f"Checked {len(report)} entries.",
        "All entries must have a 'status' field.",
        "All OK" if not missing_status else f"{len(missing_status)} entries missing 'status'",
        not missing_status,
        "1. Iterate verification_report. 2. Check 'status' key on each entry.",
        "PASS." if not missing_status else f"FAIL – {len(missing_status)} entries missing 'status'.",
    ))
    n += 1

    # TC-004+  COMPLETENESS – per-asset report entry check
    report_by_num: Dict[str, dict] = {
        e.get("asset_number", ""): e
        for e in report if isinstance(e, dict)
    }

    for i, mapped in enumerate(mapped_assets):
        if not isinstance(mapped, dict):
            continue
        asset_num  = mapped.get("asset_number", "")
        asset_type = mapped.get("asset_type", "")
        entry      = report_by_num.get(asset_num)
        present    = entry is not None

        tests.append(_tc(
            f"TC-{n:03d}",
            f"Step {i+1} Completeness: asset '{asset_num}' has a verification entry",
            "COMPLETENESS",
            f"Verify mapped asset '{asset_num}' ({asset_type}) has a corresponding "
            f"verification report entry.",
            f"Mapped asset[{i}]: asset_number='{asset_num}', asset_type='{asset_type}'",
            f"Report must contain an entry for asset_number='{asset_num}'.",
            "Found in report." if present else "NOT FOUND in report.",
            present,
            f"1. Read mapped asset[{i}]: '{asset_num}'. "
            f"2. Search verification_report for matching asset_number. 3. Check entry exists.",
            f"PASS – '{asset_num}' found in report." if present
            else f"FAIL – '{asset_num}' is in mapping but absent from verification report.",
        ))
        n += 1

    return tests


# ---------------------------------------------------------------------------
# Step router
# ---------------------------------------------------------------------------

def _route_step(step_name: str, input_data: Any, output_data: Any) -> List[dict]:
    """Route to the correct deterministic validator based on step_name keywords."""
    s = step_name.lower()

    # Step 3 – mapping
    if "mapping" in s or ("map" in s and "asset" in s) or "easement" in s and "map" in s:
        return _validate_step3(input_data, output_data)

    # Step 4 – verification
    if "verify" in s or "verification" in s:
        return _validate_step4(input_data, output_data)

    # Step 2 – legend / content review
    if "legend" in s or "icon" in s or ("extract" in s and "type" in s) or "requirements from" in s:
        return _validate_step2(output_data)

    # Step 1 – asset / schedule extraction (default)
    return _validate_step1(output_data)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.post("/validate_step")
async def validate_step(
    step_name:          str        = Form(...),
    step_description:   str        = Form(""),
    input_data_json:    str        = Form("{}"),
    output_data_json:   str        = Form("{}"),
    validation_criteria:str        = Form(""),
    output_file_path:   str        = Form(""),
    agent_files_json:   str        = Form("{}"),
    input_file:  UploadFile = File(default=None),
    output_file: UploadFile = File(default=None),
):
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"[VALIDATOR] Validating step: {step_name}")

    # --- Parse JSON fields ---
    try:
        agent_files = json.loads(agent_files_json) if agent_files_json else {}
    except json.JSONDecodeError:
        agent_files = {}

    try:
        input_data = json.loads(input_data_json) if input_data_json else {}
    except json.JSONDecodeError:
        input_data = {}

    try:
        output_data = json.loads(output_data_json) if output_data_json else {}
    except json.JSONDecodeError:
        output_data = {}

    # --- Handle uploaded files (read for metadata only) ---
    input_file_name  = None
    output_file_name = None
    input_file_content  = None
    output_file_content = None

    if input_file:
        input_bytes = await input_file.read()
        input_file_name = input_file.filename
        input_file_content = _read_file_content(input_bytes, input_file.filename)
        print(f"[VALIDATOR] Input file: {input_file.filename} ({len(input_bytes)} bytes)")

    if output_file:
        output_bytes = await output_file.read()
        output_file_name = output_file.filename
        output_file_content = _read_file_content(output_bytes, output_file.filename)
        print(f"[VALIDATOR] Output file: {output_file.filename} ({len(output_bytes)} bytes)")

    if not output_file_content and output_file_path:
        disk = _read_file_from_path(output_file_path)
        if disk:
            output_file_content = disk
            output_file_name = os.path.basename(output_file_path)

    # --- Run deterministic tests ---
    test_cases = _route_step(step_name, input_data, output_data)

    total   = len(test_cases)
    passed  = sum(1 for tc in test_cases if tc.get("result") == "PASS")
    failed  = total - passed
    score   = round((passed / total) * 100) if total > 0 else 0
    overall = "PASS" if failed == 0 else "FAIL"

    summary = (
        f"{passed}/{total} tests passed ({score}%). "
        + ("All checks passed." if failed == 0
           else f"{failed} check(s) failed – review test cases for details.")
    )

    # --- Files inventory ---
    files_read = []
    if input_file_name:
        files_read.append({
            "filename": input_file_name, "role": "input",
            "description": "Original input file sent to the agent",
            "ingested": bool(input_file_content),
            "size_chars": len(input_file_content) if input_file_content else 0,
        })
    if output_file_name or output_file_path:
        fname = output_file_name or os.path.basename(output_file_path)
        files_read.append({
            "filename": fname, "role": "output",
            "description": "Output file produced by the agent",
            "ingested": bool(output_file_content),
            "size_chars": len(output_file_content) if output_file_content else 0,
            **({"path": output_file_path} if output_file_path else {}),
        })

    # --- Save report ---
    output_dir = "OUTPUT"
    os.makedirs(output_dir, exist_ok=True)
    safe_name   = step_name.replace(" ", "_")[:30]
    report_file = os.path.join(output_dir, f"TestReport_{safe_name}_{run_timestamp}.json")

    test_report = {
        "report_metadata": {
            "report_type":          "Step Validation Test Report",
            "step_name":            step_name,
            "step_description":     step_description,
            "timestamp":            run_timestamp,
            "validation_method":    "deterministic",
            "validation_criteria":  validation_criteria or None,
        },
        "agent_files": agent_files or {},
        "files": {
            "files_read":   files_read,
            "files_output": [{
                "filename":    os.path.basename(report_file),
                "path":        report_file,
                "role":        "test_report",
                "description": "Deterministic validation test report",
            }],
        },
        "test_run_summary": {
            "overall_result": overall,
            "confidence":     "High",
            "score":          score,
            "summary":        summary,
            "total_tests":    total,
            "passed":         passed,
            "failed":         failed,
        },
        "test_cases":      test_cases,
        "issues":          [tc["test_name"] + ": " + tc["reasoning"]
                            for tc in test_cases if tc.get("result") == "FAIL"],
        "recommendations": [],
    }

    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(test_report, f, indent=2, ensure_ascii=False)
    print(f"[VALIDATOR] Report saved: {report_file}")

    # --- Log results ---
    logger.info(
        f"\n{'='*60} VALIDATION: {step_name} {'='*60}\n"
        f"Result: {overall} | {passed}/{total} passed ({score}%)\n"
        f"Method: deterministic\n"
        f"{'-'*140}"
    )
    for tc in test_cases:
        marker = "PASS" if tc.get("result") == "PASS" else "FAIL"
        logger.info(
            f"\n--- [{marker}] {tc.get('test_id')}: {tc.get('test_name')} ---\n"
            f"  Category:        {tc.get('category')}\n"
            f"  Input Data:      {tc.get('input_data')}\n"
            f"  Expected:        {tc.get('expected_output')}\n"
            f"  Actual:          {tc.get('actual_output')}\n"
            f"  Execution Notes: {tc.get('execution_notes')}\n"
            f"  Reasoning:       {tc.get('reasoning')}\n"
            f"  {'-'*60}"
        )
    logger.info(f"\n{'='*140}\n")

    # --- Console summary ---
    print(f"[VALIDATOR] {'PASS' if overall == 'PASS' else 'FAIL'} | "
          f"{step_name} | {passed}/{total} tests passed ({score}%)")
    for tc in test_cases:
        status = "PASS" if tc.get("result") == "PASS" else "FAIL"
        print(f"  [{status}] {tc.get('test_id')}: {tc.get('test_name')}")
        if tc.get("result") == "FAIL":
            print(f"         Expected: {tc.get('expected_output', '')[:120]}")
            print(f"         Actual:   {tc.get('actual_output',   '')[:120]}")

    # --- Response ---
    validation_response = {
        "is_valid":         overall == "PASS",
        "confidence":       "High",
        "score":            score,
        "summary":          summary,
        "step_name":        step_name,
        "agent_files":      agent_files or {},
        "files":            test_report["files"],
        "files_ingested":   {
            "input_file":  input_file_name,
            "output_file": output_file_name or output_file_path or None,
        },
        "test_run_summary": test_report["test_run_summary"],
        "test_cases":       test_cases,
        "issues":           test_report["issues"],
        "recommendations":  [],
        "test_report_file": report_file,
    }

    return {
        "status":      "success",
        "validation":  validation_response,
        "output_file": report_file,
    }


@app.get("/health")
async def health():
    return {"status": "ok", "agent": "step-validator"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("step_validator_agent:app", host="0.0.0.0", port=8088, reload=True)
