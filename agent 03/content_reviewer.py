import os
import json
import ast
import logging
import pdfplumber
import pandas as pd
from io import BytesIO
from docx import Document
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from dotenv import load_dotenv
from typing import Optional, List, Union
from datetime import datetime
import sys

# --- AI SDKs ---
from openai import OpenAI
import anthropic
import google.generativeai as genai

load_dotenv()

app = FastAPI(title="Content Reviewer & Validator Agent")

# ... [Logging Setup remains the same] ...
llm_logger = logging.getLogger("LLM_Traffic")
llm_logger.setLevel(logging.INFO)
if not llm_logger.handlers:
    file_handler = logging.FileHandler("llm_traffic.log", encoding='utf-8')
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    llm_logger.addHandler(file_handler)

# --- 1. Model Dispatcher Logic (UPDATED) ---
class ModelDispatcher:
    def __init__(self):
        self.openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        
        self.anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
        if self.anthropic_key:
            self.claude_client = anthropic.Anthropic(api_key=self.anthropic_key)
        
        self.google_key = os.environ.get("GOOGLE_API_KEY")
        if self.google_key:
            genai.configure(api_key=self.google_key)

    def get_provider_for_file(self, filename: str):
        ext = filename.split('.')[-1].lower()
        if ext in ['xlsx', 'xls', 'csv']:
            return "anthropic", "claude-3-opus-20240229"
        elif ext == 'pdf':
            # Use a Flash model for speed/multimodal, or Pro for deeper reasoning
            return "google", "gemini-2.5-flash" 
        elif ext in ['doc', 'docx']:
            return "openai", "gpt-4o"
        else:
            return "openai", "gpt-4o"

    def generate(self, provider: str, model: str, system_prompt: str, user_content: str, media_file: dict = None):
        """
        media_file: Dict containing {'mime_type': str, 'data': bytes} (Only used for Google)
        """
        print(f"[REVIEWER] 🧠 Routing to {provider.upper()} ({model})...")
        
        try:
            if provider == "openai":
                resp = self.openai_client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content}
                    ],
                    temperature=0.0
                )
                return resp.choices[0].message.content

            elif provider == "anthropic":
                resp = self.claude_client.messages.create(
                    model=model,
                    max_tokens=4000,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_content}]
                )
                return resp.content[0].text

            elif provider == "google":
                model_instance = genai.GenerativeModel(model)
                
                # Combine System Prompt and User Text instructions
                # Gemini doesn't have a strict 'system' role in `generate_content` yet, 
                # so we merge it into the text prompt.
                full_text_prompt = f"SYSTEM_INSTRUCTION:\n{system_prompt}\n\nUSER_REQUEST:\n{user_content}"
                
                content_payload = [full_text_prompt]
                
                # --- CRITICAL UPDATE: Add the PDF Bytes ---
                if media_file:
                    print(f"[REVIEWER] 📎 Attaching raw {media_file['mime_type']} to Gemini request...")
                    content_payload.append(media_file)

                resp = model_instance.generate_content(content_payload)
                return resp.text

        except Exception as e:
            print(f"[ERROR] {provider} failed: {e}. Falling back to OpenAI (GPT-4o)...")
            # Fallback (Note: OpenAI fallback will fail if we rely on the PDF image data, 
            # so we might need to rely on the extracted text logic if we fallback)
            return self.generate("openai", "gpt-4o", system_prompt, user_content)

dispatcher = ModelDispatcher()

# ... [File Parsers remain the same] ...
def extract_content(file_bytes: bytes, filename: str) -> str:
    # (Same function as before - we still keep this for OpenAI/Anthropic or logging)
    ext = filename.split('.')[-1].lower()
    text = ""
    try:
        if ext == 'pdf':
            with pdfplumber.open(BytesIO(file_bytes)) as pdf:
                text = "\n".join([p.extract_text() or "[EMPTY_PAGE]" for p in pdf.pages])
        elif ext in ['xlsx', 'xls']:
            df_dict = pd.read_excel(BytesIO(file_bytes), sheet_name=None)
            for sheet, df in df_dict.items():
                text += f"--- SHEET: {sheet} ---\n{df.to_string()}\n"
        elif ext in ['doc', 'docx']:
            doc = Document(BytesIO(file_bytes))
            text = "\n".join([p.text for p in doc.paragraphs])
        else:
            text = file_bytes.decode('utf-8', errors='ignore')
    except Exception as e:
        return f"Error reading file: {str(e)}"
    return text

