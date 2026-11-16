"""EchoSpeak's search and research engine, from the user's question to grounded citations.

Real tools (the registry's web_search and safe_web_fetch), the real Lean loop and Chat
execution layer, real ranking and page grading. Only the network is a fixture: search
providers return recorded-shape results and GitHub/PyPI/pages return recorded-shape
responses. No model is called (the model's turns are scripted) and nothing leaves the PC.

Regression: "What's EchoSpeak's latest updates? https://github.com/Ty0x7/EchoSpeak/pulls"
on a 4B model searched broadly, read a GitHub profile and said it couldn't see the PRs.
"""
from __future__ import annotations

import importlib.util
import json
import math
import threading
from pathlib import Path
from typing import Any

import pytest

from agent import safe_web_retrieval, search_rank, source_router, web_search_providers as wsp
from agent.lean import chat_run
from agent.lean.loop import LeanTurn
from agent.lean.personas import AgentPersona
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.toolbox import Toolbox
from agent.safe_web_retrieval import SafePageResult, SafeWebRetrievalError

_spec = importlib.util.spec_from_file_location("search_relevance", Path(__file__).parent / "fixtures" / "search_relevance.py")
relevance_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(relevance_fixture)

REQUEST = "What's EchoSpeak's latest updates? https://github.com/Ty0x7/EchoSpeak/pulls"
PULLS = [
    {"number": 106, "title": "EchoSpeak 11.11.2: Chat answers from evidence", "state": "open", "draft": False, "merged_at": None,
     "updated_at": "2026-10-28T19:02:00Z", "created_at": "2026-10-28T18:50:00Z", "user": {"login": "Ty0x7"},
     "html_url": "https://github.com/Ty0x7/EchoSpeak/pull/106", "body": "Evidence ledger and citation audit for Chat."},
    {"number": 105, "title": "EchoSpeak 11.11.1: a better Chat — diagrams that draw", "state": "closed", "draft": False,
     "merged_at": "2026-10-28T17:40:00Z", "updated_at": "2026-10-28T17:40:00Z", "created_at": "2026-10-28T16:00:00Z",
     "user": {"login": "Ty0x7"}, "html_url": "https://github.com/Ty0x7/EchoSpeak/pull/105", "body": "Mermaid repair and math."},
    {"number": 104, "title": "EchoSpeak 11.11.0: Chat, Agents and Code", "state": "closed", "draft": False,
     "merged_at": "2026-10-28T09:00:00Z", "updated_at": "2026-10-28T09:00:00Z", "created_at": "2026-10-28T08:00:00Z",
     "user": {"login": "Ty0x7"}, "html_url": "https://github.com/Ty0x7/EchoSpeak/pull/104", "body": ""},
]
API = {
    "https://api.github.com/repos/Ty0x7/EchoSpeak/pulls?state=all&sort=updated&direction=desc&per_page=20": PULLS,
    "https://api.github.com/repos/Ty0x7/EchoSpeak": {
        "full_name": "Ty0x7/EchoSpeak", "description": "Local-first personal agent runtime", "stargazers_count": 42,
        "open_issues_count": 3, "default_branch": "main", "pushed_at": "2026-10-28T19:02:00Z",
        "license": {"spdx_id": "MIT"}, "html_url": "https://github.com/Ty0x7/EchoSpeak"},
    "https://api.github.com/repos/Ty0x7/EchoSpeak/releases?per_page=10": [
        {"name": "EchoSpeak 11.10.9", "tag_name": "v11.10.9", "published_at": "2026-10-27T20:00:00Z",
         "html_url": "https://github.com/Ty0x7/EchoSpeak/releases/tag/v11.10.9", "body": "Codex and Claude plans in Echo Code."}],
    "https://pypi.org/pypi/requests/json": {
        "info": {"name": "requests", "version": "2.33.1", "summary": "Python HTTP for Humans.", "requires_python": ">=3.9",
                 "license": "Apache-2.0", "home_page": "https://requests.readthedocs.io"},
        "releases": {"2.33.1": [{"upload_time_iso_8601": "2026-09-30T10:00:00Z"}], "2.33.0": [{"upload_time_iso_8601": "2026-06-01T10:00:00Z"}]}},
}


