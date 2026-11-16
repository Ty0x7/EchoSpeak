# Harness-first: one assistant, two clients, Linux

> **Status, 10 November 2026 UTC.** Research, architecture decision and the first foundational
> changes on branch `harness-first-rearchitecture`, stacked on `codex/code-inbox` (11.13.1, PR #114).
> Nothing here is merged, released or accepted by the owner. Every claim below is labelled
> **implemented**, **tested**, **recommended** or **needs owner acceptance**.
>
> [Visual summary](../../artifacts/research/harness-first.html) ·
> previous review: [echospeak-architecture.md](echospeak-architecture.md) (30 Oct) ·
> Echo Code record: [echo-code.md](echo-code.md)

Workflow: **Objective → Research → Environment → Verification → Adaptation → Completion.**

## The decision

EchoSpeak becomes **one assistant (Echo) on one native harness**, reached through **two clients**
(the graphical app and a terminal), on **Windows and Linux**. Three changes of direction:

1. **Chat / Code / Agents stop being three applications.** They become *capability profiles* chosen
   from facts about a conversation. The owner's own conversation with a **trusted project
   attached** gets engineering tools; without one, it converses. A conversation with an agent
   stays with that agent. Tabs no longer decide behaviour.
2. **EchoSpeak stops hosting Codex, Claude Code and ACP agents.** They keep running beside it, in
   their own apps (Omarchy + tmux). The workbench parts that were built alongside them (terminal,
   files, Git review, PRs, scripts, worktrees) stay, because Echo uses them too.
3. **The terminal is a second client of the same backend.** It is not a second engine. The FastAPI
   process already owns runs, history, approvals and cancellation, so the terminal only needs a
   way to find it and an HTTP client.

None of this replaces the loop. The audit found the native harness to be the strongest part of
the codebase; the cost sits around it.

## 1. What EchoSpeak is today (source audit)

Evidence: source at `0567f94`, a Linux test run of the full backend suite, and a live boot of the
backend on this machine. Line counts are from `wc -l`.

**Strong, keep as is.**

| Capability | Where | Why it is strong |
|---|---|---|
| One native loop for every source | `agent/lean/runtime.py` (`LeanSession`), `loop.py` (`LeanTurn`) | App, voice, Discord, Telegram and routines all enter `run_lean_query`. Errors, denials and bad arguments return to the model as tool results. Stops are explicit (`max_steps`, cancel, promise guard). |
| Runs owned by the backend | `agent/query_journal.py`, `api/routes/chat.py:519` | A detached collector writes every event to SQLite. HTTP readers can detach and reattach by cursor (`_replay_seq`). A restart marks runs `interrupted` and never replays tools. This is already DeepSeek's and Hermes's model. |
| Safety in code, outside the model | `lean/policy.py`, `lean/approvals.py`, `lean/capabilities.py` | Taint tracking (Rule of Two), switches checked again when a tool runs, secrets refused in arguments, every decision written to `security/tool-audit.jsonl`. |
| Evidence-based completion | `lean/evidence.py`, `lean/verification.py`, `lean/job.py` | A tool that ran is not a task that is done. Checks count only after the last change. |
| Learning that has to earn its place | `agent/learning/` | Lessons are advisory. Each lesson is randomly held back from some matching tasks as a control and retired if it does no better than that control. This is stricter than Hermes, ReasoningBank or ACE. |
| Local-model support | `lean/provider.py`, `lean/toolbox.py` focus sets | Repairs text-formatted tool calls (Gemma, pythonic, pipe and Liquid formats), shows a smaller set of tools up front with `find_tools` for the rest, and sizes context from metadata. |

**Weak or over-built.**

- **Environment branching is concentrated in one class.** `LeanSession` (2,218 lines) holds the
  Chat, Code and Agents rules in one place (`runtime.py:175–181`, `:1046–1260`). This change does
  not add a fourth branch: unified mode chooses between the two existing profiles.
- **Hosting external harnesses.** The backend code for it is about 3,900 lines:
  - runtime and accounts: `code_runtimes`, `code_accounts`, `code_executables`, `code_limits`,
    `code_usage`, `code_claude_hook`;
  - the supervisor `code_sessions` and the Echo Code routes;
  - the optional `claude-agent-sdk` requirement.

  On the web side it covers `code/ExternalSession.tsx`, `RuntimeUsage`, `usageRefresh` and
  `settings/CodingAgents.tsx`. It reproduces another product's experience and carries vendor
  authentication and terms obligations (see `echo-code.md` §11.10.9).
- **Windows coupling.** There are about 160 `os.name` / PowerShell / `ctypes.windll` branches in 27
  backend files. The credential store required DPAPI, so on Linux no API key, MCP sign-in or A2A
  key could be saved at all (fixed here, §9).

## 2. DeepSeek Harness — what transfers

Source studied: `deepseek-ai/deepseek-harness` at `fcc0ad46`, version `0.2.2-rc.1`, MIT licence.
Shallow clone, read-only, nothing executed.

The org identity is DeepSeek's, but GitHub's API reports `is_verified: false`, and the docs live
under a separate Pages org. It is TypeScript built on a vendored Cordis. There are about 370
packages and about 557k lines of non-test code. Every release is a prerelease, the README warns of
breaking changes, and `SAFETY.md` says it has had no security audit.

| Principle | DeepSeek mechanism | EchoSpeak today | Adopt? |
|---|---|---|---|
| Controls are requests; their effects are durable events | `cancel` RPC fires an abort; the log records `turn/end {reason: aborted, user}` | `/query/cancel`, `/query/steer`, `/query/queue` already exist. The cause is recorded as `stop_reason`. | **Already have.** Keep the cause enum closed. |
| Follow from a cursor: snapshot, gap-free entries, then live | `follow(sessionId, lastSeq)` over a multiplexed WebSocket | `/query/runs/{id}/events?after=N`, per *run* | **Recommended:** a per-*session* follow endpoint, so a second client sees a run another client started without polling `/query/runs`. |
| Asked/decided pairs, fail-closed `unavailable` | `approval/asked` → `approval/decided`, closed outcome set | Broker plus `approval_request` / `approval_resolved` events. Sources with no UI are refused. | **Already have.** |
| "Model-visible means logged" | Every request is rebuilt from the log, including prompt versions | The prompt is rebuilt every turn and not logged. Chat traces record only section sizes (`chat_mode.build_prompt(sizes=…)`). | **Recommended (S):** log a hash of the prompt and the tool set per step, so a run can be explained after the fact. |
| Crash-repair vocabulary | `TOOL_OUTCOME_UNKNOWN` vs `TOOL_NOT_STARTED` | Restart marks runs `interrupted`. Effects are reconciled through the 11.10.7 work ledger. | **Already have, in substance.** |
| Sandbox honesty | `bwrap` then Landlock; fails closed; reports `full` or `partial` | Optional Docker or the host shell. Approval patterns are not confinement. | **Recommended (M):** bubblewrap for host commands on Linux, reporting whether confinement actually applied (§10). |
| Cordis, YAML patch layers, decorator-generated RPC | — | — | **No.** These fit its runtime, not a Python service. Pydantic and OpenAPI already describe the contract. |

Limits seen in the source:
- the SDK protocol has no cancel or steer request;
- pending approvals are kept only in memory;
- approvals are one-shot, with no "always allow";
- there is no TUI in the tree.

Echo is ahead on approvals (scoped grants), budgets and learning evaluation.

## 3. Hermes Agent — what transfers

Source studied: `NousResearch/hermes-agent` at `d5250190` (9 Nov 2026), Python. Read-only.

The live architecture page is out of date: at HEAD, the CLI, TUI and desktop all attach to a gateway
daemon for each profile.

| Principle | Hermes mechanism | EchoSpeak today | Adopt? |
|---|---|---|---|
| One session authority; every interface is a viewer | The gateway owns execution. Clients call create (idempotent), submit (admission receipt) and resume. Open approvals are replayed when a client attaches. | The FastAPI backend owns runs. `client_request_id` makes a resubmit idempotent. The journal allows one running request per session. | **Already have the core.** The terminal client uses it as is (§8). |
| Declare the wire once | Pydantic `extra="forbid"` contracts, generated TS, a staleness test | Hand-written TS types in `lean/types.ts` | **Recommended (S–M):** generate the event-type union from the backend. |
| Byte-stable prompt prefix | Memory snapshot frozen per session; mid-session changes go in user or tool rows | Chat orders its sections for prefix caching. The Agents/Code prompt mixes recall into the prefix. | **Recommended (S):** apply Chat's ordering to the engineering prompt. |
| Hardline floor under every mode | `HARDLINE_PATTERNS`, checked before every approval mode | Had none. The `dangerous_command` list was PowerShell-centred. | **Implemented (`4e63fcc`):** `approvals.catastrophic_command`, see §10. |
| Autonomous skill creation | A background review fork every N turns | Owner-reviewed skills from lessons | **No.** No evaluation in Hermes, and published evidence is negative (§4). |

Hermes is about 1.07M lines of non-test Python. Its memory is limited by character count, not by
relevance. Background delegation does not survive a restart. Its dangerous-command check is a regex
that containers bypass.

## 4. Evidence on learning and context

Primary sources were read through a fetch tool, so the numbers were not hand-checked against the
papers' tables.

- **Reflexion** (arXiv 2303.11366): helps only when the same task is retried with a reliable
  evaluator. It made MBPP *worse* when the self-written tests were wrong (16% false positives).
- **ReasoningBank** (2509.25140): +3 to +8 points on WebArena and SWE-bench Verified, using
  strategy memories distilled from both successes and failures by an LLM judge. Single lab;
  retrieval and consolidation are deliberately simple.
- **ACE** (2510.04618): incremental delta updates with helpful/harmful counters. A monolithic
  rewrite collapsed the context from 18k to 122 tokens and fell *below* the no-adaptation baseline.
  The gains need reliable feedback.
- **SkillsBench** (2602.12670):
  - curated skills: +16 points;
  - self-generated skills: −1.3 points on average;
  - 2–3 focused skills did best; "comprehensive" skills hurt.
- **Evaluating AGENTS.md** (2602.11988, abstract only): LLM-generated context files gave no gain
  at more than 20% extra cost.

**Consequence for Echo.** The current design is right: advisory lessons, outcome-graded episodes,
randomized holdout, and owner review for anything untrusted. Do not add autonomous skill writing
or memory written at every turn. Run the existing `scripts/eval_learning.py` ablation before
widening learning (ROADMAP §1.2, still open).

## 5. Retiring the T3-inspired direction

The dependency map was read from imports at `0567f94`. The key finding is that the workbench is
**not independent** of the external-harness supervisor:

- **The supervisor runs the workbench.**
  - `api/routes/workbench.py:21` runs every workbench method on `code_sessions.manager().call`.
  - `agent/workbench.py` calls `supervisor.admit()` at 8 sites.
  - The supervisor's monitor re-admits open workbench terminals every second.
- **The workbench reads account data.** `agent/workbench.py:493` imports `code_accounts.remembered`,
  and `:522` imports `code_diagnostics`.
- **Other modules import the supervisor or its stores.**
  - `agent/state.py:1890` imports `code_inbox`.
  - `agent/attention.py:133` imports `code_accounts`.
  - `lean/stop.py`, `api/server.py` (shutdown) and `routes/settings.py` (privacy transition) import
    `code_sessions`.

Deleting the external runtimes first would break Echo's own terminal. The order is therefore:

| Component | Decision | Note |
|---|---|---|
| `code_runtimes`, `code_claude_hook`, `code_executables`, `code_limits`, `code_usage`, `requirements-code-runtime.txt` (Claude Agent SDK) | **Remove** after phase 3 | Has no reason to exist once nothing external is launched. |
| `code_accounts` | **Remove** after the workbench and attention imports are cut | Echo never stored vendor tokens, so there is no credential to migrate. |
| `code_sessions` (external supervisor) | **Split**: move the asyncio executor, process-group ownership and privacy/stop admission into a neutral workbench executor, then remove the external part | This is the dependency that blocks everything else. |
| `code_process`, `code_worktree` | **Keep** (rename later) | A process helper and Git worktrees are useful for native isolated work. |
| `workbench` (PTY terminal, files, diff, Git review/commit/push, `gh` PRs, scripts) | **Keep**, as the conversation's right panel | Linux terminal tested (§7). |
| `code_inbox`, `code_drafts`, `code_tasks`, `code_prs`, `attention` | **Generalize** to every conversation | Background work, stashed drafts and "needs you" apply to all of Echo, not just Code. |
| `control_mcp` (scoped local MCP grants) | **Keep, re-evaluate** | This is the *right* bridge for Codex or Claude Code running beside Echo: they call Echo; Echo does not host them. |
| Web: `ExternalSession`, `RuntimeUsage`, `usageRefresh`, `settings/CodingAgents`, runtime logos in `CodeModelPicker`, the Usage tab's subscription windows | **Remove** with the backend part | Other workbench tabs stay. |
| Stored state: `<data>/echo-code/<id>/` (session.json, worktrees, checkpoints) | **Preserve, read-only** | Never deleted automatically. Diffs remain inspectable. Removal is explicit, one session at a time. |

## 6. One graphical application (recommended structure)

The current shell (at `0567f94`) partitions everything by environment. `app/environments.ts` puts
pages in exactly one environment:
- Chat gets Projects, Creations and Artifacts;
- Agents gets Group chats and Routines;
- Code gets its own project tree.

So a project shows up in two disconnected places, and coding means switching applications.

**Proposed sidebar:**

1. **New conversation** — always with Echo.
2. **Conversations** — every owner conversation. A project badge marks the ones that can engineer.
   Search and filter by project.
3. **Agents** — Echo's teammates as contacts, plus group chats. Opening one shows its conversation,
   with identity, tools, model, responsibilities and routines a click away.
4. **Projects** — folders. Opening one lists its conversations, files and changes, and starts a new
   conversation already attached.
5. **Library** — Artifacts, Creations, Routines, Learning and Memory.

**Conversation view:**
- The composer gains a **project chip**: attach, detach or change the project. That chip is the only
  "mode switch"; it changes the next turn's profile.
- The right panel keeps the existing workbench tabs. Files, Terminal and Changes appear only while a
  trusted project is attached. Research, Activity, Artifact and Screen stay as they are.

**Mapping from today:**

| Today | Proposed |
|---|---|
| Chat tab | Conversations |
| Code tab, native session | Conversation with project attached |
| Code tab, Codex / Claude / ACP session | Read-only history under the project. Not launchable. |
| Agents tab | Agents |
| Projects page (Chat only) + Code project tree | Projects (one place) |
| Settings › Models › Coding agents | Removed. A note points to running them beside EchoSpeak. |

**Not implemented here** (needs owner acceptance and a UI pass with screenshots). The backend
already sends the per-conversation profile, so the GUI change is mostly navigation.

## 7. Chat and coding in one assistant (implemented, opt-in)

**Implemented and tested.** `ASSISTANT_MODE=unified`; the default `environments` keeps 11.13
behaviour.

- `agent/environments.py` `turn_environment`:
  - owner, live, not resuming agent work, trusted project attached → `"code"` (engineering
    profile);
  - otherwise → `"chat"`;
  - agents, routines, channels, guests and resumed work → `"agents"`, unchanged.
- An untrusted, missing or archived project never grants engineering tools.
- Echo answers in both profiles (`chat_mode.identity_text`, `code_mode.identity`,
  `personas.chat_persona` / `code_persona`), naming the model it runs on.
- Permission switches, approvals, the policy check and Private/Offline mode are untouched. A profile
  only removes tools; it never adds authority.
- `code_sessions.admit_external()` refuses to create, connect, sign in, prompt or keep running any
  external harness. `echo_code.runtime_catalog()` lists only Echo.
- Echo's own workbench still uses the plain `admit()` gate.
- The first version of this change gated *every* supervisor call. That would have blocked Echo's own
  terminal and let the monitor close open terminals every second. It was found by running the
  workbench suite with unified mode on, and fixed in `f3a56c1`.

**Tests:**
- `tests/test_unified_assistant.py` (6 tests);
- the environment, native Code, external-session, Echo Code, workbench, Claude runtime, parallel
  Code and inbox suites: 126 passed, 3 skipped, with the default mode;
- with `ASSISTANT_MODE=unified` set for the whole process, every Echo-owned workbench test passes
  (PTY terminal, terminal socket, Git review and commit, scripts, Private/Offline).

The tests that fail in that unified run assert the old three-environment contract or create
Codex/ACP sessions. Both are expected. They become phase-4 deletions or rewrites.

**Open design question (owner).** In unified mode Echo's conversation profile has no delegation (the
team tools stay with Agents). A follow-up could allow explicit `@agent` hand-offs from Echo's
conversation. It is left out until measured, because routing calls cost a model call per turn on
local models.

