"""Inbound channels: signed webhooks, heartbeat, Twitch EventSub, Twitter, and A2A."""

import json
import hmac
import hashlib
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Response, Request
from fastapi.responses import JSONResponse
from loguru import logger

from config import config
from api.deps import _read_runtime_settings, _reconcile_heartbeat_runtime

router = APIRouter()


def _load_webhook_secret() -> str:
    secret = str(getattr(config, "webhook_secret", "") or "").strip()
    if secret:
        return secret
    path_val = str(getattr(config, "webhook_secret_path", "") or "").strip()
    if not path_val:
        return ""
    path = Path(path_val).expanduser()
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    return ""


def _parse_signature(header_val: str) -> Optional[str]:
    raw = str(header_val or "").strip()
    if not raw:
        return None
    if raw.lower().startswith("sha256="):
        raw = raw.split("=", 1)[1].strip()
    raw = raw.strip()
    if not raw:
        return None
    return raw


def _verify_webhook_signature(secret: str, body: bytes, signature_header: Optional[str]) -> bool:
    if not secret:
        return False
    sig = _parse_signature(signature_header or "")
    if not sig:
        return False
    expected = hmac.new(secret.encode("utf-8"), body or b"", hashlib.sha256).hexdigest()
    try:
        return hmac.compare_digest(expected, sig)
    except Exception:
        return False


@router.post("/webhooks/{path:path}")
async def webhook_trigger(path: str, request: Request):
    """Trigger a routine via webhook."""
    from agent.routines import get_routine_manager
    manager = get_routine_manager()
    
    if not bool(getattr(config, "webhook_enabled", False)):
        raise HTTPException(status_code=403, detail="Webhooks are turned off (Settings > Advanced).")
    routine = manager.get_routine_by_webhook(f"/{path}")
    if not routine:
        raise HTTPException(status_code=404, detail="Webhook not found")
    raw_body = await request.body()
    secret = _load_webhook_secret()
    if not secret:
        # Unsigned webhooks would let any local program or web page run a routine.
        raise HTTPException(status_code=403, detail="Set a webhook secret in Settings > Advanced before using webhooks.")
    sig = request.headers.get("x-echospeak-signature") or request.headers.get("x-signature") or ""
    if not _verify_webhook_signature(secret, raw_body, sig):
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    # Get request body if any — validate type and size
    try:
        body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        if not isinstance(body, dict) or len(json.dumps(body)) > 10_000:
            raise HTTPException(status_code=400, detail="Invalid webhook body (must be JSON object under 10KB)")
    except HTTPException:
        raise
    except Exception:
        body = {}
    
    # Merge body into action config for the routine
    action_config = {**routine.action_config, "webhook_body": body}
    
    # Run the routine
    manager.run_routine(routine.id)
    
    return {"ok": True, "triggered": routine.name}


# ---------------------------------------------------------------------------
# Heartbeat API (v5.4.0 — Proactive Mode)
# ---------------------------------------------------------------------------

@router.get("/heartbeat")
async def heartbeat_status():
    """Get heartbeat scheduler status and config."""
    from agent.heartbeat import get_heartbeat_manager
    hb = get_heartbeat_manager()
    return {
        "enabled": bool(getattr(config, "heartbeat_enabled", False)),
        "running": bool(hb and hb.is_running),
        "interval_minutes": getattr(config, "heartbeat_interval", 30),
        "prompt": getattr(config, "heartbeat_prompt", ""),
        "channels": list(getattr(config, "heartbeat_channels", [])),
        "last_tick": hb.last_tick if hb else None,
        "next_tick": hb.next_tick if hb else None,
    }


@router.post("/heartbeat")
async def heartbeat_update(request: Request):
    """Update heartbeat configuration at runtime."""
    data = await request.json()
    # Persist to settings
    existing = _read_runtime_settings()
    for key in ("heartbeat_enabled", "heartbeat_interval", "heartbeat_prompt", "heartbeat_channels"):
        if key in data:
            existing[key] = data[key]
    config.apply_overrides(existing)
    config.write_runtime_overrides(existing)
    await _reconcile_heartbeat_runtime()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Retired ProactiveEngine compatibility API
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Discord API
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Telegram API (v5.4.0)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Twitch API (v6.7.0)
# ---------------------------------------------------------------------------


