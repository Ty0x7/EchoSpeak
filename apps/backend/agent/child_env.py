"""Environment for processes EchoSpeak starts (terminal commands, MCP servers, dev servers).

They get the user's environment, but never EchoSpeak's own API keys: with those a
project dependency or an MCP server could call the local API, approve its own
actions or change settings.
"""

from __future__ import annotations

import os
from typing import Mapping, Optional

# Every secret EchoSpeak itself reads (config.py, channels, connectors). A package
# install or an MCP server started by an agent must not see any of them.
PRIVATE_VARS = frozenset({
    "API_AUTH_KEY", "ADMIN_API_KEY", "A2A_AUTH_KEY", "WEBHOOK_SECRET", "WEBHOOK_SECRET_PATH",
    "TAURI_SIGNING_PRIVATE_KEY", "TAURI_SIGNING_PRIVATE_KEY_PASSWORD",
    "OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "XAI_API_KEY", "MINIMAX_API_KEY",
    "BRAVE_SEARCH_API_KEY", "TAVILY_API_KEY", "RUNWAY_API_KEY", "ODDS_API_KEY", "THE_ODDS_API_KEY",
    "DISCORD_BOT_TOKEN", "TELEGRAM_BOT_TOKEN", "EMAIL_PASSWORD", "HOME_ASSISTANT_TOKEN", "NOTION_TOKEN",
    "GITHUB_TOKEN", "SPOTIFY_CLIENT_SECRET",
    "TWITCH_BOT_ACCESS_TOKEN", "TWITCH_CLIENT_SECRET", "TWITCH_EVENTSUB_SECRET",
    "TWITTER_ACCESS_TOKEN", "TWITTER_ACCESS_TOKEN_SECRET", "TWITTER_BEARER_TOKEN", "TWITTER_CLIENT_SECRET",
})
_PRIVATE_WORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL")


def is_private(name: str) -> bool:
    upper = str(name or "").upper()
    if upper in PRIVATE_VARS:
        return True
    # EchoSpeak's own settings, however a future release names them.
    return upper.startswith("ECHOSPEAK_") and any(word in upper for word in _PRIVATE_WORDS)


# Keys that control EchoSpeak itself: never handed to a child, even when configured for it.
AUTH_VARS = frozenset({
    "API_AUTH_KEY", "ADMIN_API_KEY", "A2A_AUTH_KEY", "WEBHOOK_SECRET", "WEBHOOK_SECRET_PATH",
    "TAURI_SIGNING_PRIVATE_KEY", "TAURI_SIGNING_PRIVATE_KEY_PASSWORD",
})


def child_env(extra: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """The user's environment without EchoSpeak's secrets, plus `extra` (settings configured
    for this one child, e.g. an MCP server's own token), which may not include auth keys."""
    env = {key: value for key, value in os.environ.items() if not is_private(key)}
    if extra:
        env.update({str(k): str(v) for k, v in extra.items() if str(k).upper() not in AUTH_VARS})
    return env
