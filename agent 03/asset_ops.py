import os
import json
import pandas as pd
from io import BytesIO
from fastapi import FastAPI, UploadFile, File, HTTPException
from docx import Document
from openai import OpenAI
from dotenv import load_dotenv
from datetime import datetime

# Load environment variables
load_dotenv()

app = FastAPI(title="Asset Operations Agent")
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# --- Helper: Text Extraction ---
def extract_text(file_content: bytes, filename: str) -> str:
    """Parses raw bytes based on file extension and returns a string."""
    ext = filename.split('.')[-1].lower()
    
    try:
        if ext == 'csv':
            df = pd.read_csv(BytesIO(file_content))
            return df.to_string()
            
        elif ext in ['xls', 'xlsx']:
            df = pd.read_excel(BytesIO(file_content))
            return df.to_string()
            
        elif ext == 'json':
            data = json.loads(file_content)
            return json.dumps(data)
            
        elif ext in ['doc', 'docx']:
            doc = Document(BytesIO(file_content))
            return "\n".join([para.text for para in doc.paragraphs])
            
        else:
            # Fallback for plain text
            return file_content.decode('utf-8')
            
    except Exception as e:
        raise ValueError(f"Failed to parse {ext} file: {str(e)}")

# --- Core Endpoint ---
@app.post("/extract_assets")
async def extract_assets(file: UploadFile = File(...)):
    print(f"\n[ASSET_OPS] 📥 Receiving file: {file.filename}")
    
    # 1. Read File
    try:
        content = await file.read()
        raw_text = extract_text(content, file.filename)
        # Limit text size to avoid token limits (truncate if necessary)
        preview = raw_text[:500].replace('\n', ' ')
        print(f"[ASSET_OPS] 📄 Extracted text (preview): {preview}...")
    except Exception as e:
        print(f"[ERROR] Parsing failed: {e}")
        raise HTTPException(status_code=400, detail=f"File parsing error: {str(e)}")

    # 2. LLM Extraction
    print("[ASSET_OPS] 🤖 Sending to LLM for extraction...")
    system_prompt = (
        "You are a Data Extraction Specialist. "
        "Analyze the provided text and extract all assets."
        "Return ONLY a valid JSON object with a key 'assets' containing a list of objects."
        "Each object must have exactly two fields: 'asset_number' (string) and 'asset_type' (string)."
        "If a field is missing, use null."
    )
    
    try:
        response = client.chat.completions.create(
            model=os.environ.get("LLM_MODEL", "gpt-3.5-turbo"),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"DATA:\n{raw_text}"}
            ],
            temperature=0.0,
            response_format={"type": "json_object"}
        )
        
        extracted_data = json.loads(response.choices[0].message.content)
        
    except Exception as e:
        print(f"[ERROR] LLM processing failed: {e}")
        raise HTTPException(status_code=500, detail=f"LLM Error: {e}")

    # 3. Logging & Output
    asset_count = len(extracted_data.get('assets', []))
    print(f"[ASSET_OPS] ✅ Success. Extracted {asset_count} assets.")
    print(json.dumps(extracted_data, indent=2))  # Writes to Container Logs
    
    # Write output to file
    output_dir = "OUTPUT"
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_filename = os.path.join(output_dir, f"Step1_{timestamp}.json")
    with open(output_filename, 'w', encoding='utf-8') as f:
        json.dump(extracted_data, f, indent=2, ensure_ascii=False)
    print(f"[ASSET_OPS] 💾 Output saved to {output_filename}")
    
    return {
        "status": "success",
        "result": extracted_data,
        "output_file": output_filename,
        "extracted_text": raw_text[:10000],
        "files": {
            "files_read": [
                {"filename": file.filename, "role": "input", "description": "Raw asset data file uploaded by user"}
            ],
            "files_output": [
                {"filename": os.path.basename(output_filename), "path": output_filename, "role": "output", "description": "Extracted asset list (JSON)"}
            ]
        }
    }