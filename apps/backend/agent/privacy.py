"""Private Mode: which parts of EchoSpeak may talk to the internet, enforced.

Three modes (Settings > Privacy, ``privacy_mode``):

  standard  Everything works as configured. Connections are only counted, so the
            Privacy page can show what talked to the internet.
  private   Nothing sends your content (messages, files, memories, searches, the
            things you ask about) to a service outside your own machines. Local
            and home-network servers (LM Studio, Ollama, your SearXNG) still work.
            Contacts that carry none of your content stay on: opening a web page
            (the site sees only its address), the connection catalog, model
            downloads. Each one can be switched off, and each blocked one can be
            switched back on, per component.
  offline   Only this PC and your own network. Nothing reaches the internet.

Enforcement has three layers, and the Privacy page says which covers what:

  1. Gates. Each component asks ``require(component, url)`` before it connects
     and gets a plain reason when it may not ("Private mode: Brave Search would
     receive your search, so it isn't used").
  2. A backstop. ``install_backstop`` adds a Python audit hook that sees every
     DNS lookup and socket connection the backend makes. In private and offline
     modes, a connection to the internet that no gate approved is refused, so a
     component nobody gated (or a library's own phone-home) can't slip through.
     In standard mode it never blocks.
  3. What neither can see, listed honestly: programs EchoSpeak starts (terminal
     commands, local MCP servers), native code inside some libraries (the
     DuckDuckGo client, the Playwright browser; both are gated before they
     start) and the desktop app's update check (the app asks this module first).

Hosts count as yours when they are this PC (loopback), your network (private
ranges, link-local, Tailscale's 100.64/10, .local/.lan/.home.arpa names) or a
host you trust (``privacy_trusted_hosts``, e.g. your own SearXNG on a VPS).

The connection log is in memory only: component, host and whether it was
allowed. No paths, queries or content, and nothing is written to disk.
"""

from __future__ import annotations

import contextvars
import ipaddress
import socket
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional
from urllib.parse import urlsplit

MODES = ("standard", "private", "offline")

# A permitted host stays usable by any thread for this long after its gate said yes,
# so a library that connects from a worker thread isn't refused by the backstop.
_PERMIT_SECONDS = 300.0
_RESOLVE_CACHE_SECONDS = 300.0
_CGNAT = ipaddress.ip_network("100.64.0.0/10")  # Tailscale and other overlay networks
_LOCAL_SUFFIXES = (".localhost", ".local", ".lan", ".home.arpa", ".internal")


@dataclass(frozen=True)
class Component:
    id: str
    label: str
    sends: str  # what leaves, in plain words
    content: bool  # carries your content to whoever runs the destination
    hosts: tuple[str, ...] = ()  # known destinations, for the log and the backstop
    note: str = ""


COMPONENTS: tuple[Component, ...] = (
    Component("models", "AI models", "Your messages, the files and pages agents read, memories in the prompt",
              True, ("api.openai.com", "generativelanguage.googleapis.com", "api.anthropic.com", "api.x.ai",
                     "api.minimax.io", "api.minimaxi.chat", "openrouter.ai"),
              "Local models (LM Studio, Ollama, llama-server) on this PC or your network always work."),
    Component("search", "Web search", "Your search words",
              True, ("api.tavily.com", "api.search.brave.com", "duckduckgo.com", "*.duckduckgo.com"),
              "Your own SearXNG keeps working in Private mode."),
    Component("web_pages", "Reading web pages", "The address of each page an agent opens", False),
    Component("live_data", "Live data", "What you asked about: a city, a team, a stock ticker", True),
    Component("connections_catalog", "Finding app connections", "The app name you looked for",
              False, ("registry.modelcontextprotocol.io", "registry.npmjs.org", "pypi.org")),
    Component("remote_connections", "Hosted app connections", "Whatever a tool call to that service contains", True,
              note="Connections that run on this PC (local MCP servers) aren't network traffic from EchoSpeak; "
                   "they are programs it starts."),
    Component("downloads", "Model downloads", "Which model file is needed",
              False, ("huggingface.co", "*.huggingface.co", "*.hf.co", "cdn-lfs.huggingface.co")),
    Component("updates", "Update check", "The installed version",
              False, ("github.com", "objects.githubusercontent.com"),
              "Checked by the desktop app, which asks EchoSpeak first."),
    Component("channels", "Chat channels", "Messages to and from Discord, Telegram, Twitch, X and email", True,
              ("discord.com", "*.discord.com", "*.discord.gg", "discordapp.com", "*.discordapp.com",
               "api.telegram.org", "*.twitch.tv", "api.twitter.com", "api.x.com")),
    Component("creation", "Cloud image and video generation", "Your prompt and any images you attach", True,
              ("api.dev.runwayml.com", "api.runwayml.com")),
    Component("a2a", "Other agents (A2A)", "The task you send to another agent", True),
)
_BY_ID = {c.id: c for c in COMPONENTS}

