"""System check (agent/health.py): broken capabilities are reported in plain words
with a fix, before the user finds out mid-chat."""
from __future__ import annotations

from types import SimpleNamespace

from agent import health


def _config(**keys: str) -> SimpleNamespace:
    base = {"brave_search_api_key": "", "tavily_api_key": "", "searxng_base_url": ""}
    base.update(keys)
    return SimpleNamespace(**base)


def test_web_search_ok_with_the_built_in_search(monkeypatch):
    monkeypatch.setattr(health, "_imports", lambda *m: "")
    check = health.web_search(_config())
    assert (check["status"], check["detail"]) == ("ok", "Using DuckDuckGo (no key needed).")


def test_web_search_fails_clearly_when_search_is_missing(monkeypatch):
    monkeypatch.setattr(health, "_imports", lambda *m: "ddgs")
    check = health.web_search(_config())
    assert check["status"] == "fail" and "missing from this install" in check["detail"]
    assert "Settings › Web search" in check["fix"]


def test_a_search_key_keeps_search_working_without_the_fallback(monkeypatch):
    monkeypatch.setattr(health, "_imports", lambda *m: "ddgs")
    check = health.web_search(_config(brave_search_api_key="k"))
    assert check["status"] == "warn" and check["detail"].startswith("Using Brave.")
    monkeypatch.setattr(health, "_imports", lambda *m: "")
    assert health.web_search(_config(tavily_api_key="k"))["status"] == "ok"


def test_page_reading_warns_with_the_simple_reader(monkeypatch):
    monkeypatch.setattr(health, "_installed", lambda *m: False)
    check = health.page_reading()
    assert check["status"] == "warn" and check["fix"] == health.UPDATE_FIX


def test_check_all_caches_and_survives_a_broken_check(monkeypatch):
    calls = {"n": 0}

    def fine():
        calls["n"] += 1
        return health._check("fine", "Fine", "ok", "ok")

    def broken():
        raise RuntimeError("boom")

    monkeypatch.setattr(health, "CHECKS", [fine, broken])
    monkeypatch.setattr(health, "_cache", None)
    first = health.check_all()
    assert [item["status"] for item in first] == ["ok", "warn"] and "boom" in first[1]["detail"]
    health.check_all()
    assert calls["n"] == 1
    health.check_all(force=True)
    assert calls["n"] == 2


def test_the_agent_hears_about_broken_search_only(monkeypatch):
    monkeypatch.setattr(health, "web_search", lambda: health._check("web_search", "Web search", "ok", "fine"))
    monkeypatch.setattr(health, "page_reading", lambda: health._check("page_reading", "Reading web pages", "ok", "fine"))
    assert health.prompt_note() == ""
    monkeypatch.setattr(health, "web_search", lambda: health._check(
        "web_search", "Web search", "fail", "Not working: the built-in search is missing.", "Add a key in Settings › Web search."))
    note = health.prompt_note()
    assert note.startswith("Known problems on this computer right now: Web search: Not working")
    assert note.endswith("Add a key in Settings › Web search.")


def test_capabilities_endpoint_counts_problems(monkeypatch):
    from api.routes import system

    monkeypatch.setattr(health, "check_all", lambda force=False: [
        health._check("web_search", "Web search", "fail", "x", "y"),
        health._check("model", "Model", "ok", "z"),
    ])
    body = system.health_capabilities()
    assert body["problems"] == 1 and body["items"][0]["id"] == "web_search"
