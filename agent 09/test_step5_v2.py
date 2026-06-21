"""Build and POST the v2 site plan extraction test payload using the new sub-step IDs."""
import json, requests

chunks_base = "/app/OUTPUT/DAR1509-84 - Test_20260321_045056/chunks/3ca28ff7-70b5-4491-9c3a-db70ac604eee"
regions = [
    ("top-1",1,1), ("top-2",2,1), ("top-3",3,1), ("top-4",4,1),
    ("mid-1",1,2), ("mid-2",2,2), ("mid-3",3,2), ("mid-4",4,2),
    ("bottom-1",1,3), ("bottom-2",2,3), ("bottom-3",3,3), ("bottom-4",4,3),
]
heights = [566]*8 + [568]*4
chunks = []
for i, (region, col, row) in enumerate(regions):
    seq = i + 1
    chunks.append({
        "chunk_id":    f"chunk_{seq:04d}",
        "sequence":    seq,
        "page_number": 1,
        "region":      region,
        "grid":        "4x3",
        "col":         col,
        "row":         row,
        "filename":    f"chunk_{seq:04d}.png",
        "filepath":    f"{chunks_base}/chunk_{seq:04d}.png",
        "width_px":    550,
        "height_px":   heights[i],
    })

payload = {
    "process_id":       "proc-extract-site-plan-info",
    "documents_folder": "/app/OUTPUT/DAR1509-84 - Test_20260321_045056",
    "output_dir":       "/app/OUTPUT/DAR1509-84 - Test_20260321_045056",
    "files": [{
        "filename":         "DS1_DAR1988_RETIC.pdf",
        "filepath":         "/app/OUTPUT/DAR1509-84 - Test_20260321_045056/DS1_DAR1988_RETIC.pdf",
        "document_type":    "Reticulation Drawing",
        "document_category":"Site Plan",
        "content_type":     "image",
        "text_quality":     "none",
        "page_size":        "large-format",
        "requires_chunking": True,
        "chunk_strategy":   "quadrant-split",
        "estimated_chunks": 12,
        "chunk_manifest": {
            "job_id":          "3ca28ff7-70b5-4491-9c3a-db70ac604eee",
            "source_file":     "DS1_DAR1988_RETIC.pdf",
            "chunk_strategy":  "quadrant-split",
            "page_size":       "large-format",
            "dpi":             200,
            "total_pages":     1,
            "total_chunks":    12,
            "chunks":          chunks,
        },
    }],
    "process_step": {
        "step_number": 3,
        "step_id":     "step-extract-site-plan-data",
        "step_name":   "Extract Site Plan Data",
        "details":     "Two-phase extraction. Phase 1 extracts all notes and annotations from every page. Phase 2 analyses those notes against four checks.",
        "sub_steps": [
            {
                "sub_step_number": 1,
                "sub_step_id":   "sub-step-extract-all-notes",
                "sub_step_name": "Extract All Drawing Notes and Annotations",
                "details": (
                    "Extract every textual note and annotation from every page in full. "
                    "Capture: all numbered notes (1., 2., 3., ...) from the NOTES section with complete text; "
                    "all standalone ATTENTION:, WARNING:, CAUTION:, NOTICE: callout blocks with complete text; "
                    "any inline hand-written or red-line markup annotations. "
                    "Assemble notes split across adjacent image regions into single complete entries. "
                    "Include every note number — missing numbers indicate incomplete extraction."
                ),
            },
            {
                "sub_step_number": 2,
                "phase": 2,
                "sub_step_id":   "sub-step-substation-data",
                "sub_step_name": "Substation Notes — Structured Data Extraction",
                "details": (
                    "Locate the Substation Notes annotation block and extract seven fields as a formatted "
                    "multi-line string:\n"
                    "Substation Asset Number: [value or NOT FOUND]\n"
                    "Transformer Size: [value or NOT FOUND]\n"
                    "HV Switchgear: [value — if transformer is 1.5MVA, set to Siemens RLR and append: "
                    "OVERRIDDEN per 1.5MVA rule — Siemens RLR required]\n"
                    "Voltage Level: [value or NOT FOUND]\n"
                    "LV Switchgear: [value or NOT FOUND]\n"
                    "Cubicle Size: [value or NOT FOUND]\n"
                    "Earthing: [value or NOT FOUND]"
                ),
            },
            {
                "sub_step_number": 3,
                "phase": 2,
                "sub_step_id":   "sub-step-field-check",
                "sub_step_name": "Assets Field Check Compliance",
                "details": (
                    "Search all extracted notes for: "
                    "HAVE ALL EXISTING ASSETS BEEN FIELD CHECKED AND ARE ACCURATE AT THE TIME OF DRAWING "
                    "(the word DRAWING may appear as DESIGN — treat both as equivalent). "
                    "Return exactly one of: "
                    "'YES' (note present and answered YES), "
                    "'NO — NON-COMPLIANT' (note present but answered NO), "
                    "'NOT FOUND — MISSING: this compliance note is mandatory' (note absent from drawing)."
                ),
            },
            {
                "sub_step_number": 4,
                "phase": 2,
                "sub_step_id":   "sub-step-easement-restriction",
                "sub_step_name": "Easement — Restriction on Use of Land (Swimming Pool Setback)",
                "details": (
                    "Search all extracted notes for the restriction-on-use-of-land note: "
                    "'A RESTRICTION ON THE USE OF LAND, IN RELATION TO SWIMMING POOLS, "
                    "MEASURED 5m FROM THE SUBSTATION EASEMENT IS TO BE CREATED IN FAVOUR OF "
                    "ACME ENERGY WITHIN PROPOSED LOT [xxx]'. "
                    "If found: return the full note text verbatim including the lot number(s). "
                    "If not found and the drawing involves a padmount substation: return 'NOT FOUND — REQUIRED'. "
                    "If not applicable (no padmount substation): return 'NOT REQUIRED'."
                ),
            },
            {
                "sub_step_number": 5,
                "phase": 2,
                "sub_step_id":   "sub-step-easement-substation",
                "sub_step_name": "Easement — Padmount Substation Easement Creation",
                "details": (
                    "Search all extracted notes for the padmount substation easement note: "
                    "'AN EASEMENT FOR PADMOUNT SUBSTATION MINIMUM [x]m x [x]m IS TO BE CREATED "
                    "IN FAVOUR OF ACME ENERGY WITHIN PROPOSED LOT [xxxx]'. "
                    "If found: return the full note text verbatim including easement dimensions and lot number(s). "
                    "If not found and the drawing involves a padmount substation: return 'NOT FOUND — REQUIRED'. "
                    "If not applicable (no padmount substation): return 'NOT REQUIRED'."
                ),
            },
        ],
    },
}

print("Sending extraction request...")
resp = requests.post("http://localhost:8090/extract", json=payload, timeout=600)
print(f"HTTP {resp.status_code}")

data = resp.json()
ext = data.get("extractions", [{}])[0]
sse = ext.get("sub_step_extractions", {})

print("\n" + "="*60)
for sid, v in sse.items():
    name = v.get("sub_step_name", sid) if isinstance(v, dict) else sid
    val  = v.get("value") if isinstance(v, dict) else v
    print(f"\n[{name}]")
    print(val if val is not None else "(null)")
    print("-"*60)

with open("/tmp/step5_v2_result.json", "w") as f:
    json.dump(data, f, indent=2)
print("\nFull result saved to /tmp/step5_v2_result.json")
