"""Controlling EchoSpeak from the terminal: models, permissions, modes. Standard library only.

Each function wraps an existing backend route; nothing here changes a setting on its own. Callers
(the TUI and the plain REPL) ask the owner first.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from .client import ApiError, Client
from .render import one_line

DEFAULT_OLLAMA = "http://127.0.0.1:11434"
DEFAULT_CONTEXT = 32768   # the standard window (backend agent/context_windows.py); 16K stays a choice
LOCAL_PROVIDERS = {"ollama", "lmstudio", "localai", "vllm", "llama_cpp"}

# Approval modes (agent/lean/settings.py approval_mode). Shift+Tab cycles the first three; `never`
# is never in the cycle and needs a typed confirmation.
APPROVAL_CYCLE = ("smart", "autonomous", "always")
APPROVAL_MODES = {
    "smart": "Ask for risky actions (default): destructive or external steps wait for you",
    "autonomous": "Autonomous: routine approvals don't interrupt; risky ones are held for you",
    "always": "Always ask: every action waits for your OK",
    "never": "Never ask: actions run without approval (dangerous)",
}

# Permission switches (config.py). Only ones with a real effect on agents are listed.
PERMISSIONS = (
    ("enable_system_actions", "Actions on this computer", "Master switch for anything that acts on this PC"),
    ("allow_file_write", "Write files", "Create, edit, move and delete files"),
    ("allow_terminal_commands", "Terminal commands", "Run shell commands"),
    ("allow_open_application", "Open applications", "Launch desktop apps"),
    ("allow_open_chrome", "Open the browser", "Open pages in Chrome"),
    ("allow_playwright", "Browser automation", "Drive a browser with Playwright"),
    ("allow_desktop_automation", "Desktop control", "Click and type on your desktop"),
    ("allow_self_modification", "Self-modification", "Let EchoSpeak change its own code"),
)

MASTER = "enable_system_actions"

# How people name a switch: "/permissions files on".
SWITCH_NAMES = {
    "actions": MASTER, "system": MASTER, "master": MASTER, "computer": MASTER,
    "files": "allow_file_write", "file": "allow_file_write", "write": "allow_file_write",
    "terminal": "allow_terminal_commands", "commands": "allow_terminal_commands", "shell": "allow_terminal_commands",
    "apps": "allow_open_application", "applications": "allow_open_application",
    "browser": "allow_open_chrome", "chrome": "allow_open_chrome",
    "playwright": "allow_playwright", "automation": "allow_playwright",
    "desktop": "allow_desktop_automation",
    "self": "allow_self_modification", "self-modification": "allow_self_modification",
}

# The app's names for an agent's tool groups (apps/web/src/lean/Dialogs.tsx TOOLSET_INFO).
TOOLSET_INFO = {
    "core": "Files and basics",
    "web": "Web search and reading pages",
    "live": "Weather, sports, stocks, shopping, video and image cards",
    "terminal": "Run commands",
    "vision": "Read screenshots and images",
    "desktop": "Mouse, keyboard and app control",
    "comms": "Email and Discord",
    "memory": "Remember things",
    "skills": "Installed skills and MCP",
    "self": "Edit EchoSpeak itself",
}
TOOLSET_ORDER = ("core", "web", "live", "terminal", "vision", "desktop", "comms", "memory", "skills", "self")
# Groups that can act on this computer or reach people: turning them on asks first.
TOOLSET_POWERFUL = {"terminal", "desktop", "comms", "self"}
# The global switches a group still needs, whatever the agent may use (agent/lean/toolbox.py NATIVE_POLICY_FLAGS).
TOOLSET_NEEDS = {
    "core": ("allow_file_write",),
    "terminal": ("allow_terminal_commands",),
    "desktop": ("allow_desktop_automation", "allow_open_application"),
    "self": ("allow_self_modification",),
}

APPROVAL_MODE_ORDER = ("smart", "autonomous", "always", "never")
PRIVACY_MODES = {
    "standard": "Cloud models and web services are allowed",
    "private": "Only services on your own machines: local models, your SearXNG",
    "offline": "Nothing leaves this computer",
}
SEARCH_PROVIDERS = {
    "auto": "Your configured services, then DuckDuckGo",
    "duckduckgo": "DuckDuckGo only (no key needed)",
    "searxng": "Your SearXNG (needs its address)",
    "brave": "Brave Search (needs an API key)",
    "tavily": "Tavily (needs an API key)",
}


def switch_label(key: str) -> str:
    return next((label for k, label, _d in PERMISSIONS if k == key), key)


def switch_key(name: str) -> Optional[str]:
    """A switch from what someone typed: "files", "terminal", "Write files", or the setting's own key."""
    raw = " ".join(str(name or "").lower().split())
    if raw in {k for k, _l, _d in PERMISSIONS}:
        return raw
    if raw in SWITCH_NAMES:
        return SWITCH_NAMES[raw]
    return next((k for k, label, _d in PERMISSIONS if label.lower() == raw), None)


