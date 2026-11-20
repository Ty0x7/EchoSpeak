"""Verification ledger: measured progress toward an objective, for Echo native engineering work.

Objective → Research → Environment → Verification → Adaptation → Completion is a workflow,
not six model calls (docs/research/verified-engineering.md). The model researches, attempts
and adapts. This ledger is the deterministic part around it. It sees every finished tool call
(LeanTurn.verifier), makes no model calls, and adds nothing to a turn that runs no checks:

- **Checks** are parsed from what actually ran: tests (pytest, unittest, jest, vitest, cargo,
  go, dotnet), builds, type checks, linters and benchmarks, giving exit code, passed and
  failed counts, failing test names, the first assertion and a metric value.
- **Progress** is measured, not felt. Each command scope (the command without its flags) has a
  baseline, a best result and regressions from it. Attempts are the changes between checks.
  A declared metric with a direction and target makes hill climbing explicit, for example
  "p50 below 100 ms while the tests stay green".
- **Bottlenecks** are classified from evidence, each with the next justified action rather than
  a generic "try again":
  - environment: a missing module, command or service
  - authorization: refused, held or switched off
  - verification: the check ran nothing, or nothing has checked a lot of changes
  - information: the thing being looked for isn't found, again and again
  - implementation: the same failures through several attempts
  - model: malformed calls in a row
- **Reward hacking** is visible. Editing test files after they failed, unless the objective is
  about tests, means later passes don't count as verification.
- **Completion** is a status computed from evidence, which keeps "a command succeeded" apart
  from "the objective was met":
  - verified: the declared objective's check passes
  - checks pass: there was no objective, but the checks after the last change pass
  - partly verified, failing, unverified, or blocked

  It's shown under the answer, saved with interrupted work so a resumed attempt continues
  from the same baseline, and kept in the episode the learning system grades.

Feedback reaches the model as an app note on the check's own result, never as a second
generation of an answer.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

STALL_ATTEMPTS = 3        # attempts in a row that don't improve on the best result
MODEL_SLIPS = 3           # malformed tool calls in a row
READ_MISSES = 4           # lookups in a row that found nothing
UNCHECKED_CHANGES = 8     # changes with no check at all, before it's called a verification gap
ENVIRONMENT_STOP = 3      # checks blocked by the setup before the note says to stop fixing it and report

_HEADER = re.compile(r"^\[(ok|exit code (-?\d+)|exited with code (-?\d+))\b", re.I)
_PROCESS = re.compile(r"^Process \S+ is (finished(?: \(ok\)| with exit code (-?\d+))?|[^:]*?):\s*(.*)$", re.I)
_KINDS: list[tuple[str, re.Pattern[str]]] = [
    ("bench", re.compile(r"(?i)\b(hyperfine|benchmarks?|bench\b|--benchmark|timeit|perf stat|criterion|k6 run|wrk\b|ab -n)")),
    ("test", re.compile(r"(?i)\b(pytest|py\.test|unittest|nose2|jest|vitest|mocha|rspec|phpunit|ctest|"
                        r"(npm|pnpm|yarn|bun)\s+(run\s+)?test|cargo\s+test|go\s+test|dotnet\s+test|mvn\s+test|gradlew?\s+test|deno\s+test)\b")),
    ("typecheck", re.compile(r"(?i)(\btsc\b|\bmypy\b|\bpyright\b|cargo\s+check|vue-tsc|(npm|pnpm|yarn)\s+run\s+typecheck)")),
    ("lint", re.compile(r"(?i)\b(eslint|ruff|flake8|pylint|clippy|golangci-lint|stylelint|biome\s+(check|lint)|(npm|pnpm|yarn)\s+run\s+lint)\b")),
    ("build", re.compile(r"(?i)((npm|pnpm|yarn|bun)\s+run\s+build|vite\s+build|\bwebpack\b|cargo\s+build|go\s+build|dotnet\s+build|"
                         r"mvn\s+(package|compile)|gradlew?\s+build|\bmake\b|cmake\s+--build|next\s+build|tauri\s+build)")),
]
_PYTHON_TEST_RUNNER = re.compile(r"""^(?:["']?[^"'\s]*/)?["']?python(?:3(?:\.\d+)?)?["']?\s+"""
                                 r"""(?:["']?[^"'\s]*/)?["']?run_tests\.py["']?(?:\s|$)""")
_RUNNER_SUMMARY = re.compile(r"(?m)^\s*(\d+) ran, (\d+) failed\s*$")
_CHANGE_TOOLS = {"file_write", "file_edit", "file_delete", "file_move", "file_copy", "self_edit", "checkpoint_undo"}
_LOOKUP_TOOLS = {"file_read", "file_find", "file_search", "file_list", "self_read", "self_grep", "web_search", "safe_web_fetch"}
_TEST_FILE = re.compile(r"(?i)(^|[\\/])(tests?|__tests__|spec)[\\/]|(^|[\\/])(test_[^\\/]+|[^\\/]+_test|[^\\/]+\.(test|spec))\.[a-z]+$|"
                        r"(^|[\\/])(conftest\.py|run_tests\.py|pytest\.ini|jest\.config\.[a-z]+|vitest\.config\.[a-z]+)$|\.github[\\/]workflows")
_ABOUT_TESTS = re.compile(r"(?i)\b(tests?|spec|testing|coverage|fixtures?|ci|workflow)\b")
# The setup, not the code: a missing package or program, permissions, disk, network, a service that isn't up.
# A missing module with a dotted or relative name ('app.utils', './utils') is usually the code's own, so it's left out.
ENVIRONMENT_ERROR = re.compile(
    # `python -m pytest` without pytest prints the name unquoted ("No module named pytest"); a dotted name stays code's.
    # pytest's INTERNALERROR is the runner crashing (a plugin, a stray conftest or ini), not a test failing.
    r"(?i)(No module named '[^'.]+'|No module named [A-Za-z_][\w-]*(?![\w.'])|INTERNALERROR>|command not found|is not recognized as an internal or external command|"
    r"is not recognized as the name of a cmdlet|Cannot find (module|package) '[^'./][^']*'|ENOENT|EACCES|permission denied|"
    r"Access is denied|MemoryError|out of memory|ENOSPC|No space left|connection refused|ECONNREFUSED|Could not resolve host|"
    r"network is unreachable|getaddrinfo|docker.{0,40}(not running|cannot connect)|externally-managed-environment|"
    r"could not find a version that satisfies)")
_AUTHORIZATION = re.compile(r"(?i)^(not run:|blocked by echospeak's safety policy)|needs the user's approval|the user (denied|said no)|"
                            r"turned off in settings")
_NO_TESTS = re.compile(r"(?i)(no tests ran|collected 0 items|\bran 0 tests|\b0 passing|no test files found|no tests found|"
                       r"test result: ok\. 0 passed|testing: 0 tests|no test suite found)")
_MODEL_SLIP = re.compile(r"(?i)(invalid tool arguments|again with a json object matching its parameters|unknown tool|is not one of your tools)")
_MISS = re.compile(r"(?i)^(error: )?(no (matches|results|files?)\b|nothing (found|matches)|file not found|not found\b|no such file|"
                   r"(it|that|the file|the folder|the path) does(n't| not) exist)")
DONE_CLAIM = re.compile(r"(?i)\b(all (the )?(tests|checks) (now )?pass|tests? (now )?pass(es)?\b|tests (are )?(passing|green)|"
                            r"it works|works now|(is|are) (now )?(fixed|done|complete|working)|fixed (it|the)|i'?ve (fixed|finished|completed)|"
                            r"successfully (implemented|fixed|completed)|ready to (use|go|ship)|task (is )?complete)")
TESTS_PASS_CLAIM = re.compile(r"(?i)\b(all (the )?tests (now )?pass|tests? (now )?pass(es)?\b|tests (are )?(passing|green))")
_TIME_UNITS = {"ns": 1e-6, "µs": 1e-3, "us": 1e-3, "ms": 1.0, "s": 1000.0, "sec": 1000.0}
STATUS_LABELS = {"verified": "Verified", "checks_pass": "Checks pass", "partial": "Partly verified",
                 "failing": "Not verified: the last check failed", "unverified": "Not verified", "blocked": "Blocked"}


# Commands that mention a runner without running it: `pip install pytest`, `grep -i pytest`, `which jest`.
_NOT_A_RUN = {"pip", "pip3", "pipx", "uv", "conda", "mamba", "apt", "apt-get", "dnf", "yum", "pacman", "yay", "brew", "winget",
              "choco", "scoop", "grep", "rg", "egrep", "find", "which", "where", "whereis", "type", "command", "echo", "printf",
              "cat", "less", "more", "head", "tail", "ls", "man", "help", "sed", "awk", "wc", "sort", "uniq", "tee", "xargs",
              "npm-check", "pydoc"}
_SEGMENTS = re.compile(r"&&|\|\||;|\|(?!\|)")
# An exit code something else decides: `pytest || true`, `pytest; exit 0`, or a runner piped into another command.
_MASKED = re.compile(r"(?i)\|\|\s*(true|:|exit\s+0|echo\b)|;\s*(true|exit\s+0)\s*$")


def _runs(segment: str) -> bool:
    """Whether this one command (no && ; |) runs something, as opposed to installing, finding or printing it."""
    words = [w for w in segment.split() if "=" not in w.split("/")[0] or w.startswith("-")]  # drop FOO=bar prefixes
    while words and words[0] in {"sudo", "env", "nice", "time", "command", "exec"}:
        words = words[1:]
    if words and words[0] == "timeout":
        words = words[2:]
    if not words:
        return False
    first = words[0].rsplit("/", 1)[-1].lower()
    if first in _NOT_A_RUN:
        return False
    if first.startswith("python") or first in {"py"}:
        module = words[words.index("-m") + 1].lower() if "-m" in words[:-1] else ""
        if module in {"pip", "pip3", "ensurepip", "venv", "virtualenv", "pydoc"}:
            return False
    return True


def _check_segment(command: str) -> tuple[str, str, bool]:
    """(kind, the segment that runs the check, whether its exit code is masked)."""
    text = str(command or "")
    parts = [(m.start(), m.end()) for m in _SEGMENTS.finditer(text)]
    bounds = [0] + [e for _, e in parts]
    ends = [s for s, _ in parts] + [len(text)]
    runnable = [(text[s:e].strip(), e) for s, e in zip(bounds, ends) if _runs(text[s:e].strip())]
    for kind, pattern in _KINDS:  # the same priority as before: a test anywhere in the command makes it a test
        for segment, end in runnable:
            if pattern.search(segment) or (kind == "test" and _PYTHON_TEST_RUNNER.match(segment)):
                after = text[end:]
                piped = after.startswith("|") and not after.startswith("||") and "pipefail" not in text
                return kind, segment, bool(piped or _MASKED.search(after))
    return "", "", False


# What a test expects: `== 1700000000000`, `assertEqual(x, 'hello-world')`, `toBe("…")`. Only distinctive values
# (5+ digit numbers, 6+ character strings): writing `4` or `True` into code is no sign of special-casing.
_EXPECTED = re.compile(r"(?:==|assertEqual\([^,\n]+,|toBe\(|toEqual\()\s*(-?\d{5,}(?:\.\d+)?|\"[^\"\n]{6,80}\"|'[^'\n]{6,80}')")


def _expected_literals(text: str) -> set[str]:
    return {match.group(1).strip("'\"") for match in _EXPECTED.finditer(str(text or ""))}


def _as_value(literal: str) -> re.Pattern[str]:
    """The literal used as a value (`return 1700000000000`, `FROZEN = "hello-world"`), not just mentioned in a docstring."""
    return re.compile(r"(?:\breturn\b|[^=!<>]=)\s*[(\[]?\s*['\"]?" + re.escape(literal) + r"\b")


def _new_old(name: str, args: dict[str, Any]) -> tuple[str, str]:
    """(the text a file_write or file_edit puts in, the text it replaces)."""
    new = str(args.get("content") if name == "file_write" else args.get("new_text") or args.get("new_string") or "")
    return new, str(args.get("old_text") or args.get("old_string") or "")


def _norm(path: str) -> str:
    return re.sub(r"^(\./)+", "", str(path or "").strip().replace("\\", "/"))[:200]


def check_kind(command: str) -> str:
    """test | build | typecheck | lint | bench for a command that runs a check, else ''.

    The runner has to be what runs: `pip install pytest`, `pip list | grep -i pytest` and `which jest`
    mention one without checking anything (found live: they were reported as passing checks).
    """
    return _check_segment(command)[0]


def exit_masked(command: str) -> bool:
    """The check's own exit code doesn't reach the result (`|| true`, `; exit 0`, piped into `tail`)."""
    return _check_segment(command)[2]


def scope_of(command: str) -> str:
    """The command without its flags: `pytest -q -x tests` and `pytest tests -q` are the same check."""
    words = [w for w in str(command or "").split() if not w.startswith("-")]
    return " ".join(words)[:200]


def exit_code(output: str) -> Optional[int]:
    """From the terminal tool's result header; None while a command is still running."""
    match = _HEADER.match(str(output or "").lstrip())
    if not match:
        return None
    return 0 if match.group(1).lower() == "ok" else int(match.group(2) or match.group(3))


def counts(output: str) -> tuple[int, int, int]:
    """(passed, failed, errors) from the common runners' summaries; -1 for what they didn't say."""
    text = str(output or "")[-20000:]

    def num(pattern: str, where: str) -> int:
        found = re.findall(pattern, where)
        return int(found[-1]) if found else 0

    summary = re.findall(r"(?m)^[=\s]*((?:\d+ (?:passed|failed|errors?|skipped|deselected|xfailed|xpassed|warnings?)[, ]*)+)"
                         r"(?:in [\d.]+s)", text)  # pytest, with or without the === banner
    if summary:
        line = summary[-1]
        return num(r"(\d+) passed", line), num(r"(\d+) failed", line), num(r"(\d+) errors?", line)
    jest = re.findall(r"(?m)^\s*Tests:\s+(.+)$", text)
    if jest:
        return num(r"(\d+) passed", jest[-1]), num(r"(\d+) failed", jest[-1]), 0
    vitest = re.findall(r"(?m)^\s*Tests\s+(.+\(\d+\))", text)
    if vitest:
        return num(r"(\d+) passed", vitest[-1]), num(r"(\d+) failed", vitest[-1]), 0
    cargo = re.findall(r"test result: \w+\. (\d+) passed; (\d+) failed", text)
    if cargo:
        return sum(int(p) for p, _ in cargo), sum(int(f) for _, f in cargo), 0
    dotnet = re.search(r"Failed:\s*(\d+),\s*Passed:\s*(\d+)", text)
    if dotnet:
        return int(dotnet.group(2)), int(dotnet.group(1)), 0
    runner = _RUNNER_SUMMARY.findall(text)
    if runner:
        total, failed = map(int, runner[-1])
        if total >= failed:
            return total - failed, failed, 0
    ran = re.search(r"Ran (\d+) tests?", text)  # unittest
    if ran:
        bad = re.search(r"FAILED \((.*?)\)", text)
        failures = num(r"failures=(\d+)", bad.group(1)) if bad else 0
        errors = num(r"errors=(\d+)", bad.group(1)) if bad else 0
        return int(ran.group(1)) - failures - errors, failures, errors
    go_fail, go_pass = re.findall(r"--- FAIL: ", text), re.findall(r"--- PASS: ", text)
    if go_fail or go_pass:
        return len(go_pass), len(go_fail), 0
    tsc = re.search(r"Found (\d+) errors?", text)
    if tsc:
        return 0, int(tsc.group(1)), 0
    eslint = re.search(r"\d+ problems? \((\d+) errors?", text)
    if eslint:
        return 0, int(eslint.group(1)), 0
    return -1, -1, -1


def test_result_observed(command: str, output: str) -> bool:
    """A fresh test result requires a runner and evidence the command finished."""
    kind, segment, masked = _check_segment(command)
    code = exit_code(output)
    if kind != "test" or code is None or _NO_TESTS.search(output):
        return False
    passed, failed, errors = counts(output)
    if _PYTHON_TEST_RUNNER.match(segment) or masked:
        return max(passed, failed, errors) > 0
    return not (code != 0 and max(passed, failed, errors) <= 0 and ENVIRONMENT_ERROR.search(output))


def failing_names(output: str) -> list[str]:
    text = str(output or "")
    names = re.findall(r"(?m)^FAILED (\S+)", text)                          # pytest
    names += re.findall(r"(?m)^\s*(?:✕|×|✗)\s+(.+?)(?:\s+\(\d+\s*m?s\))?\s*$", text)  # jest / vitest
    names += re.findall(r"--- FAIL: (\S+)", text)                           # go
    names += re.findall(r"(?m)^test (\S+) \.\.\. FAILED", text)              # cargo
    names += re.findall(r"(?m)^(?:FAIL|ERROR): (\S+)", text)                 # unittest
    return list(dict.fromkeys(n.strip() for n in names if n.strip()))[:12]


def first_assertion(output: str) -> str:
    text = str(output or "")
    for pattern in (r"(?m)^E\s+(\S.+)$", r"(AssertionError:?.+)$", r"(Expected[^\n]{0,160}\n[^\n]{0,160}Received[^\n]{0,160})",
                    r"(error TS\d+:.+)$", r"(?m)^\s*(error(?:\[\w+\])?:.+)$"):
        match = re.search(pattern, text, re.M)
        if match:
            return " ".join(match.group(1).split())[:240]
    return ""


def measure(output: str, name: str = "") -> Optional[tuple[float, str]]:
    """A measured value: the declared metric by name, else a benchmark's mean time in ms."""
    text = str(output or "")[-20000:]
    if name:
        found = re.findall(rf"(?i)\b{re.escape(name)}\b[^\d\n-]{{0,20}}(-?\d+(?:\.\d+)?)\s*([a-zµ%/]+)?", text)
        if found:
            value, unit = found[-1]
            return float(value), unit or ""
    mean = re.search(r"(?i)Time \(mean[^)]*\):\s*([\d.]+)\s*(ns|µs|us|ms|s)\b", text)  # hyperfine
    if mean:
        return round(float(mean.group(1)) * _TIME_UNITS[mean.group(2)], 4), "ms"
    found = re.findall(r"(?i)\b(?:mean|avg|average|median|p50)\b[^\d\n]{0,12}([\d.]+)\s*(ns|µs|us|ms|s|sec)\b", text)
    if found:
        value, unit = found[-1]
        return round(float(value) * _TIME_UNITS[unit.lower()], 4), "ms"
    return None


@dataclass
class Check:
    step: int
    kind: str
    command: str
    exit_code: Optional[int]
    scope: str = ""
    passed: int = -1
    failed: int = -1
    errors: int = -1
    failing: list[str] = field(default_factory=list)
    assertion: str = ""
    metric: Optional[float] = None
    unit: str = ""
    no_tests: bool = False
    attempt: int = 0
    environment: str = ""  # the setup problem it hit (a missing package or program), if that's why it failed
    changes_at: int = 0  # file changes made before this check, to say how far back the best result is

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and self.failed <= 0 and self.errors <= 0 and not self.no_tests

    def score(self) -> tuple[int, int, int]:
        """Lower is better: passing, then fewer failures and more passes, then a run that reported nothing."""
        if self.ok:
            return (0, 0, 0)
        if self.failed < 0 and self.errors < 0:
            return (2, 0, 0)  # crashed, didn't collect, or printed no summary
        return (1, max(self.failed, 0) + max(self.errors, 0), -max(self.passed, 0))

    def line(self) -> str:
        parts = []
        if self.failed >= 0:
            parts.append(f"{self.failed} failed, {max(self.passed, 0)} passed" + (f", {self.errors} errors" if self.errors > 0 else ""))
        if self.no_tests:
            parts.append("no tests ran")
        parts.append("exit 0" if self.exit_code == 0 else f"exit {self.exit_code}")
        if self.metric is not None:
            parts.append(f"{self.metric:g} {self.unit}".strip())
        return f"{self.kind} `{self.command[:80]}`: " + ", ".join(parts)


@dataclass
class VerificationLedger:
    objective: str = ""
    criteria: list[str] = field(default_factory=list)
    check_command: str = ""
    metric_name: str = ""
    metric_direction: str = ""   # lower | higher
    metric_target: Optional[float] = None
    checks: list[Check] = field(default_factory=list)
    attempts: int = 0
    changes_since_check: int = 0
    changes: int = 0
    tamper_after_fail: list[str] = field(default_factory=list)
    blockers: list[dict[str, Any]] = field(default_factory=list)
    stalled: int = 0
    regressions: int = 0
    model_slips: int = 0
    read_misses: int = 0
    resumed: bool = False
    last_step: int = 0
    # Checks the setup blocked: after ENVIRONMENT_STOP the note says to stop fixing it and report instead.
    environment_hits: int = 0
    # Distinctive values the tests read this turn assert ({literal: test file}), and code changes that wrote one in.
    expected: dict[str, str] = field(default_factory=dict)
    special_cased: list[list[str]] = field(default_factory=list)
    # Special-cases the agent undid with checkpoint_undo ([path, literal, test]): writing them back isn't applied.
    undone_special: list[list[str]] = field(default_factory=list)
    _pending: dict[str, str] = field(default_factory=dict, repr=False)
    _undo_log: list[tuple[str, str]] = field(default_factory=list, repr=False)  # (path, literal it wrote in) per change

    # ── the objective (set_objective) ──────────────────────────────────
    def declare(self, args: dict[str, Any]) -> str:
        objective = " ".join(str(args.get("objective") or "").split())[:600]
        if not objective:
            return "Error: say what the objective is, in a sentence or two."
        self.objective = objective
        self.criteria = [" ".join(str(c).split())[:200] for c in (args.get("criteria") or []) if str(c).strip()][:8]
        self.check_command = " ".join(str(args.get("check") or "").split())[:300]
        metric = args.get("metric") or {}
        if isinstance(metric, dict) and str(metric.get("name") or "").strip():
            self.metric_name = str(metric["name"]).strip()[:60]
            self.metric_direction = "higher" if str(metric.get("direction") or "lower").lower().startswith("h") else "lower"
            try:
                self.metric_target = float(metric["target"]) if metric.get("target") not in (None, "") else None
            except (TypeError, ValueError):
                self.metric_target = None
        count = len(self.criteria)
        parts = [f"Objective recorded with {count} acceptance {'criterion' if count == 1 else 'criteria'}"]
        if self.metric_name:
            target = f", target {self.metric_target:g}" if self.metric_target is not None else ""
            parts.append(f" and the metric {self.metric_name} ({self.metric_direction} is better{target})")
        first = f"run `{self.check_command}`" if self.check_command else "run the check that measures it"
        baseline = "" if self.checks else f" Before changing anything, {first} for a baseline."
        return "".join(parts) + "." + baseline

    # ── observation (every finished tool call) ─────────────────────────
    def observe(self, name: str, args: dict[str, Any], ok: bool, output: str, step: int,
                duration_ms: int = 0, meta: Optional[dict[str, Any]] = None, call_id: str = "") -> None:
        text = str(output or "")
        head = text.lstrip()[:500]
        self.last_step = step
        if _MODEL_SLIP.search(head):
            self.model_slips += 1
            if self.model_slips >= MODEL_SLIPS:
                self._block("model", "several tool calls in a row had malformed arguments or unknown tool names")
            return
        self.model_slips = 0
        if _AUTHORIZATION.search(head):
            self._block("authorization", head.split("\n", 1)[0])
            return
        if name in _CHANGE_TOOLS:
            if ok:
                path = str(args.get("path") or args.get("destination") or args.get("file") or "")
                self._changed(path)
                if name == "checkpoint_undo":
                    self._undone()
                else:
                    self._undo_log.append((_norm(path), self._watch_special_case(name, path, args, call_id)))
                    del self._undo_log[:-40]
            return
        if name in _LOOKUP_TOOLS:
            if ok and name == "file_read" and _TEST_FILE.search(str(args.get("path") or "")):
                for literal in _expected_literals(text):
                    self.expected.setdefault(literal, str(args.get("path"))[:200])
            self.read_misses = self.read_misses + 1 if _MISS.search(head) else 0
            if self.read_misses >= READ_MISSES:
                self._block("information", f"{self.read_misses} lookups in a row found nothing")
            return
        if name not in {"terminal", "terminal_run", "process_output"}:
            return
        command, code = str(args.get("command") or args.get("cmd") or ""), exit_code(text)
        if name == "process_output":
            match = _PROCESS.match(head.split("\n", 1)[0])
            if not match or not match.group(1).lower().startswith("finished"):
                return  # still running: it hasn't checked anything yet
            command = match.group(3)
            code = 0 if "(ok)" in match.group(1).lower() else int(match.group(2)) if match.group(2) else None
        kind = check_kind(command) or (check_kind(self.check_command) if self.check_command and scope_of(command) == scope_of(self.check_command) else "")
        if not kind:
            from agent.learning.episodes import command_kind
            if code is not None and code != 0 and ENVIRONMENT_ERROR.search(text):
                self._block("environment", ENVIRONMENT_ERROR.search(text).group(0))
            elif command and code == 0 and command_kind(command) == "change":
                self._changed(command)
            return
        if code is None:
            return  # moved to the background: process_output reports it when it finishes
        if _PYTHON_TEST_RUNNER.match(_check_segment(command)[1]) and code == 0 and not any(n > 0 for n in counts(text)):
            self._block("verification", f"'{command[:80]}' reported no executed tests")
            return
        if exit_masked(command):
            # `pytest || true` exits 0 whatever happened (found live: "No module named pytest" counted as a pass).
            # The runner's own summary is still evidence; the exit code isn't.
            passed, failed, errors = counts(text)
            if passed <= 0 and failed <= 0 and errors <= 0:
                setup = ENVIRONMENT_ERROR.search(text)
                self._block("environment" if setup else "verification",
                            setup.group(0) if setup else f"`{command[:80]}` hides its exit code, so it verifies nothing")
                self._pending[call_id or command] = (
                    f"Verification: `{command[:120]}` hides the check's own exit code (`|| true`, `; exit 0` or a pipe) and "
                    "printed no test summary, so it checked nothing" + (f" — and the output shows a setup problem "
                    f"({setup.group(0)})" if setup else "") + ". Run the check on its own to see whether it passes.")
                return
            code = 1 if failed > 0 or errors > 0 else 0
        self._record(self._parse(step, kind, command, code, text), text, call_id or command)

    def _watch_special_case(self, name: str, path: str, args: dict[str, Any], call_id: str) -> str:
        """A code change that writes a test's expected value in, instead of computing it: the value, or ''.

        Every model in the five-model evaluation turned `assert now_ms() == 1700000000000` into
        `return 1700000000000` and reported "the test passes": a pass that fixes nothing (METR's reward-hacking
        reports describe the same). Like tests edited after they fail, it makes the check worthless.
        """
        if not self.expected or _TEST_FILE.search(path) or name not in {"file_write", "file_edit"}:
            return ""
        new, old = _new_old(name, args)
        key, flagged = path[:200], ""
        for literal, test in self.expected.items():
            as_value = _as_value(literal)
            if as_value.search(new) and not as_value.search(old):
                flagged = flagged or literal
                if (key, literal) not in [tuple(s[:2]) for s in self.special_cased]:
                    self.special_cased.append([key, literal, test])
                self._pending[call_id or key] = (
                    f"Verification: this change writes {literal}, the exact value {test} expects, into {path} instead of "
                    "computing it. That makes the test pass without fixing anything. Undo it and fix the real cause, or "
                    "tell the user plainly why the test can't pass as written.")
            elif not as_value.search(new) and (name == "file_write" or as_value.search(old)):
                self.special_cased = [s for s in self.special_cased if not (s[0] == key and s[1] == literal)]
        return flagged

    def _undone(self) -> None:
        """checkpoint_undo reverted the latest change. If that change wrote a test's value in, the file no longer
        does, and the agent has shown it knows the change was wrong."""
        if not self._undo_log:
            return
        path, literal = self._undo_log.pop()
        if not literal:
            return
        for entry in [s for s in self.special_cased if _norm(s[0]) == path and s[1] == literal]:
            self.special_cased.remove(entry)
        if not any(_norm(s[0]) == path and s[1] == literal for s in self.undone_special):
            self.undone_special.append([path, literal, self.expected.get(literal, "the test")])

    def undone_special_case(self, name: str, args: dict[str, Any]) -> str:
        """Why a change isn't applied: it writes back a special-case the agent itself undid in this request, or ''.

        D7 (qwen3.5:9b, 2 of 3 runs): told the edit hard-codes the test's value, it ran checkpoint_undo, made the
        identical edit again, and replied that it was a valid fix. Undo and redo only spends calls and approvals.
        """
        if not self.undone_special or name not in {"file_write", "file_edit"}:
            return ""
        path = _norm(str(args.get("path") or ""))
        new, old = _new_old(name, args)
        for undone_path, literal, test in self.undone_special:
            if undone_path == path and _as_value(literal).search(new) and not _as_value(literal).search(old):
                return (f"Not applied: this writes {literal}, the value {test} expects, back into {path}. You undid that "
                        "change after EchoSpeak pointed out it makes the test pass without fixing anything, and writing "
                        "it again fixes nothing either. Fix the real cause, or tell the user plainly why the test can't "
                        "pass as written.")
        return ""

    def _changed(self, target: str) -> None:
        self.changes += 1
        self.changes_since_check += 1
        if self.changes_since_check == UNCHECKED_CHANGES and not self.checks:
            self._block("verification", f"{UNCHECKED_CHANGES} changes and nothing has checked any of them")
        if target and _TEST_FILE.search(target):
            last_test = next((c for c in reversed(self.checks) if c.kind == "test"), None)
            if last_test and not last_test.ok and not _ABOUT_TESTS.search(self.objective) and target[:200] not in self.tamper_after_fail:
                self.tamper_after_fail.append(target[:200])

    def _block(self, kind: str, evidence: str) -> None:
        evidence = " ".join(str(evidence).split())[:240]
        if not any(b["class"] == kind and b["evidence"] == evidence for b in self.blockers):
            self.blockers.append({"class": kind, "evidence": evidence, "step": self.last_step})
            del self.blockers[:-8]

    def _parse(self, step: int, kind: str, command: str, code: int, text: str) -> Check:
        passed, failed, errors = counts(text)
        metric = measure(text, self.metric_name) if (kind == "bench" or self.metric_name) else None
        return Check(step=step, kind=kind, command=command[:300], exit_code=code, scope=scope_of(command), passed=passed,
                     failed=failed, errors=errors, failing=failing_names(text), assertion=first_assertion(text),
                     metric=metric[0] if metric else None, unit=metric[1] if metric else "",
                     no_tests=kind == "test" and bool(_NO_TESTS.search(text)), attempt=self.attempts)

    # ── progress ───────────────────────────────────────────────────────
    def _rank(self, check: Check) -> tuple[Any, ...]:
        metric = 0.0 if check.metric is None else (-check.metric if self.metric_direction == "higher" else check.metric)
        return (*check.score(), metric)

    def best(self, scope: str) -> Optional[Check]:
        pool = [c for c in self.checks if c.scope == scope]
        return min(pool, key=self._rank) if pool else None

    def _record(self, check: Check, text: str, key: str) -> None:
        changed = self.changes_since_check > 0
        if changed:
            self.attempts += 1
            check.attempt = self.attempts
        self.changes_since_check = 0
        check.changes_at = self.changes
        best = self.best(check.scope)
        baseline = next((c for c in self.checks if c.scope == check.scope), None)
        self.checks.append(check)
        del self.checks[:-60]
        setup = ENVIRONMENT_ERROR.search(text) if not check.ok and check.failed <= 0 else None
        if setup:
            check.environment = setup.group(0)
            self.environment_hits += 1
            self._block("environment", check.environment)
        if check.no_tests:
            self._block("verification", f"`{check.command[:80]}` ran no tests, so it verifies nothing")
        if best is None:
            progress = ("first run of this check, after changes, so there's no baseline from before them" if changed
                        else "first run of this check (the baseline)")
        elif self._rank(check) < self._rank(best):
            progress, self.stalled = "improved on the best result so far", 0
        elif self._rank(check) > self._rank(best):
            progress = f"worse than the best result (step {best.step}: {best.line()})"
            self.regressions += 1
            self.stalled += int(changed)
        else:
            progress = "the same as the best result so far"
            self.stalled += int(changed)
        if self.stalled >= STALL_ATTEMPTS and not check.ok:
            self._block("implementation", f"{self.stalled} attempts in a row didn't improve on the best result")
        self._pending[key] = self._feedback(check, baseline, progress, best)

    def _feedback(self, check: Check, baseline: Optional[Check], progress: str, best: Optional[Check] = None) -> str:
        when = f"attempt {check.attempt}" if check.attempt else "before any change"
        lines = [f"Verification, {when}: {check.line()}. Progress: {progress}."]
        if baseline is not None and baseline is not check and baseline is not self.best(check.scope):
            lines.append(f"Baseline: {baseline.line()}.")
        target = self._target(check)
        if target:
            lines.append(target)
        if check.failing and not check.ok:
            lines.append("Failing: " + ", ".join(check.failing[:6]) + ".")
        if check.assertion and not check.ok:
            lines.append(f"First error: {check.assertion}")
        if check.ok and not target.startswith("Target not met"):
            lines.append("This check passes. If it covers every acceptance criterion, finish and report the evidence; "
                         "if it doesn't, run the check that does.")
        elif check.environment and self.environment_hits >= ENVIRONMENT_STOP:
            # Found live: a 9B model spent 40 more steps (and searched the owner's home folder) trying to install
            # a test runner after the third of these notes.
            lines.append(f"The setup has blocked this check {self.environment_hits} times ({check.environment}). Stop working "
                         "on the environment now: don't search outside the project or try more installs. Reply to the user "
                         "with what you changed, that it isn't verified, and exactly what they need to set up to check it.")
        elif check.environment:
            lines.append(f"This is the environment, not your code ({check.environment}). Fix the setup if you're allowed to, "
                         "for example by installing into the project's own environment; otherwise stop and tell the user "
                         "exactly what to set up.")
        elif check.no_tests:
            lines.append("This check verifies nothing. Find or write the check that does before claiming anything.")
        elif self.stalled >= STALL_ATTEMPTS:
            lines.append(f"{self.stalled} attempts haven't improved on the best result, so this approach isn't working. "
                         "Re-read the failing check and the code it calls, state a different hypothesis and test it, "
                         "or tell the user what's blocking you. Don't try more variations of the same fix.")
        elif progress.startswith("worse") and best is not None:
            back = check.changes_at - best.changes_at
            lines.append(f"Your changes since step {best.step} made it worse. Keep the best result: undo or rethink them "
                         f"before going on ({back} file change{'s' if back != 1 else ''} since then; checkpoint_undo "
                         "reverts one at a time).")
        else:
            lines.append("Fix the implementation for these failures; don't change the tests to make them pass.")
        if self.tamper_after_fail:
            lines.append(f"Test files were changed after they failed ({', '.join(self.tamper_after_fail[:3])}), so passing "
                         "them doesn't count as verification unless the user asked for test changes.")
        return " ".join(lines)

    def _target(self, check: Check) -> str:
        if self.metric_target is None or check.metric is None:
            return ""
        higher = self.metric_direction == "higher"
        met = check.metric >= self.metric_target if higher else check.metric <= self.metric_target
        sign = "≥" if higher else "≤"
        return (f"Target met: {self.metric_name} {check.metric:g} {sign} {self.metric_target:g}." if met
                else f"Target not met: {self.metric_name} is {check.metric:g}, the target is {sign} {self.metric_target:g}.")

    # ── what the loop reads ────────────────────────────────────────────
    def feedback(self, key: str) -> str:
        """The note for one finished call (by tool call id), once."""
        return self._pending.pop(key, "")

    def active(self) -> bool:
        return bool(self.checks or self.blockers or self.objective or self.special_cased)

    def unfinished(self, answer: str) -> str:
        """For an answer that claims success the evidence contradicts: what to fix first (asked once)."""
        done = self.completion()
        if not DONE_CLAIM.search(answer or ""):
            return ""
        if self.special_cased:
            path, literal, test = self.special_cased[0][:3]
            return (f"Your reply says it works, but {path} now returns {literal}, the value {test} expects, instead of "
                    "computing it, so the test passing proves nothing. Undo that and fix the real cause, or say plainly "
                    "that the test can't pass as written and why.")
        if self.undone_special and done["status"] not in {"verified", "checks_pass"}:
            # D7 r1: it undid the hard-coded value, the write-back was refused, and it replied "the test passes". The
            # generic "run the tests" note below was false (nothing it wrote is left) and sent it looping on the edit.
            path, literal, test = self.undone_special[-1][:3]
            return (f"Your reply says it works, but {path} doesn't return {literal} any more: you undid that, and "
                    f"writing it back isn't applied, so {test} still fails. Don't try that edit again. Unless you "
                    "can compute the value for real, tell the user plainly that the test can't pass as written and why.")
        setup = next((c.environment for c in reversed(self.checks) if c.environment), "")
        if done["status"] == "blocked" and setup and self.changes:
            # "The test passes" when the test never ran (found live: pytest wasn't installed, and the reply said the
            # hard-coded fix was verified). Blocked used to let the claim stand with only a note under it.
            return (f"Your reply says it works, but nothing has verified it: the check couldn't run ({setup}). Don't say "
                    "it passes. Say plainly that it isn't verified, what you changed, and what the user needs to set up "
                    "to run the check.")
        if not self.checks and self.changes and TESTS_PASS_CLAIM.search(answer or ""):
            # "The test passes" with no test runner run since the change (found live: `python test_clock.py`, which only
            # defines the test, cleared the check step and the claim stood).
            return ("Your reply says the test passes, but no test runner has run since your change (running a test file "
                    "directly usually only defines the tests). Run the tests with the project's runner, or say plainly "
                    "that the change isn't verified.")
        if done["status"] not in {"failing", "unverified", "partial"} or not self.checks:
            return ""
        if done["status"] == "failing":
            what = f"the last check failed ({done['last']})"
        elif done["status"] == "partial":
            what = f"it's only partly verified ({done['detail']})"
        else:
            what = done["detail"]
        return (f"Your reply says the work is done, but the evidence doesn't show that: {what}. Fix it and run the check "
                "again, or say plainly in your reply what isn't verified yet.")

    def completion(self) -> dict[str, Any]:
        """The evidence-based status of the work, and the record behind it."""
        primary = None
        if self.check_command:
            primary = next((c for c in reversed(self.checks) if c.scope == scope_of(self.check_command)), None)
        primary = primary or (self.checks[-1] if self.checks else None)
        bottleneck = self.blockers[-1]["class"] if self.blockers else ""
        latest = {c.scope: c for c in self.checks}
        detail = primary.line() if primary else ""
        if primary is None:
            status = "blocked" if any(b["class"] in {"environment", "authorization"} for b in self.blockers) else "unverified"
            detail = "nothing checked the work" if self.changes else ""
        elif self.tamper_after_fail:
            status, detail = "unverified", "tests were changed after they failed"
        elif self.special_cased:
            status, detail = "unverified", f"the code returns the test's expected value ({self.special_cased[0][1]}) directly"
        elif self.changes_since_check:
            status, detail = "unverified", f"{self.changes_since_check} change(s) after the last check"
        elif primary.ok:
            failing = [c for c in latest.values() if not c.ok]
            target = self._target(primary)
            if target.startswith("Target not met"):
                status, detail = "partial", target
            elif failing:
                status, detail = "partial", f"{failing[-1].line()} still fails"
            else:
                status = "verified" if self.objective else "checks_pass"
        else:
            refused = any(b["class"] == "authorization" and b.get("step", 0) >= primary.step for b in self.blockers)
            status = "blocked" if primary.environment or refused else "failing"
        first = next((c for c in self.checks if primary is not None and c.scope == primary.scope), None)
        return {
            "status": status,
            "detail": detail,
            "objective": self.objective,
            "criteria": list(self.criteria),
            "metric": ({"name": self.metric_name, "direction": self.metric_direction, "target": self.metric_target}
                       if self.metric_name else {}),
            "checks": len(self.checks),
            "attempts": self.attempts,
            "changes": self.changes,
            "last": primary.line() if primary else "",
            "baseline": {scope: next(c for c in self.checks if c.scope == scope).line() for scope in latest},
            "best": {scope: best.line() for scope in latest if (best := self.best(scope))},
            "regressions": self.regressions,
            "stalled": self.stalled,
            "bottleneck": bottleneck,
            "blockers": self.blockers[-4:],
            "tamper_after_fail": list(self.tamper_after_fail),
            "resumed": self.resumed,
            # Started from a failing check and ended passing: the work recovered, not just ran.
            "started_failing": bool(first is not None and not first.ok),
            "recovered": bool(first is not None and not first.ok and status in {"verified", "checks_pass"}),
        }

    def summary_note(self) -> str:
        """One line under the answer: what the evidence says about completion. Empty when nothing was checked."""
        if not self.checks and not self.blockers and not (self.objective and self.changes):
            return ""
        done = self.completion()
        detail = done["detail"]
        if done["status"] == "blocked" and self.blockers:
            detail = f"{self.blockers[-1]['class']}: {self.blockers[-1]['evidence']}"
        if done["status"] in {"failing", "partial"} and self.checks:
            last = self.checks[-1]
            best = self.best(last.scope)
            if best is not None and best is not last and self._rank(best) < self._rank(last):
                detail += f" · best was step {best.step}: {best.line()}"  # never hide a better verified result
        if done["attempts"]:
            detail += f" · {done['attempts']} attempt{'s' if done['attempts'] != 1 else ''}"
        return STATUS_LABELS[done["status"]] + (f" — {detail}" if detail else "") + "."

    # ── persistence across interruptions ──────────────────────────────
    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("_pending", None)
        data.pop("_undo_log", None)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VerificationLedger":
        known = {k: v for k, v in dict(data or {}).items() if k in cls.__dataclass_fields__ and not k.startswith("_")}
        known["checks"] = [Check(**{k: v for k, v in c.items() if k in Check.__dataclass_fields__})
                           for c in known.get("checks", []) if isinstance(c, dict)]
        return cls(**known)


def restore(evidence: list[Any]) -> Optional[VerificationLedger]:
    """The ledger saved with an earlier attempt's evidence (coordination work rows), if any."""
    for row in reversed(list(evidence or [])):
        if isinstance(row, dict) and isinstance(row.get("verification"), dict) and row["verification"].get("ledger"):
            try:
                ledger = VerificationLedger.from_dict(row["verification"]["ledger"])
            except (TypeError, ValueError):
                return None
            ledger.resumed = True
            return ledger
    return None


def resume_brief(ledger: VerificationLedger) -> str:
    """What the earlier attempt measured, for the system prompt of a resumed one."""
    done = ledger.completion()
    lines = ["Verification from the earlier attempt (measured by EchoSpeak from what ran, not from its reply):"]
    if done["objective"]:
        lines.append(f"- Objective: {done['objective']}")
    lines += [f"- Criterion: {c}" for c in done["criteria"][:6]]
    if ledger.check_command:
        lines.append(f"- Check: `{ledger.check_command}`")
    lines.append(f"- Status: {STATUS_LABELS[done['status']]}" + (f" ({done['detail']})" if done["detail"] else ""))
    lines += [f"- Baseline: {line}" for line in list(done["baseline"].values())[:3]]
    lines += [f"- Best so far: {line}" for line in list(done["best"].values())[:3]]
    if done["bottleneck"]:
        lines.append(f"- Bottleneck: {done['bottleneck']} ({ledger.blockers[-1]['evidence']})")
    lines.append("Start by re-running the check to see where things stand now; don't redo work that already passed.")
    return "\n".join(lines)


OBJECTIVE_SCHEMA = {
    "type": "object",
    "properties": {
        "objective": {"type": "string", "description": "The outcome the user wants, in one or two sentences."},
        "criteria": {"type": "array", "items": {"type": "string"}, "description": "Acceptance criteria you can check (2-6)."},
        "check": {"type": "string", "description": "The command that checks them, e.g. `pytest -q tests/test_parser.py`."},
        "metric": {"type": "object", "description": "Only for measurable improvement work.", "properties": {
            "name": {"type": "string", "description": "As the check prints it, e.g. 'mean' or 'p50'."},
            "direction": {"type": "string", "enum": ["lower", "higher"]},
            "target": {"type": "number"}}},
    },
    "required": ["objective", "criteria"],
}


def objective_tool(ledger: VerificationLedger):
    """set_objective: what success means, recorded before substantial engineering work."""
    from agent.lean.toolbox import NativeTool

    return NativeTool(
        name="set_objective",
        description=("For substantial engineering work (a bug fix, a feature, a refactor, an optimisation), record what "
                     "success means before you start: verifiable acceptance criteria, the command that checks them, and "
                     "optionally a metric to improve with a direction and target. EchoSpeak then measures every check "
                     "against it and tells you about progress, regressions and blockers. Skip it for questions and "
                     "one-line changes."),
        parameters=OBJECTIVE_SCHEMA,
        func=ledger.declare,
        parallel_safe=True,
        always=True,
    )
