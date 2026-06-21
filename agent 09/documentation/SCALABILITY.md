# Scalability Improvements — Agent 05

This document identifies architectural limitations in the current design and proposes concrete improvements ordered by impact and implementation effort.

---

## Current Limitations

| # | Limitation | Where | Impact |
|---|---|---|---|
| 1 | In-memory execution state | `orchestrator.py` | Single instance, no crash recovery |
| 2 | Synchronous HTTP step dispatch | `orchestrator.py` | Blocks under load; no queue-based retry |
| 3 | Frontend polling for status | `frontend/index.html` | Inefficient; misses events if page refreshes |
| 4 | Hardcoded local filesystem output | All agents | Breaks on multi-node deployment |
| 5 | `print()` / basic logging | All agents | No correlation IDs; unstructured; hard to trace |
| 6 | No LLM cost or token tracking | All agents | Unbounded API spend per execution |
| 7 | No circuit breaker on LLM calls | All agents | Provider outage cascades into full pipeline failure |
| 8 | No agent pre-flight health check | `orchestrator.py` | Steps dispatched to unavailable agents; silent failures |
| 9 | Sequential chunk processing | `document_extractor.py` | Quadrant-split A1 drawings process tiles one at a time |
| 10 | Static registry.json | Registry | Adding/removing agents requires file edits and restart |

---

## Improvement 1 — Persistent Execution State

**Problem:** `executions: Dict[str, dict] = {}` lives in the orchestrator process. A restart or crash loses all in-flight work. Horizontal scaling is impossible because different instances have different state.

**Proposed solution:** Replace the in-memory dict with a Redis hash keyed by `execution_id`. State transitions become atomic operations.

```python
# common/state.py
import json, os
import redis

_r = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379"))
TTL = 60 * 60 * 24  # 24 hours

def get_execution(exec_id: str) -> dict | None:
    raw = _r.get(f"exec:{exec_id}")
    return json.loads(raw) if raw else None

def save_execution(exec_id: str, data: dict) -> None:
    _r.setex(f"exec:{exec_id}", TTL, json.dumps(data))

def update_step(exec_id: str, step_number: int, step_data: dict) -> None:
    exec_data = get_execution(exec_id) or {}
    exec_data.setdefault("steps", {})[str(step_number)] = step_data
    save_execution(exec_id, exec_data)
```

**docker-compose addition:**
```yaml
redis:
  image: redis:7-alpine
  ports: ["6379:6379"]
  networks: [agent-network]
```

**Impact:** Enables crash recovery, horizontal orchestrator scaling, and execution history queries.

---

## Improvement 2 — Async Task Queue for Step Dispatch

**Problem:** The orchestrator calls each agent synchronously. If a step takes 60 seconds, the orchestrator HTTP thread blocks for 60 seconds. Under concurrent users this exhausts the thread pool.

**Proposed solution:** Introduce a Celery task queue (backed by Redis). The orchestrator enqueues steps rather than calling agents directly.

```
User → Responder → Orchestrator → Redis Queue → Celery Worker → Agent HTTP call
                        ↑                               ↓
                    Status poll ←──────────── Result written to Redis state
```

**Orchestrator becomes non-blocking:**
```python
from celery import Celery

celery_app = Celery("pipeline", broker=os.getenv("REDIS_URL"))

@celery_app.task(bind=True, max_retries=3, default_retry_delay=5)
def execute_step(self, execution_id: str, step: dict):
    try:
        result = requests.post(step["endpoint"], json=step["payload"], timeout=120)
        update_step(execution_id, step["step_number"], result.json())
    except Exception as exc:
        raise self.retry(exc=exc)
```

**Impact:** Non-blocking orchestrator, automatic retry at queue level, ability to scale workers independently.

---

## Improvement 3 — Server-Sent Events for Real-Time UI Updates

**Problem:** The frontend polls `/status/{execution_id}` on a timer. This wastes requests, introduces latency, and misses events if the page is refreshed.

