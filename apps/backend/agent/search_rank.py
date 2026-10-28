"""Search result ranking: which hits deserve to be read (docs/research/research-engine.md).

Providers return lists in their own order, and EchoSpeak used to read whichever came
first. This ranks them the way production search stacks do, with no model call:

1. **Canonical dedupe**: the same page under tracking parameters, ``www``, ``m.``,
   AMP or a trailing slash counts once.
2. **Field-weighted BM25** (title > URL > snippet) of each hit against the query, with
   IDF taken over this result set: a term every hit shares says little; a rare one says
   a lot.
3. **Reciprocal Rank Fusion** (k = 60) across the query variants and providers that
   produced the hits, so agreement between lists counts and no single list dominates.
4. **Scoring-profile adjustments**: the named entity in the host or path (the project's own
   site or repository), documentation hosts, freshness when the question asks for
   something recent, and a small penalty for thin aggregator and profile pages.

``relevance`` measures how well a fetched page's text covers the question, so a page
that loaded but is about something else isn't treated as evidence.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime
from typing import Any, Iterable, Optional, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

RRF_K = 60
_TOKEN = re.compile(r"[a-z0-9]+(?:[.+#][a-z0-9]+)*", re.I)
_STOP = frozenset(
    "a an and are as at be but by can do does for from has have how i if in into is it its latest me my new news "
    "of on or our recent show tell than that the their them there these this to update updates was what whats when "
    "where which who why will with you your about any get give find look search please current today now".split()
)
_RECENT = re.compile(r"(?i)\b(latest|newest|recent|current|today|this (?:week|month|year)|new|now|updates?|changelog|release(?:s|d)?|news|20\d\d)\b")
_TRACKING = re.compile(r"^(utm_[a-z]+|fbclid|gclid|mc_[a-z]+|ref|ref_src|igshid|spm|cmpid)$", re.I)
_DOC_HOSTS = re.compile(r"(^|\.)(docs|developer|developers|learn|support|help|api|reference)\.|readthedocs\.io$|\.dev$")
_THIN = re.compile(r"(^|\.)(pinterest\.[a-z.]+|quora\.com|answers\.com|ask\.com|scribd\.com|slideshare\.net)$")
_YEAR = re.compile(r"\b(20\d\d)\b")
_CODE_HOSTS = {"github.com", "gitlab.com", "codeberg.org", "bitbucket.org", "pypi.org", "npmjs.com", "crates.io"}
_UPDATES_ASKED = re.compile(r"(?i)\b(latest|newest|recent|new|updates?|changes?|changelog|release(?:s|d)?|what'?s new|news)\b")
_UPDATE_PAGE = re.compile(r"(?i)(releases?|changelog|change-log|whats-?new|what's new|release-notes|news|blog|pulls|commits|announc)")


def tokens(text: str) -> list[str]:
    """Lower-case terms, keeping version-like and dotted names whole ("3.14", "node.js", "c++")."""
    out = []
    for match in _TOKEN.finditer(str(text or "").lower().replace("c++", "cplusplus")):
        term = match.group(0)
        out.append(term)
        if "." in term or "+" in term or "#" in term:
            out.extend(part for part in re.split(r"[.+#]", term) if len(part) > 1)
    return out


def query_terms(query: str) -> list[str]:
    terms = [t for t in tokens(query) if t not in _STOP and len(t) > 1]
    return list(dict.fromkeys(terms)) or list(dict.fromkeys(tokens(query)))


def canonical(url: str) -> str:
    """The comparable form of a URL for dedupe and citation matching."""
    try:
        parts = urlsplit(str(url or "").strip())
    except ValueError:
        return ""
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    host = parts.netloc.lower()
    for prefix in ("www.", "m.", "mobile.", "amp."):
        host = host.removeprefix(prefix)
    path = re.sub(r"/(amp|index\.html?)/?$", "", parts.path).rstrip("/")
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _TRACKING.match(k)])
    return urlunsplit(("https", host, path, query, ""))


def entity_terms(query: str) -> list[str]:
    """Names in the query: CamelCase, owner/repo, or capitalised words that aren't sentence starts."""
    raw = str(query or "")
    names = [w for w in re.findall(r"\b[A-Za-z0-9][A-Za-z0-9._-]*\b", raw)
             if (re.search(r"[a-z][A-Z]|[A-Z]{2,}|\d", w) or w[:1].isupper()) and w.lower() not in _STOP and len(w) > 2]
    names += [part for pair in re.findall(r"\b([\w.-]+/[\w.-]+)\b", raw) for part in pair.split("/")]
    return list(dict.fromkeys(n.lower() for n in names))


