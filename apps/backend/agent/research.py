import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
from urllib.parse import urlparse

_RECENT_TERMS = {
    "news",
    "latest",
    "recent",
    "today",
    "update",
    "breaking",
    "headline",
    "war",
    "conflict",
    "crisis",
    "yesterday",
    "this week",
    "tonight",
}


_SCHEDULE_TERMS = {
    "schedule",
    "next game",
    "next match",
    "upcoming",
    "kickoff",
    "start time",
    "fixture",
    "who plays",
    "who is playing",
    "who's playing",
    "who playing",
    "playing today",
    "playing tomorrow",
    "playing tonight",
    "games today",
    "games tomorrow",
    "games tonight",
    "matches today",
    "matches tomorrow",
    "matches tonight",
    "what games",
    "what matches",
    "on tomorrow",
    "for tomorrow",
}

# Speech/typo fixes that are *structural* (day words, politeness, compound forms).
# Not entity/team routing maps — only words that break *parsing* when mangled.
_SPELLING_FIXES = {
    "wordlcup": "world cup",
    "worldcup": "world cup",
    "tommrrow": "tomorrow",
    "tommorow": "tomorrow",
    "tommorrow": "tomorrow",
    "tomorow": "tomorrow",
    "tomorro": "tomorrow",
    "todya": "today",
    "yesteday": "yesterday",
    "yesturday": "yesterday",
    "yeterday": "yesterday",
    "schedul": "schedule",
    "schdule": "schedule",
    "scroe": "score",
    "socre": "score",
    "scors": "scores",
    "reults": "results",
    "resutls": "results",
    "standigns": "standings",
    "playffs": "playoffs",
    "playofs": "playoffs",
    "champoins": "champions",
    "champons": "champions",
    "leauge": "league",
    "legaue": "league",
    "premire": "premier",
    "weahter": "weather",
    "weathr": "weather",
    "forcast": "forecast",
    "forecst": "forecast",
    "temprature": "temperature",
    "temperture": "temperature",
    "bitconi": "bitcoin",
    "bitcone": "bitcoin",
    "etherium": "ethereum",
    "etheruem": "ethereum",
    "electon": "election",
    "elction": "election",
    "hurrican": "hurricane",
    "hurricaine": "hurricane",
    "earthquak": "earthquake",
    "earthquke": "earthquake",
    "comparson": "comparison",
    "comparsion": "comparison",
    "diffrence": "difference",
    "differece": "difference",
    "relase": "release",
    "realease": "release",
    "reveiw": "review",
    "reivew": "review",
    "movei": "movie",
    "moive": "movie",
    "traler": "trailer",
    "trialer": "trailer",
    # Politeness STT (must not become its own search: "pelsae check")
    "pelsae": "please",
    "plese": "please",
    "plase": "please",
    "pealse": "please",
}

# High-frequency entities for fuzzy typo correction (edit-distance matching).
# These are common search terms that speech-to-text and fast typing often mangle.
_FUZZY_ENTITIES = {
    # Sports
    "fifa", "champions", "league", "premier", "basketball", "football",
    "soccer", "baseball", "hockey", "tennis", "olympics", "playoffs",
    "standings", "tournament", "championship", "semifinals", "quarterfinals",
    "stanley", "match", "matches", "schedule", "scores", "results", "highlights",
    # Teams
    "lakers", "warriors", "celtics", "knicks", "yankees", "dodgers",
    "cowboys", "patriots", "eagles", "chiefs", "arsenal", "chelsea",
    "liverpool", "barcelona", "madrid", "juventus", "bayern", "manchester",
    # General search
    "weather", "forecast", "temperature", "bitcoin", "ethereum",
    "cryptocurrency", "election", "president", "earthquake", "hurricane",
    "trailer", "release", "review", "comparison", "difference",
    "between", "versus", "tonight", "tomorrow", "yesterday", "schedule",
    "movie", "score", "game", "today",
}

# Common English words that should NOT trigger fuzzy correction
_COMMON_STOP = {
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were",
    "be", "been", "being", "have", "has", "had", "do", "does", "did",
    "will", "would", "could", "should", "may", "might", "shall", "can",
    "not", "no", "yes", "for", "to", "from", "with", "at", "by", "in",
    "on", "of", "it", "its", "this", "that", "these", "those", "my",
    "your", "his", "her", "our", "their", "me", "him", "them", "us",
    "who", "what", "when", "where", "whether", "how", "why", "which", "all", "each",
    "some", "any", "few", "more", "most", "other", "than", "then", "so",
    "if", "up", "out", "about", "into", "over", "after", "also", "just",
    "like", "very", "too", "here", "there", "now", "even", "still",
    "back", "well", "much", "many", "only", "also", "just", "get", "got",
    "let", "put", "say", "tell", "give", "take", "come", "go", "make",
    "know", "think", "see", "look", "want", "use", "find", "need",
    "hey", "echo", "please", "check", "okay", "real", "quick",
}


def _levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(a) < len(b):
        return _levenshtein(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (ca != cb)))
        prev = curr
    return prev[-1]


def _fuzzy_correct_word(word: str) -> str:
    """Fuzzy-correct a word against high-frequency search entities.

    Only corrects if:
    - Word is >= 4 chars and not a common English word
    - Edit distance is 1 (for words 4-5 chars) or <= 2 (for words 6+ chars)
    - There's a single unambiguous match (not multiple candidates)
    """
    low = word.lower()
    if len(low) < 4:
        return word
    if low in _COMMON_STOP:
        return word
    if low in _FUZZY_ENTITIES:
        return word  # already correct

    best_match = None
    best_dist = 999
    ambiguous = False

    max_dist = 1 if len(low) <= 5 else 2

    for entity in _FUZZY_ENTITIES:
        # Quick length filter — edit distance can't be less than length difference
        if abs(len(low) - len(entity)) > max_dist:
            continue
        dist = _levenshtein(low, entity)
        if dist <= max_dist:
            if dist < best_dist:
                best_dist = dist
                best_match = entity
                ambiguous = False
            elif dist == best_dist and best_match != entity:
                ambiguous = True

    if best_match and not ambiguous:
        # Preserve original casing style
        if word.isupper():
            return best_match.upper()
        if word[:1].isupper():
            return best_match[:1].upper() + best_match[1:]
        return best_match
    return word


_WEATHER_TERMS = {
    "weather",
    "forecast",
    "temperature",
    "temp",
    "humidity",
    "precipitation",
    "rain",
    "snow",
    "wind chill",
    "feels like",
    "high of",
    "low of",
    "°c",
    "°f",
}


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def extract_research_query(input_text: str) -> str:
    raw = str(input_text or "").strip()
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            query = parsed.get("query")
            if query is not None:
                return normalize_web_search_query(_normalize_text(query))
    except Exception:
        pass

    match = re.search(r"query\s*[:=]\s*['\"]([^'\"]+)['\"]", raw, flags=re.IGNORECASE)
    if match:
        return normalize_web_search_query(_normalize_text(match.group(1)))
    return normalize_web_search_query(_normalize_text(raw))


# Social / chat filler that must never ship to Tavily as the search string.
_SOCIAL_OPEN_RE = re.compile(
    r"(?i)\b(?:"
    r"how(?:'re| are) you(?:\s+feeling)?|"
    r"how(?:'s| is) it going|"
    r"how you doing|"
    r"how(?:'re| are) things|"
    r"how(?:'s| is) everything|"
    r"what(?:'s| is) up|"
    r"wyd|"
    r"how(?:'s| is) your day|"
    r"good (?:morning|afternoon|evening|night)"
    r")\b[^.?!]*[.?!]?"
)
_CHAT_FILLER_RE = re.compile(
    r"(?i)\b(?:"
    r"can you|could you|would you|please|pls|"
    r"i wonder(?:ing)?|i was wondering|just wondering|"
    r"tell me|do you know|any idea|any news|"
    r"hey|hi|hello|yo|sup|"
    r"for me|thanks|thank you|thx|"
    r"real quick|quickly|btw|by the way"
    r")\b"
)
_RELEASE_DATE_RE = re.compile(
    r"(?i)(?:"
    r"release\s*date|"
    r"when\s+(?:does|is|will|do|did)\b.+\b(?:come\s+out|coming\s+out|release|released|drop(?:ping)?|launch(?:ing|es)?)\b|"
    r"\bcomes?\s+out\b|"
    r"\bcoming\s+out\b"
    r")"
)

# Stopwords stripped when extracting free-form team / place / vs sides (structure only).
_SPORTS_STOP = {
    "when", "is", "the", "next", "upcoming", "game", "games", "match", "matches",
    "schedule", "for", "of", "what", "time", "times", "a", "an", "do", "does",
    "did", "play", "plays", "playing", "who", "whom", "whose", "are", "was",
    "were", "will", "would", "can", "could", "please", "check", "look", "up",
    "get", "me", "my", "our", "their", "and", "or", "also", "then", "today",
    "tonight", "tomorrow", "this", "that", "week", "weekend", "score", "scores",
    "live", "right", "now", "fixture", "fixtures", "kickoff", "kick", "off",
    "versus", "vs", "against", "at", "on", "in", "to", "from", "with", "about",
    "happening", "being", "played", "explain", "tell", "show", "find", "search",
}


def apply_spelling_fixes(text: str) -> str:
    """Apply structural speech/typo fixes + fuzzy entity correction.

    Two-pass: 1) Static dict for known compound/day/politeness fixes.
    2) Levenshtein fuzzy match for high-frequency search entities.
    """
    s = str(text or "")
    if not s:
        return s
    # Multi-word first
    low = s.lower()
    for bad, good in (("wordl cup", "world cup"), ("worldcup", "world cup"), ("wordlcup", "world cup")):
        if bad in low:
            s = re.sub(re.escape(bad), good, s, flags=re.IGNORECASE)
            low = s.lower()

    def _fix_token(m: re.Match) -> str:
        word = m.group(0)
        key = re.sub(r"[^a-z0-9]", "", word.lower())
        # Pass 1: static dictionary
        fixed = _SPELLING_FIXES.get(key)
        if fixed:
            if word.isupper():
                return fixed.upper()
            if word[:1].isupper():
                return fixed[:1].upper() + fixed[1:]
            return fixed
        # Pass 2: fuzzy entity correction
        return _fuzzy_correct_word(word)

    return re.sub(r"[A-Za-z][A-Za-z']*", _fix_token, s)


_PLACE_STOP = {
    "the", "a", "an", "my", "our", "today", "tonight", "tomorrow",
    "weather", "forecast", "temperature", "temp", "me", "you", "us",
    "this", "that", "week", "weekend", "morning", "evening", "night",
    "what", "whats", "what's", "check", "look", "get", "tell", "please",
    "can", "could", "would", "how", "is", "are", "like", "for", "me",
    "check the", "what the", "whats the", "what's the", "the weather",
    # League/sport tokens must never become "city" from "for fifa" / "in nhl"
    "fifa", "nhl", "nba", "nfl", "mlb", "uefa", "mls", "soccer", "football",
    "hockey", "basketball", "baseball", "world", "cup", "matches", "match",
    "games", "game", "score", "schedule", "fixture", "fixtures",
}


def _trim_place_candidate(place: str) -> str:
    """Keep leading place tokens; stop before day/weather/chat stopwords."""
    toks = []
    for tok in (place or "").split():
        if tok.lower() in _PLACE_STOP:
            break
        if re.search(r"(?i)^(high|low|degrees?)$", tok):
            break
        toks.append(tok)
    return " ".join(toks).strip()


def _looks_like_place(place: str) -> bool:
    p = _trim_place_candidate(place)
    if not p or len(p) < 2:
        return False
    low = p.lower()
    if low in _PLACE_STOP:
        return False
    if any(tok in _PLACE_STOP for tok in low.split()):
        return False
    if re.search(r"(?i)\b(high|low|degrees?|check|weather|forecast)\b", p):
        return False
    return True