# Tools whose service is outside your machines by nature (the toolbox asks before running them).
TOOL_COMPONENTS: dict[str, str] = {
    "safe_web_fetch": "web_pages", "browse_task": "web_pages", "youtube_transcript": "web_pages",
    "weather_live": "live_data", "sports_live": "live_data", "stock_history": "live_data",
    "product_search": "search", "video_search": "search", "image_search": "search",
    "email_send": "channels", "email_reply": "channels", "email_read_inbox": "channels", "email_search": "channels",
    "email_get_thread": "channels", "discord_send_channel": "channels", "discord_read_channel": "channels",
    "discord_web_send": "channels", "discord_web_read_recent": "channels", "discord_contacts_discover": "channels",
    "telegram_send": "channels", "twitter_post": "channels", "tweet_post": "channels",
}

NOT_ENFORCED = (
    "Programs EchoSpeak starts: terminal commands on this PC and local MCP servers run as their own programs. "
    "In Offline mode the sandbox terminal has no internet; commands on this PC are not network-restricted.",
    "Native code inside two libraries (the DuckDuckGo client and the Playwright browser) is outside the backstop; "
    "both are gated before they start.",
    "Your operating system, browser and other apps on this PC.",
)


class PrivacyBlocked(PermissionError):
    """A component may not connect in the current mode. ``str()`` is the user-facing reason."""

    def __init__(self, component: str, reason: str) -> None:
        super().__init__(reason)
        self.component = component
        self.reason = reason


@dataclass
class Decision:
    allowed: bool
    reason: str = ""
    locality: str = ""  # this_pc | your_network | trusted | internet | unknown


# ── settings ────────────────────────────────────────────────────────────

def _config() -> Any:
    from config import config

    return config


def mode() -> str:
    raw = str(getattr(_config(), "privacy_mode", "standard") or "standard").strip().lower()
    return raw if raw in MODES else "standard"


def _overrides() -> dict[str, str]:
    raw = getattr(_config(), "privacy_overrides", None) or {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v).strip().lower() for k, v in raw.items() if str(v).strip().lower() in {"allow", "block"}}


def trusted_hosts() -> list[str]:
    raw = getattr(_config(), "privacy_trusted_hosts", None) or []
    if isinstance(raw, str):
        raw = [part for part in raw.replace("\n", ",").split(",")]
    return [str(h).strip().lower().strip(".") for h in raw if str(h).strip()]


# ── where a host is ─────────────────────────────────────────────────────

_guard = threading.local()
_resolved: dict[str, tuple[float, list[str]]] = {}
_resolved_lock = threading.Lock()


def _host_of(target: str) -> str:
    target = str(target or "").strip()
    if "://" in target:
        target = urlsplit(target).hostname or ""
    elif target.startswith("["):
        target = target[1:target.find("]")] if "]" in target else target
    elif target.count(":") == 1:
        target = target.split(":", 1)[0]
    return target.strip().strip(".").lower()


def _ip_kind(ip: ipaddress._BaseAddress) -> str:
    if ip.is_loopback:
        return "this_pc"
    if ip.is_private or ip.is_link_local or (ip.version == 4 and ip in _CGNAT):
        return "your_network"
    return "internet"


def _matches(host: str, pattern: str) -> bool:
    pattern = pattern.lower().strip(".")
    if pattern.startswith("*."):
        return host.endswith(pattern[1:]) or host == pattern[2:]
    return host == pattern


def _resolve(host: str) -> list[str]:
    """IP addresses for a name, cached. Our own lookup is invisible to the backstop."""
    now = time.monotonic()
    with _resolved_lock:
        hit = _resolved.get(host)
        if hit and now - hit[0] < _RESOLVE_CACHE_SECONDS:
            return list(hit[1])
    _guard.busy = True
    try:
        infos = socket.getaddrinfo(host, None)
        ips = sorted({str(info[4][0]).split("%", 1)[0] for info in infos})
    except OSError:
        ips = []
    finally:
        _guard.busy = False
    with _resolved_lock:
        _resolved[host] = (now, ips)
    return ips


def locality(target: str, *, resolve: bool = True) -> str:
    """this_pc | your_network | trusted | internet | unknown (a name that didn't resolve)."""
    host = _host_of(target)
    if not host:
        return "unknown"
    if host == "localhost" or host.endswith(".localhost"):
        return "this_pc"
    try:
        return _ip_kind(ipaddress.ip_address(host))
    except ValueError:
        pass
    if any(_matches(host, pattern) for pattern in trusted_hosts()):
        return "trusted"
    if host.endswith(_LOCAL_SUFFIXES) or "." not in host:
        return "your_network"  # mDNS and single-label names live on your network
    if not resolve:
        return "unknown"
    ips = _resolve(host)
    if not ips:
        return "unknown"
    kinds = {_ip_kind(ipaddress.ip_address(ip)) for ip in ips}
    if kinds <= {"this_pc", "your_network"}:
        return "your_network" if "your_network" in kinds else "this_pc"
    return "internet"


