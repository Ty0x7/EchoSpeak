"""Echo Companion's own SOUL: its personality and phrases, separate from Echo's SOUL.

The owner edits ``DATA_DIR/companion/SOUL.md`` (or Settings › Echo Companion). It is
plain Markdown with three sections:

- ``## Personality``: free text that guides the optional local-model wording.
- ``## Phrases``: ``situation: option | option``. Placeholders are {agent}, {where},
  {project} and {headline}.
- ``## Never``: phrases the companion must not say.

The companion only reads this file. Nothing an agent reports can change it, and it
never touches Echo's SOUL, permissions or instructions.

Wording has two modes (``companion_soul_style``):

- **templates** (default, no model call): commentary picks from the SOUL's phrases.
- **local_model**: the owner's *local* model rewords the grounded line in this
  personality. ``fact_check`` rejects any rewrite that adds a number, reference,
  agent or claim ("verified", "saved", "merged") that wasn't in the grounded line.
  Rejected rewrites, timeouts and non-local providers all fall back to the template.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

from config import DATA_DIR, config

logger = logging.getLogger(__name__)

DEFAULT_SOUL = """# Echo Companion

## Personality
Warm, observant and a little dry. Speaks like a teammate glancing over from the next desk:
short, plain sentences, never hype. Credits the agent that did the work by name. Jokes only
when the facts give it something to work with, and never about the owner.

## Phrases
success: Report in from {agent}{where}. | {agent} wrapped up{where}. | {agent} finished a report{where}.
failure: That didn't work, but we learned something. On to the next one. | Not this time. The notes are worth keeping, though.
all_green: Everything passed, which is honestly a little suspicious.
saved: I saved the key points for Echo. | Echo can use what it found from now on. | The important parts are saved for Echo.
needs_you: {agent} is waiting for your permission{where}.
question: {agent} has a question for you{where}.

## Never
"""

_CLAIMS = ("verified", "confirmed", "saved", "merged", "installed", "deployed", "released", "shipped", "fixed")
_cache: dict[str, Any] = {"mtime": -1.0, "soul": None}


def soul_path() -> Path:
    return Path(DATA_DIR) / "companion" / "SOUL.md"


def parse(text: str) -> dict[str, Any]:
    sections: dict[str, list[str]] = {}
    current = ""
    for line in str(text or "").splitlines():
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            current = heading.group(1).strip().lower()
            sections.setdefault(current, [])
        elif current:
            sections[current].append(line)
    phrases: dict[str, list[str]] = {}
    for line in sections.get("phrases", []):
        match = re.match(r"^\s*([a-z_]+)\s*:\s*(.+)$", line)
        if match:
            options = [o.strip() for o in match.group(2).split("|") if o.strip()]
            if options:
                phrases[match.group(1)] = options[:12]
    never = [l.strip(" -*") for l in sections.get("never", []) if l.strip(" -*")]
    return {"personality": "\n".join(sections.get("personality", [])).strip()[:1500], "phrases": phrases,
            "never": never[:30]}


def load() -> dict[str, Any]:
    """The owner's SOUL (created from the default on first use), cached by mtime."""
    path = soul_path()
    try:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(DEFAULT_SOUL, encoding="utf-8")
        mtime = path.stat().st_mtime
        if _cache["mtime"] != mtime:
            _cache.update(mtime=mtime, soul=parse(path.read_text(encoding="utf-8")[:20000]))
        return _cache["soul"]
    except OSError:
        return parse(DEFAULT_SOUL)


def save(text: str) -> dict[str, Any]:
    path = soul_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(text)[:20000], encoding="utf-8")
    return load()


def phrase(name: str, seed: str, default: str, **values: str) -> str:
    import hashlib

    options = load().get("phrases", {}).get(name) or [default]
    chosen = options[int(hashlib.sha256((seed + name).encode()).hexdigest(), 16) % len(options)]
    try:
        return chosen.format(**values)
    except (KeyError, IndexError, ValueError):
        return default.format(**values) if "{" in default else default


