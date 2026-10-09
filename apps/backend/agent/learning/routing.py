"""Experience-driven model choice: which model does each kind of task best, here?

Optional and off by default (Settings › Models › Smart model choice):

  off      Nothing changes. Every agent uses the model you set.
  suggest  When another model you listed has done clearly better at this kind of
           task on this PC, the agent says so under its reply. Nothing switches.
  auto     For the agents you opted in, and only among the models you listed,
           the agent switches to the one with the strongest record. Paid cloud
           models only when you allowed them, within your daily token cap.

The evidence is EchoSpeak's own learning episodes (agent/learning/episodes.py),
not leaderboards: per model and task kind, the tasks it finished with proof (V2+
or your "Worked") against the ones it failed. Following "Agentic Routing: The
Harness-Native Data Flywheel" (arXiv 2607.11399), the labels come from the
harness (checks, tool outcomes, your feedback), and each pick is tagged with who
made it. Unlike that paper, failures that aren't the model's are kept apart:
provider outages, bad keys, rate limits and privacy blocks never count as losses,
and an outage only makes a model unavailable for a while.

The rule is deliberately simple, no extra model call and no parallel testing:
a candidate needs at least five decided tasks of this kind, and switches only
when its pessimistic success estimate (Wilson lower bound, 80%) beats the
current model's average. Local models win ties. When your model isn't available
(server off, key missing, blocked by Private mode) the best available listed
model stands in, in auto mode, and the note says why.

Never: a model you didn't list, a cloud model you didn't allow, a provider the
privacy mode blocks, or a change for an agent you didn't opt in. The router only
picks a model; approvals, policy and privacy decide everything else as before.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from agent.learning.episodes import guess_kind
from agent.learning.store import Episode, get_experience_store

MODES = ("off", "suggest", "auto")
MIN_EVIDENCE = 5
WINDOW_DAYS = 90
Z = 1.28  # Wilson lower bound at 80%: few tasks make a cautious estimate
MARGIN = 0.05
# An outage this recent makes a model unavailable to the router.
OUTAGE_COOLDOWN = 3600.0
_CACHE_SECONDS = 60.0

_profile_cache: dict[str, tuple[float, Any]] = {}
_avail_cache: dict[str, tuple[float, tuple[bool, str]]] = {}
_lock = threading.Lock()


def _config() -> Any:
    from config import config

    return config


def mode() -> str:
    raw = str(getattr(_config(), "routing_mode", "off") or "off").strip().lower()
    return raw if raw in MODES else "off"


def parse_ref(text: str) -> Optional[tuple[str, str]]:
    """'ollama:qwen3:8b' -> ('ollama', 'qwen3:8b'). Model ids may contain colons; providers don't."""
    provider, sep, model = str(text or "").strip().partition(":")
    return (provider.strip().lower(), model.strip()) if sep and provider.strip() and model.strip() else None


def ref_text(ref: tuple[str, str]) -> str:
    return f"{ref[1]} ({ref[0]})"


def pool() -> list[tuple[str, str]]:
    raw = getattr(_config(), "routing_pool", None) or []
    if isinstance(raw, str):
        raw = raw.split(",")
    refs = [parse_ref(item) for item in raw]
    return list(dict.fromkeys(r for r in refs if r))


def opted_in(agent_id: str) -> bool:
    agents = getattr(_config(), "routing_auto_agents", None) or []
    return str(agent_id) in {str(a) for a in agents}


def is_cloud(provider: str) -> bool:
    from agent.cloud_providers import CLOUD_PROVIDERS

    return provider in CLOUD_PROVIDERS


def wilson_lower(wins: int, n: int, z: float = Z) -> float:
    if n <= 0:
        return 0.0
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - spread) / denom)


def mean(wins: int, n: int) -> float:
    return (wins + 1) / (n + 2)  # with one imagined win and loss, so a blank record reads 50%


# ── profiles from experience ────────────────────────────────────────────