def _infer_city_from_text(text: str) -> str:
    """
    Structural place extraction only — never invents a city from a team nickname.

    Accepts: 'in Denver', 'weather for Osaka', leading 'Seattle weather…'.
    Rejects: team→home-city maps and fixed city whitelists.
    """
    raw = text or ""
    # Explicit preposition + place (allow lower or Title case from speech)
    m = re.search(
        r"(?i)\b(?:in|for|near|around|at)\s+([A-Za-z][A-Za-z.'-]{1,}(?:\s+[A-Za-z][A-Za-z.'-]{1,}){0,2})\b",
        raw,
    )
    if m:
        place = _trim_place_candidate(m.group(1).strip())
        if _looks_like_place(place):
            return place.title() if place.islower() else place
    # Leading place before weather/forecast: "Osaka weather tomorrow"
    m2 = re.search(
        r"(?i)^\s*([A-Za-z][A-Za-z.'-]{1,}(?:\s+[A-Za-z][A-Za-z.'-]{1,}){0,2})\s+"
        r"(?:weather|forecast|temperature|temps?)\b",
        raw.strip(),
    )
    if m2:
        place = _trim_place_candidate(m2.group(1).strip())
        if _looks_like_place(place):
            return place.title() if place.islower() else place
    return ""


def _is_weather_clause(text: str) -> bool:
    low = (text or "").lower()
    if any(t in low for t in _WEATHER_TERMS):
        return True
    # Spoken shorthand: "what's the temp tomorrow"
    if re.search(r"\btemp(?:s|erature|eratures)?\b", low):
        return True
    return False


def _is_local_or_software_game_context(text: str) -> bool:
    """Video-game / code-project language — must NOT be classified as sports."""
    low = apply_spelling_fixes(text or "").lower()
    if not low:
        return False
    if re.search(
        r"\b(desktop|folder|directory|codebase|repo|workspace|file_list|file_read|"
        r"html|css|javascript|typescript|python|godot|unity|unreal|pygame|"
        r"2d|3d|shooter|platformer|roguelike|rpg|sandbox|indie)\b",
        low,
    ):
        return True
    # "build/code/make a game" / "start the X game" with software framing
    if re.search(r"\b(build|code|make|create|scaffold|implement|develop)\b.{0,40}\bgame\b", low):
        return True
    if re.search(r"\bgame\b.{0,40}\b(project|folder|desktop|files?|code|scan|together)\b", low):
        return True
    if re.search(r"\b(project|folder|desktop|files?|code|scan)\b.{0,40}\bgame\b", low):
        return True
    return False


def _is_schedule_or_sports_clause(text: str) -> bool:
    """True for schedules, fixtures, leagues — structural, not team-name lists."""
    low = (text or "").lower()
    # Software / local project "game" is never sports
    if _is_local_or_software_game_context(low):
        return False
    if any(t in low for t in _SCHEDULE_TERMS):
        return True
    if re.search(r"\b(next|upcoming)\s+(game|match|matches|fixture|fixtures)\b", low):
        return True
    # Plural matches/games alone + competition or day
    if re.search(r"\b(matches|games|fixtures)\b", low) and re.search(
        r"\b(today|tonight|tomorrow|this week|weekend|schedule|happening|playing|fifa|world cup|"
        r"nhl|nba|nfl|mlb|soccer|football|premier|uefa|mls|hockey|basketball)\b",
        low,
    ):
        return True
    # game/match/schedule + league OR vs-structure OR residual team-ish noun
    # Bare "game" + residual words alone is too weak (catches "2d shooter game")
    if re.search(r"\b(match|schedule|fixture)\b", low) and (
        re.search(
            r"\b(nhl|nba|nfl|mlb|fifa|world cup|soccer|football|hockey|basketball|uefa|mls)\b",
            low,
        )
        or re.search(r"\b(?:vs\.?|versus|against)\b", low)
        or _extract_teamish_phrase(low)
    ):
        return True
    if re.search(r"\bgame\b", low) and (
        re.search(
            r"\b(nhl|nba|nfl|mlb|fifa|world cup|soccer|football|hockey|basketball|uefa|mls|"
            r"next game|upcoming game|score|kickoff)\b",
            low,
        )
        or re.search(r"\b(?:vs\.?|versus|against)\b", low)
    ):
        return True
    # League + day/playing without the word "match"
    if re.search(r"\b(fifa|world cup|uefa|premier league|champions league)\b", low) and re.search(
        r"\b(today|tonight|tomorrow|schedule|playing|fixtures?|matches?|games?)\b",
        low,
    ):
        return True
    return False


def _clean_match_side(s: str) -> str:
    """Normalize one free-form match side (any nation/club — no whitelist)."""
    s = _normalize_text(s)
    for _ in range(4):
        nxt = re.sub(
            r"(?i)^(the|a|an|who|what|which|when|wins?|win|plays?|playing|with|between)\s+",
            "",
            s,
        )
        if nxt == s:
            break
        s = nxt
    s = re.sub(
        r"(?i)\b(fifa|world\s*cup|nhl|nba|nfl|mlb|uefa|premier\s*league|champions\s*league|"
        r"soccer|football|hockey|basketball|baseball)\b",
        " ",
        s,
    )
    s = re.sub(
        r"(?i)\s+\b(kickoff|time|schedule|fixtures?|matches?|games?|today|tomorrow|tonight|"
        r"start|starts|starting|et|pt|mt|ct|utc|gmt|mnt|mst|est|pst)\b.*$",
        "",
        s,
    )
    s = _normalize_text(s)
    toks = s.split()
    if len(toks) > 3:
        s = " ".join(toks[-3:])
    return _normalize_text(s)


def _extract_vs_sides(text: str) -> str:
    """
    Structural matchup parse — free-form sides, no country whitelist.

    Accepts:
      France vs Morocco | A versus B | X against Y
      with France and Morocco | between A and B
      game with France and maracoo  (STT OK — keep free-form spelling)
    """
    raw = text or ""
    patterns = (
        # Classic vs
        r"(?iu)\b([\w][\w .'-]{0,40}?)\s+(?:vs\.?|versus|against)\s+([\w][\w .'-]{0,40}?)\b",
        # with/between X and Y (live: "fifa game with france and maracoo")
        r"(?iu)\b(?:with|between)\s+([\w][\w'-]{1,30})\s+and\s+([\w][\w'-]{1,30})\b",
        # "with france game" / "france game today" (opponent unknown — keep named side)
        r"(?iu)\b(?:with|for)\s+([\w][\w'-]{2,30})\s+(?:game|match|fixture)\b",
        # game/match ... X and Y
        r"(?iu)\b(?:game|match|fixture|matchup)\s+(?:with\s+|between\s+)?"
        r"([\w][\w'-]{1,30})\s+and\s+([\w][\w'-]{1,30})\b",
    )
    for pat in patterns:
        m = re.search(pat, raw)
        if not m:
            continue
        # One named side only (e.g. "with france game") — still useful
        if m.lastindex == 1:
            a = _clean_match_side(m.group(1))
            if a and len(a) >= 2 and a.lower() not in _SPORTS_STOP:
                if a.lower() not in {"time", "what", "when", "start", "does", "the", "game", "match"}:
                    return a
            continue
        a, b = _clean_match_side(m.group(1)), _clean_match_side(m.group(2))
        if not a or not b or len(a) < 2 or len(b) < 2:
            continue
        if a.lower() in _SPORTS_STOP or b.lower() in _SPORTS_STOP:
            continue
        # Reject obvious non-sides ("time and the")
        if a.lower() in {"time", "what", "when", "start", "does", "the"} or b.lower() in {
            "time", "what", "when", "start", "does", "the", "today", "tomorrow",
        }:
            continue
        return f"{a} {b}"
    return ""


def _extract_teamish_phrase(text: str) -> str:
    """
    Residual team/org phrase after stripping schedule stopwords.
    Works for any club/nation — not a nickname map.
    """
    words = re.findall(r"(?u)[\w]+(?:['-][\w]+)?", text or "")
    # Drop pure digits
    words = [w for w in words if not w.isdigit()]
    keep = [w for w in words if w.lower() not in _SPORTS_STOP]
    # Drop pure league tokens from the "team" phrase (kept separately by caller)
    leagueish = {
        "nhl", "nba", "nfl", "mlb", "fifa", "uefa", "mls", "soccer", "football",
        "hockey", "basketball", "world", "cup", "premier", "league", "champions",
    }
    keep = [w for w in keep if w.lower() not in leagueish]
    if not keep:
        return ""
    # Cap length so we don't re-absorb the whole chatty utterance
    phrase = " ".join(keep[:5])
    if len(phrase) < 2:
        return ""
    return phrase


def intent_domains(text: str) -> set[str]:
    """
    Lightweight domain tags for multi-intent detection.

    General mechanism — not a per-combo recipe. Two or more distinct domains
    in one utterance ⇒ multi-intent, regardless of whether we have a recipe.
    """
    low = apply_spelling_fixes(text or "").lower()
    if not low:
        return set()
    domains: set[str] = set()
    if _is_weather_clause(low):
        domains.add("weather")
    if not _is_local_or_software_game_context(low) and (
        _is_schedule_or_sports_clause(low)
        or re.search(
            r"\b(fifa|world cup|nhl|nba|nfl|mlb|soccer|football|premier league|"
            r"stanley cup|championship|match(?:es)?|score(?:s)?|standings|playoff)\b",
            low,
        )
    ):
        domains.add("sports")
    if re.search(
        r"\b(stock|share price|nasdaq|s&p|dow jones|bitcoin|btc|ethereum|eth|crypto|ticker)\b",
        low,
    ):
        domains.add("finance")
    if re.search(
        r"\b(movie|film|trailer|netflix|show|series|album|box office|dlc|sequel|pre-?order)\b",
        low,
    ) or _has_trailer_intent(low) or _has_character_cast_intent(low) or _has_product_title_context(low):
        domains.add("entertainment")
    if re.search(r"\b(news|headline|breaking|headlines)\b", low) and "weather" not in domains:
        domains.add("news")
    if re.search(
        r"\b(capital of|who is the|ceo of|founded|invented|tallest|longest|population of)\b",
        low,
    ):
        domains.add("fact")
    if re.search(r"\b(odds|betting|moneyline|spread|chances|probability|likelihood|win probability)\b", low):
        domains.add("odds")
    if is_deep_research_intent(low):
        domains.add("deep_research")
    return domains


def is_deep_research_intent(text: str) -> bool:
    """True for genuine multi-hop research, not ordinary current-fact lookup."""
    low = _normalize_text(text or "").lower()
    if not low:
        return False
    explicit = bool(
        re.search(
            r"\b(deep (?:research|search)|deep[- ]dive|research report|literature review|"
            r"multi[- ]hop|investigate|trace (?:the )?(?:root cause|timeline|evidence)|"
            r"synthesize (?:sources|evidence)|compare (?:sources|evidence|claims)|"
            r"primary sources|source audit|technical report)\b",
            low,
        )
    )
    if explicit:
        return True
    asks_for_evidence = bool(re.search(r"\b(evidence|sources|citations|timeline|root cause|tradeoffs|conflicts)\b", low))
    multi_step_verbs = len(
        re.findall(r"\b(compare|verify|trace|explain|evaluate|rank|summarize|synthesize|investigate)\b", low)
    )
    return asks_for_evidence and multi_step_verbs >= 2 and len(low.split()) >= 12


def _relative_day_labels(text: str) -> tuple[str, str]:
    """Return (day_word, calendar_label) e.g. ('tomorrow', 'Thursday July 9 2026')."""
    low = (text or "").lower()
    now = datetime.now()
    if re.search(r"\btomorrow\b", low):
        d = now + timedelta(days=1)
        return "tomorrow", d.strftime("%A %B %d %Y")
    if re.search(r"\btonight\b", low):
        return "tonight", now.strftime("%A %B %d %Y")
    if re.search(r"\btoday\b", low):
        return "today", now.strftime("%A %B %d %Y")
    return "", ""


