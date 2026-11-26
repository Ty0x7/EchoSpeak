"""Echo Companion's background service inside the EchoSpeak backend.

It waits for hook files in the spool, using inotify on Linux and a cheap directory check
elsewhere, and ingests them into the observation store. Ingested events go to the
listeners the commentary and knowledge layers register.

Nothing here can affect the agents. They have already finished writing their hook file
before the service sees it, and any error is logged and swallowed.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import sys
import threading
import time
from typing import Optional

from config import config
from agent.companion.observe import get_companion_store

logger = logging.getLogger(__name__)

_thread: Optional[threading.Thread] = None
_stop = threading.Event()
IN_MOVED_TO = 0x00000080
IN_CLOSE_WRITE = 0x00000008


def _inotify_fd(path: str) -> int:
    if not sys.platform.startswith("linux"):
        return -1
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
        fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if fd < 0:
            return -1
        if libc.inotify_add_watch(fd, path.encode(), IN_MOVED_TO | IN_CLOSE_WRITE) < 0:
            os.close(fd)
            return -1
        return fd
    except (OSError, AttributeError):
        return -1


def _run() -> None:
    import select

    store = get_companion_store()
    try:
        from agent.companion.commentary import get_commentary

        get_commentary()  # subscribes to new events; does nothing while the companion is off
    except Exception:
        logger.warning("Echo Companion commentary did not start", exc_info=True)
    store.spool.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(store.spool, 0o700)
        os.chmod(store.spool.parent, 0o700)
    except OSError:
        pass
    fd = _inotify_fd(str(store.spool))
    last_prune = 0.0
    while not _stop.is_set():
        try:
            store.ingest_spool()
            if config.companion_enabled:
                from agent.companion.history import refresh

                refresh(store)
            if time.time() - last_prune > 3600:
                store.prune()
                last_prune = time.time()
        except Exception:
            logger.warning("Echo Companion could not read the hook spool", exc_info=True)
        if fd >= 0:
            ready, _, _ = select.select([fd], [], [], 60)
            if ready:
                try:
                    while os.read(fd, 65536):
                        pass
                except BlockingIOError:
                    pass
                time.sleep(0.05)  # let a burst of hook files land together
        else:
            _stop.wait(2)
    if fd >= 0:
        os.close(fd)


def start() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_run, name="echo-companion", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()

