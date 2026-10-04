"""The /gateway/ws websocket: live events for the desktop app, Discord/Twitch broadcasts, Spotify now-playing."""

import queue
import asyncio
import threading
import time
import uuid
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

import anyio

from config import config
from api.auth import _api_auth_ok, _get_client_ip
from api.deps import (
    _metric_inc,
    _register_query_cancellation,
    _release_query_cancellation,
    _start_agent_thread,
    get_agent,
)

router = APIRouter()


# Track active WebSocket connections for cross-source notifications (Fix 5)
_gateway_connections: set = set()
_gateway_loop = None


async def _broadcast_to_gateway(event: dict) -> None:
    """Push an event to all connected gateway WebSocket clients."""
    dead: list = []
    for ws in _gateway_connections:
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _gateway_connections.discard(ws)


def broadcast_discord_event(event: dict) -> None:
    """Schedule a broadcast from a sync context (e.g. Discord bot callbacks)."""
    import asyncio
    try:
        loop = _gateway_loop
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
        if loop is None:
            return
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(_broadcast_to_gateway(event), loop)
    except Exception:
        pass


# ── Spotify Playback Monitor ───────────────────────────────────────────────
# Polls Spotify current_playback() and broadcasts state changes to the
# Web UI so the avatar can vibe when music is playing.
# If Spotify returns a fatal error (403 Premium required, 401, etc.) the
# monitor logs once and stops polling permanently.

_spotify_monitor_task: Optional[asyncio.Task] = None
_spotify_last_state: dict = {"is_playing": False, "track_id": None}


def attach_loop(loop: Optional[asyncio.AbstractEventLoop]) -> None:
    """Remember the server's event loop so other threads can broadcast."""
    global _gateway_loop
    _gateway_loop = loop


def start_spotify_monitor() -> None:
    global _spotify_monitor_task
    _spotify_monitor_task = asyncio.create_task(_spotify_playback_monitor())


async def stop_spotify_monitor() -> None:
    global _spotify_monitor_task
    task, _spotify_monitor_task = _spotify_monitor_task, None
    if task and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

_SPOTIFY_FATAL_MARKERS = (
    "403",
    "premium",
    "forbidden",
    "401",
    "unauthorized",
)


async def _spotify_playback_monitor():
    """Background loop: poll Spotify playback every ~12s, broadcast changes."""
    poll_interval = 12  # seconds
    consecutive_errors = 0
    while True:
        try:
            await asyncio.sleep(poll_interval)
            try:
                from config import config as _cfg
                if not getattr(_cfg, "allow_spotify", False):
                    continue
            except Exception:
                continue

            # Run the blocking spotipy call in a thread
            try:
                from skills.spotify.tools import _get_spotify_client
                sp = await asyncio.to_thread(_get_spotify_client)
                current = await asyncio.to_thread(sp.current_playback)
                consecutive_errors = 0
            except Exception as exc:
                msg = str(exc).strip() or exc.__class__.__name__
                msg_lower = msg.lower()
                # Detect fatal / permanent errors and stop polling
                if any(m in msg_lower for m in _SPOTIFY_FATAL_MARKERS):
                    logger.warning(
                        f"Spotify monitor disabled — account lacks required access: {msg}"
                    )
                    # Broadcast a final "stopped" so the avatar stops dancing
                    _spotify_last_state["is_playing"] = False
                    _spotify_last_state["track_id"] = None
                    await _broadcast_to_gateway({
                        "type": "spotify_playback",
                        "is_playing": False,
                        "track_id": "",
                        "track_name": "",
                        "track_artist": "",
                        "duration_ms": 0,
                        "progress_ms": 0,
                        "at": time.time(),
                    })
                    return  # exit the loop permanently
                consecutive_errors += 1
                if consecutive_errors <= 3:
                    logger.warning(f"Spotify monitor error ({consecutive_errors}/3): {msg}")
                elif consecutive_errors == 4:
                    logger.warning("Spotify monitor: suppressing further errors until success")
                continue

            is_playing = False
            track_id = None
            track_name = ""
            track_artist = ""
            track_duration_ms = 0
            progress_ms = 0

            if current and current.get("item"):
                is_playing = bool(current.get("is_playing", False))
                track = current["item"]
                track_id = track.get("id") or track.get("uri")
                track_name = track.get("name", "")
                track_artist = ", ".join(
                    a.get("name", "") for a in track.get("artists", [])
                )
                track_duration_ms = track.get("duration_ms", 0)
                progress_ms = current.get("progress_ms", 0)

            prev = _spotify_last_state
            changed = (
                prev["is_playing"] != is_playing
                or prev["track_id"] != track_id
            )

            _spotify_last_state["is_playing"] = is_playing
            _spotify_last_state["track_id"] = track_id

            # Broadcast on every poll if playing, or once on stop
            if is_playing or changed:
                await _broadcast_to_gateway({
                    "type": "spotify_playback",
                    "is_playing": is_playing,
                    "track_id": track_id or "",
                    "track_name": track_name,
                    "track_artist": track_artist,
                    "duration_ms": track_duration_ms,
                    "progress_ms": progress_ms,
                    "at": time.time(),
                })
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.debug(f"Spotify monitor tick error: {exc}")
            await asyncio.sleep(30)