@dataclass
class Stats:
    wins: int = 0
    losses: int = 0
    answered: int = 0
    errors: dict[str, int] = field(default_factory=dict)
    duration: float = 0.0
    tokens: int = 0
    timed: int = 0
    by_router: int = 0
    last_outage: float = 0.0

    @property
    def decided(self) -> int:
        return self.wins + self.losses

    def add(self, episode: Episode, verdict: str) -> None:
        if verdict == "win":
            self.wins += 1
        elif verdict == "loss":
            self.losses += 1
        elif episode.outcome == "error":
            kind = episode.error_kind or "other"
            self.errors[kind] = self.errors.get(kind, 0) + 1
            if kind == "outage":
                self.last_outage = max(self.last_outage, episode.created_at)
        else:
            self.answered += 1
        if episode.duration_s > 0:
            self.duration += episode.duration_s
            self.tokens += int(episode.tokens or 0)
            self.timed += 1
        if episode.routed == "router":
            self.by_router += 1

    def public(self) -> dict[str, Any]:
        n = self.decided
        return {
            "wins": self.wins, "losses": self.losses, "decided": n, "answered": self.answered,
            "errors": dict(self.errors), "rate": round(self.wins / n, 3) if n else None,
            "confidence_low": round(wilson_lower(self.wins, n), 3), "estimate": round(mean(self.wins, n), 3),
            "avg_seconds": round(self.duration / self.timed, 1) if self.timed else None,
            "avg_tokens": int(self.tokens / self.timed) if self.timed else None,
            "picked_by_router": self.by_router,
        }


def _verdict(episode: Episode) -> str:
    from agent.learning.profiles import _verdict as verdict

    return verdict(episode)


def profiles(window_days: int = WINDOW_DAYS) -> dict[tuple[str, str], dict[str, Any]]:
    """Per (provider, model): Stats overall and per task kind, from the owner's graded episodes."""
    now = time.time()
    with _lock:
        hit = _profile_cache.get("profiles")
        if hit and now - hit[0] < _CACHE_SECONDS:
            return hit[1]
    data: dict[tuple[str, str], dict[str, Any]] = {}
    for episode in get_experience_store().episodes(since=now - window_days * 86400, limit=5000):
        if not episode.provider or not episode.model:
            continue
        row = data.setdefault((episode.provider, episode.model), {"overall": Stats(), "kinds": {}, "last_at": 0.0})
        verdict = _verdict(episode)
        row["overall"].add(episode, verdict)
        row["kinds"].setdefault(episode.task_kind, Stats()).add(episode, verdict)
        row["last_at"] = max(row["last_at"], episode.created_at)
    with _lock:
        _profile_cache["profiles"] = (now, data)
    return data


def forget_profiles() -> None:
    """New experience arrived: recompute records next time (availability keeps its short cache)."""
    with _lock:
        _profile_cache.clear()


def forget_cache() -> None:
    with _lock:
        _profile_cache.clear()
        _avail_cache.clear()


def _stats(prof: dict, ref: tuple[str, str], kind: str) -> Stats:
    return ((prof.get(ref) or {}).get("kinds") or {}).get(kind) or Stats()


# ── who can be used right now ───────────────────────────────────────────

def availability(ref: tuple[str, str]) -> tuple[bool, str]:
    """(usable now, why not). Keys, a running local server with that model, Private mode, recent outages."""
    now = time.monotonic()
    key = f"{ref[0]}:{ref[1]}"
    with _lock:
        hit = _avail_cache.get(key)
        if hit and now - hit[0] < _CACHE_SECONDS:
            return hit[1]
    result = _check_availability(ref)
    with _lock:
        _avail_cache[key] = (now, result)
    return result


