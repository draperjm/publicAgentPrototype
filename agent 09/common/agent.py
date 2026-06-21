"""
FastAPI application factory and common middleware.

Every specialist agent calls `create_app()` to get a pre-configured
FastAPI instance with:
  - CORS middleware (permissive for internal service mesh)
  - A /health endpoint returning the standard AgentHealth schema
  - Optional request/response logging middleware

Usage:
    from common.agent import create_app
    from common.config import settings

    app = create_app(title="My Specialist Agent", model=settings.llm_model)

    @app.post("/process")
    def process(request: MyRequest): ...
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Optional

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from common.models import AgentHealth

logger = logging.getLogger(__name__)


def create_app(
    title: str,
    version: str = "1.0",
    model: Optional[str] = None,
    enable_request_logging: bool = True,
) -> FastAPI:
    """
    Create a FastAPI app with standard pipeline configuration.

    Parameters
    ----------
    title:
        Human-readable agent name (returned in /health responses).
    version:
        Semantic version string.
    model:
        Primary LLM model this agent uses (reported in /health).
    enable_request_logging:
        If True, logs method, path, duration, and status for every request.
    """
    app = FastAPI(title=title, version=version)

    # ── CORS ────────────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Request logging middleware ───────────────────────────────────────────────
    if enable_request_logging:
        @app.middleware("http")
        async def _log_requests(request: Request, call_next) -> Response:
            request_id = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
            start = time.perf_counter()
            response = await call_next(request)
            duration_ms = round((time.perf_counter() - start) * 1000)
            logger.info(
                f"[{title}] {request.method} {request.url.path} "
                f"→ {response.status_code} ({duration_ms}ms) req={request_id}"
            )
            return response

    # ── Health endpoint ─────────────────────────────────────────────────────────
    health_payload = AgentHealth(agent=title, version=version, model=model)

    @app.get("/health", response_model=AgentHealth, tags=["meta"])
    def health() -> AgentHealth:
        """Liveness probe. Returns agent name, version, and primary model."""
        return health_payload

    return app