def allowed(text: str) -> bool:
    lowered = text.lower()
    return not any(n.lower() in lowered for n in load().get("never", []) if n)


def _facts(text: str) -> dict[str, set[str]]:
    lowered = text.lower()
    return {
        "numbers": set(re.findall(r"\d+(?:[.,]\d+)?", text)),
        "claims": {c for c in _CLAIMS if re.search(rf"\b{c}\b", lowered)},
    }


def fact_check(grounded: str, rewrite: str, agent_label: str) -> tuple[bool, str]:
    """The rewrite may drop details but must not add any."""
    if not rewrite or len(rewrite) > max(320, int(len(grounded) * 1.4)):
        return False, "length"
    source, new = _facts(grounded), _facts(rewrite)
    if new["numbers"] - source["numbers"]:
        return False, f"new numbers {sorted(new['numbers'] - source['numbers'])}"
    if new["claims"] - source["claims"]:
        return False, f"new claims {sorted(new['claims'] - source['claims'])}"
    short = agent_label.replace("Claude Code", "Claude").split()[0].lower()
    if short and short in grounded.lower() and short not in rewrite.lower():
        return False, "dropped the agent's name"
    hedges = ("by its account", "it reports", "reported", "reports", "says", "according to", "its own account",
              "not independently", "haven't checked")
    if any(h in grounded.lower() for h in hedges) and not any(h in rewrite.lower() for h in hedges):
        return False, "dropped that the result is only reported"
    if not allowed(rewrite):
        return False, "uses a phrase the owner ruled out"
    return True, ""


def reword(grounded: str, agent_label: str, *, timeout: float = 8.0) -> tuple[str, dict[str, Any]]:
    """Ask the owner's local model to say `grounded` in the SOUL's voice; keep it only if it passes."""
    info: dict[str, Any] = {"style": "local_model", "used": False}
    try:
        from agent.lean.provider import resolve_endpoint

        endpoint = resolve_endpoint(str(getattr(config.local.provider, "value", config.local.provider)),
                                    str(config.local.model_name or ""))
        if not getattr(endpoint, "local", False):
            info["reason"] = "not a local model"
            return grounded, info
        soul = load()
        body = {
            "model": endpoint.model, "stream": False, "temperature": 0.6, "max_tokens": 160,
            "messages": [
                {"role": "system", "content": (
                    "You voice a desktop companion. Personality:\n" + soul["personality"] + "\n\n"
                    "Rewrite the status line in that voice, in at most two short spoken sentences. Keep every fact "
                    "exactly; do not add numbers, results, claims or names that are not in it. Results an agent only "
                    "reported stay attributed to it: keep wording like 'by its account', or say '<agent> says' or "
                    "'<agent> reports'. Keep the agent's name. Reply with the sentence only.")},
                {"role": "user", "content": grounded},
            ],
        }
        from agent.lean.provider import reasoning_effort_for

        effort = reasoning_effort_for(endpoint, False, "low")  # the provider's own "thinking off"
        if effort:
            if str(getattr(endpoint, "provider", "")) in {"llama_cpp", "vllm"}:
                body["chat_template_kwargs"] = {"enable_thinking": effort != "none"}
            else:
                body["reasoning_effort"] = effort
        started = time.monotonic()
        request = urllib.request.Request(endpoint.base_url.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
        if getattr(endpoint, "api_key", ""):
            request.add_header("Authorization", f"Bearer {endpoint.api_key}")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read())
        text = str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip().strip('"').strip()
        info["ms"] = int((time.monotonic() - started) * 1000)
        ok, why = fact_check(grounded, text, agent_label)
        if not ok:
            info["reason"] = f"rejected: {why}"
            return grounded, info
        info["used"] = True
        return text, info
    except Exception as exc:  # timeouts, no model, provider errors: the template line stands
        info["reason"] = f"{type(exc).__name__}"
        return grounded, info
