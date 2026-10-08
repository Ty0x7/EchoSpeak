"""Limits on messages agents send to other people (OWASP ASI10, rogue agents).

A confused or hijacked agent should not be able to spam your contacts. Every
send (email, Discord, Telegram, WhatsApp, Slack, X, GitHub comments, and the
results routines deliver) counts against a per-channel budget: a few a minute,
a few dozen an hour. Over the budget, the send is refused with a reason the
agent passes on to the user, instead of the message going out.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Any

OUTBOUND_MESSAGES = {
    "email_send", "email_reply",
    "discord_send_channel", "discord_web_send",
    "telegram_send", "whatsapp_send", "slack_send",
    "twitter_post", "tweet_post",
    "github_create_issue", "github_comment",
}

_lock = threading.Lock()
_sent: dict[str, deque[float]] = {}


def _limit(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, "") or default))
    except ValueError:
        return default


def per_minute() -> int:
    return _limit("ECHOSPEAK_OUTBOUND_PER_MINUTE", 5)


def per_hour() -> int:
    return _limit("ECHOSPEAK_OUTBOUND_PER_HOUR", 30)


def channel_of(name: str) -> str:
    """email_send -> email, discord_web_send -> discord, tweet_post -> x."""
    name = str(name or "")
    if name.startswith(("twitter", "tweet")):
        return "x"
    return name.split("_", 1)[0] or name


def is_outbound(name: str, entry: Any = None) -> bool:
    if name in OUTBOUND_MESSAGES:
        return True
    origin = str(getattr(entry, "origin", "") or "")
    lowered = name.lower()
    return origin in {"mcp", "connection"} and bool(getattr(entry, "is_action", False)) and any(
        word in lowered for word in ("send", "post", "message", "reply", "email", "tweet", "dm"))


def take(channel: str, now: float | None = None) -> str:
    """Count one message on `channel`. '' when it may go out, otherwise why it can't."""
    now = time.time() if now is None else now
    with _lock:
        times = _sent.setdefault(channel, deque())
        while times and now - times[0] > 3600:
            times.popleft()
        last_minute = sum(1 for t in times if now - t <= 60)
        if last_minute >= per_minute():
            return (f"Not sent: the limit of {per_minute()} {channel} messages a minute was reached. "
                    "Tell the user what you were about to send instead of retrying.")
        if len(times) >= per_hour():
            return (f"Not sent: the limit of {per_hour()} {channel} messages an hour was reached. "
                    "Tell the user what you were about to send instead of retrying.")
        times.append(now)
        return ""


def counts(now: float | None = None) -> dict[str, dict[str, int]]:
    now = time.time() if now is None else now
    with _lock:
        return {channel: {"last_minute": sum(1 for t in times if now - t <= 60),
                          "last_hour": sum(1 for t in times if now - t <= 3600)}
                for channel, times in _sent.items() if times}


def reset() -> None:
    with _lock:
        _sent.clear()
