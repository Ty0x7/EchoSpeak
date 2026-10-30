"""Sign-in status, plan models and sign-in for the owner's own coding agents.

Each check runs the vendor's unmodified program in an empty folder and keeps only "signed
in, how, which plan" and the models that harness offers. Sign-in always completes in the
vendor's own flow (ChatGPT's page for Codex, Anthropic's for Claude Code), and EchoSpeak
never sees, copies or stores a token, email or session.

The last check is remembered (agent status, no secrets) so Echo Code knows an agent is
signed in and which models it offers right after a restart, before any session connects.
Checks run only when the owner asks or Echo Code needs a fresh answer; the runtime catalog
stays passive.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from urllib.parse import urlsplit

from agent.child_env import child_env
from agent.code_executables import executable, locate

LOGIN_SECONDS = 600
STALE_SECONDS = 600  # Echo Code re-checks a remembered status older than this
_codex_login = None  # (process, task) while an official Codex sign-in is open
_cache_lock = threading.Lock()



def _scratch() -> str:
    # No attached project. Metadata checks below disable Claude hooks explicitly.
    from config import DATA_DIR
    folder = Path(DATA_DIR) / "echo-code" / "account-check"
    folder.mkdir(parents=True, exist_ok=True)
    return str(folder)


def _cache_path() -> Path:
    from config import DATA_DIR
    return Path(DATA_DIR) / "echo-code" / "agent-status.json"


def remembered(runtime: str) -> dict:
    """The last status Echo saw for this agent (no secrets), or {} if never checked."""
    try:
        with _cache_lock:
            return dict(json.loads(_cache_path().read_text(encoding="utf-8")).get(runtime) or {})
    except (OSError, ValueError):
        return {}


def _remember(runtime: str, status: dict) -> dict:
    from agent.atomic_replace import replace as atomic_replace
    status = {**status, "checked_at": time.time()}
    path = _cache_path()
    with _cache_lock:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data[runtime] = status
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        atomic_replace(temporary, path)
    return status


def _found(runtime: str) -> dict:
    where = locate(runtime)
    return {"installed": bool(where["path"]), "source": where["source"], "problem": where["problem"]}


async def _codex_server(on_event=None):
    from agent.code_process import RpcProcess

    async def handler(method, params, request=False):
        if on_event:
            await on_event(method, params)
        if request:
            raise ValueError("No client requests are served to an account check")
        return {}

    rpc = RpcProcess([executable("codex"), "app-server"], _scratch(), child_env(), handler)
    await rpc.request("initialize", {"clientInfo": {"name": "echospeak", "title": "Echo Code", "version": "1.0.0"}})
    await rpc.notify("initialized")
    return rpc


def codex_models(rows) -> list[dict]:
    """The models Codex offers this account, as Echo Code lists them. Codex marks internal
    models hidden; those aren't offered (it leaves them out of its own list too)."""
    models = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get("hidden") or not isinstance(row.get("model"), str):
            continue
        name = row["model"].strip()
        if not 0 < len(name) <= 200:
            continue
        efforts = [str(e.get("reasoningEffort") if isinstance(e, dict) else e)[:20]
                   for e in (row.get("supportedReasoningEfforts") or [])[:8]]
        models.append({"id": str(row.get("id") or name)[:200], "model": name,
                       "displayName": str(row.get("displayName") or name)[:120],
                       "description": str(row.get("description") or "")[:240],
                       "isDefault": row.get("isDefault") is True, "efforts": efforts,
                       "defaultEffort": str(row.get("defaultReasoningEffort") or "")[:20]})
    return models[:60]


async def codex_status() -> dict:
    found = _found("codex")
    if not found["installed"]:
        return _remember("codex", {**found, "signed_in": False, "models": []})
    rpc = await _codex_server()
    try:
        account = (await rpc.request("account/read", {"refreshToken": False})).get("account") or {}
        models = codex_models((await rpc.request("model/list", {})).get("data", []))
        from agent.code_limits import codex
        try:
            limits = codex(await rpc.request("account/rateLimits/read", {}))
        except (RuntimeError, TimeoutError):
            limits = codex({})
    finally:
        await rpc.close()
    return _remember("codex", {**found, "signed_in": bool(account), "method": str(account.get("type") or ""),
                               "plan": str(account.get("planType") or ""), "models": models, "subscription_usage": limits,
                               "login_open": bool(_codex_login and not _codex_login[1].done())})


