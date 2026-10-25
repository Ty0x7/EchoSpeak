"""Does this Python match apps/backend/requirements.lock.txt? Run by python-env.ps1 before a build.

The installer ships whatever packages the build Python has. Built from a shared Python, a
release picked up whatever was installed there (11.10.3 shipped Pillow 12.2 and PyJWT 2.13,
both with critical advisories). The lock pins exact, hash-checked versions; this refuses a
build whose packages differ from it.

Exit 0 when every locked package is installed at its locked version, 1 otherwise.
"""
from __future__ import annotations

import re
import sys
from importlib import metadata
from pathlib import Path

LOCK = Path(__file__).resolve().parents[2] / "backend" / "requirements.lock.txt"
PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def locked(path: Path = LOCK) -> dict[str, str]:
    return {_norm(m.group(1)): m.group(2) for line in path.read_text(encoding="utf-8").splitlines()
            if (m := PIN.match(line.strip()))}


def installed() -> dict[str, str]:
    return {_norm(d.metadata["Name"] or ""): d.version for d in metadata.distributions() if d.metadata["Name"]}


def differences(want: dict[str, str], have: dict[str, str]) -> list[str]:
    problems = []
    for name, version in sorted(want.items()):
        got = have.get(name)
        if got is None:
            problems.append(f"{name}: not installed (lock: {version})")
        elif got != version:
            problems.append(f"{name}: {got} installed, lock: {version}")
    return problems


if __name__ == "__main__":
    problems = differences(locked(), installed())
    if problems:
        print(f"{len(problems)} packages differ from {LOCK.name}:")
        for line in problems[:40]:
            print("  " + line)
        sys.exit(1)
    print(f"Build Python matches {LOCK.name}.")
