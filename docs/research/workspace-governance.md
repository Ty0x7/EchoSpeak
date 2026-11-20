# EchoSpeak workspace and recovery audit, 20 November 2026

At the start of issue #119, the default branch on GitHub and the clean main checkout both pointed to `1ffa211`. The Linux installation uses application source `e3f8c4c`; the intervening commit changed six documentation files. The terminal-reported backend was running 11.15.0. The [release ledger](integration-11.15.md) tracks the engineering decisions. [The shared workflow](../engineering-prompt.md) governs future efforts.

## What can be restored

- **Remote history.** Before 11.15 consolidation there were 96 names targeting 94 distinct commits. Ten of the names were release backup branches. The 95 non-main names were retired; main advanced normally. In a new empty bare repo, fetched main and the annotated archive tag directly from origin. The tag's manifest matched the independently saved pre-deletion listing byte for byte. Every listed tip passed an ancestry check against the tag, and `git fsck --full` passed. All 26 pre-audit local branch heads are ancestors of main or the archive. Nine local branch heads are outside main's ancestry. Their measured or superseded changes are classified in the release ledger.
- **Secondary Git copy.** The existing pre-release bundle passed `git bundle verify`; its local branch heads were fetched and the restored object store passed fsck again. This bundle predates the final integration and stash. The three files in stash `dcd42cac` matched the separate backup copies. The 29 dirty files in the five-model tree matched both their backup and the blobs in integrated commit `6228e0d`, including an untracked PNG. Retain the stash and private file backup. Keep the existing archive tag and bundle; no additional permanent Git backup was created.
- **Private user data.** The separate pre-release `runtime-data.tgz` passed gzip integrity. In a private temporary directory we restored the SQLite state database with its WAL, ran `PRAGMA integrity_check` (ok), and loaded the archived JSON for threads, agents, memory, settings and both projects. The restored data had 16 conversations, 88 executions, 173 items, 17 thread states and 366 tool runs. This was a sampled recovery test. Full credential recovery, externally attached project directories and off-device disaster recovery were outside this check. Runtime archives can contain credentials and must stay outside Git.

To inspect or recover a historical tip without changing the live checkout, use an **empty scratch repository**. Use the original branch's full ref in the lookup. A recovery branch can be created locally there if needed.

```sh
git init --bare recovery.git
git -C recovery.git fetch https://github.com/Ty0x7/EchoSpeak.git refs/tags/archive/pre-11.15-branches-20261120:refs/tags/archive/pre-11.15-branches-20261120
git -C recovery.git show archive/pre-11.15-branches-20261120:branches.tsv
git -C recovery.git fsck --full
```

## Workspace inventory and proposed disposition

Snapshot before the governance worktree. The 16 registered locations comprise main, one under the project's Claude worktrees directory and fourteen directly under `~/Work`. The release ledger lists their full names and source decisions. Sizes below refer to ignored backend data at audit time. A clean source tree can still contain the only copy of a measurement.

| Location or branch (each entry represents one registered tree unless grouped) | State and dependencies | Proposed disposition |
|---|---|---|
| Main project checkout | Clean, installed backend/TUI source; main includes the release | Keep active |
| `.claude/worktrees/conversation-audit` | Merged release branch; untracked web dependency symlink; 164 MB of evidence including release judge reports | Retain until evidence and link target are accounted for |
| `echospeak-five-model-harness` | Merged base, 29 verified working files, 1.8 GB data; shared Python environment used by other trees and release packaging | Retain until dependency migration and a build verification |
| `echospeak-env-intelligence`, `echospeak-search-recall`, `echospeak-connected-apps` | Three clean merged trees, 318/56/54 MB | Compare each with retained evaluation evidence |
| `echospeak-app-budget`, `echospeak-apps-note`, `echospeak-findtools-candidate` | Three clean superseded candidates, 14/26/41 MB, exact tips recoverable from tag | Candidates after evidence check and owner approval |
| `echospeak-campaign-base` | Clean, historical base, 37 MB | Same |
| Six `echospeak-auto-*` trees | Claude demo 161 MB; Codex demo 8.4 MB; agentic demo 33 MB; campaigns 1/2/3 hold 182/36/28 MB. Codex demo tip is in main; the other tips are in the archive | Compare each with its session ledger and retained runs |
| Governance worktree created for #119 | Scoped code, tests and documentation for this PR | Retire only this newly created tree after merge and checks |

The eight controller state records showed six stopped sessions and two awaiting review. None reported running. The independent support folders `echospeak-audit`, `echospeak-fastfetch`, and `echospeak-model-testing-2026` hold audit tools, branding assets, and private evidence/backups respectively. The `~/Work/echospeak` symlink resolves to the main project. Preserve these locations and the existing vault.

**Future layout.** Keep the main checkout under Projects. Create new effort worktrees under `~/Work/echospeak-worktrees/`; retain prior session paths on resume. Keep campaign logs under the existing model-testing directory and knowledge links in the EchoSpeak vault. Close an unsuccessful experiment by recording its result in the issue, preserving unique artifacts in the evidence directory and verifying the copy before retirement. The owner reviews the enumerated legacy paths under [issue #120](https://github.com/Ty0x7/EchoSpeak/issues/120). No legacy deletion is part of #119.

## Governance checks

On Codex CLI 0.171.0, `codex debug prompt-input` contained the new AGENTS instruction. A fresh read-only ChatGPT subscription session opened the canonical workflow and cited its retirement criterion. On Claude Code 2.1.329, an outbound-disabled network namespace with a loopback HTTP recorder captured the real client request. It contained the CLAUDE entry point and imported workflow text, including the acceptance, V4 and explicit-approval clauses. The recorder returned an error without invoking a model. These checks establish instruction discovery on these client versions; future agent decisions still require review.

The 20-system review already covers the full harness. This change affects project instructions, execution state, verification, approvals, orchestration and lifecycle (systems 2, 6, 10, 16, 17 and 20). The controller keeps the evaluation gates, separates local observation from the outer session's GitHub publication, and stops with dirty edits preserved on unsuccessful cycles. The existing Linux application remains installed. Remaining release measurements have separate issues for [final verification on LT1](https://github.com/Ty0x7/EchoSpeak/issues/121) and [multi-module completion](https://github.com/Ty0x7/EchoSpeak/issues/122).

Two targeted regressions failed before the fix. Afterward the controller and zero-billing suites passed 27/27. A separate disposable Git rehearsal resumed a manual cycle, rejected an out-of-scope binary, retained its bytes and base commit, refused a second resume, and printed a trace-free issue summary. No live evaluation campaign or application restart was needed.
