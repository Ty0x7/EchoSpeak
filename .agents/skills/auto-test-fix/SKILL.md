---
name: auto-test-fix
description: Run bounded, evidence-gated engineering cycles on EchoSpeak — test the harness with a real model, judge, diagnose the failing layer, let an engineering agent (Claude Code, Codex, or any CLI agent) fix it with a failing-first test, retest dev and held-out cases, and keep only what the gates prove. Use when the owner asks for autonomous improvement cycles; never as a side effect of other work.
---

# auto-test-fix

The repeatable loop around `model-harness-testing` (read that skill first: its cases, judge, scoring and
diagnosis rules all apply). One entry point, `apps/backend/scripts/auto_test_fix.py`, runs it without
re-prompting. Design and evidence: `docs/research/auto-test-fix.md`.

**Cycle:** Discover → Baseline → Execute → Capture → Verify → Judge → Diagnose → Fix → Retest → Preserve → Repeat.

## Two agents, never confused

- **System under test:** EchoSpeak's harness running `--model` (qwen3.5:9b is the reference). It works only in
  disposable eval projects through `harness_eval.py`'s isolated backend. Its output is evidence, never authority.
- **Engineering agent:** `--driver claude | codex | command | manual`. It edits one disposable worktree on branch
  `auto/<session>` with scoped tools and limits. `command` takes any CLI (`--driver-cmd "tool --brief {brief}
  --cwd {tree}"`), so EchoSpeak itself can take this role later on a strong enough model. `manual` writes the brief
  and pauses; make the fix yourself, then `run --resume`.

## Run

```bash
cd apps/backend
.venv/bin/python scripts/auto_test_fix.py discover                 # what it would work on; changes nothing
.venv/bin/python scripts/auto_test_fix.py run --driver claude \
    --cases E1,E2,E3 --heldout EH1,EH2 --repeat 2 --cycles 2 --minutes 180 --driver-budget 5
.venv/bin/python scripts/auto_test_fix.py status                   # progress (latest session)
.venv/bin/python scripts/auto_test_fix.py stop                     # finish the current stage, then stop
.venv/bin/python scripts/auto_test_fix.py review                   # decisions, evidence, commits
```

Before `run`, do the model-harness-testing machine checks (memory, `ollama ps`, no other evaluation). Tell the
owner the expected duration: a cycle with two repetitions of three dev and two held-out cases takes about
20–40 minutes plus the engineering agent's time.

## What the loop decides (not the engineering agent)

A fix is committed on the cycle branch only if every gate passes:

1. **Scope:** only `ALLOWED` paths; never the cases, judge, recorder, campaign runner, this loop, skills, AGENTS.md,
   CLAUDE.md or conftest.
2. **Security:** changing a security boundary (sandbox, policy, approvals, file access, secrets, privacy,
   credentials, auth) **halts** for the owner; model names in code are rejected; secret-shaped strings halt.
3. **Failing first:** a new or changed `tests/test_*.py` fails on the cycle's base commit (copied onto a
   temporary worktree of it) and passes with the fix.
4. **Regression:** related suites pass; the full backend suite has no failure the session's base didn't have.
5. **Measurement:** the dev mean improves by more than 0.5 (noise) with no new critical finding, and the
   held-out cases (never shown to the engineering agent) don't fall more than 1.0 or gain a critical finding.

Results: `accept` (commit), `reject` / `inconclusive` / `no_change` (worktree reset; diff kept in the cycle
folder), `halt` (left uncommitted in the worktree for the owner).

**Stops:** cycle and minute budgets, `stop`, nothing below `--target`, two rejected cycles in a row, a security
halt, `needs_authorization` from the engineering agent, an engineering agent that stops without its report
(`driver_failed`, e.g. a usage limit), under 2 GB free memory (`ATF_MIN_FREE_GB`), or a broken run.

## Zero API billing (hard rule)

- **No paid API call, ever.** The engineering agent and the judge run only through the owner's subscriptions
  (`claude`, `codex`), or locally (`ollama:<model>`). `command` drivers and judges are disabled.
- **Sessions check the logins and stop.** A session refuses to start unless `claude auth status` / `codex login
  status` show the subscription. It stops, rather than falling back, on a usage limit or on any fallback credit.
- **The system under test is local only:** a localhost model server, with paid-provider keys stripped.
- **`cost_usd` / `--max-cost-usd` are estimates** of subscription usage at API list prices, not charges.
- See `docs/research/agentic-evaluation.md` §10. Never add an override.

## Optional AI judge and budgets

- **`--ai-judge claude:haiku`** (or `codex`, `ollama:<model>`; never a paid API) adds Layer 4 to every evaluation in the
  session.
  - Cited findings go into the engineering agent's brief.
  - A retest that passes the gates but is flagged for a suspicious process ends as **`review`**: committed on the
    session branch, and the loop stops for the owner.
  - The judge never changes a score. Calibrate it first: `harness_ai_judge.py calibrate`.
- **Budgets:**
  - `--max-cost-usd`: engineering agent plus judge; default $15.
  - `--max-eval-tokens`: model-server tokens.
  - Spend is in `state.json` under `spent`.

## Before keeping a session's commits

The gates only see the session's dev and held-out cases. `review` prints a sweep command for every D, H and E
case, comparing the session base against the session branch. Run it, then repeat each case that flipped four
times on both arms before calling it a regression or noise. In the first demonstration, a single-run sweep
flipped four cases:
- two came from code paths the fixes don't touch (model variance);
- two needed the four-repeat confirmation.

## Never

- Push, merge, publish, or touch `~/Projects/echospeak`, the owner's runtime data or other projects. The only
  owner data read is an aggregate of recent failed tool runs (tool name and first error line, paths redacted).
- Grant permissions, change credentials, or loosen a gate to let a fix through.
- Show held-out failures to the engineering agent, or edit cases or the judge to fit a model.

## Where things go

- Session: `~/Work/echospeak-model-testing-2026/auto-test-fix/<stamp>-<label>/` — `state.json`, `ledger.jsonl`,
  `report.md`, `runs/` (judged eval runs), `cycle-N/` (`brief.md`, `diagnosis.json`, `driver.log`,
  `agent-report.json`, `gate.json`, `fix.diff`, `report.md`).
- Code: worktree `~/Work/echospeak-auto-<session>`, branch `auto/<session>`. Review, then cherry-pick what you keep.
- Obsidian: `EchoSpeak/Evaluations/<date> auto-test-fix <session>.md`, plus one line in note 27.