## 8. Graphical and terminal clients on one harness

The backend already behaves as the session authority:
- `/query/stream` claims a run for a session; a second concurrent run is refused with 409;
- `client_request_id` makes a retried submit idempotent;
- the collector is detached, so a reader can disconnect;
- `/query/runs/{id}/events?after=` replays from a cursor;
- cancel, steer, queue and approvals are separate requests that any authenticated client may send.

The terminal client is built on exactly those calls.

**Terminal client — implemented and tested** (`apps/terminal/`, `5beecd2`). It uses the Python
standard library only, about 1,575 lines. Command: `apps/terminal/echospeak`.

- **Finding the backend.** Precedence is `--url/--key` flags, then `ECHOSPEAK_URL`/`ECHOSPEAK_KEY`,
  then the endpoint file.
  - The desktop app's backend (and `echospeak serve`) publishes its address and per-launch key in
    `$XDG_RUNTIME_DIR/echospeak/endpoint.json` (0600 file, 0700 folder) on POSIX only
    (`agent/endpoint_file.py`, published from the lifespan in `api/server.py`).
  - Plain-http URLs to other machines are refused unless `--insecure-remote` is given.
- **Commands.** `sessions`, `resume`, `new [--agent] [--project]`, `attach <session> [--after N]`,
  `send` (pipe-friendly, `--json` for raw events, exit 0/2), `status`, `stop [--all]`, `agents`,
  `approvals`, and `serve`, which starts a backend with a fresh random key in the environment
  (never on argv). The REPL slash commands mirror these.
