"""
Test the _filter_notes_by_scope fix using chunks from the last run.
Sends the existing chunk data as phase1_cache so only the consolidation
and normalisation path re-runs — no vision OCR needed.
"""
import json, requests

EXTRACTOR_URL = "http://localhost:8090/extract"
LAST_RUN_EXTRACTION = (
    r"c:\Code\experiments\agent 09\OUTPUT"
    r"\DAR1509-84 - Exp_20260511_151913"
    r"\Extraction_DS1_DAR1988_RETIC_NOTES1_pdf_Extract_Site_Plan_Information_20260511_152336_434269.json"
)
LAST_RUN_REPORT = (
    r"c:\Code\experiments\agent 09\OUTPUT"
    r"\DAR1509-84 - Exp_20260511_151913"
    r"\ExtractionReport_proc_extract_site_plan_info_20260511_152336_434269.json"
)

with open(LAST_RUN_EXTRACTION) as f:
    ext = json.load(f)

with open(LAST_RUN_REPORT) as f:
    report = json.load(f)

filename      = ext["filename"]
chunk_details = ext["chunk_details"]
process_step  = report["process_step"]

# Build a minimal chunk_manifest from the chunk_details so the extractor
# takes the chunked-file path. Strip the 'extracted' key — that goes in
# phase1_cache, not the manifest.
manifest_chunks = [
    {k: v for k, v in cd.items() if k != "extracted"}
    for cd in chunk_details
]
chunk_manifest = {
    "chunk_strategy": ext.get("chunk_strategy", "dual-split"),
    "page_size":      ext.get("page_size", "A3"),
    "total_chunks":   len(manifest_chunks),
    "chunks":         manifest_chunks,
}

payload = {
    "files": [
        {
            "filename":           filename,
            "processing_tool_id": "tool-extract-pdf-content",
            "document_type":      ext.get("document_type"),
            "document_category":  ext.get("document_category"),
            "content_type":       "visual",
            "requires_chunking":  True,
            "chunk_strategy":     ext.get("chunk_strategy", "dual-split"),
            "chunk_manifest":     chunk_manifest,
            "phase1_cache":       chunk_details,  # skips Phase 1 vision OCR
        }
    ],
    "process_step":     process_step,
    "documents_folder": "/documents/DAR1509-84 - Exp",
}

print(f"Calling extractor with phase1_cache ({len(chunk_details)} chunk(s))...")
resp = requests.post(EXTRACTOR_URL, json=payload, timeout=120)

if resp.status_code != 200:
    print(f"ERROR {resp.status_code}: {resp.text[:500]}")
else:
    result = resp.json()
    exs = result.get("extractions", [])
    for ex in exs:
        sse   = ex.get("sub_step_extractions") or {}
        notes = sse.get("sub-step-extract-all-notes", {})
        val   = notes.get("value", "")
        import re
        note_nums = sorted({int(m.group(1)) for l in val.split("\n") for m in [re.match(r'^(\d{1,2})[.)]\s', l.strip())] if m})
        print(f"\nFile: {ex.get('filename')}")
        print(f"Sub-step keys returned: {list(sse.keys())}")
        print(f"Notes extracted: {len(note_nums)} — numbers found: {note_nums}")
        print(f"\n--- Full notes output (first 800 chars) ---")
        print(val[:800])
