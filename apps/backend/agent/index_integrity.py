"""Pin FAISS index files so a tampered one is never unpickled.

LangChain's FAISS.load_local reads index.pkl with pickle (allow_dangerous_deserialization),
and unpickling a crafted file runs code. EchoSpeak writes these files itself, so after
every save it records the file's SHA-256 next to it, and before every load it checks the
file still matches. A mismatch means something else changed it: the caller refuses to
load and rebuilds the index from its own records instead.

Indexes saved before pinning existed are trusted once and pinned on their next load.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

PIN_NAME = "index.pkl.sha256"


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            sha.update(chunk)
    return sha.hexdigest()


def pin(folder: str | Path) -> None:
    """Record the hash of a just-saved index (call right after save_local)."""
    folder = Path(folder)
    pickle_file = folder / "index.pkl"
    if pickle_file.is_file():
        (folder / PIN_NAME).write_text(_digest(pickle_file), encoding="utf-8")


def verified(folder: str | Path) -> bool:
    """True when index.pkl is the file EchoSpeak saved (or a pre-pinning index, now pinned)."""
    folder = Path(folder)
    pickle_file = folder / "index.pkl"
    if not pickle_file.is_file():
        return True  # nothing to unpickle
    pin_file = folder / PIN_NAME
    if not pin_file.is_file():
        pin(folder)  # trust on first use: saved by an EchoSpeak release without pinning
        return True
    return pin_file.read_text(encoding="utf-8").strip() == _digest(pickle_file)


class TamperedIndex(ValueError):
    """index.pkl changed outside EchoSpeak; it was not loaded."""


def check(folder: str | Path) -> None:
    if not verified(folder):
        raise TamperedIndex(f"{Path(folder) / 'index.pkl'} changed outside EchoSpeak; not loading it")
