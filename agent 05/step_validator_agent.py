import os
import json
import logging
from datetime import datetime
from typing import Optional, List
from fastapi import FastAPI, Form, UploadFile, File
from dotenv import load_dotenv

import google.generativeai as genai
from openai import OpenAI

load_dotenv()

app = FastAPI(title="Step Validator Agent")

# --- Logging ---
logger = logging.getLogger("StepValidator")
logger.setLevel(logging.INFO)
if not logger.handlers:
    fh = logging.FileHandler("validation_traffic.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(fh)

# --- AI Clients ---
google_key = os.environ.get("GOOGLE_API_KEY")
if google_key:
    genai.configure(api_key=google_key)

openai_client = None
openai_key = os.environ.get("OPENAI_API_KEY")
if openai_key:
    openai_client = OpenAI(api_key=openai_key)

SYSTEM_PROMPT = """You are an independent Quality Assurance Validator for a multi-agent workflow system.
You execute structured test cases against an agent's output and produce a formal test report.

You will receive:
1. The TASK the agent was asked to perform (step name + description)
2. The INPUT DATA the agent received
3. The OUTPUT DATA the agent produced
4. The FULL CONTENTS of any input and output files (when provided)
5. Any specific VALIDATION CRITERIA

CRITICAL: You will receive the COMPLETE contents of input files and output files when available.
Use these as your primary evidence for validation:
- Cross-reference every item in the output against the original input file contents
- Verify that all items from the source are represented in the output (completeness)
- Verify that extracted values match the source text exactly (correctness)
- Count items in source vs output to check nothing was missed or duplicated

IMPORTANT: If source_content starts with "[VISUAL PDF]", the file was processed visually by a
multimodal AI model (e.g. Gemini analysing PDF images directly). In this case, raw text extraction
was not possible. You should NOT flag this as an error. Instead, validate:
- Output structure and format are correct
- Internal consistency (no duplicate entries, no empty fields, logical values)
- Values appear reasonable for the described task
- Set confidence to "Medium" since you cannot cross-reference against raw source text

YOUR OUTPUT FORMAT:

You must generate explicit, numbered test cases and execute each one. Each test case must document
its full execution trace: what input data was examined, what output was expected, what was actually
found, and how the validation was performed.

Respond with ONLY valid JSON:

{
  "test_run": {
    "overall_result": "PASS" or "FAIL",
    "confidence": "High" or "Medium" or "Low",
    "summary": "Brief 1-2 sentence overall assessment",
    "total_tests": <number>,
    "passed": <number>,
    "failed": <number>
  },
  "test_cases": [
    {
      "test_id": "TC-001",
      "test_name": "Short descriptive name for this test",
      "category": "COMPLETENESS" or "CORRECTNESS" or "FORMAT" or "CONSISTENCY" or "DATA_INTEGRITY",
      "description": "What this test verifies and why it matters",
      "input_data": "The specific input data examined for this test (e.g. the source items, field values, or structure inspected). Quote actual data.",
      "expected_output": "What the correct/expected result should be, derived from the input data. Be specific with values.",
      "actual_output": "What was actually found in the agent's output. Quote the actual values found.",
      "result": "PASS" or "FAIL",
      "execution_notes": "Step-by-step description of how validation was performed: what was looked up, what was compared, what logic was applied",
      "reasoning": "Final verdict explanation - why this test passed or failed based on the comparison between expected and actual"
    }
  ],
  "issues": ["list of problems found, empty if none"],
  "recommendations": ["suggestions for improvement, empty if none"]
}

TEST CASE GUIDELINES:
- Generate test cases appropriate to the step type and data available
- Always include these categories when applicable:
  * FORMAT: Output structure matches expected schema (correct keys, types, nesting)
  * COMPLETENESS: All items from source are present in output (count source items vs output items)
  * CORRECTNESS: Specific values match between source and output (spot-check individual items)
  * CONSISTENCY: Internal consistency (no duplicates, no nulls where values expected, types match)
  * DATA_INTEGRITY: Cross-reference specific data points between input and output
- EVERY test case MUST include:
  * input_data: Quote the actual source data examined (e.g. "Input file contains asset_number 'SUB 95988' with asset_type 'New Padmount Substation'")
  * expected_output: State what the output should contain based on the input (e.g. "Output should include an entry with asset_number='SUB 95988' and asset_type='New Padmount Substation'")
  * actual_output: Quote what was actually found (e.g. "Output contains asset_number='SUB 95988' with asset_type='New Padmount Substation'")
  * execution_notes: Describe the validation steps (e.g. "1. Located 'SUB 95988' in input file at index 0. 2. Searched output assets array for matching asset_number. 3. Found match at index 0. 4. Compared asset_type values.")
- For COMPLETENESS tests: count items in both input and output, list any missing
- For CORRECTNESS tests: pick specific items and compare field-by-field
- For DATA_INTEGRITY tests: trace a data point from input through to output
- Generate at minimum 5 test cases per validation, more for complex steps

Be rigorous but fair. Flag real issues, not stylistic preferences.

EXTRACTION-SPECIFIC TEST DIRECTIVES (apply when step_name contains "extract" or input contains "extracted_sections"):

When validating a document extraction step, you have access to two data sources:
- `source_pdf_raw_text`: the full raw page-by-page text from the original PDFs (in input_data)
- `step_extraction`: the structured extraction result produced by the agent (in output_data)
- `extracted_sections`: the list of tagged sections with relevance scores (in output_data)

Execute ALL of the following mandatory test cases in addition to the standard ones:

TC-ORDERING: Section order integrity
  Verify that the sections in `extracted_sections` appear in the same page order as the source PDF.
  Check `page_number` values are non-decreasing. Flag any section that appears out of order.

TC-COVERAGE: Heading coverage
  From `source_pdf_raw_text`, identify ALL section headings (numbered like "1.2 Title" or ALL-CAPS lines).
  Compare against `extracted_sections[*].section_name`. Flag any heading present in the source that is missing from extracted_sections.
  Count: source headings vs extracted section names.

TC-CONTENT-INTEGRITY: Line-by-line text fidelity
  For at least 3 sections with relevance_score >= 0.5, take the first 200 characters of `extracted_sections[n].text` and verify that exact text appears in the corresponding page of `source_pdf_raw_text`.
  Flag any text that does not match the source (possible corruption, hallucination, or truncation).

TC-HV: High-voltage assets extraction
  Search `source_pdf_raw_text` for keywords: "HV", "high voltage", "11kV", "22kV", "33kV", "66kV", "132kV", "transmission".
  Verify that `step_extraction` contains a corresponding HV category/key with items for each HV asset found.
  Count source HV references vs extracted HV items.

TC-LV: Low-voltage assets extraction
  Search `source_pdf_raw_text` for keywords: "LV", "low voltage", "415V", "400V", "240V", "distribution".
  Verify that `step_extraction` contains a corresponding LV category/key with matching items.

TC-SL: Street lighting extraction
  Search `source_pdf_raw_text` for keywords: "street light", "SL", "public lighting", "lighting column", "luminaire".
  Verify that `step_extraction` contains a corresponding street lighting category with matching items.

TC-SUBSTATION: Substation extraction
  Search `source_pdf_raw_text` for keywords: "substation", "zone substation", "padmount", "transformer", "kVA", "MVA".
  Verify that `step_extraction` contains a substation/transformer category with matching items.
  Check that kVA/MVA ratings and transformer counts match the source.

TC-FUNDING: Funding and cost extraction
  Search `source_pdf_raw_text` for keywords: "$", "cost", "funding", "budget", "contribution", "payment", "capital".
  Verify that `step_extraction` contains a funding/cost category with all dollar values found in source.
  Check that specific dollar amounts match exactly.

TC-NO-HALLUCINATION: Hallucination check
  Pick 5 items from `step_extraction` (any category). For each item's `description` field, verify that the key facts (asset names, numbers, voltages, distances, costs) appear verbatim in `source_pdf_raw_text`.
  Flag any item where the extracted values cannot be found in the source text.

TC-MISSING-CONTENT: Missing content detection
  For every section in `extracted_sections` with relevance_score >= 0.6, verify that the section's content is represented in `step_extraction`.
  Flag relevant sections that have no corresponding items in the structured extraction.

TC-TABLE-EXTRACTION: Table data completeness
  Count tables in `extracted_sections[*].tables`. For each table, verify that the table data appears in `step_extraction` (rows/values extracted, not just the section text).
  Flag tables present in source that have no corresponding extracted items."""


GEMINI_MODEL   = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
VALIDATOR_MODEL = os.environ.get("VALIDATOR_MODEL", "gpt-4o")


def _call_gemini(prompt: str) -> str:
    model = genai.GenerativeModel(GEMINI_MODEL)
    full_prompt = f"SYSTEM_INSTRUCTION:\n{SYSTEM_PROMPT}\n\nUSER_REQUEST:\n{prompt}"
    resp = model.generate_content(full_prompt)
    return resp.text


def _call_openai(prompt: str) -> str:
    resp = openai_client.chat.completions.create(
        model=VALIDATOR_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
    )
    return resp.choices[0].message.content


def _generate(prompt: str) -> tuple:
    """Try configured OpenAI model first, fall back to Gemini. Returns (response_text, model_used)."""
    last_error = None
    if openai_client:
        try:
            return _call_openai(prompt), VALIDATOR_MODEL
        except Exception as e:
            last_error = e
            print(f"[VALIDATOR] {VALIDATOR_MODEL} failed: {e}, falling back to Gemini...")

    if google_key:
        try:
            return _call_gemini(prompt), GEMINI_MODEL
        except Exception as e:
            last_error = e
            print(f"[VALIDATOR] Gemini also failed: {e}")

    raise RuntimeError(f"No AI provider available. Last error: {last_error}")


def _truncate(data: str, max_chars: int = 80000) -> str:
    """Truncate large data to stay within LLM context limits."""
    if len(data) <= max_chars:
        return data
    return data[:max_chars] + f"\n... [TRUNCATED - {len(data)} total chars]"


def _read_file_content(file_bytes: bytes, filename: str) -> Optional[str]:
    """Read file bytes and return text content. Returns None for binary files."""
    ext = filename.split('.')[-1].lower()

    # Binary files we can't meaningfully include as text
    if ext in ('png', 'jpg', 'jpeg', 'gif', 'bmp', 'tiff', 'webp'):
        return f"[BINARY IMAGE FILE: {filename} ({len(file_bytes)} bytes)]"

    if ext == 'pdf':
        return f"[PDF FILE: {filename} ({len(file_bytes)} bytes) - binary content, use extracted text from agent]"

    # Text-based files
    try:
        if ext == 'json':
            data = json.loads(file_bytes.decode('utf-8'))
            return json.dumps(data, indent=2)
        elif ext in ('csv', 'txt', 'log', 'md'):
            return file_bytes.decode('utf-8')
        elif ext in ('xlsx', 'xls'):
            return f"[EXCEL FILE: {filename} ({len(file_bytes)} bytes) - use extracted text from agent]"
        elif ext in ('doc', 'docx'):
            return f"[WORD FILE: {filename} ({len(file_bytes)} bytes) - use extracted text from agent]"
        else:
            # Try as text
            return file_bytes.decode('utf-8')
    except Exception as e:
        return f"[UNREADABLE FILE: {filename} - {str(e)}]"


def _read_file_from_path(file_path: str) -> Optional[str]:
    """Read a file from disk by path (for output files saved by agents)."""
    if not file_path or not os.path.exists(file_path):
        return None
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        print(f"[VALIDATOR] Failed to read file {file_path}: {e}")
        return None


@app.post("/validate_step")
async def validate_step(
    step_name: str = Form(...),
    step_description: str = Form(""),
    input_data_json: str = Form("{}"),
    output_data_json: str = Form("{}"),
    validation_criteria: str = Form(""),
    output_file_path: str = Form(""),
    agent_files_json: str = Form("{}"),
    input_file: UploadFile = File(default=None),
    output_file: UploadFile = File(default=None),
):
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"[VALIDATOR] Validating step: {step_name}")

    # --- Parse agent files metadata ---
    try:
        agent_files = json.loads(agent_files_json) if agent_files_json else {}
    except json.JSONDecodeError:
        agent_files = {}

    # --- Read uploaded files ---
    input_file_content = None
    output_file_content = None
    input_file_name = None
    output_file_name = None

    if input_file:
        input_bytes = await input_file.read()
        input_file_content = _read_file_content(input_bytes, input_file.filename)
        input_file_name = input_file.filename
        print(f"[VALIDATOR] Received input file: {input_file.filename} ({len(input_bytes)} bytes)")

    if output_file:
        output_bytes = await output_file.read()
        output_file_content = _read_file_content(output_bytes, output_file.filename)
        output_file_name = output_file.filename
        print(f"[VALIDATOR] Received output file: {output_file.filename} ({len(output_bytes)} bytes)")

    # --- Read output file from disk path (fallback if not uploaded) ---
    if not output_file_content and output_file_path:
        disk_content = _read_file_from_path(output_file_path)
        if disk_content:
            output_file_content = disk_content
            output_file_name = os.path.basename(output_file_path)
            print(f"[VALIDATOR] Read output file from disk: {output_file_path} ({len(disk_content)} chars)")

    # --- Build the validation prompt ---
    prompt_parts = [
        f"## STEP NAME\n{step_name}",
        f"\n## STEP DESCRIPTION\n{step_description}",
    ]

    # Include full input file content if available
    if input_file_content:
        prompt_parts.append(
            f"\n## ORIGINAL INPUT FILE ({input_file_name or 'unknown'})\n"
            f"{_truncate(input_file_content)}"
        )

    # Include input data JSON (agent's processed view of the input)
    prompt_parts.append(f"\n## INPUT DATA (metadata/context sent to agent)\n{_truncate(input_data_json)}")

    # Include full output file content if available
    if output_file_content:
        prompt_parts.append(
            f"\n## FULL OUTPUT FILE (saved by agent)\n"
            f"{_truncate(output_file_content)}"
        )

    # Include output data JSON (what the orchestrator captured)
    prompt_parts.append(f"\n## OUTPUT DATA (agent's returned response)\n{_truncate(output_data_json)}")

    if validation_criteria:
        prompt_parts.append(f"\n## VALIDATION CRITERIA\n{validation_criteria}")

    prompt_parts.append(
        "\n## YOUR TASK\n"
        "Generate and execute structured test cases against the agent's output. "
        "Use the FULL FILE CONTENTS (when provided) as your primary evidence. "
        "Cross-reference every item between input and output files. "
        "For each test case, document: the input data examined, the expected output, "
        "the actual output found, step-by-step execution notes of how you validated, "
        "and your final reasoning. "
        "Respond with the JSON test report."
    )

    prompt = "\n".join(prompt_parts)

    # Log request header
    logger.info(
        f"\n{'='*60} VALIDATION REQUEST {'='*60}\n"
        f"Step: {step_name}\n"
        f"Description: {step_description}\n"
        f"Input file: {input_file_name or 'None'}\n"
        f"Output file: {output_file_name or output_file_path or 'None'}\n"
        f"Validation criteria: {validation_criteria or 'None'}\n"
        f"Prompt length: {len(prompt)} chars\n"
        f"{'='*140}\n"
    )

    # Log full prompt
    logger.info(
        f"\n{'='*60} FULL PROMPT {'='*60}\n"
        f"{prompt}\n"
        f"{'='*140}\n"
    )

    # Call LLM
    try:
        raw_response, model_used = _generate(prompt)
    except Exception as llm_err:
        print(f"[VALIDATOR] LLM unavailable: {llm_err}")
        return {
            "status": "error",
            "validation": {
                "is_valid": False,
                "confidence": "Low",
                "score": 0,
                "summary": f"Validator LLM unavailable: {llm_err}",
                "step_name": step_name,
                "test_run_summary": {"overall_result": "FAIL", "confidence": "Low", "score": 0,
                                     "summary": str(llm_err), "total_tests": 0, "passed": 0, "failed": 0},
                "test_cases": [],
                "issues": [str(llm_err)],
                "recommendations": [],
                "agent_files": agent_files or {},
                "files": {"files_read": [], "files_output": []},
                "files_ingested": {},
            },
            "output_file": None,
        }

    # Log raw response
    logger.info(
        f"\n{'='*60} RAW LLM RESPONSE (Model: {model_used}) {'='*60}\n"
        f"{raw_response}\n"
        f"{'='*140}\n"
    )

    # Parse response
    clean = raw_response.replace("```json", "").replace("```", "").strip()
    try:
        validation = json.loads(clean)
    except json.JSONDecodeError:
        validation = {
            "test_run": {
                "overall_result": "FAIL",
                "confidence": "Low",
                "summary": "Validator could not parse its own response",
                "total_tests": 0,
                "passed": 0,
                "failed": 0,
            },
            "test_cases": [],
            "issues": [f"Raw response: {clean[:500]}"],
            "recommendations": [],
        }

    # --- Build the full test report ---
    test_run = validation.get("test_run", {})
    test_cases = validation.get("test_cases", [])

    # Compute pass/fail counts from actual test cases
    total_tests = test_run.get("total_tests", len(test_cases))
    passed_tests = test_run.get("passed", sum(1 for tc in test_cases if tc.get("result") == "PASS"))
    failed_tests = test_run.get("failed", sum(1 for tc in test_cases if tc.get("result") == "FAIL"))
    score_pct = round((passed_tests / total_tests) * 100) if total_tests > 0 else 0

    # --- Build files inventory ---
    files_read = []
    if input_file_name:
        file_entry = {"filename": input_file_name, "role": "input", "description": "Original input file sent to the agent"}
        if input_file_content:
            file_entry["size_chars"] = len(input_file_content)
            file_entry["ingested"] = True
        else:
            file_entry["ingested"] = False
        files_read.append(file_entry)

    if output_file_name or output_file_path:
        fname = output_file_name or os.path.basename(output_file_path)
        file_entry = {"filename": fname, "role": "output", "description": "Output file produced by the agent"}
        if output_file_content:
            file_entry["size_chars"] = len(output_file_content)
            file_entry["ingested"] = True
        else:
            file_entry["ingested"] = False
        if output_file_path:
            file_entry["path"] = output_file_path
        files_read.append(file_entry)

    files_output = []
    # The test report itself is an output file (path filled in after save)
    # Additional agent output files are tracked in files_read with role="output"

    test_report = {
        "report_metadata": {
            "report_type": "Step Validation Test Report",
            "step_name": step_name,
            "step_description": step_description,
            "timestamp": run_timestamp,
            "model_used": model_used,
            "validation_criteria": validation_criteria or None,
        },
        "agent_files": agent_files if agent_files else {},
        "files": {
            "files_read": files_read,
            "files_output": files_output,  # Updated after save
        },
        "execution_context": {
            "prompt_length_chars": len(prompt),
            "response_length_chars": len(raw_response),
            "prompt": prompt,
            "raw_response": raw_response,
        },
        "test_run_summary": {
            "overall_result": test_run.get("overall_result", "FAIL"),
            "confidence": test_run.get("confidence", "Low"),
            "score": score_pct,
            "summary": test_run.get("summary", ""),
            "total_tests": total_tests,
            "passed": passed_tests,
            "failed": failed_tests,
        },
        "test_cases": test_cases,
        "issues": validation.get("issues", []),
        "recommendations": validation.get("recommendations", []),
    }

    # --- Save test report ---
    output_dir = "OUTPUT"
    os.makedirs(output_dir, exist_ok=True)
    safe_name = step_name.replace(" ", "_")[:30]
    report_file = os.path.join(output_dir, f"TestReport_{safe_name}_{run_timestamp}.json")

    # Update files_output with the test report itself
    test_report["files"]["files_output"] = [
        {"filename": os.path.basename(report_file), "path": report_file, "role": "test_report", "description": "Validation test report generated by step validator"}
    ]

    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(test_report, f, indent=2, ensure_ascii=False)
    print(f"[VALIDATOR] Test report saved: {report_file}")

    # --- Log each test case explicitly ---
    logger.info(
        f"\n{'='*60} TEST RESULTS: {step_name} {'='*60}\n"
        f"Overall: {test_report['test_run_summary']['overall_result']} | "
        f"{passed_tests}/{total_tests} passed ({score_pct}%) | "
        f"Model: {model_used}\n"
        f"Files read: {', '.join(f['filename'] + (' [ingested]' if f.get('ingested') else ' [not ingested]') for f in files_read) or 'None'}\n"
        f"Files output: {report_file}\n"
        f"{'-'*140}"
    )

    for tc in test_cases:
        tc_result = tc.get("result", "?")
        tc_marker = "PASS" if tc_result == "PASS" else "FAIL"
        logger.info(
            f"\n--- [{tc_marker}] {tc.get('test_id', '?')}: {tc.get('test_name', '?')} ---\n"
            f"  Category:        {tc.get('category', '?')}\n"
            f"  Description:     {tc.get('description', 'N/A')}\n"
            f"  Input Data:      {tc.get('input_data', 'N/A')}\n"
            f"  Expected Output: {tc.get('expected_output', tc.get('expected', 'N/A'))}\n"
            f"  Actual Output:   {tc.get('actual_output', tc.get('actual', 'N/A'))}\n"
            f"  Result:          {tc_result}\n"
            f"  Execution Notes: {tc.get('execution_notes', 'N/A')}\n"
            f"  Reasoning:       {tc.get('reasoning', 'N/A')}\n"
            f"  {'-'*60}"
        )

    logger.info(f"\n{'='*140}\n")

    # --- Print summary to console ---
    overall = test_report["test_run_summary"]["overall_result"]
    print(f"[VALIDATOR] {'PASS' if overall == 'PASS' else 'FAIL'} | {step_name} | {passed_tests}/{total_tests} tests passed ({score_pct}%)")
    if agent_files:
        agent_read = [f.get("filename", "?") for f in agent_files.get("files_read", [])]
        agent_out = [f.get("filename", "?") for f in agent_files.get("files_output", [])]
        if agent_read:
            print(f"[VALIDATOR] Agent files read: {', '.join(agent_read)}")
        if agent_out:
            print(f"[VALIDATOR] Agent files output: {', '.join(agent_out)}")
    if files_read:
        print(f"[VALIDATOR] Validator files read: {', '.join(f['filename'] for f in files_read)}")
    print(f"[VALIDATOR] Validator files output: {report_file}")
    for tc in test_cases:
        status = "PASS" if tc.get("result") == "PASS" else "FAIL"
        print(f"  [{status}] {tc.get('test_id', '?')}: {tc.get('test_name', '?')}")
        if tc.get("result") == "FAIL":
            print(f"         Expected: {tc.get('expected_output', tc.get('expected', ''))[:120]}")
            print(f"         Actual:   {tc.get('actual_output', tc.get('actual', ''))[:120]}")

    # --- Build backward-compatible validation response for orchestrator ---
    validation_response = {
        "is_valid": overall == "PASS",
        "confidence": test_report["test_run_summary"]["confidence"],
        "score": score_pct,
        "summary": test_report["test_run_summary"]["summary"],
        "step_name": step_name,
        "agent_files": test_report["agent_files"],
        "files": test_report["files"],
        "files_ingested": {
            "input_file": input_file_name,
            "output_file": output_file_name or output_file_path or None,
        },
        "test_run_summary": test_report["test_run_summary"],
        "test_cases": test_cases,
        "issues": test_report["issues"],
        "recommendations": test_report["recommendations"],
        "test_report_file": report_file,
    }

    return {
        "status": "success",
        "validation": validation_response,
        "output_file": report_file,
    }


@app.get("/health")
async def health():
    return {"status": "ok", "agent": "step-validator"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("step_validator_agent:app", host="0.0.0.0", port=8088, reload=True)
