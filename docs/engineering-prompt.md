# Engineering task prompts: Objective → Research → Environment → Verification → Adaptation → Completion

A compact convention for giving substantial engineering work to Claude Code, Codex or Echo Native. It says what success means and how it will be measured. It doesn't say how to implement it. The six stages are a checklist for the prompt, not six model calls: each agent runs them inside its own loop. The [verified-engineering report](research/verified-engineering.md) explains how Echo Native measures them.

**Hill climbing, in one line:** define the target, measure a baseline, try an improvement, verify it, keep the best result, and stop when the target is met or progress stops. It is a disciplined way to measure progress and know when to stop or ask. It doesn't promise the optimum, and it isn't "repeat until it works".

## What goes where

| Layer | Put here | Don't put here | In this repo |
|---|---|---|---|
| **Repository instructions**: loaded every session, always true | Build and test commands, conventions, boundaries ("never merge or publish"), test policy ("targeted tests only"), environment facts | Task goals, one-off decisions, long procedures | `CLAUDE.md` (Claude Code), `AGENTS.md` (Codex), project instructions (Echo, via `home.instructions_section`) |
| **Skills**: loaded when a class of task needs them | Reusable procedures with their own checks: documentation, releases, reviews, a deploy | Facts every task needs (those belong in the repository instructions); this task's goal | `.agents/skills/*`, Claude Code skills, Echo's approved agent skills |
| **Task prompt**: this one task | The objective, acceptance criteria, the check, constraints, stop conditions | Repeating repository instructions; step-by-step implementation | The template below |

Keep repository instructions short: every line is paid for on every request, and a long file buries the rules that matter. If a rule only applies to one kind of task, it belongs in a skill.

## The template

Copy it, delete the sections that don't apply, and keep each section to a few lines.

```text
OBJECTIVE
What should be true when this is done, in user terms: <outcome>.
Acceptance criteria (verifiable):
- <criterion 1>
- <criterion 2>
Needs human judgment (not checkable by a command): <e.g. visual polish, wording> - or "none".

RESEARCH
Read first: <files, docs, PRs, prior reports>. Prefer primary sources and the actual code over assumptions.
Depth: <light | substantial>, in proportion to the task.

ENVIRONMENT
Available: <runtimes, test tools, services, credentials that already exist>.
Not available / not allowed: <paid services, new permissions, network, merging, publishing>.
If something essential is missing, say exactly what and stop. Don't work around a permission.

VERIFICATION
Check: <the command(s) that decide success, e.g. `pytest -q tests/test_parser.py`>.
Baseline first: run the check before changing anything and record the result.
Metric (optional, for improvement work): <name> <lower|higher> is better, target <value>.
Correctness guard: <the tests that must keep passing while the metric improves>.
Never weaken a check, edit tests to pass, or redefine the objective to fit a result,
unless the task is to change the tests.

ADAPTATION
After each attempt, compare against the best result so far. Use the evidence (expected vs actual,
failing tests, errors), not a guess. If it got worse, return to the best state. If several attempts
haven't improved, change hypothesis or report the blocker. Don't retry variations of the same fix.

COMPLETION
Stop when the check passes (and the target is met), when the owner approves a judgment call,
or when a blocker needs the owner. Report:
- what changed, and where
- verification evidence (commands and their results)
- what isn't verified, and remaining uncertainty
- blockers, and what would unblock them
Partial completion is reported as partial.
```

### Small tasks: three lines

> Fix the off-by-one in `paginate()` (`api/pages.py`). Done when `pytest -q tests/test_pages.py` passes; don't touch other tests. Report the diff and the test result.

If the objective, the check and the stop condition fit in one sentence, the full template is overhead.

## Per harness

The template is the same everywhere. Each harness keeps its own loop, tools, models and approvals.

- **Claude Code.** Repository rules live in `CLAUDE.md`, and procedures in skills. Put the template in the task message. Plan mode suits the Research stage of large work. Claude Code runs the checks with its own tools. Ask for targeted tests by name; the repository instructions already say not to run full suites.
- **Codex.** Repository rules live in `AGENTS.md`. The template goes in the task, and the Environment section should state the sandbox and approval mode the run has. Codex's own runtime executes. EchoSpeak doesn't change it.
- **Echo Native (Agents and Code).** The Objective and Verification sections map onto `set_objective` (objective, criteria, check, metric). EchoSpeak then measures every check. It sends back progress against the baseline and the best result, the failing tests and first error, regressions and stalls, and the bottleneck class, attached to each check's result. Completion is computed from that evidence and shown under the answer: verified, checks pass, partly verified, failing, not verified or blocked. Echo calls `set_objective` itself for substantial work; you can also give the template in the message.

## Example: an optimisation with a correctness guard

```text
OBJECTIVE   Make `parse_log()` faster on large files without changing its output.
            Criteria: mean time on bench/large.log under 100 ms; all parser tests pass.
RESEARCH    Read parser/log.py and its tests; check how the file is read (I/O vs regex).
ENVIRONMENT Python 3.12 venv in .venv; hyperfine installed; no network needed.
VERIFICATION Baseline: `hyperfine "python bench.py"` and `pytest -q tests/test_log.py`.
            Metric: mean, lower is better, target 100 (ms). Guard: tests/test_log.py stays green.
ADAPTATION  Keep the fastest version that passes the tests. If a change breaks the tests, undo it first.
COMPLETION  Report baseline vs final timing, the test result, and what was tried and dropped.
            If 100 ms isn't reachable, report the best verified result and why.
```

## Anti-patterns

- **"Keep going until it works."** There's no measure and no stop. Give a check and a stop condition.
- **Prescribing the implementation.** It closes off better solutions. Say what must be true; leave the how to the agent.
- **Vague success** ("make it better"). Name a check, or say the outcome needs the owner's judgment.
- **Letting the check move.** Changing the tests, the benchmark input or the objective to fit a result is reward hacking.
- **Optimising only against known fixtures.** Include a check the agent didn't write, or an independent one such as real data, a build, or the owner's eyes.
- **Hiding partial work.** "Done" with failing checks is worse than "3 of 4 criteria verified; the fourth is blocked by X".