class Network:
    """The fixture internet: structured APIs and pages, with failures on request."""

    def __init__(self) -> None:
        self.api_calls: list[str] = []
        self.page_calls: list[str] = []
        self.pages: dict[str, Any] = {}
        self.api_failures: dict[str, str] = {}

    def fetch_bytes(self, url, headers=None, timeout_seconds=8.0, max_bytes=3_000_000, max_redirects=3):
        self.api_calls.append(url)
        if url in self.api_failures:
            raise SafeWebRetrievalError(self.api_failures[url], code="http_error")
        if url not in API:
            raise SafeWebRetrievalError("HTTP 404", code="http_error")
        return url, {"content-type": "application/json"}, json.dumps(API[url]).encode()

    def fetch_page(self, url, max_text_chars=24000, **_kwargs):
        self.page_calls.append(url)
        page = self.pages.get(url)
        if isinstance(page, Exception):
            raise page
        if page is None:
            raise SafeWebRetrievalError("Public page returned HTTP 404", code="http_error")
        title, text = page
        return SafePageResult(url=url, final_url=url, title=title, text=text, content_type="text/html", status_code=200)


@pytest.fixture
def net(monkeypatch):
    from agent.tool_registry import ToolRegistry
    from agent.tools import TOOL_METADATA, get_available_tools
    ToolRegistry.register_from_metadata(get_available_tools(), TOOL_METADATA)
    network = Network()
    monkeypatch.setattr(safe_web_retrieval, "fetch_public_bytes", network.fetch_bytes)
    monkeypatch.setattr(safe_web_retrieval, "fetch_public_page", network.fetch_page)
    monkeypatch.setattr(wsp.DuckDuckGoProvider, "extract_url", lambda self, url: "")
    from config import config
    monkeypatch.setattr(config, "web_search_provider", "duckduckgo", raising=False)
    return network


def _provider(monkeypatch, hits_by_query: dict[str, list[tuple]], *, errors: list[str] | None = None, name: str = "duckduckgo"):
    def search(self, query, **_kwargs):
        rows = next((rows for key, rows in hits_by_query.items() if key.lower() in query.lower()), [])
        hits = [wsp.SearchHit(title=t, url=u, snippet=s, provider=name, query=query) for t, u, s, *_ in rows]
        return wsp.SearchProviderResult(hits=hits, provider=name, errors=list(errors or []) if not hits else [], queries_used=[query])
    monkeypatch.setattr(wsp.DuckDuckGoProvider, "search", search)


class Script:
    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = list(turns)
        self.calls: list[dict[str, Any]] = []

    def stream_turn(self, messages, **kwargs):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": list(kwargs.get("tools") or [])})
        turn = self.turns.pop(0)
        if kwargs.get("on_content") and turn.content:
            kwargs["on_content"](turn.content)
        return turn


def _chat(script: Script, user: str, *, profile: str = "compact") -> tuple[LeanTurn, chat_run.ChatRun]:
    box = Toolbox(toolsets=["web"], session_id="t")
    run = chat_run.ChatRun(session_id="t", model="google/gemma-3n-e4b", provider="lmstudio", profile=profile, user_message=user)
    turn = LeanTurn(client=script, persona=AgentPersona(id="chat", name="gemma-3n-e4b"), system_prompt="s", history=[], toolbox=box,
                    session_id="t", request_id="r", execution_id="e", emit=lambda _e: None, cancel=threading.Event(), persist_tool_runs=False)
    turn.tool_observer = run.observe
    turn.final_check = run.check_answer
    turn.max_steps_override = run.budget
    turn.final_grace = True
    turn.grace_prompt = chat_run.GRACE_PROMPT
    if "safe_web_fetch" in box.names:
        turn.prefetch = run.prefetch
    return turn, run


