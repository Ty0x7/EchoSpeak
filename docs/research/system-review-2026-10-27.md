# EchoSpeak system review — 27 October 2026

A review of EchoSpeak as one environment after PRs #94–#99. It covers what each feature is
for, who owns its state, where it runs and what it is allowed to do. It puts ordinary Echo Chat
first and proposes a small plan before more feature work.
[Visual report](../../artifacts/research/system-review-2026-10-27.html).

**Evidence labels:**
- **Source:** read in the code at the baseline below.
- **Test:** reproduced by a deterministic test that is now in the repository.
- **Fixture:** scripted model or transport, as in earlier reports.
- **Doc:** an official vendor page, dated.
- **Owner:** only live acceptance on your PC can establish it.

No paid or live model was called, no message was sent, and no account or system setting was changed.

## 1. Verified state

| Item | Checked |
|---|---|
| `main` | `1a8f4c3d5444947c4383042631ef1ad371adbbc8`, equal to `origin/main` and to tag `v11.10.7`. The working tree is clean apart from your own untracked folders. |
| Versions | 11.10.7 in the backend, web, desktop, Tauri config, `Cargo.toml` and both locks |
| Release | v11.10.7, published 27 Oct 2026 00:28 UTC, with the setup EXE (270 MB), MSI (383 MB) and `latest.json`. The update signatures carry `version:11.10.7`, so installed copies with `requireSignedVersion` accept it. |
| Release text | `latest.json` notes say *"Prepared source release; no installer built, installed or published by this work."* This is the sentence the app shows beside "Update to 11.10.7" (finding R9). |
| Earlier security branch | `claude/security-audit` has no commits that aren't in `main`. Its work landed through #94, so it has nothing to open as a PR. |
| Runtimes on this PC | The Codex CLI and Claude Code are not installed (`claude-agent-sdk` 0.2.174 is in the build environment). External runtime acceptance can't happen here until they are. |

**Fresh checks (this review, scripted only):**
- **Backend, full suite on main:** 1397 passed, 7 failed, 1 error, 22 skipped. Not green (R0, §11).
- **Web `npm run check`:** typecheck plus 192 tests passed.
- **Desktop contracts:** 16 of 16.
- **Rust:** `cargo test --lib`, 4 of 4.
- **New review tests:** `test_system_review_1027.py`, 2 passed and 14 expected failures (one per open finding).

These are not live model or installed-app acceptance.

## 2. The system today, in one page

EchoSpeak is a Tauri desktop shell over a local FastAPI backend. It listens on 127.0.0.1 only,
with a per-launch key. Everything you talk to runs through one native harness, **Lean**
(`agent/lean/runtime.py` → `loop.py` → `toolbox.py`). Echo, Jarvis, GLaDOS and your own agents
are *personas*: an identity, instructions, a model override and toolsets. They are not separate
programs. Echo Code adds a second family of harnesses, Codex, Claude Agent and ACP agents, which
run their **own** tool loops in isolated Git worktrees and never enter Lean.

Ordinary Echo Chat, traced through the source:

1. **Request.** `POST /query/stream` → `run_lean_query` → `LeanSession.run`, under a per-chat lock.
2. **Work record (deterministic).** A record is created when you're the owner *and* either `needs_action(message)` matches a keyword list, or the first non-read-only tool runs (`ensure_work` in `loop._run_tool`). See R4.
3. **Context.** This includes:
   - your soul and identity, the working rules, team and environment
   - the capabilities report built from the live toolbox
   - recalled memories (scoped), learned lessons, approved skill names and `AGENTS.md` instructions
   - the project brief and the chat summary
   - past chats, only when you point back ("last time…")
4. **Model selection.** The chat's provider and model binding. Smart model choice can route non-visual work. Groups add a routing call (R8).
5. **Model and tool loop.** Every tool call goes through these, in order:
   - policy (Rule of Two, taint, secrets)
   - approvals or Autonomous mode
   - the per-tool switches
   - privacy
   - Blocked apps
   - the work ledger's intent and resource reservation (R1–R3)

   Results carry an outcome and observed effects (`evidence.py`).
