"""HTTP API for the chat assistant.

    POST /ask     {"question": "...", "view": {...}}  -> answer, citations, view to show
    GET  /health  data freshness and model in use

Run locally:  uvicorn gse_api.app:app --port 8000
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import OrderedDict, defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .agent import Agent
from .data import Database, download_bundle

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("gse_api")

ALLOWED_ORIGINS = [o.strip() for o in os.environ.get(
    "ALLOWED_ORIGINS", "https://sahiljangra12.github.io,http://localhost:8765").split(",") if o.strip()]
RATE_PER_MIN = int(os.environ.get("RATE_LIMIT_PER_MINUTE", "12"))
REFRESH_HOURS = float(os.environ.get("DATA_REFRESH_HOURS", "12"))

app = FastAPI(title="Global Sustainability Explorer API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["GET", "POST"],
                   allow_headers=["Content-Type"])

_state = {"db": None, "agent": None, "loaded_at": 0.0}
_lock = threading.Lock()
_cache: OrderedDict[str, dict] = OrderedDict()
_hits: dict[str, deque] = defaultdict(deque)


def load():
    local = os.environ.get("LOCAL_DATA_DIR")
    data_dir = Path(local) if local else download_bundle()
    db = Database(data_dir)
    _state.update(db=db, agent=Agent(db), loaded_at=time.time())
    _cache.clear()
    log.info("Loaded %d indicators from %s", len(db.indicators), data_dir)


def ensure_loaded():
    with _lock:
        stale = time.time() - _state["loaded_at"] > REFRESH_HOURS * 3600
        if _state["db"] is None or (stale and not os.environ.get("LOCAL_DATA_DIR")):
            try:
                load()
            except Exception:
                log.exception("Data load failed")
                if _state["db"] is None:
                    raise


class AskBody(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    view: dict | None = None


@app.get("/health")
def health():
    try:
        ensure_loaded()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    db = _state["db"]
    return {"ok": True, "indicators": len(db.indicators), "data_built": db.meta.get("generated"),
            "model": _state["agent"].model, "llm_configured": bool(_state["agent"].api_key)}


@app.post("/ask")
def ask(body: AskBody, request: Request):
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0]
    now = time.time()
    q = _hits[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= RATE_PER_MIN:
        raise HTTPException(429, "Too many questions in a minute. Please wait a moment.")
    q.append(now)
    ensure_loaded()
    agent = _state["agent"]
    if not agent.api_key:
        raise HTTPException(503, "No language model is configured (set LLM_API_KEY).")
    key = body.question.strip().lower()
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    try:
        result = agent.ask(body.question.strip(), body.view)
    except Exception as e:  # noqa: BLE001
        log.exception("ask failed")
        raise HTTPException(502, f"The language model could not be reached ({type(e).__name__}).") from e
    _cache[key] = result
    if len(_cache) > 500:
        _cache.popitem(last=False)
    return result
