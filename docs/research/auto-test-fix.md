# auto-test-fix: an autonomous, gated engineering loop for EchoSpeak (17 November 2026)

Entry point: `apps/backend/scripts/auto_test_fix.py`. The skill, for Claude Code and Codex, is
`.agents/skills/auto-test-fix/SKILL.md`. Its tests are in `apps/backend/tests/test_auto_test_fix.py`. Visual summary:
`artifacts/research/auto-test-fix.html`. Built on the model-harness-testing skill: the same cases, judge, scoring
(completion 30, tools 25, efficiency 20, recovery 15, security 10), recorder and V0–V4 records.

## 1. What it is for

The owner wants EchoSpeak to improve through repeated engineering cycles without being re-prompted each time.
The cycle is: test, capture evidence, diagnose, fix, retest, judge, document, repeat.

Per the owner's clarification of 17 November, **Claude Code and Codex are the engineering agents**. EchoSpeak, with
Qwen 3.5 9B as the reference model, is **the system being tested**. The workflow must not depend on a particular
agent or model, so that EchoSpeak itself can take the engineering role later on a stronger model.

## 2. Roles

| | System under test | Engineering agent | The loop (this script) |
|---|---|---|---|
| Who | EchoSpeak's harness with `--model` (qwen3.5:9b) | `--driver claude` · `codex` · `command` (any CLI, later EchoSpeak) · `manual` | Deterministic Python |
| Works in | Disposable eval projects, through harness_eval's isolated backend (own data folder, auth on, recorder) | One disposable git worktree, `~/Work/echospeak-auto-<session>`, branch `auto/<session>` | The session folder and the worktree's git |
| Can change | Nothing in EchoSpeak | Allowed source paths in its worktree | Commits accepted cycles on the session branch |
| Authority | None: its output is evidence | None: its report is a claim the gates check | Accepts only what the gates prove; never pushes or merges |

What each engineering agent can do:
- **Claude:** `claude -p --permission-mode acceptEdits`, with `Read/Edit/Write/Glob/Grep`, plus `pytest`,
  `git diff/status/log` and `ls` in Bash. Web access, push and commit are denied. It has `--max-budget-usd` and runs
  without session persistence.
- **Codex:** `codex exec -s workspace-write -C <worktree>`. That means edits only in the worktree, with no network,
  under Codex's own bubblewrap sandbox.
- **Either one:** a time limit (`--driver-minutes`), and a parent session's environment markers are stripped.

## 3. One cycle

**Discover → Baseline → Execute → Capture → Verify → Judge → Diagnose → Fix → Retest → Preserve → Repeat**

1. **Session start, once:**
   - the full backend suite on the base commit records which tests already fail;
   - the held-out cases are run and judged. They are never shown to the engineering agent.
2. **Baseline:** run and judge the dev cases on the worktree's current commit, with `--repeat` repetitions.
3. **Diagnose (deterministic):**
   - every run below `--target`, with a critical finding, or with a failed outcome gets a failing layer, classified
     from the evidence: provider (truncation, request errors), permissions (declined content read, refused instead
     of asked), tools (malformed calls, tool errors), verification (harness status against independent tests),
     harness (unaccounted usage, hard stops), context (prompt near the window), and otherwise model;
   - the diagnosis includes the tool and approval trail and the reply;
   - **field evidence** from the owner's EchoSpeak is added: failed tool runs over the last three days, aggregated
     to the tool name and first error line, with paths redacted, and with no messages or arguments.
4. **Fix:** the brief (`cycle-N/brief.md`) gives the engineering agent the failures, its rules and a JSON report
   format. Its status is `fixed`, `no_change` or `needs_authorization`.
5. **Verify (the gates, in order):**

   | Gate | Rejects or halts when |
   |---|---|
   | Scope | A path outside `ALLOWED`, or any of the cases, judge, recorder, campaign runner, this loop, skills, AGENTS.md, CLAUDE.md or conftest (reject) |
   | Secrets | A secret-shaped string in added lines (halt) |
   | Test exists | No new or changed `tests/test_*.py` (reject) |
   | Failing first | The new test, copied onto a temporary worktree of the base commit, passes there (reject: it proves nothing), or fails with the fix (reject) |
   | Related suites | Any failure (reject) |
   | Full suite | A failure the session's base didn't have (reject) |
   | Model names | A model family name in added code (reject: fix behaviours, not models) |
   | Security boundary | Any change to the sandbox, policy, approvals, file access, secrets, privacy, credentials or auth (**halt for the owner**) |

6. **Retest:**
   - re-run and re-judge the same dev cases, then the held-out cases;
   - **accept** when the dev mean rises by more than 0.5 (half the measured noise of three repetitions), with no
     new critical finding, and the held-out mean is no more than 1.0 below its baseline with no more critical
     findings;
   - otherwise **reject** or **inconclusive**.
7. **Preserve:**
   - an accepted cycle is committed on the session branch, with the evidence in the message;
   - a rejected, inconclusive or no-change cycle is reset, and its diff is kept in `cycle-N/fix.diff`;
   - a halt leaves the change uncommitted in the worktree for the owner;
   - every cycle writes `cycle-N/report.md` and a `ledger.jsonl` row. The session writes `report.md`, an Obsidian
     note and one line in note 27.

**Stop conditions:**
- `--cycles` or `--minutes` used up;
- `stop` (a STOP file; the current stage finishes first);
- every dev case at `--target`;
- two rejected or inconclusive cycles in a row;
- a security halt, or `needs_authorization` from the engineering agent;
- under 3 GB of free memory;
- a broken run or gate.

## 4. Commands

```bash
cd apps/backend
.venv/bin/python scripts/auto_test_fix.py discover
.venv/bin/python scripts/auto_test_fix.py run --driver claude --cases E1,E2,E3 --heldout EH1,EH2 \
    --repeat 2 --cycles 2 --minutes 180 --driver-minutes 30 --driver-budget 5 --target 9.0 --label demo
.venv/bin/python scripts/auto_test_fix.py status      # or --session <dir>
.venv/bin/python scripts/auto_test_fix.py stop
.venv/bin/python scripts/auto_test_fix.py review
# Any other agent, including EchoSpeak later:
.venv/bin/python scripts/auto_test_fix.py run --driver command --driver-cmd "my-agent --brief {brief} --cwd {tree}"
# A human or an interactive agent:
.venv/bin/python scripts/auto_test_fix.py run --driver manual ...   # writes the brief and pauses
.venv/bin/python scripts/auto_test_fix.py run --resume              # continues at the gates
```

Output goes to `~/Work/echospeak-model-testing-2026/auto-test-fix/<stamp>-<label>/`. Code goes to the worktree
`~/Work/echospeak-auto-<stamp>-<label>`, branch `auto/<stamp>-<label>`. To keep a fix, review it with `review`,
then cherry-pick. To discard everything, run `git worktree remove` and `git branch -D`.

## 5. Relation to what existed

Reused unchanged: `harness_eval.py`, which got only the environment cases and the `attach` flag in Phase 1;
`harness_judge.py`, which got Phase 1's generic environment checks; the recorder; the campaign conventions; and the
diagnosis taxonomy and rules of the model-harness-testing skill.

New: the orchestration, the gates, the driver adapters, the field-evidence reader and the session records. No
second judge, case set or recorder was written.

## 6. Demonstrated cycles

_Filled after the demonstration sessions, below._