async def codex_login() -> dict:
    """Start ChatGPT sign-in in Codex's own app-server; returns only its official URL."""
    global _codex_login
    if _codex_login and not _codex_login[1].done():
        _codex_login[1].cancel()
    finished = asyncio.Event()

    async def on_event(method, params):
        if method == "account/login/completed":
            finished.set()

    rpc = await _codex_server(on_event)
    try:
        result = await rpc.request("account/login/start", {"type": "chatgpt"})
        url = str(result.get("authUrl", ""))
        host = urlsplit(url).hostname or ""
        if urlsplit(url).scheme != "https" or not any(host == d or host.endswith("." + d) for d in ("openai.com", "chatgpt.com")):
            raise ValueError("Codex returned an unsupported sign-in URL")
    except BaseException:
        await rpc.close()
        raise

    async def wait():
        # Codex's own local callback finishes the sign-in; then its process is done.
        try:
            await asyncio.wait_for(finished.wait(), LOGIN_SECONDS)
        except asyncio.TimeoutError:
            pass
        finally:
            await rpc.close()

    _codex_login = (rpc, asyncio.create_task(wait()))
    return {"url": url}


def _claude(*args, timeout=30) -> subprocess.CompletedProcess:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.run([executable("claude_cli"), *args], cwd=_scratch(), env=child_env(), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout, creationflags=flags)


def claude_status() -> dict:
    found = _found("claude_cli")
    if not found["installed"]:
        return _remember("claude_cli", {**found, "signed_in": False, "models": []})
    done = _claude("auth", "status", "--json")
    try:
        data = json.loads(done.stdout or "{}")
    except ValueError:
        data = {}
    # Kept: signed in, how, which plan. Dropped: email, organization and anything else.
    method = str(data.get("authMethod") or "")
    signed_in = bool(data.get("loggedIn"))
    models, problem = [], ""
    if signed_in:
        try:
            models = claude_models()
        except (ValueError, OSError, subprocess.TimeoutExpired):
            problem = "Model discovery unavailable. Update Claude Code or enter an exact model ID; account access is checked by Claude Code when sending."
    return _remember("claude_cli", {**found, "signed_in": signed_in, "method": method,
                                    "plan": str(data.get("subscriptionType") or "") if method == "claude.ai" else "",
                                    "uses_plan": signed_in and method == "claude.ai",
                                    "models": models, "model_problem": problem,
                                    "subscription_usage": {"windows":[],"unavailable":"Claude Code reports limits during runtime events; no numeric window has been observed."}})


def claude_login() -> dict:
    """Open Claude Code's own `claude auth login` in a console window the owner completes."""
    flags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
    subprocess.Popen([executable("claude_cli"), "auth", "login"], cwd=_scratch(), env=child_env(),
                     creationflags=flags, close_fds=True)
    return {"started": True}


def normalize_claude_models(rows):
    """Exact runtime model IDs and capabilities; never expand a guessed alias list."""
    result = {}
    for row in rows if isinstance(rows,list) else []:
        if not isinstance(row,dict): continue
        identity = row.get('resolvedModel') or row.get('value')
        if not isinstance(identity,str) or not 0 < len(identity) <= 200: continue
        old = result.get(identity,{})
        efforts = row.get('supportedEffortLevels')
        result[identity] = {'id':identity,'model':identity,
            'displayName':str(row.get('displayName') or identity)[:120],
            'description':str(row.get('description') or '')[:240],
            'isDefault':row.get('value')=='default' or old.get('isDefault',False),
            'efforts':[v for v in efforts if isinstance(v,str) and v in {'low','medium','high','xhigh','max'}][:5] if isinstance(efforts,list) else [],
            'defaultEffort':'', 'source':'Claude Code initialization'}
    return list(result.values())[:60]

def claude_models():
    """Read the SDK initialization metadata from the owner's official CLI. No prompt."""
    packet = json.dumps({'type':'control_request','request_id':'echo-models','request':{'subtype':'initialize'}})+'\n'
    done = subprocess.run([executable('claude_cli'),'--print','--input-format','stream-json',
        '--output-format','stream-json','--verbose','--strict-mcp-config','--mcp-config','{"mcpServers":{}}',
        '--settings','{"disableAllHooks":true}','--no-session-persistence','--tools',''],
        input=packet,cwd=_scratch(),env=child_env(),capture_output=True,text=True,encoding='utf-8',errors='replace',
        timeout=25,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if done.returncode or len(done.stdout)>1024*1024: raise ValueError('Claude metadata unavailable')
    for line in done.stdout.splitlines():
        try: row=json.loads(line)
        except ValueError: continue
        if not isinstance(row, dict):
            continue
        response = row.get('response')
        if row.get('type')=='control_response' and isinstance(response,dict) and response.get('request_id')=='echo-models':
            payload = response.get('response')
            models=normalize_claude_models(payload.get('models') if isinstance(payload,dict) else None)
            if models:return models
    raise ValueError('Claude Code did not report models')
