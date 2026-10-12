"""The privacy check: is the chosen privacy mode really what this setup does?

Settings say what should happen; this looks at what will. It resolves where
each configured model, search service, channel and hosted connection actually
points, probes the ones on your own machines (does LM Studio answer? does your
SearXNG return results?), and lists anything that would leave your machines in
the current mode. It sends nothing to the internet itself: probes only go to
hosts that are this PC, your network or hosts you trust.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from agent import privacy

OK, WARN, BLOCKED, INFO = "ok", "warn", "blocked", "info"
_WHERE = {"this_pc": "this PC", "your_network": "your network", "trusted": "a host you trust",
          "internet": "the internet", "unknown": "an address that didn't resolve"}


def _item(id_: str, label: str, status: str, detail: str, fix: str = "") -> dict[str, Any]:
    return {"id": id_, "label": label, "status": status, "detail": detail, "fix": fix}


def _probe(url: str, *, params: Optional[dict[str, Any]] = None, timeout: float = 4.0) -> tuple[bool, Any, str]:
    """GET a URL on your own machines. (ok, json-or-None, error)."""
    import httpx

    try:
        response = httpx.get(url, params=params, timeout=timeout, follow_redirects=False)
        if response.status_code >= 400:
            return False, None, f"HTTP {response.status_code}"
        try:
            return True, response.json(), ""
        except ValueError:
            return True, None, ""
    except Exception as exc:  # refused, timed out, DNS
        return False, None, type(exc).__name__


def _endpoint(provider: str, model_id: str) -> tuple[str, str]:
    """(base URL, model) the way agent/lean/provider.resolve_endpoint picks them, without its
    "first loaded model" lookup: one slow LM Studio probe per agent made the check crawl."""
    from agent.cloud_providers import BASE_URLS, CLOUD_PROVIDERS, cloud_config
    from agent.model_runtime import resolve_local_provider_base_url
    from config import ModelProvider, config

    if provider in CLOUD_PROVIDERS:
        return BASE_URLS[provider], model_id or str(cloud_config(provider).model or "")
    base = resolve_local_provider_base_url(ModelProvider(provider), str(config.local.base_url or "")).rstrip("/")
    return (base if base.endswith("/v1") else f"{base}/v1"), model_id or str(config.local.model_name or "")


def _model_items() -> list[dict[str, Any]]:
    from agent.lean.personas import get_persona_store

    from agent.cloud_providers import default_cloud_provider
    from config import config

    # The provider the next chat would use (api/routes/settings._resolve_runtime_provider), without
    # importing the API layer, which pulls in heavy model libraries.
    local = getattr(config.local, "provider", "lmstudio")
    default_provider = (str(getattr(local, "value", local)) if getattr(config, "use_local_models", True)
                        else default_cloud_provider())
    seen: dict[tuple[str, str], list[str]] = {}
    for persona in get_persona_store().list():
        provider = persona.model.provider or default_provider
        seen.setdefault((provider, persona.model.model_id or ""), []).append(persona.name)
    items = []
    for (provider, model_id), names in seen.items():
        who = ", ".join(names)
        try:
            base_url, model = _endpoint(provider, model_id)
        except Exception as exc:
            items.append(_item(f"model:{provider}", f"Model for {who}", WARN, f"Not usable: {exc}"))
            continue
        where = privacy.locality(base_url)
        decision = privacy.decide("models", base_url)
        label = f"Model for {who}: {model or provider}"
        if not decision.allowed:
            items.append(_item(f"model:{provider}", label, BLOCKED, decision.reason,
                               "Pick a local model for these agents in Settings › Agents or Settings › Models."))
            continue
        if where in {"this_pc", "your_network", "trusted"}:
            ok, data, error = _probe(base_url.rstrip("/") + "/models", timeout=2.0)
            if ok:
                items.append(_item(f"model:{provider}", label, OK, f"Runs on {_WHERE[where]} and answered."))
            else:
                items.append(_item(f"model:{provider}", label, WARN, f"Runs on {_WHERE[where]} but didn't answer ({error}).",
                                   "Start LM Studio or Ollama and load a model."))
        else:
            items.append(_item(f"model:{provider}", label, INFO,
                               f"Runs on {_WHERE[where]}: conversations go to {provider}. Allowed in {privacy.mode().title()} mode."))
    return items


def _search_items() -> list[dict[str, Any]]:
    from config import config

    items = []
    searx = str(getattr(config, "searxng_base_url", "") or "").strip()
    if searx:
        decision = privacy.decide("search", searx)
        where = _WHERE.get(decision.locality, decision.locality)
        if not decision.allowed:
            items.append(_item("search:searxng", "Your SearXNG", BLOCKED, decision.reason,
                               "Add its host to trusted hosts in Settings › Privacy if it's your own server."))
        else:
            ok, data, error = _probe(searx.rstrip("/") + "/search", params={"q": "echospeak", "format": "json"})
            count = len((data or {}).get("results") or []) if isinstance(data, dict) else 0
            if ok and isinstance(data, dict):
                items.append(_item("search:searxng", "Your SearXNG", OK,
                                   f"On {where}; answered a test search with {count} results."))
            elif ok:
                items.append(_item("search:searxng", "Your SearXNG", WARN, f"On {where}, but it didn't return JSON.",
                                   "Enable the json format in SearXNG's settings.yml (search: formats: - json)."))
            else:
                items.append(_item("search:searxng", "Your SearXNG", WARN, f"On {where}, but it didn't answer ({error}).",
                                   "Check that SearXNG is running and the address in Settings › Web search is right."))
    cloud = [("DuckDuckGo", "https://duckduckgo.com", True)]
    for label, key, url in (("Brave Search", "brave_search_api_key", "https://api.search.brave.com"),
                            ("Tavily", "tavily_api_key", "https://api.tavily.com")):
        if str(getattr(config, key, "") or "").strip():
            cloud.append((label, url, True))
    for label, url, _ in cloud:
        decision = privacy.decide("search", url)
        if decision.allowed:
            items.append(_item(f"search:{label.lower()}", label, INFO,
                               "Gets your search words when agents search the web."))
        else:
            items.append(_item(f"search:{label.lower()}", label, BLOCKED, "Not used in this mode."))
    if not searx and not any(privacy.decide("search", url).allowed for _, url, _ in cloud):
        items.append(_item("search:none", "Web search", WARN, "No search service is usable in this mode.",
                           "Run your own SearXNG and add its address in Settings › Web search."))
    return items


def _channel_items() -> list[dict[str, Any]]:
    from config import config

    configured = [label for label, flag in (("Discord", "allow_discord_bot"), ("Telegram", "allow_telegram_bot"),
                                            ("Twitch", "allow_twitch"), ("X", "allow_twitter"))
                  if bool(getattr(config, flag, False))]
    if not configured:
        return []
    allowed = privacy.decide("channels").allowed
    return [_item("channels", "Chat channels: " + ", ".join(configured), INFO if allowed else BLOCKED,
                  "Messages go through those services." if allowed else "Not started in this mode.",
                  "" if allowed else "Allow chat channels in Settings › Privacy if you want them anyway.")]


def _connection_items() -> list[dict[str, Any]]:
    from config import config

    servers = dict(getattr(config, "mcp_servers", None) or {})
    hosted, local = [], []
    for name, raw in servers.items():
        if not isinstance(raw, dict) or raw.get("enabled") is False:
            continue
        url = str(raw.get("url") or raw.get("endpoint") or "")
        (hosted if url else local).append((name, url))
    items = []
    for name, url in hosted:
        decision = privacy.decide("remote_connections", url)
        items.append(_item(f"mcp:{name}", f"Connection: {name}", INFO if decision.allowed else BLOCKED,
                           f"Hosted on {_WHERE.get(decision.locality, decision.locality)}."
                           if decision.allowed else decision.reason))
    if local:
        items.append(_item("mcp:local", f"Connections that run on this PC: {', '.join(n for n, _ in local)}", WARN,
                           "These are programs EchoSpeak starts. Private mode can't see what they send themselves.",
                           "Only approve servers you trust; Offline mode doesn't change what these programs do."))
    return items


def _keys_item() -> list[dict[str, Any]]:
    from agent.cloud_providers import CLOUD_LABELS, CLOUD_PROVIDERS, cloud_config

    saved = [CLOUD_LABELS.get(p, p) for p in CLOUD_PROVIDERS if str(cloud_config(p).api_key or "").strip()]
    if not saved:
        return []
    if privacy.decide("models").allowed:
        return [_item("keys", "Saved cloud keys", INFO, f"{', '.join(saved)} can be used in this mode.")]
    return [_item("keys", "Saved cloud keys", OK, f"{', '.join(saved)} are saved but not used in this mode.")]


def run() -> dict[str, Any]:
    started = time.time()
    current = privacy.mode()
    items: list[dict[str, Any]] = []
    for build in (_model_items, _search_items, _channel_items, _connection_items, _keys_item):
        try:
            items.extend(build())
        except Exception as exc:  # a broken part shouldn't hide the rest
            items.append(_item(build.__name__.strip("_"), build.__name__.strip("_").replace("_", " "), WARN,
                               f"Couldn't check: {type(exc).__name__}"))
    for component_id, label in (("web_pages", "Reading web pages"), ("updates", "Update check"),
                                ("downloads", "Model downloads"), ("connections_catalog", "Finding app connections")):
        state = privacy.component_state(component_id, current)
        items.append(_item(component_id, label, INFO if state == "allowed" else BLOCKED,
                           privacy.component(component_id).sends + (" leaves your machines." if state == "allowed"
                                                                    else ": off in this mode.")))
    status = privacy.status()
    blocked = sum(row["blocked"] for row in status["components"]) + status["other"]["blocked"]
    items.append(_item("backstop", "Backstop", OK if status["backstop"] else WARN,
                       ("Active: refuses internet connections nothing approved. " if status["backstop"] else
                        "Not active in this process. ") + f"{blocked} refused since EchoSpeak started."))
    leaks = [i for i in items if i["status"] == INFO and current != "standard"]
    problems = [i for i in items if i["status"] == WARN]
    if current == "standard":
        verdict = "Standard mode: everything works as configured. Switch to Private or Offline to restrict it."
    elif leaks:
        verdict = f"{len(leaks)} part(s) still reach outside your machines in {current.title()} mode; each is listed."
    elif current == "offline":
        verdict = "Offline: EchoSpeak itself reaches nothing outside this PC and your network."
    else:
        verdict = "Private: nothing sends your content outside your own machines."
    return {
        "mode": current,
        "verdict": verdict,
        "problems": len(problems),
        "items": items,
        "not_enforced": status["not_enforced"],
        "checked_at": started,
        "took_ms": int((time.time() - started) * 1000),
    }