- **Behaviour.**
  - Output is printed inline: no alternate screen, no curses. tmux scrollback, copy-mode,
    `capture-pane` and plain pipes all work. tmux is not a dependency.
  - Every submit carries a stable `client_request_id`, so a retried POST reattaches instead of
    starting a second run.
  - Streams reconnect from the last cursor.
  - Ctrl-C mid-run cancels the run and keeps the REPL. Detaching leaves the run going on the backend.
  - Typing mid-run steers the run, or queues the message if steering is refused.
- **Approvals and safety.**
  - Approvals are shown inline with the exact arguments (as JSON). The offered decisions are exactly
    the ones `/lean/approvals/{id}` accepts.
  - A non-TTY client never answers an approval. It reports where to answer and exits non-zero.
  - Server text is sanitised: escape sequences (CSI, OSC including OSC 52 and title changes, DCS),
    every C0/C1 control including a lone ESC, bidi overrides, and carriage-return overwrites.
- **Tests: 29 passed**, re-run independently.
  - Unit tests: renderer, sanitiser, discovery precedence, cursor reconnect, approval prompts.
  - An integration test runs the real `app.py --mode api` with auth on and a scripted local
    OpenAI-compatible model. It checks:
    - 401 without the key;
    - the endpoint file's mode and pid;
    - create → send → streamed tokens → `/history` → `attach` replays the same text from the journal;
    - an idempotent resubmit;
    - `send` in a pipe and `--json`;
    - the file is removed on SIGTERM.
