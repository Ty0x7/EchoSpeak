"""
API module for Echo Speak.
Provides FastAPI server for REST API access.
"""

import os
import sys
import asyncio
import hmac
import re
import threading
import time
from contextlib import asynccontextmanager

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
# Avoid Tk/matplotlib GUI backends on worker threads (Tcl_AsyncDelete process death).
os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from fastapi import FastAPI, HTTPException, Response, Request, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from loguru import logger

from collections import defaultdict


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    config,
    ModelProvider,
)
from api.auth import (
    _ALLOWED_ORIGINS,
    _PUBLIC_AUTH_PATHS,
    _api_auth_ok,
    _get_client_ip,
    _local_request_guard,
)
from api.deps import (
    forget_discord_runtime,
    _reconcile_discord_bot_runtime,
    _reconcile_heartbeat_runtime,
    get_agent,
)
from api.routes import (
    capabilities,
    channels,
    chat,
    gateway,
    lean,
    media,
    media_runtime,
    memory,
    projects,
    sessions,
    settings,
    system,
)
from api.routes.settings import (
    _autoconfigure_local_provider,
)
from api.routes.system import (
    _WARMUP_GATE,
)


def _start_background_warmup() -> None:
    """Load the heavy chat stack after the server is up, off the startup path.

    The first agent needs langchain, FAISS and the embedding model loaded, and
    the memory store. Doing it here, a moment after readiness, means the UI is
    interactive immediately and the first message rarely waits on imports.
    """
    if os.getenv("ECHOSPEAK_TESTING", "").strip().lower() in {"1", "true", "yes"}:
        return
    if os.getenv("ECHOSPEAK_DISABLE_WARMUP", "").strip().lower() in {"1", "true", "yes"}:
        return

    def warm() -> None:
        # Wait until readiness has been reported once (or 20s), so the heavy
        # imports never hold the import lock while readiness is being checked.
        _WARMUP_GATE.wait(timeout=20)
        time.sleep(1.0)  # let the UI hydrate first
        started = time.perf_counter()
        try:
            get_agent("default")
            logger.info("Background warmup finished in {:.1f}s", time.perf_counter() - started)
        except Exception as exc:
            logger.warning("Background warmup skipped: {}", exc)

    threading.Thread(target=warm, name="echospeak-warmup", daemon=True).start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan."""
    # The API lifespan is the sole scheduler/coordinator owner in server and
    # desktop processes. Agent instances must not start competing daemons.
    os.environ["ECHOSPEAK_API_RUNTIME"] = "1"
    try:
        from agent.lean.soul_defaults import refresh_default_soul

        refresh_default_soul()
    except Exception:
        logger.warning("SOUL.md default refresh failed", exc_info=True)
    threading.Thread(target=_autoconfigure_local_provider, name="local-model-autoconfig", daemon=True).start()
    build_id = (
        os.environ.get("ECHOSPEAK_BUILD_ID")
        or os.environ.get("ECHOSPEAK_DESKTOP_INSTANCE_ID")
        or "dev"
    )
    logger.info(
        "Starting Echo Speak API server build_id={} pid={} data_dir={}",
        build_id,
        os.getpid(),
        os.environ.get("ECHOSPEAK_DATA_DIR") or "",
    )
    gateway.attach_loop(asyncio.get_running_loop())
    # Build the shared memory/embedding owner during readiness so the first
    # user Turn never pays model-load or vector-store initialization latency.
    # Note: prewarm owns the "default" Session agent only. Other Sessions still
    # construct Session-scoped agents on first use; they share StateStore via
    # get_state_store() singleton + phase3 process lock (not duplicate writers).
    if bool(getattr(config, "enable_prewarm", False)):
        try:
            prewarmed_agent = await asyncio.to_thread(get_agent, "default")
            if prewarmed_agent.llm_provider == ModelProvider.LM_STUDIO:
                from agent.model_runtime import ensure_selected_model_ready, resolve_model_profile
                model_id = str(prewarmed_agent._selected_model_id() or "default")
                profile = resolve_model_profile(
                    ModelProvider.LM_STUDIO.value,
                    model_id,
                    {"context_limit": int(getattr(config.local, "context_length", 0) or 32768)},
                )
                await asyncio.to_thread(
                    ensure_selected_model_ready,
                    ModelProvider.LM_STUDIO.value,
                    model_id,
                    llm=prewarmed_agent.model_runtime.llm,
                    profile=profile,
                    timeout=float(
                        getattr(config, "turn_understanding_cold_start_timeout_seconds", 120.0)
                        or 120.0
                    ),
                )
            logger.info("Default Session runtime and embeddings prewarmed")
        except Exception as exc:
            logger.warning("Runtime prewarm degraded; startup continues honestly: {}", exc)
    else:
        logger.info("Model prewarming disabled on startup; model loads on demand.")
    await _reconcile_discord_bot_runtime()
    await _reconcile_heartbeat_runtime()
    _start_background_warmup()
    
    # --- Telegram Bot startup (v5.4.0) ---
    if bool(getattr(config, "allow_telegram_bot", False)):
        try:
            from telegram_bot import TelegramBotManager, set_telegram_bot
            tg_agent = get_agent()
            tg_bot = TelegramBotManager(agent=tg_agent)
            set_telegram_bot(tg_bot)
            tg_bot.start()
            logger.info("Telegram bot startup initiated")
        except Exception as e:
            logger.warning(f"Failed to start Telegram bot: {e}")
    
    # --- Twitch Bot startup (v6.7.0) ---
    if bool(getattr(config, "allow_twitch", False)):
        try:
            from twitch_bot import get_twitch_bot
            twitch = get_twitch_bot()
            twitch.set_agent(get_agent())
            await twitch.start()
            logger.info("Twitch bot startup initiated")
        except Exception as e:
            logger.warning(f"Failed to start Twitch bot: {e}")

    # --- Twitter/X Bot startup (v6.7.0) ---
    if bool(getattr(config, "allow_twitter", False)):
        try:
            from twitter_bot import get_twitter_bot
            twitter = get_twitter_bot()
            twitter.set_agent(get_agent())
            await twitter.start()
            logger.info("Twitter/X bot startup initiated")
        except Exception as e:
            logger.warning(f"Failed to start Twitter/X bot: {e}")

    # --- Routine Scheduler startup ---
    try:
        from agent.routines import get_routine_manager

        from agent.lean.automations import routine_runner

        # Routines run on the lean loop and post into their own chat.
        _routine_callback = routine_runner(get_agent)

        rm = get_routine_manager()
        rm.set_run_callback(_routine_callback)
        rm.start_scheduler()
        logger.info("Routine scheduler started")
    except Exception as exc:
        logger.warning(f"Failed to start routine scheduler: {exc}")

    # Warn if A2A is enabled without authentication
    if getattr(config, "a2a_enabled", False) and not getattr(config, "a2a_auth_key", ""):
        logger.warning("⚠️ A2A protocol enabled WITHOUT authentication key! Set A2A_AUTH_KEY for production.")

    # --- Spotify Playback Monitor startup ---
    if bool(getattr(config, "allow_spotify", False)):
        gateway.start_spotify_monitor()
        logger.info("Spotify playback monitor started")

    yield
    try:
        from agent.lean.terminal import stop_all_processes

        stop_all_processes()
    except Exception:
        pass
    
    # Shutdown heartbeat scheduler
    try:
        from agent.heartbeat import get_heartbeat_manager
        hb = get_heartbeat_manager()
        if hb:
            hb.stop()
    except Exception:
        pass

    # Shutdown Telegram bot
    try:
        from telegram_bot import get_telegram_bot
        tg = get_telegram_bot()
        if tg:
            tg.stop()
    except Exception:
        pass

    # Shutdown Twitch bot
    try:
        from twitch_bot import get_twitch_bot
        twitch = get_twitch_bot()
        if twitch and twitch.is_running:
            await twitch.stop()
    except Exception:
        pass

    # Shutdown Twitter/X bot
    try:
        from twitter_bot import get_twitter_bot
        twitter = get_twitter_bot()
        if twitter and twitter.is_running:
            await twitter.stop()
    except Exception:
        pass

    # Shutdown routine scheduler
    try:
        from agent.routines import get_routine_manager
        get_routine_manager().stop_scheduler()
    except Exception:
        pass

    await gateway.stop_spotify_monitor()

    # Shutdown Discord bot
    try:
        from discord_bot import get_bot, stop_discord_bot

        if get_bot() is not None:
            await stop_discord_bot()
    except Exception:
        pass
    forget_discord_runtime()
    gateway.attach_loop(None)
    
    logger.info("Shutting down Echo Speak API server...")


app = FastAPI(
    title="Echo Speak API",
    description="Voice AI Assistant API with support for local models",
    version="1.0.0",
    lifespan=lifespan
)


from api.routes import creations, onboarding

for _routes in (system, chat, sessions, projects, memory, settings, capabilities, channels, gateway, lean, media, media_runtime, creations, onboarding):
    app.include_router(_routes.router)
# Ensure domain ToolRegistry entries load independently of agent import order.
for _domain_module in ("agent.voice_runtime", "agent.generation_runtime"):
    try:
        __import__(_domain_module)
    except Exception:
        pass

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def local_request_guard_middleware(request: Request, call_next):
    client_ip = request.client.host if request.client else ""
    problem = _local_request_guard(
        request.method, request.headers.get("host", ""), request.headers.get("origin", ""), client_ip
    )
    if problem:
        logger.warning("Refused request to {}: {} (client {})", request.url.path, problem, client_ip)
        return JSONResponse({"detail": f"Request refused: {problem}."}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def api_auth_middleware(request: Request, call_next):
    """Optional shared-key auth for network/remote EchoSpeak access."""
    if request.method.upper() == "OPTIONS" or request.url.path in _PUBLIC_AUTH_PATHS:
        return await call_next(request)
    if request.method.upper() == "GET":
        # Artifact frames load in an <iframe>, which can't send the auth header;
        # a short-lived token from an authenticated call opens that one frame.
        frame = re.fullmatch(r"/lean/artifacts/([a-f0-9]{12})/frame", request.url.path)
        if frame:
            from agent.lean.artifacts import frame_token_ok

            if frame_token_ok(frame.group(1), request.query_params.get("t", "")):
                return await call_next(request)
    client_ip = _get_client_ip(request)
    if not _api_auth_ok(request.headers, client_ip):
        return Response(
            content='{"detail":"EchoSpeak API auth required."}',
            status_code=401,
            media_type="application/json",
        )
    return await call_next(request)

# Graceful restart support
_restart_requested = False
_restart_lock = threading.Lock()

# Rate limiting
# Interactive desktop use + hydration + dense live harnesses need more headroom
# than 100/min when every poll, state refresh, and mutation shares one IP.
_rate_limit_lock = threading.Lock()
_rate_limits: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT_REQUESTS = int(os.getenv("ECHOSPEAK_RATE_LIMIT_REQUESTS", "240") or 240)
RATE_LIMIT_WINDOW = float(os.getenv("ECHOSPEAK_RATE_LIMIT_WINDOW", "60") or 60.0)
# Safe reads / hydration / diagnostics do not consume the mutation budget.
# Mutations and expensive model routes still count.
_RATE_LIMIT_SAFE_GET_PREFIXES = (
    "/threads",
    "/pending-action",
    "/approvals",
    "/executions",
    "/traces",
    "/provider",
    "/settings",
    "/memory",
    "/projects",
    "/history",
    "/soul",
)


def _rate_limit_exempt(path: str, method: str) -> bool:
    """Return True only for inexpensive, read-only, non-model routes.

    Never exempt:
    - POST/PUT/PATCH/DELETE (mutations, model calls, workers)
    - /query, /approvals/*/confirm
    - rebuild/compact endpoints
    """
    p = str(path or "")
    m = str(method or "GET").upper()
    if p in {"/health", "/metrics", "/favicon.ico"} or p.startswith("/gateway/"):
        return True
    if m != "GET":
        return False
    if p.startswith("/query") or "/confirm" in p or p.endswith("/rebuild-index") or p.endswith("/compact"):
        return False
    if any(p == pref.rstrip("/") or p.startswith(pref) for pref in _RATE_LIMIT_SAFE_GET_PREFIXES):
        return True
    return False


def _check_rate_limit(client_ip: str) -> tuple[bool, int]:
    """Check if client is within rate limit. Returns (allowed, remaining)."""
    now = time.time()
    with _rate_limit_lock:
        # Clean old entries
        _rate_limits[client_ip] = [
            t for t in _rate_limits[client_ip] if now - t < RATE_LIMIT_WINDOW
        ]
        current_count = len(_rate_limits[client_ip])
        if current_count >= RATE_LIMIT_REQUESTS:
            return False, 0
        _rate_limits[client_ip].append(now)
        return True, RATE_LIMIT_REQUESTS - current_count - 1


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Rate limit requests per client IP.

    Safe GET hydration paths are exempt so a completed mutation is never
    misreported as failed solely because a follow-up state read was limited.
    Mutations still count and must not be blindly retried by clients.
    """
    path = request.url.path
    method = request.method
    if _rate_limit_exempt(path, method):
        response = await call_next(request)
        response.headers["X-RateLimit-Policy"] = "safe-get-exempt"
        return response

    client_ip = _get_client_ip(request)
    allowed, remaining = _check_rate_limit(client_ip)

    if not allowed:
        retry_after = max(1, int(RATE_LIMIT_WINDOW))
        return Response(
            content='{"detail":"Rate limit exceeded. Try again later.","code":"rate_limited"}',
            status_code=429,
            media_type="application/json",
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Remaining": "0",
            },
        )

    response = await call_next(request)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    return response


@app.middleware("http")
async def check_graceful_restart(request: Request, call_next):
    """Middleware to handle graceful restart after request completes."""
    global _restart_requested
    response = await call_next(request)
    
    with _restart_lock:
        if _restart_requested:
            logger.info("Graceful restart requested - exiting after response")
            # Use os._exit for immediate termination
            # External process manager (systemd, docker, uvicorn --reload) will restart
            # Small delay to ensure response is sent
            def _do_exit():
                time.sleep(0.5)
                logger.info("Exiting for restart...")
                os._exit(0)
            threading.Thread(target=_do_exit, daemon=True).start()
    
    return response


class RestartRequest(BaseModel):
    """Request model for restart endpoint."""
    delay_seconds: int = Field(default=1, description="Seconds to wait before restart")


class RestartResponse(BaseModel):
    """Response model for restart endpoint."""
    message: str
    restart_scheduled: bool


def _get_admin_api_key() -> str:
    """Get admin API key from environment or generate one."""
    key = os.getenv("ADMIN_API_KEY", "").strip()
    if not key:
        # Generate a random key if not set
        import secrets
        key = secrets.token_hex(16)
        logger.warning("ADMIN_API_KEY not set. A random key has been generated. Set ADMIN_API_KEY in .env for production.")
        logger.debug(f"Generated admin key: {key}")
    return key


_ADMIN_API_KEY = None


def _verify_admin_key(api_key: str = Header(None, alias="X-Admin-Key")) -> str:
    """Dependency to verify admin API key."""
    global _ADMIN_API_KEY
    if _ADMIN_API_KEY is None:
        _ADMIN_API_KEY = _get_admin_api_key()
    
    if not api_key or not hmac.compare_digest(str(api_key).encode("utf-8"), str(_ADMIN_API_KEY).encode("utf-8")):
        raise HTTPException(
            status_code=403,
            detail="Admin access required. Provide X-Admin-Key header."
        )
    return api_key


@app.post("/admin/restart", response_model=RestartResponse)
async def request_restart(
    req: RestartRequest = RestartRequest(),
    _: str = Depends(_verify_admin_key)
):
    """Schedule a graceful restart after current request completes.
    
    Requires X-Admin-Key header with ADMIN_API_KEY from environment.
    The server will exit after completing the current response,
    and an external process manager (systemd, docker, uvicorn) will restart it.
    """
    global _restart_requested
    
    with _restart_lock:
        if _restart_requested:
            return RestartResponse(message="Restart already scheduled", restart_scheduled=True)
        _restart_requested = True
    
    logger.info(f"Restart scheduled in {req.delay_seconds}s")
    return RestartResponse(
        message=f"Restart scheduled. Server will restart after current request completes.",
        restart_scheduled=True
    )


def start_server(host: str = None, port: int = None):
    """
    Start the FastAPI server.

    Args:
        host: Host to bind to.
        port: Port to listen on.
    """
    import uvicorn
    host = host or config.api.host
    port = port or config.api.port
    import ipaddress
    normalized_host = str(host or "").strip().strip("[]").lower()
    try:
        loopback = normalized_host == "localhost" or ipaddress.ip_address(normalized_host).is_loopback
    except ValueError:
        loopback = False
    auth_ready = bool(
        getattr(config, "api_auth_enabled", False)
        and str(getattr(config, "api_auth_key", "") or "").strip()
    )
    if not loopback and not auth_ready:
        raise RuntimeError(
            f"Refusing unauthenticated non-loopback API bind to {host}. "
            "Enable API_AUTH_ENABLED and set API_AUTH_KEY, or bind to 127.0.0.1/::1."
            )
    logger.info(f"Starting server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    start_server()
