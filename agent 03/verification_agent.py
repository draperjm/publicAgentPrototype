import os
import json
import logging
import tempfile
import time
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from dotenv import load_dotenv
from datetime import datetime
from google import genai
from google.genai import types
import pypdfium2 as pdfium

# Load environment variables
load_dotenv()

app = FastAPI(title="Asset Verification Agent")

# --- Logging Setup ---
logger = logging.getLogger("Verification_Agent")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

# --- Traffic Logger (file-based, for debugging prompts and LLM responses) ---
traffic_logger = logging.getLogger("Verification_Traffic")
traffic_logger.setLevel(logging.INFO)
if not traffic_logger.handlers:
    _file_handler = logging.FileHandler("verification_traffic.log", encoding="utf-8")
    _file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    traffic_logger.addHandler(_file_handler)

# --- AI Client Setup ---
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    logger.warning("GOOGLE_API_KEY not found. Agent may fail on visual tasks.")

# --- Logic ---
def verify_with_gemini(file_bytes: bytes, file_type: str, asset_list: List[Dict], legend: List[Dict]) -> str:
    """
    Uploads the drawing via the Gemini File API and cross-references every asset.
    Uses google-genai (the current supported SDK).
    """
    model_name = "gemini-2.5-flash"
    client = genai.Client(api_key=GOOGLE_API_KEY)

    asset_str = json.dumps(asset_list, indent=2)
    legend_str = json.dumps(legend, indent=2)

    # Pre-build a result skeleton with every asset already slotted in.
    # Gemini must replace every "FILL_IN" — it cannot return an empty array.
    skeleton = [
        {
            "asset_number": a.get("asset_number"),
            "asset_type": a.get("asset_type"),
            "expected_icon_description": a.get("expected_icon_description", ""),
            "status": "FILL_IN",
            "observed_symbol": "FILL_IN",
            "confidence": "FILL_IN"
        }
        for a in asset_list
    ]
    skeleton_str = json.dumps({"verification_report": skeleton}, indent=2)

    system_prompt = (
        "You are a strict Senior QA Engineer auditing an infrastructure site plan drawing.\n"
        "Your task: verify every asset in the Asset List against the provided Drawing.\n\n"
        "INPUT DATA:\n"
        f"1. ASSET LIST with expected icon descriptions:\n{asset_str}\n\n"
        f"2. LEGEND (for reference):\n{legend_str}\n\n"
        "IMPORTANT VISUAL GUIDANCE:\n"
        "- Asset number labels (e.g. '2004852', 'S405104') are SMALL TEXT placed directly\n"
        "  beside a small icon symbol on the drawing.\n"
        "- 'Hollow square outline' / 'Empty square outline' = a small open square (no fill).\n"
        "  This is the icon for NEW COLUMN and NEW PILLAR assets.\n"
        "- DO NOT confuse this with the larger starburst/asterisk/radial-spike circle symbols\n"
        "  along the roads — those are service connection markers (labeled B1, B2, K, J etc.)\n"
        "  and are NOT the asset icon. Ignore them.\n"
        "- The asset icon is the SMALL symbol immediately adjacent to the asset number text.\n\n"
        "INSTRUCTIONS — follow for every single asset without exception:\n"
        "1. Find the exact 'asset_number' text label on the drawing.\n"
        "2. Look at the SMALL icon immediately next to that label (not nearby road markers).\n"
        "3. Compare the small icon to 'expected_icon_description':\n"
        "   - Match → status='VERIFIED'\n"
        "   - No match → status='ICON_MISMATCH', describe what you actually see\n"
        "4. If the label is NOT found: status='MISSING_ON_DRAWING', "
        "observed_symbol='Not found on drawing', confidence='High'.\n"
        "5. confidence: 'High' = label and icon both clearly visible; "
        "'Medium' = one unclear; 'Low' = estimating.\n\n"
        f"FILL IN THE SKELETON BELOW — replace every 'FILL_IN' value.\n"
        f"The array MUST contain exactly {len(asset_list)} entries. "
        f"Do NOT remove entries. Do NOT return an empty array.\n\n"
        f"SKELETON TO COMPLETE:\n{skeleton_str}\n"
    )

    output_req = (
        "\nRETURN ONLY the completed JSON object — no markdown, no extra text.\n"
        f"The 'verification_report' array must have exactly {len(asset_list)} entries."
    )

    # Render PDF to high-resolution PNG (300 DPI) so Gemini can read small labels.
    if file_type == "pdf":
        logger.info("Converting PDF to high-resolution PNG (300 DPI)...")
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as pdf_tmp:
            pdf_tmp.write(file_bytes)
            pdf_tmp_path = pdf_tmp.name
        try:
            doc = pdfium.PdfDocument(pdf_tmp_path)
            page = doc[0]
            bitmap = page.render(scale=3.5, rotation=0)
            img = bitmap.to_pil()
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as png_tmp:
                img.save(png_tmp, format="PNG")
                upload_path = png_tmp.name
            upload_mime = "image/png"
            logger.info(f"Rendered to PNG: {img.size[0]}x{img.size[1]} px")
        finally:
            os.unlink(pdf_tmp_path)
    else:
        with tempfile.NamedTemporaryFile(suffix=f".{file_type}", delete=False) as img_tmp:
            img_tmp.write(file_bytes)
            upload_path = img_tmp.name
        upload_mime = f"image/{file_type}"

    tmp_path = upload_path
    uploaded_file = None
    try:
        logger.info(f"Uploading to Gemini File API ({os.path.getsize(upload_path)//1024} KB)...")
        with open(upload_path, "rb") as f:
            uploaded_file = client.files.upload(
                file=f,
                config=types.UploadFileConfig(mime_type=upload_mime)
            )

        # Wait for Google to finish processing the file
        while uploaded_file.state.name == "PROCESSING":
            time.sleep(2)
            uploaded_file = client.files.get(name=uploaded_file.name)

        if uploaded_file.state.name == "FAILED":
            raise HTTPException(status_code=500, detail="Gemini File API failed to process the drawing.")

        logger.info(f"File ready. Sending analysis request to {model_name}...")

        full_prompt = system_prompt + output_req
        traffic_logger.info(
            f"\n{'='*60} VERIFICATION REQUEST {'='*60}\n"
            f"MODEL: {model_name}\n"
            f"FILE: {uploaded_file.name}  (original={file_type}, uploaded={upload_mime}, {len(file_bytes)//1024} KB)\n"
            f"ASSETS ({len(asset_list)}): {json.dumps([a.get('asset_number') for a in asset_list])}\n"
            f"--- FULL PROMPT ---\n{full_prompt}\n"
            f"{'='*60} END REQUEST {'='*60}\n"
        )

        response = client.models.generate_content(
            model=model_name,
            contents=[full_prompt, uploaded_file]
        )
        raw_text = response.text

        traffic_logger.info(
            f"\n{'='*60} VERIFICATION RESPONSE {'='*60}\n"
            f"MODEL: {model_name}\n"
            f"--- RAW RESPONSE ---\n{raw_text}\n"
            f"{'='*60} END RESPONSE {'='*60}\n"
        )

        return raw_text

    except HTTPException:
        raise
    except Exception as e:
        traffic_logger.error(
            f"\n{'='*60} VERIFICATION ERROR {'='*60}\n"
            f"MODEL: {model_name}\n"
            f"ERROR: {e}\n"
            f"{'='*60} END ERROR {'='*60}\n"
        )
        logger.error(f"Gemini API Error: {e}")
        raise HTTPException(status_code=500, detail=f"AI Processing Failed: {e}")
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        if uploaded_file:
            try:
                client.files.delete(name=uploaded_file.name)
            except Exception:
                pass


