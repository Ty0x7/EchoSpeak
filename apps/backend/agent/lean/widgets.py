"""Structured cards ("widgets") that tools attach to their results for the UI.

A tool still returns plain text for the model. Alongside it, the tool may call
``attach(widget)`` with data straight from its source (a weather API, search
results, a retailer page). The loop sends those widgets to the chat in the
``tool_end`` event and stores them in the message timeline, so the card is
built from tool data and the model never retypes prices, links or numbers.

Every widget is normalised here: unknown types are dropped, strings are capped,
and only http(s) URLs survive. A widget that fails validation is simply not
shown; the model's text answer still is.
"""

from __future__ import annotations

import contextvars
import math
import time
from typing import Any, Callable, Optional, TypeVar
from urllib.parse import urlparse

T = TypeVar("T")

_SINK: contextvars.ContextVar[Optional[list[dict[str, Any]]]] = contextvars.ContextVar("lean_widget_sink", default=None)

MAX_WIDGETS_PER_CALL = 4


def attach(widget: dict[str, Any]) -> None:
    """Called by a tool to show a card for its result. Ignored outside a lean tool call."""
    sink = _SINK.get()
    if sink is None or len(sink) >= MAX_WIDGETS_PER_CALL:
        return
    clean = normalize(widget)
    if clean is not None:
        sink.append(clean)


def collect(func: Callable[[], T]) -> tuple[T, list[dict[str, Any]]]:
    """Run ``func`` with a fresh widget sink; return its result and the widgets it attached."""
    sink: list[dict[str, Any]] = []
    token = _SINK.set(sink)
    try:
        result = func()
    finally:
        _SINK.reset(token)
    return result, sink


# ── validation ────────────────────────────────────────────────────────────

def safe_url(value: Any) -> str:
    """Only absolute http(s) URLs; anything else (javascript:, data:, relative) becomes ''."""
    text = str(value or "").strip()
    if not text or len(text) > 2048:
        return ""
    try:
        parsed = urlparse(text)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return text