def _explicit_calendar_date_label(text: str) -> str:
    """Pull 'July 9 2026' / 'July 9th' style dates into a compact calendar pin."""
    low = (text or "").lower()
    m = re.search(
        r"\b(january|february|march|april|may|june|july|august|september|october|november|december)"
        r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?\b",
        low,
    )
    if not m:
        return ""
    month, day_n = m.group(1).title(), m.group(2)
    year = m.group(3) or str(datetime.now().year)
    return f"{month} {int(day_n)} {year}"


def _normalize_sports_query(text: str) -> str:
    """
    Compact sports/schedule search string via structure (league, vs-sides, day, TZ).
    Never rewrites to a hard-coded franchise string (no team→canonical map).
    """
    q = _normalize_text(apply_spelling_fixes(text or ""))
    low = q.lower()
    day, cal = _relative_day_labels(low)
    explicit = _explicit_calendar_date_label(low)
    # Timezone conversion follow-ups must NOT collapse to a fresh full-day slate
    # (lost prior match/clock context on "what time MNT?").
    tz_convert = bool(
        re.search(
            r"\b("
            r"timezone|time\s*zone|convert\s+local|my\s+time|local\s+time|"
            r"mountain\s+time|pacific\s+time|eastern\s+time|central\s+time|"
            r"\bmnt\b|\bmst\b|\bmdt\b|\best\b|\bedt\b|\bpst\b|\bpdt\b|\butc\b|\bgmt\b"
            r")\b",
            low,
        )
    )
    side = _extract_vs_sides(q)
    clock = ""
    tm = re.search(
        r"\b(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?|am|pm))\b",
        low,
        flags=re.IGNORECASE,
    )
    if tm:
        clock = tm.group(1).strip()
    league = ""
    if re.search(r"\b(fifa|world cup)\b", low):
        league = "FIFA World Cup"
    elif re.search(r"\bnhl\b|\bhockey\b", low):
        league = "NHL"
    elif re.search(r"\bnba\b|\bbasketball\b", low):
        league = "NBA"
    elif re.search(r"\bnfl\b", low):
        league = "NFL"
    elif re.search(r"\bmlb\b|\bbaseball\b", low):
        league = "MLB"
    elif re.search(r"\buefa|premier league|champions league\b", low):
        league = "soccer"

    def _pin() -> str:
        if day and cal:
            return f"{day} {cal}".strip()
        return day or explicit or ""

    if tz_convert:
        tz_bits = re.findall(
            r"\b(mnt|mst|mdt|est|edt|pst|pdt|cst|cdt|utc|gmt|mt|et|pt|"
            r"mountain|pacific|eastern|central)\b",
            low,
        )
        tz_label = " ".join(dict.fromkeys(tz_bits[:3])) or "Mountain Time"
        pin = _pin() or "today"
        if not side and not league:
            team = _extract_teamish_phrase(q)
            head = team or "match"
            return f"{head} kickoff {pin} convert to {tz_label} timezone ET schedule".strip()
        if not side:
            head = league or "sports"
            return (
                f"{head} {pin} full match list each kickoff time "
                f"ET and {tz_label} convert timezone schedule"
            ).strip()
        parts = [league or "match", side, "kickoff"]
        if clock:
            parts.append(clock)
        parts.append(f"what time is that in {tz_label} convert timezone ET")
        if pin:
            parts.append(pin)
        return " ".join(p for p in parts if p).strip()

    # League slate (World Cup / NHL / …) — demand concrete kickoffs, pin calendar
    if league and re.search(r"\b(fifa|world cup)\b", low):
        pin = _pin()
        # "next match today" → force next/upcoming kickoff language (not 104-game fluff)
        wants_next = bool(re.search(r"\b(next|upcoming|what time|kickoff|start)\b", low))
        if side:
            base = f"{league} {side} kickoff time ET schedule fixtures"
        elif wants_next and (day or "today" in low or "tonight" in low):
            base = (
                f"{league} matches today next kickoff times ET "
                f"fixtures TV schedule ESPN"
            )
        else:
            base = f"{league} matches today kickoff times ET fixtures schedule ESPN"
        if clock:
            base = f"{base} {clock}"
        if pin:
            return f"{base} {pin}".strip()
        return base

    # Next/upcoming game for any free-form team phrase (no franchise rewrite)
    if re.search(r"\b(next|upcoming)\s+(game|match)\b", low) or (
        re.search(r"\b(when|schedule)\b", low) and re.search(r"\b(game|match|play)\b", low)
    ):
        team = _extract_teamish_phrase(q)
        if team:
            bits = [team, "next game schedule"]
            if league:
                bits.append(league)
            pin = _pin()
            if pin:
                bits.append(pin)
            return " ".join(bits).strip()

    # Generic matches/games happening + relative day or explicit calendar date
    if re.search(r"\b(matches|games|fixtures|playing|matchup)\b", low) and (day or explicit):
        cleaned = re.sub(
            r"(?i)\b(what|which|are|is|happening|for|the|a|an|also|just|wondering|sorry|not|"
            r"then|being|played|explain|me|who|when|next)\b",
            " ",
            q,
        )
        cleaned = _normalize_text(cleaned) or q
        pin = _pin()
        if side:
            head = f"{league} {side}".strip() if league else side
            return f"{head} schedule fixtures {pin}".strip()
        if re.search(r"\b(fifa|world cup|nhl|nba|nfl|mlb|soccer|football)\b", cleaned.lower()):
            return f"{cleaned} schedule fixtures {pin}".strip()
        if league:
            return f"{league} schedule fixtures {pin}".strip()
        team = _extract_teamish_phrase(cleaned)
        if team:
            return f"{team} schedule fixtures {pin}".strip()
        return f"sports games matches schedule fixtures {pin}".strip()

    # Bare vs-sides without day
    if side:
        bits = [league, side, "schedule fixtures"]
        pin = _pin()
        if pin:
            bits.append(pin)
        return " ".join(b for b in bits if b).strip()

    return q


def _strip_weather_chat_filler(text: str) -> str:
    """Leaf cleaner for weather strings — must NEVER call normalize_web_search_query*."""
    q = _normalize_text(text)
    if not q:
        return ""
    q = q.replace("\u2019", "'").replace("\u2018", "'")
    q = _SOCIAL_OPEN_RE.sub(" ", q)
    # Drop compliments / small-talk that often co-occur with weather asks
    q = re.sub(
        r"(?i)\b(you look(?:ing)? great|looking good|look good|hope you(?:'re| are) well|"
        r"not much|just chilling|just chiiling|what'?s up|whats up|echo)\b[^.?!]*",
        " ",
        q,
    )
    q = _CHAT_FILLER_RE.sub(" ", q)
    q = re.sub(
        r"(?i)\b(can you|could you|would you|please|pls|check|look up|get me|for me|"
        r"tho|though|right now|real quick|quickly|hope|well|just|much)\b",
        " ",
        q,
    )
    q = re.sub(r"(?i)^(what(?:'s| is)|how(?:'s| is)|and|also|the|a|an)\s+", "", q)
    q = re.sub(r"[?!.]+", " ", q)
    q = _normalize_text(q)
    return q


def _normalize_weather_query(text: str, *, city_hint: str = "") -> str:
    """Build a compact weather search string. Must not call normalize_web_search_query*."""
    city = (city_hint or "").strip() or _infer_city_from_text(text)
    day, cal = _relative_day_labels(text or "")
    if not day:
        day = "today"
        cal = datetime.now().strftime("%A %B %d %Y")
    day_part = f"{day} {cal}".strip() if cal else day
    if city:
        return f"{city} weather {day_part} high low temperature forecast"
    # No city: strip chat filler only (never re-enter normalize_web_search_query_single —
    # that path used to recurse: weather → single → weather → …).
    cleaned = _strip_weather_chat_filler(text)
    cleaned = re.sub(r"(?i)\b(weather|forecast|temperature|temp)\b", " ", cleaned)
    cleaned = re.sub(
        r"(?i)\b(the|a|an|for|me|my|you|your|tomorrow|today|tonight|check|look|up|"
        r"please|can|could|would|get|tell|like|whats|what's|how|is|are)\b",
        " ",
        cleaned,
    )
    cleaned = _normalize_text(cleaned)
    # Only keep cleaned if it looks like a place name (short, no leftover chatter/day words)
    if (
        cleaned
        and 2 <= len(cleaned) <= 40
        and len(cleaned.split()) <= 4
        and not re.search(
            r"(?i)\b(high|low|going|be|what|matches|fifa|check|tho|though|well|hope)\b",
            cleaned,
        )
    ):
        return f"{cleaned} weather {day_part} high low temperature forecast"
    return f"weather {day_part} high low temperature forecast"


def _clean_title_entity(raw: str) -> str:
    """Strip chat filler from a free-form title/product span."""
    s = _normalize_text(raw)
    s = re.sub(
        r"(?i)\b(i need you to|can you|could you|please|search for|search when|look up|find out|explain to me)\b",
        " ",
        s,
    )
    s = re.sub(
        r"(?i)^(the|a|an|new|latest|that|this|for|about|of|when|does|is|will|do|did|search)\s+",
        "",
        s,
    )
    # Chat particles that leak into titles from social openers ("gta 6 hey")
    s = re.sub(r"(?i)\b(hey|hi|hello|yo|sup|please|thanks|thank you)\b", " ", s)
    s = re.sub(
        r"(?i)\b(the|a|an|new|latest|please|trailer|release|price|cost|characters?|cast|search|when)\b",
        " ",
        s,
    )
    s = _normalize_text(s)
    # Trailing roman/word sequels only (any title): "Foo VI" / "Foo six" → "Foo 6"
    s = re.sub(r"(?i)\s+(vi|six)\s*$", " 6", s)
    s = re.sub(r"(?i)\s+(v|five)\s*$", " 5", s)
    if len(s) > 40:
        s = " ".join(s.split()[-4:])
    return s


def _extract_title_entity(text: str) -> str:
    """
    Free-form product/title extraction from the user utterance.

    No whitelist of games/movies — only structural patterns
    (\"X trailer 3\", \"when does X come out\", \"price of X\").
    """
    work = _normalize_text(apply_spelling_fixes(text or ""))
    if not work:
        return ""
    # Light structural cleanup: trailing roman/word sequels only (no franchise aliases)
    work = re.sub(r"(?i)\s+(vi|six)\b", " 6", work)

    patterns = (
        # Short subject: "when X is released" (X <= 5 tokens)
        r"(?i)\bwhen\s+((?:[a-z0-9][\w'.-]*)(?:\s+[a-z0-9][\w'.-]*){0,4})\s+is\s+(?:release|released|launching|dropping)\b",
        r"(?i)\bwhen\s+(?:does|is|will)\s+((?:[a-z0-9][\w'.-]*)(?:\s+[a-z0-9][\w'.-]*){0,4})\s+(?:come\s+out|release|launch|drop)\b",
        r"(?i)\bhow much (?:does|will|is)\s+((?:[a-z0-9][\w'.-]*)(?:\s+[a-z0-9][\w'.-]*){0,4})\s+(?:cost|be|go for)\b",
        r"(?i)\b(?:price of|cost of)\s+((?:[a-z0-9][\w'.-]*)(?:\s+[a-z0-9][\w'.-]*){0,5})\b",
        r"(?i)\btrailer\s*(?:#?\s*)?(?:\d+|three|two|one)\s+(?:for|of)\s+((?:[a-z0-9][\w'.-]*)(?:\s+[a-z0-9][\w'.-]*){0,5})\b",
        r"(?i)\b((?:[a-z0-9][\w'.-]*)(?:\s+[a-z0-9][\w'.-]*){0,5})\s+trailer\s*(?:#?\s*)?(?:\d+|three|two|one)\b",
        r"(?i)\b(?:characters?|cast|protagonists?)\s+(?:of|in|for)\s+((?:[a-z0-9][\w'.-]*)(?:\s+[a-z0-9][\w'.-]*){0,5})\b",
        r"(?i)\b(?:for|about)\s+(?:the\s+)?(?:new\s+)?((?:[a-z0-9][\w'.-]*)(?:\s+[a-z0-9][\w'.-]*){0,5})\s+(?:trailer|release|price|cast)\b",
    )
    for pat in patterns:
        m = re.search(pat, work)
        if m:
            ent = _clean_title_entity(m.group(1))
            bad = {"i need", "you to", "search when", "search", "when", "how much"}
            if ent and len(ent) >= 2 and ent.lower() not in bad and not ent.lower().startswith("i need"):
                return ent
    return ""