# --- Endpoints ---

@app.post("/verify_assets")
async def verify_assets(
    drawing: Optional[UploadFile] = File(None),
    asset_list_json: str = Form(..., description="JSON string of the asset list"),
    legend_json: str = Form(..., description="JSON string of the legend")
):
    """
    Main Verification Endpoint.
    1. Checks if Drawing is present.
    2. If NOT, returns a 'request_file' action to the Frontend.
    3. If YES, proceeds with AI verification.
    """
    print(f"\n[VERIFIER] Request Received. Checking for file...")

    if not drawing:
        print("[VERIFIER] No file provided. Requesting upload from User...")
        return {
            "status": "interaction_required",
            "action": "request_file_upload",
            "message": "Please upload the P&ID Drawing (PDF or Image) to proceed with verification.",
            "required_fields": ["drawing"]
        }

    print(f"[VERIFIER] Processing {drawing.filename}...")

    try:
        assets = json.loads(asset_list_json)
        legend = json.loads(legend_json)
        if isinstance(assets, dict) and "assets" in assets:
            assets = assets["assets"]
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON input: {e}")

    content = await drawing.read()
    file_ext = drawing.filename.split('.')[-1].lower()

    raw_response = verify_with_gemini(content, file_ext, assets, legend)

    clean_json = raw_response.replace("```json", "").replace("```", "").strip()

    try:
        parsed_report = json.loads(clean_json)
    except json.JSONDecodeError:
        parsed_report = {"raw_output": clean_json, "status": "parse_error"}

    output_dir = "OUTPUT"
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_filename = os.path.join(output_dir, f"Verification_Report_{timestamp}.json")

    with open(output_filename, 'w', encoding='utf-8') as f:
        json.dump(parsed_report, f, indent=2)

    print(f"[VERIFIER] Report generated: {output_filename}")

    return {
        "status": "success",
        "report": parsed_report,
        "output_file": output_filename,
        "files": {
            "files_read": [
                {"filename": drawing.filename, "role": "input", "description": "P&ID drawing file for visual verification"},
                {"filename": "asset_map (from Step 3)", "role": "input", "description": "Asset-to-legend mapping from mapping step"},
                {"filename": "legend (from Step 2)", "role": "input", "description": "Legend data from content review step"}
            ],
            "files_output": [
                {"filename": os.path.basename(output_filename), "path": output_filename, "role": "output", "description": "Verification report (JSON)"}
            ]
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8086)