- **Manual checks by the building agent:**
  - `serve`, `status`, `sessions` and `agents` found the backend through the endpoint file.
  - In a REPL under a real PTY: sending, switching agent, steering mid-run, Ctrl-C cancel, and
    `attach` to a run started by another client (curl).
- **Not verified:** a real tool approval end to end, a real dropped connection, detaching mid-run,
  the queued follow-up path, the packaged desktop sidecar writing the file, and macOS/Windows.

**Backend gaps found while building it** (recommended follow-ups):
1. `agent_id` on a plain chat thread is ignored (`agent/lean/runtime.py:180`, `:360`). The client
   opens a direct room for `--agent`, as the Agents view does.
2. There is no backend-wide list of running runs. `GET /query/runs` needs `thread_id` and returns at
   most 10 (`api/routes/chat.py:588`, `agent/query_journal.py:89`).
3. There is no per-session follow stream (see below).
4. Queued follow-ups need a client to call `/query/queue/claim` (`chat.py:217`). Nothing drains them
   when no client is attached.
5. `/query/*` GETs count against the 240/min mutation budget (`api/server.py:498`). Reconnects and
   polling consume it.
6. Replayed `approval_request` events don't say whether the approval is still pending. The client
   checks `GET /lean/approvals` first.
7. `serve` sets `ECHOSPEAK_RUNTIME_KIND=desktop`, because that is what makes config enforce host auth
   over saved settings (`config.py:1254`). A dedicated "terminal" runtime kind would be clearer.


