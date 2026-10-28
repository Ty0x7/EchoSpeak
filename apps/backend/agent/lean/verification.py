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
_CHANGE_TOOLS = {"file_write", "file_edit", "file_delete", "file_move", "file_copy", "self_edit", "checkpoint_undo"}
_LOOKUP_TOOLS = {"file_read", "file_find", "file_search", "file_list", "self_read", "self_grep", "web_search", "safe_web_fetch"}
_TEST_FILE = re.compile(r"(?i)(^|[\\/])(tests?|__tests__|spec)[\\/]|(^|[\\/])(test_[^\\/]+|[^\\/]+_test|[^\\/]+\.(test|spec))\.[a-z]+$|"
                        r"(^|[\\/])(conftest\.py|pytest\.ini|jest\.config\.[a-z]+|vitest\.config\.[a-z]+)$|\.github[\\/]workflows")
_ABOUT_TESTS = re.compile(r"(?i)\b(tests?|spec|testing|coverage|fixtures?|ci|workflow)\b")
# The setup, not the code: a missing package or program, permissions, disk, network, a service that isn't up.
# A missing module with a dotted or relative name ('app.utils', './utils') is usually the code's own, so it's left out.
ENVIRONMENT_ERROR = re.compile(
    r"(?i)(No module named '[^'.]+'|command not found|is not recognized as an internal or external command|"
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
_TIME_UNITS = {"ns": 1e-6, "µs": 1e-3, "us": 1e-3, "ms": 1.0, "s": 1000.0, "sec": 1000.0}
STATUS_LABELS = {"verified": "Verified", "checks_pass": "Checks pass", "partial": "Partly verified",
                 "failing": "Not verified: the last check failed", "unverified": "Not verified", "blocked": "Blocked"}


def check_kind(command: str) -> str:
    """test | build | typecheck | lint | bench for a command that verifies something, else ''."""
    for kind, pattern in _KINDS:
        if pattern.search(command or ""):
            return kind
    return ""


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
    _pending: dict[str, str] = field(default_factory=dict, repr=False)

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
                self._changed(str(args.get("path") or args.get("destination") or args.get("file") or ""))
            return
        if name in _LOOKUP_TOOLS:
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
        self._record(self._parse(step, kind, command, code, text), text, call_id or command)

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
        return bool(self.checks or self.blockers or self.objective)

    def unfinished(self, answer: str) -> str:
        """For an answer that claims success the evidence contradicts: what to fix first (asked once)."""
        done = self.completion()
        if done["status"] not in {"failing", "unverified", "partial"} or not self.checks or not DONE_CLAIM.search(answer or ""):
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