def _check_availability(ref: tuple[str, str]) -> tuple[bool, str]:
    from agent import privacy

    provider, model = ref
    if is_cloud(provider):
        from agent.cloud_providers import BASE_URLS, cloud_config

        base = BASE_URLS.get(provider, "")
        if not str(cloud_config(provider).api_key or "").strip():
            return False, "no API key saved"
    else:
        from agent.model_runtime import list_local_models, resolve_local_provider_base_url
        from config import ModelProvider

        try:
            base = resolve_local_provider_base_url(ModelProvider(provider), str(_config().local.base_url or ""))
        except ValueError:
            return False, "unknown provider"
        decision = privacy.decide("models", base)
        if not decision.allowed:
            return False, decision.reason
        offered = list_local_models(provider, base, timeout=1.0)
        if not offered:
            return False, "its server isn't running"
        if model not in offered and model.lower() not in {m.lower() for m in offered}:
            return False, "the model isn't loaded"
        return True, ""
    decision = privacy.decide("models", base)
    if not decision.allowed:
        return False, decision.reason
    outage = (profiles().get(ref) or {}).get("overall")
    if outage is not None and time.time() - outage.last_outage < OUTAGE_COOLDOWN:
        return False, "it had an outage in the last hour"
    return True, ""


def cloud_tokens_today() -> int:
    """New tokens cloud models used today on picks the router made (auto mode's spending)."""
    start = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
    return sum(int(e.tokens or 0) for e in get_experience_store().episodes(since=start, limit=5000)
               if e.routed == "router" and is_cloud(e.provider))


def auto_allowed(ref: tuple[str, str]) -> tuple[bool, str]:
    """May auto mode switch an agent to ``ref``? Local: yes. Cloud: only when allowed, within the cap."""
    if not is_cloud(ref[0]):
        return True, ""
    if not bool(getattr(_config(), "routing_allow_cloud", False)):
        return False, "paid cloud models aren't allowed for automatic picks"
    cap = int(getattr(_config(), "routing_daily_cloud_tokens", 0) or 0)
    if cap and cloud_tokens_today() >= cap:
        return False, f"today's cloud token cap ({cap:,}) is used up"
    return True, ""


# ── the decision ────────────────────────────────────────────────────────

@dataclass
class RouteDecision:
    mode: str
    kind: str
    current: tuple[str, str]
    chosen: tuple[str, str]
    applied: bool
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def changes(self) -> bool:
        return self.chosen != self.current

    def note(self) -> str:
        """The line under the agent's reply, or '' when there is nothing to say."""
        if not self.changes:
            return ""
        if self.applied:
            return f"Model: {ref_text(self.chosen)} instead of {ref_text(self.current)}. {self.reason}"
        return f"Suggestion: {ref_text(self.chosen)} may suit {self.kind.replace('_', ' ')} tasks better. {self.reason}"

    def public(self) -> dict[str, Any]:
        return {"mode": self.mode, "kind": self.kind, "current": list(self.current), "chosen": list(self.chosen),
                "applied": self.applied, "reason": self.reason, "evidence": self.evidence, "note": self.note()}


def _record_line(stats: Stats, kind: str) -> str:
    return f"{stats.wins} of {stats.decided} {kind.replace('_', ' ')} tasks done with proof here"