def _s(value: Any, limit: int = 200) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _num(value: Any) -> Optional[float]:
    try:
        number = float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _host(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _weather(data: dict[str, Any]) -> Optional[dict[str, Any]]:
    current = data.get("current") or {}
    temp = _num(current.get("temp"))
    if temp is None:
        return None
    hourly = []
    for row in (data.get("hourly") or [])[:24]:
        t = _num(row.get("temp"))
        if t is not None and row.get("time"):
            hourly.append({"time": _s(row.get("time"), 32), "temp": t, "code": int(_num(row.get("code")) or 0),
                           "precip": _num(row.get("precip")) or 0})
    daily = []
    for row in (data.get("daily") or [])[:10]:
        hi, lo = _num(row.get("max")), _num(row.get("min"))
        if hi is not None and lo is not None and row.get("date"):
            daily.append({"date": _s(row.get("date"), 16), "max": hi, "min": lo, "code": int(_num(row.get("code")) or 0),
                          "precip": _num(row.get("precip")) or 0})
    return {
        "location": _s(data.get("location"), 120),
        "units": "F" if str(data.get("units")).upper() == "F" else "C",
        "current": {
            "temp": temp,
            "feels": _num(current.get("feels")),
            "code": int(_num(current.get("code")) or 0),
            "humidity": _num(current.get("humidity")),
            "wind": _num(current.get("wind")),
            "wind_unit": _s(current.get("wind_unit") or "km/h", 8),
        },
        "hourly": hourly,
        "daily": daily,
        "as_of": _s(data.get("as_of"), 40),
        "source": _s(data.get("source") or "Open-Meteo", 60),
    }


def _chart(data: dict[str, Any]) -> Optional[dict[str, Any]]:
    kind = str(data.get("kind") or "line")
    if kind not in {"line", "bar", "area", "pie"}:
        kind = "line"
    labels = [_s(label, 40) for label in (data.get("labels") or [])][:400]
    series = []
    for item in (data.get("series") or [])[:8]:
        values = [_num(v) for v in (item.get("values") or [])][: len(labels) or 400]
        if not values or all(v is None for v in values):
            continue
        series.append({"name": _s(item.get("name"), 60), "values": values})
    if not labels or not series:
        return None
    return {
        "kind": kind,
        "title": _s(data.get("title"), 120),
        "labels": labels,
        "series": series,
        "unit": _s(data.get("unit"), 16),
        "y_label": _s(data.get("y_label"), 40),
        "as_of": _s(data.get("as_of"), 40),
        "source": _s(data.get("source"), 80),
        "source_url": safe_url(data.get("source_url")),
    }


def _products(data: dict[str, Any]) -> Optional[dict[str, Any]]:
    items = []
    for item in (data.get("items") or [])[:12]:
        url = safe_url(item.get("url"))
        title = _s(item.get("title"), 160)
        price = _num(item.get("price"))
        if not url or not title or price is None:
            continue
        rating = _num(item.get("rating"))
        items.append({
            "title": title,
            "url": url,
            "image": safe_url(item.get("image")),
            "price": price,
            "currency": _s(item.get("currency") or "USD", 8),
            "merchant": _s(item.get("merchant") or _host(url), 60),
            "rating": rating if rating is not None and 0 <= rating <= 5 else None,
            "reviews": int(_num(item.get("reviews")) or 0) or None,
        })
    if not items:
        return None
    return {"query": _s(data.get("query"), 120), "items": items, "as_of": _s(data.get("as_of"), 40)}


def _media(data: dict[str, Any]) -> Optional[dict[str, Any]]:
    kind = "video" if data.get("kind") == "video" else "image"
    items = []
    for item in (data.get("items") or [])[:12]:
        url = safe_url(item.get("url"))
        thumb = safe_url(item.get("thumbnail") or item.get("image"))
        if not url or not thumb:
            continue
        items.append({
            "title": _s(item.get("title"), 160),
            "url": url,
            "thumbnail": thumb,
            "image": safe_url(item.get("image")) or thumb,
            "duration": _s(item.get("duration"), 12),
            "publisher": _s(item.get("publisher") or _host(url), 60),
            "published": _s(item.get("published"), 32),
            "width": int(_num(item.get("width")) or 0) or None,
            "height": int(_num(item.get("height")) or 0) or None,
        })
    if not items:
        return None
    return {"kind": kind, "query": _s(data.get("query"), 120), "items": items}


def _citations(data: dict[str, Any]) -> Optional[dict[str, Any]]:
    seen: set[str] = set()
    items = []
    for item in (data.get("items") or [])[:10]:
        url = safe_url(item.get("url"))
        if not url or url in seen:
            continue
        seen.add(url)
        items.append({"title": _s(item.get("title") or _host(url), 140), "url": url, "site": _host(url),
                      "snippet": _s(item.get("snippet"), 220), "image": safe_url(item.get("image"))})
    return {"items": items} if items else None


def _score(data: dict[str, Any]) -> Optional[dict[str, Any]]:
    games = []
    for game in (data.get("games") or [])[:8]:
        home, away = _s(game.get("home"), 60), _s(game.get("away"), 60)
        if not home or not away:
            continue
        games.append({"home": home, "away": away, "home_score": _num(game.get("home_score")), "away_score": _num(game.get("away_score")),
                      "status": _s(game.get("status"), 40), "start": _s(game.get("start"), 40), "league": _s(game.get("league"), 60)})
    return {"title": _s(data.get("title"), 120), "games": games, "as_of": _s(data.get("as_of"), 40)} if games else None


def _artifact(data: dict[str, Any]) -> Optional[dict[str, Any]]:
    artifact_id = _s(data.get("id"), 64)
    if not artifact_id:
        return None
    return {"id": artifact_id, "title": _s(data.get("title"), 120), "kind": _s(data.get("kind"), 16),
            "version": int(_num(data.get("version")) or 1), "language": _s(data.get("language"), 24)}


_NORMALIZERS: dict[str, Callable[[dict[str, Any]], Optional[dict[str, Any]]]] = {
    "weather": _weather,
    "chart": _chart,
    "product_carousel": _products,
    "media": _media,
    "citations": _citations,
    "score_card": _score,
    "artifact": _artifact,
}


def normalize(widget: Any) -> Optional[dict[str, Any]]:
    if not isinstance(widget, dict):
        return None
    kind = str(widget.get("type") or "")
    fn = _NORMALIZERS.get(kind)
    if fn is None:
        return None
    try:
        data = fn(dict(widget.get("data") or {}))
    except Exception:
        return None
    return {"type": kind, "data": data} if data is not None else None


def now_label() -> str:
    """'as of' stamp for prices and live data, in local time."""
    return time.strftime("%b %d, %Y %I:%M %p").replace(" 0", " ")
