"""Put a finished temporary file in place of its target, riding out Windows sharing violations.

On Windows, os.replace fails with WinError 5/32 while any other handle has the target open
without delete sharing: a reader in another thread, the search indexer, an antivirus scan of a
file that was just written. Those holds last milliseconds, so a short bounded retry succeeds
where a single attempt loses work. StateStore._write_json does the same for the main state.
"""
from __future__ import annotations

import os
import random
import time

ATTEMPTS = 8
WINDOWS = os.name == "nt"


def replace(source, target) -> None:
    """os.replace(source, target), retried briefly on a Windows sharing violation, then raised."""
    for attempt in range(ATTEMPTS):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if not WINDOWS or attempt == ATTEMPTS - 1:
                raise
            time.sleep(min(0.05 * 2 ** attempt, 0.8) + random.uniform(0.0, 0.05))