def switch_state(settings: dict, key: str) -> str:
    """on, off, or blocked: on, but "Actions on this computer" (the master switch) is off, so it does nothing."""
    if not settings.get(key):
        return "off"
    if key != MASTER and not settings.get(MASTER):
        return "blocked"
    return "on"


def enable_plan(settings: dict, key: str) -> list[str]:
    """The switches to turn on so ``key`` takes effect: the master switch first when it is off."""
    plan = [] if key == MASTER or settings.get(MASTER) else [MASTER]
    return plan + ([key] if not settings.get(key) or key == MASTER or plan else [])


_BLOCKED_WORDS = re.compile(r"\b(permission|permissions|turned off|switched off|is off|are off|disabled|not allowed|"
                            r"not enabled|settings ?[›>] ?permissions)\b", re.I)
# ...and it is about the agent being stopped (or EchoSpeak's settings), not, say, an explanation of chmod.
_ABOUT_ITSELF = re.compile(r"(\bI can'?t\b|\bI cannot\b|\bI'?m not able\b|\bI am not able\b|\bunable to\b|\bmy \w+(?: \w+)? "
                           r"permissions?\b|\b(?:EchoSpeak|in) settings\b|Settings ?[›>]|^Error:|\bFailed:)", re.I | re.M)
_TOPICS = (
    ("allow_file_write", re.compile(r"\b(file|files|write|writing|create|creation|edit|editing|save|saving)\b", re.I)),
    ("allow_terminal_commands", re.compile(r"\b(terminal|command|commands|shell|run|running)\b", re.I)),
    ("allow_open_application", re.compile(r"\b(open apps?|application|applications|launch)\b", re.I)),
    ("allow_open_chrome", re.compile(r"\b(browser|chrome)\b", re.I)),
    ("allow_desktop_automation", re.compile(r"\b(desktop|click|mouse|keyboard)\b", re.I)),
    ("allow_self_modification", re.compile(r"\bself[- ]modification\b", re.I)),
)


def blocked_hint(texts: list[str], settings: dict) -> Optional[str]:
    """One line when an answer or a step says a permission blocked it and that switch really is off now.

    Names only switches that are off in ``settings`` (read fresh by the caller), so the hint is never a guess
    about the state, only about which switch the words were about.
    """
    said = " ".join(str(t or "") for t in texts)
    if not _BLOCKED_WORDS.search(said) or not _ABOUT_ITSELF.search(said):
        return None
    wanted = [key for key, pattern in _TOPICS if pattern.search(said)]
    off = [k for k in wanted if switch_state(settings, k) != "on"]
    if not off:
        return None
    names = [switch_label(k) for k in off]
    if not settings.get(MASTER):
        names.append(switch_label(MASTER))
    what = " and ".join(dict.fromkeys(names))
    verb = "is" if len(dict.fromkeys(names)) == 1 else "are"
    return f"{what} {verb} off · /permissions to turn {'it' if verb == 'is' else 'them'} on"


def toolset_rows(agent_toolsets: list[str], available: list[str], settings: dict) -> list[dict]:
    """An agent's tool groups for the terminal: id, the app's label, on/off, and the global switches still off."""
    ids = [t for t in TOOLSET_ORDER if t in available] + [t for t in available if t not in TOOLSET_ORDER]
    rows = []
    for tid in ids:
        needs = [switch_label(k) for k in TOOLSET_NEEDS.get(tid, ()) if switch_state(settings, k) != "on"]
        if needs and not settings.get(MASTER):
            needs.append(switch_label(MASTER))
        rows.append({"id": tid, "label": TOOLSET_INFO.get(tid, tid), "on": tid in agent_toolsets,
                     "needs": ", ".join(dict.fromkeys(needs))})
    return rows


