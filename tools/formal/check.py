"""Release/engineering gate for the reviewed canonical memory visibility model."""
from __future__ import annotations

import itertools
import json
import os
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ALLOWED_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}
OBLIGATIONS = {"readRequiresActiveOwner", "projectNavigationIsolates", "legacyPathRequired",
               "sessionNavigationIsolates", "retirementRevokes"}


def compare_vectors(expected, actual):
    ids = ("", "a", "b")
    cases = itertools.product((False, True), ("account", "session", "project", "unknown"), ("a", "b"),
                              ids, ids, ids, ids, ids, ids)
    count = 0
    for vector, verdict in zip(cases, expected, strict=True):
        active, scope, owner, session, project, path, ask_session, ask_project, ask_path = vector
        record = {"active": active, "scope": scope, "owner_id": owner, "source_session_id": session,
                  "project_id": project, "metadata": {"project_path": path}}
        result = actual(record, owner="a", thread_id=ask_session or None, project_id=ask_project,
                        project_path=ask_path or None)
        if type(verdict) is not bool or bool(result) != verdict:
            raise ValueError(f"Canonical visibility differs from the Lean contract on {vector!r}")
        count += 1
    return count


def execute(argv, env, cwd=HERE):
    output = subprocess.run(argv, cwd=cwd, env=env, check=True, capture_output=True,
                            text=True, timeout=90)
    return output.stdout


def main():
    path = os.environ.get("ECHOSPEAK_LEAN4_ROOT", "")
    toolchain = Path(path)
    if not toolchain.is_absolute() or not (toolchain / "bin/lake").is_file():
        raise ValueError("Set ECHOSPEAK_LEAN4_ROOT to the pinned Lean 4 distribution.")
    pin = (HERE / "lean-toolchain").read_text().strip().split(":v")[1]
    binary = toolchain / "bin"
    env = {"PATH": f"{binary}:/usr/bin:/bin"}
    version = execute([str(binary / "lean"), "--version"], env)
    if f"version {pin}," not in version:
        raise ValueError(f"Expected Lean {pin}.")
    execute([str(binary / "lake"), "build"], env)
    env["LEAN_PATH"] = str(HERE / ".lake" / "build" / "lib" / "lean")
    output = execute([str(binary / "lean"), "-DwarningAsError=true", "MemoryVisibility.lean"], env)
    checked = dict(re.findall(r"'EchoSpeak\.(\w+)' depends on axioms: \[([^\]]*)\]", output))
    for name in re.findall(r"'EchoSpeak\.(\w+)' does not depend on any axioms", output):
        checked[name] = ""
    if set(checked) != OBLIGATIONS or any(set(filter(None, (a.strip() for a in row.split(",")))) -
                                          ALLOWED_AXIOMS for row in checked.values()):
        raise ValueError("Missing proof obligation or unapproved axioms")
    execute([str(binary / "lake"), "env", "leanchecker", "--fresh", "MemoryVisibility"], env)
    vectors = json.loads(execute([str(binary / "lake"), "env", "lean", "--run", "MemoryVectors.lean"], env))
    sys.path.insert(0, str(HERE.parents[1] / "apps" / "backend"))
    from agent.memory import AgentMemory

    count = compare_vectors(vectors, AgentMemory._record_visible)
    print(version.strip())
    print(output.strip())
    print(f"Independent environment replay passed. {count} Python/Lean scope decisions matched.")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print((exc.stdout or "") + (exc.stderr or ""), file=sys.stderr)
        raise SystemExit(1) from None
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(str(exc)) from None
