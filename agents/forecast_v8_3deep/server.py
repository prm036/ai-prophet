"""FastAPI wrapper around agent_v3.

POST /predict — Prophet Arena Forecast track contract.
GET  /health  — liveness check.

Run:
    uvicorn server:app --host 0.0.0.0 --port 8000 --workers 2

Reliability features:
  - Per-event hard timeout (8 min, leaves 2 min slack inside the 10-min eval window)
  - Uniform fallback if anything throws or times out — NEVER return invalid JSON
  - In-memory cache keyed by market_ticker (same event re-queried over 2 weeks)
  - Request ID logged for debugging

The Prophet Arena eval server POSTs ONE event per request. The expected shape is:
    {"probabilities": [{"market": <outcome>, "probability": float in [0,1]}, ...]}
"""
from __future__ import annotations
import asyncio
import logging
import os
import time
import uuid
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("forecast.server")

import agent_v8_3deep_orall_v2 as agent

TIMEOUT_SEC = float(os.environ.get("FORECAST_TIMEOUT_SEC", "480"))  # 8 min
CACHE_TTL_SEC = float(os.environ.get("FORECAST_CACHE_TTL_SEC", "1800"))  # 30 min

app = FastAPI(title="Prophet Arena Forecast Agent")

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    try:
        body = await request.body()
        body_text = body.decode("utf-8")
    except Exception:
        body_text = "<could not read body>"
    
    logger.error("Validation error for request %s: %s", request.url, exc)
    logger.error("Raw request headers: %s", dict(request.headers))
    logger.error("Raw request body: %s", body_text)
    
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body": body_text},
    )

_cache: dict[str, tuple[float, dict]] = {}


class Event(BaseModel):
    event_ticker: str | None = None
    market_ticker: str | None = None
    title: str | None = None
    subtitle: str | None = None
    description: str | None = None
    category: str | None = None
    rules: str | None = None
    close_time: str | None = None
    outcomes: list[str]
    resolved_outcome: dict | None = None


def _uniform(outcomes: list[str]) -> dict:
    p = 1.0 / max(1, len(outcomes))
    return {"probabilities": [{"market": o, "probability": p} for o in outcomes]}


def _cache_key(ev: dict) -> str:
    return ev.get("market_ticker") or ev.get("event_ticker") or str(ev.get("title", ""))


def _cached(key: str) -> dict | None:
    item = _cache.get(key)
    if not item:
        return None
    ts, val = item
    if time.time() - ts > CACHE_TTL_SEC:
        _cache.pop(key, None)
        return None
    return val


def _store(key: str, val: dict) -> None:
    _cache[key] = (time.time(), val)


async def _predict_with_timeout(ev: dict, request_id: str) -> dict:
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, agent.predict, ev),
            timeout=TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        logger.warning("[%s] agent timeout after %.0fs; using uniform fallback",
                       request_id, TIMEOUT_SEC)
        return _uniform(ev.get("outcomes") or ["Yes", "No"])
    except Exception as exc:
        logger.warning("[%s] agent raised %s (%s); using uniform fallback",
                       request_id, type(exc).__name__, exc)
        return _uniform(ev.get("outcomes") or ["Yes", "No"])


@app.post("/predict")
async def predict(event: Event, request: Request):
    request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())[:8]
    
    logger.info("[%s] Received Request - URL: %s, Method: %s, Client: %s", 
                request_id, request.url, request.method, request.client)
    logger.info("[%s] Request Headers: %s", request_id, dict(request.headers))
    logger.info("[%s] Request Payload: %s", request_id, event.model_dump())
    
    ev = event.model_dump()
    key = _cache_key(ev)

    cached = _cached(key)
    if cached:
        logger.info("[%s] cache HIT %s outcomes=%d", request_id, key, len(ev.get("outcomes") or []))
        return cached

    t0 = time.perf_counter()
    result = await _predict_with_timeout(ev, request_id)
    dt = time.perf_counter() - t0

    # final validation: every outcome must be in result, probs in [0,1]
    out_set = set(ev.get("outcomes") or [])
    probs = result.get("probabilities") or []
    have = {p.get("market") for p in probs}
    if not out_set or have != out_set:
        logger.warning("[%s] validation FAIL for %s: expected %s, got %s; using uniform",
                       request_id, key, out_set, have)
        result = _uniform(ev.get("outcomes") or ["Yes", "No"])
    for p in result["probabilities"]:
        p["probability"] = max(0.0, min(1.0, float(p["probability"])))

    _store(key, result)
    logger.info("[%s] predicted %s in %.2fs outcomes=%d",
                request_id, key, dt, len(result["probabilities"]))
    return result


@app.get("/health")
async def health():
    return {"status": "ok", "agent": "v8_3deep_orall_v2", "cache_size": len(_cache)}


@app.get("/")
async def root():
    return {
        "service": "prophet-arena-forecast-agent",
        "agent": "v8_3deep_orall_v2 (cost-optimized for forecast)",
        "endpoints": ["/predict (POST)", "/health (GET)"],
    }
