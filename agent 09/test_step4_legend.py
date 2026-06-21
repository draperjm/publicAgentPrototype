"""Test Step 4 — Extract Drawing Legend from proc-extract-site-plan-info (all pages)."""
import json, requests, os

manifest_path = os.path.join(
    os.path.dirname(__file__),
    "OUTPUT", "DAR1509-84 - Test_20260322_042106",
    "chunks", "7bd21e32-fbd2-4703-a0fc-bebbe9249cd2", "manifest.json"
)
with open(manifest_path, encoding="utf-8") as f:
    manifest = json.load(f)

chunks = manifest["chunks"]
total_pages = manifest["total_pages"]
total_chunks = manifest["total_chunks"]
print(f"Loaded {total_chunks} chunks across {total_pages} page(s) from manifest")

payload = {
    "process_id":       "proc-extract-site-plan-legend",
    "documents_folder": "/app/OUTPUT/DAR1509-84 - Test_20260322_042106",
    "output_dir":       "/app/OUTPUT/DAR1509-84 - Test_20260322_042106",
    "files": [{
        "filename":          "DS1_DAR1988_RETIC.pdf",
        "filepath":          "/app/OUTPUT/DAR1509-84 - Test_20260322_042106/DS1_DAR1988_RETIC.pdf",
        "document_type":     "Reticulation Drawing",
        "document_category": "Site Plan",
        "content_type":      "image",
        "text_quality":      "none",
        "page_size":         "large-format",
        "requires_chunking": True,
        "chunk_strategy":    "quadrant-split",
        "estimated_chunks":  12,
        "chunk_manifest": {
            "job_id":         "7bd21e32-fbd2-4703-a0fc-bebbe9249cd2",
            "source_file":    "DS1_DAR1988_RETIC.pdf",
            "chunk_strategy": "quadrant-split",
            "page_size":      "large-format",
            "dpi":            200,
            "total_pages":    total_pages,
            "total_chunks":   total_chunks,
            "chunks":         chunks,
        },
    }],
    "process_step": {
        "step_number": 4,
        "step_id":     "step-extract-drawing-legend",
        "step_name":   "Extract Drawing Legend",
        "details": (
            "Scan every image chunk of the drawing for a legend, key, notation, or symbol table. "
            "For each row in the legend, extract: (1) the VISUAL GEOMETRY of the drawn symbol — describe its exact shape, fill, "
            "line weight, line pattern, and any interior marks as you see them drawn; (2) the exact text label; (3) the page number. "
            "CRITICAL RULE: symbol_description must describe what the symbol LOOKS LIKE as a drawn graphic, NOT a paraphrase or echo "
            "of the label text. Each legend row has its own unique symbol — do not assign the same visual description to multiple rows. "
            "If you see the same description repeating for consecutive rows, you have misread the legend — look again at each row "
            "individually. "
            "REFERENCE TABLE — use these known symbol descriptions when you recognise the visual pattern next to the label: "
            "NEW LV TRENCH → Long dashed line; STRING NEW OH CABLE → Short dashed line; "
            "EXISTING UNDERGROUND MAINS → Alternating long-short dashed line; EXISTING OH CABLE → Solid thin line; "
            "REMOVE CONDUCTOR → Dotted line; EXISTING DUCTS → Thick heavy solid black line; "
            "NEW HV TRENCH → Solid line with two diagonal slash marks; "
            "EXISTING LANTERN → Small circle with internal cross (four-quadrant cross inside circle); "
            "REMOVE LANTERN → Circle with internal cross and starburst outer spikes radiating from edge; "
            "NEW LANTERN → Circle with starburst outer spikes and hollow centre; "
            "EXISTING POLE → Small solid black filled circle; "
            "REMOVE POLE → Small solid black filled circle with large X through it; "
            "REPLACE POLE → Circle split vertically half-black half-white; NEW POLE → Small hollow open circle; "
            "EXISTING COLUMN → Small solid black filled square; NEW COLUMN → Small hollow open square; "
            "EXISTING PILLAR → Solid black filled rectangle; NEW PILLAR → Hollow rectangle outline; "
            "PADMOUNT SUBSTATION → Rectangle containing two triangles touching at their points; "
            "POLE SUBSTATION → Circle with triangle inside; "
            "LV LINK (N/O) → Small circle with short vertical ticks on top and bottom; "
            "HV ABS (N/C) → Circle with horizontal line through the centre; "
            "HV USL (N/C) → Semi-circle dome with horizontal base line; "
            "HIGH PRESSURE GAS → Solid line with GAS text centred along it; "
            "WATER MAIN → Solid line with W characters spaced evenly along it; "
            "LGA DEMARCATION → Thin solid line; NEW FREEWAY BOUNDARY → Faint light grey long dashed line; "
            "ACME ENERGY EASEMENT → Faint light grey medium dashed line."
        ),
        "sub_steps": [
            {
                "sub_step_number": 1,
                "phase": 1,
                "sub_step_id":   "sub-step-extract-legend",
                "sub_step_name": "Extract Legend Entries from All Pages",
                "details": (
                    "Scan every image chunk of the drawing for a legend, key, notation, or symbol table. "
                    "For each row in the legend, extract the VISUAL GEOMETRY of the drawn symbol (not a label paraphrase), "
                    "the exact text label, and the page number. Each row must produce a UNIQUE symbol_description. "
                    "Use the REFERENCE TABLE in the step details to match known symbols to their descriptions."
                ),
                "output_format": (
                    "JSON array of legend entry objects. Each object must have: "
                    "'page' (integer, 1-based), "
                    "'symbol_description' (string — visual geometry only: shape, fill, line weight, pattern, interior marks — "
                    "NEVER a label paraphrase. Each entry must be UNIQUE. Examples: "
                    "'Long dashed line', 'Small hollow open circle', 'Hollow rectangle outline', "
                    "'Circle with starburst outer spikes and hollow centre', "
                    "'Small solid black filled circle with large X through it'), "
                    "'label' (string — verbatim label text from drawing), "
                    "'category' (string — one of: cable, equipment, substation, boundary, annotation, earthing, other). "
                    'Example: [{"page":1,"symbol_description":"Long dashed line","label":"NEW LV TRENCH","category":"cable"},'
                    '{"page":1,"symbol_description":"Small hollow open circle","label":"NEW POLE","category":"equipment"},'
                    '{"page":1,"symbol_description":"Hollow rectangle outline","label":"NEW PILLAR","category":"equipment"},'
                    '{"page":1,"symbol_description":"Rectangle containing two triangles touching at their points","label":"PADMOUNT SUBSTATION","category":"substation"}]'
                ),
            }
        ],
    },
}