**Further multi-client work (recommended):**
1. **Per-session follow.** One stream of every run in a session, so the GUI shows a run the
   terminal started (and the reverse) without polling.
2. **Approval answer attribution.** Record which client answered, in the audit.
3. **Generated event schema** shared by the web reducer and the terminal renderer.

## 9. Linux and Omarchy

Verified on this machine: Omarchy, Linux 7.2, Hyprland on Wayland, Python 3.12 venv, Node 22.

| Area | State |
|---|---|
| Backend | **Tested.** Boots in about 1 s with an isolated data folder. Readiness 15/15 (degraded only for no model or embeddings configured). `/threads` returns 401 without the key and 200 with it. Host shell is detected as `bash`. |
| Test suite | **Tested.** Baseline 1,596 passed / 27 failed, plus a pytest crash: a test fakes `os.name = "nt"`, which breaks `pathlib` on Linux. Now Windows-only tests skip with a reason (launcher, desktop control, a Windows-path fixture). The stale policy test noted on 30 Oct still fails. |
| Credentials | **Implemented and tested.** `agent/credential_posix.py`: AES-256-GCM under a 32-byte key held in the Secret Service (`secret-tool`, GNOME Keyring here), one key per data folder. The id is bound as associated data. Files are 0600 in a 0700 folder. It fails closed with instructions when there is no keyring. `ECHOSPEAK_CREDENTIAL_KEYFILE=1` is an explicit 0600 key-file fallback, labelled honestly. 7 unit tests plus one real keyring round trip (its test key removed afterwards). DPAPI ciphertext copied from Windows is reported, never guessed. |
| Workbench terminal | **Tested.** POSIX PTY input/resize/stop and the authenticated terminal socket pass. |
| Tauri shell | **Not built here.** Rust is not installed. `webkit2gtk-4.1`, `libayatana-appindicator` and `librsvg` are present. Undecorated windows and the custom titlebar need a check under Hyprland (tiling ignores some window hints). The updater and release scripts are NSIS/PowerShell only; Linux distribution should be a PKGBUILD (fits Omarchy) or AppImage. |
| Desktop control, app launcher | Windows-only today (`user32`, Start-menu, Store). The Linux path would be Wayland-native (`grim` for capture, `hyprctl` for windows). Input injection on Wayland needs `ydotool`/uinput, which is a privileged design decision. |
| Voice | Windows SAPI unavailable; Piper and faster-whisper are cross-platform. Not tested here. |
| Data location | Dev defaults to `apps/backend/data`. The terminal's `serve` uses `${XDG_DATA_HOME}/echospeak`. **Recommended:** the same default for a packaged Linux build. |

