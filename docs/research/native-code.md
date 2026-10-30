# Native Echo Code: its own environment on the shared harness

> **Current 11.13.0 continuation, 30 October:** upward-opening bottom shelves, runtime-derived model IDs, subscription observations, actionable Git admission and native local/API models. [Implementation and live checks](#13-ui-and-runtime-reliability-continuation). Earlier sections retain historical evidence; the local-only Code policy is superseded.

EchoSpeak 11.11.8 makes native Echo Code a coding environment instead of an Agents conversation. Before, a native Code session ran as **Echo the agent**, with Echo's persona, team, delegation, soul editing and agent-work tools, and Echo's own configured model. Now the model you picked answers *as itself, as a coding assistant*, with an engineering workflow, engineering tools, project and git context, and 11.11.5's verification. Chat, Code and Agents still share one runtime, loop, toolbox, ledger, approvals and learning. [Visual comparison](../../artifacts/research/native-code.html).

## 1. Audit: where Code inherited Agents

| Area | Before | Problem |
|---|---|---|
| Routing (`agent/environments.py`) | `model_first` was true only for Chat; everything else, including native Code, went to the Agents path | Code answered as **Echo**: persona, soul, "your team" roster, delegation |
| Model | Echo's persona model when Echo has one configured; otherwise the conversation's | The model picked in the Code composer could be overridden by Echo's own setting |
| Tools | Echo's full toolbox: reminders, media generation, live-data cards, shopping, desktop control, agent work and notes, `soul_update`, `delegate_to_agent`, research workers | Noise and wrong affordances for engineering; small models choose worse from long lists |
| Prompt | Echo's soul, rich-answer rules (charts, cards), a team section, past chats from all environments | Nothing about the engineering workflow; git rules only when the request mentioned git |
| Activity | A command that exited non-zero kept its present-tense label ("Running `pytest`") | A failing test run looked unfinished in the timeline |
| Learning | Episodes credited to Echo | Code's engineering experience mixed into Echo's agent record |
| Provider rule | Historical local-only Code policy (28 Oct) | Superseded by the 30 October brief: native Code supports configured local/API models; subscription harnesses stay independent (§13) |
| External runtimes | Codex, Claude Code and ACP through `agent/echo_code.py` sessions | Correct, and untouched |

## 2. Research: how coding harnesses work, and what Echo takes

| Practice | Claude Code / Codex | Echo Code now |
|---|---|---|
| **The model drives a tool loop** | One agent: read, edit, run, iterate until done | The same Lean loop, with the selected model as the answerer (`code` persona) and no agent identity |
| **Project instructions** | `CLAUDE.md` / `AGENTS.md`, nearest file wins ([Claude Code tips](https://support.claude.com/en/articles/14554000-claude-code-power-user-tips), [Codex](https://learn.chatgpt.com/docs/customization/overview)) | Project instructions (`home.instructions_section`), the project overview, and git status in every Code prompt |
| **Precise edits** | Exact-string replace, plus new-file writes | `file_edit` (exact text replace) and `file_write`, checkpointed for `checkpoint_undo` |
| **Terminal, tests, git** | Run tests and builds; inspect with git status and diff; don't commit unless asked | Terminal with Echo's permissions; git rules always on in a repo; "don't commit, push, rebase or discard unless asked" |
| **Verification** | Run the check after changing code; report honestly | 11.11.5's ledger: parsed checks, progress against the baseline, stalls, a false-success nudge, and completion under the answer |
| **Observable activity** | A timeline of tool calls (read, edit, ran, output) | Lean tool steps: "Read", "Edited", "Wrote", "Ran `cmd`" with "Tests: 5 passed" / "Exit code 1", plus the verification note |
| **Context management** | Compaction, progressive skill loading | The loop's compaction and saved long outputs (`read_output`). On local models, engineering tools up front and the rest through `find_tools` (`CODE_FOCUS`) |
| **Long work and recovery** | Resumable sessions, checkpoints | Work records, resume with the saved ledger (11.11.5), checkpoints, steering and cancellation, all unchanged |
| **Skills and corrections that compound** | Skills; corrections recorded in instruction files | Approved skills; Code learns as its own learner and can use proven coding lessons from Echo (measured transfer, 11.11.7) |

Not copied: Code doesn't run a separate "plan mode" or a second model for planning. The rules ask for a plan when the change isn't trivial, inside the same loop, so there are no extra calls.

## 3. Architecture

```
                  ┌── Chat   : chat persona (the model) · chat_mode rules/tools · ChatRun answer checks
LeanSession ──────┼── Code   : code persona (the model) · code_mode rules/tools · verification ledger
 (one harness)    └── Agents : Echo & agents · souls, team, delegation, routines, work
        shared: Lean loop · toolbox · approvals/policy · checkpoints · steering/cancel · ledger · learning · memory
```

- **`environments.turn_environment`** decides who answers. It's `code` for the owner, live, in a Code conversation, not resuming agent work. Routines, channels, guests and resumed work stay with their agent.
- **`code_mode.py`** defines Code's identity ("you're the model the user picked, not one of EchoSpeak's agents"), the workflow rules, the toolsets, the tool boundary (`restrict`) and the local-model focus.
- **The runtime** gives Code:
  - no hand-off, teammates, coordination tools, research workers or soul editing
  - no smart model routing: the model is the one you picked
  - project-scoped context, with no past chats from other environments
  - git rules whenever there's a repo
  - its own learner (`code`)
- **Provider admission now follows the 30 October brief.** Native Code accepts configured local and API endpoints through Lean. Codex and Claude Code use their own sign-in and harnesses. This supersedes the earlier local-only policy; API billing and coding subscriptions remain separate.
- **External harnesses are untouched.** Codex, Claude Code and ACP sessions never enter the Lean loop.

## 4. Verification

These checks ran on this branch. No paid models were used, and only tests for the changed components ran.

- **New: `apps/backend/tests/test_native_code.py` (6 tests):**
  - Each conversation runs in its environment, and routines, guests and resumed work stay with their agent.
  - Code is the selected model with engineering tools: no agent-only or non-engineering tools, the Code workflow in the prompt, no team or cards, and the ledger attached.
  - **An end-to-end native Code run** with a scripted model and terminal: failing test → fix → passing test → answer. It makes exactly 4 model calls. The answer comes from `code` named after the model. Steps read "Ran `pytest …`", "Wrote dates.py", "Ran `pytest …`", and the note under the answer is "Checks pass — …". The episode is recorded under `code` / `code`.
  - Chat and Agents are unchanged.
  - The provider rule is kept.
  - Code can use Echo's proven coding lessons through measured transfer.
- **Re-run and passing (323):**
  - `test_environments`, `test_lean_runtime`, `test_lean_harness`, `test_lean_group`, `test_lean_soul`
  - `test_chat_mode`, `test_chat_paths`, `test_chat_lifecycle`
  - `test_check_step`, `test_live_steps`, `test_execution_evidence`
  - `test_learning`, `test_learning_research`, `test_autonomous_learning`, `test_verified_engineering`
  - `test_capabilities`, `test_screen_progress`, `test_ask_and_recover`, `test_echo_code`

## 5. Needs your live test (Code, native runtime, a local model)

1. Open a project in Code with the native runtime and ask it to fix a failing test. Expect:
   - messages headed by the model's name, not Echo
   - steps "Read …", "Edited …", "Ran `…`" with test counts
   - "Checks pass" or "Verified" under the answer
2. Ask for a feature in a git repo. It should inspect `git status` and diff, but not commit unless asked.
3. Small model (≤9B): only the engineering tools are listed. Others load through `find_tools` if needed.
4. Check Agents still feels like Echo (persona, delegation) and Chat still feels like Chat.

## 6. Limits

- **Mentions don't switch agents in Code.** An `@agent` in a Code conversation is answered by Code; agents live in Agents.
- **`file_edit` follows Settings › Permissions,** like everywhere else. Without file-write permission, Code can read and run but not edit.
- **The Code UI shows the existing Lean step timeline.** Inline diffs per edit aren't added in this release; the "Edited x" step and the file's checkpoint are the record.

## 7. Codex continuation and T3 research, 29 October 2026

This continuation starts at `416fd8de89db90355c4351e176d563c1d3a28db5`
(`claude/v11.12`, PR #113), not at released `main`. GitHub's `main` was
`36835a09ffa2479cd274e6c2db280cc151de6dba`. PRs #103–#113 were all open,
forming one linear stack. The local implementation is on `codex/code-inbox`.
No version bump, merge, push, installer or release is part of this work.
The [Codex handoff](../CODEX_HANDOFF.md) gives the stack and continuation commands.

### Research method and reference snapshot

Read current Echo source, Claude's research/release notes, and GitHub PR metadata.
Read T3's public documentation and relevant source portions at commit
[`b6fbd181cae49d38927360913851121cc35ec4ff`](https://github.com/pingdotgg/t3code/commit/b6fbd181cae49d38927360913851121cc35ec4ff),
the head resolved during this audit. No T3 dependencies, repository scripts,
agent instructions, or provider binaries were executed. No T3 runtime code is
copied. [Source manifest](echo-code-sources.json) preserves the earlier research
and adds this commit's paths and Git blob identities.

T3's [Orchestration V2](https://github.com/pingdotgg/t3code/blob/b6fbd181cae49d38927360913851121cc35ec4ff/apps/server/src/orchestration-v2/Orchestrator.ts)
separates command decisions from side effects. Its
[EventSink](https://github.com/pingdotgg/t3code/blob/b6fbd181cae49d38927360913851121cc35ec4ff/apps/server/src/orchestration-v2/EventSink.ts)
commits events, projections, receipts and outbox entries together; the
[EffectWorker](https://github.com/pingdotgg/t3code/blob/b6fbd181cae49d38927360913851121cc35ec4ff/apps/server/src/orchestration-v2/EffectWorker.ts)
performs external work afterwards. The useful lesson for Echo is explicit
ownership and evidence, rather than adopting another backend or event store.

T3's [settlement service](https://github.com/pingdotgg/t3code/blob/b6fbd181cae49d38927360913851121cc35ec4ff/apps/server/src/orchestration-v2/ThreadSettlementService.ts)
guards settlement against live work, queued starts, snoozing and explicit owner
choices. Its [provider adapter contract](https://github.com/pingdotgg/t3code/blob/b6fbd181cae49d38927360913851121cc35ec4ff/packages/provider-core/src/server/ProviderAdapter.ts)
keeps provider capabilities, steering, native sessions and rollback behind an
adapter. Echo already has these ownership seams; the implemented inbox uses them.

T3's [source-control workflow](https://github.com/pingdotgg/t3code/blob/b6fbd181cae49d38927360913851121cc35ec4ff/docs/user/source-control.md)
distinguishes a turn's own edits from changes brought in by Git, and supports
forge-specific account setup. This motivates turn-linked review and CI as the
next Echo workflow, rather than another generic diff panel.

### Capability inventory validated against Echo source

| Goal | Already implemented | This continuation / remaining gap |
|---|---|---|
| 1. Native model-first Code | `environments.turn_environment`, `lean/code_mode.py`, `runtime.py`: model identity, coding policy, focused tools, selected supported local model; no team or soul | Preserved. No prompt growth, model calls or new routing. Existing native scripted repair test is rerun |
| 2. Project inbox | `echo_code.overview`, `CodeTree`: native/external project tree, native moves, active-session marker | Implemented evidence-backed five-state inbox, filtering, settle/snooze/restore, live refresh |
| 3. Execution timeline | `lean/LeanMessage.tsx`, `lean/steps.ts`: commands, outcomes, folded activity; `workbench.activity`: runtime items | Inbox now exposes native completion evidence. Per-edit clickable diffs and unified external command-output presentation remain |
| 4. Git and PRs | `agent/workbench.py`, `api/routes/workbench.py`: diff, reviewed commit/push/branch operations, PR listing through the owner's GitHub CLI | PR comments, inline review, CI diagnosis and reviewed merge are future work. Existing PR listing is not full PR management |
| 5. Worktrees/checkpoints | `code_worktree.py`, `code_sessions.py`: isolated external worktrees, writer leases, fingerprinted before/after checkpoints, guarded rollback; native file checkpoints | Fixed recovery losing `rolled_back`. Native Code still uses the attached project folder; native per-session isolation and branch ownership remain |
| 6. Queue/steer/resume | StateStore queued turns and pause semantics, `lean/steering.py`, cancellation, approvals/questions, durable agent-work verification restore; external stop/connect/native IDs | New activity wakes a settled/snoozed inbox item. Restart never replays an external prompt. Full provider capability-specific queue/steer UI remains |
| 7. Visual previews | `project_preview.py`, project HTML previews, artifact viewer, workbench/browser controls | Existing Code policy excludes `create_artifact` / `update_artifact`. Need a narrow engineering-preview capability with sandbox/permission tests; do not simply enable all artifact tools |
| 8. Attachments | Existing project-safe paths/file previews, native vision tools and upload/image handling | No universal video/document transport to all external runtimes was established. Review MIME limits, attachment ownership and provider capabilities before broadening |
| 9. Cross-runtime delegation | Independent runtime adapters plus native Agents coordination | No automatic native-to-vendor supervisor. Research only: explicit scoped MCP bridge, provider permission ceilings and completion receipts |
| 10. Scheduled coding | `lean/automations.py`, RoutineManager and existing work records | Reuse them later for project tasks/CI. No second scheduler or unattended external-agent loop added |
| 11. Usage | Context/token UI and `workbench.usage`: Codex-reported account limits; unavailable values remain unavailable | Claude/ACP usage coverage is incomplete; workbench still contains old SDK event handling. Do not infer remaining quota or cost from absent data |
| 12. Remote development | Local authenticated API, process supervisor, privacy admission | Research only. Host identity, capability-scoped access, reconnect, credential ownership and Private/Offline enforcement must precede remote execution |
| 13. Desktop SnapShots | General native screenshot/desktop tools exist; deliberately excluded from Code policy | Research only. User-triggered scoped capture, preview/redaction, explicit attachment, no background capture |
| 14. Device development | No dedicated Code device workflow established in inspected source | Research only. Windows Android emulator is a plausible first slice; iOS simulator requires an appropriate Mac host |

T3's [outside-agent MCP access](https://github.com/pingdotgg/t3code/blob/b6fbd181cae49d38927360913851121cc35ec4ff/docs/user/outside-agents.md)
is explicitly authorized and permission-scoped; it is not a reason to put an AI
supervisor in every coding session. T3's
[remote architecture](https://github.com/pingdotgg/t3code/blob/b6fbd181cae49d38927360913851121cc35ec4ff/docs/internals/remote.md)
keeps execution and credentials on the owning environment. Echo should first
define those host and authority boundaries, rather than expose local owner APIs.
T3's [device architecture](https://github.com/pingdotgg/t3code/blob/b6fbd181cae49d38927360913851121cc35ec4ff/docs/internals/devices.md)
uses consented, supervised device services; this is later platform work.

## 8. Implemented: the Code inbox follows actual work

`apps/backend/agent/code_inbox.py` is a deterministic presentation layer over
existing records. It does not execute work or grade an external harness.

| State | Meaning |
|---|---|
| Working | An existing turn/connection is active, including external workspace preparation |
| Needs attention | An approval/question or queued follow-up is waiting, a turn failed/interrupted, or the native ledger says failing, blocked, partial or unverified |
| Ready to review | A turn finished and needs owner review. Native evidence separately says Verified, Checks pass or Not checked; external sessions say Runtime-owned checks |
| Settled | No work yet, a rolled-back external session, or an explicit owner acknowledgement of this activity |
| Snoozed | Explicitly deferred until its deadline, owner restore, or new execution/queue activity. Expiry changes presentation only; it never runs a task |

Native choices live in `ThreadSessionState.code_inbox`. The native StateStore
lock makes the activity comparison and choice update atomic with turn updates.
External choices live in the existing `session.json`, under its existing writer
lease and supervisor event loop. Both write only presentation metadata. A choice
never changes a ledger, learning attribution, approval, execution status or files.
Active turns, pending decisions (including recovered coordination approvals and
questions) and queued native follow-ups cannot be triaged.
New activity invalidates older choices; stale actions fail rather than conceal it.
An owner can acknowledge an idle failed turn as settled without changing the
visible `Checks failed` evidence.

The authenticated API adds:

- `POST /echo-code/sessions/{thread_id}/inbox`
- `POST /echo-code/external-sessions/{session_id}/inbox`
- Body: `{ "action": "settled" | "snoozed" | "reopen", "activity": "<current 32-character activity key>" }`.

`GET /echo-code/overview` adds `inbox` to each row. External overview rows are
small summaries with no transcript events, approval arguments, worktree details,
auth or model probes. The existing external-session listing contract remains
unchanged. The UI refreshes every four seconds while visible and immediately
after a choice; overlapping/stale view responses are discarded on view revision.
Errors remain visible after refresh. This is bounded polling, not a new event bus.

### Recovery correction found during the work

Previously, `_load` reset every external session to `stopped`, including one
already `rolled_back`. That erased the terminal reconnect guard. Recovery now
preserves `rolled_back` and retains the last completed/failed/interrupted turn
separately from connection state. An interrupted in-flight session remains
disconnected and needs attention. The worktree lease rechecks the on-disk row
before persisting an inbox choice. Restart never reconnects or replays a task.

## 9. Verification for this continuation

Evidence and exact commands are recorded in [native-code-verification.json](native-code-verification.json)
and [CODEX_HANDOFF.md](../CODEX_HANDOFF.md). Counts there apply only to this
continuation; section 4 records Claude's earlier checks.

New focused tests cover failed evidence despite a success flag, all native
completion statuses, persisted choices, new-activity invalidation, stale-action
rejection, live questions/approvals, queued follow-ups, read-only projection,
Chat refusal, request validation, actual fixture protocol activity, summaries,
and terminal rollback recovery. The existing native repair test keeps its
scripted failing-test → edit → passing-test path and model-call assertions.

The actual `CodeTree` component is browser-checked by
`scripts/check-code-inbox-ui.py` using deterministic API fixtures. Its dark/light
captures are [inbox-dark.png](../../artifacts/media/code-inbox/inbox-dark.png) and
[inbox-light.png](../../artifacts/media/code-inbox/inbox-light.png). These are
component fixture evidence, not a live model or installed desktop screenshot.

No paid-model evaluations, real-account/provider sessions, full unrelated test
suites, packaging, installation, merge, push, publish or release were performed.
The owner still needs to accept real-model coding, live approvals/cancellation,
external stop/reconnect, and the installed Tauri experience.

## 10. Next priorities after the initial inbox pass

1. Accept one everyday native local-model workflow: failing test → edit → rerun →
   inspect the diff → owner marks settled. Check Chat/Agents boundaries alongside it.
2. Add turn-linked change review and GitHub CI/comment diagnosis using the existing
   workbench and `gh` context. Keep reviewed commit/merge authority explicit.
3. Design native session worktree/branch ownership with project binding, writer
   arbitration, checkpoint/history consistency and cleanup before parallel writers.
4. Add bounded engineering HTML/JS previews. The local image-stash and scoped MCP
   continuation below covers the first attachment/control slice. Remote hosts and
   devices still need a separate authority and reconnect design.

## 11. Screenshot follow-up: parallel inbox, control and observations

Updated 30 October 2026 UTC. This section supersedes the initial inventory's
research-only entries for local MCP and image stashes, and its old five-group UI.
The original source and verification snapshots above remain historical evidence.

### Research and adaptation

Compared the user's T3 sidebar screenshot with current Echo components, then read
T3 at [`cba30591f95859bc76154e5be97891ea4ae1b85a`](https://github.com/pingdotgg/t3code/commit/cba30591f95859bc76154e5be97891ea4ae1b85a).
The [sidebar guide](https://github.com/pingdotgg/t3code/blob/cba30591f95859bc76154e5be97891ea4ae1b85a/docs/user/thread-sidebar.md),
[sidebar logic](https://github.com/pingdotgg/t3code/blob/cba30591f95859bc76154e5be97891ea4ae1b85a/apps/web/src/components/Sidebar.logic.ts)
and [snooze helpers](https://github.com/pingdotgg/t3code/blob/cba30591f95859bc76154e5be97891ea4ae1b85a/apps/web/src/components/Sidebar.snooze.ts)
support a flat inbox with parked work, background drafts and bulk actions. Echo
adapts those interactions to its own StateStore and external supervisor; no T3
runtime source or dependencies were copied or executed. The source manifest
records exact Git blob identities.

T3's [outside-agent guide](https://github.com/pingdotgg/t3code/blob/cba30591f95859bc76154e5be97891ea4ae1b85a/docs/user/outside-agents.md)
and [MCP access logic](https://github.com/pingdotgg/t3code/blob/cba30591f95859bc76154e5be97891ea4ae1b85a/apps/server/src/mcp/McpToolAccess.ts)
establish separate clients and permission ceilings. Echo's first slice is local
stdio with explicit scope, rather than T3's remote HTTP/OAuth environment system.
This is an optional tool interface, not another supervisory model in each turn.

T3's [usage guide](https://github.com/pingdotgg/t3code/blob/cba30591f95859bc76154e5be97891ea4ae1b85a/docs/user/usage.md)
distinguishes available history, account limits and API-equivalent estimates from
subscription bills. Its [triage guide](https://github.com/pingdotgg/t3code/blob/cba30591f95859bc76154e5be97891ea4ae1b85a/docs/user/triage.md)
and [CLI](https://github.com/pingdotgg/t3code/blob/cba30591f95859bc76154e5be97891ea4ae1b85a/apps/server/src/cli/triage.ts)
start the owner's coding agent with a troubleshooting playbook. Echo instead
implements a smaller local observation export first. The inspected sources do
not establish the claimed 80% fix rate or a CPU-based automatic routing policy.

### Left sidebar and composer

`CodeTree.tsx` now renders flat rows with project, title, runtime, known branch or
honest folder fallback, elapsed time and state. The **left** Code sidebar changes;
the sidebar replacement does not move to the right. The existing right Usage panel
also gains the observations described below. Working is an optional,
initially collapsed shelf; Snoozed and Settled sit below the active inbox. Pending
decisions remain visible as Input even when the runtime is busy. Project filtering
replaces nested project groups. Chat and Agents keep their navigation.

- Ctrl/Cmd-click toggles selection; Shift-click selects a visible range, excluding
  collapsed shelves. Bulk actions return per-item errors without hiding failures.
- Settle, wake, and snooze for 15 minutes, one hour or one day. The backend supports
  up to 30 days. Deadline expiry restores inferred state with a Woke badge on read;
  it starts no model, scheduler or execution. New activity invalidates the choice.
- Ctrl/Cmd+S stashes a native prompt and attached images at the top of the sidebar.
  Restoring validates project trust and original root. Removing a stash keeps its
  uploaded project files. Stashes are bounded to 50 entries and four verified
  PNG/JPEG/WebP images below 2 MB each. Images become project-relative read_image
  references under `.echospeak/attachments/`; file-write permission is required.
- Ctrl/Cmd+Enter creates and sends a native task in the background through the
  existing `/query` handler. Stable request receipts prevent duplicate admission;
  follow-ups use the existing queue. Stop preserves cancellation across admission
  and worker registration. Four helper sessions maximum; same-project native
  parallel writers are refused because their folder is shared. Separate vendor
  worktrees retain their existing parallel execution. For external composers the
  shortcut sends to the selected runtime; it does not create another draft.
- OpenAI and Claude marks use the existing local SVG assets in model/runtime
  controls. OpenAI contrast follows the light/dark theme; no remote image fetch.

`code_prs.py` supports **explicitly linked** PRs using the owner's existing `gh`
workbench. Link the PR number from an idle row's menu. Visible-inbox sync checks
at most once per minute and settles only Ready to review activity with the same
project/root, feature branch, HEAD, clean checkout, PR number, URL and repository,
after the forge reports MERGED. Activity is rechecked atomically when writing.
Late forge responses cannot override owner snooze, new work or decisions. This
does not merge PRs or discover all related PRs automatically.

New owner API routes: `/echo-code/inbox/batch`, `/inbox/pull-request`, `/inbox/sync`,
`/drafts`, `/drafts/{id}`, `/background` and `/diagnostics`; single-row inbox bodies
also accept `snooze_seconds`. All retain owner authentication.

### Optional MCP for Code and Agents

Open **MCP access & diagnostics** at the bottom of the left Code sidebar. Choose
named projects and/or persistent agents, then create a client. Read only is the
default; control is explicit. Tokens are shown once, stored only as hashes, expire
after seven days by default (API: 1–30 days), and can be revoked. Project roots and
trust are rebound on each call. Agent conversations with an attached project also
require that project's grant. Ordinary Chat conversations are excluded.

The panel provides a client configuration using the current Python interpreter
or the packaged backend's `--control-mcp` mode. The existing MCP SDK 2 server and
stdio transport expose `echo_projects`, `echo_runtimes`, `echo_threads`,
`echo_thread_status`, `echo_thread_create`, `echo_thread_send`, `echo_thread_stop`
and `echo_thread_triage`. The bridge calls only the exact local
`POST /echo-code/control/invoke` endpoint with a separate grant. It bypasses
ambient proxies and refuses redirects before forwarding credentials. The route
rejects physical non-loopback clients, forwarded-IP spoofing and browser Origins.
Owner API keys cannot substitute for client grants. Grant creation/revocation
still uses owner authentication.

Control submits through existing native Code/Agents execution and queues, or an
already connected external harness. It never logs in, silently launches an
external CLI, approves tools, changes settings, merges, or publishes. Underlying
harness permissions still govern tools; project scope is not an OS sandbox.
Native Code retains its supported-local-model rule. Agents retain their own
identities and model policy. Add this config to an outside MCP client, or explicitly
to Echo's existing MCP connections for approved agents. No connection is silently
registered. Tests cover the real SDK stdio client and both Python entry paths;
the initial implementation pass did not build the compiled desktop binary.
Subsequent 11.13.0 preparation validates the compiled API and stdio mode; see the
[versioned release evidence](../releases/v11.13.0.md#verification).

Remote HTTP MCP, OAuth/pairing, hosted-bot reachability, mobile UI, multi-host
identity/routing and provider permission-level delegation remain future work.

### Usage, limits and local triage

`code_usage.py` persists finite, nonnegative, allowlisted token fields from actual
Codex tokenUsage events and Claude CLI result messages. Workbench Usage shows the
reported scope, timestamp and any reported USD cost, labeled separately from a
subscription bill. Codex totals can be cumulative for its native thread; Claude
data is the latest reported result. These are not account-wide historical totals.
The old removed Agent SDK event path is replaced. Existing live Codex account
limit reads remain; missing Claude/ACP limits are unavailable, never zero or an
invented percentage. No login credential or unrelated terminal history scraping.

The Usage panel also shows local CPU percentage, total/available memory and
backend RSS when psutil can observe them. These measurements do not prove remote
capacity, provider health or any intelligent routing policy. **Save triage report**
exports an allowlist of version/platform/privacy, resources and runtime state/counts;
prompts, conversations, raw logs, credentials, paths and account IDs are excluded.
It runs no model, uploads nothing and opens no GitHub issue. For source CLI use:

```powershell
Push-Location apps/backend
& .\.venv\Scripts\python.exe -m agent.code_diagnostics --output ..\..\.test-state\echospeak-triage.json
Pop-Location
```

The CLI refuses an existing output file. CLI reports observe that process and do
not connect to a running app; use the in-app export for live runtime state.

### Verification and remaining acceptance

The [verification record](native-code-verification.json) separates initial and
follow-up runs. Follow-up coverage includes real stores, local MCP stdio/HTTP,
grant expiry/revocation/scope, image validation, timed wake, native queue drain,
idempotence, admission cancellation, PR identity/dirty/new-work/owner-choice guards,
and usage export redaction. Native model behavior remains scripted; no paid or
live provider call was made. The browser verifier mounts actual ProjectSidebar,
CodeTree and EchoCodeWorkspace components with deterministic APIs, verifies their
left placement, and exercises selection, stale errors, stash/restore, image stash,
background submission and theme/layout. Its captures are fixture evidence.

Remaining parity gaps include bulk title regeneration, full PR/CI review and
merge workflows, automatic PR discovery, native worktree isolation, durable
cross-restart background dispatch receipts, provider-wide historical usage and
remote/mobile control. Real-model tasks, real vendor limits, owner approvals,
external stop/reconnect, Windows packaging and installed desktop acceptance still
need the owner's hands-on checks. The initial pass did not push or package.
The owner subsequently authorized 11.13.0 versioning, local packaging and a
stacked PR; [release notes](../releases/v11.13.0.md) record that delivery separately.
Merging and GitHub release publication remain unauthorized.

## 12. Shared sidebar visual refinement

11.13.0's follow-up replaces the sparse sidebar presentation across all three
environments. `components/sidebar.css` scopes the shared header, icon tabs,
conversation/agent rows, action visibility and Code typography. `SidebarIcon.tsx`
provides local SVG navigation glyphs. Code filters are disclosed from the toolbar;
the existing inbox state/permission owners are unchanged. Runtime layout uses
288px expanded and 56px collapsed widths. Roster previews use existing messages
or configured roles. The versioned release HTML shows actual dark/light component
captures for Chat, Agents and Code; fixture checks include search, opening an agent,
keyboard filter toggles and collapse/expand. See the release record for build and
installer provenance; older installer hashes describe the earlier visual pass.

## 13. UI and runtime reliability continuation

**Objective → Research → Environment → Verification → Adaptation → Completion.**
The audit reused existing environment routing, Lean, the external supervisor,
permission hooks and isolation instead of adding another agent controller.

### Findings and changes

| Finding | Implemented behavior |
|---|---|
| Parked sections expanded downward inside the active list | Working, Snoozed and Settled bars dock below the scrolling active list. One parked list opens above its bar at a time; switching bars reserves useful reading space in short windows. Settings and connection controls remain accessible. Working remains optional in Filters; all enabled bars appear even when empty. |
| Claude used a static alias catalog | `code_accounts.py` reads the installed official CLI's stream initialization model metadata, resolves version IDs, merges duplicate default aliases and keeps reported effort levels. No model prompt, repository hooks, MCP servers or persistence in this metadata probe. Missing/unsupported metadata displays unavailable, with an exact-ID input. |
| Reconnecting Codex could replace a saved model with the harness default | Preserve the durable session choice; pass it to thread start/resume and the next turn. Claude also reuses the saved model and keeps previously observed limit events on reconnect. |
| Git stderr was discarded | Bounded classification distinguishes non-repository, missing committed HEAD, nested folder, ownership refusal, access/lock failure, missing Git and timeout. Workspace admission explains the blocker before external session creation. No owner files are auto-committed and `safe.directory` is never changed. |
| Code excluded configured cloud/API endpoints | Native Code now uses the same configured local/API endpoints as Lean, preserving project trust and provider readiness. The picker labels API vs on-PC models. External coding subscriptions keep their own provider harness and permissions. |
| Account windows were raw/missing and Claude failures were generic | Normalize provider-reported quota windows, reset times, observation/source and limit rejections. Share `RuntimeUsage` across the picker, workspace, connection and Usage views. Consolidated account observations are separate from per-session tokens/costs. Missing numeric quota never becomes zero. Claude's returned error text is redacted and displayed. |

The owner-project metadata audit confirmed that the two reported non-repository
folders cannot provision an isolated external session; the EchoSpeak repository
has a usable committed HEAD. Native Code still accepts a trusted plain folder.
Selecting an actual repository root or explicitly initializing/reviewing an
initial commit remains an owner project operation, not an automatic runtime fix.

### Research and compatibility

- [Codex app-server](https://learn.chatgpt.com/docs/app-server): `model/list`,
  `account/read`, `account/rateLimits/read`, account limit updates and native
  thread/turn APIs. Quota percentages are account windows, not token costs.
- [Claude CLI](https://code.claude.com/docs/en/cli-reference),
  [model configuration](https://code.claude.com/docs/en/model-config) and
  [SDK reference](https://code.claude.com/docs/en/agent-sdk/typescript): official
  sign-in, stream initialization/model metadata and `rate_limit_event`.
  The metadata wire is CLI-version dependent; incompatible versions show
  discovery unavailable rather than a guessed list. An advertised model is
  not proof that the account has remaining allowance for a turn.
- No supported universal passive Claude subscription-percentage endpoint was
  established by this research. Show numeric utilization/reset only when the
  runtime supplies it; a rejection/error may provide useful reset information
  without a numeric window. No credential scraping or private usage endpoint.

### Verification actually performed

| Layer | Result |
|---|---|
| Live Codex, installed `0.166.0-alpha.5.1`, existing ChatGPT sign-in | Eight reported models; one isolated file-read/write task completed and its file content was checked. Session identity/model survived stop/reconnect. Account primary/secondary windows were reported. No API key or fresh login. |
| Live Claude Code, installed `2.1.310`, existing Claude subscription | Twelve resolved models, including **Opus 5.5 / `claude-opus-5-5`**. One low-effort Haiku 5.5 task was attempted; the runtime rejected it at the weekly limit. Native identity/model survived reconnect. File execution did **not** pass. No repeated inference attempts. |
| Deterministic backend | A broad 128-case run passed 127; its obsolete local-only policy assertion was updated for this explicit brief. Final affected runs passed **26/26** (Git/usage/native/workbench) and **47/47** (Claude/Codex/session protocols). Across these runs all **130 unique** relevant cases passed. |
| Web | TypeScript, **33/33** runtime/layout tests and **29/29** additional Chat/sidebar tests passed. Browser interactions cover all three sidebars, local/API selection, exact Claude ID, quota rendering, upward shelves at 900px/560px heights, 390px width, keyboard/filter/bulk/stash/background/MCP actions. Screenshots are deterministic component fixtures. |
| Desktop contracts | **16/16** source contract tests passed; these are separate from installed acceptance. |
| Packaging / installed | See the current candidate in [11.13.0 notes](../releases/v11.13.0.md). Building a local installer does not establish installed-app acceptance. |

[Machine-readable sanitized evidence](runtime-reliability-verification.json)
keeps live results separate from fixtures; raw account IDs, native session IDs,
personal paths, credentials and live quota percentages are excluded.

Live reconnect checks covered transport/authentication and saved identity/model;
a separate **no-prompt Codex `thread/resume`** check then reopened the original
native history with its selected model preserved. No second inference turn or
in-flight live interruption was attempted. Claude resumed execution remains
provider acceptance, separate from protocol fixtures.

### Architecture and next acceptance

Chat remains model-first conversation/research/artifacts. Code is model-first
engineering with project/files/terminal/Git/verification. Agents keep identity,
responsibility and coordination. Native surfaces share the loop, tools,
permissions, persistence and verification; external harnesses never enter that
loop or become tools controlled by Echo's native model.

Existing Chat sizing already gives API/large/unknown models its full profile and
small local models a compact one. Native context follows configured/discovered
metadata; unknown windows retain the existing **32,768-token fallback**, which
is a conservative application setting, not a claim about provider capacity.
Reasoning/tool controls follow adapter capabilities. The Code welcome composer
defers effort for a different, unbound native model until its session profile is
loaded, instead of displaying the previous model's controls.

Next priorities: Claude file/tool/approval/interrupt acceptance after its limit
resets; installed Windows testing of both providers and the actual projects;
live native API/local acceptance; richer provider context metadata and model
catalog pagination beyond the current bounded catalog. The existing browser
checks are focused, not an exhaustive acceptance test of every app surface.
Future mobile/remote access needs authenticated pairing, scoped transport,
session routing, reconnect/replay and approval UX. Remote routing, new schedulers
and separately forked harnesses were not prematurely implemented.

## 14. Final 11.13.1 sidebar refinement

The approved Code inbox is retained. Search replaces the sidebar identity header;
Chat and Agents use vertical page lists, and four icon utilities share the footer.
See [11.13.1 notes](../releases/v11.13.1.md) and the paired visual report for current
captures and installer commands. Earlier 11.13.0 screenshots remain historical.
