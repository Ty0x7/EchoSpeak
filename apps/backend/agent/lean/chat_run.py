"""Chat's execution layer over the shared Lean loop (docs/research/chat-orchestration.md).

The loop itself is the documented agent-runner cycle (call the model, run the tools it
asks for, return their outputs, repeat until it answers), and Chat keeps it. What a
general-purpose assistant adds around that cycle, and what this module supplies:

- **Evidence ledger.** Every tool result is recorded as evidence: searches and the URLs
  they returned, pages actually opened, files read, memory looked up. This is EchoSpeak's
  equivalent of the Responses API's search actions (search / open_page) and its source list.
- **Citation provenance.** Before an answer is accepted, the links in it are checked against
  that ledger (plus sources read earlier in this chat and links the user gave). A link to a
  page nothing retrieved is a likely fabrication; an answer built on pages it read but cites
  none of them is ungrounded. Either gets one correction request, like an output guardrail.
  Links in an answer that used no research are left alone (and traced as unverified).
- **A budget that still answers.** Chat gets a step budget sized to its model profile, and
  running out produces a final answer from what was found (the runner's max-turns fallback),
  never "press Continue".
- **Model profile.** Small local models (about 9B parameters or fewer) get a compact prompt
  and a smaller tool set up front; larger and API models get the full Chat instructions.
  The selected model is never changed.
- **Run trace.** One record per Chat turn: profile, context sections and sizes, tools offered,
  each call and outcome, evidence, the citation audit, stop reason and token usage. No
  reasoning text and no prompt bodies (the Agents SDK's sensitive-data default, inverted).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

# Step budgets. A step is one model call plus the tools it asked for (several can run at once,
# and web_search can open pages in the same step), so 10 steps covers several searches,
# reading and a synthesis; a small model gets less rope.
BUDGETS = {"full": 10, "compact": 6}
GRACE_PROMPT = ("[EchoSpeak]: This answer's research budget is used up. Answer the user now, without tools, "
                "using only what you found. Cite the pages you read with inline links, and say plainly what you "
                "couldn't confirm.")
SMALL_MODEL_BILLIONS = 9.0
_SIZE = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d)?)\s*b(?![a-z0-9])", re.I)
_LINK = re.compile(r"\[([^\]]{1,300})\]\((https?://[^)\s]+)\)|(?<![(\[<])\b(https?://[^\s)<>\]]+)")
_URL = re.compile(r"https?://[^\s)<>\]\"'`]+")
_TRACKING = re.compile(r"^(utm_[a-z]+|fbclid|gclid|ref|ref_src)$", re.I)
SEARCH_TOOLS = {"web_search", "image_search", "video_search", "product_search"}
PAGE_TOOLS = {"safe_web_fetch", "youtube_transcript"}
FILE_TOOLS = {"file_read", "file_search", "file_find", "file_list", "document_search"}
MEMORY_TOOLS = {"memory_search", "chat_search"}


def model_billions(model: str) -> Optional[float]:
    """Parameter count from a model id ("qwen3-4b", "gemma-3n-e4b", "llama-3.2-3b-instruct"), when it says."""
    sizes = [float(m.group(1)) for m in _SIZE.finditer(str(model or "").replace("_", "-"))]
    return min(sizes) if sizes else None


def profile_for(model: str, *, local: bool) -> str:
    """"compact" for small local models, "full" for everything else (API models, bigger or unknown sizes)."""
    size = model_billions(model)
    return "compact" if local and size is not None and size <= SMALL_MODEL_BILLIONS else "full"


def normalize_url(url: str) -> str:
    """Comparable form: no fragment, no tracking parameters, no trailing slash, lower-case host."""
    try:
        parts = urlsplit(str(url or "").strip().rstrip(".,;:!?"))
    except ValueError:
        return ""
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    query = "&".join(p for p in parts.query.split("&") if p and not _TRACKING.match(p.split("=", 1)[0]))
    host = parts.netloc.lower().removeprefix("www.")
    return urlunsplit(("https", host, parts.path.rstrip("/") or "", query, ""))


def links_in(text: str) -> list[str]:
    """URLs the answer links to, Markdown links and bare URLs alike, in order."""
    found = []
    for match in _LINK.finditer(text or ""):
        # A bare URL ending a sentence keeps the sentence's full stop out of it.
        url = match.group(2) or (match.group(3) or "").rstrip(".,;:!?")
        if url and url not in found:
            found.append(url)
    return found


@dataclass
class Evidence:
    kind: str  # search | page | file | memory
    ref: str   # query, URL or path
    tool: str
    step: int
    ok: bool


@dataclass
class ChatRun:
    session_id: str
    model: str = ""
    provider: str = ""
    profile: str = "full"
    user_message: str = ""
    earlier_sources: list[dict[str, str]] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)   # URLs any result showed (search results, pages)
    read: set[str] = field(default_factory=set)   # URLs actually opened and read
    calls: list[dict[str, Any]] = field(default_factory=list)
    nudges: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    audit: dict[str, Any] = field(default_factory=dict)
    started: float = field(default_factory=time.time)

    @property
    def budget(self) -> int:
        return BUDGETS.get(self.profile, BUDGETS["full"])

    # ── evidence ─────────────────────────────────────────────────────────
    def observe(self, name: str, args: dict[str, Any], ok: bool, output: str, step: int,
                duration_ms: int = 0, meta: Optional[dict[str, Any]] = None) -> None:
        """Record one finished tool call (LeanTurn.tool_observer)."""
        meta = dict(meta or {})
        row: dict[str, Any] = {"tool": name, "ok": bool(ok), "step": step, "ms": int(duration_ms or 0)}
        text = str(output or "")
        if name in SEARCH_TOOLS:
            query = str(args.get("query") or args.get("q") or "")
            row["query"] = query[:200]
            self.evidence.append(Evidence("search", query[:200], name, step, ok))
            if ok:
                urls = [normalize_url(u) for u in _URL.findall(text)]
                self.seen.update(u for u in urls if u)
                row["results"] = int(meta.get("results") or len({u for u in urls if u}))
                # web_search(read=N) opens the top results in the same step; their pages are headed "### <url>".
                for url in re.findall(r"^### (https?://\S+)", text, re.M):
                    if key := normalize_url(url):
                        self.read.add(key)
                        self.evidence.append(Evidence("page", key, name, step, True))
                row["pages"] = int(meta.get("pages") or 0)
        elif name in PAGE_TOOLS:
            url = str(args.get("url") or args.get("video") or "")
            key = normalize_url(url)
            row["url"] = key or url[:200]
            if key and ok and "safe_fetch_error" not in text:
                self.read.add(key)
                self.seen.add(key)
                self.evidence.append(Evidence("page", key, name, step, True))
                self.seen.update(u for u in (normalize_url(x) for x in _URL.findall(text)) if u)
            else:
                self.evidence.append(Evidence("page", key or url[:200], name, step, False))
        elif name == "research_notebook":
            if ok:
                self.seen.update(u for u in (normalize_url(x) for x in _URL.findall(text)) if u)
        elif name in FILE_TOOLS:
            path = str(args.get("path") or args.get("pattern") or args.get("query") or "")
            row["path"] = path[:200]
            self.evidence.append(Evidence("file", path[:200], name, step, ok))
        elif name in MEMORY_TOOLS:
            row["query"] = str(args.get("query") or "")[:200]
            self.evidence.append(Evidence("memory", row["query"], name, step, ok))
        self.calls.append(row)

    def _known(self) -> set[str]:
        """Every URL this answer may honestly cite: retrieved now, read earlier in this chat, or given by the user."""
        known = set(self.seen) | set(self.read)
        known.update(normalize_url(s.get("url", "")) for s in self.earlier_sources)
        known.update(normalize_url(u) for u in _URL.findall(self.user_message or ""))
        known.discard("")
        return known

    @property
    def researched(self) -> bool:
        return any(e.kind in {"search", "page"} and e.ok for e in self.evidence)

    # ── output check (an output guardrail with one correction) ──────────
    def check_answer(self, text: str) -> str:
        """The correction to ask for before accepting this answer, or "" to accept it. At most once."""
        result = self.cite_audit(text)
        if self.nudges or not self.researched:
            return ""
        if result["unknown"]:
            links = ", ".join(result["unknown"][:4])
            self.nudges.append("unknown_links")
            return ("[EchoSpeak]: These links in your answer weren't in any search result or page you retrieved: "
                    f"{links}. Link only pages from your results (prefer the ones you read), or remove those links. "
                    "Then give the full answer again.")
        if self.read and not result["cited"] and len(text) > 400:
            pages = ", ".join(sorted(self.read)[:4])
            self.nudges.append("uncited")
            return ("[EchoSpeak]: You read these pages but your answer cites none of them: "
                    f"{pages}. Add inline links to the ones that support your key claims, and say what they didn't "
                    "confirm. Then give the full answer again.")
        return ""

    def cite_audit(self, text: str) -> dict[str, Any]:
        cited = [normalize_url(u) for u in links_in(text)]
        cited = [u for u in dict.fromkeys(cited) if u]
        known = self._known()
        result = {
            "cited": cited,
            "read": [u for u in cited if u in self.read or u in {normalize_url(s.get("url", "")) for s in self.earlier_sources}],
            "seen_only": [u for u in cited if u in self.seen and u not in self.read],
            "unknown": [u for u in cited if u not in known],
            "research": self.researched,
        }
        self.audit = result
        return result

    # ── trace ────────────────────────────────────────────────────────────
    def trace(self, *, steps: int = 0, stop_reason: str = "", usage: Optional[dict[str, Any]] = None,
              answer: str = "") -> dict[str, Any]:
        """One run record for diagnostics. Refs (queries, URLs, paths) only; no prompts, replies or reasoning."""
        if answer:
            self.cite_audit(answer)
        kinds = [e.kind for e in self.evidence if e.ok]
        path = ("research" if kinds.count("search") + kinds.count("page") >= 3 else
                "search" if "search" in kinds or "page" in kinds else
                "files" if "file" in kinds else "memory" if "memory" in kinds else "direct")
        return {
            "version": 1,
            "path": path,
            "profile": self.profile,
            "model": self.model,
            "provider": self.provider,
            "budget": self.budget,
            "steps": steps,
            "stop_reason": stop_reason or "answered",
            "context": self.context,
            "calls": self.calls[:40],
            "evidence": {
                "searches": [e.ref for e in self.evidence if e.kind == "search"][:12],
                "pages_read": sorted(self.read)[:20],
                "results_seen": len(self.seen),
                "files": [e.ref for e in self.evidence if e.kind == "file"][:12],
                "memory_lookups": sum(1 for e in self.evidence if e.kind == "memory"),
                "earlier_sources": len(self.earlier_sources),
            },
            "citations": {key: (value[:20] if isinstance(value, list) else value) for key, value in self.audit.items()},
            "corrections": list(self.nudges),
            "usage": {k: int(v) for k, v in dict(usage or {}).items() if isinstance(v, (int, float))},
            "seconds": round(time.time() - self.started, 2),
        }


def earlier_sources(session_id: str, limit: int = 8) -> list[dict[str, str]]:
    """Pages read earlier in this chat (the research notebook), for follow-ups that point back at them."""
    try:
        from agent.research_notebook import ResearchNotebook
        rows = ResearchNotebook().sources(session_id, limit=40)
    except Exception:
        return []
    out = []
    for row in rows:
        if not row.get("inspected"):
            continue
        out.append({"id": str(row.get("id") or ""), "url": str(row.get("url") or ""), "title": str(row.get("title") or "")[:140]})
        if len(out) >= limit:
            break
    return out


def earlier_sources_section(sources: list[dict[str, str]]) -> str:
    if not sources:
        return ""
    lines = ["## Pages read earlier in this chat",
             "You can cite these again, or reopen one with research_notebook (its id) instead of searching again."]
    lines += [f"- [{s['title'] or s['url']}]({s['url']}) ({s['id']})" for s in sources]
    return "\n".join(lines)
