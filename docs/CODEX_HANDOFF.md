# EchoSpeak: Codex engineering handoff

Updated 30 October 2026 UTC from source; the PR table is the 29 October GitHub snapshot. Current local work
is on `codex/code-inbox`, based on `416fd8d` (Claude's 11.12 stack). Source version is 11.13.0. The owner authorized a scoped PR and local Windows build.
Do not merge or publish a GitHub release without a further instruction.

[PR #114](https://github.com/Ty0x7/EchoSpeak/pull/114) is open, ready for review,
targeting `claude/v11.12` (stacked on #113). Consult its current head for the complete continuation.
The previous sidebar candidate was pushed. The current runtime continuation is summarized below and in native-code §13; verify the latest commit before continuing.

## Required handoff workflow

**Objective → Research → Environment → Verification → Adaptation → Completion.**
For meaningful work, keep technical Markdown plus visual HTML and actual labeled
screenshots, use the owner's requested version consistently, and provide exact
build/test/install commands. Check the stack before choosing a PR base. Record
source tests, packaged checks and owner live/installed acceptance separately.
Commit scoped work for an authorized PR; merging and release publication need
their own authorization. The 11.13.0 release record is
[v11.13.0.md](releases/v11.13.0.md), paired with its
[HTML report](../artifacts/releases/v11.13.0.html).

## Repository and Claude context

`origin` is `https://github.com/Ty0x7/EchoSpeak`. Verified GitHub `main`:
`36835a09ffa2479cd274e6c2db280cc151de6dba` (merged #102). All of the following
PRs were open when checked. Each targets the preceding branch, not `main`.
Do not cherry-pick or merge the top independently and lose its prerequisites.

| PR | Branch | Work already in the current checkout |
|---|---|---|
| [#103](https://github.com/Ty0x7/EchoSpeak/pull/103) | `claude/agents-and-checkins` | 11.10.10: account-plan Code policy, connectors/check-ins |
| [#104](https://github.com/Ty0x7/EchoSpeak/pull/104) | `claude/three-environments` | 11.11.0: Chat / Agents / Code, Code tree and moves |
| [#105](https://github.com/Ty0x7/EchoSpeak/pull/105) | `claude/chat-harness` | 11.11.1: lighter model-first Chat |
| [#106](https://github.com/Ty0x7/EchoSpeak/pull/106) | `claude/chat-orchestration` | 11.11.2: evidence-oriented Chat |
| [#107](https://github.com/Ty0x7/EchoSpeak/pull/107) | `claude/research-engine` | 11.11.3: source research |
| [#108](https://github.com/Ty0x7/EchoSpeak/pull/108) | `claude/chat-reliability` | 11.11.4: answer review and lifecycle fixes |
| [#109](https://github.com/Ty0x7/EchoSpeak/pull/109) | `claude/verified-engineering` | 11.11.5: deterministic verification ledger |
| [#110](https://github.com/Ty0x7/EchoSpeak/pull/110) | `claude/autonomous-learning` | 11.11.6: shared native learning and updater dependency fix |
| [#111](https://github.com/Ty0x7/EchoSpeak/pull/111) | `claude/learning-research` | 11.11.7: held-out transfer, curation and context budgets |
| [#112](https://github.com/Ty0x7/EchoSpeak/pull/112) | `claude/native-code` | 11.11.8: native model-first Code |
| [#113](https://github.com/Ty0x7/EchoSpeak/pull/113) | `claude/v11.12` | 11.12.0: activity folding, following, effort, workspace details, deletes |

Pre-existing untracked user work was preserved: `Echo Marketing/`,
`Echo Minecraft/`, and `docs/research/agent-collaboration-11.9.1.md`.
Do not include it in an incidental commit or cleanup. No changes were made to
the earlier Claude branches or their PRs. Recheck remote state before any later
integration; this table is a dated snapshot.

## Execution boundaries to keep

- Chat: selected model as a general assistant. Code: selected supported local
  model as an engineering assistant. Agents: persistent identities and routines.
  `agent/environments.py` owns the distinction.
- Native Code uses the shared Lean loop, tool permissions, engineering prompt,
  11.11.5 ledger and its own learning identity. No ordinary coding supervisor,
  agent personality, delegation roster, or extra orchestration model call.
- Preserve the current provider rule: API-key models are for Chat; the user's
  Codex and Claude Code run with their own installed harnesses and sign-ins.
  ACP agents keep their configured native executable and negotiated capabilities.
- External sessions never become native threads or enter `/query/stream`.
  `code_sessions.py` owns them, their leases and their isolated worktrees.
  External execution remains blocked in Private/Offline mode.
- `code_inbox.py` projects status only. A review choice is not verified completion,
  permission, cancellation, a worktree rollback, or a learning outcome.

## This continuation

Implemented a flat **left** Code inbox with project labels, optional Working shelf,
timed snooze, settle/wake, visible Shift range selection and bulk actions. Native
Ctrl/Cmd+Enter submits background work through the existing query/queue; Ctrl/Cmd+S
stashes prompts and bounded project images. Explicit PR links can settle unchanged,
clean, idle activity after confirmed merge. Owner choices remain bound to activity;
native verification stays separate. External recovery preserves rollback finality
and the last turn outcome without replaying work. Local OpenAI/Claude marks are fixed.

Optional local stdio MCP has separate hashed, expiring, revocable project/agent
grants, read-only default, explicit control and the existing harnesses. No remote
HTTP/OAuth/mobile control, implicit provider launch or tool approval bypass.
Workbench Usage consumes actual reported token/cost data and available limits;
local CPU/memory observations and an allowlisted triage export are added.

Start with [native-code.md](research/native-code.md) sections 7–11 for the source
inventory, current T3 research, APIs, decisions and remaining gaps. The paired
[HTML report](../artifacts/research/native-code.html) shows actual component
fixtures and a labeled architecture comparison. The
[evidence file](research/native-code-verification.json) contains this run's checks.

## Repeat focused verification

From the repository root in PowerShell, use disposable temp paths so sandbox
temporary-directory restrictions do not affect the test runners:

```powershell
# Run from the EchoSpeak repository root.
$env:TMP = Join-Path (Get-Location) '.test-state'
$env:TEMP = $env:TMP
New-Item -ItemType Directory -Force -Path $env:TMP | Out-Null
Push-Location apps/backend
& .\.venv\Scripts\python.exe -m pytest tests/test_code_parallel.py tests/test_code_inbox.py tests/test_environments.py tests/test_native_code.py tests/test_code_sessions.py tests/test_echo_code.py tests/test_verified_engineering.py tests/test_ask_and_recover.py tests/test_workbench.py tests/test_chat_lifecycle.py tests/test_mcp_v760.py tests/test_security_controls.py -q
Pop-Location
Push-Location apps/web
npm.cmd run test:run -- src/code src/app/environments.test.tsx src/workbench/store.test.ts src/lean/activity.test.tsx
npm.cmd run typecheck
npm.cmd run build
Pop-Location
Push-Location apps/desktop
npm.cmd test
Pop-Location
& .\apps\backend\.venv\Scripts\python.exe scripts/check-code-inbox-ui.py
```

The browser script needs the already available Playwright Chromium runtime and
existing web dependencies. It starts a temporary loopback Vite server, writes
fixture screenshots, and removes its own web entries. It does not start Echo's
backend or contact models/accounts. Local process/network checks needed an
unsandboxed test run in this session; application policies were not relaxed.

For source development, start the ordinary backend and web processes in separate
terminals. These commands do not package or publish the Windows application:

```powershell
Push-Location apps/backend
& .\.venv\Scripts\python.exe app.py --mode api
```

```powershell
Push-Location apps/web
npm.cmd run dev
```

## Owner acceptance and next development

Run one local-model repair in native Code, inspect the diff and verification,
stop/resume with a queued message, and acknowledge the result in the inbox.
Check pending decisions take attention priority. Then test a connected external
runtime's approval, stop, restart and reconnect using your own account. Test the
installed desktop separately. No automated fixture establishes these layers.

Also accept native background submit, stash/restore including an image, Shift bulk
triage, expiry waking without execution, and an explicitly linked merged PR. Add a
read-only MCP client, confirm denied sends, revoke it, then explicitly test control
on a disposable project. Keep the separate client grant out of committed config.
Real provider quotas and installed stdio startup remain unverified.

Next: turn-linked diffs/CI review, bulk title regeneration, durable background
admission recovery, a native worktree lifecycle and bounded engineering previews.
Remote access, mobile, HTTP MCP/OAuth and devices remain research proposals.

## Build and try the local Windows source

Run from this checkout; no pull/reset is needed to test this branch.
This command builds a local installer and does not publish a release:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\apps\desktop\scripts\build-windows.ps1
& .\apps\desktop\src-tauri\target\release\bundle\nsis\EchoSpeak_11.13.0_x64-setup.exe
```

The retained local installers are `release/v11.13.0/EchoSpeak_11.13.0_x64-setup.exe`
and `release/v11.13.0/EchoSpeak_11.13.0_x64_en-US.msi`. Packaging and compiled API/MCP
checks passed, but updater signing failed because its encrypted key password was
unavailable in the noninteractive run. No updater signatures/latest.json were
produced. Run `apps/desktop/scripts/release-windows.ps1` interactively to enter the
password locally when preparing a signed release. No installation, merge or GitHub
release publication was performed. See the versioned artifact evidence.

## Shared sidebar visual continuation

The current 11.13.0 candidate updates Chat, Agents and Code navigation together.
Expanded width is 288px; the collapsed rail stays 56px. Shared styling lives in
`components/sidebar.css`, SVG glyphs in `SidebarIcon.tsx`, and actual agent previews
in `Roster.tsx`. The existing section resizing, search, callbacks and Code inbox
owners are retained. Filters now open from the Code toolbar. Browser fixtures
cover all environments, themes, filter keyboard activation and rail collapse.

Earlier UI installer: `release/v11.13.0/sidebar-refinement/EchoSpeak_11.13.0_x64-setup.exe`, superseded by the runtime candidate below.
Its normal NSIS build exited 0. This unsigned local test candidate supersedes the
earlier UI EXE at the version folder root. See the versioned sidebar verification
record and HTML report. Do not label earlier source hashes or installer checks
as evidence for this refined UI. No installation, merge or publication occurred.

## Current reliability continuation

30 October: the owner explicitly expanded native Code to configured **local/API**
models. The earlier local-only policy is superseded. Agents retain their identity
and coordination; Chat/Code remain model-first; external harnesses stay separate.

Bottom-docked Working/Snoozed/Settled bars open upward, one list at a time, with
bounded independent scrolling. Runtime models come from provider metadata;
Claude's actual catalog includes Opus 5.5. Saved session models survive reconnect.
Plan windows/reset/rejection observations are separate from token/cost data.

Live Codex passed one isolated file-read/write task and reconnect. Claude sign-in,
catalog and reconnect passed; its single Haiku task hit the weekly limit. Do not
claim Claude file/tool execution passed or rerun paid prompts automatically.
The reported plain folders have no Git repository; owner initialization/review
is needed before isolated external sessions. No source project was auto-committed.

Read [native-code §13](research/native-code.md#13-ui-and-runtime-reliability-continuation)
and [11.13.0 continuation](releases/v11.13.0.md#runtime-reliability-continuation-30-october)
for exact checks, architecture, sources and current installer provenance. Older
fixture-only/live-unverified statements and installer hashes are historical.
No merge, installation, signing or public release is authorized by this report.

Current EXE: `release/v11.13.0/runtime-reliability/EchoSpeak_11.13.0_x64-setup.exe`.
Fresh full build exited 0; both EXE/MSI report 11.13.0. Compiled import/API/MCP
checks passed. Local unsigned artifacts are retained; none was installed.
EXE SHA-256: `cee96b1635c382073d2a1fd303ce5c7135295b5c670d4f2622264e222fd5d74d` (174,666,238 bytes).
