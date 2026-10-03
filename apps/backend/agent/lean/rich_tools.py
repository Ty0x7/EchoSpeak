"""Look-up tools whose results show as cards in the chat.

Each returns compact text for the model and attaches a widget built from the
same source data (agent/lean/widgets.py), so the card never shows a number,
price or link the source didn't give.

Sources (all keyless):
- stock_history: Yahoo Finance chart API.
- product_search: DuckDuckGo results on retailer product pages, read from the
  page's own schema.org Product data or Walmart's page data.
- video_search / image_search: DuckDuckGo video and image search.
"""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote, urlparse

from loguru import logger

from agent.lean.toolbox import NativeTool
from agent.lean.widgets import attach, now_label

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
RANGES = {"1mo": "1mo", "3mo": "3mo", "6mo": "6mo", "ytd": "ytd", "1y": "1y", "2y": "2y", "5y": "5y"}


def _get_json(url: str, timeout: float = 8.0) -> Any:
    from agent.safe_web_retrieval import fetch_public_bytes

    _, _, body = fetch_public_bytes(url, headers={"User-Agent": UA, "Accept": "application/json"}, timeout_seconds=timeout, max_bytes=4_000_000)
    return json.loads(body.decode("utf-8", "replace"))


def _ddgs():
    try:
        from ddgs import DDGS
    except ImportError:  # older package name
        from duckduckgo_search import DDGS  # type: ignore
    return DDGS()


# ── stocks ────────────────────────────────────────────────────────────────

def _resolve_symbol(text: str) -> str:
    raw = text.strip()
    if re.fullmatch(r"[\^A-Za-z0-9.\-=]{1,12}", raw) and (raw.isupper() or "." in raw or raw.startswith("^")):
        return raw.upper()
    data = _get_json(f"https://query1.finance.yahoo.com/v1/finance/search?q={quote(raw)}&quotesCount=3&newsCount=0")
    for row in data.get("quotes") or []:
        if row.get("symbol") and row.get("quoteType") in {"EQUITY", "ETF", "INDEX", "MUTUALFUND", "CRYPTOCURRENCY"}:
            return str(row["symbol"])
    raise ValueError(f"no ticker found for '{raw}'")


