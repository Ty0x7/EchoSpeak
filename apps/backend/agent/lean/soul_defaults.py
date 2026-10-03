"""Keep the desktop's copy of SOUL.md current when the user never edited it.

The packaged app reads SOUL.md from its data folder (SOUL_PATH), not from the
bundle, so a new default personality would never reach existing installs. On
startup, a copy that is missing or still byte-for-byte a previous default is
replaced with the bundled one. A copy the user edited is left alone.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from loguru import logger

# sha256 of earlier default SOUL.md texts (normalised: no \r, trailing spaces stripped).
PREVIOUS_DEFAULTS = {
    "27777e8e5421be53b27bbc01e2f2fde6840904f46846e652abd1084a59cae659",  # 10.0.0 ("a little sassy")
    "3b4f0a96030cb4e3ebcfe9ba46c9e349c3170c5cee7b543916cc57462ce0e64e",
    "631f485889874e51901c59c0a8aa2019aee8e0d10f3b787113b5e07a40c8645b",
    "e644ff2645943e48dc6f33d689dab81a94c48d93357c5cbac8f2d5370836a168",
}


def _digest(text: str) -> str:
    normalised = "\n".join(line.rstrip() for line in text.replace("\r", "").strip().split("\n"))
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def refresh_default_soul(bundled: Path | None = None, target: Path | None = None) -> str:
    """Returns 'installed', 'updated', 'kept' or 'skipped'."""
    bundled = bundled or Path(__file__).resolve().parents[2] / "SOUL.md"
    raw_target = target or (Path(os.environ["SOUL_PATH"]) if os.environ.get("SOUL_PATH") else None)
    if raw_target is None or not bundled.exists():
        return "skipped"
    target = Path(raw_target).expanduser()
    if target.resolve() == bundled.resolve():
        return "skipped"
    new_text = bundled.read_text(encoding="utf-8")
    try:
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(new_text, encoding="utf-8")
            return "installed"
        current = target.read_text(encoding="utf-8")
        if _digest(current) in PREVIOUS_DEFAULTS and _digest(current) != _digest(new_text):
            target.write_text(new_text, encoding="utf-8")
            logger.info("Updated the default SOUL.md at {} (it had not been edited)", target)
            return "updated"
    except OSError:
        logger.warning("Could not refresh SOUL.md at {}", target, exc_info=True)
        return "skipped"
    return "kept"