def _call(name: str, **args: Any) -> ModelTurn:
    return ModelTurn(tool_calls=[ToolCall(f"c{abs(hash(json.dumps(args, sort_keys=True))) % 10**6}", name, json.dumps(args))])


# ── 1. The reproduced failure ────────────────────────────────────────────────

def test_the_supplied_github_url_is_read_through_the_api_before_the_model_answers(net):
    answer = ("The latest updates are [#106 Chat answers from evidence](https://github.com/Ty0x7/EchoSpeak/pull/106) (open), "
              "and the merged [#105 diagrams that draw](https://github.com/Ty0x7/EchoSpeak/pull/105).")
    script = Script([ModelTurn(content=answer)])
    turn, run = _chat(script, REQUEST)
    result = turn.run(REQUEST)
    first = "\n".join(str(m.get("content") or "") for m in script.calls[0]["messages"])
    # Before the model's first call, the pull requests are already in its context, from the API.
    assert "Pull requests in Ty0x7/EchoSpeak" in first and "#106 EchoSpeak 11.11.2: Chat answers from evidence" in first
    assert net.api_calls == ["https://api.github.com/repos/Ty0x7/EchoSpeak/pulls?state=all&sort=updated&direction=desc&per_page=20"]
    assert net.page_calls == []  # no scraping, no generic search
    trace = run.trace(steps=2, stop_reason=result.stop_reason, answer=result.text)
    assert trace["corrections"] == [] and trace["citations"]["unknown"] == [] and trace["citations"]["unsupported"] == []
    assert len(trace["citations"]["supported"]) == 2  # each PR's own URL came from the API listing that was read
    assert result.text == answer


def test_same_question_without_a_url_reads_echospeaks_own_repository_not_a_namesake(net):
    # Live test (11.11.3): with no link, a search found other apps called EchoSpeak. The app knows its own source.
    release = "https://github.com/Ty0x7/EchoSpeak/releases/tag/v11.10.9"
    answer = (f"The newest pull request is [#106](https://github.com/Ty0x7/EchoSpeak/pull/106); the latest release is "
              f"[EchoSpeak 11.10.9]({release}).")
    script = Script([ModelTurn(content=answer)])
    turn, run = _chat(script, "What's EchoSpeak's latest updates?")
    result = turn.run("What's EchoSpeak's latest updates?")
    first = "\n".join(str(m.get("content") or "") for m in script.calls[0]["messages"])
    assert "Pull requests in Ty0x7/EchoSpeak" in first and "Releases of Ty0x7/EchoSpeak" in first
    assert net.page_calls == [] and len(script.calls) == 1  # no web search, no namesakes
    trace = run.trace(steps=2, stop_reason=result.stop_reason, answer=result.text)
    assert sorted(trace["citations"]["supported"]) == sorted(["https://github.com/Ty0x7/EchoSpeak/pull/106", release])


def test_searching_for_the_project_ranks_the_repository_over_the_profile_and_reads_it(net, monkeypatch):
    _provider(monkeypatch, {"echospeak": relevance_fixture.CASES[0]["hits"]})
    box = Toolbox(toolsets=["web"], session_id="t")
    result = box.run("web_search", {"query": "EchoSpeak latest updates", "read": 1})
    order = search_rank.urls_in(result.output.split("## Pages read")[0])
    assert order.index("https://github.com/Ty0x7/EchoSpeak") < order.index("https://github.com/Ty0x7")
    # The project's own repository or releases, read through the GitHub API; never the profile or the namesake shop.
    assert net.api_calls[0] in {"https://api.github.com/repos/Ty0x7/EchoSpeak", "https://api.github.com/repos/Ty0x7/EchoSpeak/releases?per_page=10"}
    assert net.page_calls == [] and result.meta["pages"] == 1


# ── 2. Official sources and alternative extraction ───────────────────────────

def test_official_package_release_info_comes_from_the_registry_api(net):
    from agent.tools import safe_web_fetch
    out = safe_web_fetch.invoke({"url": "https://pypi.org/project/requests/", "objective": "latest requests version"})
    assert "requests 2.33.1 on PyPI" in out and "2.33.1 (2026-09-30)" in out and '"source": "pypi_api"' in out
    assert net.page_calls == []


