"""Show, install or remove Echo Companion's Claude Code / Codex hooks. Dry run unless --apply.

    python scripts/companion_hooks.py status
    python scripts/companion_hooks.py install --root ~/Work --root ~/Projects          # shows the changes
    python scripts/companion_hooks.py install --root ~/Work --root ~/Projects --apply
    python scripts/companion_hooks.py uninstall --apply
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.companion.install import main  # noqa: E402

raise SystemExit(main(sys.argv[1:]))
