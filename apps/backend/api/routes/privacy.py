"""Privacy: the mode, what talks to the internet, and the check (agent/privacy.py)."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter

router = APIRouter()


@router.get("/privacy/status")
def privacy_status() -> dict[str, Any]:
    """The mode, each component's state, and connections since EchoSpeak started (memory only)."""
    from agent import privacy

    return privacy.status()


@router.get("/privacy/log")
def privacy_log(limit: int = 50) -> dict[str, Any]:
    from agent import privacy

    return {"items": privacy.recent(max(1, min(int(limit), 200)))}


@router.post("/privacy/log/clear")
def privacy_log_clear() -> dict[str, Any]:
    from agent import privacy

    privacy.reset_log()
    return {"ok": True}


@router.post("/privacy/check")
async def privacy_check() -> dict[str, Any]:
    """Probe this setup: local models and your SearXNG answer, and what would leave your machines."""
    from agent import privacy_check

    return await asyncio.to_thread(privacy_check.run)