def _has_product_title_context(text: str) -> bool:
    """True when utterance carries a title/product entity (any franchise — not GTA-only)."""
    if _extract_title_entity(text):
        return True
    # Explicit media/product markers with some content around them
    low = (text or "").lower()
    return bool(
        re.search(r"\b(trailer|pre-?order|box office|dlc|sequel|season pass)\b", low)
        and len((text or "").split()) >= 3
    )


# Backward-compatible alias (tests/history) — now product-general


def _has_trailer_intent(text: str) -> bool:
    return bool(re.search(r"(?i)\btrailer\s*(?:#?\s*)?(\d+|three|two|one)\b", text or ""))


def _has_character_cast_intent(text: str) -> bool:
    low = (text or "").lower()
    return bool(
        re.search(r"\b(characters?|cast|protagonists?|playable)\b", low)
        or re.search(r"\bnames of the (?:characters?|cast)\b", low)
        or re.search(r"\bwho (?:is|are) (?:in|the) (?:cast|characters?|game|movie|film)\b", low)
        or re.search(r"\bwho (?:is|are) (?:playable|protagonists?)\b", low)
    )


def _has_product_release_intent(text: str) -> bool:
    low = (text or "").lower()
    if not (_has_product_title_context(low) or _extract_title_entity(text)):
        # "when does it release" with product only in subject is handled via rebind
        if not re.search(r"\b(it|this|that|game|movie|show|album)\b", low):
            return False
    return bool(
        re.search(
            r"\b(release(?:s|d)?|come\s+out|coming\s+out|launch(?:es|ing)?|drop(?:s|ping)?|"
            r"when\s+(?:does|is|will)|out\s+on)\b",
            low,
        )
    )


def _has_product_price_intent(text: str) -> bool:
    low = (text or "").lower()
    return bool(
        re.search(
            r"\b(how much|cost(?:s|ing)?|price|pricing|msrp|pre-?order|edition(?:s)?|"
            r"money it costs|dollars?)\b",
            low,
        )
    )


def _normalize_product_trailer_query(text: str, full_context: str = "") -> str:
    blob = f"{text or ''} {full_context or ''}"
    entity = _extract_title_entity(blob) or _extract_title_entity(text) or "trailer"
    m = re.search(r"(?i)\btrailer\s*(?:#?\s*)?(\d+|three|two|one)\b", blob)
    num = ""
    if m:
        raw_n = m.group(1).lower()
        num = {"one": "1", "two": "2", "three": "3"}.get(raw_n, raw_n)
    if num:
        return f"{entity} Trailer {num} release date announcement"
    return f"{entity} trailer release date announcement"


def _normalize_product_cast_query(text: str = "") -> str:
    entity = _extract_title_entity(text) or "title"
    return f"{entity} characters cast protagonists known details"


def _normalize_product_release_query(text: str = "") -> str:
    entity = _extract_title_entity(text) or "title"
    return f"{entity} release date launch platforms official"


def _normalize_product_price_query(text: str = "") -> str:
    entity = _extract_title_entity(text) or "title"
    return f"{entity} price cost pre-order editions"


# Backward-compatible aliases used by older tests/call sites


def _prep_search_work_text(text: str) -> str:
    """Strip social fluff and conversational framing before intent detection / split."""
    raw = _normalize_text(text)
    if not raw:
        return ""
    raw = raw.replace("\u2019", "'").replace("\u2018", "'")
    raw = apply_spelling_fixes(raw)
    work = _SOCIAL_OPEN_RE.sub(" ", raw)
    work = re.sub(
        r"(?i)\b(you look(?:ing)? great|looking good|love (?:the|your) (?:look|design|avatar)|really liking how you look)[^.?!]*[.?!]?",
        " ",
        work,
    )
    # Trailing politeness must never become its own search ("pelsae check" / "please check")
    work = re.sub(
        r"(?i)[?!.]?\s*\b(?:please|pls|plz)\s*(?:check|look(?:\s+up)?|search|confirm|verify)?\s*[?!.]*\s*$",
        " ",
        work,
    )
    work = re.sub(
        r"(?i)\b(?:can you|could you|would you)\s+(?:please\s+)?(?:check|look up|search|confirm)\s*[?!.]*\s*$",
        " ",
        work,
    )
    # ── Conversational framing that adds zero search value ──
    # Agent name / address: "hey echo", "yo echo", "echo can you"
    work = re.sub(r"(?i)\b(?:hey|yo|hi|hello)\s+echo\b", " ", work)
    work = re.sub(r"(?i)\becho\s+(?:can you|could you|would you)\b", " ", work)
    # Filler phrases: "so like", "I was wondering", "just curious", "real quick"
    work = re.sub(
        r"(?i)\b(?:so\s+like|I\s+was\s+wondering|do\s+you\s+know|can\s+you\s+tell\s+me|"
        r"I\s+want\s+to\s+know|I\s+need\s+to\s+find\s+out|help\s+me\s+find|"
        r"just\s+curious|real\s+quick|out\s+of\s+curiosity|any\s+idea|"
        r"I\s+was\s+just\s+thinking|you\s+know\s+like)\b",
        " ",
        work,
    )
    # Strip leading check/search/find verbs at the start of the query
    work = _normalize_text(work)
    work = re.sub(
        r"(?i)^\s*\b(?:check|search\s+for|look\s+up|find|get|show\s+me|tell\s+me\s+(?:about|the)?)\s+(?:the|a|an)?\b",
        " ",
        work,
    )
    return _normalize_text(work)


def _is_hollow_secondary_clause(text: str) -> bool:
    """True for tails like 'recommend the best value pick' that need the prior topic.

    These are the same research ask, not a second independent multi-intent search.
    """
    low = _normalize_text(text).lower()
    if not low or len(low.split()) > 10:
        return False
    # Already has a concrete product/topic noun → keep as its own query
    if re.search(
        r"(?i)\b(microphone|mic|iphone|laptop|bitcoin|weather|stock|"
        r"python|nvidia|tesla|trailer|price|release|score|schedule)\b",
        low,
    ):
        return False
    if _extract_title_entity(low) or _extract_teamish_phrase(low):
        return False
    if re.search(
        r"(?i)^\s*(?:and\s+)?(?:also\s+)?(?:please\s+)?"
        r"(?:recommend|pick|choose|which\s+(?:one|is)|the\s+best\s+value|"
        r"best\s+value\s+pick|value\s+pick|which\s+to\s+buy)\b",
        low,
    ):
        return True
    if re.search(r"(?i)\b(best value pick|recommend the best|which is better)\b", low):
        return True
    return False


def _is_smalltalk_clause(text: str) -> bool:
    """True for pure social/filler clauses that must not become search queries."""
    low = _normalize_text(apply_spelling_fixes(text or "")).lower()
    if not low:
        return True
    if _is_hollow_secondary_clause(low):
        return True
    if re.search(r"(?i)\b(look(?:ing)? great|look(?:ing)? good|you look|liking how you look|love your look)\b", low):
        return True
    # Pure politeness / confirmation tails (never search these alone)
    if re.search(
        r"(?i)^\s*(?:please|pls|plz)?\s*(?:check|look|confirm|verify|thanks|thank you)?\s*[?!.]*\s*$",
        low,
    ) or re.fullmatch(r"(?:please|pelsae|pls|plz)(?:\s+check)?", low):
        return True
    if _is_weather_clause(low) or _is_schedule_or_sports_clause(low):
        return False
    if any(
        t in low
        for t in (
            "score", "odds", "price", "stock", "news", "headline", "trailer",
            "release", "forecast", "temperature", "schedule", "fixture",
            "games", "matches", "fifa", "python", "bitcoin", "cast", "characters",
        )
    ):
        return False
    # Apology / self-correction prefixes: "sorry not tomorrow today" (real ask follows)
    if re.search(
        r"(?i)^\s*(?:sorry|my bad|oops|actually|i meant|never ?mind|nvm)\b",
        low,
    ) and len(low.split()) <= 8:
        if not re.search(
            r"(?i)\b(what|when|where|who|which|how much|how many|find|check|search|"
            r"look up|games?|matches?|weather|price|score)\b",
            low,
        ):
            return True
    if re.search(
        r"(?i)\b(not much|just chilling|chilling|hope you(?:'re| are) well|"
        r"look(?:ing)? good|you look|sounds good|lol|haha|thanks|thank you|"
        r"how(?:'re| are) you|what's up|whats up|cool|nice|awesome|hey|hi|hello)\b",
        low,
    ):
        # Pure vibes / greeting with no fact noun
        if not re.search(r"(?i)\b(what|when|where|who|which|how much|how many|find|check|search|look up)\b", low):
            return True
        # "look good" style — not "look up"
        if re.search(r"(?i)\blook(?:ing)?\s+good\b", low) and "look up" not in low:
            return True
    return False


def looks_like_multi_intent(text: str) -> bool:
    """
    Cheap classifier: is this message more than one distinct ask?

    Must stay false for simple single-fact questions so they never pay
    decomposition latency (no extra LLM call, no extra Tavily fan-out).

    Primary general signal: **two or more intent domains** (weather+sports,
    finance+weather, fact+entertainment, …) — not a list of hand-written combos.
    """
    t = _prep_search_work_text(text)
    if not t:
        return False
    words = t.split()
    # Domain diversity is the real multi-intent signal (works on novel combos).
    domains = intent_domains(t)
    if len(domains) >= 2:
        return True
    if re.search(r"(?i)\b(and then|also|plus|as well as)\b", t):
        product_need = bool(re.search(r"(?i)\b(release(?:d|s)?|come out|cost|price|how much|pre-?order)\b", t))
        sports_need = bool(re.search(r"(?i)\b(fifa|world cup|match(?:up|es)?|who(?:'s| is)? playing|next game|next match)\b", t))
        if product_need and sports_need:
            return True    # Single-fact short asks never multi
    if len(words) < 10:
        return False
    low = t.lower()
    # Single sports/schedule ask is never multi (even with "france and morocco" or trailing please)
    if domains == {"sports"} or (
        "sports" in domains and len(domains) == 1
    ):
        return False
    # Two+ explicit question marks (after stripping politeness tails)
    if t.count("?") >= 2:
        return True
    # Clear multi-join markers with substance on both sides
    if re.search(
        r"(?i)\b(?:and also|also|plus|as well as|and then)\b",
        low,
    ):
        parts = re.split(r"(?i)\b(?:and also|also|plus|as well as|and then)\b", t)
        parts = [
            p.strip(" ,;.")
            for p in parts
            if p and len(p.split()) >= 2 and not _is_smalltalk_clause(p)
        ]
        if len(parts) >= 2:
            # Two fact-bearing sides → multi even if domains only resolved on full text
            return True
    # Two interrogative heads — ignore trailing "check" politeness after please-strip
    inters = re.findall(
        r"(?i)\b(what|when|where|who|which|how|why|find out|look up|tell me)\b",
        low,
    )
    # Bare "check" only counts mid-sentence as its own ask, not "please check"
    if re.search(r"(?i)\bcheck\b", low) and not re.search(
        r"(?i)\b(?:please|pls|plz)?\s*check\s*$", low
    ):
        if re.search(r"(?i)\bcheck\b.+\b(weather|score|price|news|status)\b", low):
            inters.append("check")
    if len(inters) >= 2 and len(words) >= 12:
        return True
    # "A, and B" clause split — do NOT split matchup "X and Y" pairs into fake multi
    # Protect "with France and Morocco" style before splitting on and
    protected = re.sub(
        r"(?iu)\b((?:with|between)\s+[\w][\w'-]{1,30}\s+)and(\s+[\w][\w'-]{1,30})\b",
        r"\1&AND&\2",
        t,
    )
    protected = re.sub(
        r"(?iu)\b((?:game|match|fixture)\s+(?:with\s+|between\s+)?[\w][\w'-]{1,30}\s+)and(\s+[\w][\w'-]{1,30})\b",
        r"\1&AND&\2",
        protected,
    )
    clauses = [
        c.strip(" ,;:").replace("&AND&", "and")
        for c in re.split(r"[?!.]+|\band\b", protected, flags=re.IGNORECASE)
        if c
        and len(c.split()) >= 3
        and not _is_smalltalk_clause(c.replace("&AND&", "and"))
        and not _is_hollow_secondary_clause(c.replace("&AND&", "and"))
    ]
    if len(clauses) >= 2:
        # Distinct domains across clauses
        clause_domains = [intent_domains(c) for c in clauses]
        union: set[str] = set()
        for d in clause_domains:
            union |= d
        if len(union) >= 2:
            return True
        qish = sum(
            1
            for c in clauses
            if re.search(r"(?i)\b(what|when|where|who|which|how|find|check|tell|names?)\b", c)
        )
        if qish >= 2:
            return True
        # One interrogative + another substantive fact clause (e.g. "tallest building in Dubai
        # and who is the CEO of Tesla") — still multi even if only one clause has who/what.
        if qish >= 1 and len(clauses) >= 2 and all(len(c.split()) >= 3 for c in clauses[:2]):
            return True
        # Two noun-heavy clauses with little overlap (independent facts joined by "and")
        if len(words) >= 10 and all(len(c.split()) >= 3 for c in clauses[:2]):
            a = set(re.findall(r"[a-z0-9]{4,}", clauses[0].lower()))
            b = set(re.findall(r"[a-z0-9]{4,}", clauses[1].lower()))
            stop = {"what", "when", "where", "which", "that", "this", "with", "from", "about", "current", "right"}
            a, b = a - stop, b - stop
            if a and b and len(a & b) / max(1, len(a | b)) < 0.35:
                return True
    return False