# ... [Validation Logic remains the same] ...
def validate_output(result, criteria):
    """
    Uses a lightweight LLM call to verify if the output matches specific criteria.
    Also validates JSON structure to catch invalid items in arrays.
    """
    if not criteria: 
        return True, ""
    
    # First, try to parse JSON and check for invalid array items
    try:
        parsed = json.loads(result)
        # Check if any values in the JSON are stray strings/numbers (likely errors)
        for key, value in (parsed.items() if isinstance(parsed, dict) else []):
            if isinstance(value, list):
                for item in value:
                    if not isinstance(item, dict):
                        return False, f"Invalid array item: '{item}' is not a dictionary. All items in '{key}' array must be objects with proper keys."
        # If we get here, basic structure is valid
    except json.JSONDecodeError as e:
        return False, f"JSON Parse Error: {str(e)}. Ensure all strings use double quotes and all arrays/objects are properly closed."
    
    # Now run validation criteria
    prompt = (
        f"Check if this output meets the criteria: {criteria}\n"
        f"OUTPUT: {result}\n"
        "Respond with JSON: {\"valid\": boolean, \"reason\": string}"
    )
    
    try:
        # --- LOGGING VALIDATION REQUEST ---
        llm_logger.info(
            f"\n========== VALIDATION REQUEST ==========\n"
            f"PROVIDER: openai / gpt-4o\n"
            f"--- VALIDATION PROMPT ---\n{prompt}\n"
            f"========================================\n"
        )
        
        resp = dispatcher.openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        
        validation_response = resp.choices[0].message.content
        
        # --- LOGGING VALIDATION RESPONSE ---
        llm_logger.info(
            f"\n========== VALIDATION RESPONSE ==========\n"
            f"{validation_response}\n"
            f"=========================================\n"
        )
        
        data = json.loads(validation_response)
        return data.get("valid"), data.get("reason")
    except Exception as e:
        error_msg = f"Validation crashed: {str(e)}"
        llm_logger.error(f"\n========== VALIDATION ERROR ==========\n{error_msg}\n=====================================\n")
        print(f"[WARNING] {error_msg}")
        return False, error_msg