@router.post("/twitch/eventsub")
async def twitch_eventsub_webhook(request: Request):
    """Handle Twitch EventSub webhook notifications.

    Twitch sends:
      - Verification challenges (respond with the challenge string)
      - Notifications (chat messages, stream events)
      - Revocations
    """
    try:
        from twitch_bot import get_twitch_bot
        bot = get_twitch_bot()
        if not bot or not bot.is_running:
            return JSONResponse({"error": "Twitch bot not running"}, status_code=503)

        headers = {k.lower(): v for k, v in request.headers.items()}
        body = await request.body()
        result = await bot.handle_eventsub_webhook(headers, body)

        if "error" in result:
            if result["error"] == "signature_invalid":
                return JSONResponse({"error": "Forbidden"}, status_code=403)
            return JSONResponse(result, status_code=400)

        if "challenge" in result:
            # Must return the challenge as plain text for verification
            return Response(content=result["challenge"], media_type="text/plain")

        return result
    except Exception as e:
        logger.error(f"Twitch EventSub webhook error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Twitter/X API (v6.7.0)
# ---------------------------------------------------------------------------

@router.get("/twitter")
async def twitter_status():
    """Get Twitter/X bot status."""
    try:
        from twitter_bot import get_twitter_bot
        bot = get_twitter_bot()
        return bot.get_status()
    except Exception:
        return {"enabled": False, "running": False}


@router.get("/twitter/autonomous")
async def twitter_autonomous_status():
    """Get autonomous tweeting status, pending tweet, and recent history."""
    try:
        from twitter_bot import get_twitter_bot
        bot = get_twitter_bot()
        status = bot.get_status().get("autonomous", {})
        history = bot.get_auto_tweet_history(limit=10) if bot.is_running else []
        return {"ok": True, **status, "history": history}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/twitter/autonomous/approve")
async def twitter_autonomous_approve():
    """Approve and post the pending autonomous tweet."""
    try:
        from twitter_bot import get_twitter_bot
        bot = get_twitter_bot()
        if not bot or not bot.is_running:
            return {"ok": False, "error": "Twitter bot is not running"}
        return bot.approve_pending_tweet()
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/twitter/autonomous/reject")
async def twitter_autonomous_reject():
    """Reject the pending autonomous tweet."""
    try:
        from twitter_bot import get_twitter_bot
        bot = get_twitter_bot()
        if not bot or not bot.is_running:
            return {"ok": False, "error": "Twitter bot is not running"}
        return bot.reject_pending_tweet()
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── A2A Protocol Endpoints (v6.0.0) ─────────────────────────────────

def _a2a_auth_check(request):
    """A2A always needs its key: inbound tasks run an agent turn on this PC."""
    auth_key = str(getattr(config, "a2a_auth_key", "") or "").strip()
    if not auth_key:
        raise HTTPException(status_code=503, detail="A2A is enabled but A2A_AUTH_KEY is not set")
    auth_header = str(request.headers.get("authorization", "") or "").strip()
    supplied = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else auth_header
    if not hmac.compare_digest(supplied.encode("utf-8"), auth_key.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid A2A auth key")


@router.get("/.well-known/agent.json")
async def agent_card():
    """Publish EchoSpeak's A2A Agent Card for discovery."""
    if not getattr(config, "a2a_enabled", False):
        raise HTTPException(status_code=404, detail="A2A protocol is disabled")
    from agent.a2a import build_agent_card
    base_url = str(getattr(config, "api", None) and getattr(config.api, "base_url", "") or "")
    card = build_agent_card(base_url)
    return card.to_dict()


@router.post("/a2a")
async def a2a_rpc(request: Request):
    """JSON-RPC 2.0 endpoint for A2A task operations.

    Methods: tasks/send, tasks/get, tasks/cancel
    """
    if not getattr(config, "a2a_enabled", False):
        raise HTTPException(status_code=404, detail="A2A protocol is disabled")
    _a2a_auth_check(request)

    from agent.a2a import (
        get_task_manager, A2AMessage, TextPart, TaskState,
    )

    try:
        body = await request.json()
    except Exception:
        return {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None}

    rpc_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params", {})

    tm = get_task_manager()

    # ── tasks/send ──────────────────────────────────────
    if method == "tasks/send":
        msg_data = params.get("message", {})
        parts = msg_data.get("parts", [])
        text = " ".join(p.get("text", "") for p in parts if p.get("type") == "text")
        if not text:
            return {"jsonrpc": "2.0", "error": {"code": -32602, "message": "No text content in message"}, "id": rpc_id}

        message = A2AMessage(role="user", parts=[TextPart(text=text)])
        task = tm.create_task(message, metadata=params.get("metadata"))
        task = tm.process_task(task)
        return {"jsonrpc": "2.0", "result": task.to_dict(), "id": rpc_id}

    # ── tasks/get ───────────────────────────────────────
    if method == "tasks/get":
        task_id = params.get("id", "")
        task = tm.get_task(task_id)
        if not task:
            return {"jsonrpc": "2.0", "error": {"code": -32001, "message": "Task not found"}, "id": rpc_id}
        return {"jsonrpc": "2.0", "result": task.to_dict(), "id": rpc_id}

    # ── tasks/cancel ────────────────────────────────────
    if method == "tasks/cancel":
        task_id = params.get("id", "")
        task = tm.update_status(task_id, TaskState.CANCELED)
        if not task:
            return {"jsonrpc": "2.0", "error": {"code": -32001, "message": "Task not found"}, "id": rpc_id}
        return {"jsonrpc": "2.0", "result": task.to_dict(), "id": rpc_id}

    return {"jsonrpc": "2.0", "error": {"code": -32601, "message": f"Method not found: {method}"}, "id": rpc_id}


@router.get("/a2a/tasks")
async def a2a_list_tasks(request: Request, limit: int = 50):
    """Admin endpoint: list active A2A tasks."""
    if not getattr(config, "a2a_enabled", False):
        raise HTTPException(status_code=404, detail="A2A protocol is disabled")
    _a2a_auth_check(request)
    from agent.a2a import get_task_manager
    tm = get_task_manager()
    tasks = tm.list_tasks(limit=limit)
    return {"tasks": [t.to_dict() for t in tasks], "count": len(tasks)}