def _fields(hit: Any) -> dict[str, str]:
    get = (lambda k: hit.get(k, "")) if isinstance(hit, dict) else (lambda k: getattr(hit, k, ""))
    url = str(get("url") or "")
    parts = urlsplit(url) if url else None
    return {
        "title": str(get("title") or ""),
        "url": " ".join([parts.netloc, parts.path.replace("/", " ").replace("-", " ").replace("_", " ")]) if parts else "",
        "snippet": " ".join([str(get("snippet") or ""), str(get("extract") or "")[:600]]),
        "date": str(get("date") or ""),
        "raw_url": url,
    }


def bm25_scores(query: str, docs: Sequence[dict[str, str]], weights: Optional[dict[str, float]] = None,
                k1: float = 1.2, b: float = 0.75) -> list[float]:
    """BM25F-style score per document over weighted fields, IDF over this set of documents."""
    weights = weights or {"title": 2.5, "url": 1.5, "snippet": 1.0}
    terms = query_terms(query)
    bags = []
    for doc in docs:
        bag: Counter = Counter()
        for field_name, weight in weights.items():
            for term in tokens(doc.get(field_name, "")):
                bag[term] += weight
        bags.append(bag)
    n = max(1, len(bags))
    avg = sum(sum(bag.values()) for bag in bags) / n or 1.0
    df = Counter(term for bag in bags for term in set(bag) if term in terms)
    scores = []
    for bag in bags:
        length = sum(bag.values()) or 1.0
        score = 0.0
        for term in terms:
            tf = bag.get(term, 0.0)
            if not tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / avg))
        scores.append(score)
    return scores


def coverage(query: str, text: str) -> float:
    """Share of the query's terms the text contains (0..1)."""
    terms = query_terms(query)
    if not terms:
        return 0.0
    bag = set(tokens(text))
    return sum(1 for t in terms if t in bag) / len(terms)


def relevance(question: str, text: str) -> float:
    """How well a fetched page covers the question, 0..1, weighting the question's names double."""
    names = entity_terms(question)
    terms = query_terms(question)
    if not terms:
        return 0.0
    bag = set(tokens(text[:60000]))
    weight = {t: (2.0 if t in names else 1.0) for t in terms}
    found = sum(w for t, w in weight.items() if t in bag)
    return round(found / sum(weight.values()), 3)


def _recent_years() -> set[str]:
    year = datetime.now().year
    return {str(year), str(year - 1)}


