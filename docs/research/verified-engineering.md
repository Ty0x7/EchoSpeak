# Verification-driven engineering, hill climbing and evidence-based learning

EchoSpeak 11.11.5 connects one workflow — Objective → Research → Environment → Verification → Adaptation → Completion — to the harness that already existed. It adds no new engine.

- **Echo Native** (Agents and Code) now measures engineering work against its objective. Checks are parsed from what actually ran. Every check's result tells the model about progress, regressions, stalls and the bottleneck. Completion is computed from evidence and shown under the answer.
- **Learning** now records why an episode turned out the way it did. Blockers that weren't the strategy's doing no longer count against lessons or agents. The owner's feedback reclassifies an episode instead of only flipping it to a loss.
- **Evaluation** reports verified completion, false success, recovery and stalls, and compares each lesson with its random holdout.

[Visual report](../../artifacts/research/verified-engineering.html) · [Reusable prompt template](../engineering-prompt.md) · builds on [the Chat live-test audit](chat-live-audit.md) (11.11.4).

## 1. What EchoSpeak already had

Audited from source on `claude/verified-engineering` (stacked on 11.11.4, PR #108; #103–#107 below it).

| Stage | What existed | Where | Gap found |
|---|---|---|---|
| Objective | The request text as the turn's `goal`, restated every few steps (`recall.reminder`). The Job board for teams | `agent/lean/loop.py`, `agent/lean/job.py` | **Missing.** There were no acceptance criteria, no check command and no metric. "Done" meant the model stopped calling tools |
| Research | Search engine, source router, notebooks, citations (11.11.3) | `agent/search_rank.py`, `agent/source_router.py` | Sound. Chat-focused; nothing needed here for engineering |
| Environment | Terminal with sandbox or host, `failure_hint` after non-zero exits, approvals, policy | `agent/lean/terminal.py`, `agent/lean/policy.py` | **Poorly connected.** A missing package looked like any other failure, and learning counted it against the agent |
| Verification | `check_nudge`: code changed and nothing ran since, so ask once to run it. V0–V4 ladder after the run | `loop.py`, `agent/learning/episodes.py` | **Weak during the run.** A *failing* test cleared the nudge ("something ran, pass or fail"), so an agent could run a failing test and then say "all tests pass" with no correction. Test output was never parsed |
| Adaptation | Fail-streak note after 3 failures of one tool; refusal circuit breaker; screen-stall notes | `loop.py` | **Missing for code.** No baseline, no best result, no regression or stall detection, no structured failure record |
| Completion | `complete_task`, `max_steps`, claim guard; work rows with `verification: "observed"/"unverified"` | `loop.py`, `agent/coordination.py` | **Too coarse.** "observed" meant any tool had succeeded |
| Long-running | Durable work rows, attempts, budgets, cancellation, `checkpoint_undo`, resume with the prior report | `agent/coordination.py`, `runtime.py` | Resume carried the earlier report text, not what was measured |
| Learning | Episodes, reflection, curator, playbook with random holdouts, lifecycle, competence profiles, routing | `agent/learning/*` | **Attribution gap.** An environment blocker, a tool outage or a misunderstood request counted as a loss for every lesson used, which is negative transfer that retires good lessons. Owner feedback always queued a reflection, even for a one-off misunderstanding |
| Evaluation | Per-lesson holdout comparison on the Learning page | `playbook.lifecycle`, `LearningPage.tsx` | No verified-completion, false-success or recovery rates |

Unnecessary: a separate verifier model, a new orchestration engine, or a second memory store. Deterministic evidence from the run covers what's needed. External harnesses (Codex, Claude Code, ACP) were left alone.

## 2. Research findings

| Approach | Status | What EchoSpeak takes from it |
|---|---|---|
| ReAct: reason, act, observe in one loop ([Yao et al. 2022](https://arxiv.org/abs/2210.03629)) | Production-proven; every coding agent runs this loop | The Lean loop already is one. Feedback goes into the observation, not a separate call |
| Test-driven repair loops (SWE-bench agents, [Jimenez et al. 2023](https://arxiv.org/abs/2310.06770)) | Production-proven | Real test output is the signal. Parse it: exit code, counts, failing tests, first assertion |
| Verbal feedback across attempts (Reflexion, [Shinn et al. 2023](https://arxiv.org/abs/2303.11366); Self-Refine, [Madaan et al. 2023](https://arxiv.org/abs/2303.17651)) | Works when the feedback is grounded; benchmark-proven | Feed back *evidence* (expected vs actual, attempt history), not "reflect on your answer" |
| Self-correction without external feedback ([Huang et al. 2023](https://arxiv.org/abs/2310.01798)) | Shown **not** to help reliably | No "review yourself" pass. Corrections come from checks, never from the model grading itself |
| Experience and insight memory (ExpeL, [Zhao et al. 2023](https://arxiv.org/abs/2308.10144); Agent Workflow Memory, [Wang et al. 2024](https://arxiv.org/abs/2409.07429); Voyager's skill library, [Wang et al. 2023](https://arxiv.org/abs/2305.16291)) | Experimental. Gains on benchmarks; sensitive to retrieval and negative transfer | Lessons stay advisory, unproven until checked tasks go better than holdouts, and are retired when worse |
| Evaluator-driven search (AlphaEvolve, [Novikov et al. 2025](https://arxiv.org/abs/2506.13131)) | Production use at Google, with strong automated evaluators | Hill climbing needs a measurable target, so the metric has a direction and target, and the best result is kept |
| Self-modifying agents (Darwin Gödel Machine, [Zhang et al. 2025](https://arxiv.org/abs/2505.22954)) | Experimental. Reported the agent gaming its own evaluation | Checks must be protected: tests edited after they failed don't count |
| Reward hacking in frontier models ([METR, June 2025](https://metr.org/blog/2025-06-05-recent-reward-hacking/)) | Observed in practice: special-casing tests, editing scorers | Tamper-after-fail detection, plus the prompt rule "fix the code, not the tests" |
| Repository instructions and skills ([AGENTS.md](https://agents.md); Claude Code's `CLAUDE.md` and skills; [Anthropic, *Building effective agents*](https://www.anthropic.com/engineering/building-effective-agents)) | Production convention | The "what goes where" split in the [prompt template](../engineering-prompt.md) |

## 3. Design: one ledger, plugged into what exists

Every stage maps onto an existing part. The only new runtime piece is a deterministic per-turn ledger (`agent/lean/verification.py`) that watches tool calls.

| Shared concept (Part D) | Where it lives now |
|---|---|
| Objective and acceptance criteria | `set_objective` → `VerificationLedger.objective / criteria / check_command / metric_*`. Optional; Echo uses it for substantial work |
| Environment readiness and blockers | `ENVIRONMENT_ERROR` on check and command output, `Check.environment`, `blockers[]` |
| Execution attempts | `attempts`: changes between checks. `changes_at` on each check |
| Verification evidence | `Check`: kind, scope, exit code, passed/failed/errors, failing names, first assertion, metric |
| Failure classification | Bottleneck classes during the run. `Episode.cause` (`classify_cause`) afterwards |
| Adaptation decisions | Feedback note on each check's result: improve, revert to the best, change hypothesis, fix the setup or stop |
| Completion status | `completion()`: verified · checks pass · partial · failing · unverified · blocked. Shown under the answer, on `TurnResult.verification`, in the work row and execution metadata |
| Learning attribution | `Episode.verification`, `cause`, `feedback_cause`. `store.NOT_ATTRIBUTABLE` / `LOSS_CAUSES` drive `failed`, `verified_success`, `playbook.verdict` and `profiles._verdict` |

**Chat stays light:** no ledger and no `set_objective` (`chat_run` turns are excluded). **Read-only planning turns, guests and turns without a terminal** are excluded too. The ledger makes no model calls and adds nothing to a turn that runs no checks.

## 4. Echo Native: what happens during a run

### Measuring checks

The loop calls `verifier.observe(...)` for every finished call (`LeanTurn._tool_finished`):

- **Terminal results** are classified from the command: test, build, typecheck, lint, bench, or a check declared by `set_objective`.
- **The exit code** comes from the terminal's own header (`[ok …`, `[exit code N …`, `[exited with code N …`), or from `process_output`'s "finished …" for background runs. A command still running checks nothing.
- **Counts** are parsed for pytest, jest, vitest, cargo, go, unittest, dotnet, tsc and eslint. The ledger also takes failing test names, the first assertion and a metric: a declared metric by name, or a benchmark's mean, with hyperfine's units converted to ms.
- **Changes** are file tools, or terminal commands that `command_kind` classes as changes. Changes to test files after a failing test are flagged.

Runs are grouped by **scope** (the command without its flags), so `pytest -q tests/a.py` and `pytest tests/a.py -q` are the same check, while a full run and one file are not.

### Feedback on the check's own result

The note is appended to that tool result as an `<echospeak-note>`. It never becomes a second message or a second generation, and the 11.11.4 framing rules apply. An example from the tests:

> Verification, before any change: test `pytest -q tests/test_dates.py`: 1 failed, 4 passed, exit 1. Progress: first run of this check (the baseline). Failing: tests/test_dates.py::test_leap_year. First error: assert 28 == 29 Fix the implementation for these failures; don't change the tests to make them pass.

Later checks say whether they improved on the best result so far or were worse than it (naming the best step and how many file changes ago it was), and show the baseline. They also report the target status and the bottleneck with its next action.

### The hill-climbing loop and its bounds

Baseline → attempt → verify → compare with the best (by score, then metric) → adapt:

- **Improved:** the stall counter resets.
- **Worse:** a regression. The note says how many file changes back the best result is (`checkpoint_undo` reverts one at a time, with approval).
- **No improvement for 3 attempts** (`STALL_ATTEMPTS`): an implementation bottleneck. The note asks for a different hypothesis or a report of the blocker, not more variations.
- **Hard bounds stay as they were:** the step budget, work budgets (tokens and seconds), owner cancellation and the refusal circuit breaker. The ledger adds no loop of its own.

### Completion and false success

`completion()` decides from evidence:

- **Unverified:** changes after the last check, or tests edited after they failed.
- **Failing:** the declared check (or the last check) fails.
- **Partly verified:** the declared check passes, but another check's latest run fails, or the metric target isn't met.
- **Blocked:** the check failed for environment reasons, or an authorization refusal came after it.
- **Verified** (with an objective) or **checks pass** (without one).

If the answer claims success ("all tests pass", "fixed it") while the status is failing, unverified or partial, the loop sends the evidence back **once** (`verification_nudge`, like the existing `check_nudge`). If the claim stands, the note under the answer says what the evidence shows. The best verified result is never hidden: a failing final state also reports "best was step N: …".

### Long-running work and resume

When the run has a work record, the ledger is saved whole in the work's evidence: objective, criteria, checks, best results and blockers. The work's `verification` field shows the measured status ("checks pass", "not verified: the last check failed", …), and the Work panel already displays it. A resumed attempt (`work_id`) restores the ledger for that agent (`verification.restore`), adds a brief to the system prompt (objective, status, baseline, best so far, bottleneck, "re-run the check first"), and measures new checks against the earlier baseline.

### Streaming and lifecycle

- Feedback rides on tool results the loop already sends. Model calls are unchanged: the scenario-1 test asserts exactly 3 calls for write → test → answer.
- The only added generation is the single evidence nudge for a contradicted success claim. It's bounded to one, and the scenario-7 test asserts 4 calls total.
- The completion note uses the existing `answer_note` event and persisted note items. The web client renders it under the answer. The per-check `verification` and `verification_nudge` events are ignored by the UI, like `check_nudge`.

## 5. Bottleneck investigation

| Bottleneck | Detected from | What the agent is told | When it stops |
|---|---|---|---|
| **Environment** | A missing package or program, permissions, disk, network or services (`ENVIRONMENT_ERROR`) on a failed check with no test failures, or on a failed command. `No module named 'app.utils'` and `Cannot find module './x'` count as the code's own problem | "This is the environment, not your code (…). Fix the setup if you're allowed to … otherwise stop and tell the user exactly what to set up." | Blocked; not counted against lessons or the agent |
| **Authorization** | Refused, held or switched-off results | Existing refusal-streak notes; status blocked | The existing circuit breaker (10 refusals) |
| **Verification** | `no tests ran` / `collected 0 items`, or 8 changes with no check at all | "This check verifies nothing. Find or write the check that does." | Unverified unless a real check passes |
| **Information** | 4 lookups in a row found nothing (`No matches`, `not found`) | Recorded as the bottleneck; the existing fail-streak note applies | `information_gap`, which isn't counted against lessons |
| **Implementation** | 3 attempts without improving on the best result, or regressions | "This approach isn't working. Re-read the failing check … state a different hypothesis … or tell the user." | Failing or stalled, which *is* counted |
| **Model** | 3 malformed or unknown tool calls in a row | Recorded; the existing invalid-argument notes apply | Shown in the record. Routing already learns model fit from episodes |

The order of response is: try the safe, authorized fix first (install into the project's own environment), then ask the owner for exactly what's missing. The prompt rule and the environment note both say so. Nothing new asks the owner for information the run already has.

## 6. Learning: attribution from evidence

**Causes** (`episodes.classify_cause`), from outcome, stop reasons, tool rows and the ledger's record:

- **Counted as a win:** `effective`, a checked success.
- **Counted as losses** (`LOSS_CAUSES`):
  - `failed_check` (checks still fail)
  - `false_success` (said done, but the evidence disagrees)
  - `action_failed`
  - `stalled`
  - `verifier_gap` and `owner_failed` (from feedback)
- **Never counted** (`NOT_ATTRIBUTABLE`): `environment`, `authorization`, `tool_failure` (the tool broke, not the agent's command), `information_gap`, `model_error`, `misunderstood`.
- **Neither:** `unverified` and `partial`.

**Effects:**

- `Episode.failed` and `verified_success` respect causes. `playbook.verdict` gives lessons no loss for a blocker. `profiles._verdict` gives agents none.
- `false_success` is counted in profiles.
- A failing final check caps the V-ladder grade at V1, and so does a test edited after it failed.
- Reflection is skipped for blockers and misunderstandings ("the strategy itself wasn't tested").
- The reflector is told the cause in plain words.

**Owner feedback reclassifies** the episode (`feedback_cause`) instead of only flipping it to a loss:

- "Didn't work" on a checked episode is a **verifier gap**: the checks passed but missed something. That's the brief's example, "The file was generated, but it doesn't open in Excel". It counts as a loss and is worth a reflection about better checks.
- An explicit misunderstanding ("you misunderstood", "not what I asked", "I meant…") is **misunderstood**. Lessons aren't blamed, and no reflection runs.
- An environment explanation ("my machine was offline") is **environment**.
- Anything else is **owner_failed**.
- Clearing the feedback restores the automatic cause.

The note never becomes an instruction by itself. It only reaches a reflection, whose lesson still starts unproven and must beat its holdout. "Wrong file" alone stays a loss: it could equally be a failed strategy, and excusing it would shield bad lessons (an existing test pins this).

**Measuring whether learning works** (`agent/learning/evaluation.py`, `GET /lean/learning/evaluation?agent_id=&days=30`):

- **Outcome rates:**
  - verified completion
  - false success: of the replies that claimed success, those the evidence or the owner contradicted
  - recovery: started failing, ended passing
  - stall rate
  - attempts to verified
  - tokens and seconds per task, and per verified task
  - causes and bottlenecks
- **Lesson effects:** each lesson's checked win rate with it vs without it (its random holdout), with the verdict *helps*, *no measurable effect*, *negative transfer* or *not enough holdout data*.
- **With vs without:** an aggregate of tasks that read lessons against tasks whose lessons were all held back.

The Learning page shows each experience's cause and verification status, labels blockers "Blocked by the setup" and says they don't count for or against any lesson.

The existing holdout is what separates a lesson that helped from **success unrelated to the strategy**: a task that would have gone well anyway does so for the holdout too, so the lesson gains no relative advantage. This is deliberately a comparison, not a per-episode guess.

## 7. Verification

These checks ran on this branch. No paid models were used, and only tests for the changed components ran.

- **New: `apps/backend/tests/test_verified_engineering.py` (20 tests).** They use the real loop and runtime with a scripted model and a scripted terminal, and cover all ten scenarios:
  1. A simple task: exactly 3 model calls, no nudge, nothing erased, "Checks pass" note, cause `effective`, the work row shows "checks pass".
  2. A failed test: the model sees counts, the failing test, the first assertion and the next action. The retry shows the improvement and the baseline. Recovery is recorded.
  3. An optimisation: the target is reported until met, a faster but wrong result is only partly verified, and hyperfine units are converted.
  4. Several failed approaches, then verified: attempts are counted and recovery is recorded.
  5. An environment blocker: blocked status and the setup note, an internal missing module counts as code, an unknown command is caught, and lessons aren't charged.
  6. A stall after 3 flat attempts, regression detection, and "N file changes since then".
  7. A false success: sent back once, then noted. The record shows `false_success`, the profile counts it, and tests edited after failing don't count unless the task is about tests.
  8. Resume: evidence round-trips through the work row. The resume brief carries the objective, baseline and next step, and the next check compares with the earlier baseline.
  9. A lesson judged against its holdout. Verified success credits it, and a misunderstanding takes the blame back.
  10. Negative transfer detected and retired. Blockers excluded from the with/without comparison.

  Also: `set_objective` inside the grant of work created before the turn (routines), feedback reclassification and clearing, evidence-based causes, evaluation rates, Chat getting no workflow while Echo Native does, no feedback for turns without checks, and parsing real runner output.
- **Re-run and passing** (existing tests for the changed loop, runtime, prompt and learning code):
  - `test_learning`, `test_routing`, `test_swarm`
  - `test_chat_lifecycle`, `test_check_step`
  - `test_lean_runtime`, `test_lean_harness`, `test_lean_group`
  - `test_environments`, `test_execution_evidence`
  - `test_capabilities`, `test_screen_progress`, `test_ask_and_recover`, `test_chat_paths`

  One existing expectation caught a regression during development: "Didn't work: wrong file" must remain a loss. The misunderstanding rule was narrowed.
- **Web:** `LearningPage.test.tsx` (new case: blocker and false-success labels, verification text), `liveReducer.test.ts`, and `tsc --noEmit`.

**Needs the owner's real-model test** (Gemma or another local model, in Agents or Code with a project attached):

1. "Fix the failing test in <project>" with a known failing test. Expect a baseline run, a fix and a re-run, then **"Checks pass — …"** (or **Verified**, if it called `set_objective`) under the answer.
2. Ask for a fix where the test needs a missing package. Expect the setup to be named, and either an allowed install or a clear stop. The note says **Blocked — environment: …**.
3. A task the model can't finish in a few tries. Expect a change of approach after "attempts haven't improved", or an honest report. No "all tests pass" over a red run.
4. Interrupt long work, then resume it from Activity. Expect it to re-run the check first and continue from the earlier best.
5. On the Learning page, open an experience from these runs: cause and verification text. Mark a checked one "Didn't work: it doesn't open in Excel". Expect a verifier gap, and a reflection queued.

Whether a given model *uses* `set_objective` well, and how often it reaches verified, are model properties to measure with the evaluation endpoint over real use.

## 8. Additional findings

- **A failing check used to satisfy the "check before done" nudge.** `_unchecked` was cleared by any terminal run, pass or fail. That is now covered by the evidence nudge.
- **Blockers counted as losses everywhere,** in lessons, competence profiles and routing. A broken venv could retire a good lesson and steer routing away from a capable model. Fixed through `NOT_ATTRIBUTABLE`.
- **Owner feedback always queued a reflection,** so a one-off misunderstanding could become a strategy lesson. It's now skipped for misunderstandings and blockers.
- **Tool rows keep 240 characters of output.** Failure classes read from rows alone are lossy. The ledger's structured record is now kept on the episode for measured work.
- **The work row's `verification` field said "observed" whenever any tool had succeeded.** It now carries the measured status when checks ran.
- **Background test runs** (`process_output` "finished with exit code N: npm test") are now parsed as checks. Before, they were only "run" or nothing.

## 9. Limits and next steps

- **Parsing is lexical.** It covers the common runners; others count by exit code only. Scope grouping ignores flags, so `pytest -k x` and `pytest` are the same scope.
- **A declared metric is read by name from the check's output.** Benchmarks that print several values with the same label use the last one.
- **`checkpoint_undo` reverts one change at a time,** with approval. The ledger points to the best state but doesn't restore it, and terminal-made changes aren't checkpointed.
- **Research workers (swarm) and Chat aren't measured.** Group discussions measure each agent separately.
- **The evaluation endpoint isn't on the Learning page yet;** the page shows per-experience causes and the existing holdout comparison. A small "outcomes" panel is a natural follow-up once real data exists.
- **External harnesses** (Codex, Claude Code, ACP) keep their own verification. Sharing their test results through Echo Code's session records is possible later through supported interfaces only.
