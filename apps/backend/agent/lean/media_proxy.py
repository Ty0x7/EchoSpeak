"""Fetch remote images for chat cards through the backend.

The chat never loads third-party images directly: every thumbnail and product
photo goes through ``/lean/media?url=...``. That keeps the user's IP and cookies
away from arbitrary hosts, applies the same SSRF rules as web fetches (public
addresses only, pinned DNS), and caps time and size. Results are cached on disk.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Optional

from agent.lean.widgets import safe_url

MAX_BYTES = 5_000_000
CACHE_LIMIT_BYTES = 200_000_000
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/avif", "image/svg+xml", "image/x-icon", "image/vnd.microsoft.icon"}
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
_OSM_UA = "EchoSpeak/10.0 (local desktop assistant; https://github.com/Ty0x7/EchoSpeak)"
_LOCK = threading.Lock()


class MediaError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status


def _cache_dir() -> Path:
    from config import DATA_DIR

    path = Path(DATA_DIR) / "cache" / "media"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _sniff(body: bytes) -> str:
    head = body[:16]
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp"
    if b"ftypavif" in body[:32]:
        return "image/avif"
    return ""


def _prune(directory: Path) -> None:
    files = sorted((p for p in directory.glob("*.bin")), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in files)
    while files and total > CACHE_LIMIT_BYTES:
        victim = files.pop(0)
        total -= victim.stat().st_size
        victim.unlink(missing_ok=True)
        victim.with_suffix(".json").unlink(missing_ok=True)


def fetch_image(url: str) -> tuple[bytes, str]:
    """Return (bytes, content_type) for a public image URL, from cache when possible."""
    clean = safe_url(url)
    if not clean:
        raise MediaError(400, "Only http(s) image URLs are allowed.")
    key = hashlib.sha256(clean.encode("utf-8")).hexdigest()
    directory = _cache_dir()
    body_path, meta_path = directory / f"{key}.bin", directory / f"{key}.json"
    if body_path.exists() and meta_path.exists():
        try:
            content_type = json.loads(meta_path.read_text(encoding="utf-8")).get("type", "")
            if content_type in ALLOWED_TYPES:
                body_path.touch()
                return body_path.read_bytes(), content_type
        except Exception:
            pass
    from agent.safe_web_retrieval import SafeWebRetrievalError, fetch_public_bytes

    try:
        # OpenStreetMap's tile policy asks apps to identify themselves.
        agent = _OSM_UA if "tile.openstreetmap.org" in clean else _UA
        _, headers, body = fetch_public_bytes(
            clean, headers={"User-Agent": agent, "Accept": "image/avif,image/webp,image/*;q=0.8"}, timeout_seconds=6, max_bytes=MAX_BYTES
        )
    except SafeWebRetrievalError as exc:
        raise MediaError(502, f"Image unavailable: {exc}") from exc
    declared = str(headers.get("content-type", "")).split(";")[0].strip().lower()
    content_type = declared if declared in ALLOWED_TYPES else _sniff(body)
    if content_type not in ALLOWED_TYPES or not body:
        raise MediaError(415, "Not an image.")
    with _LOCK:
        body_path.write_bytes(body)
        meta_path.write_text(json.dumps({"type": content_type, "url": clean}), encoding="utf-8")
        _prune(directory)
    return body, content_type


def media_response_headers(content_type: str) -> dict[str, str]:
    headers = {"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"}
    if content_type == "image/svg+xml":
        # Opened on its own, an SVG could run script; this keeps it inert.
        headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
    return headers


def proxied(url: Optional[str]) -> str:
    """Path the UI should load for a remote image."""
    from urllib.parse import quote

    clean = safe_url(url)
    return f"/lean/media?url={quote(clean, safe='')}" if clean else ""
