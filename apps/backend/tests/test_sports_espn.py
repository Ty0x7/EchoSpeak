"""sports_live uses ESPN's keyless feeds: team names resolve to a league, results show a score card."""

from __future__ import annotations

from agent import sports_espn
from agent.lean import widgets


def _event(home, away, state, home_score="0", away_score="0", date="2026-10-04T23:00Z"):
    return {
        "name": f"{away} at {home}",
        "date": date,
        "competitions": [{
            "status": {"type": {"state": state, "shortDetail": {"post": "Final", "in": "Q3 4:12", "pre": "Sun 5:00 PM"}[state]}},
            "competitors": [
                {"homeAway": "home", "team": {"displayName": home}, "score": home_score},
                {"homeAway": "away", "team": {"displayName": away}, "score": away_score},
            ],
        }],
    }


def test_team_name_alone_finds_its_league_and_games(monkeypatch):
    def fake_get(url, ttl=30.0):
        if "search" in url:
            return {"items": [{"type": "team", "id": "7", "displayName": "Denver Nuggets", "sport": "basketball", "defaultLeagueSlug": "nba"}]}
        if "/teams/7/schedule" in url:
            return {"events": [_event("Denver Nuggets", "Utah Jazz", "post", "112", "98"), _event("Los Angeles Lakers", "Denver Nuggets", "pre")]}
        return {"events": [_event("Denver Nuggets", "Phoenix Suns", "in", "70", "66")]}

    monkeypatch.setattr(sports_espn, "_get", fake_get)
    result, cards = widgets.collect(lambda: sports_espn.query("Nuggets score?", "live_scores"))
    assert result.ok and result.provider == "espn" and result.sport_key == "nba"
    text = result.as_tool_text()
    assert "Phoenix Suns 66, Denver Nuggets 70 (Q3 4:12)" in text  # live game first
    assert "Utah Jazz 98, Denver Nuggets 112 (Final)" in text
    assert cards[0]["type"] == "score_card" and cards[0]["data"]["games"][0]["home_score"] == 70


def test_unknown_names_get_a_clear_message_not_an_api_error(monkeypatch):
    monkeypatch.setattr(sports_espn, "_get", lambda url, ttl=30.0: {"items": []})
    result = sports_espn.query("blah blah", "live_scores")
    assert not result.ok and "Name the team" in result.error and "API" not in result.error


def test_standings_become_a_table(monkeypatch):
    standings = {"children": [{"name": "Western Conference", "standings": {"entries": [
        {"team": {"displayName": "Denver Nuggets"}, "stats": [{"name": "wins", "displayValue": "3"}, {"name": "losses", "displayValue": "1"}, {"name": "playoffSeed", "displayValue": "1"}]},
    ]}}]}
    monkeypatch.setattr(sports_espn, "_get", lambda url, ttl=30.0: standings)
    result = sports_espn.query("NBA standings", "standings")
    assert result.ok and "| Denver Nuggets | 3 | 1 |" in result.summary