def test_a_rate_limited_api_falls_back_to_reading_the_page_and_says_so(net):
    url = "https://api.github.com/repos/Ty0x7/EchoSpeak/pulls?state=all&sort=updated&direction=desc&per_page=20"
    net.api_failures[url] = "HTTP 403"
    net.pages["https://github.com/Ty0x7/EchoSpeak/pulls"] = ("Pull requests · Ty0x7/EchoSpeak",
        "EchoSpeak pull requests: EchoSpeak 11.11.2 Chat answers from evidence #106 opened by Ty0x7. " * 6)
    from agent.tools import safe_web_fetch
    out = safe_web_fetch.invoke({"url": "https://github.com/Ty0x7/EchoSpeak/pulls", "objective": "EchoSpeak updates"})
    assert "API couldn't answer" in out and "Chat answers from evidence" in out
    assert net.page_calls == ["https://github.com/Ty0x7/EchoSpeak/pulls"]


def test_privacy_mode_blocks_structured_apis_like_any_page(net, monkeypatch):
    blocked = SafeWebRetrievalError("Private mode is on: web pages are off", code="privacy")
    monkeypatch.setattr(safe_web_retrieval, "fetch_public_bytes", lambda *a, **k: (_ for _ in ()).throw(blocked))
    net.pages["https://pypi.org/project/requests/"] = blocked
    from agent.tools import safe_web_fetch
    out = safe_web_fetch.invoke({"url": "https://pypi.org/project/requests/"})
    assert "safe_fetch_error=privacy" in out


# ── 3. Ranking quality, measured ─────────────────────────────────────────────

def _ndcg(grades: list[int], k: int = 3) -> float:
    dcg = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(grades[:k]))
    ideal = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(sorted(grades, reverse=True)[:k]))
    return dcg / ideal if ideal else 0.0


def ranking_metrics() -> dict[str, float]:
    """Top-1 relevance and nDCG@3, provider order vs ranked, over the labelled fixture (also used by the report)."""
    base_top, ranked_top, base_ndcg, ranked_ndcg = [], [], [], []
    for case in relevance_fixture.CASES:
        grade = {search_rank.canonical(u): g for _t, u, _s, g in case["hits"]}
        provider = [g for _t, _u, _s, g in case["hits"]]
        hits = [wsp.SearchHit(title=t, url=u, snippet=s) for t, u, s, _g in case["hits"]]
        ranked = [grade[search_rank.canonical(h.url)] for h in search_rank.rank_hits(case["query"], [hits])]
        seen_base, dedup_base = set(), []
        for _t, u, _s, g in case["hits"]:  # the old exact-URL dedupe kept tracking-parameter duplicates
            if u.lower() not in seen_base:
                seen_base.add(u.lower())
                dedup_base.append(g)
        base_top.append(provider[0] == 2)
        ranked_top.append(ranked[0] == 2)
        base_ndcg.append(_ndcg(dedup_base))
        ranked_ndcg.append(_ndcg(ranked))
    n = len(relevance_fixture.CASES)
    return {"cases": n, "top1_provider": sum(base_top) / n, "top1_ranked": sum(ranked_top) / n,
            "ndcg3_provider": round(sum(base_ndcg) / n, 3), "ndcg3_ranked": round(sum(ranked_ndcg) / n, 3)}


def test_ranking_puts_an_answering_source_first_and_beats_provider_order():
    metrics = ranking_metrics()
    assert metrics["top1_ranked"] == 1.0, metrics
    assert metrics["ndcg3_ranked"] >= 0.9 and metrics["ndcg3_ranked"] > metrics["ndcg3_provider"] + 0.2, metrics
    assert metrics["top1_provider"] <= 0.25, metrics  # the fixture's realistic failure shapes


