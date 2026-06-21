"""
Centralised configuration loaded from environment variables.

All agents import `settings` rather than calling os.getenv() directly,
so the full config surface is visible in one place.
"""
import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    # ── LLM models ─────────────────────────────────────────────────────────────
    llm_model: str                  = field(default_factory=lambda: os.getenv("LLM_MODEL",              "gpt-4o"))
    tagging_model: str              = field(default_factory=lambda: os.getenv("TAGGING_MODEL",          "gpt-4o-mini"))
    vision_model: str               = field(default_factory=lambda: os.getenv("VISION_MODEL",           "gemini-2.0-flash"))
    asset_extraction_model: str     = field(default_factory=lambda: os.getenv("ASSET_EXTRACTION_MODEL", "claude-sonnet-4-6"))
    validator_model: str            = field(default_factory=lambda: os.getenv("VALIDATOR_MODEL",        "gpt-4o"))

    # ── API keys ────────────────────────────────────────────────────────────────
    openai_api_key: str             = field(default_factory=lambda: os.getenv("OPENAI_API_KEY",    ""))
    anthropic_api_key: str          = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    google_api_key: str             = field(default_factory=lambda: os.getenv("GOOGLE_API_KEY",    ""))

    # ── Service URLs ────────────────────────────────────────────────────────────
    orchestrator_url: str           = field(default_factory=lambda: os.getenv("ORCHESTRATOR_URL",   "http://orchestrator:8001/execute"))
    validator_url: str              = field(default_factory=lambda: os.getenv("VALIDATOR_URL",      "http://step-validator:8088/validate_step"))
    chunker_url: str                = field(default_factory=lambda: os.getenv("CHUNKER_URL",        "http://document-chunker:8091/chunk"))
    reviewer_url: str               = field(default_factory=lambda: os.getenv("REVIEWER_URL",       "http://document-reviewer:8089"))
    extractor_url: str              = field(default_factory=lambda: os.getenv("EXTRACTOR_URL",      "http://document-extractor:8090"))

    # ── Storage ─────────────────────────────────────────────────────────────────
    output_dir: str                 = field(default_factory=lambda: os.getenv("OUTPUT_DIR",         "/app/OUTPUT"))
    documents_dir: str              = field(default_factory=lambda: os.getenv("DOCUMENTS_DIR",      "/documents"))
    chunk_output_dir: str           = field(default_factory=lambda: os.getenv("CHUNK_OUTPUT_DIR",   "/output/chunks"))

    # ── Behaviour ───────────────────────────────────────────────────────────────
    llm_retries: int                = field(default_factory=lambda: int(os.getenv("LLM_RETRIES", "3")))
    llm_retry_delays: tuple         = (2, 5, 10)
    max_content_chars: int          = field(default_factory=lambda: int(os.getenv("MAX_CONTENT_CHARS", "4000")))
    max_prompt_chars: int           = field(default_factory=lambda: int(os.getenv("MAX_PROMPT_CHARS", "80000")))

    def provider_for(self, model: str) -> str:
        """Infer provider from model name."""
        if "gemini" in model:
            return "google"
        if "claude" in model:
            return "anthropic"
        return "openai"


# Module-level singleton — import this directly
settings = Settings()
