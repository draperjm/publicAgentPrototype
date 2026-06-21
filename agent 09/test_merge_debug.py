"""
Debug test: run dual-split extraction and print exactly what the orchestrator's
_merge_extraction_results returns — this is what the frontend actually sees.
"""
import json, re, requests, sys

EXTRACTOR_URL = "http://localhost:8090/extract"

# Load the last run's extraction data
LAST_EXTRACTION = (
    r"c:\Code\experiments\agent 09\OUTPUT"
    r"\DAR1509-84 - Exp_20260511_220942"
    r"\Extraction_DS1_DAR1988_RETIC_NOTES1_pdf_Extract_Site_Plan_Information_20260511_221217_399851.json"
)
LAST_REPORT = (
    r"c:\Code\experiments\agent 09\OUTPUT"
    r"\DAR1509-84 - Exp_20260511_220942"
    r"\ExtractionReport_proc_extract_site_plan_info_20260511_221217_399851.json"
)

with open(LAST_EXTRACTION) as f:
    ext = json.load(f)
with open(LAST_REPORT) as f:
    report = json.load(f)

filename      = ext["filename"]
chunk_details = ext["chunk_details"]
process_step  = report["process_step"]

# Reconstruct dual-split chunk manifest from the last run's chunk test report
CHUNK_TEST = (
    r"c:\Code\experiments\agent 09\OUTPUT"
    r"\TestReport_Chunk_Documents_for_Processing_20260511_221004.json"
)
import glob as _glob
chunk_test_files = _glob.glob(
    r"c:\Code\experiments\agent 09\OUTPUT\TestReport_Chunk_Documents_for_Processing*.json"
)
chunk_test_files.sort(key=lambda f: f)
print(f"Chunk test files: {[f.split(chr(92))[-1] for f in chunk_test_files[-3:]]}")

# Use the latest chunk test report to get the manifest
with open(chunk_test_files[-1]) as f:
    ct = json.load(f)

ctx = ct.get("execution_context", {})
prompt = ctx.get("prompt", "")
# Extract the manifest JSON from the prompt
import re as _re
m = _re.search(r'"chunk_strategy":\s*"dual-split".*?"chunks":\s*(\[.*?\])', prompt, _re.DOTALL)
if m:
    chunks_json = m.group(1)
    # Fix: extract up to the matching bracket
    depth = 0
    end = 0
    for i, c in enumerate(chunks_json):
        if c == '[': depth += 1
        elif c == ']':
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    chunks_list = json.loads(chunks_json[:end])
    print(f"\nChunks in manifest: {len(chunks_list)}")
    for c in chunks_list:
        print(f"  chunk_id={c.get('chunk_id')}, region={c.get('region')}, split_pass={c.get('split_pass')}")

    chunk_manifest = {
        "chunk_strategy": "dual-split",
        "page_size": "A3",
        "total_chunks": len(chunks_list),
        "chunks": chunks_list,
    }
else:
    print("ERROR: Could not find dual-split manifest in test report")
    sys.exit(1)

def _filter_manifest(manifest, pass_num):
    filtered = [c for c in manifest.get("chunks", []) if c.get("split_pass") == pass_num]
    return {**manifest, "chunks": filtered, "total_chunks": len(filtered)}

def run_pass(pass_num, label):
    filtered = _filter_manifest(chunk_manifest, pass_num)
    print(f"\n--- Pass {pass_num} ({label}): {len(filtered['chunks'])} chunks ---")
    for c in filtered["chunks"]:
        print(f"  {c.get('chunk_id')}, region={c.get('region')}, filepath={c.get('filepath')}")

    payload = {
        "files": [{
            "filename":           filename,
            "processing_tool_id": "tool-extract-pdf-content",
            "document_type":      ext.get("document_type"),
            "document_category":  ext.get("document_category"),
            "content_type":       "visual",
            "requires_chunking":  True,
            "chunk_strategy":     "dual-split",
            "chunk_manifest":     filtered,
            "phase1_cache":       chunk_details,
        }],
        "process_step":     process_step,
        "documents_folder": "/documents/DAR1509-84 - Exp",
    }
    resp = requests.post(EXTRACTOR_URL, json=payload, timeout=120)
    if resp.status_code != 200:
        print(f"  ERROR {resp.status_code}: {resp.text[:200]}")
        return None
    rj = resp.json()
    exs = rj.get("extractions", [])
    if exs:
        sse = exs[0].get("sub_step_extractions", {})
        notes_val = (sse.get("sub-step-extract-all-notes") or {}).get("value", "")
        nums = sorted({int(m.group(1)) for l in notes_val.split("\n") for m in [re.match(r'^(\d{1,2})[.)]\s', l.strip())] if m})
        print(f"  Notes: {len(nums)} — {nums}")
        print(f"  First 120 chars: {repr(notes_val[:120])}")
    return rj

pass1 = run_pass(1, "top/bottom")
pass2 = run_pass(2, "left/right")

if pass1 and pass2:
    # Simulate _merge_notes
    def _merge_notes(t1, t2):
        if not t1: return t2 or ""
        if not t2: return t1 or ""
        def _parse(text):
            out = {}
            for part in re.split(r'(?=\n\d{1,2}\.\s)', "\n" + text.strip()):
                part = part.strip()
                if not part: continue
                m = re.match(r'^(\d{1,2})\.\s', part)
                if m:
                    out[int(m.group(1))] = part
            return out
        d1, d2 = _parse(t1), _parse(t2)
        merged = {**d1}
        for k, v in d2.items():
            if k not in merged or len(v) > len(merged[k]):
                merged[k] = v
        return "\n".join(merged[k] for k in sorted(merged))

    p1_notes = (pass1.get("extractions", [{}])[0].get("sub_step_extractions", {}).get("sub-step-extract-all-notes", {}) or {}).get("value", "")
    p2_notes = (pass2.get("extractions", [{}])[0].get("sub_step_extractions", {}).get("sub-step-extract-all-notes", {}) or {}).get("value", "")

    merged = _merge_notes(p1_notes, p2_notes)
    nums = sorted({int(m.group(1)) for l in merged.split("\n") for m in [re.match(r'^(\d{1,2})[.)]\s', l.strip())] if m})
    print(f"\n=== MERGED RESULT: {len(nums)} notes — {nums} ===")
    print(merged[:400])