**Proposed solution:** Add an SSE endpoint to the orchestrator. The frontend subscribes once and receives step events as they complete.

```python
# orchestrator.py addition
from fastapi.responses import StreamingResponse
import asyncio

@app.get("/stream/{execution_id}")
async def stream_execution(execution_id: str):
    async def event_generator():
        last_seen = -1
        while True:
            execution = get_execution(execution_id)  # from Redis
            if not execution:
                yield "data: {\"error\": \"not found\"}\n\n"
                return
            steps = execution.get("steps", {})
            for step_num, step_data in steps.items():
                if int(step_num) > last_seen:
                    yield f"data: {json.dumps(step_data)}\n\n"
                    last_seen = int(step_num)
            if execution.get("status") in ("complete", "failed"):
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

**Frontend change:** Replace `setInterval(poll, 2000)` with `new EventSource("/stream/{id}")`.

---

## Improvement 4 — Pluggable Storage Backend

**Problem:** Every agent writes to `/app/OUTPUT/` (local filesystem). This breaks on multi-node deployments and prevents cloud deployment without persistent volume mounts.

**Proposed solution:** Introduce a `StorageBackend` abstraction in `common/output.py`. Swap implementations via an environment variable.

```python
# common/storage.py
from abc import ABC, abstractmethod
from pathlib import Path
import os

class StorageBackend(ABC):
    @abstractmethod
    def write(self, path: str, data: bytes) -> str:
        """Write data to storage. Returns a URI."""

    @abstractmethod
    def read(self, uri: str) -> bytes:
        """Read data from a URI."""


class LocalStorage(StorageBackend):
    def write(self, path: str, data: bytes) -> str:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return str(p)

    def read(self, uri: str) -> bytes:
        return Path(uri).read_bytes()


class AzureBlobStorage(StorageBackend):
    def __init__(self):
        from azure.storage.blob import BlobServiceClient
        self._client = BlobServiceClient.from_connection_string(
            os.getenv("AZURE_STORAGE_CONNECTION_STRING")
        )
        self._container = os.getenv("AZURE_STORAGE_CONTAINER", "agent-outputs")

    def write(self, path: str, data: bytes) -> str:
        blob_name = path.lstrip("/")
        self._client.get_blob_client(self._container, blob_name).upload_blob(data, overwrite=True)
        return f"az://{self._container}/{blob_name}"

    def read(self, uri: str) -> bytes:
        blob_name = uri.replace(f"az://{self._container}/", "")
        return self._client.get_blob_client(self._container, blob_name).download_blob().readall()


def get_storage() -> StorageBackend:
    backend = os.getenv("STORAGE_BACKEND", "local")
    if backend == "azure":
        return AzureBlobStorage()
    return LocalStorage()
```

**`OutputManager` delegates to the backend** — no changes needed in individual agents.

---

## Improvement 5 — Structured Logging with Correlation IDs

**Problem:** Agents use `print()` or bare `logging.info()`. Distributed failures are hard to trace because log lines from different agents have no shared identifier.

**Proposed solution:** Thread an `execution_id` through every request and log it as a structured field.

```python
# common/logging.py
import logging
import json
from contextvars import ContextVar

_execution_id: ContextVar[str] = ContextVar("execution_id", default="-")

def set_execution_id(exec_id: str) -> None:
    _execution_id.set(exec_id)

class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({
            "ts":           self.formatTime(record),
            "level":        record.levelname,
            "agent":        record.name,
            "execution_id": _execution_id.get(),
            "message":      record.getMessage(),
        })

def configure_logging(agent_name: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredFormatter())
    logging.getLogger(agent_name).addHandler(handler)
    logging.getLogger(agent_name).setLevel(logging.INFO)
```

**Orchestrator injects the ID into each step request; each agent extracts and sets it:**
```python
# In orchestrator step dispatch
headers = {"X-Execution-ID": execution_id}
requests.post(agent_url, json=payload, headers=headers)

