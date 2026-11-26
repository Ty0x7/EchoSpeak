"""Let long-lived server-sent-event streams end when the server is asked to stop.

On shutdown, uvicorn waits for open responses to finish *before* running the app's
lifespan shutdown, and an endless event stream never finishes, so the old process
lingers. It holds its clients' connections, which then never reconnect to a new backend.
``install()`` wraps ``uvicorn.Server.handle_exit``, as sse-starlette does, so streams see
``SHUTTING_DOWN`` straight away. ``MAX_STREAM_SECONDS`` caps any one stream as a backstop;
clients simply reconnect.
"""

from __future__ import annotations

import threading

SHUTTING_DOWN = threading.Event()
MAX_STREAM_SECONDS = 15 * 60
_installed = False


def install() -> None:
    global _installed
    if _installed:
        return
    try:
        import uvicorn

        original = uvicorn.Server.handle_exit

        def handle_exit(self, sig, frame):  # noqa: ANN001
            SHUTTING_DOWN.set()
            return original(self, sig, frame)

        uvicorn.Server.handle_exit = handle_exit
        _installed = True
    except Exception:
        pass
