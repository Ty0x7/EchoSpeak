"""Prove intent/coding/search paths are structural — novel, never-discussed cases."""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_disposable_coding_agent(
    tmp_path,
    monkeypatch,
    thread_id: str,
    project_name: str = "synthetic-shooter",
):
    """Create a Project/Session/ActiveWork authority chain under tmp_path only."""
    from agent import projects as projects_module
    from agent import state as state_module
    from agent.active_work import ActiveWorkStore
    from agent.core import EchoSpeakAgent
    from agent.projects import ProjectManager
    from agent.state import StateStore

    root = tmp_path / project_name
    root.mkdir(parents=True, exist_ok=True)
    (root / "game.js").write_text(
        "const player={hp:100}; const enemies=[]; function update() {}\n",
        encoding="utf-8",
    )
    (root / "index.html").write_text("<canvas id='game'></canvas>\n", encoding="utf-8")
    (root / "style.css").write_text("canvas { display: block; }\n", encoding="utf-8")

    project_manager = ProjectManager(tmp_path / "projects")
    state_store = StateStore(tmp_path / "phase3")
    monkeypatch.setattr(projects_module, "_project_manager", project_manager)
    monkeypatch.setattr(state_module, "_state_store", state_store)

    project = project_manager.attach_folder(str(root), name=project_name, trust_state="trusted")
    state = state_store.update_thread_state(
        thread_id,
        active_project_id=project.id,
        project_path=str(root.resolve()),
        workspace_root=str(root.resolve()),
    )
    agent = EchoSpeakAgent(
        memory_path=str(tmp_path / "memory"),
        manage_background_services=False,
    )
    agent._current_thread_id = thread_id
    agent._state_store = state_store
    agent._execution_context = state
    agent._active_project_id = project.id
    agent._active_work_store = ActiveWorkStore(tmp_path / "active-work")
    return agent, root


def test_product_title_extraction_not_gta_only():
    """Any title with trailer/price/cast structure — not a GTA whitelist."""
    from agent.research import (
        _extract_title_entity,
        _normalize_product_trailer_query,
        _normalize_product_price_query,
        _normalize_product_cast_query,
        resolve_web_search_queries,
    )

    assert "dune" in _extract_title_entity("when does the new dune movie come out").lower()
    tq = _normalize_product_trailer_query("trailer 2 for hollow knight silksong", full_context="")
    assert "silksong" in tq.lower() or "hollow" in tq.lower()
    assert "trailer" in tq.lower()
    pq = _normalize_product_price_query("how much will elden ring dlc cost")
    assert "price" in pq.lower() or "cost" in pq.lower()
    assert "elden" in pq.lower() or "ring" in pq.lower()
    cq = _normalize_product_cast_query("who are the characters in baldurs gate 3")
    assert "cast" in cq.lower() or "characters" in cq.lower()

    # Multi: release + cost for a novel title (no GTA recipe required)
    multi = (
        "when does starfield shattered space expansion release and how much will it cost"
    )
    resolved = resolve_web_search_queries(multi, multi, use_decomposition=True)
    assert len(resolved) >= 1
    rjoin = " ".join(resolved).lower()
    # Should not collapse to only GTA strings
    assert "gta" not in rjoin or "starfield" in rjoin


def test_sports_normalize_is_structural_not_franchise_map():
    """Novel clubs/nations must compact the same way as any prior test team."""
    import agent.research as research
    from agent.research import (
        _normalize_sports_query,
        _extract_vs_sides,
        _infer_city_from_text,
        resolve_web_search_queries,
    )

    assert getattr(research, "_TEAM_CITY", None) is None

    # Free-form next-game for never-discussed club
    q = _normalize_sports_query("when do the Reykjavik Frost play next")
    assert "reykjavik" in q.lower() or "frost" in q.lower()
    assert "next game" in q.lower() or "schedule" in q.lower()
    # Must NOT rewrite to a different hard-coded franchise
    assert "oilers" not in q.lower()
    assert "edmonton oilers" not in q.lower()

    # vs-sides structural (never-discussed nations)
    sides = _extract_vs_sides("who wins Senegal vs Curaçao tomorrow")
    assert "senegal" in sides.lower()
    assert "cura" in sides.lower() or "curacao" in sides.lower().replace("ç", "c")

    # World Cup + novel sides — no country whitelist required
    fifa_q = _normalize_sports_query("fifa world cup Senegal vs Curaçao kickoff tomorrow")
    assert "senegal" in fifa_q.lower()
    assert "fifa" in fifa_q.lower() or "world cup" in fifa_q.lower()

    # City only when explicitly present — never invent from team nickname
    assert _infer_city_from_text("when do the oilers play next") == ""
    assert _infer_city_from_text("weather in Osaka tomorrow").lower().startswith("osaka")
    assert _infer_city_from_text("Osaka weather tomorrow").lower().startswith("osaka")

    # Multi-intent: novel product + novel sports — no GTA/FIFA recipe required
    multi = (
        "when does hollow knight silksong release and how much will it cost "
        "and what matches are happening for the world cup tomorrow"
    )
    resolved = resolve_web_search_queries(multi, multi, use_decomposition=True)
    rjoin = " ".join(resolved).lower()
    assert len(resolved) >= 2
    assert "silksong" in rjoin or "hollow" in rjoin
    assert "world cup" in rjoin or "fifa" in rjoin or "match" in rjoin
    assert "oilers" not in rjoin
    assert "gta" not in rjoin