def recipe_multi_search_queries(text: str) -> list[str]:
    """
    Fast path: hand-written multi-intent recipes only.

    Returns 2+ queries when a recipe matches, else [].
    Single-intent weather/sports alone is *not* multi — handled by single normalize.
    """
    work = _prep_search_work_text(text)
    if not work:
        return []

    city_hint = _infer_city_from_text(work)
    has_weather = _is_weather_clause(work)
    has_sports = _is_schedule_or_sports_clause(work)
    has_product = _has_product_title_context(work)
    has_trailer = _has_trailer_intent(work)
    has_chars = _has_character_cast_intent(work)

    out: list[str] = []
    # Domain-pair recipe: weather+sports (structural domains, not a product whitelist)
    if has_sports and has_weather:
        clauses = [
            c.strip(" ,;:")
            for c in re.split(r"[?!.]+|\band\b|\balso\b", work, flags=re.IGNORECASE)
            if c and c.strip(" ,;:")
        ]
        sports_clause = next((c for c in clauses if _is_schedule_or_sports_clause(c)), work)
        weather_clause = next((c for c in clauses if _is_weather_clause(c)), "weather")
        out.append(_normalize_sports_query(sports_clause))
        w_city = _infer_city_from_text(weather_clause) or city_hint
        out.append(_normalize_weather_query(weather_clause, city_hint=w_city))
    # Product multi: trailer and/or cast for whatever title was mentioned
    elif (has_trailer or has_chars) and (has_product or has_trailer):
        if has_trailer:
            out.append(_normalize_product_trailer_query(work, full_context=work))
        if has_chars or (has_product and re.search(r"(?i)\b(who|names?|know|cast)\b", work)):
            out.append(_normalize_product_cast_query(work))

    return _dedupe_queries(out)[:4]


def _query_token_set(q: str) -> set[str]:
    """Extract content word tokens for Jaccard similarity comparison."""
    stop = {
        "the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "at",
        "each", "full", "list", "with", "please", "make", "sure", "its",
        "is", "are", "was", "were", "what", "when", "where", "how", "who",
        "do", "does", "did", "can", "will", "has", "have",
    }
    toks = set(re.findall(r"[a-z0-9]+", q.lower()))
    return {t for t in toks if t not in stop and len(t) > 1}


def _jaccard_similarity(a: set, b: set) -> float:
    """Jaccard similarity between two sets."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _dedupe_queries(queries: list[str]) -> list[str]:
    """Deduplicate queries using exact match + Jaccard token-set similarity.

    Pass 1: exact normalized string match (original behavior).
    Pass 2: Jaccard similarity >= 0.5 on content-word token sets (catches
    near-duplicates like 'FIFA matches today' vs 'FIFA games today').
    """
    deduped: list[str] = []
    seen: set[str] = set()
    accepted_token_sets: list[set[str]] = []
    for q in queries:
        key = _normalize_text(q).lower()
        if not key or key in seen:
            continue
        # Jaccard near-duplicate check
        q_tokens = _query_token_set(key)
        is_near_dupe = False
        if q_tokens:
            for accepted_set in accepted_token_sets:
                if _jaccard_similarity(q_tokens, accepted_set) >= 0.5:
                    is_near_dupe = True
                    break
        if is_near_dupe:
            continue
        seen.add(key)
        accepted_token_sets.append(q_tokens)
        deduped.append(_normalize_text(q))
    return deduped


def _is_orphan_price_query(q: str) -> bool:
    """True for bare 'how much will it cost' with no product/entity noun."""
    low = _normalize_text(q).lower()
    if not low:
        return False
    if not re.search(r"(?i)\b(how much|cost(?:s|ing)?|price|pricing|msrp|pre-?order)\b", low):
        return False
    # Already entity-grounded: free-form title OR residual non-price tokens (any product)
    if _extract_title_entity(q):
        return False
    if re.search(r"(?i)\b(bitcoin|btc|ethereum|stock|iphone|ps5|xbox|python|nvidia|tesla)\b", low):
        return False
    price_stop = {
        "how", "much", "will", "it", "cost", "costs", "costing", "price", "pricing",
        "msrp", "pre", "order", "preorder", "editions", "edition", "the", "a", "an",
        "for", "of", "does", "is", "be", "go", "what", "dollars", "money", "and",
    }
    residual = [
        t for t in re.findall(r"[a-z0-9]+", low)
        if t not in price_stop and not t.isdigit()
    ]
    # "gta 6 price…" / "silksong price…" have residual product tokens → not orphan
    if residual:
        return False
    # Short cost-only / "will it cost" clauses
    if len(low.split()) <= 8:
        return True
    return bool(re.search(r"(?i)\b(how much will it cost|how much does it cost|what does it cost)\b", low))


def _rebind_orphan_queries(work: str, queries: list[str]) -> list[str]:
    """Attach bare cost/price sub-queries to title/entity from full message."""
    work_n = _normalize_text(work)
    if not work_n or not queries:
        return queries
    out: list[str] = []
    for q in queries:
        if _is_orphan_price_query(q) and _has_product_title_context(work_n):
            out.append(_normalize_product_price_query(work_n))
        elif _is_orphan_price_query(q) and re.search(r"(?i)\b(bitcoin|btc)\b", work_n):
            out.append("current bitcoin price USD")
        else:
            out.append(q)
    return _dedupe_queries(out)


def _normalize_clause_for_search(part: str, *, full_context: str = "") -> str:
    """Normalize one fact clause with domain-aware compactors."""
    p = _normalize_text(apply_spelling_fixes(part or ""))
    if not p or _is_smalltalk_clause(p):
        return ""
    ctx = _normalize_text(full_context or p)
    if _is_weather_clause(p) and not _is_schedule_or_sports_clause(p):
        return _normalize_weather_query(p, city_hint=_infer_city_from_text(p) or _infer_city_from_text(ctx))
    if _is_schedule_or_sports_clause(p) and not _is_weather_clause(p):
        return _normalize_sports_query(p)
    if _has_trailer_intent(p) and (
        _has_product_title_context(p) or _has_product_title_context(ctx) or _has_trailer_intent(p)
    ):
        if _has_character_cast_intent(p):
            pass
        return _normalize_product_trailer_query(p, full_context=ctx)
    if (_has_product_title_context(p) or _has_product_title_context(ctx)) and _has_character_cast_intent(p):
        return _normalize_product_cast_query(p if _extract_title_entity(p) else ctx)
    if (_has_product_title_context(p) or _has_product_title_context(ctx)) and (
        _has_product_release_intent(p)
        or _has_product_release_intent(f"{p} {ctx}" if _has_product_title_context(ctx) else p)
    ):
        if _has_product_title_context(p) and _has_product_release_intent(p):
            return _normalize_product_release_query(p)
        if _has_product_title_context(p) and re.search(r"(?i)\b(how much|cost|price|money)\b", p):
            return _normalize_product_price_query(p)
        if _has_product_release_intent(p) and _has_product_title_context(ctx):
            return _normalize_product_release_query(ctx)
    # Bare "how much will it cost" after a product clause in the same message
    if _is_orphan_price_query(p) and _has_product_title_context(ctx):
        return _normalize_product_price_query(ctx)
    if _has_product_title_context(p) and re.search(r"(?i)\b(how much|cost|price|money)\b", p):
        return _normalize_product_price_query(p)
    n = normalize_web_search_query_single(p) or p
    return _normalize_text(n)


def _clip_span_to_clause(span: str) -> str:
    """Stop a domain span at multi-intent joiners so domains don't bleed into each other."""
    s = str(span or "")
    s = re.split(r"(?i)\b(?:and also|also|plus|as well as|and then)\b", s)[0]
    return _normalize_text(s)