def _history(symbol: str, period: str) -> dict[str, Any]:
    data = _get_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol)}?range={period}&interval=1d")
    result = ((data.get("chart") or {}).get("result") or [None])[0]
    if not result:
        error = ((data.get("chart") or {}).get("error") or {}).get("description") or "no data"
        raise ValueError(f"{symbol}: {error}")
    stamps = result.get("timestamp") or []
    closes = ((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    points = [(datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d"), float(c)) for t, c in zip(stamps, closes) if c is not None]
    if not points:
        raise ValueError(f"{symbol}: no prices in that range")
    meta = result.get("meta") or {}
    return {"symbol": symbol, "name": meta.get("longName") or meta.get("shortName") or symbol, "currency": meta.get("currency") or "USD", "points": points}


def stock_history(args: dict[str, Any]) -> str:
    raw = args.get("symbols") or args.get("symbol") or ""
    names = [s for s in (raw if isinstance(raw, list) else re.split(r"[,;/]| vs\.? | and ", str(raw))) if str(s).strip()][:4]
    if not names:
        return "Error: give one or more stock tickers or company names in symbols, e.g. ['NVDA', 'AMD']."
    period = RANGES.get(str(args.get("range") or "ytd").lower(), "ytd")
    series, lines = [], []
    for name in names:
        try:
            series.append(_history(_resolve_symbol(str(name)), period))
        except Exception as exc:
            lines.append(f"{name}: no data ({exc})")
    if not series:
        return "Error: " + "; ".join(lines)
    as_of = now_label()
    for item in series:
        pts = item["points"]
        first, last = pts[0][1], pts[-1][1]
        hi = max(pts, key=lambda p: p[1])
        lo = min(pts, key=lambda p: p[1])
        lines.append(
            f"{item['symbol']} ({item['name']}): {pts[0][0]} {first:.2f} -> {pts[-1][0]} {last:.2f} {item['currency']} "
            f"({(last / first - 1) * 100:+.1f}%), high {hi[1]:.2f} on {hi[0]}, low {lo[1]:.2f} on {lo[0]}"
        )
    # One shared date axis; several symbols are compared as % change from the start.
    dates = sorted({d for item in series for d, _ in item["points"]})
    step = max(1, len(dates) // 120)
    axis = dates[::step] if dates[-1] in dates[::step] else dates[::step] + [dates[-1]]
    compare = len(series) > 1
    chart_series = []
    for item in series:
        lookup = dict(item["points"])
        base = item["points"][0][1]
        values = []
        last_value: Optional[float] = None
        for d in axis:
            v = lookup.get(d, last_value)
            last_value = v
            values.append(None if v is None else round((v / base - 1) * 100, 2) if compare else round(v, 2))
        chart_series.append({"name": item["symbol"], "values": values})
    label = {"ytd": "this year", "1mo": "1 month", "3mo": "3 months", "6mo": "6 months", "1y": "1 year", "2y": "2 years", "5y": "5 years"}[period]
    attach({"type": "chart", "data": {
        "kind": "line",
        "title": (" vs ".join(s["symbol"] for s in series) + f", % change {label}") if compare else f"{series[0]['name']} ({series[0]['symbol']}), {label}",
        "labels": axis,
        "series": chart_series,
        "unit": "%" if compare else series[0]["currency"],
        "as_of": as_of,
        "source": "Yahoo Finance",
        "source_url": f"https://finance.yahoo.com/quote/{quote(series[0]['symbol'])}",
    }})
    return f"Daily closing prices ({label}), as of {as_of}, from Yahoo Finance. The user sees a chart of this data.\n" + "\n".join(lines)


# ── products ──────────────────────────────────────────────────────────────

def _first(value: Any) -> Any:
    return value[0] if isinstance(value, list) and value else value


def _product_from_jsonld(html: str) -> Optional[dict[str, Any]]:
    for block in re.findall(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", html, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except Exception:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for item in items:
            if not isinstance(item, dict) or "Product" not in str(item.get("@type")):
                continue
            offer = _first(item.get("offers"))
            if isinstance(offer, dict) and offer.get("@type") == "AggregateOffer":
                price = offer.get("lowPrice") or offer.get("price")
            else:
                price = offer.get("price") if isinstance(offer, dict) else None
            rating = item.get("aggregateRating") if isinstance(item.get("aggregateRating"), dict) else {}
            image = _first(item.get("image"))
            if isinstance(image, dict):
                image = image.get("url")
            return {
                "title": item.get("name"),
                "price": price,
                "currency": (offer or {}).get("priceCurrency") if isinstance(offer, dict) else None,
                "image": image,
                "rating": rating.get("ratingValue"),
                "reviews": rating.get("reviewCount") or rating.get("ratingCount"),
            }
    return None


def _product_from_walmart(html: str) -> Optional[dict[str, Any]]:
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
    except Exception:
        return None
    product = (((data.get("props") or {}).get("pageProps") or {}).get("initialData") or {}).get("data", {}).get("product")
    if not isinstance(product, dict):
        return None
    price = (((product.get("priceInfo") or {}).get("currentPrice") or {}).get("price"))
    return {
        "title": product.get("name"),
        "price": price,
        "currency": (((product.get("priceInfo") or {}).get("currentPrice") or {}).get("currencyUnit")) or "USD",
        "image": (product.get("imageInfo") or {}).get("thumbnailUrl"),
        "rating": product.get("averageRating"),
        "reviews": product.get("numberOfReviews"),
    }


def _meta(html: str, name: str) -> str:
    match = re.search(rf'<meta[^>]+(?:property|name)="{re.escape(name)}"[^>]+content="([^"]*)"', html, re.I)
    return match.group(1) if match else ""


def _read_product(url: str) -> Optional[dict[str, Any]]:
    from agent.safe_web_retrieval import fetch_public_bytes

    try:
        final_url, _, body = fetch_public_bytes(url, headers={"User-Agent": UA, "Accept": "text/html", "Accept-Language": "en-US"}, timeout_seconds=8, max_bytes=4_000_000)
    except Exception:
        return None
    html = body.decode("utf-8", "ignore")
    product = _product_from_walmart(html) if "walmart.com" in final_url else None
    product = product or _product_from_jsonld(html)
    if not product:
        price = _meta(html, "product:price:amount") or _meta(html, "og:price:amount")
        if price:
            product = {"title": _meta(html, "og:title"), "price": price, "currency": _meta(html, "product:price:currency") or "USD", "image": _meta(html, "og:image")}
    if not product or not product.get("title") or product.get("price") in (None, ""):
        return None
    product["image"] = product.get("image") or _meta(html, "og:image")
    product["url"] = final_url
    product["merchant"] = urlparse(final_url).netloc.removeprefix("www.")
    return product


def product_search(args: dict[str, Any]) -> str:
    query = str(args.get("query") or "").strip()
    if not query:
        return "Error: give a product query, e.g. 'wireless controller'."
    max_price = args.get("max_price")
    try:
        max_price = float(max_price) if max_price not in (None, "") else None
    except (TypeError, ValueError):
        max_price = None
    urls: list[str] = []
    try:
        with _ddgs() as ddgs:
            for search in (f"{query} site:walmart.com/ip", f"{query} buy price"):
                for hit in ddgs.text(search, max_results=8):
                    url = str(hit.get("href") or "")
                    if url and url not in urls:
                        urls.append(url)
    except Exception as exc:
        return f"Error: product search failed ({exc})"
    with ThreadPoolExecutor(max_workers=8) as pool:
        found = [p for p in pool.map(_read_product, urls[:16]) if p]
    items = []
    for item in found:
        try:
            price = float(str(item["price"]).replace(",", "").replace("$", ""))
        except (TypeError, ValueError):
            continue
        if max_price is not None and price > max_price:
            continue
        items.append({**item, "price": price})
    if not items:
        limit = f" under ${max_price:g}" if max_price is not None else ""
        return (f"No product listings with a readable price were found for '{query}'{limit}. "
                "Say so, and suggest the user check a store directly; do not invent products or prices.")
    items = items[:8]
    as_of = now_label()
    attach({"type": "product_carousel", "data": {"query": query, "items": items, "as_of": as_of}})
    lines = [f"{i}. {p['title']} - ${p['price']:.2f} at {p['merchant']}" + (f", rated {p['rating']}" if p.get("rating") else "") + f"\n   {p['url']}"
             for i, p in enumerate(items, 1)]
    return (f"Product listings for '{query}' read from the store pages, prices as of {as_of} (they can change). "
            "The user sees these as cards with links.\n" + "\n".join(lines))


# ── video and images ──────────────────────────────────────────────────────

def _youtube_fallback(query: str) -> list[dict[str, Any]]:
    """Web results on youtube.com, then title/channel from YouTube's oEmbed and duration from the watch page."""
    from agent.safe_web_retrieval import fetch_public_bytes

    ids: list[str] = []
    try:
        with _ddgs() as ddgs:
            for hit in ddgs.text(f"{query} site:youtube.com/watch", max_results=8):
                match = re.search(r"[?&]v=([\w-]{11})", str(hit.get("href") or ""))
                if match and match.group(1) not in ids:
                    ids.append(match.group(1))
    except Exception:
        return []

    def describe(video_id: str) -> Optional[dict[str, Any]]:
        url = f"https://www.youtube.com/watch?v={video_id}"
        try:
            meta = _get_json(f"https://www.youtube.com/oembed?format=json&url={quote(url, safe='')}")
        except Exception:
            return None
        duration = ""
        try:
            _, _, body = fetch_public_bytes(url, headers={"User-Agent": UA, "Accept-Language": "en-US"}, timeout_seconds=8, max_bytes=3_000_000)
            seconds = re.search(rb'"lengthSeconds":"(\d+)"', body)
            if seconds:
                total = int(seconds.group(1))
                duration = f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}" if total >= 3600 else f"{total // 60}:{total % 60:02d}"
        except Exception:
            pass
        return {"title": meta.get("title"), "url": url, "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                "duration": duration, "publisher": meta.get("author_name") or "YouTube", "published": ""}

    with ThreadPoolExecutor(max_workers=6) as pool:
        return [item for item in pool.map(describe, ids[:6]) if item]


def video_search(args: dict[str, Any]) -> str:
    query = str(args.get("query") or "").strip()
    if not query:
        return "Error: give a video search query."
    rows: list[dict[str, Any]] = []
    for attempt in range(2):
        try:
            with _ddgs() as ddgs:
                rows = list(ddgs.videos(query, max_results=8))
            break
        except Exception as exc:
            logger.debug("video search attempt {} failed: {}", attempt + 1, exc)
            time.sleep(1.0)
    items = []
    if not rows:
        items = _youtube_fallback(query)
    for row in rows:
        images = row.get("images") or {}
        items.append({
            "title": row.get("title"),
            "url": row.get("content"),
            "thumbnail": images.get("large") or images.get("medium") or images.get("small"),
            "duration": row.get("duration"),
            "publisher": row.get("publisher") or row.get("uploader"),
            "published": str(row.get("published") or "")[:10],
        })
    items = [i for i in items if i["url"] and i["thumbnail"]][:6]
    if not items:
        return f"No videos found for '{query}'."
    attach({"type": "media", "data": {"kind": "video", "query": query, "items": items}})
    lines = [f"{i}. {v['title']} ({v['duration'] or '?'}, {v['publisher'] or 'unknown'})\n   {v['url']}" for i, v in enumerate(items, 1)]
    return "Videos found (the user sees them as cards):\n" + "\n".join(lines)


def image_search(args: dict[str, Any]) -> str:
    query = str(args.get("query") or "").strip()
    if not query:
        return "Error: give an image search query."
    try:
        with _ddgs() as ddgs:
            rows = list(ddgs.images(query, max_results=12))
    except Exception as exc:
        return f"Error: image search failed ({exc})"
    items = [{
        "title": row.get("title"),
        "url": row.get("url"),
        "image": row.get("image"),
        "thumbnail": row.get("thumbnail") or row.get("image"),
        "width": row.get("width"),
        "height": row.get("height"),
        "publisher": row.get("source"),
    } for row in rows if row.get("url") and (row.get("thumbnail") or row.get("image"))][:9]
    if not items:
        return f"No images found for '{query}'."
    attach({"type": "media", "data": {"kind": "image", "query": query, "items": items}})
    lines = [f"{i}. {img['title']} ({img['url']})" for i, img in enumerate(items, 1)]
    return "Images found (the user sees them as a gallery):\n" + "\n".join(lines)


def rich_tools() -> list[NativeTool]:
    return [
        NativeTool(
            name="stock_history",
            description=(
                "Daily stock or index prices for a period, shown to the user as a chart. Use for 'how did X do', "
                "'compare X and Y stock'. Pass tickers (NVDA) or company names."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "symbols": {"type": "array", "items": {"type": "string"}, "description": "1-4 tickers or company names"},
                    "range": {"type": "string", "enum": list(RANGES), "description": "Period; default ytd (this year)"},
                },
                "required": ["symbols"],
            },
            func=stock_history,
            parallel_safe=True,
        ),
        NativeTool(
            name="product_search",
            description=(
                "Find products for sale with real prices, images and store links, shown as product cards. "
                "Use when the user wants to buy or compare products. Prices come from the store pages."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to buy, e.g. 'wireless controller'"},
                    "max_price": {"type": "number", "description": "Upper price limit in dollars, if the user gave one"},
                },
                "required": ["query"],
            },
            func=product_search,
            parallel_safe=True,
        ),
        NativeTool(
            name="video_search",
            description="Find videos (YouTube and others) with thumbnails and durations, shown as video cards. Use when the user asks for a video or tutorial to watch.",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            func=video_search,
            parallel_safe=True,
        ),
        NativeTool(
            name="image_search",
            description="Find pictures on the web, shown as an image gallery. Use when the user wants to see what something looks like.",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            func=image_search,
            parallel_safe=True,
        ),
    ]