# --- 4. Endpoints (UPDATED) ---
@app.post("/review_content")
async def review_content(
    file: UploadFile = File(...),
    instruction: str = Form(...),
    agent_instructions: str = Form(None),
    validation_criteria: str = Form(None)
):
    print(f"[REVIEWER] 📥 Processing {file.filename}...")
    
    # 1. Read File Bytes
    content = await file.read()
    file_size = len(content)
    file_ext = file.filename.split('.')[-1].lower()
    
    # 2. Select Model
    provider, model = dispatcher.get_provider_for_file(file.filename)
    
    # 3. Prepare Content
    # Always extract text for logging or fallback purposes
    extracted_text = extract_content(content, file.filename)
    
    media_payload = None
    
    # --- GOOGLE PDF HANDLING ---
    if provider == "google" and file_ext == "pdf":
        # We create a dictionary that Gemini SDK recognizes
        media_payload = {
            "mime_type": "application/pdf",
            "data": content
        }
        # For Gemini, the 'user_msg' will just be the instruction, 
        # because the DATA is in the media_payload
        context_msg = f"UPLOADED FILE: {file.filename}\n\nINSTRUCTION: {instruction}"
    else:
        # Standard Text Handling for OpenAI / Anthropic
        context_msg = f"UPLOADED FILE: {file.filename}\nDATA_CONTEXT:\n{extracted_text[:30000]}\n\nINSTRUCTION: {instruction}"

    # 4. Execution Loop
    last_result = ""
    last_feedback = ""
    
    for attempt in range(1, 4):
        print(f"[REVIEWER] 🔄 Attempt {attempt}/3 using {provider}...")
        
        # Build System Prompt
        sys_prompt = (
            "You are a Strict Data Extraction and Review Agent.\n"
            "CRITICAL RULES:\n"
            "1. Output ONLY valid JSON - no markdown, no explanations, no extra text.\n"
            "2. Every item in arrays MUST be a complete dictionary/object.\n"
            "3. Do NOT include stray strings, numbers, or partial values in arrays.\n"
            "4. Do NOT add commas after the last item in arrays or objects.\n"
            "5. Use double quotes for all JSON keys and string values.\n"
            "6. Validate your JSON is parseable before responding.\n"
            "7. When extracting legend/icon data, each object MUST have exactly these fields:\n"
            '   - "Icon": A visual description of what the icon/symbol LOOKS LIKE (e.g. "Small hollow square", "Single dashed horizontal line")\n'
            '   - "asset_type": The TEXT LABEL or NAME of the asset type (e.g. "NEW COLUMN", "EXISTING UNDERGROUND MAINS")\n'
            "   IMPORTANT: Do NOT swap these fields. 'Icon' = visual appearance, 'asset_type' = the name/label.\n"
            "8. Every 'Icon' description MUST be UNIQUE across all entries. If two icons look similar,\n"
            "   describe their visual differences precisely (e.g. line thickness, dash pattern, spacing,\n"
            "   additional marks like hash marks or perpendicular segments). Two different asset types\n"
            "   MUST NEVER share the same Icon description."
        )
        if agent_instructions:
            sys_prompt += f"\n\nCRITICAL OPERATIONAL PROTOCOLS:\n{agent_instructions}"

        user_msg = context_msg
        if last_feedback:
            user_msg += f"\n\nPREVIOUS ATTEMPT FAILED - FIX THESE ERRORS:\n{last_feedback}\nEnsure ALL items in arrays are complete dictionary objects, never stray strings."

        # --- LOGGING REQUEST ---
        llm_logger.info(
            f"\n========== REQUEST START [Attempt {attempt}] ==========\n"
            f"PROVIDER: {provider} / {model}\n"
            f"--- SYSTEM PROMPT ---\n{sys_prompt}\n"
            f"--- USER MESSAGE ---\n{user_msg}\n"
            f"=======================================================\n"
        )

        # Generate (Passing the media_payload if it exists)
        last_result = dispatcher.generate(provider, model, sys_prompt, user_msg, media_file=media_payload)
        
        # --- LOGGING RESPONSE ---
        llm_logger.info(
            f"\n========== RESPONSE [Attempt {attempt}] ==========\n"
            f"{last_result}\n"
            f"================================================\n"
        )
        
        # Cleanup & Validate
        clean_result = last_result.replace("```json", "").replace("```", "").strip()
        is_valid, feedback = validate_output(clean_result, validation_criteria)
        
        if is_valid:
            print(f"[REVIEWER] ✅ FINAL OUTPUT:\n{clean_result}\n")
            try:
                parsed_result = json.loads(clean_result)
            except:
                parsed_result = {"raw": clean_result}
            
            # Write output to file
            output_dir = "OUTPUT"
            os.makedirs(output_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_filename = os.path.join(output_dir, f"Step2_{timestamp}.json")
            with open(output_filename, 'w', encoding='utf-8') as f:
                json.dump(parsed_result, f, indent=2, ensure_ascii=False)
            print(f"[REVIEWER] 💾 Output saved to {output_filename}")
            
            # For visual PDF processing (Gemini), pdfplumber may return [EMPTY_PAGE]
            # because the PDF contains images not text. In that case, provide the
            # LLM's own extraction as the source context for the validator.
            source_text = extracted_text[:10000]
            is_empty_extraction = all(
                line.strip() in ("[EMPTY_PAGE]", "") for line in extracted_text.split("\n")
            )
            if is_empty_extraction and media_payload:
                source_text = (
                    f"[VISUAL PDF] File '{file.filename}' ({file_size} bytes) was processed "
                    f"visually by {provider}/{model} (multimodal). Text extraction returned empty "
                    f"because the PDF contains images/diagrams, not selectable text. "
                    f"The model analysed the raw PDF bytes directly. "
                    f"Validator should assess output structure and internal consistency rather "
                    f"than cross-referencing against raw text."
                )

            return {
                "status": "success",
                "model": model,
                "result": parsed_result,
                "output_file": output_filename,
                "extracted_text": source_text,
                "files": {
                    "files_read": [
                        {"filename": file.filename, "role": "input", "description": f"Content file for review ({file_ext.upper()})"}
                    ],
                    "files_output": [
                        {"filename": os.path.basename(output_filename), "path": output_filename, "role": "output", "description": "Extracted legend/content data (JSON)"}
                    ]
                }
            }
        
        last_feedback = feedback
        print(f"[REVIEWER] ❌ Validation Failed: {feedback}")

    raise HTTPException(status_code=422, detail=f"Failed: {last_feedback}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)