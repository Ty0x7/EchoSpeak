"""Health, startup readiness, metrics, the root banner and screen analysis."""

import threading
from collections import deque

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from loguru import logger

from config import config
from api.deps import _metrics, _metrics_lock

router = APIRouter()
_vision_manager = None
_tool_latency_ms: deque[float] = deque(maxlen=200)


def get_vision_manager():
    """Get or create the vision manager instance."""
    global _vision_manager
    if _vision_manager is None:
        from io_module.vision import create_vision_manager
        _vision_manager = create_vision_manager()
    return _vision_manager


_WARMUP_GATE = threading.Event()


class ScreenAnalysisResponse(BaseModel):
    """Response model for screen analysis."""
    text: str
    text_length: int
    has_text: bool
    image_size: dict


@router.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "name": "Echo Speak API",
        "version": "1.0.0",
        "status": "running",
        "local_models_enabled": config.use_local_models
    }


@router.post("/vision/analyze", response_model=ScreenAnalysisResponse)
async def analyze_screen():
    """
    Capture screen and perform OCR analysis.

    Returns:
        Analysis results with extracted text.
    """
    try:
        vision = get_vision_manager()
        result = vision.capture_and_analyze()

        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])

        return ScreenAnalysisResponse(
            text=result.get("text", ""),
            text_length=result.get("text_length", 0),
            has_text=result.get("has_text", False),
            image_size=result.get("image_size", {})
        )
    except Exception as e:
        logger.error(f"Screen analysis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    from version import APP_VERSION

    return {"status": "healthy", "version": APP_VERSION}


@router.get("/health/capabilities")
def health_capabilities(refresh: bool = False):
    """System check for Settings and the startup notice: what works on this computer (agent/health.py)."""
    from agent.health import check_all

    items = check_all(force=refresh)
    return {"items": items, "problems": sum(1 for item in items if item["status"] != "ok")}


@router.get("/startup/readiness")
def startup_readiness():
    # Sync on purpose: FastAPI runs it in a worker thread, so readiness checks
    # (file reads, provider probe) never block the event loop.
    """Authoritative durable-owner readiness; optional providers never block it."""
    from agent.startup_readiness import build_startup_readiness

    result = build_startup_readiness()
    if result.get("core_ready"):
        _WARMUP_GATE.set()
    return result


@router.get("/metrics")
async def metrics():
    with _metrics_lock:
        counts = dict(_metrics)
        samples = list(_tool_latency_ms)

    stats = {"count": len(samples)}
    if samples:
        samples.sort()
        n = len(samples)
        avg = sum(samples) / max(1, n)
        def pick(p: float) -> float:
            idx = int(max(0, min(n - 1, round((n - 1) * p))))
            return float(samples[idx])

        stats.update(
            {
                "avg_ms": round(avg, 2),
                "p50_ms": round(pick(0.50), 2),
                "p90_ms": round(pick(0.90), 2),
                "p99_ms": round(pick(0.99), 2),
            }
        )
    return {"requests": counts.get("requests", 0), "errors": counts.get("errors", 0), "tool_calls": counts.get("tool_calls", 0), "tool_errors": counts.get("tool_errors", 0), "tool_latency_ms": stats}