def decide(agent_id: str, current: tuple[str, str], goal: str, *, kind: str = "") -> Optional[RouteDecision]:
    """The model this agent should use for ``goal``. None when routing is off."""
    current_mode = mode()
    if current_mode == "off":
        return None
    kind = kind or guess_kind(goal)
    prof = profiles()
    may_apply = current_mode == "auto" and opted_in(agent_id)
    current_ok, current_why = availability(current)
    current_stats = _stats(prof, current, kind)
    candidates = []
    for ref in pool():
        if ref == current:
            continue
        ok, why = availability(ref)
        if not ok:
            continue
        stats = _stats(prof, ref, kind)
        candidates.append((ref, stats, wilson_lower(stats.wins, stats.decided)))
    evidence = {"kind": kind, "current": current_stats.public(),
                "candidates": {f"{r[0]}:{r[1]}": s.public() for r, s, _ in candidates}}

    def rank(item):  # strongest pessimistic record, then local, then fewer tokens
        ref, stats, low = item
        tokens = stats.tokens / stats.timed if stats.timed else 1e9
        return (low, not is_cloud(ref[0]), -tokens)

    def applicable(ref):
        allowed, why = auto_allowed(ref)
        return may_apply and allowed, why

    if not current_ok:
        usable = sorted(candidates, key=rank, reverse=True)
        if not usable:
            return RouteDecision(current_mode, kind, current, current, False,
                                 f"{ref_text(current)} isn't available ({current_why}) and no listed model is.", evidence)
        ref, stats, _ = usable[0]
        apply, why = applicable(ref)
        reason = f"{ref_text(current)} isn't available ({current_why})." + (
            f" {ref_text(ref)}: {_record_line(stats, kind)}." if stats.decided else "")
        if not apply and current_mode == "auto" and opted_in(agent_id) and why:
            reason += f" Not switched: {why}."
        return RouteDecision(current_mode, kind, current, ref, apply, reason, evidence)

    strong = [c for c in candidates if c[1].decided >= MIN_EVIDENCE]
    if not strong:
        return RouteDecision(current_mode, kind, current, current, False, "Not enough experience yet to compare.", evidence)
    ref, stats, low = max(strong, key=rank)
    baseline = mean(current_stats.wins, current_stats.decided)
    if low <= baseline + MARGIN:
        return RouteDecision(current_mode, kind, current, current, False,
                             f"{ref_text(current)} is doing as well as the alternatives.", evidence)
    apply, why = applicable(ref)
    reason = f"{ref_text(ref)}: {_record_line(stats, kind)}; {ref_text(current)}: " + (
        _record_line(current_stats, kind) if current_stats.decided else f"no checked {kind.replace('_', ' ')} tasks yet") + "."
    if current_mode == "auto" and opted_in(agent_id) and not apply and why:
        reason += f" Not switched: {why}."
    elif not apply:
        reason += " Change it in Settings › Agents, or let Echo pick in Settings › Models."
    return RouteDecision(current_mode, kind, current, ref, apply, reason, evidence)


def recommendations() -> list[dict[str, Any]]:
    """Per agent and task kind it has done: its model's record, and the best listed alternative."""
    from agent.lean.personas import get_persona_store

    prof = profiles()
    rows = []
    for persona in get_persona_store().list():
        kinds = {e.task_kind for e in get_experience_store().episodes(agent_id=persona.id, limit=200)}
        current = (persona.model.provider, persona.model.model_id) if persona.model.provider and persona.model.model_id else None
        for kind in sorted(kinds):
            best = None
            for ref in pool():
                stats = _stats(prof, ref, kind)
                if stats.decided and (best is None or wilson_lower(stats.wins, stats.decided) > best[2]):
                    best = (ref, stats, wilson_lower(stats.wins, stats.decided))
            rows.append({
                "agent_id": persona.id, "agent": persona.name, "kind": kind,
                "current": list(current) if current else None,
                "current_stats": _stats(prof, current, kind).public() if current else None,
                "best": list(best[0]) if best else None,
                "best_stats": best[1].public() if best else None,
                "auto": opted_in(persona.id),
            })
    return rows


def model_table() -> list[dict[str, Any]]:
    """Every model with experience: overall and per-kind records (Settings › Models)."""
    rows = []
    listed = set(pool())
    for ref, row in sorted(profiles().items(), key=lambda item: -item[1]["last_at"]):
        rows.append({
            "provider": ref[0], "model": ref[1], "listed": ref in listed, "cloud": is_cloud(ref[0]),
            "overall": row["overall"].public(),
            "kinds": {kind: stats.public() for kind, stats in row["kinds"].items()},
            "last_at": row["last_at"],
        })
    return rows