## 10. Security

- **Unified mode does not widen authority.** Tests assert that untrusted projects get no
  engineering tools, that switches still decide, and that agents, guests and routines are
  unchanged.
- **Host commands on Linux are not confined.** The terminal tool runs as the user. Approval patterns
  and the policy check are application-level controls, not isolation.
  - **Recommended (M):** run host commands through `bwrap`: read-only root, writable project, no
    network unless allowed. Report `confined: full/partial/none` to the model and the user, as Codex
    and Claude Code do.
  - Never run the backend as root.
- **Command floor (implemented, tested).** The dangerous-command list was written for PowerShell. On
  Linux, `mkfs`, `dd` onto a disk, `wipefs`, `chmod -R … /` and a fork bomb were not recognised, so in
  the **default** mode they ran on the host without asking. `approvals.catastrophic_command` now
  covers those and recursive deletes aimed at `/`, the home folder or a system folder (and the
  Windows equivalents). They:
  - always ask, even with approvals set to "never";
  - can never be covered by "Always allow" or autonomous mode.

  Ordinary deletes (`rm -rf build`, `rm -rf /tmp/x`) are unaffected.
  `tests/test_command_floor.py` (42 cases); approval, terminal and security suites: 170 passed.
- **Endpoint discovery file** (terminal client):
  - holds the per-launch key in a 0600 file inside a 0700 `$XDG_RUNTIME_DIR/echospeak` folder, only
    on POSIX;
  - is refused if it is a symlink, owned by someone else, or readable by others;
  - adds no new exposure, since a same-user process can already read `/proc/<pid>/environ`;
  - Windows behaviour (key never persisted) is unchanged.