def test_duplicates_collapse_and_off_topic_results_are_not_opened():
    case = relevance_fixture.CASES[1]
    ranked = search_rank.rank_hits(case["query"], [[wsp.SearchHit(title=t, url=u, snippet=s) for t, u, s, _g in case["hits"]]])
    canon = [search_rank.canonical(h.url) for h in ranked]
    assert len(canon) == len(set(canon)) == 4
    shirts = wsp.SearchHit(title="Football shirts sale", url="https://shop.example.net/shirts", snippet="Buy shirts")
    assert not search_rank.is_relevant(shirts, "who won the 2026 world cup final")


def test_model_written_queries_reach_the_provider_as_written():
    queries = ["python 3.14 release notes", "node.js 22 changelog", "react 19.2 features", "gpt-5.5 pricing", "site:github.com Ty0x7/EchoSpeak",
               "rust 1.90 release", "Next.js 16 app router", "who won the 2026 world cup final", "kubernetes v1.34 deprecations", ".NET 10 what's new"]
    assert [search_rank.prepare_query(q) for q in queries] == queries
    assert sum(wsp.normalize_provider_query(q) != q for q in queries) >= 9  # what the old path did to them
    assert search_rank.prepare_query("Search for chances edmonton oilers win the standly cup") == "chances edmonton oilers win the stanley cup"


# ── 4. Failures and recovery ─────────────────────────────────────────────────

def test_failed_blocked_and_empty_pages_are_skipped_for_the_next_good_one(net, monkeypatch):
    hits = [("Release notes for widget 9", "https://a.example/widget-9", "widget 9 release notes"),
            ("Widget 9 changelog", "https://b.example/widget-9", "widget 9 changes"),
            ("Widget 9 announced", "https://c.example/widget-9", "widget 9 is out"),
            ("Widget 9 review", "https://d.example/widget-9", "widget 9 hands-on")]
    _provider(monkeypatch, {"widget": hits})
    net.pages["https://a.example/widget-9"] = SafeWebRetrievalError("timed out", code="timeout")
    net.pages["https://b.example/widget-9"] = ("Sign in", "Please sign in to continue reading this widget article.")
    net.pages["https://c.example/widget-9"] = ("Widget 9 announced", "Widget 9 ships on 3 November with a faster widget engine. " * 12)
    net.pages["https://d.example/widget-9"] = ("Widget 9 review", "Our widget 9 review: fast, quiet and cheap. " * 12)
    box = Toolbox(toolsets=["web"], session_id="t")
    result = box.run("web_search", {"query": "widget 9 release notes", "read": 2})
    assert net.page_calls == ["https://a.example/widget-9", "https://b.example/widget-9", "https://c.example/widget-9", "https://d.example/widget-9"]
    assert result.meta["pages"] == 2 and "### https://c.example/widget-9" in result.output and "### https://d.example/widget-9" in result.output
    assert "https://a.example/widget-9: timeout" in result.output and "https://b.example/widget-9: behind a sign-in" in result.output