ASSISTANT_MODES = {
    "unified": "One Echo for chat and code: a trusted project in the session gives Echo engineering tools",
    "environments": "Separate Chat, Code and Agents environments (the app's classic layout)",
}


def next_approval_mode(mode: str) -> str:
    if mode not in APPROVAL_CYCLE:
        return "smart"
    return APPROVAL_CYCLE[(APPROVAL_CYCLE.index(mode) + 1) % len(APPROVAL_CYCLE)]


# ── state outside the backend (per user) ────────────────────────────
def state_dir(env: Mapping[str, str] = os.environ) -> Path:
    if env.get("ECHOSPEAK_STATE_DIR", "").strip():
        return Path(env["ECHOSPEAK_STATE_DIR"]).expanduser()
    base = env.get("XDG_STATE_HOME", "").strip() or str(Path(env.get("HOME") or Path.home()) / ".local" / "state")
    return Path(base) / "echospeak"


def _prefs_path(env: Mapping[str, str]) -> Path:
    return state_dir(env) / "terminal.json"


def read_prefs(env: Mapping[str, str] = os.environ) -> dict:
    try:
        data = json.loads(_prefs_path(env).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_prefs(prefs: dict, env: Mapping[str, str] = os.environ) -> None:
    path = _prefs_path(env)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(prefs, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def confirmed_model(data_dir: str, env: Mapping[str, str] = os.environ) -> str:
    return str((read_prefs(env).get("confirmed_models") or {}).get(data_dir) or "")


def confirm_model(data_dir: str, model_ref: str, env: Mapping[str, str] = os.environ) -> None:
    prefs = read_prefs(env)
    prefs.setdefault("confirmed_models", {})[data_dir] = model_ref
    write_prefs(prefs, env)


def folder_offer(cwd: str, env: Mapping[str, str] = os.environ) -> bool:
    """Whether to offer "work in this folder?" at start: a real folder, not / or the home folder itself,
    and not one the owner already said no to."""
    try:
        path = Path(cwd).resolve()
    except OSError:
        return False
    home = Path(env.get("HOME") or Path.home()).resolve()
    if not path.is_dir() or path == Path(path.anchor) or path == home:
        return False
    return str(path) not in (read_prefs(env).get("declined_folders") or [])


def decline_folder(cwd: str, env: Mapping[str, str] = os.environ) -> None:
    prefs = read_prefs(env)
    declined = [p for p in (prefs.get("declined_folders") or []) if isinstance(p, str)]
    path = str(Path(cwd).resolve())
    if path not in declined:
        prefs["declined_folders"] = (declined + [path])[-200:]
        write_prefs(prefs, env)


def forget_folder_decline(cwd: str, env: Mapping[str, str] = os.environ) -> None:
    prefs = read_prefs(env)
    path = str(Path(cwd).resolve())
    if path in (prefs.get("declined_folders") or []):
        prefs["declined_folders"] = [p for p in prefs["declined_folders"] if p != path]
        write_prefs(prefs, env)


# ── models ───────────────────────────────────────────────────────────
@dataclass
class ModelInfo:
    name: str
    size: int = 0              # bytes on disk (Ollama)
    params: str = ""
    quant: str = ""
    fits: Optional[bool] = None
    hint: str = ""


def gpu_vram_mib(timeout: float = 2.0) -> int:
    """Total VRAM of the largest NVIDIA GPU, or 0 when unknown."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return 0
    try:
        out = subprocess.run([exe, "--query-gpu=memory.total", "--format=csv,noheader,nounits"], capture_output=True,
                             text=True, timeout=timeout, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    best = 0
    for line in out.splitlines():
        try:
            best = max(best, int(float(line.strip())))
        except ValueError:
            continue
    return best


def fit_hint(size_bytes: int, vram_mib: int) -> tuple[Optional[bool], str]:
    """Whether the weights plus ~1 GiB of KV cache/overhead fit in VRAM."""
    if not size_bytes:
        return None, ""
    gib = size_bytes / 1024 ** 3
    size = f"{gib:.1f} GB"
    if not vram_mib:
        return None, size
    need_mib = size_bytes / 1024 ** 2 + 1024
    if need_mib <= vram_mib:
        return True, f"{size} · fits your GPU"
    return False, f"{size} · slow: spills to RAM"


def ollama_base(settings: dict) -> str:
    local = settings.get("local") if isinstance(settings.get("local"), dict) else {}
    if str(local.get("provider") or "") == "ollama" and local.get("base_url"):
        return str(local["base_url"]).rstrip("/")
    host = os.environ.get("OLLAMA_HOST", "").strip()
    if host:
        return (host if host.startswith("http") else f"http://{host}").rstrip("/")
    return DEFAULT_OLLAMA


def ollama_models(base: str, timeout: float = 3.0) -> list[ModelInfo]:
    """Models installed in Ollama (/api/tags), largest-first is not assumed: sorted by name."""
    req = urllib.request.Request(base.rstrip("/") + "/api/tags", headers={"User-Agent": "echospeak-terminal"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - loopback/user-configured URL
        data = json.loads(resp.read(4 * 1024 * 1024) or b"{}")
    out = []
    for m in data.get("models") or []:
        if not isinstance(m, dict) or not m.get("name"):
            continue
        details = m.get("details") if isinstance(m.get("details"), dict) else {}
        out.append(ModelInfo(one_line(m["name"], 120), int(m.get("size") or 0), one_line(details.get("parameter_size"), 20),
                             one_line(details.get("quantization_level"), 20)))
    return sorted(out, key=lambda x: x.name)


def with_hints(models: list[ModelInfo], vram_mib: int) -> list[ModelInfo]:
    for m in models:
        m.fits, m.hint = fit_hint(m.size, vram_mib)
        if m.params:
            m.hint = f"{m.params} · {m.hint}" if m.hint else m.params
    return models


def settings_of(client: Client) -> dict:
    return (client.get("/settings") or {}).get("settings") or {}


def current_model(settings: dict) -> tuple[str, str]:
    """(provider, model) the global default uses."""
    if settings.get("use_local_models"):
        local = settings.get("local") if isinstance(settings.get("local"), dict) else {}
        return str(local.get("provider") or ""), str(local.get("model_name") or "")
    return "cloud", ""


def model_setup_reason(settings: dict, installed: list[str], confirmed: str) -> str:
    """Why the owner must pick a model now, or '' when the configured one is fine.

    Only local Ollama setups are checked; a cloud provider is the owner's explicit choice.
    """
    provider, model = current_model(settings)
    if provider == "cloud":
        return ""
    if provider and provider != "ollama":
        return ""
    if not model or model in {"auto", "default"}:
        return "No model is chosen yet."
    if installed and model not in installed:
        return f"The configured model {model} is not installed in Ollama."
    if confirmed != f"ollama:{model}":
        return f"Echo is set to {model}, chosen automatically or elsewhere. Confirm it, or choose another."
    return ""


def save_default_model(client: Client, model: str, base_url: str, context_length: int = DEFAULT_CONTEXT) -> dict:
    return client.request("PUT", "/settings", body={
        "use_local_models": True,
        "local": {"provider": "ollama", "base_url": base_url, "model_name": model, "context_length": int(context_length)},
    })


def context_windows(client: Client) -> dict:
    """GET /settings/context-windows: the choices for the current model, the configured and the served window."""
    return client.get("/settings/context-windows") or {}


def save_context_length(client: Client, tokens: int) -> dict:
    return client.request("PUT", "/settings", body={"local": {"context_length": int(tokens)}})


def switch_session_model(client: Client, session_id: str, provider: str, model: str, base_url: str = "") -> dict:
    """POST /provider/switch with the session's current binding revision (CAS)."""
    info = client.get("/provider", session_id=session_id) or {}
    revision = int(info.get("binding_revision") or 1)
    if str(info.get("provider")) == provider and str(info.get("model")) == model:
        return {"success": True, "message": "Session already uses that model", "model": model}
    body: dict[str, Any] = {"provider": provider, "session_id": session_id, "expected_revision": revision, "model": model}
    if base_url:
        body["base_url"] = base_url
    return client.post("/provider/switch", body)


def put_setting(client: Client, key: str, value: Any) -> dict:
    return client.request("PUT", "/settings", body={key: value})


def put_setting_many(client: Client, values: dict) -> dict:
    """Several settings in one write, so a switch and the master switch it needs change together."""
    return client.request("PUT", "/settings", body=dict(values))


def available(client: Client, path: str, **params: Any) -> bool:
    try:
        client.get(path, **params)
        return True
    except ApiError:
        return False
