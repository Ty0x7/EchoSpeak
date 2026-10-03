"""Keyless sports data from ESPN's public site API.

Replaces The Odds API as the default for scores, schedules, results and
standings: that one needs an ODDS_API_KEY and failed with "API" errors
without it, and couldn't map a bare team name ("Nuggets score?") to a league.

Endpoints (no key, JSON):
- search:     site.web.api.espn.com/apis/common/v3/search?query=...&type=team
- scoreboard: site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard[?dates=YYYYMMDD-YYYYMMDD]
- schedule:   site.api.espn.com/apis/site/v2/sports/{sport}/{league}/teams/{id}/schedule
- standings:  site.api.espn.com/apis/v2/sports/{sport}/{league}/standings

The Odds API is still used for betting odds when a key is set.
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from loguru import logger

from agent.sports_data import SportsLiveResult

SITE = "https://site.api.espn.com/apis/site/v2/sports"
STANDINGS = "https://site.api.espn.com/apis/v2/sports"
SEARCH = "https://site.web.api.espn.com/apis/common/v3/search"

# (pattern, sport, league, label). Order matters: specific before general.
LEAGUES: list[tuple[re.Pattern, str, str, str]] = [
    (re.compile(r"(?i)\bwnba\b"), "basketball", "wnba", "WNBA"),
    (re.compile(r"(?i)\b(ncaab|college basketball|march madness|men'?s college basketball)\b"), "basketball", "mens-college-basketball", "NCAA men's basketball"),
    (re.compile(r"(?i)\b(nba|basketball)\b"), "basketball", "nba", "NBA"),
    (re.compile(r"(?i)\b(ncaaf|college football|cfb)\b"), "football", "college-football", "College football"),
    (re.compile(r"(?i)\b(nfl|super\s*bowl|football)\b"), "football", "nfl", "NFL"),
    (re.compile(r"(?i)\b(mlb|baseball|world series)\b"), "baseball", "mlb", "MLB"),
    (re.compile(r"(?i)\b(nhl|hockey|stanley cup)\b"), "hockey", "nhl", "NHL"),
    (re.compile(r"(?i)\b(premier league|epl)\b"), "soccer", "eng.1", "Premier League"),
    (re.compile(r"(?i)\b(la liga|laliga)\b"), "soccer", "esp.1", "La Liga"),
    (re.compile(r"(?i)\bbundesliga\b"), "soccer", "ger.1", "Bundesliga"),
    (re.compile(r"(?i)\bserie a\b"), "soccer", "ita.1", "Serie A"),
    (re.compile(r"(?i)\bligue 1\b"), "soccer", "fra.1", "Ligue 1"),
    (re.compile(r"(?i)\b(champions league|ucl)\b"), "soccer", "uefa.champions", "Champions League"),
    (re.compile(r"(?i)\b(world cup|fifa)\b"), "soccer", "fifa.world", "World Cup"),
    (re.compile(r"(?i)\b(mls|soccer)\b"), "soccer", "usa.1", "MLS"),
    (re.compile(r"(?i)\b(f1|formula 1|formula one|grand prix)\b"), "racing", "f1", "Formula 1"),
    (re.compile(r"(?i)\b(ufc|mma)\b"), "mma", "ufc", "UFC"),
]
LEAGUE_LABELS = {(sport, league): label for _, sport, league, label in LEAGUES}

_CACHE: dict[str, tuple[float, Any]] = {}
_LOCK = threading.Lock()
_STOP = {
    "the", "a", "an", "score", "scores", "game", "games", "match", "matches", "live", "right", "now", "currently",
    "tonight", "today", "tomorrow", "yesterday", "last", "night", "next", "odds", "spread", "standings", "what",
    "whats", "who", "won", "win", "winning", "for", "of", "and", "vs", "versus", "against", "at", "in", "is", "are",
    "did", "do", "does", "when", "play", "playing", "schedule", "results", "result", "how", "please", "check",
    "get", "me", "my", "show", "this", "week", "weekend", "season", "team", "table", "league",
}


def _get(url: str, ttl: float = 30.0) -> Any:
    with _LOCK:
        hit = _CACHE.get(url)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 (EchoSpeak sports)", "Accept": "application/json"})
    with urlopen(request, timeout=10) as response:
        body = response.read(3_000_001)
    if len(body) > 3_000_000:
        raise ValueError("response too large")
    data = json.loads(body.decode("utf-8"))
    with _LOCK:
        _CACHE[url] = (time.time(), data)
    return data


def find_league(text: str) -> Optional[tuple[str, str]]:
    for pattern, sport, league, _ in LEAGUES:
        if pattern.search(text or ""):
            return sport, league
    return None


def find_team(text: str) -> Optional[dict[str, Any]]:
    """Best ESPN team match for the words left after removing sports filler."""
    words = [w for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9.'&-]*", text or "") if w.lower() not in _STOP]
    candidates: list[str] = []
    for size in (3, 2, 1):
        for i in range(len(words) - size + 1):
            phrase = " ".join(words[i:i + size])
            if phrase.lower() not in {c.lower() for c in candidates}:
                candidates.append(phrase)
    league_hint = find_league(text)
    for phrase in candidates[:8]:
        try:
            data = _get(f"{SEARCH}?{urlencode({'query': phrase, 'limit': 5, 'type': 'team'})}", ttl=3600)
        except Exception:
            continue
        items = [i for i in data.get("items") or [] if i.get("type") == "team" and i.get("sport") and i.get("league", i.get("defaultLeagueSlug"))]
        if league_hint:
            items = [i for i in items if (i.get("sport"), i.get("defaultLeagueSlug") or i.get("league")) == league_hint] or items
        if items:
            team = items[0]
            return {
                "id": str(team.get("id")),
                "name": team.get("displayName") or phrase,
                "sport": team.get("sport"),
                "league": team.get("defaultLeagueSlug") or team.get("league"),
            }
    return None


def _event_row(event: dict[str, Any], league_label: str = "") -> dict[str, Any]:
    comp = (event.get("competitions") or [{}])[0]
    status = (comp.get("status") or event.get("status") or {}).get("type") or {}
    teams = comp.get("competitors") or []

    def side(where: str) -> dict[str, Any]:
        for t in teams:
            if t.get("homeAway") == where:
                return t
        return teams[0 if where == "home" else -1] if teams else {}

    def name(t: dict[str, Any]) -> str:
        return str((t.get("team") or {}).get("displayName") or (t.get("athlete") or {}).get("displayName") or "")

    def logo(t: dict[str, Any]) -> str:
        team = t.get("team") or {}
        if team.get("logo"):
            return str(team["logo"])
        logos = team.get("logos") or []
        return str((logos[0] or {}).get("href") or "") if logos else str(((t.get("athlete") or {}).get("flag") or {}).get("href") or "")

    def record(t: dict[str, Any]) -> str:
        rows = t.get("records") or t.get("record") or []
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            return str(rows[0].get("summary") or rows[0].get("displayValue") or "")
        return ""

    def abbr(t: dict[str, Any]) -> str:
        return str((t.get("team") or {}).get("abbreviation") or "")

    def score(t: dict[str, Any]) -> Optional[str]:
        raw = t.get("score")
        if isinstance(raw, dict):
            raw = raw.get("displayValue") or raw.get("value")
        return None if raw in (None, "") else str(raw)

    home, away = side("home"), side("away")
    state = status.get("state") or ""  # pre | in | post
    odds = (comp.get("odds") or [{}])[0] if comp.get("odds") else {}
    return {
        "home": name(home),
        "away": name(away),
        "home_score": score(home) if state != "pre" else None,
        "away_score": score(away) if state != "pre" else None,
        "state": state,
        "status": status.get("shortDetail") or status.get("description") or "",
        "start": event.get("date") or "",
        "league": league_label,
        "venue": ((comp.get("venue") or {}).get("fullName")) or "",
        "odds": odds.get("details") or "",
        "over_under": odds.get("overUnder"),
        "name": event.get("name") or f"{name(away)} at {name(home)}",
        "home_logo": logo(home), "away_logo": logo(away),
        "home_abbr": abbr(home), "away_abbr": abbr(away),
        "home_record": record(home), "away_record": record(away),
    }


def _local_time(iso: str) -> str:
    try:
        moment = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return iso
    return moment.strftime("%a %b %d, %I:%M %p").replace(" 0", " ")


def _line(row: dict[str, Any]) -> str:
    if row["state"] == "pre":
        text = f"{row['away']} at {row['home']}, {_local_time(row['start'])}"
    else:
        text = f"{row['away']} {row['away_score'] or 0}, {row['home']} {row['home_score'] or 0} ({row['status']})"
    if row.get("odds"):
        text += f"; line {row['odds']}" + (f", O/U {row['over_under']}" if row.get("over_under") else "")
    return text


def _attach_card(title: str, rows: list[dict[str, Any]]) -> None:
    try:
        from agent.lean.widgets import attach, now_label

        attach({"type": "score_card", "data": {"title": title, "as_of": now_label(), "games": [
            {"home": r["home"], "away": r["away"], "home_score": r["home_score"], "away_score": r["away_score"],
             "status": r["status"] if r["state"] != "pre" else _local_time(r["start"]), "league": r["league"],
             "state": r["state"], "start": r["start"], "venue": r.get("venue", ""), "line": r.get("odds", ""),
             "home_logo": r.get("home_logo", ""), "away_logo": r.get("away_logo", ""),
             "home_abbr": r.get("home_abbr", ""), "away_abbr": r.get("away_abbr", ""),
             "home_record": r.get("home_record", ""), "away_record": r.get("away_record", "")}
            for r in rows[:8]
        ]}})
    except Exception:
        logger.debug("score card skipped", exc_info=True)


def _scoreboard(sport: str, league: str, upcoming: bool) -> list[dict[str, Any]]:
    label = LEAGUE_LABELS.get((sport, league), league.upper())
    url = f"{SITE}/{sport}/{league}/scoreboard"
    if not upcoming:
        return [_event_row(e, label) for e in _get(url).get("events") or []]
    # Date ranges aren't accepted for every league; single days are.
    rows: list[dict[str, Any]] = []
    today = datetime.now()
    for offset in range(8):
        day = today + timedelta(days=offset)
        try:
            events = _get(f"{url}?dates={day:%Y%m%d}", ttl=300).get("events") or []
        except Exception:
            continue
        rows += [_event_row(e, label) for e in events]
        if len(rows) >= 12:
            break
    return rows


def _standings(sport: str, league: str) -> str:
    data = _get(f"{STANDINGS}/{sport}/{league}/standings", ttl=600)
    groups = data.get("children") or [data]
    out = []
    for group in groups[:4]:
        entries = ((group.get("standings") or {}).get("entries")) or []
        if not entries:
            continue
        rows = []
        for entry in entries:
            stats = {s.get("name"): s.get("displayValue") for s in entry.get("stats") or []}
            rows.append(((entry.get("team") or {}).get("displayName", "?"), stats))
        sort_key = "playoffSeed" if any(s.get("playoffSeed") for _, s in rows) else "rank"

        def order(item):
            try:
                return float(item[1].get(sort_key) or item[1].get("rank") or 99)
            except ValueError:
                return 99.0

        rows.sort(key=order)
        cols = [c for c in ("wins", "losses", "ties", "points", "winPercent", "gamesBehind") if any(s.get(c) for _, s in rows)]
        header = "| Team | " + " | ".join({"winPercent": "Pct", "gamesBehind": "GB"}.get(c, c.title()) for c in cols) + " |"
        lines = [f"**{group.get('name') or 'Standings'}**", header, "|" + "---|" * (len(cols) + 1)]
        lines += [f"| {team} | " + " | ".join(str(s.get(c) or "") for c in cols) + " |" for team, s in rows[:20]]
        out.append("\n".join(lines))
    return "\n\n".join(out)


def query(text: str, operation: str = "live_scores") -> SportsLiveResult:
    """Scores, schedules, results, standings (and lines when ESPN has them) for a team or league."""
    op = (operation or "live_scores").lower()
    league = find_league(text)
    residual = text or ""
    for pattern, *_ in LEAGUES:
        residual = pattern.sub(" ", residual)
    has_name = any(w.lower() not in _STOP for w in re.findall(r"[A-Za-z][A-Za-z.'&-]*", residual))
    team = find_team(residual + (" " + LEAGUE_LABELS[league] if league else "")) if has_name else None
    if team and not league:
        league = (team["sport"], team["league"])
    if not league:
        return SportsLiveResult(ok=False, mode=op, provider="espn",
                                error="Could not map that to a team or league. Name the team (e.g. 'Nuggets') or league (e.g. 'NBA').")
    sport, league_slug = league
    label = LEAGUE_LABELS.get(league, league_slug.upper())
    try:
        if op == "standings":
            table = _standings(sport, league_slug)
            if not table:
                return SportsLiveResult(ok=False, mode="standings", provider="espn", sport_key=league_slug, error=f"No standings available for {label} right now.")
            return SportsLiveResult(ok=True, mode="standings", provider="espn", sport_key=league_slug, summary=f"{label} standings:\n\n{table}")

        if team:
            data = _get(f"{SITE}/{sport}/{league_slug}/teams/{team['id']}/schedule")
            rows = [_event_row(e, label) for e in data.get("events") or []]
            if not any(r["state"] == "pre" for r in rows):
                # Some leagues (soccer) list only results unless fixtures are asked for.
                try:
                    fixtures = _get(f"{SITE}/{sport}/{league_slug}/teams/{team['id']}/schedule?fixture=true")
                    rows += [_event_row(e, label) for e in fixtures.get("events") or []]
                except Exception:
                    pass
            # Today's scoreboard has live scores the schedule may lag on.
            try:
                live = [r for r in _scoreboard(sport, league_slug, upcoming=False) if team["name"] in (r["home"], r["away"])]
            except Exception:
                live = []
            done = [r for r in rows if r["state"] == "post"]
            ahead = [r for r in rows if r["state"] == "pre"]
            now_playing = [r for r in live if r["state"] == "in"]
            picked = now_playing + (done[-3:] if op in {"results", "live_scores", "scores"} else []) + ahead[:3 if op in {"schedule", "team_next_event"} else 1]
            if not picked:
                picked = (done[-1:] + ahead[:1]) or rows[:1]
            title = f"{team['name']} ({label})"
        else:
            rows = _scoreboard(sport, league_slug, upcoming=op in {"schedule", "competition_next_event"})
            live = [r for r in rows if r["state"] == "in"]
            picked = live + [r for r in rows if r["state"] != "in"]
            picked = picked[:12]
            title = f"{label} {'schedule' if op in {'schedule', 'competition_next_event'} else 'scoreboard'}"
        if not picked:
            return SportsLiveResult(ok=False, mode=op, provider="espn", sport_key=league_slug,
                                    error=f"No {label} games found right now (it may be the off-season).", result_state="no_data")
        _attach_card(title, picked)
        summary = f"{title}, from ESPN (times are local):\n" + "\n".join(f"- {_line(r)}" for r in picked)
        return SportsLiveResult(ok=True, mode=op, provider="espn", sport_key=league_slug, summary=summary, events=picked)
    except Exception as exc:
        logger.warning("ESPN sports request failed: {}", exc)
        return SportsLiveResult(ok=False, mode=op, provider="espn", sport_key=league_slug, error=f"ESPN request failed ({type(exc).__name__}).")
