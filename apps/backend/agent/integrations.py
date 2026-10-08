"""Echo Connections: find, propose and check connections to other apps and services.

When a task needs an app EchoSpeak can't use yet (Blender, Premiere, OBS,
Stripe...), an agent can look for a way in, explain it, and propose it. It can
never install or approve one itself:

  find_integrations   setup guides (agent/integration_guides.py) plus the official
                      MCP registry. Read-only; results are outside content.
  propose_integration queues a proposal: what would run (with the version pinned),
                      what the user must do inside the app, which keys it needs
                      (names only). Nothing runs yet.
  (the user)          reviews it in Settings, types any keys there (stored with
                      Windows DPAPI like every other MCP secret) and approves.
                      Approval goes through the same MCP trust pin as any server
                      (agent/mcp_trust.py), so a later change needs approval again.
  check_integration   says whether it is waiting, running, or what went wrong and
                      what to try, and which of its tools can confirm it works.

Finding a server is not permission to run it. Every tool from an approved
connection still goes through the normal per-call approvals and Rule-of-Two
policy (agent/lean/approvals.py, agent/lean/policy.py).
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from loguru import logger

from agent.integration_guides import GUIDES, guide as get_guide

REGISTRY = "https://registry.modelcontextprotocol.io/v0.1"
NPM = "https://registry.npmjs.org"
PYPI = "https://pypi.org/pypi"
MAX_WAITING = 10
_CACHE_SECONDS = 600
_cache: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()

# Words that say "connect" rather than what to connect to.
_FILLER = {
    "connect", "connection", "connections", "integration", "integrations", "integrate", "mcp", "server", "servers",
    "app", "apps", "application", "plugin", "with", "the", "and", "for", "to", "my", "can", "you", "use", "using",
    "control", "tool", "tools", "into", "how", "set", "setup", "up", "want", "need", "echo", "work",
}

# Programs a launch needs, and how to tell the user to get them.
_RUNNERS = {
    "npx": ("Node.js", "Install Node.js LTS from nodejs.org, then restart EchoSpeak."),
    "uvx": ("uv", "Install uv (docs.astral.sh/uv), then restart EchoSpeak."),
}


# ── HTTP ────────────────────────────────────────────────────────────────

def _get_json(url: str, *, params: Optional[dict[str, Any]] = None, timeout: float = 12.0, attempts: int = 2) -> Any:
    """GET JSON from a fixed public catalog (registry, npm, PyPI), cached."""
    import requests

    from version import APP_VERSION

    key = url + "?" + json.dumps(params or {}, sort_keys=True)
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < _CACHE_SECONDS:
            return hit[1]
    last: Exception | None = None
    for _ in range(max(1, attempts)):
        try:
            response = requests.get(url, params=params, timeout=timeout,
                                    headers={"User-Agent": f"EchoSpeak/{APP_VERSION}", "Accept": "application/json"})
            if response.status_code == 404:
                return None
            response.raise_for_status()
            data = response.json()
            with _lock:
                _cache[key] = (time.time(), data)
            return data
        except Exception as exc:  # timeouts happen: the registry is a preview service
            last = exc
    raise RuntimeError(f"could not reach {url.split('/')[2]}: {type(last).__name__}")


# ── candidates ──────────────────────────────────────────────────────────

def _terms(query: str) -> list[str]:
    words = re.findall(r"[a-z0-9][a-z0-9+.#-]*", str(query or "").lower())
    out = [w for w in words if w not in _FILLER and len(w) >= 2]
    return list(dict.fromkeys(out))[:4]


def _hits(term: str, text: str) -> bool:
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text.lower()))


def _runner_state(command: str) -> tuple[list[str], list[str]]:
    """(needs, missing) for the program that starts a local server."""
    if command not in _RUNNERS:
        return [], []
    name, _ = _RUNNERS[command]
    return [name], ([] if shutil.which(command) else [name])


def _publisher(name: str, server: dict[str, Any]) -> str:
    """Who published a registry entry. The registry checks namespace ownership:
    io.github.<user>/ is a GitHub account; com.example/ proves control of example.com."""
    namespace = name.split("/", 1)[0]
    if namespace.startswith("io.github."):
        return f"community: GitHub user {namespace[len('io.github.'):]}"
    domain = ".".join(reversed(namespace.split(".")))
    return f"published by {domain} (domain verified by the registry)"


def _setting_rows(package: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    rows = []
    for item in package.get("environmentVariables" if kind == "env" else "headers") or []:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        rows.append({
            "name": name,
            "kind": kind,
            "secret": bool(item.get("isSecret")),
            "required": bool(item.get("isRequired")),
            "default": str(item.get("default") or ""),
            "about": str(item.get("description") or "")[:300],
        })
    return rows


def _launch_from_registry(server: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    """(launch, settings, unsupported_reason) for one registry server.json."""
    reasons = []
    for package in server.get("packages") or []:
        kind = str(package.get("registryType") or "").lower()
        ident = str(package.get("identifier") or "").strip()
        version = str(package.get("version") or server.get("version") or "").strip()
        transport = str((package.get("transport") or {}).get("type") or "stdio").lower()
        if transport != "stdio":
            reasons.append(f"{kind} package over {transport}")
            continue
        required_args = [a for a in package.get("packageArguments") or [] if a.get("isRequired") and not a.get("value")]
        if required_args:
            reasons.append(f"{ident} needs start-up arguments EchoSpeak can't fill in yet")
            continue
        if kind == "npm" and ident and version:
            return {"command": "npx", "args": ["-y", f"{ident}@{version}"]}, _setting_rows(package, "env"), ""
        if kind == "pypi" and ident and version:
            return {"command": "uvx", "args": [f"{ident}=={version}"]}, _setting_rows(package, "env"), ""
        reasons.append(f"a {kind or 'unknown'} package")
    for remote in server.get("remotes") or []:
        kind = str(remote.get("type") or "").lower()
        url = str(remote.get("url") or "").strip()
        if "{" in url:
            reasons.append("a hosted server whose address needs values filled in")
            continue
        transport = {"streamable-http": "streamable_http", "sse": "sse"}.get(kind)
        if transport and url.startswith("https://"):
            return {"transport": transport, "url": url}, _setting_rows(remote, "header"), ""
    why = ", ".join(dict.fromkeys(reasons)) or "no package or address"
    return {}, [], f"it ships as {why}, which EchoSpeak can't start yet"


def _candidate_from_registry(entry: dict[str, Any]) -> dict[str, Any]:
    server = entry.get("server") or entry
    name = str(server.get("name") or "")
    launch, settings, unsupported = _launch_from_registry(server)
    needs, missing = _runner_state(str(launch.get("command") or ""))
    repo = str((server.get("repository") or {}).get("url") or server.get("websiteUrl") or "")
    watch_out = ["Listed in the MCP registry; nobody has reviewed what it does. Read its page before approving."]
    if launch.get("url"):
        watch_out.append("If the service wants a browser sign-in, EchoSpeak can't do that yet.")
    return {
        "id": name,
        "title": str(server.get("title") or name.split("/")[-1]),
        "summary": " ".join(str(server.get("description") or "").split())[:240],
        "publisher": _publisher(name, server),
        "version": str(server.get("version") or ""),
        "launch": launch,
        "unsupported": unsupported,
        "needs": needs,
        "missing": missing,
        "settings": settings,
        "steps": [],
        "watch_out": watch_out,
        "verify": "",
        "capability_policies": {},
        "homepage": repo,
        "field": "",
        "source": "registry",
    }


def _guide_summary(item: dict[str, Any]) -> dict[str, Any]:
    """A guide as a search result (version resolved only when proposed)."""
    how = item["how"]
    launch: dict[str, Any] = {}
    unsupported = ""
    if how["kind"] == "npm":
        launch = {"command": "npx", "args": ["-y", how["package"]]}
    elif how["kind"] == "pypi":
        launch = {"command": "uvx", "args": [how["package"]]}
    elif how["kind"] == "http":
        launch = {"transport": "streamable_http", "url": how["url"]}
    elif how["kind"] == "signin":
        unsupported = "it needs a browser sign-in, which EchoSpeak can't do yet"
    elif how["kind"] == "builtin":
        unsupported = f"EchoSpeak already has this: {how['where']}"
    _, missing = _runner_state(str(launch.get("command") or ""))
    return {
        "id": f"guide:{item['id']}",
        "title": item["app"],
        "summary": f"{item['app']} for {item['field']}.",
        "publisher": item["publisher"],
        "version": "",
        "launch": launch,
        "unsupported": unsupported,
        "needs": list(item.get("needs", [])),
        "missing": missing,
        "settings": [dict(s, kind=s.get("kind", "env")) for s in item.get("settings", [])],
        "steps": list(item.get("steps", [])),
        "watch_out": list(item.get("watch_out", [])),
        "verify": item.get("verify", ""),
        "capability_policies": dict(item.get("capability_policies") or {}),
        "homepage": item.get("homepage", ""),
        "field": item["field"],
        "source": "guide",
    }


def _score(candidate: dict[str, Any], terms: list[str], keywords: list[str] = ()) -> int:
    # The whole id: an app's own company often names its server just "mcp" (com.figma.mcp/mcp).
    name = candidate["id"].replace("guide:", "").replace("/", " ").replace(".", " ")
    score = 0
    for term in terms:
        score += 6 * any(_hits(term, k) for k in keywords)
        score += 4 * _hits(term, name.replace("-", " ") + " " + name)
        score += 2 * _hits(term, candidate["title"])
        score += 1 * _hits(term, candidate["summary"])
        if "domain verified" in candidate["publisher"] and _hits(term, candidate["publisher"].replace(".", " ")):
            score += 6  # the app's own company published it
    if score and not candidate["unsupported"]:
        score += 1
    return score


def _names_app(candidate: dict[str, Any], term: str, keywords: list[str] = ()) -> bool:
    """Does the candidate mention ``term`` (usually the app's name) at all?"""
    text = " ".join([candidate["id"].replace("/", " ").replace(".", " "), candidate["title"], candidate["summary"]])
    return any(_hits(term, k) for k in keywords) or _hits(term, text)


def search(query: str, limit: int = 6) -> dict[str, Any]:
    """Guides and registry servers matching ``query``, best first.

    The first meaningful word is usually the app ("blender", "obs"): results must
    mention it, so a broad word like "payments" or "music" can't pull in unrelated
    listings. Other words only order the results.
    """
    terms = _terms(query)
    if not terms:
        return {"terms": [], "results": [], "error": "Say which app or service to connect to."}
    primary = terms[0]
    scored: list[tuple[int, bool, dict[str, Any]]] = []
    for item in GUIDES:
        candidate = _guide_summary(item)
        score = _score(candidate, terms, item.get("keywords", []))
        if score:  # reviewed guides first
            scored.append((score + 10, _names_app(candidate, primary, item.get("keywords", [])), candidate))
    seen = {c["id"] for _, _, c in scored}
    covered_ids = {g["how"].get("registry") for g in GUIDES}
    covered_urls = {g["how"].get("url", "").rstrip("/") for g in GUIDES if g["how"].get("url")}
    error = ""
    for term in terms[:2]:
        try:
            data = _get_json(f"{REGISTRY}/servers", params={"search": term, "version": "latest", "limit": 40},
                             timeout=15.0, attempts=1) or {}
        except RuntimeError as exc:
            error = str(exc)
            break  # the registry is a preview service and sometimes stalls: don't wait twice
        for entry in data.get("servers") or []:
            status = ((entry.get("_meta") or {}).get("io.modelcontextprotocol.registry/official") or {}).get("status")
            if status and status != "active":
                continue
            candidate = _candidate_from_registry(entry)
            url = str(candidate["launch"].get("url") or "").rstrip("/")
            if candidate["id"] in seen or candidate["id"] in covered_ids or (url and url in covered_urls):
                continue  # a guide already covers it, with the steps the registry lacks
            score = _score(candidate, terms)
            if score:
                seen.add(candidate["id"])
                scored.append((score, _names_app(candidate, primary), candidate))
    if any(named for _, named, _ in scored):
        scored = [row for row in scored if row[1]]
    scored.sort(key=lambda row: -row[0])
    return {"terms": terms, "results": [c for _, _, c in scored[:limit]], "error": error}


def _latest_version(how: dict[str, Any]) -> str:
    if how.get("registry"):
        data = _get_json(f"{REGISTRY}/servers/{quote(how['registry'], safe='')}/versions/latest") or {}
        for package in (data.get("server") or {}).get("packages") or []:
            if package.get("identifier") == how["package"] and package.get("version"):
                return str(package["version"])
    if how["kind"] == "npm":
        return str((_get_json(f"{NPM}/{quote(how['package'], safe='@')}/latest") or {}).get("version") or "")
    if how["kind"] == "pypi":
        return str(((_get_json(f"{PYPI}/{quote(how['package'])}/json") or {}).get("info") or {}).get("version") or "")
    return ""


def resolve(candidate_id: str) -> dict[str, Any]:
    """Full details for one candidate, with the version pinned. Raises ValueError."""
    candidate_id = str(candidate_id or "").strip()
    if candidate_id.startswith("guide:"):
        item = get_guide(candidate_id[len("guide:"):])
        if item is None:
            raise ValueError(f"No setup guide called {candidate_id}.")
        candidate = _guide_summary(item)
        how = item["how"]
        if how["kind"] in {"npm", "pypi"}:
            version = _latest_version(how)
            if not version:
                raise ValueError(f"Couldn't find the current version of {how['package']}.")
            candidate["version"] = version
            candidate["launch"] = ({"command": "npx", "args": ["-y", f"{how['package']}@{version}"]} if how["kind"] == "npm"
                                   else {"command": "uvx", "args": [f"{how['package']}=={version}"]})
            if how.get("registry"):
                # Settings the package itself declares, after the guide's own.
                data = _get_json(f"{REGISTRY}/servers/{quote(how['registry'], safe='')}/versions/latest") or {}
                for package in (data.get("server") or {}).get("packages") or []:
                    if package.get("identifier") == how["package"]:
                        names = {s["name"] for s in candidate["settings"]}
                        candidate["settings"] += [s for s in _setting_rows(package, "env") if s["name"] not in names]
        return candidate
    if not re.fullmatch(r"[A-Za-z0-9.-]+/[A-Za-z0-9._-]+", candidate_id):
        raise ValueError("Use an id exactly as find_integrations gave it.")
    data = _get_json(f"{REGISTRY}/servers/{quote(candidate_id, safe='')}/versions/latest")
    if not data:
        raise ValueError(f"{candidate_id} isn't in the MCP registry.")
    return _candidate_from_registry(data)


# ── proposals ───────────────────────────────────────────────────────────

def _proposals_path() -> Path:
    from config import DATA_DIR

    path = Path(DATA_DIR) / "connections" / "proposals.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load() -> list[dict[str, Any]]:
    try:
        data = json.loads(_proposals_path().read_text(encoding="utf-8"))
        return [p for p in data.get("proposals", []) if isinstance(p, dict)]
    except (OSError, ValueError):
        return []


def _save(items: list[dict[str, Any]]) -> None:
    path = _proposals_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"proposals": items}, indent=2), encoding="utf-8")
    tmp.replace(path)


