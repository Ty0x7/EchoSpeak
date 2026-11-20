# Integration ledger: EchoSpeak 11.15.0 (19–20 November 2026)

**What this is.** Every branch, worktree and uncommitted change found on 19 November, what became of it in 11.15.0,
and why. One authoritative `main` replaces the stack of unmerged lines the owner had been testing from.

**Before.** `main` = `origin/main` = `d4cea98` (11.14.0 plus PR #118). The owner's running backend (`:38551`) ran from
`~/Work/echospeak-five-model-harness` (`agent-first-sessions` 9c0c208 + Codex's 29 uncommitted files); the
`echospeak` command ran the TUI from `~/Work/echospeak-env-intelligence`; the AppImage was 11.14.1 from 9c0c208.
Nothing after `agent-first-sessions` was live in the backend.

**Backup first.** `~/Work/echospeak-model-testing-2026/backups/20261119-release/`: a verified bundle of all 143 refs,
copies of every uncommitted file, and the owner's runtime data (conversations, memory, projects, settings).

## 1. Branches

| Branch | State on 19 Nov | Decision | Why |
|---|---|---|---|
| `context-recall-integration` / `context-memory` (0f31370) | local, 64 ahead of main | **Integrated** (the base of the release line) | Memory ×4 51/56 → 60/60; auto-continue verified live; contains `env-intelligence`, `tui-polish`, `tui-refine`, `search-recall-candidate`, `agent-first-sessions`, `five-model-harness`, `echo-identity-tui` |
| `conversation-audit-1119` (a9a12f6) | local | **Integrated** | Continuation fix measured live (turn 2 inspects first 4/4 vs 2/4); eval fixes (D8 setup, honest test runner); a reverted change recorded |
| `connected-apps-candidate` (383e2c0) | local, owner review | **Integrated** (merge `a41b77b`) | Six-arm comparison X1/XH1 ×8: 3/8+3/8 → 8/8+8/8; capability set 8.88 → 9.04. Supersedes `findtools-`, `app-budget-` and `apps-note-candidate` (same fixes, earlier versions) |
| D7 ledger fixes (`bb24aab`, `ffa7c35` on the campaign line; `fb8e6b4`/`cc234aa` on `demo-claude`) | owner review | **Integrated** (cherry-picked `-x`: `0a52622`, `1a87fe1`) | H7 held-out 1/4 → 4/4, D7 no worse (campaign ×4) |
| W1 false-success fix `82b0812` (`campaign-2`) | owner review | **Integrated** (cherry-picked `-x`: `6a07343`) | W1 3/4 → 4/4, critical findings 1 → 0 (campaign ×4) |
| `find_tools` app-name fix `480a5b4` / `313d72e` / `a7ba430` | owner review | Superseded by the version in `connected-apps-candidate` | Same fix, later and measured with clean trials |
| `campaign-base-20261118`, `auto/…campaign-1/-2/-3` | local | Their unique commits integrated as above; the rest was already in the line (patch-equivalent) | `git cherry` shows nothing else unique |
| `auto/…demo-codex-2` | local | Already contained | — |
| `origin/main` merge #118 | remote | Merged (`e6b7825`) | — |
| `origin/codex/10.3-capabilities`, `10.3.1-cloud-models`, `website-10.3-polish` | remote, Oct | Nothing to do | Patch-equivalent commits are already in main |
| All other `origin/claude/*`, `origin/codex/*`, release branches | remote | Already contained | Ancestors of the release line |

## 2. Uncommitted work

| Where | What | Decision |
|---|---|---|
| `~/Work/echospeak-five-model-harness` | 29 files (Codex's deep-integration pass) | Byte-identical to commit `6228e0d`, already in the line. Copied to the backup; left untouched |
| `~/Projects/echospeak` (main checkout) | `AGENTS.md`, `CLAUDE.md`, `docs/engineering-prompt.md` | The first two equal the line; the third is the older 12-loop text (the line has 13). Copied to the backup before the checkout moved to the release |

## 3. Finished during integration

- **The 22 long-standing backend failures (`76e5295`).** None was a product bug:
  - 17 tested the external Codex/Claude Code/ACP features, which only the legacy "environments" mode runs, under
    the "unified" default (since `aefc279`, 16 Nov). They now set that mode, and a new test pins the unified
    behaviour.
  - 1 kept a rule deliberately removed in `52071b8` (30 Oct).
  - 1 had no project context.
  - 1 depended on the repo's location (a `.claude` folder).
  - Backend suite: **1,977 passed, 0 failed** (was 1,938 passed, 22 failed).
- **Python environment (`4d9c63c`).** The sandbox's `python3` (system 3.14) has no pytest or pip, while `pip` and
  `pytest` on the PATH belong to mise's 3.12. Reproduced in the real sandbox. "No module named pytest" now says
  which Python and the route that works: the project's `.venv` if it has one, else a bare `pytest` (verified to
  run in the sandbox) or a new venv.
- **Working room at 16K (`8a41566`, then `41b5e8b`).** About 9K of a 16K window is the system prompt (16.7K chars)
  and 22 tool schemas (14K chars). At the old fill of 0.8 a turn had ~2K tokens for its work, compacted every few
  calls, and never fixed a project whose bugs span modules: PJ1/PJH1 0/6 across both arms of the conversation audit.
  - At 0.9, on the integrated build: PJ1 r1 3/3, held-out PJH1 r1 3/3, PJ1 r2 1/3.
  - Compactions 12–20 → 4–9 per run; peak prompts ≤ 13.3K; no truncation.
  - PJH1 r2 was stopped by the memory guard.
  - Windows up to 32K now fill to 0.9; larger ones keep 0.8; `LEAN_CONTEXT_FILL` overrides.
- **Terminal open items from `tui-refine`:**
  - `fde1462`: a failed step reads "Couldn't write notes.md", not "Writing notes.md".
  - `1701bb7`: errors show their message, never raw JSON.
  - `4f0dd1d`: "← sessions" stays in the footer down to 64 columns (was 110).
- **Eval fixes (`b79293b`):**
  - D8 had crashed on setup since `e11a1f5` (a shadowed `_FILLER`).
  - The plain test runner printed "0 failed" for tests it never ran.

## 4. Verification

Claude's integration checks passed 1,977 backend tests and 4,291 terminal tests. The subsequent context-default check passed 126 tests. The session, command and settings labs passed 14/14, 34/34 and 13/13. The web suite passed 245/245 across 50 files and TypeScript exited 0.

Codex inspected all five completed local Qwen 3.5 9B judge reports at revision `41b5e8b` with a 16,384-token window. Each case ran once. The reports are under the release worktree's `apps/backend/data/evals/harness/`.

| Report | Cases successful | Score / 10 |
|---|---:|---:|
| v-dev | 8/8 | 9.46 |
| v-cap | 6/6 | 9.69 |
| v-caph | 3/3 | 9.68 |
| v-long | 3/3 | 9.52 |
| v-lt1 | 1/1 | 7.60 |

All five judges recorded zero critical findings. The long task used 34 calls. The independent rerun passed all ten tests. The saved trace also shows two successful test runs after the last edit. Echo and the judge failed to recognize the custom runner, so the historical report recorded the result as unverified. [Trace follow-up](https://github.com/Ty0x7/EchoSpeak/issues/121)

## 5. Retirement candidates (nothing deleted)

Every branch below is either contained in `main` or superseded by a version in it (§1). Their worktrees have no
uncommitted work, except `five-model-harness`, whose 29 files equal `6228e0d` and are in the backup.

**Before removing any of these worktrees, archive its `apps/backend/data/`.** It holds the untracked evaluation runs
(8 MB to 1.8 GB each) that the reports cite.

| Worktree | Branch | Data to archive | Safe to retire because |
|---|---|---|---|
| `~/Work/echospeak-app-budget` | `app-budget-candidate` | 14 MB | Superseded by `connected-apps-candidate` (merged) |
| `~/Work/echospeak-apps-note` | `apps-note-candidate` | 26 MB | Same |
| `~/Work/echospeak-findtools-candidate` | `findtools-candidate` | 41 MB | Same |
| `~/Work/echospeak-connected-apps` | `connected-apps-candidate` | 54 MB | Merged (`a41b77b`) |
| `~/Work/echospeak-auto-20261117-194230-demo-claude` | `auto/…demo-claude` | 161 MB | D7 fixes integrated (patch-equivalent cherry-picks) |
| `~/Work/echospeak-auto-20261117-220454-demo-codex-2` | `auto/…demo-codex-2` | 8 MB | Contained in `main` |
| `~/Work/echospeak-auto-20261118-083210-agentic-demo` | `auto/…agentic-demo` | 33 MB | find_tools fix superseded |
| `~/Work/echospeak-auto-20261118-095256-campaign-1`, `-161927-campaign-3` | `auto/…campaign-1/-3` | 182 MB, 28 MB | Unique commits integrated |
| `~/Work/echospeak-auto-20261118-101243-campaign-2` | `auto/…campaign-2` | 36 MB | W1 fix integrated |
| `~/Work/echospeak-campaign-base` | `campaign-base-20261118` | 37 MB | Patch-equivalent in `main` |
| `~/Work/echospeak-search-recall` | `search-recall-candidate` | 56 MB | Contained in `main` |
| `~/Work/echospeak-env-intelligence` | `context-recall-integration` | 318 MB | Contained in `main`. The `echospeak` command no longer points here after the install |
| `~/Work/echospeak-five-model-harness` | `agent-first-sessions` (+29 uncommitted = `6228e0d`) | 1.8 GB | Contained in `main`. The shared backend no longer runs from here after the install. Also holds the 18 GB Tauri build cache |
| `~/Projects/echospeak/.claude/worktrees/conversation-audit` | `release-11.15` | 109 MB | Merged into `main` |

Local branches contained in `main` after the release, deletable with `git branch -d` (which refuses anything unmerged):

- `agent-first-sessions`, `context-memory`, `context-recall-integration`, `conversation-audit-1119`, `release-11.15`;
- `env-intelligence`, `tui-polish`, `tui-refine`, `search-recall-candidate`, `connected-apps-candidate`;
- `echo-identity-tui`, `five-model-harness`, `harness-first-rearchitecture`, `release-11.14.0`, `tui-responsive`.

The superseded candidates (`findtools-`, `app-budget-`, `apps-note-candidate`, `auto/*`, `campaign-base-20261118`) are
not ancestors of `main`, so removing them needs `-D`. That is safe only because the backup bundle keeps them.

## Remote preservation

Ten branch tips were pushed atomically under `backup/20261120/`: the three superseded candidate branches, `campaign-base-20261118`, and all six `auto/*` branches. The uncommitted five-model checkout remains in place.

The remote now has one branch, `main`, and zero open PRs. One annotated tag, `archive/pre-11.15-branches-20261120`, preserves all 96 branch names at consolidation and all 94 distinct target commits. Codex fetched the tag into an empty repository and verified every tip before retiring 95 remote names in one atomic push. Local worktrees, evaluation data and the pre-release bundle remain available.

For a historical branch lookup, run `git show archive/pre-11.15-branches-20261120:branches.tsv` after fetching the tag. The first column is the full commit, suitable for `git show` or restoring a branch. The production build comes from `main`.

## Installed Linux baseline

The installed TUI and Linux AppImage use application commit `e3f8c4c`. The shared backend runs from the main checkout. Its authenticated endpoint and the desktop attachment log both reported 11.15.0. The installed TUI displayed 11.15.0, opened its status panel and exited cleanly in a real pseudo-terminal. The desktop opened a mapped EchoSpeak window. The packaged backend passed its self-check.

Before the restart there were zero active turns, active streams and tracked processes. After startup, the 16 conversations, two projects, three agents, memory index and settings were present. Fingerprints matched for eight saved files and the database contents of 88 executions, 173 items and 366 tool runs. The 17 thread-state records matched apart from one refreshed timestamp.

Core readiness passed with local Ollama `qwen3.5:9b`. Typed memory is available. The optional embedding model is absent, document retrieval reported unavailable, and the legacy Calendar and Spotify connections reported unavailable.

Installed AppImage SHA-256: `91369061938091b33ce865da9f1c2ffc197608a8fac21a196aed3ada4ae8582e`. The previous desktop image and terminal launcher are retained with the suffix `.before-11.15.0`.