# In each agent endpoint
exec_id = request.headers.get("X-Execution-ID", "-")
set_execution_id(exec_id)
```

---

## Improvement 6 — LLM Cost and Token Tracking

**Problem:** There is no visibility into how many tokens or API dollars each execution consumes. Runaway extractions or retries can accumulate significant cost silently.

**Proposed solution:** Add a lightweight cost tracker to `LLMClient`.

```python
# In common/llm.py — extend LLMClient

from dataclasses import dataclass, field

# Approximate cost per 1M tokens (USD) — update as pricing changes
_COST_TABLE = {
    "gpt-4o":            {"input": 2.50, "output": 10.00},
    "gpt-4o-mini":       {"input": 0.15, "output": 0.60},
    "gemini-2.0-flash":  {"input": 0.10, "output": 0.40},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
}

@dataclass
class UsageSummary:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0

class LLMClient:
    def __init__(self):
        # ... existing init ...
        self.usage = UsageSummary()

    def _record_usage(self, model: str, input_tokens: int, output_tokens: int) -> None:
        self.usage.calls += 1
        self.usage.input_tokens += input_tokens
        self.usage.output_tokens += output_tokens
        costs = _COST_TABLE.get(model, {"input": 0, "output": 0})
        self.usage.estimated_cost_usd += (
            input_tokens  / 1_000_000 * costs["input"] +
            output_tokens / 1_000_000 * costs["output"]
        )
```

**The orchestrator logs and stores usage per execution. Thresholds can halt runaway pipelines:**
```python
MAX_COST_USD = float(os.getenv("MAX_EXECUTION_COST_USD", "2.00"))
if llm.usage.estimated_cost_usd > MAX_COST_USD:
    raise RuntimeError(f"Cost limit exceeded: ${llm.usage.estimated_cost_usd:.2f}")
```

---

## Improvement 7 — Circuit Breaker for LLM Providers

**Problem:** When an LLM provider has an outage, every step retries to exhaustion before failing. Under load this means many concurrent requests are all waiting out retry delays, accumulating latency.

**Proposed solution:** Implement a simple circuit breaker in `LLMClient`. After N consecutive failures for a provider, the breaker opens and fails fast, triggering fallback routing immediately.

```python
# common/circuit_breaker.py
import time
from dataclasses import dataclass, field
from enum import Enum

class State(Enum):
    CLOSED = "closed"       # Normal operation
    OPEN = "open"           # Failing fast
    HALF_OPEN = "half_open" # Testing recovery

