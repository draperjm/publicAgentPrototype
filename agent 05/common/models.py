"""
Shared Pydantic models used across multiple agents.

Standardising these ensures consistent response shapes at every
agent boundary and simplifies validation in the orchestrator.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ── File tracking ───────────────────────────────────────────────────────────────

class FileEntry(BaseModel):
    """A single file that was read or written by an agent."""
    filename: str
    path: str
    role: str = "output"            # "input" | "output" | "test_report" | etc.
    description: str = ""
    size_chars: Optional[int] = None
    ingested: Optional[bool] = None


class FilesManifest(BaseModel):
    """
    Standardised file provenance block returned by every agent endpoint.
    Allows the orchestrator and validator to locate and ingest agent artefacts
    without parsing the full response payload.
    """
    files_read: List[FileEntry] = Field(default_factory=list)
    files_output: List[FileEntry] = Field(default_factory=list)

    def add_read(self, filename: str, path: str, **kwargs) -> None:
        self.files_read.append(FileEntry(filename=filename, path=path, role="input", **kwargs))

    def add_output(self, filename: str, path: str, role: str = "output", **kwargs) -> None:
        self.files_output.append(FileEntry(filename=filename, path=path, role=role, **kwargs))

    def to_dict(self) -> dict:
        return {
            "files_read":   [f.model_dump(exclude_none=True) for f in self.files_read],
            "files_output": [f.model_dump(exclude_none=True) for f in self.files_output],
        }


# ── Standard agent responses ────────────────────────────────────────────────────

class AgentHealth(BaseModel):
    status: str = "ok"
    agent: str
    model: Optional[str] = None
    version: str = "1.0"


class AgentError(BaseModel):
    """Returned when an agent endpoint raises an unhandled exception."""
    error: str
    detail: Optional[str] = None
    step: Optional[str] = None
    agent: Optional[str] = None


# ── Orchestrator / plan models ──────────────────────────────────────────────────

class StepResources(BaseModel):
    agent_id: Optional[str] = None
    tool_id: Optional[str] = None
    knowledge_id: Optional[str] = None


class StepValidation(BaseModel):
    criteria: Optional[str] = None
    critical_fail: Optional[bool] = False
    retry_on_failure: Optional[bool] = True
    max_retries: Optional[int] = 3


class PlanStep(BaseModel):
    step_number: int
    name: Optional[str] = None
    description: str
    assigned_resource_id: Optional[str] = None
    required_resources: Optional[StepResources] = None
    validation: Optional[StepValidation] = None
    parallel_group: Optional[str] = None


class ExecutionRequest(BaseModel):
    plan_overview: str
    steps: List[PlanStep]


# ── Validation / test report models ────────────────────────────────────────────

class TestCase(BaseModel):
    test_id: str
    test_name: str
    category: str       # COMPLETENESS | CORRECTNESS | FORMAT | CONSISTENCY | DATA_INTEGRITY
    description: str
    input_data: str
    expected_output: str
    actual_output: str
    result: str         # PASS | FAIL
    execution_notes: str = ""
    reasoning: str = ""


class TestRunSummary(BaseModel):
    overall_result: str     # PASS | FAIL
    confidence: str         # High | Medium | Low
    score: int = 0          # 0–100
    summary: str = ""
    total_tests: int = 0
    passed: int = 0
    failed: int = 0


class ValidationReport(BaseModel):
    is_valid: bool
    confidence: str
    score: int
    summary: str
    step_name: str
    test_run_summary: TestRunSummary
    test_cases: List[TestCase] = Field(default_factory=list)
    issues: List[str] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)
    files: Optional[Dict[str, Any]] = None
