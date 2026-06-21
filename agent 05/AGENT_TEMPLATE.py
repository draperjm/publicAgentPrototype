"""
Specialist Agent Template
=========================
Copy this file, rename it (e.g. my_agent.py), and implement the
endpoint logic in the marked sections.

Every new agent should follow this structure to stay consistent with
the common framework. The boilerplate (app setup, health, output, LLM)
is handled by the common package — your agent only implements logic.

Checklist when creating a new agent:
  [ ] Copy this file and rename it
  [ ] Define request/response Pydantic models
  [ ] Implement the endpoint function(s)
  [ ] Add a Dockerfile.<agent_name>
  [ ] Add the service to docker-compose.yml
  [ ] Register the agent in registry.json
  [ ] Add a task template to tasks.json if needed
"""

import logging
import os
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import HTTPException
from pydantic import BaseModel

from common.agent import create_app
from common.config import settings
from common.llm import LLMClient
from common.models import AgentError, FilesManifest
from common.output import OutputManager

# ── 1. Logging setup ────────────────────────────────────────────────────────────
logger = logging.getLogger("my_agent")
logging.basicConfig(level=logging.INFO)

# ── 2. App + dependencies ───────────────────────────────────────────────────────
#    Pass the primary model so /health reports it.
app = create_app(title="My Specialist Agent", model=settings.llm_model)

llm = LLMClient()


# ── 3. Request / Response models ────────────────────────────────────────────────
#    Define input and output schemas with Pydantic.
#    Every response should include "output_file" and "files" for the orchestrator.

class MyRequest(BaseModel):
    # ── Required inputs ──────────────────────────────────────────────────────────
    input_text: str
    # ── Optional orchestrator-injected fields ─────────────────────────────────
    output_dir: Optional[str] = None      # Injected by orchestrator for job isolation
    job_id: Optional[str] = None          # Correlation ID for logging


# ── 4. Core logic ────────────────────────────────────────────────────────────────
#    Implement as plain functions (easier to test independently of FastAPI).

def _do_work(input_text: str) -> Dict[str, Any]:
    """
    Implement the agent's core logic here.
    Returns a dict that will be saved as the output JSON artefact.
    """
    # Example: call LLM with the configured primary model
    result = llm.json(
        prompt=(
            f"Process the following input and return a JSON object with "
            f"keys 'summary' and 'items':\n\n{input_text}"
        ),
        model=settings.llm_model,
    )
    return result


# ── 5. Endpoint ──────────────────────────────────────────────────────────────────

@app.post("/process")
def process(request: MyRequest) -> dict:
    """
    Main endpoint. Called by the orchestrator.

    Response contract (must include for orchestrator compatibility):
      - output_file: str — path to primary output artefact
      - files: dict      — FilesManifest (files_read + files_output)
      - Any domain-specific fields
    """
    logger.info(f"[MyAgent] Processing request (job_id={request.job_id})")

    # Initialise output manager — job_dir keeps outputs isolated per execution
    out = OutputManager(job_dir=request.output_dir)

    # ── Do work ─────────────────────────────────────────────────────────────────
    try:
        result = _do_work(request.input_text)
    except Exception as e:
        logger.error(f"[MyAgent] Processing failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    # ── Save artefact ────────────────────────────────────────────────────────────
    output_path = out.write(
        data=result,
        prefix="MyAgentOutput",
        role="agent_output",
        description="Primary output produced by My Specialist Agent",
    )
    logger.info(f"[MyAgent] Output saved: {output_path}")

    # ── Return standardised response ─────────────────────────────────────────────
    return {
        **result,
        "output_file": str(output_path),
        "files": out.manifest().to_dict(),
    }


# ── 6. Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", "8099"))
    uvicorn.run("AGENT_TEMPLATE:app", host="0.0.0.0", port=port, reload=True)