def _force_domain_decompose(text: str) -> list[str]:
    """
    When the full message has 2+ domains but clause split only yielded one query,
    carve domain-specific compact queries from the whole text.

    This is still *general* (domain tags), not a weather+FIFA special case.
    """
    work = _prep_search_work_text(text)
    domains = intent_domains(work)
    if len(domains) < 2:
        return []
    out: list[str] = []
    city_hint = _infer_city_from_text(work)

    if "weather" in domains:
        m = re.search(
            r"(?i)(?:what(?:'s| is)?\s+)?(?:the\s+)?(?:temp(?:erature)?s?|weather|forecast|high|low)"
            r".{0,80}?(?:tomorrow|today|tonight|this week)?(?:\s+in\s+[A-Za-z .'-]+)?",
            work,
        )
        span = _clip_span_to_clause(m.group(0) if m else "")
        if not span or not _is_weather_clause(span):
            # Recover from "… weather in Denver" style after a joiner
            m2 = re.search(
                r"(?i)\b(?:temp(?:erature)?s?|weather|forecast)\b.{0,60}",
                work,
            )
            span = _clip_span_to_clause(m2.group(0) if m2 else "weather")
        # Strip non-weather domains from span
        span = re.sub(
            r"(?i)\b(bitcoin|btc|stock|nasdaq|fifa|world cup|score|movie|trailer|news)\b",
            " ",
            span,
        )
        wq = _normalize_weather_query(span, city_hint=city_hint or _infer_city_from_text(work))
        if wq:
            out.append(wq)

    if "sports" in domains:
        # Start near sports keywords — NEVER from message start (product clause used to
        # swallow the whole string, then _clip_span_to_clause dropped the sports half).
        kw = re.search(
            r"(?i)\b(?:fifa|world\s*cup|nhl|nba|nfl|mlb|uefa|premier\s*league|"
            r"matches?|games?|fixtures?|matchup|score|playing|"
            r"vs\.?|versus|against|next\s+game|schedule)\b",
            work,
        )
        if kw:
            # Expand left only to last multi-intent joiner / punctuation
            left = work[: kw.start()]
            cut = 0
            for mjoin in re.finditer(
                r"(?i)(?:[?!.]+\s*|\b(?:and also|as well as|and then|also|plus)\b\s*)",
                left,
            ):
                cut = mjoin.end()
            span = work[cut : min(len(work), kw.end() + 90)]
        else:
            span = work
        span = _clip_span_to_clause(span)
        # If clip still landed on a non-sports half, take from keyword only
        if kw and not _is_schedule_or_sports_clause(span) and not re.search(
            r"(?i)\b(fifa|world cup|match|game|score|nhl|nba|nfl|mlb|vs\.?|versus)\b", span
        ):
            span = work[kw.start() : min(len(work), kw.end() + 90)]
        span = re.sub(
            r"(?i)\b(temp(?:erature)?s?|weather|forecast|humidity|bitcoin|stock)\b",
            " ",
            span,
        )
        # Orphan "who is playing" alone → keep parent sports context
        if re.search(r"(?i)^\s*who\s+is\s+playing\s*$", span.strip()) and kw:
            span = work[max(0, kw.start() - 40) : min(len(work), kw.end() + 90)]
        sq = _normalize_sports_query(span)
        # Avoid treating a pure product-title span as sports
        if sq and not _is_weather_clause(sq) and not (
            _has_product_title_context(sq) and not _is_schedule_or_sports_clause(sq)
        ):
            out.append(sq)
        elif re.search(r"(?i)\b(fifa|world cup)\b", work):
            out.append(_normalize_sports_query("fifa world cup matches " + (span or "")))

    if "finance" in domains:
        m = re.search(
            r"(?i)(?:what(?:'s| is)?\s+)?(?:the\s+)?(?:\w+\s+)?(?:stock|share)\s*price|"
            r"(?:bitcoin|btc|ethereum|eth|nasdaq|crypto)\s*(?:price)?|"
            r"price of \w+",
            work,
        )
        span = _clip_span_to_clause(m.group(0) if m else "")
        if not span:
            m2 = re.search(r"(?i)\b(?:bitcoin|btc|ethereum|stock|nasdaq|crypto)\b.{0,40}", work)
            span = _clip_span_to_clause(m2.group(0) if m2 else "")
        span = re.sub(r"(?i)\b(weather|forecast|temp(?:erature)?s?|fifa|match(?:es)?)\b", " ", span)
        span = _normalize_text(span)
        if span:
            fq = normalize_web_search_query_single(span) or span
            # Keep finance-y; don't let weather normalizer swallow it
            if fq and not _is_weather_clause(fq):
                out.append(fq)
            elif span:
                out.append(span)

    if "entertainment" in domains:
        if _has_product_title_context(work) or _has_trailer_intent(work):
            if _has_trailer_intent(work):
                out.append(_normalize_product_trailer_query(work, full_context=work))
            if _has_character_cast_intent(work):
                out.append(_normalize_product_cast_query(work))
            if _has_product_release_intent(work) and not _has_trailer_intent(work):
                out.append(_normalize_product_release_query(work))
            if _has_product_price_intent(work) or (
                _has_product_title_context(work)
                and re.search(r"(?i)\b(how much|cost|price|money it costs|pre-?order)\b", work)
            ):
                out.append(_normalize_product_price_query(work))
            if (
                _has_product_title_context(work)
                and not _has_trailer_intent(work)
                and not _has_character_cast_intent(work)
                and not _has_product_release_intent(work)
                and not re.search(r"(?i)\b(how much|cost|price)\b", work)
            ):
                out.append(_normalize_product_release_query(work))
        else:
            m = re.search(
                r"(?i)(?:when|what).{0,40}\b(?:movie|film|trailer|release|netflix|show|series|album)\b.{0,40}",
                work,
            )
            if m:
                span = _clip_span_to_clause(m.group(0))
                out.append(normalize_web_search_query_single(span) or span)

    if "news" in domains:
        m = re.search(r"(?i)(?:local\s+)?news.{0,40}|headlines.{0,40}", work)
        if m:
            span = _clip_span_to_clause(m.group(0))
            span = re.sub(r"(?i)\b(weather|stock|bitcoin|fifa)\b", " ", span)
            nq = normalize_web_search_query_single(span) or span
            if nq:
                out.append(nq)

    if "fact" in domains:
        m = re.search(
            r"(?i)(?:what(?:'s| is)|who is|tallest|capital of|ceo of).{0,60}",
            work,
        )
        if m:
            span = _clip_span_to_clause(m.group(0))
            span = re.sub(r"(?i)\b(weather|stock|score|match(?:es)?)\b", " ", span)
            fq = normalize_web_search_query_single(span) or span
            if fq and not _is_weather_clause(fq) and not _is_schedule_or_sports_clause(fq):
                out.append(fq)

    if "odds" in domains and "sports" not in domains:
        m = re.search(r"(?i)(?:odds|betting|moneyline).{0,40}", work)
        if m:
            out.append(normalize_web_search_query_single(_clip_span_to_clause(m.group(0))) or m.group(0))

    return _dedupe_queries(out)[:5]


def _heuristic_decompose(text: str) -> list[str]:
    """No-LLM fallback: split on and/also/plus/? into compact sub-queries."""
    work = _prep_search_work_text(text)
    if not work:
        return []
    # Single-domain sports/schedule: one compact query (never explode matchup "and")
    if intent_domains(work) <= {"sports"} and _is_schedule_or_sports_clause(work):
        one = _normalize_clause_for_search(work, full_context=work)
        return [one] if one else []
    # Protect matchup "X and Y" from clause splits
    protected = re.sub(
        r"(?iu)\b((?:with|between)\s+[\w][\w'-]{1,30}\s+)and(\s+[\w][\w'-]{1,30})\b",
        r"\1&AND&\2",
        work,
    )
    protected = re.sub(
        r"(?iu)\b((?:game|match|fixture)\s+(?:with\s+|between\s+)?[\w][\w'-]{1,30}\s+)and(\s+[\w][\w'-]{1,30})\b",
        r"\1&AND&\2",
        protected,
    )
    parts = [
        c.strip(" ,;:").replace("&AND&", "and")
        for c in re.split(
            r"[?!.]+|\band also\b|\bas well as\b|\band then\b|\balso\b|\bplus\b|\band\b",
            protected,
            flags=re.IGNORECASE,
        )
        if c and len(c.replace("&AND&", "and").split()) >= 2 and not _is_smalltalk_clause(c.replace("&AND&", "and"))
    ]
    # Merge orphaned "who is playing" onto prior sports clause
    merged_parts: list[str] = []
    for p in parts:
        if (
            merged_parts
            and re.search(r"(?i)^\s*who\s+(?:is|are)\s+playing\b", p)
            and _is_schedule_or_sports_clause(merged_parts[-1])
        ):
            merged_parts[-1] = f"{merged_parts[-1]} {p}".strip()
            continue
        merged_parts.append(p)
    parts = merged_parts
    out: list[str] = []
    for p in parts:
        # Prefer specialized single normalize per clause (full work rebinds bare "how much")
        n = _normalize_clause_for_search(p, full_context=work)
        if n and len(n) >= 4 and not _is_smalltalk_clause(n):
            out.append(n)
    out = _rebind_orphan_queries(work, _dedupe_queries(out))
    # Prefer force domain when multi domains and heuristic stayed chatty / incomplete
    forced = _force_domain_decompose(work) if len(intent_domains(work)) >= 2 else []
    if len(out) < 2 and forced:
        if len(forced) >= 2:
            return forced
        out = _dedupe_queries(out + forced)
    elif forced and len(forced) >= 2:
        # Heuristic chatty residue: "i need you to search…", "explain to me…"
        chatty = sum(
            1
            for q in out
            if re.search(
                r"(?i)\b(i need|can you|please|explain to me|search when|how much money it)\b",
                q,
            )
            or len(q.split()) > 12
        )
        # "how much will it cost" alone still matches cost — require entity-grounded price
        has_grounded_price = any(
            re.search(r"(?i)\b(price|cost|pre-?order|msrp)\b", q)
            and (
                _extract_title_entity(q)
                or re.search(r"(?i)\b(bitcoin|btc|ethereum|msrp|edition)\b", q)
                or len(q.split()) >= 4
            )
            for q in out
        )
        missing_price = bool(re.search(r"(?i)\b(how much|cost|price)\b", work)) and not has_grounded_price
        orphan_price = any(_is_orphan_price_query(q) for q in out)
        # Sports domain present in work but missing from outputs
        missing_sports = "sports" in intent_domains(work) and not any(
            _is_schedule_or_sports_clause(q) or re.search(r"(?i)\b(fifa|world cup|nhl|nba|nfl|mlb|schedule|fixture)\b", q)
            for q in out
        )
        if chatty or missing_price or orphan_price or missing_sports or len(forced) > len(out):
            return forced[:5]
    return out[:5]


def decompose_search_intents(text: str, llm_invoke=None) -> list[str]:
    """
    General multi-intent decomposition (fallback when no recipe matches).

    llm_invoke: optional callable(str) -> str for a focused decompose prompt.
    If missing or parse fails, uses heuristic clause split.
    """
    work = _prep_search_work_text(text)
    if not work:
        return []

    if callable(llm_invoke):
        prompt = (
            "You convert chat into web SEARCH QUERIES (not answers).\n"
            "Rules:\n"
            "1) ONE independent fact ask → ONE query. Do NOT split politeness "
            "('please check'), STT noise, or matchup names joined by 'and' "
            "(France and Morocco = one match).\n"
            "2) Multiple DIFFERENT fact domains (e.g. weather + sports, release + price) "
            "→ separate queries (2-5 max).\n"
            "3) Each query must be a compact search string with the specific anchors: "
            "who/what/where (names, places, products), when (today/tomorrow/dates), "
            "and the fact type (kickoff time, high/low, price, release date).\n"
            "4) Never emit fragments like 'please check', 'start today', or bare 'and X'.\n"
            "5) Return ONLY a JSON array of strings.\n"
            'Example one-ask: ["FIFA World Cup France Morocco kickoff time today"]\n'
            'Example multi: ["Osaka weather tomorrow high low", '
            '"FIFA World Cup match list kickoff tomorrow"]\n\n'
            f"User: {work[:500]}\n"
        )
        try:
            raw = str(llm_invoke(prompt) or "").strip()
            # Prefer JSON array
            m = re.search(r"\[[\s\S]*\]", raw)
            if m:
                import json as _json

                data = _json.loads(m.group(0))
                if isinstance(data, list):
                    items = [_normalize_text(str(x)) for x in data if str(x or "").strip()]
                    items = [x for x in items if len(x) >= 3]
                    if items:
                        return _dedupe_queries(items)[:5]
            # Numbered / bulleted lines
            lines = []
            for line in raw.splitlines():
                line = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip().strip("\"'")
                if len(line.split()) >= 2:
                    lines.append(line)
            if lines:
                return _dedupe_queries(lines)[:5]
        except Exception:
            pass

    return _heuristic_decompose(work)


def _search_content_anchors(text: str) -> set[str]:
    """
    Tokens that make a search *specific*: places, names, numbers, domain keywords.

    Fragment queries like \"maracoo start today\" or \"pelsae check\" fail this bar
    when the parent utterance had richer anchors they dropped.
    """
    low = (text or "").lower()
    anchors: set[str] = set()
    # Numbers / clock / dates
    for m in re.finditer(r"\b(\d{1,4}(?::\d{2})?|\d{4})\b", low):
        anchors.add(m.group(1))
    # Domain keywords (category, not entity hardcode)
    for tok in (
        "weather", "forecast", "temperature", "fifa", "world", "cup", "nhl", "nba",
        "nfl", "mlb", "kickoff", "schedule", "fixture", "score", "odds", "price",
        "cost", "release", "trailer", "bitcoin", "stock", "news", "tomorrow", "today",
        "tonight",
    ):
        if re.search(rf"\b{re.escape(tok)}\b", low):
            anchors.add(tok)
    # Content words: length >= 4, not stopwords
    stop = {
        "what", "when", "where", "which", "with", "from", "that", "this", "they",
        "them", "have", "does", "will", "would", "could", "should", "please", "check",
        "start", "starts", "starting", "about", "into", "just", "also", "then",
        "there", "here", "your", "you", "for", "the", "and", "are", "was", "were",
        "how", "who", "why", "can", "need", "want", "tell", "find", "look", "search",
        "game", "games", "match", "matches", "time", "times",
    }
    for w in re.findall(r"(?u)[\w']+", low):
        if len(w) >= 4 and w not in stop and not w.isdigit():
            anchors.add(w)
    return anchors


