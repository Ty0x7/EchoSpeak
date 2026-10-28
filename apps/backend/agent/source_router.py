"""Source-aware retrieval: read a known source the way it's meant to be read.

A URL often names something with an official API: a GitHub repository's pull requests,
a PyPI package, a Reddit thread. Scraping those pages returns navigation noise or a
JavaScript shell; the API returns exactly the records asked for. ``resolve`` classifies a
URL deterministically (no model call); ``fetch_structured`` reads it through the API and
renders a compact document whose citations are the human-facing URLs.

Everything goes through ``safe_web_retrieval.fetch_public_bytes``: public addresses only,
SSRF-pinned, redirects re-validated, and blocked in Private/Offline mode like any page.
No credentials are used except an optional GitHub token the owner configured, sent only
to api.github.com. Rate limits and errors fall back to the ordinary page fetch.

``assess`` grades any fetched page (empty, JavaScript-only, login wall, blocked, not found,
thin, stale) so a page that loaded isn't mistaken for a page that answered.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from urllib.parse import quote, urlsplit

_GH = re.compile(r"^https?://(?:www\.)?github\.com/(?P<owner>[A-Za-z0-9-]{1,39})/(?P<repo>[A-Za-z0-9._-]{1,100})"
                 r"(?P<rest>/[^?#]*)?(?:[?#].*)?$", re.I)
_GH_RESERVED = {"orgs", "settings", "marketplace", "topics", "collections", "sponsors", "features", "about", "login", "search"}
_PYPI = re.compile(r"^https?://(?:www\.)?pypi\.org/project/(?P<pkg>[A-Za-z0-9._-]+)", re.I)
_NPM = re.compile(r"^https?://(?:www\.)?npmjs\.com/package/(?P<pkg>(?:@[a-z0-9._-]+/)?[a-z0-9._-]+)", re.I)
_REDDIT = re.compile(r"^https?://(?:www\.|old\.)?reddit\.com/r/[^/]+/comments/[a-z0-9]+", re.I)


@dataclass
class SourcePlan:
    kind: str                 # github_pulls, github_pull, github_issues, github_issue, github_releases, github_commits,
                              # github_repo, github_file, pypi, npm, reddit, page
    url: str                  # what the user or model gave
    api: str = ""             # the structured endpoint, when there is one
    human: str = ""           # the page a reader would open (the citation)
    params: dict[str, str] = field(default_factory=dict)


def resolve(url: str) -> SourcePlan:
    """Classify a URL. Unknown sites are plain pages."""
    raw = str(url or "").strip()
    match = _GH.match(raw)
    if match and match.group("owner").lower() not in _GH_RESERVED:
        owner, repo = match.group("owner"), re.sub(r"\.git$", "", match.group("repo"))
        rest = [p for p in (match.group("rest") or "").split("/") if p]
        api = f"https://api.github.com/repos/{owner}/{repo}"
        human = f"https://github.com/{owner}/{repo}"
        params = {"owner": owner, "repo": repo}
        head = rest[0].lower() if rest else ""
        if head == "pulls":
            return SourcePlan("github_pulls", raw, f"{api}/pulls?state=all&sort=updated&direction=desc&per_page=20", f"{human}/pulls", params)
        if head == "pull" and len(rest) > 1 and rest[1].isdigit():
            return SourcePlan("github_pull", raw, f"{api}/pulls/{rest[1]}", f"{human}/pull/{rest[1]}", {**params, "number": rest[1]})
        if head == "issues" and len(rest) > 1 and rest[1].isdigit():
            return SourcePlan("github_issue", raw, f"{api}/issues/{rest[1]}", f"{human}/issues/{rest[1]}", {**params, "number": rest[1]})
        if head == "issues":
            return SourcePlan("github_issues", raw, f"{api}/issues?state=all&sort=updated&direction=desc&per_page=30", f"{human}/issues", params)
        if head == "releases":
            return SourcePlan("github_releases", raw, f"{api}/releases?per_page=10", f"{human}/releases", params)
        if head == "commits":
            return SourcePlan("github_commits", raw, f"{api}/commits?per_page=20", f"{human}/commits", params)
        if head == "blob" and len(rest) > 2:
            ref, path = rest[1], "/".join(rest[2:])
            return SourcePlan("github_file", raw, f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}",
                              f"{human}/blob/{ref}/{path}", {**params, "path": path})
        if not rest:
            return SourcePlan("github_repo", raw, api, human, params)
        return SourcePlan("page", raw)
    if match := _PYPI.match(raw):
        pkg = match.group("pkg")
        return SourcePlan("pypi", raw, f"https://pypi.org/pypi/{pkg}/json", f"https://pypi.org/project/{pkg}/", {"package": pkg})
    if match := _NPM.match(raw):
        pkg = match.group("pkg")
        return SourcePlan("npm", raw, f"https://registry.npmjs.org/{quote(pkg, safe='@')}", f"https://www.npmjs.com/package/{pkg}", {"package": pkg})
    if _REDDIT.match(raw):
        base = raw.split("?", 1)[0].rstrip("/")
        return SourcePlan("reddit", raw, f"{base}.json?limit=20", base)
    return SourcePlan("page", raw)


@dataclass
class Document:
    url: str            # the citation: the human-facing page
    title: str
    text: str           # compact, readable rendering
    source: str         # e.g. "github_api"
    items: list[dict[str, str]] = field(default_factory=list)  # each record with its own URL
    published: str = ""  # newest date in the records (ISO)


class StructuredUnavailable(RuntimeError):
    """The API couldn't answer (rate limit, not found, privacy); the caller reads the page instead."""