print("Sending legend extraction request...")
resp = requests.post("http://localhost:8090/extract", json=payload, timeout=600)
print(f"HTTP {resp.status_code}")

data = resp.json()
ext = data.get("extractions", [{}])[0]
sse = ext.get("sub_step_extractions", {})

print("\n" + "="*60)
legend_entry = sse.get("sub-step-extract-legend", {})
legend_val = legend_entry.get("value") if isinstance(legend_entry, dict) else legend_entry

if legend_val is None:
    print("(no legend extracted)")
elif isinstance(legend_val, list):
    print(f"Extracted {len(legend_val)} legend entries:\n")
    # Group by page
    pages = {}
    for e in legend_val:
        pg = e.get("page") or 1
        pages.setdefault(pg, []).append(e)
    for pg in sorted(pages):
        print(f"  Page {pg}:")
        for e in pages[pg]:
            sym  = e.get("symbol_description") or "?"
            lbl  = e.get("label") or "?"
            cat  = e.get("category") or "?"
            print(f"    [{cat:12s}]  {sym:<45s}  ->  {lbl}")
        print()
elif isinstance(legend_val, str):
    # Try JSON parse
    try:
        entries = json.loads(legend_val)
        print(f"Extracted {len(entries)} legend entries (JSON string):\n")
        for e in entries:
            pg  = e.get("page", 1)
            sym = e.get("symbol_description") or "?"
            lbl = e.get("label") or "?"
            cat = e.get("category") or "?"
            print(f"  p{pg} [{cat:12s}]  {sym:<45s}  ->  {lbl}")
    except Exception:
        print(legend_val)
else:
    print(repr(legend_val))

print("="*60)

# Save full result
import os
out_path = os.path.join(os.path.dirname(__file__), "test_step4_legend_result.json")
with open(out_path, "w") as f:
    json.dump(data, f, indent=2)
print(f"\nFull result saved to {out_path}")