def is_viable_search_query(q: str, *, parent: str = "") -> bool:
    """
    True only for search strings that look like *reframed factual asks*.

    Rejects: politeness fragments, mid-sentence debris, empty chat crumbs.
    Keeps: entity+intent compact queries (place, teams, titles, numbers).
    """
    s = _normalize_text(q)
    if not s or len(s) < 4:
        return False
    if _is_smalltalk_clause(s) or _is_hollow_secondary_clause(s):
        return False
    low = s.lower()
    # Pure politeness / meta
    if re.fullmatch(
        r"(?:please|pls|plz|pelsae|check|look|confirm|verify|thanks|thank you|"
        r"can you|could you)(?:\s+\w+){0,2}",
        low,
    ):
        return False
    words = low.split()
    if len(words) < 2:
        return False
    anchors = _search_content_anchors(s)
    if not anchors:
        return False

    has_domain = bool(
        re.search(
            r"(?i)\b(weather|forecast|fifa|world cup|nhl|nba|nfl|mlb|kickoff|schedule|"
            r"fixture|score|odds|price|cost|release|trailer|bitcoin|stock|news|"
            r"temperature|high|low|capital|population|ceo|founded|invented|"
            r"tallest|longest|who|what|when|where)\b",
            low,
        )
    )
    if parent:
        parent_anchors = _search_content_anchors(parent)
        parent_has_domain = bool(
            re.search(
                r"(?i)\b(weather|forecast|fifa|world cup|nhl|nba|nfl|mlb|kickoff|"
                r"price|cost|release|trailer|bitcoin|stock|news|capital|score)\b",
                parent,
            )
        )
        distinctive = parent_anchors - {
            "today", "tomorrow", "tonight", "start", "time", "game", "check",
            "please", "pelsae", "starts", "starting",
        }
        kept = anchors & distinctive
        # Short debris with no domain keyword while parent was a domain ask
        if parent_has_domain and not has_domain and len(words) <= 5:
            return False
        # Kept almost none of parent's distinctive anchors and is short
        if distinctive and len(distinctive) >= 2 and len(kept) <= 1 and len(words) <= 4 and not has_domain:
            return False
    # Short queries without a domain keyword are almost always fragments
    if not has_domain and len(words) <= 4:
        return False
    return True


def quality_gate_search_queries(queries: list[str], parent: str) -> list[str]:
    """
    Final filter: only ship entity-rich, intent-clear queries to the web.

    If multi-split produced junk fragments, drop them. If nothing survives,
    fall back to one compact query from the full parent utterance.
    """
    parent_n = _prep_search_work_text(parent) or _normalize_text(parent)
    cleaned: list[str] = []
    for q in queries or []:
        n = _normalize_text(q)
        if not n:
            continue
        # Prefer already-viable candidates AS-IS. Re-normalizing a compact sports
        # string (\"FIFA … france maracoo kickoff\") used to wipe free-form sides.
        if is_viable_search_query(n, parent=parent_n):
            cleaned.append(n)
            continue
        compact = normalize_web_search_query_single(n) or n
        if compact != n and is_viable_search_query(compact, parent=parent_n):
            cleaned.append(compact)
    cleaned = _dedupe_queries(cleaned)
    if cleaned:
        return cleaned[:5]
    # Fallback: one well-formed query from the whole user turn
    one = normalize_web_search_query_single(parent_n) or parent_n
    return [one] if one else []


def resolve_web_search_queries(
    user_text: str,
    model_query: str = "",
    *,
    llm_invoke=None,
    use_decomposition: bool = True,
) -> list[str]:
    """
    Full query resolution for grounded search.

    Architecture (intent → reframed search strings, not utterance fragments):
      1) Prep: strip social/politeness fluff (please check, greetings)
      2) Detect domains / multi-intent (2+ distinct fact domains only)
      3) Recipe multi-split OR domain carve OR single compact normalize
      4) Quality gate: drop fragment/filler queries; require content anchors
         (places, names, numbers, domain keywords)

    Critical: model tool args are often single-intent or chatty. User text is
    authoritative for multi detection — never ship raw chat crumbs to Tavily.
    """
    user = _normalize_text(user_text)
    model_q = _normalize_text(model_query)
    user_prep = _prep_search_work_text(user) or user

    # Prefer user text for multi detection — model tool args are often single-intent.
    multi_src = user_prep or model_q
    if user_prep and (len(intent_domains(user_prep)) >= 2 or looks_like_multi_intent(user_prep)):
        multi_src = user_prep
    elif model_q and len(intent_domains(model_q)) >= 2:
        multi_src = model_q

    # 1) Fast recipes
    recipes = recipe_multi_search_queries(multi_src)
    if len(recipes) >= 2:
        multi = list(recipes)
    else:
        multi = []
        # 2) General decomposition fallback (only when multi-intent looks real)
        multi_suspected = use_decomposition and (
            looks_like_multi_intent(multi_src)
            or len(intent_domains(multi_src)) >= 2
        )
        if multi_suspected:
            decomp = decompose_search_intents(multi_src, llm_invoke=llm_invoke)
            if len(decomp) < 2:
                # Hard fail-safe: domain carve even if LLM returned one blob
                decomp = _force_domain_decompose(multi_src) or decomp
            if len(decomp) >= 2:
                multi = decomp
        # 3) Single-intent compact — full utterance, not a clause fragment
        if not multi:
            one = (
                normalize_web_search_query_single(user_prep)
                or normalize_web_search_query_single(user)
                or normalize_web_search_query_single(model_q)
            )
            if not one:
                one = model_q or user_prep or user
            multi = [one] if one else []

    # Optionally append distinct model tool arg if useful and not already covered.
    # Never add a same-domain duplicate (model often rephrases weather already in multi).
    if model_q and len(multi) >= 2:
        keys = {m.lower() for m in multi}
        model_c = normalize_web_search_query_single(model_q) or model_q
        model_dom = intent_domains(model_c)
        covered_dom: set[str] = set()
        for m in multi:
            covered_dom |= intent_domains(m)
        same_domain = bool(model_dom and model_dom.issubset(covered_dom))
        if (
            model_c
            and len(model_c) >= 8
            and len(model_c.split()) >= 2
            and model_c.lower() not in keys
            and len(model_c.split()) <= 14
            and not same_domain
            and is_viable_search_query(model_c, parent=multi_src)
            and not re.match(r"(?i)^(i |can you|please|find out|what |when )", model_c)
        ):
            multi.append(model_c)

    # Final guard: if user had 2+ domains but multi is still 1, force domain split
    if use_decomposition and len(multi) < 2 and len(intent_domains(multi_src)) >= 2:
        forced = _force_domain_decompose(multi_src)
        if len(forced) >= 2:
            multi = forced

    # Rebind bare "how much will it cost" → product price when parent turn has a title
    multi = _rebind_orphan_queries(multi_src, multi)
    # If product+price still missing grounded price query, inject it
    if _has_product_title_context(multi_src) and re.search(r"(?i)\b(how much|cost|price)\b", multi_src):
        if not any(re.search(r"(?i)\b(price|cost|pre-?order)\b", q) for q in multi):
            multi = _dedupe_queries(list(multi) + [_normalize_product_price_query(multi_src)])

    # 4) Quality gate — never ship utterance fragments as searches
    return quality_gate_search_queries(multi, multi_src)[:5]


def split_web_search_queries(text: str) -> list[str]:
    """
    Split multi-intent chat into separate compact search queries.

    Fast path: recipes. Fallback: general decomposition when multi-intent is detected.
    For single-intent weather/sports alone, returns one normalized query (no LLM).
    """
    work = _prep_search_work_text(text)
    if not work:
        return []

    # Recipes that produce 2+ queries
    recipes = recipe_multi_search_queries(work)
    if len(recipes) >= 2:
        return recipes

    # General multi-intent (no LLM here — callers that have an LLM should use
    # resolve_web_search_queries(..., llm_invoke=...). Heuristic only for pure split.)
    if looks_like_multi_intent(work):
        decomp = _heuristic_decompose(work)
        if len(decomp) >= 2:
            return decomp

    # Single-intent specialties (weather alone, sports alone, etc.)
    city_hint = _infer_city_from_text(work)
    if _is_weather_clause(work) and not _is_schedule_or_sports_clause(work):
        return [_normalize_weather_query(work, city_hint=city_hint)]
    if _is_schedule_or_sports_clause(work) and not _is_weather_clause(work):
        return [_normalize_sports_query(work)]
    if (_has_trailer_intent(work) or _has_character_cast_intent(work)) and (
        _has_product_title_context(work) or _has_trailer_intent(work)
    ):
        if _has_trailer_intent(work) and not _has_character_cast_intent(work):
            return [_normalize_product_trailer_query(work, full_context=work)]
        if _has_character_cast_intent(work) and not _has_trailer_intent(work):
            return [_normalize_product_cast_query(work)]

    single = normalize_web_search_query_single(work)
    return [single] if single else []