def _yours(where: str) -> bool:
    return where in {"this_pc", "your_network", "trusted"}


# ── the decision ────────────────────────────────────────────────────────

def component(component_id: str) -> Component:
    return _BY_ID.get(component_id) or Component(component_id, component_id, "Unknown", True)


def component_state(component_id: str, current: Optional[str] = None) -> str:
    """allowed | blocked | local_only, for a component's internet destinations in this mode."""
    current = current or mode()
    if current == "offline":
        return "local_only"
    override = _overrides().get(component_id)
    if override == "block":
        return "local_only"
    if current == "standard" or override == "allow":
        return "allowed"
    return "local_only" if component(component_id).content else "allowed"


def decide(component_id: str, target: str = "") -> Decision:
    """May ``component_id`` connect to ``target`` (a URL or host) right now?"""
    current = mode()
    state = component_state(component_id, current)
    where = locality(target) if target else "internet"
    if _yours(where) or state == "allowed":
        return Decision(True, "", where)
    item = component(component_id)
    if current == "offline":
        reason = f"Offline mode is on: {item.label.lower()} can only use this PC and your own network."
    elif _overrides().get(component_id) == "block":
        reason = f"{item.label} is switched off in Settings › Privacy."
    else:
        reason = (f"Private mode is on: {item.label.lower()} would send {item.sends.lower()} to a service "
                  "outside your machines.")
        if component_id == "search":
            reason += " Set up your own SearXNG in Settings › Web search to search privately."
        elif component_id == "models":
            reason += " Choose a local model (LM Studio or Ollama) in Settings › Models."
        else:
            reason += " You can allow it in Settings › Privacy."
    return Decision(False, reason, where)


def require(component_id: str, target: str = "") -> Decision:
    """Gate: raise PrivacyBlocked, or record the permission so the backstop lets it through."""
    decision = decide(component_id, target)
    host = _host_of(target)
    _record(component_id, host, decision.allowed, "gate")
    if not decision.allowed:
        raise PrivacyBlocked(component_id, decision.reason)
    if host and mode() != "standard" and not _yours(decision.locality):
        _permit(host)
    return decision


def tool_component(tool_name: str, args: Optional[dict[str, Any]] = None) -> str:
    """The component a tool's network use belongs to, or '' for tools that stay on this PC."""
    if tool_name == "create_media":
        return "" if str((args or {}).get("provider") or "") == "comfyui-local" else "creation"
    return TOOL_COMPONENTS.get(tool_name, "")


def tool_block_reason(tool_name: str, args: Optional[dict[str, Any]] = None) -> str:
    """'' when the tool may run; otherwise why Private or Offline mode stops it."""
    component_id = tool_component(tool_name, args)
    if not component_id:
        return ""
    decision = decide(component_id)
    if decision.allowed:
        return ""
    _record(component_id, "", False, "gate")
    return decision.reason


_scope: contextvars.ContextVar[str] = contextvars.ContextVar("echospeak_privacy_scope", default="")


class _Scope:
    def __init__(self, component_id: str) -> None:
        self.component_id = component_id
        self.token: Optional[contextvars.Token] = None

    def __enter__(self) -> "_Scope":
        self.token = _scope.set(self.component_id)
        return self

    def __exit__(self, *exc: Any) -> None:
        if self.token is not None:
            _scope.reset(self.token)


def scope(component_id: str) -> _Scope:
    """Connections made inside this block (same thread) count as ``component_id``'s."""
    return _Scope(component_id)


# ── permits and the backstop ────────────────────────────────────────────

_permits: dict[str, float] = {}
_permit_ips: dict[str, float] = {}
_permit_lock = threading.Lock()


def _permit(host: str) -> None:
    until = time.monotonic() + _PERMIT_SECONDS
    ips = _resolve(host)
    with _permit_lock:
        _permits[host] = until
        for ip in ips:
            _permit_ips[ip] = until


def _permitted(table: dict[str, float], key: str) -> bool:
    with _permit_lock:
        until = table.get(key)
    return bool(until and until > time.monotonic())


def attribute(host: str) -> str:
    """The component a known destination belongs to, or ''."""
    for item in COMPONENTS:
        if any(_matches(host, pattern) for pattern in item.hosts):
            return item.id
    from config import config

    searx = _host_of(str(getattr(config, "searxng_base_url", "") or ""))
    return "search" if searx and host == searx else ""