def proposals(status: str = "") -> list[dict[str, Any]]:
    with _lock:
        return [p for p in _load() if not status or p.get("status") == status]


def get_proposal(proposal_id: str) -> Optional[dict[str, Any]]:
    return next((p for p in proposals() if p.get("id") == proposal_id), None)


def _server_name(candidate: dict[str, Any], taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9_-]+", "-", candidate["id"].replace("guide:", "").split("/")[-1].lower()).strip("-") or "app"
    base = re.sub(r"(^mcp-|-mcp(-server)?$|-server$)", "", base) or base
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    return name


def _same_launch(raw: dict[str, Any], launch: dict[str, Any]) -> bool:
    if launch.get("url"):
        return str(raw.get("url") or raw.get("endpoint") or "").rstrip("/") == launch["url"].rstrip("/")
    return str(raw.get("command") or "") == launch.get("command") and list(raw.get("args") or []) == launch.get("args")


def propose(candidate_id: str, *, reason: str = "", session_id: str = "",
            configured: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Queue a connection for the user to review. Returns the proposal; raises ValueError."""
    candidate = resolve(candidate_id)
    if candidate["unsupported"]:
        raise ValueError(f"{candidate['title']} can't be set up from here: {candidate['unsupported']}.")
    servers = dict(configured or {})
    for name, raw in servers.items():
        if isinstance(raw, dict) and _same_launch(raw, candidate["launch"]):
            raise ValueError(f"It's already set up as the connection \"{name}\". Use check_integration to see how it's doing.")
    with _lock:
        items = _load()
        waiting = [p for p in items if p.get("status") == "waiting"]
        existing = next((p for p in waiting if p.get("source_id") == candidate["id"]), None)
        if existing is None and len(waiting) >= MAX_WAITING:
            raise ValueError(f"{MAX_WAITING} suggestions are already waiting in Settings. Ask the user to review those first.")
        taken = set(servers) | {p.get("server_name") for p in items if p.get("status") in {"waiting", "approved"}}
        proposal = existing or {"id": f"prop_{uuid.uuid4().hex[:10]}", "created_at": time.time(), "status": "waiting"}
        if existing is None:
            proposal["server_name"] = _server_name(candidate, taken)
        proposal.update({
            "source_id": candidate["id"],
            "title": candidate["title"],
            "summary": candidate["summary"],
            "publisher": candidate["publisher"],
            "version": candidate["version"],
            "launch": candidate["launch"],
            "settings": candidate["settings"],
            "needs": candidate["needs"],
            "missing": candidate["missing"],
            "steps": candidate["steps"],
            "watch_out": candidate["watch_out"],
            "verify": candidate["verify"],
            "capability_policies": candidate["capability_policies"],
            "homepage": candidate["homepage"],
            "reason": " ".join(str(reason or "").split())[:300],
            "session_id": session_id,
            "updated_at": time.time(),
        })
        if existing is None:
            items.append(proposal)
        _save(items)
    return proposal


def server_config(proposal: dict[str, Any], values: dict[str, str]) -> dict[str, Any]:
    """The mcp_servers entry for an approved proposal. ``values`` are what the user typed
    in Settings for the proposal's settings; unknown names are refused."""
    settings = {s["name"]: s for s in proposal.get("settings") or []}
    unknown = sorted(set(values) - set(settings))
    if unknown:
        raise ValueError(f"Not a setting of this connection: {', '.join(unknown)}")
    env: dict[str, str] = {}
    headers: dict[str, str] = {}
    for name, spec in settings.items():
        value = str(values.get(name) or "").strip() or str(spec.get("default") or "")
        if not value:
            if spec.get("required"):
                raise ValueError(f"{name} is required.")
            continue
        if spec.get("template"):
            value = spec["template"].replace("{value}", value)
        (headers if spec.get("kind") == "header" else env)[name] = value
    launch = dict(proposal.get("launch") or {})
    config: dict[str, Any] = {"enabled": True, "connection_id": f"mcp-{proposal['server_name']}",
                              "capability_policies": dict(proposal.get("capability_policies") or {})}
    if launch.get("url"):
        config.update(transport=launch.get("transport") or "streamable_http", url=launch["url"])
        if headers:
            config["headers"] = headers
    else:
        command = str(launch.get("command") or "")
        # npx / uvx are .cmd / .exe shims on Windows: start the resolved file.
        config.update(transport="stdio", command=shutil.which(command) or command, args=list(launch.get("args") or []))
    if env:
        config["env"] = env
    return config


def mark(proposal_id: str, status: str, **fields: Any) -> Optional[dict[str, Any]]:
    with _lock:
        items = _load()
        for item in items:
            if item.get("id") == proposal_id:
                item.update(status=status, updated_at=time.time(), **fields)
                _save(items)
                return item
    return None


def note_connected(server_name: str, tool_count: int) -> None:
    """Remember that an approved proposal's server answered: setup knowledge for next time."""
    with _lock:
        items = _load()
        changed = False
        for item in items:
            if item.get("server_name") == server_name and item.get("status") == "approved" and not item.get("connected_at"):
                item.update(connected_at=time.time(), tool_count=tool_count)
                changed = True
        if changed:
            _save(items)


# ── checking a connection ───────────────────────────────────────────────

_VERIFY_TOOL = re.compile(r"(?i)(ping|verify|health|status|doctor|capabilit|info|version|list_|get_)")


def _hint(error: str, guide: Optional[dict[str, Any]], command: str = "") -> str:
    low = (error or "").lower()
    if any(s in low for s in ("enoent", "no such file", "not found", "cannot find", "is not recognized")):
        runner = Path(command).stem.lower() if command else ""
        if runner in _RUNNERS and not shutil.which(runner):
            name, fix = _RUNNERS[runner]
            return f"{name} doesn't seem to be installed. {fix}"
        return "The program that starts it wasn't found. Check the project page for what to install."
    if any(s in low for s in ("refused", "econnrefused", "10061", "connect call failed", "unreachable")):
        app = guide["app"] if guide else "the app"
        step = f" ({guide['steps'][-1]})" if guide and guide.get("steps") else ""
        return f"{app} isn't answering. Open it and start its connection{step}."
    if "timed out" in low or "timeout" in low:
        return ("It didn't answer in time. The first start downloads the package, so try again in a minute; "
                "if it keeps happening, check the internet connection.")
    if any(s in low for s in ("401", "403", "unauthorized", "forbidden", "invalid api key", "authentication")):
        return "The service refused the key. Re-enter it in Settings > Connections, or create a new one."
    return "Open the project page for its troubleshooting steps." if error else ""


def check(name: str = "", *, status: Optional[dict[str, Any]] = None, registry_tools: Optional[dict[str, Any]] = None) -> str:
    """A plain report on one connection (or all of them)."""
    rows = list((status or {}).get("servers") or [])
    waiting = proposals("waiting")
    wanted = str(name or "").strip().lower()
    report: list[str] = []
    for proposal in waiting:
        if wanted and wanted not in {proposal["server_name"], proposal["id"], str(proposal["title"]).lower()}:
            continue
        report.append(f"{proposal['title']} (\"{proposal['server_name']}\"): waiting for the user to review and approve it "
                      "in Settings > Advanced > Connections & skills. Nothing runs until they do.")
    for row in rows:
        server = str(row.get("name") or "")
        if wanted and wanted != server.lower():
            continue
        proposal = next((p for p in proposals() if p.get("server_name") == server), None)
        guide = get_guide(str(proposal.get("source_id", "")).replace("guide:", "")) if proposal else None
        if row.get("approval"):
            report.append(f"{server}: waiting for approval in Settings ({row.get('approval')}). {row.get('last_error') or ''}".strip())
            continue
        if row.get("running") and int(row.get("tool_count") or 0) > 0:
            # Registered names are mcp__<server>__<tool> (agent/mcp_client.py re_sub_safe).
            prefix = "mcp__" + (re.sub(r"[^a-zA-Z0-9_]+", "_", server).strip("_") or "unnamed") + "__"
            tools = sorted(n for n in (registry_tools or {}) if n.startswith(prefix))
            # Read-only tools can confirm it works without changing anything; ping/status-like names first.
            readonly = sorted((n for n in tools if not getattr((registry_tools or {}).get(n), "is_action", True)),
                              key=lambda n: (not _VERIFY_TOOL.search(n.split("__")[-1]), n))
            line = f"{server}: connected, {row.get('tool_count')} tools."
            if readonly:
                line += f" To confirm it really works, call a read-only one: {', '.join(readonly[:4])}."
            elif guide and guide.get("verify"):
                line += f" To confirm it works: {guide['verify']}"
            if tools:
                line += f" Tools: {', '.join(t.split('__')[-1] for t in tools[:20])}" + (" ..." if len(tools) > 20 else "")
            note_connected(server, int(row.get("tool_count") or 0))
            report.append(line)
            continue
        error = str(row.get("last_error") or "")
        state = "not running" if row.get("enabled", True) else "turned off"
        hint = _hint(error, guide, str((proposal or {}).get("launch", {}).get("command") or ""))
        report.append(f"{server}: {state}." + (f" Error: {error[:300]}." if error else "") + (f" Try: {hint}" if hint else ""))
    if not report:
        target = f"\"{name}\"" if name else "anything"
        return (f"No connection or suggestion matches {target}. Use find_integrations to look for one, "
                "or ask the user which app they mean.")
    return "\n".join(report)


# ── agent tools ─────────────────────────────────────────────────────────

def _format_results(found: dict[str, Any]) -> str:
    if not found["results"]:
        lines = [f"Nothing found for {', '.join(found['terms'])}."]
        if found.get("error"):
            lines.append(f"(The MCP registry didn't answer: {found['error']}.)")
        lines.append("Other ways in, depending on the app: its own command line or scripting (many apps run scripts "
                     "from the terminal), a web API, the desktop tools (click and type), or an Agent Skill the user "
                     "imports. Explain the options to the user and ask which they prefer.")
        return "\n".join(lines)
    out = []
    for c in found["results"]:
        lines = [f"- id: {c['id']}  ({c['title']}; {c['publisher']})"]
        if c["summary"]:
            lines.append(f"  {c['summary']}")
        if c["unsupported"]:
            lines.append(f"  Can't be set up from EchoSpeak: {c['unsupported']}.")
        else:
            runs = c["launch"].get("url") or " ".join([c["launch"].get("command", "")] + c["launch"].get("args", []))
            lines.append(f"  Runs: {runs}" + (f" (version {c['version']})" if c["version"] else " (version pinned when proposed)"))
        if c["needs"]:
            lines.append("  Needs: " + ", ".join(c["needs"]) + (f". Not installed here: {', '.join(c['missing'])}" if c["missing"] else ""))
        keys = [s["name"] + (" (secret)" if s.get("secret") else "") for s in c["settings"] if s.get("required")]
        if keys:
            lines.append("  The user enters in Settings: " + ", ".join(keys))
        if c["steps"]:
            lines.append("  The user does: " + " / ".join(c["steps"]))
        if c["watch_out"]:
            lines.append("  Watch out: " + " ".join(c["watch_out"]))
        if c["homepage"]:
            lines.append(f"  Page: {c['homepage']}")
        out.append("\n".join(lines))
    tail = ("\nTell the user the best option, what it needs from them and anything to watch out for. "
            "If they want it, call propose_integration with its id. Finding it doesn't install it.")
    if found.get("error"):
        tail += f"\n(The MCP registry didn't answer, so only setup guides are shown: {found['error']}.)"
    return "\n".join(out) + tail


def integration_tools(session_id: str = "") -> list[Any]:
    from agent.lean.toolbox import NativeTool

    def find(args: dict[str, Any]) -> str:
        query = str(args.get("query") or args.get("app") or "")[:120]
        return _format_results(search(query))

    def propose_tool(args: dict[str, Any]) -> str:
        from config import config

        try:
            proposal = propose(str(args.get("id") or ""), reason=str(args.get("reason") or ""), session_id=session_id,
                               configured=dict(getattr(config, "mcp_servers", None) or {}))
        except (ValueError, RuntimeError) as exc:
            return f"Error: {exc}"
        keys = [s["name"] for s in proposal["settings"] if s.get("required")]
        lines = [f"Suggested \"{proposal['title']}\" as the connection \"{proposal['server_name']}\" "
                 f"({'version ' + proposal['version'] if proposal['version'] else proposal['launch'].get('url', '')})."
                 " It is waiting in Settings > Advanced > Connections & skills; nothing is installed until the user approves it there."]
        if keys:
            lines.append(f"The user types {', '.join(keys)} in that card. Never ask them to paste keys into the chat.")
        if proposal["missing"]:
            lines.append(f"This PC is missing {', '.join(proposal['missing'])}; tell the user how to install it first.")
        if proposal["steps"]:
            lines.append("Steps for the user inside the app: " + " / ".join(proposal["steps"]))
        lines.append("After they approve, call check_integration to confirm it works.")
        return "\n".join(lines)

    def check_tool(args: dict[str, Any]) -> str:
        from agent.mcp_client import get_mcp_manager
        from agent.tool_registry import ToolRegistry

        return check(str(args.get("name") or ""), status=get_mcp_manager().status(), registry_tools=ToolRegistry.get_all())

    return [
        NativeTool(
            name="find_integrations",
            description=(
                "Find a way to connect EchoSpeak to an app or service it can't use yet (Blender, Photoshop, Premiere, "
                "OBS, Figma, Stripe, GitHub, KiCad...). Searches EchoSpeak's setup guides and the official MCP registry, "
                "and says what each option needs. Use it when a task needs an app none of your tools reach. Results "
                "come from outside sources; finding one installs nothing."
            ),
            parameters={"type": "object", "properties": {
                "query": {"type": "string", "description": "The app or service, e.g. \"Blender\" or \"OBS streaming\"."},
            }, "required": ["query"]},
            func=find,
            parallel_safe=True,
        ),
        NativeTool(
            name="propose_integration",
            description=(
                "Suggest a connection found with find_integrations to the user. It appears in Settings for them to "
                "review, type any keys and approve; you can't install or approve it yourself. Only after the user "
                "says they want it."
            ),
            parameters={"type": "object", "properties": {
                "id": {"type": "string", "description": "The id from find_integrations."},
                "reason": {"type": "string", "description": "One line on what the user wants to do with it."},
            }, "required": ["id"]},
            func=propose_tool,
        ),
        NativeTool(
            name="check_integration",
            description=(
                "Whether a connection works: waiting for approval, running and its tools, or what went wrong and what to "
                "try. Use after the user approves one, or when its tools fail."
            ),
            parameters={"type": "object", "properties": {
                "name": {"type": "string", "description": "Connection name; empty for all of them."},
            }},
            func=check_tool,
            parallel_safe=True,
        ),
    ]
