"""Environment for processes EchoSpeak starts (terminal commands, MCP servers, dev servers).

They get the user's environment, but never EchoSpeak's own API keys: with those a
project dependency or an MCP server could call the local API, approve its own
actions or change settings.
"""

from __future__ import annotations

import os
from typing import Mapping, Optional

PRIVATE_VARS = frozenset({
    "API_AUTH_KEY", "ADMIN_API_KEY", "A2A_AUTH_KEY", "WEBHOOK_SECRET",
    "TAURI_SIGNING_PRIVATE_KEY", "TAURI_SIGNING_PRIVATE_KEY_PASSWORD",
})


def child_env(extra: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key.upper() not in PRIVATE_VARS}
    if extra:
        env.update({str(k): str(v) for k, v in extra.items()})
    return env