- **Terminal escape injection.** Model and tool text is untrusted. The terminal client strips
  CSI/OSC sequences (including OSC 52 clipboard writes and title changes) before printing.
- **Credentials of other tools** (Codex, Claude, GitHub CLI) are never read. Identity VMs and
  unrelated sessions were not touched.

## 11. Sequence and acceptance criteria

| Phase | Work | Done when |
|---|---|---|
| 0 ✅ | Linux credential store; Linux test baseline; unified mode (opt-in); external gate; command floor | Implemented and tested (this branch) |
| 0 ✅ | Terminal client and endpoint discovery | Implemented and tested (§8) |
| 1 | Neutral workbench executor out of `code_sessions`; cut the `code_accounts` and `code_diagnostics` imports from workbench and attention | Workbench suite passes with `code_runtimes` not importable |
| 2 | GUI: sidebar and project chip behind `assistant_mode` (§6) | Screenshots of both modes at 1400×900 and 960×640; web tests; owner walkthrough |
| 3 | Owner acceptance; flip the default to `unified` | One local-model repair and one cloud-model repair in a project conversation; reconnect from the terminal mid-run; approval answered from the other client |
| 4 | Delete the external-runtime code, UI and SDK requirement (§5); keep stored sessions read-only | Full suites green; old data folder opens; no stored session lost |
| 5 | bubblewrap confinement for host commands; per-session follow endpoint; Tauri Linux build + PKGBUILD | Confinement reported truthfully; GUI and terminal on one session at once; installed app opens under Hyprland |

## Verification record

| Check | Kind | Result |
|---|---|---|
| Full backend suite, Linux, stack tip, before changes | automated | 1,596 passed, 27 failed, 21 skipped (one test deselected because it crashes pytest) |
| Full backend suite after changes | automated | **1,659 passed, 2 failed, 51 skipped.** The failures are the stale Code/API policy test (noted 30 Oct) and `test_swarm.py::test_the_shared_token_budget_stops_the_swarm`, a pre-existing timing race: it failed 7 of 8 runs on an unmodified export of the base commit. |
| Terminal client suite | automated | 29 passed (re-run independently of the building agent) |
| Unified-mode suite, plus 8 Code/workbench suites | automated | 126 passed, 3 skipped |
| Workbench suite with `ASSISTANT_MODE=unified` set for the whole process | automated | all Echo-owned tests pass; 2 failures are tests that create Codex sessions (expected) |
| Credential store against the real GNOME Keyring | manual | round trip ok; test item removed |
| Backend boot, auth, readiness, terminal status | manual (curl) | as §9 |
| GUI, Tauri, installed app, live model inference | — | **not verified** |

No paid model was called. Nothing was merged, published or released.

## Milestone 2 (15 November 2026): terminal, Linux desktop, evaluation loop

**Built.** All commits are on this branch.

- **Echo terminal UI** (`apps/terminal`, `7c14ef3`). A Claude Code / Codex-style interactive client:
  - designed from real captures and source (`cli-design.md`);
  - inline scrollback, Echo identity, live status;
  - approvals, steer, queue and stop;
  - `/model`, `/agents`, `/project`, `/mode`, `/permissions`, `/approvals`, `/resume` and more;
  - first-run model picker with GPU-fit hints;
  - a "Work in this folder?" trust prompt (`b241324`).
- **Edit view** (`786d981`):
  - inline numbered diffs for every edit;
  - the proposed diff beside the approval (stacked when narrow);
  - `/diff` pager;
  - `/preview`, a live diff pane on the right inside tmux;
  - backed by a structured `file_change` record (`404ebcc`).