def normalize_web_search_query_single(query: str) -> str:
    """Compact a single-intent string (no multi-intent fan-out)."""
    # Prep first: spelling + strip "pelsae check" so we never search politeness
    q = _prep_search_work_text(query) or _normalize_text(query)
    if not q:
        return ""
    q = q.replace("\u2019", "'").replace("\u2018", "'")

    # Drop "do a deeper search about …" wrappers so we never Tavily the meta-phrase.
    q = re.sub(
        r"(?i)\b(?:please\s+)?(?:can you\s+)?(?:do\s+a\s+)?(?:deep(?:er)?|more)\s+(?:web\s+)?search(?:\s+(?:about|on|for|into))?\b",
        " ",
        q,
    )
    q = re.sub(
        r"(?i)\b(?:dig|go)\s+deeper(?:\s+(?:on|into|about|for))?\b",
        " ",
        q,
    )
    q = re.sub(
        r"(?i)\b(?:search|research)\s+deeper(?:\s+(?:about|on|into|for))?\b",
        " ",
        q,
    )
    q = re.sub(r"(?i)\b(?:look\s+into\s+it\s+more|check\s+more|expand\s+on\s+that)\b", " ", q)
    q = _normalize_text(q)

    # Sports/schedule first on FULL string — before "and" clause splits destroy matchups
    # (live: "france and maracoo" must not become "maracoo start today" alone).
    if _is_schedule_or_sports_clause(q) or re.search(r"(?i)\b(fifa|world cup)\b", q):
        sports = _normalize_sports_query(q)
        if sports and (
            _extract_vs_sides(q)
            or re.search(r"(?i)\b(fifa|world cup|nhl|nba|nfl|mlb|schedule|kickoff)\b", sports)
        ):
            return sports

    # Drop leading social openers / greeting clauses.
    q = _SOCIAL_OPEN_RE.sub(" ", q)
    # Split multi-intent: prefer the clause that looks like the factual ask.
    # Protect matchup "X and Y" from being torn apart.
    protected = re.sub(
        r"(?iu)\b((?:with|between)\s+[\w][\w'-]{1,30}\s+)and(\s+[\w][\w'-]{1,30})\b",
        r"\1&AND&\2",
        q,
    )
    protected = re.sub(
        r"(?iu)\b((?:game|match|fixture)\s+(?:with\s+|between\s+)?[\w][\w'-]{1,30}\s+)and(\s+[\w][\w'-]{1,30})\b",
        r"\1&AND&\2",
        protected,
    )
    clauses = [
        c.strip(" ,;:").replace("&AND&", "and")
        for c in re.split(r"[?!.]+|\band\b|\balso\b", protected, flags=re.IGNORECASE)
        if c and c.strip(" ,;:")
    ]

    def _clause_score(c: str) -> int:
        low = c.lower()
        score = 0
        if _RELEASE_DATE_RE.search(c):
            score += 5
        if any(t in low for t in ("weather", "forecast", "score", "trailer", "news", "price", "stock", "release", "fifa", "kickoff")):
            score += 3
        if re.search(r"\b(when|what|who|where|which|how much|how many)\b", low):
            score += 2
        if re.search(r"\b(hey|hi|hello|feeling|doing|please|check)\b", low) and len(c.split()) <= 3:
            score -= 8
        score += min(len(c.split()), 8)  # slight preference for substance
        return score

    if clauses:
        # Always rebuild from clauses so leftover "and …" glue is dropped.
        q = max(clauses, key=_clause_score)

    q = _CHAT_FILLER_RE.sub(" ", q)
    q = re.sub(r"(?i)\b(i wonder(?:ing)?|just wondering)\b", " ", q)
    # Drop glue left after social/clause splits
    q = re.sub(r"(?i)^(and|also|plus|so|but|well)\s+", "", q)
    q = re.sub(r"(?i)^(what(?:'s| is)|how(?:'s| is)|when(?:'s| is))\s+", "", q)
    q = _normalize_text(q)

    # "release notes" is documentation — never rewrite into "release date"
    if re.search(r"(?i)\brelease\s+notes\b", q):
        q_notes = re.sub(
            r"(?i)^(search(?:\s+for)?|look\s+up|find(?:\s+out)?|research|check|get)\s+",
            "",
            q,
        )
        q_notes = re.sub(r"(?i)\b(latest|new|official|current)\b", " ", q_notes)
        q_notes = _normalize_text(q_notes)
        # Prefer crisp doc query
        # Keep whatever product/language the user named (not Python-only)
        if q_notes:
            if "release notes" in q_notes.lower() or "changelog" in q_notes.lower():
                return q_notes
            return f"{q_notes} release notes changelog official"
        return "release notes changelog official"

    # Product/title rewrite: any franchise with trailer / cast / release / price structure
    if _has_trailer_intent(q) and (_has_product_title_context(q) or _has_trailer_intent(q)):
        return _normalize_product_trailer_query(q, full_context=q)
    if _has_product_title_context(q) and _has_character_cast_intent(q):
        return _normalize_product_cast_query(q)
    if _has_product_title_context(q) or _extract_title_entity(q):
        wants_price = _has_product_price_intent(q)
        wants_release = _has_product_release_intent(q) or bool(
            re.search(r"(?i)\b(when|release|launch|come out)\b", q)
        )
        if wants_price and not wants_release:
            return _normalize_product_price_query(q)
        if wants_release:
            return _normalize_product_release_query(q)
        if wants_price:
            return _normalize_product_price_query(q)

    # Sports / next-game compact form
    if _is_schedule_or_sports_clause(q):
        sports = _normalize_sports_query(q)
        if sports:
            return sports

    # Weather compact form (with inferred city when possible)
    if _is_weather_clause(q):
        return _normalize_weather_query(q, city_hint=_infer_city_from_text(q))

    # Generic: "when does X come out / release" → "X release date"
    # Never treat "release notes" as a product launch date.
    if not re.search(r"(?i)\brelease\s+notes\b", q):
        m = re.search(
            r"(?i)(?:when\s+(?:does|is|will|do|did)\s+)?(.+?)\s+"
            r"(?:come\s+out|coming\s+out|release(?:s|d)?|drop(?:s|ping)?|launch(?:es|ing)?)\b",
            q,
        )
        if m:
            subject = _normalize_text(m.group(1))
            subject = re.sub(
                r"(?i)^(when|that|the|a|an|new|latest|next|search for|look up)\s+",
                "",
                subject,
            )
            subject = re.sub(r"(?i)\b(that|the|a|an|new|latest|search|for)\b", " ", subject)
            subject = _normalize_text(subject)
            # Reject chatty leftovers ("i need you to search when gta 6 is")
            if (
                2 <= len(subject) <= 80
                and not re.search(r"(?i)\b(i need|can you|please|explain|search when)\b", subject)
                and len(subject.split()) <= 8
            ):
                return f"{subject} release date"

    # Strip leftover conversational glue / trailing greetings
    q = re.sub(
        r"(?i)\b(when|what|who|where|which|how|does|is|are|will|do|did|the|a|an|that|this|for|of|to|me|you|please)\b",
        " ",
        q,
    ) if len(q.split()) > 12 else q
    # Lighter strip for shorter queries — only leading wrappers
    q = re.sub(
        r"(?i)^(search(?:\s+for)?|look\s+up|find(?:\s+out)?|research(?:\s+deeply)?|check|get)\s+",
        "",
        q,
    )
    q = re.sub(r"(?i)\b(hey|hi|hello|yo|sup)\b[?.!]*$", "", q)
    q = _normalize_text(q.strip(" ?!.,;:"))
    return q or _normalize_text(query)


def normalize_web_search_query(query: str) -> str:
    """Turn a chatty multi-intent user line into a compact web search string.

    For multi-intent (sports + weather), returns the *primary* query only.
    Callers that need every intent must use ``split_web_search_queries``.

    """
    parts = split_web_search_queries(query)
    if parts:
        # Prefer fact-bearing queries over leftover small-talk if split misfired.
        def _part_score(p: str) -> int:
            low = (p or "").lower()
            score = 0
            if any(
                t in low
                for t in (
                    "weather", "forecast", "score", "schedule", "trailer", "odds",
                    "price", "nhl", "nba", "nfl", "mlb", "world cup", "temperature",
                    "release", "cast", "characters",
                )
            ):
                score += 6
            if re.search(r"\b(vs|versus|tomorrow|today|tonight|release)\b", low):
                score += 3
            if _extract_title_entity(p) or _extract_teamish_phrase(p):
                score += 2
            if _is_smalltalk_clause(p):
                score -= 10
            score += min(len(p.split()), 6)
            return score

        return max(parts, key=_part_score)
    return normalize_web_search_query_single(query)


def _parse_date_value(value: str) -> Optional[datetime]:
    s = str(value or "").strip()
    if not s:
        return None
    low = s.lower()
    match = re.match(r"^(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago\b", low)
    if match:
        n = int(match.group(1))
        unit = match.group(2)
        now = datetime.now(timezone.utc)
        if unit == "minute":
            return now - timedelta(minutes=n)
        if unit == "hour":
            return now - timedelta(hours=n)
        if unit == "day":
            return now - timedelta(days=n)
        if unit == "week":
            return now - timedelta(weeks=n)
        if unit == "month":
            return now - timedelta(days=30 * n)
        if unit == "year":
            return now - timedelta(days=365 * n)

    iso = s.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        pass
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y"):
        try:
            parsed = datetime.strptime(s, fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def _classify_recency(published_raw: str) -> tuple[Optional[str], str]:
    dt = _parse_date_value(published_raw)
    if dt is None:
        return None, "unknown"
    now = datetime.now(timezone.utc)
    age = max((now - dt).total_seconds(), 0.0)
    if age <= 72 * 3600:
        bucket = "breaking"
    elif age <= 30 * 24 * 3600:
        bucket = "recent"
    else:
        bucket = "archive"
    return dt.isoformat(), bucket


def _domain(url: str) -> str:
    try:
        host = (urlparse(url).netloc or "").lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _infer_mode(query: str) -> str:
    low = str(query or "").strip().lower()
    if not low:
        return "general"
    if any(term in low for term in _RECENT_TERMS):
        return "recent"
    return "general"


def _parse_numbered_blocks(output: str) -> list[dict[str, Any]]:
    blocks = re.split(r"\n\s*\n", str(output or "").strip())
    items: list[dict[str, Any]] = []
    for block in blocks:
        lines = [line.rstrip() for line in str(block or "").splitlines() if line.strip()]
        if not lines:
            continue
        title_match = re.match(r"^\d+\.\s*(.*)$", lines[0].strip())
        title = (title_match.group(1) if title_match else lines[0]).strip()
        fields: dict[str, str] = {}
        current_label: Optional[str] = None
        for raw_line in lines[1:]:
            line = raw_line.strip()
            field_match = re.match(r"^(URL|Query|Date|Snippet|Page|Extract|Content|Title):\s*(.*)$", line, flags=re.IGNORECASE)
            if field_match:
                current_label = field_match.group(1).lower()
                fields[current_label] = field_match.group(2).strip()
                continue
            if current_label:
                fields[current_label] = (fields.get(current_label, "") + " " + line).strip()
        items.append({
            "title": title,
            "url": fields.get("url", ""),
            "query": fields.get("query", ""),
            "published_raw": fields.get("date", ""),
            "snippet": fields.get("snippet", ""),
            "page_title": fields.get("page", "") or fields.get("title", ""),
            "extract": fields.get("extract", "") or fields.get("content", ""),
        })
    return items


def _normalize_evidence(item: dict[str, Any], *, tool_name: str, fallback_query: str, position: int) -> dict[str, Any]:
    published_at, recency_bucket = _classify_recency(str(item.get("published_raw") or ""))
    query = _normalize_text(item.get("query") or fallback_query)
    url = _normalize_text(item.get("url"))
    title = _normalize_text(item.get("title")) or "Untitled source"
    snippet = _normalize_text(item.get("snippet"))
    extract = _normalize_text(item.get("extract"))
    page_title = _normalize_text(item.get("page_title"))
    summary = snippet or extract or page_title
    if len(summary) > 600:
        summary = summary[:600].rstrip() + "…"
    content = extract or snippet
    if len(content) > 2000:
        content = content[:2000].rstrip() + "…"
    return {
        "id": f"{tool_name}-{position}-{abs(hash((url, title, query))) % 1000000}",
        "kind": "search_result",
        "position": position,
        "query": query,
        "title": title,
        "url": url,
        "domain": _domain(url),
        "summary": summary,
        "snippet": snippet,
        "content": content,
        "page_title": page_title,
        "published_raw": _normalize_text(item.get("published_raw")),
        "published_at": published_at,
        "recency_bucket": recency_bucket,
    }


# Marker so wrappers can detect already-grounded tool output and avoid double-grounding.
GROUNDED_SEARCH_MARKER = "[GROUNDED_SEARCH]"


def is_grounded_search_output(text: str) -> bool:
    return GROUNDED_SEARCH_MARKER in str(text or "")


def build_research_run(*, run_id: str, tool_name: str, tool_input: str, output: str, at: float) -> Optional[dict[str, Any]]:
    if tool_name != "web_search":
        return None
    query = extract_research_query(tool_input)
    raw = str(output or "").strip()
    if not raw or raw.lower().startswith("search failed") or raw.lower().startswith("no search results"):
        evidence: list[dict[str, Any]] = []
    else:
        # Strip grounded headers so the research panel still parses numbered blocks.
        parse_src = raw
        if is_grounded_search_output(raw):
            # Prefer BEST_AVAILABLE_EVIDENCE / body after the instruction block.
            marker_split = re.split(r"BEST_AVAILABLE_EVIDENCE:\s*", raw, maxsplit=1, flags=re.IGNORECASE)
            if len(marker_split) == 2:
                parse_src = marker_split[1]
            else:
                # accepted=true body after blank line following header
                parts = raw.split("\n\n", 1)
                parse_src = parts[1] if len(parts) == 2 else raw
        evidence = [_normalize_evidence(item, tool_name=tool_name, fallback_query=query, position=index) for index, item in enumerate(_parse_numbered_blocks(parse_src), start=1)]

    evidence = [item for item in evidence if item.get("title") or item.get("url") or item.get("summary")]
    mode = _infer_mode(query)
    grounded_accepted: Optional[bool] = None
    if is_grounded_search_output(raw):
        grounded_accepted = "accepted=true" in raw.splitlines()[0].lower() if raw else None
        if grounded_accepted is None:
            grounded_accepted = "SEARCH_EVIDENCE_INSUFFICIENT: true" not in raw
    return {
        "id": run_id,
        "tool": tool_name,
        "query": query,
        "at": at,
        "mode": mode,
        "recency_intent": mode == "recent",
        "evidence_count": len(evidence),
        "evidence": evidence,
        "grounded": is_grounded_search_output(raw),
        "grounded_accepted": grounded_accepted,
    }