@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    recovery_timeout: float = 60.0   # seconds before attempting recovery

    _failures: int = field(default=0, init=False)
    _state: State = field(default=State.CLOSED, init=False)
    _opened_at: float = field(default=0.0, init=False)

    def call(self, fn, *args, **kwargs):
        if self._state == State.OPEN:
            if time.time() - self._opened_at > self.recovery_timeout:
                self._state = State.HALF_OPEN
            else:
                raise RuntimeError("Circuit breaker OPEN — failing fast")
        try:
            result = fn(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise

    def _on_success(self):
        self._failures = 0
        self._state = State.CLOSED

    def _on_failure(self):
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._state = State.OPEN
            self._opened_at = time.time()
```

**`LLMClient` maintains one breaker per provider** and routes around open circuits to the fallback model immediately.

---

## Improvement 8 — Agent Health Pre-flight Check

**Problem:** The orchestrator dispatches steps without checking whether the target agent is available. A step that fails because the agent is down is indistinguishable from a logic failure.

**Proposed solution:** The orchestrator calls `/health` on each unique agent before beginning step execution.

```python
# In orchestrator.py — before the step loop
def _preflight_health_check(agent_urls: list[str]) -> dict[str, bool]:
    results = {}
    for url in agent_urls:
        base = url.rsplit("/", 1)[0]  # strip endpoint path
        try:
            r = requests.get(f"{base}/health", timeout=3)
            results[base] = r.status_code == 200
        except Exception:
            results[base] = False
    return results

health = _preflight_health_check(unique_agent_urls(plan.steps))
unavailable = [url for url, ok in health.items() if not ok]
if unavailable:
    raise HTTPException(
        status_code=503,
        detail=f"Agents unavailable before execution: {unavailable}"
    )
```

---

## Improvement 9 — Parallel Chunk Processing

**Problem:** In `document_extractor.py`, image chunks for a large-format drawing are processed sequentially. An A1 drawing produces 6 chunks; each requires a Gemini vision call (~3–5s). Total: ~30s per drawing, serialised.

**Proposed solution:** Process chunks concurrently with a bounded thread pool.

```python
# In document_extractor.py — replace sequential chunk loop
from concurrent.futures import ThreadPoolExecutor, as_completed

MAX_CHUNK_WORKERS = int(os.getenv("MAX_CHUNK_WORKERS", "4"))

def _extract_chunks_parallel(chunks: list[dict], process_step: dict) -> list[dict]:
    results = [None] * len(chunks)
    with ThreadPoolExecutor(max_workers=MAX_CHUNK_WORKERS) as pool:
        futures = {
            pool.submit(_extract_single_chunk, chunk, process_step): i
            for i, chunk in enumerate(chunks)
        }
        for future in as_completed(futures):
            idx = futures[future]
            results[idx] = future.result()
    return results
```

**Impact:** ~4× speedup for A1 drawings with `MAX_CHUNK_WORKERS=4` (bounded to avoid Gemini rate limits).

---

## Improvement 10 — Dynamic Agent Registry

**Problem:** `registry.json` is static. Adding a new agent requires editing the file and restarting the orchestrator. There is no way to register agents at runtime.

**Proposed solution:** Agents self-register on startup by calling a registration endpoint on the orchestrator.

```python
# Each agent — on startup
import requests, os, socket

def _self_register():
    registry_url = os.getenv("REGISTRY_URL", "http://orchestrator:8001/register")
    try:
        requests.post(registry_url, json={
            "id":          os.getenv("AGENT_ID"),
            "name":        os.getenv("AGENT_NAME"),
            "endpoint":    f"http://{socket.gethostname()}:{os.getenv('PORT', '8090')}",
            "capabilities": [],
        }, timeout=3)
    except Exception:
        pass  # Non-fatal — static registry is fallback

# In orchestrator.py — new endpoint
@app.post("/register")
def register_agent(agent: dict):
    registry_data["agents"][agent["id"]] = agent
    return {"status": "registered"}
```

**With Redis backing**, the registry survives orchestrator restarts and is shared across instances.

---

## Summary — Recommended Implementation Order

| Priority | Improvement | Effort | Impact |
|---|---|---|---|
| 1 | **Persistent state (Redis)** | Low–Medium | Crash recovery, horizontal scale |
| 2 | **Structured logging + correlation IDs** | Low | Debuggability, observability |
| 3 | **Parallel chunk processing** | Low | Direct 4× speed improvement |
| 4 | **Agent health pre-flight** | Low | Fail fast on infrastructure issues |
| 5 | **LLM cost tracking** | Low–Medium | Budget control |
| 6 | **Circuit breaker** | Medium | Resilience to provider outages |
| 7 | **SSE real-time UI updates** | Medium | Better UX, reduced polling overhead |
| 8 | **Async task queue (Celery)** | High | Horizontal scale, queue-level retry |
| 9 | **Pluggable storage backend** | Medium | Cloud deployment readiness |
| 10 | **Dynamic agent registry** | Medium | Operational flexibility |

Items 1–5 are largely additive changes with no breaking modifications to existing agent contracts. Items 6–10 involve more structural changes and are better suited to a future iteration.