def _get_json(url: str, *, fetch: Optional[Callable[..., tuple[str, dict[str, str], bytes]]] = None) -> Any:
    from agent.safe_web_retrieval import SafeWebRetrievalError, fetch_public_bytes

    headers = {"Accept": "application/json", "User-Agent": "EchoSpeak-SafeResearch/1.0"}
    if url.startswith("https://api.github.com/"):
        headers["Accept"] = "application/vnd.github+json"
        try:
            from config import config
            token = str(getattr(config, "github_token", "") or "").strip()
        except Exception:
            token = ""
        if token:
            headers["Authorization"] = f"Bearer {token}"  # only ever to api.github.com
    try:
        _final, _headers, body = (fetch or fetch_public_bytes)(url, headers=headers, timeout_seconds=10.0, max_bytes=2_500_000)
    except SafeWebRetrievalError as exc:
        raise StructuredUnavailable(f"{exc.code}: {exc}") from None
    try:
        return json.loads(body.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise StructuredUnavailable(f"not JSON: {exc}") from None


def _day(value: Any) -> str:
    return str(value or "")[:10]


def _clip(text: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _pull_line(pr: dict[str, Any]) -> str:
    state = "merged" if pr.get("merged_at") else ("draft" if pr.get("draft") else str(pr.get("state") or ""))
    when = _day(pr.get("merged_at") or pr.get("updated_at") or pr.get("created_at"))
    return (f"- #{pr.get('number')} {pr.get('title')} ({state}, {when}, by {(pr.get('user') or {}).get('login', '?')}) "
            f"{pr.get('html_url')}" + (f"\n  {_clip(pr.get('body'), 280)}" if pr.get("body") else ""))


def fetch_structured(plan: SourcePlan, *, fetch: Optional[Callable[..., tuple[str, dict[str, str], bytes]]] = None) -> Document:
    """Read a classified source through its API. Raises StructuredUnavailable to fall back to the page."""
    if plan.kind == "page" or not plan.api:
        raise StructuredUnavailable("no structured source")
    if plan.kind == "github_file":
        from agent.safe_web_retrieval import SafeWebRetrievalError, fetch_public_bytes
        try:
            _f, _h, body = (fetch or fetch_public_bytes)(plan.api, headers={"User-Agent": "EchoSpeak-SafeResearch/1.0"},
                                                         timeout_seconds=10.0, max_bytes=1_500_000)
        except SafeWebRetrievalError as exc:
            raise StructuredUnavailable(f"{exc.code}: {exc}") from None
        text = body.decode("utf-8", errors="replace")
        return Document(plan.human, plan.params.get("path", ""), text[:60000], "github_raw")
    data = _get_json(plan.api, fetch=fetch)
    p = plan.params
    repo = f"{p.get('owner')}/{p.get('repo')}" if p.get("owner") else ""
    if plan.kind == "github_pulls":
        rows = [pr for pr in (data if isinstance(data, list) else []) if isinstance(pr, dict)]
        items = [{"title": f"#{pr.get('number')} {pr.get('title')}", "url": str(pr.get("html_url") or ""),
                  "date": _day(pr.get("merged_at") or pr.get("updated_at"))} for pr in rows]
        text = (f"Pull requests in {repo}, most recently updated first ({len(rows)} shown; from the GitHub API):\n"
                + "\n".join(_pull_line(pr) for pr in rows)) if rows else f"{repo} has no pull requests."
        return Document(plan.human, f"Pull requests · {repo}", text, "github_api", items, max((i["date"] for i in items), default=""))
    if plan.kind in {"github_pull", "github_issue"}:
        if not isinstance(data, dict):
            raise StructuredUnavailable("unexpected response")
        kind = "Pull request" if plan.kind == "github_pull" else "Issue"
        extra = (f"\nChanges: {data.get('changed_files', '?')} files, +{data.get('additions', '?')} −{data.get('deletions', '?')}; "
                 f"merged: {bool(data.get('merged_at'))}" if plan.kind == "github_pull" else "")
        text = (f"{kind} #{data.get('number')} in {repo}: {data.get('title')}\nState: {data.get('state')}; "
                f"opened {_day(data.get('created_at'))} by {(data.get('user') or {}).get('login', '?')}; "
                f"updated {_day(data.get('updated_at'))}{extra}\n\n{_clip(data.get('body'), 6000)}")
        return Document(str(data.get("html_url") or plan.human), f"{kind} #{data.get('number')}: {data.get('title')}", text,
                        "github_api", [], _day(data.get("updated_at")))
    if plan.kind == "github_issues":
        rows = [i for i in (data if isinstance(data, list) else []) if isinstance(i, dict) and "pull_request" not in i]
        items = [{"title": f"#{i.get('number')} {i.get('title')}", "url": str(i.get("html_url") or ""), "date": _day(i.get("updated_at"))} for i in rows]
        text = f"Issues in {repo}, most recently updated first (from the GitHub API):\n" + "\n".join(
            f"- #{i.get('number')} {i.get('title')} ({i.get('state')}, {_day(i.get('updated_at'))}) {i.get('html_url')}" for i in rows)
        return Document(plan.human, f"Issues · {repo}", text, "github_api", items, max((i["date"] for i in items), default=""))
    if plan.kind == "github_releases":
        rows = [r for r in (data if isinstance(data, list) else []) if isinstance(r, dict)]
        items = [{"title": str(r.get("name") or r.get("tag_name")), "url": str(r.get("html_url") or ""), "date": _day(r.get("published_at"))} for r in rows]
        text = f"Releases of {repo}, newest first (from the GitHub API):\n" + "\n".join(
            f"- {r.get('name') or r.get('tag_name')} ({_day(r.get('published_at'))}) {r.get('html_url')}\n  {_clip(r.get('body'), 500)}" for r in rows)
        return Document(plan.human, f"Releases · {repo}", text, "github_api", items, max((i["date"] for i in items), default=""))
    if plan.kind == "github_commits":
        rows = [c for c in (data if isinstance(data, list) else []) if isinstance(c, dict)]
        items = [{"title": _clip(((c.get("commit") or {}).get("message") or "").split("\n")[0], 120), "url": str(c.get("html_url") or ""),
                  "date": _day(((c.get("commit") or {}).get("author") or {}).get("date"))} for c in rows]
        text = f"Recent commits in {repo} (from the GitHub API):\n" + "\n".join(f"- {i['date']} {i['title']} {i['url']}" for i in items)
        return Document(plan.human, f"Commits · {repo}", text, "github_api", items, max((i["date"] for i in items), default=""))
    if plan.kind == "github_repo":
        if not isinstance(data, dict):
            raise StructuredUnavailable("unexpected response")
        lines = [f"Repository {data.get('full_name')}: {data.get('description') or 'no description'}",
                 f"Stars {data.get('stargazers_count')}, open issues {data.get('open_issues_count')}, "
                 f"default branch {data.get('default_branch')}, last push {_day(data.get('pushed_at'))}, licence "
                 f"{(data.get('license') or {}).get('spdx_id', 'none')}. {data.get('html_url')}"]
        items: list[dict[str, str]] = []
        for kind, label in (("github_releases", "Latest releases"), ("github_pulls", "Recently updated pull requests")):
            try:
                sub = fetch_structured(SourcePlan(kind, plan.url, resolve(f"{plan.human}/{'releases' if kind == 'github_releases' else 'pulls'}").api,
                                                  plan.human, plan.params), fetch=fetch)
            except StructuredUnavailable:
                continue
            lines.append(f"\n{label}:\n" + "\n".join(sub.text.split("\n")[1:9]))
            items += sub.items[:8]
        return Document(plan.human, str(data.get("full_name") or plan.human), "\n".join(lines), "github_api", items,
                        max([i["date"] for i in items] + [_day(data.get("pushed_at"))]))
    if plan.kind == "pypi":
        info = (data or {}).get("info") or {}
        releases = (data or {}).get("releases") or {}
        dated = sorted(((max((f.get("upload_time_iso_8601") or "" for f in files), default=""), version)
                        for version, files in releases.items() if files), reverse=True)[:8]
        text = (f"{info.get('name')} {info.get('version')} on PyPI: {info.get('summary')}\nRequires Python {info.get('requires_python') or '?'}; "
                f"licence {info.get('license') or '?'}; home {info.get('home_page') or (info.get('project_urls') or {}).get('Homepage', '')}\n"
                "Recent releases: " + ", ".join(f"{v} ({_day(d)})" for d, v in dated))
        return Document(plan.human, f"{info.get('name')} · PyPI", text, "pypi_api", [], _day(dated[0][0]) if dated else "")
    if plan.kind == "npm":
        tags = (data or {}).get("dist-tags") or {}
        times = (data or {}).get("time") or {}
        latest = tags.get("latest", "")
        recent = sorted(((t, v) for v, t in times.items() if v not in {"created", "modified"}), reverse=True)[:8]
        text = (f"{data.get('name')} on npm: {data.get('description')}\nLatest {latest} ({_day(times.get(latest))}); "
                f"licence {data.get('license') or '?'}\nRecent versions: " + ", ".join(f"{v} ({_day(t)})" for t, v in recent))
        return Document(plan.human, f"{data.get('name')} · npm", text, "npm_api", [], _day(times.get(latest)))
    if plan.kind == "reddit":
        try:
            post = data[0]["data"]["children"][0]["data"]
            comments = [c["data"] for c in data[1]["data"]["children"] if c.get("kind") == "t1"][:12]
        except (KeyError, IndexError, TypeError):
            raise StructuredUnavailable("unexpected response") from None
        when = datetime.fromtimestamp(float(post.get("created_utc") or 0), tz=timezone.utc).date().isoformat()
        text = (f"r/{post.get('subreddit')}: {post.get('title')} (score {post.get('score')}, {when})\n{_clip(post.get('selftext'), 3000)}\n\nTop comments:\n"
                + "\n".join(f"- {_clip(c.get('body'), 400)} (score {c.get('score')})" for c in comments))
        return Document(plan.human, str(post.get("title") or ""), text, "reddit_json", [], when)
    raise StructuredUnavailable("unsupported source")


# ── Page quality ────────────────────────────────────────────────────────────

_JS_SHELL = re.compile(r"(?i)(enable javascript|javascript (?:is )?(?:required|disabled)|you need to enable javascript|"
                       r"this (?:app|site) (?:requires|works best with) javascript|<noscript)")
_LOGIN = re.compile(r"(?i)(sign in to (?:continue|view|see)|log ?in to (?:continue|view|see)|please (?:sign|log) ?in|"
                    r"subscribe to (?:continue|read)|create (?:a free )?account to (?:continue|read)|members only)")
_BLOCKED = re.compile(r"(?i)(verify you are (?:a )?human|are you a robot|unusual traffic|access denied|request blocked|"
                      r"captcha|just a moment\.\.\.|attention required)")
_NOT_FOUND = re.compile(r"(?i)\b(404|page not found|not found|doesn't exist|does not exist)\b")


def assess(*, title: str, text: str, objective: str = "", published: str = "", structured: bool = False) -> dict[str, Any]:
    """Grade a fetched page: status (ok, thin, js_required, login_wall, blocked, not_found), relevance, staleness.

    A structured API document (a release list, a package record) is dense by nature: it's never "thin".
    """
    from agent.search_rank import relevance

    body = str(text or "")
    words = len(body.split())
    status = "ok"
    if _BLOCKED.search(body[:3000]) and words < 400:
        status = "blocked"
    elif _LOGIN.search(body[:4000]) and words < 300:
        status = "login_wall"
    elif _JS_SHELL.search(body[:3000]) and words < 250:
        status = "js_required"
    elif _NOT_FOUND.search(str(title or "")) and words < 300:
        status = "not_found"
    elif words < 60 and not structured:
        status = "thin"
    result: dict[str, Any] = {"status": status, "words": words}
    if objective:
        result["relevance"] = relevance(objective, f"{title}\n{body}")
    if published:
        result["published"] = published[:10]
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(published[:10]).replace(tzinfo=timezone.utc)).days
            result["age_days"] = age
        except ValueError:
            pass
    return result


def published_date(*, metadata: dict[str, str], json_ld: list[Any], last_modified: str = "") -> str:
    """The page's own date: JSON-LD, article meta tags, then the Last-Modified header."""
    for block in json_ld or []:
        for item in (block if isinstance(block, list) else [block]):
            if isinstance(item, dict):
                for key in ("dateModified", "datePublished", "uploadDate"):
                    if item.get(key):
                        return str(item[key])[:10]
    for key in ("article:modified_time", "article:published_time", "og:updated_time", "date", "dc.date", "pubdate"):
        if metadata.get(key):
            return str(metadata[key])[:10]
    if last_modified:
        try:
            from email.utils import parsedate_to_datetime
            return parsedate_to_datetime(last_modified).date().isoformat()
        except (TypeError, ValueError):
            return ""
    return ""


QUALITY_NOTES = {
    "js_required": "This page builds its content with JavaScript, so its text isn't available here.",
    "login_wall": "This page is behind a sign-in or paywall; its content isn't available.",
    "blocked": "The site blocked automated access (a bot check).",
    "not_found": "The page says it doesn't exist.",
    "thin": "The page has very little text.",
}


def retrieval_note(quality: dict[str, Any]) -> str:
    """One line the model reads about what this page can and can't support."""
    notes = []
    status = quality.get("status", "ok")
    if status != "ok":
        notes.append(QUALITY_NOTES.get(status, status) + " Don't treat it as evidence; try another source.")
    if "relevance" in quality and quality["relevance"] < 0.34 and status == "ok":
        notes.append("This page barely mentions what was asked; check before relying on it.")
    if quality.get("age_days", 0) > 540:
        notes.append(f"Dated {quality.get('published')}: it may be out of date.")
    return " ".join(notes)


def now_iso() -> str:
    return time.strftime("%Y-%m-%d")