def check_connection(event: str, args: tuple) -> None:
    """The backstop's decision for one audit event. Raises PrivacyBlocked to refuse."""
    if getattr(_guard, "busy", False):
        return
    if event == "socket.getaddrinfo":
        raw = args[0] if args else None
        host = raw.decode("ascii", "ignore") if isinstance(raw, (bytes, bytearray)) else str(raw or "")
        host = _host_of(host)
        if not host:
            return
        where = locality(host, resolve=False)
        current = mode()
        if current == "standard":
            if where in {"internet", "unknown"}:
                _record(attribute(host) or _scope.get() or "other", host, True, "backstop")
            return
        if _yours(where) or _permitted(_permits, host):
            return
        component_id = _scope.get() or attribute(host)
        if component_id:
            decision = decide(component_id, host)
            if decision.allowed:
                if not _yours(decision.locality):
                    _permit(host)
                _record(component_id, host, True, "backstop")
                return
        else:
            _guard.busy = True
            try:
                where = locality(host)  # a home-network name resolves to a private address
            finally:
                _guard.busy = False
            if _yours(where):
                return
        _record(component_id or "other", host, False, "backstop")
        raise PrivacyBlocked(component_id or "other",
                             f"EchoSpeak's {current.title()} mode refused a connection to {host}: nothing approved it.")
    if event == "socket.connect" and mode() != "standard":
        address = args[1] if len(args) > 1 else None
        if not isinstance(address, tuple) or not address:
            return  # Unix sockets and the like
        try:
            ip = ipaddress.ip_address(str(address[0]).split("%", 1)[0])
        except ValueError:
            return
        if _ip_kind(ip) != "internet" or _permitted(_permit_ips, str(ip)):
            return
        _record(_scope.get() or "other", str(ip), False, "backstop")
        raise PrivacyBlocked(_scope.get() or "other",
                             f"EchoSpeak's {mode().title()} mode refused a connection to {ip}: nothing approved it.")


_installed = False
_install_lock = threading.Lock()


def install_backstop() -> bool:
    """Add the audit hook once per process (it can't be removed, so tests call check_connection)."""
    global _installed
    with _install_lock:
        if _installed:
            return True

        def hook(event: str, args: tuple) -> None:
            if event == "socket.getaddrinfo" or event == "socket.connect":
                check_connection(event, args)

        sys.addaudithook(hook)
        _installed = True
        return True


def backstop_installed() -> bool:
    return _installed


# ── the connection log (in memory only) ─────────────────────────────────

@dataclass
class _Tally:
    allowed: int = 0
    blocked: int = 0
    hosts: dict[str, float] = field(default_factory=dict)
    last_at: float = 0.0


_log: deque[dict[str, Any]] = deque(maxlen=200)
_tallies: dict[str, _Tally] = {}
_log_lock = threading.Lock()
_started = time.time()


def _record(component_id: str, host: str, allowed: bool, via: str) -> None:
    now = time.time()
    with _log_lock:
        tally = _tallies.setdefault(component_id, _Tally())
        if allowed:
            tally.allowed += 1
        else:
            tally.blocked += 1
        tally.last_at = now
        if host:
            tally.hosts[host] = now
            if len(tally.hosts) > 12:
                oldest = min(tally.hosts, key=tally.hosts.get)
                tally.hosts.pop(oldest, None)
        _log.append({"at": now, "component": component_id, "host": host, "allowed": allowed, "via": via})


def recent(limit: int = 50) -> list[dict[str, Any]]:
    with _log_lock:
        return list(_log)[-limit:][::-1]


def reset_log() -> None:
    with _log_lock:
        _log.clear()
        _tallies.clear()


def status() -> dict[str, Any]:
    current = mode()
    with _log_lock:
        tallies = {k: _Tally(v.allowed, v.blocked, dict(v.hosts), v.last_at) for k, v in _tallies.items()}
    rows = []
    for item in COMPONENTS:
        tally = tallies.pop(item.id, _Tally())
        rows.append({
            "id": item.id, "label": item.label, "sends": item.sends, "content": item.content, "note": item.note,
            "state": component_state(item.id, current), "override": _overrides().get(item.id, ""),
            "allowed": tally.allowed, "blocked": tally.blocked, "hosts": sorted(tally.hosts), "last_at": tally.last_at,
        })
    other = tallies.get("other", _Tally())
    return {
        "mode": current,
        "modes": list(MODES),
        "components": rows,
        "other": {"allowed": other.allowed, "blocked": other.blocked, "hosts": sorted(other.hosts)},
        "trusted_hosts": trusted_hosts(),
        "backstop": _installed,
        "not_enforced": list(NOT_ENFORCED),
        "since": _started,
        "updates_allowed": component_state("updates", current) == "allowed",
    }


def iter_components() -> Iterator[Component]:
    return iter(COMPONENTS)