6. **Checks.** These include the promise guard, unbacked-claim checks, the check-before-done nudge and screen-stall notes (11.10.3). Groups, and requests that were handed off, add a completion judge (R8).
7. **Persistence.** The timeline and messages go to StateStore. The work record closes as completed, failed or `waiting_*`. A "chat" delivery row is recorded for work, even though the reply is already in the chat.
8. **Afterwards.** Each of these is admitted through `admission.py`'s gate: one local inference slot, four total, foreground first.
   - Learning: an episode is graded now; reflection runs later, while idle.
   - A title, for the first message only.
   - A rolling summary, once past a batch threshold.

Plain conversation stays fairly light. Ordinary Chat has no extra router call, and no work record
unless a keyword or a mutating tool appears. The main costs are elsewhere:
- **Prompt weight:** a plain question sends 32 tool schemas and about 6.8k tokens with default permissions, and 64 schemas and about 11.9k tokens in your 11.10.1 trace (R12).
- **Group chats:** extra model calls (R8).
- **The work ledger:** sharp edges once tools fail (R1–R3).

## 3. Feature map and behaviour contracts

Each row: purpose and entry point → **state owner** → runtime/model → context → permissions → lifecycle → output → settings → links and deliberate isolation → evidence and gaps.

| Feature | Contract |
|---|---|
| **Echo Chat** | Default assistant; New chat. **ThreadManager + StateStore** (executions, items); work records in `phase3/coordination.json` when work starts. Lean with the chat's model binding. Context is the session history and summary, account memory (Echo only), lessons, skills and `AGENTS.md`. Toolsets ∩ owner switches ∩ policy ∩ privacy. Stop cancels; a restart reconciles attempts as interrupted or unknown. Output: the chat, plus artifacts and files. Settings: Models, General › How agents work, Permissions. Links: delegation, reminders, artifacts, Activity. **Gaps:** R1–R5, R12; live owner acceptance. |
| **Specialist chat** (Jarvis, GLaDOS, custom) | Pick an agent; a direct room. Same owners. Lean with the persona's model override. Context is session/project-scoped, not Echo's account-wide projection (#99), plus the agent's own lessons and working notes. Its own toolsets. Output: its room. Settings: Agents. Isolation: a separate identity and memory scope by design. **Gap:** owner acceptance. |
| **Group: Reply mode** | A group room. RoomStore plus the request-local Job. @mentions pick up to 4 agents; otherwise a routing model call picks 1–2, defaulting to the last speaker; speakers then answer in turn. A completion judge runs on every message (R8). Stops: judge says done, round limit (default 8, max 30), repeats, stalls, 60-round ceiling, token budget, or Stop. |
| **Group: Work together** | Up to 30 turns per message. A lead plans, `assign_tasks` puts owned tasks on a board, owners do them, and `complete_task` closes them. The judge decides continuation. The plan is copied to the work record. See §4. |
| **Delegation and workers** | `delegate_to_agent`: depth 2, 6 hand-offs per message, child grant ⊆ parent, no circular delegation. Research workers: off by default, read-only, fresh context, up to 6. Output returns to the caller's message. |
| **Projects** | Attach a folder. **ProjectManager**; project trust gates Echo Code. Context: the brief, saved findings, project memory and `AGENTS.md`/`CLAUDE.md`. File tools are scoped to the project root. Outputs show in the Work gallery and Files. |
| **Artifacts** | The `create_artifact` tool. Artifact store; shown in a sandboxed frame (opaque origin) with token access. Found through the artifact library and the side panel. |
| **Routines and reminders** | Settings › Automations, or by asking Echo. **RoutineManager**, the only scheduler (60 s tick). Plain reminders never call a model; task routines run Lean as `source=routine`. Results go to the chat and the desktop feed (tray poller), and to Discord or Telegram for the owner. Stop everything holds them. |
| **Responsibilities and work** (#99) | Proposed by Echo and accepted by you in Activity. The coordination ledger owns them, with occurrences on RoutineManager. The grant is frozen at the first occurrence. Activity › Agent work shows them. **Gaps:** R1–R3, R10; owner acceptance. |
| **Creations** | The Creations page or `create_media`. The generation service and media library. Cloud providers ask first (cost). Local ComfyUI uses a pinned download. Isolated from Chat context. |
| **Memory, learning, skills** | Memory store (scoped facts); experience store (episodes, lessons with a control group); Agent Skills (SKILL.md, hash-pinned, owner-approved). Lessons and skills are advice only and never add permissions. **Gap:** R7 (two memory/name settings do nothing). |
| **Echo Code: native** | The Code tab. A thread with `surface=code`. Lean on the `/query/stream` route, with the session's model binding. Native tools and policy. |
| **Echo Code: Codex, Claude Agent, ACP** | Echo Code connection popovers. `code_sessions.py` (external identity ↔ native thread). The **original harness** runs in a detached worktree (Job Object kill-on-close) and owns its own approvals: Echo maps exact approval IDs and fails closed on unknown requests. No Echo memory, keys or tools cross over. **Gaps:** none installed here; live sign-in, approval, cancel and resume are owner tests (§8). |
| **Workbench** | Code › details, terminal, files, changes, Git/PR, usage, preview. `workbench.py`, scoped to project plus session. A ConPTY terminal over an authenticated WebSocket. Git actions need review and a writer lease. The Browser tab is a **local preview frame**, not browser automation and not your accounts. |
| **Research and Activity** | Right panel. Research notebook (per chat, 7 days); Activity holds the work, approvals and delivery controls. |
| **Settings, connections, privacy** | `config.py` / `settings.json`; Connections and MCP under Advanced; Private/Offline modes enforced in-process. See §9 and R7, R11. |
| **Channels** | Discord and Telegram bots. Owner identity is a stable numeric ID (#99). Guests get reduced tools and no owner memory. Delivery goes through the outbox. Owner-only sending; rate limits; privacy is rechecked when sending. |
| **Desktop control** | `computer_action` plus the named-control tools; screen-control approvals. See §7. **Gap:** R1/R2 can lock it after one bad argument. |
| **Browser automation** | `browse_task` and the Discord web tools (Playwright, headless). **Gap:** Chromium isn't installed by the installer (R13). |
| **Voice** | The composer and the voice stage. Local Whisper and wake word; TTS providers. Separate from Chat permissions. |

## 4. Group chats: the actual protocol

- **Who speaks and why.** @mentions win, up to 4 agents. Otherwise a routing model call
  (`_model_route`) picks 1–2, told to prefer the last speaker for follow-ups; if it can't decide,
  the last speaker answers. In Reply mode, a second speaker sees the first one's text and is told
  to add or correct, not repeat.
- **Who owns the work.** The request's Job, and its work record if work started. Work-together
  tasks have explicit owners on the board. "Done" requires `complete_task` *and* tool evidence:
  `unbacked_tasks()` reopens a task marked done with no tool call.
- **How helpers return.** `delegate_to_agent` returns the teammate's answer as the tool result;
  the caller's message is sealed first, so the chat reads A → B → A.
- **Disagreements.** There is no explicit mechanism. Later speakers can correct earlier ones, and
  the judge reads the transcript plus the tool log. The judge's `in_loop` flag counts stalls.
- **What stops it.**
  - The judge says done.
  - The round limit is reached (8; a question still stops at the limit, while work that is making progress may continue).
  - Two repeats.
  - Stall limit.
  - The 60-round ceiling.
  - Token budget (0, meaning none, by default).
  - Stop.
- **Cost.** For a plain group question: one routing call, one or two answers, and one or two
  judge calls (R8). On a single local model slot this roughly doubles or triples the wait.

## 5. Findings, prioritized

Categories: **Bug**, **Missing integration**, **UX**, **Architecture**, **Optional**. The ones
marked *Test* are reproduced by `apps/backend/tests/test_system_review_1027.py`.

| ID | Pri | Cat | Finding and evidence | Impact | Reproduce |
|---|---|---|---|---|---|
| R1 | P0 | Bug | Any exception in a tool, including argument validation where nothing ran, sets `ToolResult.uncertain=True` (`toolbox.py` ~703, ~793). The work ledger then records the action as `unknown` (`coordination.action_result`). | A 4B model's bad coordinate or bad JSON leaves a fake "uncertain effect" that needs your manual reconciliation. | *Test* `test_an_argument_error_is_not_an_uncertain_effect` |
| R2 | P0 | Bug | The ledger reserves one global resource per kind: `desktop`, or `project:<id or "unscoped">` (`coordination.action_intent`). An `unknown` or `background` action from **any** chat holds it, and `action_intent` raises `PermissionError` outside `_run_tool`'s error handling (`loop.py` ~1122). | After R1 happens once, every later desktop action, in any chat, **fails the whole turn** until you reconcile in Activity. The same applies to file writes in the same project, or in all project-less chats. | *Test* `test_a_held_resource_is_a_tool_result_not_a_crashed_turn` |
| R3 | P0 | Bug | A background process (dev server) is a `background` action holding `project:<id>`, and the same work's later writes aren't exempt. `finish` then returns `waiting_dependency` for good. | "Start the dev server, then fix App.tsx" can't be done in one request, and the work never completes. | *Test* `test_a_running_background_process_does_not_block_its_own_work` |
| R4 | P1 | Bug/UX | `needs_action` is a keyword regex (`make`, `add`, `update`, `test`…). "What should I make for dinner?" opens durable work, a ledger write, an Activity row and a "chat" delivery row. | Clutter in Agent work; more ledger traffic (R10). | *Test* (3 cases) |
| R5 | P0 | Bug | Legacy tools report failure only in text, and the toolbox treats it as a success unless it starts with a known prefix. A static scan found **93 failure messages in 36 tools**, for example `email_send` "Email send failed: …", `discord_send_channel` "Discord bot client not available.", "Path not found.", `browse_task` "Playwright is not available…", `[WEATHER_LIVE] ok=false`. | A failed send counts as succeeded, so Echo's "I've sent it" isn't challenged. This is the class fixed for desktop tools in 11.10.3, but wider. | *Test* (5 cases) |
| R6 | P1 | Missing integration | The GitHub, Google Calendar and Notion skills import `github`, `googleapiclient`/`google_auth_oauthlib` and `notion_client` inside functions. PyInstaller never sees those imports, and the latest build's module table (`PYZ-00.toc`, 26 Oct) contains none of them. | The tools appear in the installed app and fail only when called ("No module named …"). | *Test* `test_skill_libraries_are_bundled_into_the_installer` |
| R7 | P1 | UX/Bug | Settings › General › **Your name** (`user_display_name`) and Settings › Memory › **Remember conversations** (`session_memory_enabled`) are saved but read nowhere outside `config.py`. Past-chat recall ignores the toggle. | Controls that claim behaviour they don't have. | *Test* `test_your_name_and_remember_conversations_are_read_somewhere` |
| R8 | P1 | Architecture | Every group message runs the completion judge, a model call, even for a plain question with no tools or tasks (`_job_needs_closing` is true for any group). Reply mode also makes a routing call with `max_tokens=1200` for a name. | 2–3× the wait on one local slot; a misjudgement adds rounds. | *Test* `test_a_group_question_without_tools_or_tasks_skips_the_model_judge` |
| R9 | P1 | UX | The release script shows the first paragraph of the notes in the app. The v11.10.7 notes start with a pre-publication sentence. | Users are told the update "wasn't built or published". | Source: `release-windows.ps1` (notes summary); `latest.json` |
| R10 | P1 | Architecture | `coordination.json` is one JSON document, read, validated and rewritten **in full** on every transaction (`_tx`), and never pruned. Each tool call in tracked work runs several transactions (`check`, `action_intent`, `action_result`, `account`). | Cost grows with history; after weeks of use every tool call rewrites a large file. No retention policy exists. | Source: `coordination.py` `_tx`, no prune |
| R11 | P2 | UX | Settings placement. Approval mode, research workers, the outside-content reader and the step budget sit under **General**, while Permissions holds the switches and Always allowed. **Advanced** is a catch-all: Connections/MCP, webhooks, A2A, folders, documents. Echo Code runtime accounts (Codex sign-in, the Claude Agent key, ACP commands) appear nowhere in Settings, only in Code's connection popovers. | Hard to find; scope unclear. | Source: `SettingsPanel.tsx` NAV and sections |
| R12 | P1 | Architecture | Ordinary chat sends every enabled tool schema. Measured today with the default test permissions (most off): "What's the capital of Peru?" sends 32 schemas (17.6k characters) plus a 9.5k-character system prompt, about 6.8k tokens. Your 11.10.1 trace, with more permissions on: 64 tools, about 11.9k prompt tokens before the first word. Only builders on small models get `BUILDER_FOCUS` with deferred discovery. | Slow first token and worse tool choice on 4B models. | Source: `toolbox.py` focus; measured with the real `_build_turn`; trace `msg_4e4c3c51b31a` |
| R13 | P2 | Missing integration | `browse_task` uses headless Chromium that the installer doesn't download. Its message tells users to "run `playwright install`", which an installed app doesn't have. The message is also recorded as a success (R5). | Real-browser tasks fail on a fresh install. | Source: `tools.py` `browse_task` |
| R14 | P2 | Risk | No code sets DPI awareness; `mss` and `pyautogui` each set it on import, in whichever order they load. This PC runs at 96 DPI, so it's untested at 125–150 %. | Possible offset clicks on scaled displays. | *Owner* test at 150 % |
| R15 | P2 | Risk | The Codex app-server docs label the app-server command experimental, not for production. `thread/rollback` was removed (Echo doesn't call it), and approvals now include `item/permissions/requestApproval` (handled). | Protocol drift between Codex releases. | *Doc* §8 |
| R0 | P0 | Process | `main`'s **full** backend suite isn't green: 5 deterministic failures and 3 order-dependent ones (§11). #99's "279 passed" covered 17 files. Mostly outdated fixtures, but a red suite hides real regressions such as R17. | No reliable regression signal. | Full `pytest` at `1a8f4c3` |
| R17 | P1 | Bug | `POST /webhook/...` verifies the signature and builds `action_config` with `webhook_body`, then calls `run_routine(routine.id, event_id=…)` without it (`api/routes/channels.py` ~91–95). No version has ever passed the body (checked back to 11.10.1). | Webhook-triggered routines never see the payload they were sent. | Source; `git show` history |
| R16 | — | Doc | Some records predate publication: `v11.10.7.md` and `multi-agent-review.md` say nothing was published; `echo-code.md` describes the 11.10.3 baseline. | Readers can't tell the current status. | Status notes added in this pass (history kept) |

**Guards that hold today and must keep holding**, as tests:
- A write whose outcome is genuinely unknown, from a crash after intent, still blocks a blind retry.
- The desktop failure prefixes from 11.10.3 are still in the list.

## 6. Practical capabilities and integrations

| Capability | Exists and usable | Partial | Missing | Externally blocked |
|---|---|---|---|---|
| **GitHub** | Echo Code workbench: Git status, diffs, reviewed commits, `gh` PRs; the terminal `gh` | Chat skill `github_comms` (issues, PRs, comment) **not bundled** (R6) | Chat-side PR creation and review through one tool | — |
| **Documents and PDF** | Read PDF (pypdf), document search over uploads, Markdown/HTML artifacts and project docs | — | Create or edit **DOCX/PDF** natively (only via terminal Python) | — |
| **Spreadsheets** | Read CSV through files | — | XLSX read/write tool | — |
| **Presentations** | HTML artifacts can present | — | PPTX creation | — |
| **Calendar** | Reminders and routines (local) | Google Calendar skill **not bundled** (R6); OAuth needs a client secret file | Outlook/Microsoft 365 | Google OAuth app verification for general distribution |
| **Browser** | Read-only page fetch (`safe_web_fetch`) and the research notebook; workbench preview | `browse_task` headless, missing Chromium (R13) | Visible, user-supervised browser automation | Your real signed-in accounts: needs your own browser with your consent |
| **Windows apps** | Named controls (UIA), coordinate control with change detection and stall notes, `open_application`, Blocked apps | DPI (R14); ledger lock (R1–R2) | App-specific APIs (Office COM, for example) | Games that block synthetic input |
| **Visual explanations** | Mermaid, charts and HTML artifacts | — | — | — |

**Recommendation:** finish one end-to-end workflow before adding new connectors. The best
candidate is **GitHub issue → fix → PR from Chat**, reusing the Echo Code worktree and Git review
flow. Documents (DOCX/PDF/XLSX) come second, as native tools with render-and-verify. Every new
tool must return a real failure outcome (R5).

## 7. Windows controller: proposed architecture

The current design already pairs screenshots with named UIA controls, measures screen change and
nudges a stalled model. What's missing is one controller that owns identity, freshness and focus:

1. **Target resolution:** a window handle plus process ID plus title, fixed for the task. Refuse to act if the foreground window changed; re-acquire it instead.
2. **Semantic first:** for each step, prefer an app API (Office COM, browser DevTools, `gh`) over UIA `Invoke`/`ValueSet` patterns, and those over keyboard shortcuts, and those over a coordinate click. Coordinate clicks are the bounded fallback for drawn interfaces (games, canvases).
3. **Observation freshness:** one observation ID per step. Actions run against it only if the window rect, DPI scale and foreground identity still match. 11.10.3's two-minute and window-moved checks already cover part of this.
4. **DPI:** declare per-monitor-v2 awareness once at backend start; record the scale in each observation and convert in one place.
5. **Verify every action:** the expected change (control state, text, window) or a measured screen delta, then the stall rules: a 3-look note, and a 2-no-effect note.
6. **Cancellation and limits:** keep the existing 5 s / 10 s caps; release held keys and buttons in `finally`; Stop cancels mid-batch.
7. **Ledger integration:** desktop actions reserve `desktop:<window>` rather than one global `desktop`, and a validation error never becomes `unknown` (R1–R2).

## 8. External coding runtimes: official contracts (checked 27 Oct 2026)

- **Claude Agent (SDK).** *Doc:* the Agent SDK overview says: *"Unless previously approved, Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products, including agents built on the Claude Agent SDK. Use the API key authentication methods…"*
  - **Supported:** EchoSpeak's explicit API key (`ECHOSPEAK_CLAUDE_AGENT_API_KEY` or a key for that connection). It's correctly labelled "Claude Agent", as the branding guidance asks.
  - **Not available without Anthropic's approval:** using your Claude subscription inside EchoSpeak. This isn't a beta wait.
  - **Separately permitted:** running the **unmodified Claude Code CLI** with your own login. Wiring that into EchoSpeak, via ACP or a terminal, would need a fresh look at the terms first.
  - Claude Code isn't installed on this PC.
- **Codex app-server.** *Doc:* stdio JSONL JSON-RPC. `initialize` then `initialized`; `thread/start|resume`, `turn/start|steer|interrupt`, `model/list`; approval requests including `item/permissions/requestApproval`. `thread/rollback` was **removed**. The docs label the app-server command experimental, and generated schemas match the Codex version that produced them.
  - **Echo's adapter** uses `thread/start`, `thread/resume`, `turn/start`, `turn/interrupt`, `model/list`, `account/read` and `account/login/start`.
  - **Approvals** go to the three `item/*/requestApproval` handlers.
  - **Fails closed:** it refuses `account/chatgptAuthTokens` and unknown requests.
  - **Rollback:** no `thread/rollback` call; rollback is Echo's own file/index restore.
  - **Not here:** the Codex CLI isn't installed on this PC. Re-validate the schemas against your installed Codex version before relying on it.
- **ACP.** *Doc:* the v1 pages. Baseline is `initialize`, `session/new`, `session/prompt`, `session/request_permission` and `session/update`; `session/load` needs the `loadSession` capability. Echo advertises no filesystem or terminal capabilities and refuses resume when it isn't supported, which is consistent.

## 9. Settings: what controls what

| Area | Where it is now | Enforced by | Issue |
|---|---|---|---|
| Native Chat models | Models (local/cloud), composer picker per chat | provider settings, `SessionModelBinding` | OK |
| External coding accounts | Echo Code connection popovers only | the runtimes themselves | Not discoverable in Settings (R11) |
| Agent identity and toolsets | Agents | PersonaStore, Toolbox | OK |
| Approvals and Autonomous | **General** › How agents work | `settings.approval_mode()`, loop | Belongs with Permissions (R11) |
| Tool switches, folders, Blocked apps, Always allowed | Permissions | toolbox flags, `_file_tool_roots`, approvals | OK |
| Privacy and network | Privacy | `privacy.py`, backstop | OK |
| Desktop and browser control | Permissions switches | policy, `computer_ready` | Browser automation lacks Chromium (R13) |
| Memory, learning, skills | Memory (two dead controls, R7), General (learning), Skills | memory, learning, `agent_skills` | R7 |
| Responsibilities, reminders, delivery | Automations, Channels, Activity | RoutineManager, coordination, outbox | Responsibilities are reviewed in Activity, not Settings: say so in Automations |
| Your name | General | nothing (R7) | R7 |

Proposed order, without a redesign:
- **Models** › Chat models, plus a new "Coding runtimes" page that lists Codex, Claude Agent and ACP connection states with links into Code.
- **Agents.**
- **Permissions** › approvals and Autonomous moved here from General, with the switches, folders, apps and Always allowed.
- **Privacy.**
- **Memory and learning:** fix or remove the two dead controls.
- **Skills.**
- **Automations:** routines, reminders, responsibilities (pointing to Activity), heartbeat.
- **Channels.**
- **Connections:** MCP, A2A and webhooks out of Advanced.
- **Advanced:** genuinely low-level items only.

## 10. Phased plan (small, one workflow at a time)

| Phase | Scope | Exit criterion (observable) |
|---|---|---|
| **0: Truthful outcomes and the work ledger** (P0) | R0: make the full suite green (update the four outdated fixtures, settle the heartbeat delivery expectation, isolate the order-dependent tests) and keep it as the merge gate. R17: pass the webhook body. R1: only lost receipts or timeouts are uncertain. R2: a held resource becomes a refused tool result, never a crashed turn, and is scoped to `desktop:<window>` / `project:<id>` with same-work exemption (R3). R5: a shared failure classifier for legacy tool strings. R9: release-notes summary. Keep the unknown-write guard. | The R1–R3 and R5 tests pass (markers removed); the full suite is green; the owner checklist A1–A4 passes on the installed app |
| **1: Ordinary chat is light** (P1) | R4: work only on a real mutating tool, not keywords. R8: deterministic judge skip, and a routing call with a small token cap. R12: Chat focus mode for local models (core tools, then `find_tools`). R7: wire or remove the two settings. R10: a retention window and per-record files or SQLite for the ledger. | R4, R7 and R8 tests pass; first-token prompt for "hello" under a measured bound on your model; Activity shows only real work |
| **2: One practical workflow** | GitHub issue → fix → PR from Chat, through the Echo Code worktree, plus R6 packaging (hidden imports and a packaged import self-check). | R6 test; owner runs the flow end to end on a test repo |
| **3: Windows controller v2** | §7, steps 1–7, with DPI (R14). | Owner checklist D1–D4 at 100 % and 150 % scaling |
| **4: Settings and documents** | §9 structure; DOCX/XLSX/PDF tools with render-and-verify. | Each control maps to an enforced key (scripted check); document round-trip tests |

## 11. Tests run in this review, and the owner acceptance checklist

**Run (scripted, no live model):**
- **Backend suite on `main`:** 1397 passed, **7 failed, 1 error**, 22 skipped. The failures, rerun in isolation:
  - Three pass alone, so they depend on run order: both `test_code_sessions` tests and `test_unattended_approval_survives_restart_and_resumes_in_another_chat`.
  - Five fail deterministically (R0). Four of them are fixtures that #99's new call contracts outdated: `work_context` in `test_release_integration` (×3) and `event_id=` in `test_signed_webhooks_run_the_routine`.
  - `test_heartbeat_discord_route_delivers_only_to_owner` now gets no delivery, because the outbox requires the Discord channel switch and passes `on_receipt=`.
  - Reading the webhook route exposed R17.
- **New review tests:** `tests/test_system_review_1027.py`, 2 passed and 14 expected failures. They fail for the stated reason: an argument error *is* recorded as uncertain; a held resource *does* raise out of the turn.
- **Web:** typecheck plus 192 tests.
- **Desktop contracts:** 16.
- **Rust:** 4.
- **Failure-string scan:** static scan of `agent/tools.py`.
- **Release:** `latest.json` signatures decoded.

**Owner acceptance checklist.** Use your installed 11.10.7 and the model you normally use.

| # | Do | Expected |
|---|---|---|
| A1 | New chat: "What's the capital of Peru?" | One answer, no tool rows, nothing new in Activity › Agent work |
| A2 | "What should I make for dinner?" | An answer. *Today a work row appears (R4).* |
| A3 | "Save a note called test.md that says hi" | Tool row "done", the file exists; one completed work row |
| A4 | Settings › Permissions, Control the desktop on: "Click at 9999, 9999 on my screen" | The tool row fails. *Today the next "take a screenshot and click the Start button" in a **new** chat may fail the whole turn with "Resource is held…" (R1/R2). If so, open Activity, reconcile, and note it.* |
| A5 | In a group with Jarvis and GLaDOS: "What's your favourite movie?" | One or two short answers. Note the total time. *Today the judge adds a call (R8).* |
| B1 | Settings › General › Your name = "Ty"; new chat: "What's my name?" | *Today Echo doesn't know it from this setting (R7).* |
| C1 | Set up GitHub in Channels/skills, then ask Echo "list my open issues in EchoSpeak" | *Today: "No module named 'github'" in the installed app (R6).* |
| D1 | Code tab → Codex (after installing Codex and signing in) → a tiny task in a test repo → deny one approval → stop → resume | The session behaves as described in §8; record what happens. |
| D2 | Display scaling 150 %: take a screenshot and click a named button | The click lands on the button (R14) |

## 12. Sources (checked 27 Oct 2026)

- Repository and release: `git rev-parse`, `gh release view v11.10.7`, the published `latest.json`.
- [Claude Agent SDK overview: authentication note and branding](https://code.claude.com/docs/en/agent-sdk/overview)
- [Codex app-server protocol](https://learn.chatgpt.com/docs/app-server)
- [ACP v1 overview](https://agentclientprotocol.com/protocol/v1/overview)
- Earlier records reconciled: [multi-agent-review.md](multi-agent-review.md), [echo-code.md](echo-code.md), [model-ecosystem.md](model-ecosystem.md), [computer-control-2026-10-25.md](computer-control-2026-10-25.md), [security-audit-2026-10-25.md](security-audit-2026-10-25.md), [TESTING_GUIDE.md](../TESTING_GUIDE.md).