def test_no_entity_hardcode_strings_in_sports_normalize_source():
    """Production normalizer must not contain franchise rewrite string literals."""
    import inspect
    from agent.research import _normalize_sports_query

    src = inspect.getsource(_normalize_sports_query)
    banned = (
        "Edmonton Oilers",
        "Calgary Flames",
        "Vancouver Canucks",
        "morocco|portugal|spain|brazil",
        '"oilers"',
    )
    for b in banned:
        assert b not in src, f"hardcoded entity residue in _normalize_sports_query: {b}"


def test_live_sports_intent_without_team_whitelist():
    from agent.sports_data import is_live_sports_data_intent, infer_sport_key, infer_team_tokens

    assert is_live_sports_data_intent("what's the Reykjavik Frost score right now") is True
    assert is_live_sports_data_intent("Senegal vs Curaçao score live") is True
    # No league keyword → no invented Odds API sport key
    assert infer_sport_key("Reykjavik Frost score") is None
    toks = infer_team_tokens("Reykjavik Frost score right now")
    assert any("reykjavik" in t or "frost" in t for t in toks)


def test_weather_place_structural_not_city_list():
    from agent.research import _normalize_weather_query, _infer_city_from_text

    assert _infer_city_from_text("what's the weather in Cape Town")
    wq = _normalize_weather_query("weather tomorrow", city_hint="Cape Town")
    assert "cape town" in wq.lower()
    # Bare weather with no place stays generic (does not invent Edmonton)
    bare = _normalize_weather_query("what's the weather tomorrow")
    assert "edmonton" not in bare.lower()
    assert "weather" in bare.lower()


def test_file_edit_resolves_desktop_project_not_echospeak_root():
    """index.html edit during shooter work must hit Desktop/2d-shooter-game, not EchoSpeak."""
    from pathlib import Path
    from agent.tools import set_active_project_root, get_active_project_root, _desktop_root

    desk = _desktop_root()
    proj = desk / "2d-shooter-game"
    if not proj.is_dir():
        return  # skip if not present
    set_active_project_root(str(proj))
    assert get_active_project_root() is not None
    from agent.tools import _candidate_file_path, _file_tool_root

    p = _candidate_file_path("index.html", _file_tool_root())
    assert "2d-shooter-game" in str(p).replace("\\", "/")
    assert "echospeak" not in str(p).lower() or "2d-shooter" in str(p).lower()
    assert p.name == "index.html"


def test_search_query_quality_gate_rejects_fragments():
    """Utterance fragments must not become searches; multi keeps entity-rich queries only."""
    from agent.research import (
        is_viable_search_query,
        quality_gate_search_queries,
        resolve_web_search_queries,
    )

    parent = "what time does the fifa game with france and maracoo start today? pelsae check"
    assert is_viable_search_query("pelsae check", parent=parent) is False
    assert is_viable_search_query("please check", parent=parent) is False
    assert is_viable_search_query("maracoo start today", parent=parent) is False
    good = "FIFA World Cup france maracoo kickoff time ET today"
    assert is_viable_search_query(good, parent=parent) is True

    gated = quality_gate_search_queries(
        [
            "FIFA World Cup match list kickoff times ET each game schedule fixtures",
            "maracoo start today",
            "pelsae check",
        ],
        parent,
    )
    assert len(gated) >= 1
    assert not any("pelsae" in g.lower() for g in gated)
    assert not any(g.lower() == "maracoo start today" for g in gated)
    # Prefer queries that keep the matchup when present
    rjoin = " ".join(gated).lower()
    assert "fifa" in rjoin or "world cup" in rjoin or "france" in rjoin

    # Real multi still fans out cleanly
    multi = (
        "weather in Osaka tomorrow and what matches are happening for the world cup tomorrow"
    )
    resolved = resolve_web_search_queries(multi, multi, use_decomposition=True)
    assert len(resolved) >= 2
    rjoin2 = " ".join(resolved).lower()
    assert "osaka" in rjoin2 or "weather" in rjoin2
    assert "fifa" in rjoin2 or "world cup" in rjoin2 or "match" in rjoin2
    assert not any("please" in x.lower() and len(x.split()) <= 3 for x in resolved)


def test_fifa_matchup_single_query_not_junk_split():
    """Live: France/maracoo + 'pelsae check' must be ONE sports query, not 3 junk ones."""
    from agent.research import (
        resolve_web_search_queries,
        looks_like_multi_intent,
        _extract_vs_sides,
        _normalize_sports_query,
        _prep_search_work_text,
    )

    q = "what time does the fifa game with france and maracoo start today? pelsae check"
    prep = _prep_search_work_text(q)
    assert "pelsae" not in prep.lower()
    assert "please check" not in prep.lower()
    assert looks_like_multi_intent(q) is False
    sides = _extract_vs_sides(q)
    assert "france" in sides.lower()
    assert "maracoo" in sides.lower() or "morocco" in sides.lower()
    sports = _normalize_sports_query(q)
    assert "france" in sports.lower()
    assert "fifa" in sports.lower() or "world cup" in sports.lower()
    assert "kickoff" in sports.lower() or "time" in sports.lower()
    resolved = resolve_web_search_queries(q, q, use_decomposition=True)
    assert len(resolved) == 1, resolved
    r0 = resolved[0].lower()
    assert "france" in r0
    assert "maracoo" in r0 or "morocco" in r0
    assert "pelsae" not in r0
    assert r0 != "maracoo start today"
    assert "pelsae check" not in r0


