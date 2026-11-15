"""Echo TUI (prompt_toolkit + rich). Optional: the plain REPL is the fallback when these are missing."""
from __future__ import annotations

INSTALL_HINT = ("echospeak: using the plain REPL; for the full terminal UI install its dependencies with "
                "apps/terminal/install.sh (prompt_toolkit and rich in apps/terminal/.venv)")


def available() -> bool:
    try:
        import prompt_toolkit  # noqa: F401
        import rich  # noqa: F401
        import wcwidth  # noqa: F401
    except ImportError:
        return False
    return True