- **Linux desktop** (`15d7f22`):
  - runs natively on Hyprland/Wayland;
  - attaches to a running shared backend instead of starting a second one;
  - `.deb`, `.rpm` and `.AppImage` built and inspected.
  - See `apps/desktop/LINUX.md`. Packages built on Arch need glibc 2.44, so release builds belong in an
    older-distro container.
- **Workspace.** Echo is window 3 of the owner's tmux AI workspace; Commands moved to 4.
- **Setup.** Ollama 0.40.16 runs as a user service, with `qwen3.5:9b` (fits 8 GB) and `qwen3.8:27b` (spills to RAM).

**Evaluation loop.** Each defect below was found by driving the real terminal against a real backend
on qwen3.5:9b, then fixed, covered by a test and re-run live:

| Found live | Fix |
|---|---|
| Denying one step (even "tell Echo what to do instead") ended the whole run | Live denials keep the work running (`7e51e59`) |
| With no project, Echo searched EchoSpeak's own source | Start-up folder trust prompt (`b241324`) |
| "Always ask" approval mode never asked for native file edits or terminal commands | `approvals.NATIVE_ACTIONS` (`404ebcc`) |
| A task whose tests passed ended "Work budget exhausted · The run failed" (71k of 64k tokens charged) | The loop charges fresh tokens once; the admission wrapper defers to it (`da171a3`, `9d60611`). Same task: 2,117 tokens, "Done · Checks pass" |
| A re-indent fitted back to the file changed nothing but was reported as an edit | "Error: nothing changed" (`da171a3`) |
| Desktop showed a stopped run as "ERROR · cancelled" | Owner cancellation isn't a failure (`11e3987`) |
| Terminal: backend start crashed without a backend venv; denied steps counted as run; edits counted as files; markdown in the resume recap | `d0dce4e`, `47e324b`, `a2775c2`, `8fbdd87` |

**Scores** (my own, from the live evidence above; 1–10):

| Category | Score | Why not higher |
|---|---|---|
| Functionality | 8.5 | No agent create/edit, routines or privacy mode from the terminal |
| Harness integration | 8.5 | No live cross-client follow stream; a 16k local context fills fast (60% after one research answer) |
| Visual design | 9 | Some truncation; old rule lines remain in scrollback after `/preview` re-wraps the pane |
| Terminal usability | 8.5 | `/preview` needs a 140-column pane; the old\|new split needs 150 |
| Real end-to-end behaviour | 8.5 | Verified fix, tests, verification, cancel, resume, agents and the shared desktop. The 9B model wastes steps |

**Tests at the end of the milestone:**
- Backend: 1,674 passed. Only the known stale `test_cloud_models` case and the pre-existing flaky `test_swarm` race fail.
- Terminal: 99 (TUI venv) + 30 (backend venv).
- Web: 238.
- Desktop: 19 contracts + 9 Rust.

**Next.** See `interfaces.md` for the prioritised desktop/terminal changes.

## Harness evaluation on qwen3.5:9b (15 November 2026)

The harness was measured with real local-model runs and a separate judge, then fixed where the evidence pointed. Full
record: [harness-evaluation.md](harness-evaluation.md) ([visual](../../artifacts/research/harness-evaluation.html)).

- **Score.** 7.94 → 8.95 out of 10 over 14 cases × 3 runs; held-out cases 7.41 → 9.38.
- **Execution success.** 48% → 83%.
- **Security.** Critical findings 6 → 0, at the same token cost.
- **Fixed** (commits `bc35212` and `9b221d2`):
  - a context window assumed at 32k while Ollama served 16k and silently truncated (audit F06);
  - answers written only as reasoning;
  - file-emptying writes without approval, including as a workaround after a denial;
  - compaction that never worked on this model and was never charged;
  - fresh-token accounting after a trim (audit F09);
  - verification checks that checked nothing;
  - a run-stream race that dropped the end of a run.
- **Evaluation workflow:**
  - `scripts/model_recorder.py`
  - `scripts/harness_eval.py`
  - `scripts/harness_judge.py`
- **Still open:**
  - D7, where the model hard-codes a test's expected value;
  - planted instructions in trusted project files;
  - the D8 cost increase under the true window.