@router.websocket("/gateway/ws")
async def gateway_ws(websocket: WebSocket):
    client_host = _get_client_ip(websocket)
    if not _api_auth_ok(websocket.headers, client_host):
        await websocket.close(code=1008, reason="EchoSpeak API auth required")
        return
    offered_protocols = {
        value.strip()
        for value in str(websocket.headers.get("sec-websocket-protocol") or "").split(",")
        if value.strip()
    }
    await websocket.accept(subprotocol="echospeak" if "echospeak" in offered_protocols else None)
    _gateway_connections.add(websocket)
    session_id = str(uuid.uuid4())
    await websocket.send_json({"type": "gateway_ready", "session_id": session_id, "at": time.time()})

    while True:
        try:
            payload = await websocket.receive_json()
        except WebSocketDisconnect:
            _gateway_connections.discard(websocket)
            break
        except Exception as exc:
            await websocket.send_json({"type": "error", "message": f"Invalid message: {exc}", "at": time.time()})
            continue

        if not isinstance(payload, dict):
            await websocket.send_json({"type": "error", "message": "Message must be a JSON object.", "at": time.time()})
            continue

        msg_type = str(payload.get("type") or "").strip().lower()
        if msg_type == "ping":
            await websocket.send_json({"type": "pong", "at": time.time()})
            continue
        if msg_type != "query":
            await websocket.send_json({"type": "error", "message": f"Unknown message type: {payload.get('type')}", "at": time.time()})
            continue

        message = payload.get("message")
        if not isinstance(message, str) or not message.strip():
            await websocket.send_json({"type": "error", "message": "Missing 'message' field for query.", "at": time.time()})
            continue

        include_memory = payload.get("include_memory", True)
        if isinstance(include_memory, str):
            include_memory = include_memory.strip().lower() not in {"false", "0", "no", "off"}
        elif include_memory is None:
            include_memory = True
        else:
            include_memory = bool(include_memory)

        thread_id_val = payload.get("thread_id")
        thread_id = str(thread_id_val).strip() if thread_id_val is not None else None
        if thread_id == "":
            thread_id = None

        request_id = payload.get("request_id") or str(uuid.uuid4())
        request_id = str(request_id)
        thinking_enabled = bool(payload.get("thinking_enabled", True))
        reasoning_effort = str(payload.get("reasoning_effort") or "medium")
        if reasoning_effort not in {
            "minimal", "low", "medium", "high", "extra_high", "max", "ultra"
        }:
            reasoning_effort = "medium"

        agent = get_agent(thread_id)
        try:
            ws = str(payload.get("workspace") or "").strip()
            if ws:
                if ws.lower() in {"auto", "default", "none", "clear"}:
                    agent.configure_workspace(None)
                else:
                    agent.configure_workspace(ws)
        except Exception:
            pass

        q: queue.Queue = queue.Queue()
        cancel_event = threading.Event()
        _register_query_cancellation(request_id, thread_id or "default", cancel_event)
        _metric_inc("requests", 1)
        _start_agent_thread(
            agent=agent,
            message=message,
            include_memory=include_memory,
            thread_id=thread_id,
            workspace=payload.get("workspace"),
            request_id=request_id,
            q=q,
            cancel_event=cancel_event,
            thinking_enabled=thinking_enabled,
            reasoning_effort=reasoning_effort,
        )

        try:
            first = True
            startup_timeout = max(
                1.0,
                float(getattr(config, "stream_startup_timeout_seconds", 15.0) or 15.0),
            )
            while True:
                try:
                    item = await anyio.to_thread.run_sync(
                        (lambda: q.get(timeout=startup_timeout)) if first else q.get,
                        abandon_on_cancel=True,
                    )
                except queue.Empty:
                    cancel_event.set()
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": "The selected model did not start responding in time. This run was cancelled.",
                            "request_id": request_id,
                            "at": time.time(),
                        }
                    )
                    break
                first = False
                if item is None:
                    break
                try:
                    await websocket.send_json(item)
                except WebSocketDisconnect:
                    return
                except Exception as exc:
                    logger.warning(f"Gateway WS send failed: {exc}")
                    break
        finally:
            cancel_event.set()
            _release_query_cancellation(request_id, cancel_event)