def test_a_provider_outage_falls_through_to_the_next_provider(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(wsp.BraveProvider, "search", lambda self, q, **k: wsp.SearchProviderResult(provider="brave", errors=["Brave: HTTP 503"], queries_used=[q]))
    monkeypatch.setattr(wsp.DuckDuckGoProvider, "search", lambda self, q, **k: wsp.SearchProviderResult(
        hits=[wsp.SearchHit(title="Widget 9 notes", url="https://c.example/widget-9", snippet="widget 9", provider="duckduckgo", query=q)],
        provider="duckduckgo", queries_used=[q]))
    cfg = SimpleNamespace(brave_search_api_key="k", searxng_base_url="", web_search_provider="brave", web_search_timeout=3, web_search_max_results=5, tavily_api_key="")
    result = wsp.run_web_search("widget 9 notes", config=cfg, enrich_extract=False, max_hits=3)
    assert [h.url for h in result.hits] == ["https://c.example/widget-9"] and "Brave: HTTP 503" in result.errors
    monkeypatch.setattr(wsp.DuckDuckGoProvider, "search", lambda self, q, **k: wsp.SearchProviderResult(provider="duckduckgo", errors=["DDG: rate limit 429"], queries_used=[q]))
    failed = wsp.run_web_search("widget 9 notes", config=cfg, enrich_extract=False, max_hits=3)
    assert not failed.hits and "rate-limiting" in wsp.describe_search_failure(failed.errors)


def test_page_grading():
    assess = source_router.assess
    assert assess(title="App", text="You need to enable JavaScript to run this app.")["status"] == "js_required"
    assert assess(title="News", text="Subscribe to continue reading. " * 3)["status"] == "login_wall"
    assert assess(title="Just a moment...", text="Verify you are human by completing the action below.")["status"] == "blocked"
    assert assess(title="404 Not Found", text="The page you requested does not exist.")["status"] == "not_found"
    good = assess(title="Widget 9", text="Widget 9 ships with a faster widget engine. " * 20, objective="widget 9 engine", published="2023-01-02")
    assert good["status"] == "ok" and good["relevance"] == 1.0 and good["age_days"] > 540
    assert "out of date" in source_router.retrieval_note(good)
    assert source_router.published_date(metadata={"article:published_time": "2026-10-01T10:00:00Z"}, json_ld=[]) == "2026-10-01"


# ── 5. Grounding: relevance, conflicts, small models ─────────────────────────

def _page_output(url: str, text: str, relevance: float, status: str = "ok") -> str:
    return (f"execution_status=success\nresult_state=data_found\n{json.dumps({'url': url, 'text': text})}\n"
            f"quality={json.dumps({'status': status, 'relevance': relevance, 'source': 'page'})}")


def test_conflicting_sources_are_both_evidence_and_an_off_topic_page_is_not_support():
    run = chat_run.ChatRun(session_id="t", user_message="When does widget 9 ship?")
    run.observe("safe_web_fetch", {"url": "https://vendor.example/widget-9"}, True, _page_output("https://vendor.example/widget-9", "ships 3 Nov", 1.0), 1)
    run.observe("safe_web_fetch", {"url": "https://blog.example/widget-9"}, True, _page_output("https://blog.example/widget-9", "ships 10 Nov", 0.67), 1)
    run.observe("safe_web_fetch", {"url": "https://other.example/gardening"}, True, _page_output("https://other.example/gardening", "tomatoes", 0.0), 1)
    both = ("The vendor says 3 November ([vendor](https://vendor.example/widget-9)); a blog says 10 November "
            "([blog](https://blog.example/widget-9)). The vendor is the primary source.")
    assert run.check_answer(both) == "" and len(run.audit["supported"]) == 2
    run2 = chat_run.ChatRun(session_id="t", user_message="When does widget 9 ship?", pages=dict(run.pages), read=set(run.read),
                            seen=set(run.seen), evidence=list(run.evidence))
    correction = run2.check_answer("It ships 3 November ([source](https://other.example/gardening)).")
    assert "couldn't support the answer" in correction and run2.audit["unsupported"] == ["https://other.example/gardening"]


def test_a_failed_page_is_not_evidence_even_if_opened():
    run = chat_run.ChatRun(session_id="t", user_message="widget 9")
    run.observe("safe_web_fetch", {"url": "https://a.example/w"}, True, "execution_status=error\nsafe_fetch_error=timeout: timed out", 1)
    assert run.read == set() and run.trace()["evidence"]["pages_rejected"] == {"https://a.example/w": "timeout: timed out"}


def test_small_model_budget_counts_the_prefetch_and_needs_no_url_noticing(net):
    script = Script([ModelTurn(content="Here are the latest: [#106](https://github.com/Ty0x7/EchoSpeak/pull/106).")])
    turn, run = _chat(script, REQUEST, profile="compact")
    result = turn.run(REQUEST)
    assert run.budget == 6 and len(script.calls) == 1  # one model call: the reading was done for it
    assert result.stop_reason in {"", "answered"}
    no_url = chat_run.ChatRun(session_id="t", user_message="What's new in Python 3.14?")
    assert no_url.prefetch() == []  # nothing is fetched unless the user gave an address