def rank_hits(query: str, lists: Iterable[Sequence[Any]], *, context: str = "", limit: int = 10) -> list[Any]:
    """Merge, dedupe and rank hits from one or more result lists. Each hit gains ``score_hint`` and ``rank_reason``."""
    question = " ".join(part for part in (query, context) if part)
    fused: dict[str, float] = {}
    first: dict[str, Any] = {}
    order: list[str] = []
    for hits in lists:
        for position, hit in enumerate(hits, 1):
            key = canonical(_fields(hit)["raw_url"]) or _fields(hit)["title"].lower()
            if not key:
                continue
            fused[key] = fused.get(key, 0.0) + 1.0 / (RRF_K + position)
            if key not in first:
                first[key] = hit
                order.append(key)
    if not order:
        return []
    docs = [_fields(first[key]) for key in order]
    lexical = bm25_scores(question, docs)
    top = max(lexical) or 1.0
    names = entity_terms(question)
    recent = bool(_RECENT.search(question))
    years = _recent_years()
    scored = []
    for key, doc, lex in zip(order, docs, lexical):
        reasons = []
        score = fused[key] * RRF_K  # ~1.0 for a first place in one list
        score += 1.5 * (lex / top)
        if lex == 0:
            score -= 0.8
            reasons.append("no query terms")
        host = urlsplit(doc["raw_url"]).netloc.lower().removeprefix("www.")
        path = urlsplit(doc["raw_url"]).path.lower()
        segments = [s for s in path.split("/") if s]
        site = host.split(".")[-2] if host.count(".") >= 1 else host
        # Where the name appears matters: the project's own domain, or its owner/repo on a code host,
        # is the authority; the same word deep in another site's path is not.
        owned = [n for n in names if n == site or n in host.split(".")[:-1]
                 or (host in _CODE_HOSTS and n in segments[:2])]
        mentioned = [n for n in names if n not in owned and (n in host or n in path)]
        if owned:
            score += 0.9 + 0.2 * min(2, len(owned) - 1)
            reasons.append("official " + ", ".join(owned[:2]))
        elif mentioned:
            score += 0.25
            reasons.append("mentions " + ", ".join(mentioned[:2]))
        if _UPDATES_ASKED.search(question) and _UPDATE_PAGE.search(f"{path} {doc['title']}"):
            score += 0.35
            reasons.append("release or changelog page")
        if _DOC_HOSTS.search(host):
            score += 0.2
            reasons.append("documentation")
        if _THIN.search(host):
            score -= 0.5
            reasons.append("aggregator")
        if host == "github.com" and path.count("/") == 1 and names and not any(n in path for n in names):
            score -= 0.4  # a profile page when the question names something else
            reasons.append("profile page")
        if recent:
            found = set(_YEAR.findall(" ".join([doc["date"], doc["title"], doc["snippet"]])))
            if found & years:
                score += 0.3
                reasons.append("recent")
            elif found and max(found) < min(years):
                score -= 0.3
                reasons.append("dated " + max(found))
        scored.append((score, key, reasons))
    scored.sort(key=lambda row: row[0], reverse=True)
    ranked = []
    for score, key, reasons in scored[:limit]:
        hit = first[key]
        if isinstance(hit, dict):
            hit["_score_hint"], hit["_rank_reason"] = round(score, 3), "; ".join(reasons)
        else:
            hit.score_hint = round(score, 3)
            try:
                hit.rank_reason = "; ".join(reasons)
            except AttributeError:
                pass
        ranked.append(hit)
    return ranked


def is_relevant(hit: Any, query: str, context: str = "") -> bool:
    """Worth opening: shares at least one meaningful term with the question."""
    fields = _fields(hit)
    return coverage(" ".join([query, context]), " ".join([fields["title"], fields["url"], fields["snippet"]])) > 0


# ── Query preparation ────────────────────────────────────────────────────────

_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"'`)\]]+")
_WRAPPER = re.compile(r"(?i)^\s*(?:please\s+)?(?:can you\s+|could you\s+)?(?:search(?:\s+the\s+web)?\s+for|look\s+up|google|find(?:\s+out)?|search)\s*[:\-]?\s+")


def urls_in(text: str) -> list[str]:
    return list(dict.fromkeys(u.rstrip(".,;:!?") for u in _URL_IN_TEXT.findall(str(text or ""))))


def prepare_query(query: str) -> str:
    """Light, faithful clean-up of a model-written query: whitespace and a leading "search for" only.

    The model already wrote a search query. Rewriting it destroyed versions ("python 3.14"),
    dotted names ("node.js"), operators ("site:github.com") and intent; see the report.
    URLs are left in (the caller retrieves them directly).
    """
    q = re.sub(r"\s+", " ", str(query or "")).strip()
    q = _WRAPPER.sub("", q).strip()
    try:
        # Word-level typo fixes only ("standly cup" → "stanley cup"); measured safe on versions and operators.
        from agent.research import apply_spelling_fixes
        q = apply_spelling_fixes(q) or q
    except Exception:
        pass
    return q.strip(" ?") or str(query or "").strip()
