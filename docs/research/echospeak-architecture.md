# EchoSpeak architecture, as built (October 2026)

How one message becomes an agent's work, and how the pieces around the loop (capabilities, permissions, approvals, memory, learning, workers, queue and steering, projects, artifacts, providers, privacy) connect.

- **Visual version:** [artifacts/research/echospeak-architecture.html](../../artifacts/research/echospeak-architecture.html)
- **Manual tests:** [docs/TESTING_GUIDE.md](../TESTING_GUIDE.md)
- **Code:** paths are relative to `apps/backend` unless they start with `apps/web`.
- **Status:** "Verified" means covered by automated tests in this repository. Behavior with a real model, in the packaged app, or in a browser is called out separately.

## 1. The path of one message

1. **Composer.** `apps/web/src/index.tsx` `sendText` posts `/query/stream`. While a turn runs, Enter queues and Steer steers (section 8); nothing cancels the run anymore.
2. **API.** `api/routes/chat.py` registers the turn as active for that chat (`_ACTIVE_QUERY_CANCELLATIONS`), starts a worker thread and streams NDJSON events.
3. **Session.** `agent/lean/runtime.py` `LeanSession.run` takes the chat's lock (one turn per chat), creates an Execution and picks the agent(s):
   - @mentions first;
   - then the model router;
   - then the last speaker.
4. **Turn setup.** `_build_turn` assembles:
   - the toolbox (section 3);
   - the system prompt (section 2);
   - recalled memory, the lessons the learning system picked, project brief and evidence;
   - the model client.
5. **The loop.** `agent/lean/loop.py` `LeanTurn._run` repeats until the model answers without tools, Stop is pressed, or the step budget runs out:
   1. read steering;
   2. fit the context;
   3. call the model;
   4. check calls against policy, approvals and the toolbox, then run them (read-only ones in parallel);
   5. append the results.

   Guards:
   - a promise without action gets nudged;
   - an "I saved it" claim without a tool behind it is retracted;
   - changed code must be checked before "done";
   - exact repeats, denied calls and failure streaks are stopped;
   - the goal is restated every few steps.
6. **After the turn.** Each message and its timeline (tools, approvals, notes, steers, worker state) are persisted. Learning grades the run (section 6). Unread steers move to the queue.

## 2. Context the model gets (`agent/lean/prompt.py`)

The sections run from stable to volatile, so the provider can cache the prefix:

1. **Soul.** The agent's identity text.
2. **About EchoSpeak (from the running app).**
   - Name, `version.APP_VERSION`, desktop app or source build, OS, the creator (Ty, ty0x7) and the source URL.
   - A rule that versions in memories, chats, files or app names never answer "what version are you".
   - It's in the system message, which compaction never rewrites.
3. **Working rules and answer formats.** Rules for tools the turn doesn't offer are dropped (`_for_tools`).
4. **Team.** Teammates this agent may delegate to.
5. **Environment.**
   - Date and time, the OS, the active project and the folders it may use (read-only when writing is off).
   - The terminal mode and the computer-control note.
   - App names, only when the request is about apps.
6. **What you can use right now** (`agent/lean/capabilities.py`). Built in code from:
   - the live toolbox;
   - the permission switches (`policy_flags`, `NATIVE_POLICY_FLAGS`);
   - Private/Offline mode;
   - the agent's toolsets;
   - the approval mode.

   It lists what's ready, what's off and what turns each thing on, plus when approvals apply. Owners only.
7. **Recalled sources.** Memory facts (minus stale version claims), lessons, approved skills (names only; `read_skill` loads one), project brief and evidence (as untrusted), a chat summary, and matching past chats.

## 3. Tools: selection, discovery, dispatch (`agent/lean/toolbox.py`)

- **Selection.** An agent's toolsets name its tools: `core`, `web`, `live`, `terminal`, `vision`, `desktop`, `comms`, `memory`, `skills` and `self`.
  - Registry tools (`agent/tools.py`) are kept only if their permission switches are on.
  - Lean-native tools (file editing, the terminal, artifacts, computer, `spawn_workers`, coordination) are offered when their toolset is requested, and the native ones that change the PC also need their switches.
- **Discovery.**
  - More than 12 connected-app (MCP) tools wait behind `find_tools`, which matches by keyword and by meaning.
  - `find_integrations` finds new connections.
- **Dispatch.** `Toolbox.run` goes through these steps:
  1. resolves spelling and other harnesses' names;
  2. applies the privacy gate (`agent/privacy.py`);
  3. scopes the call to its component;
  4. runs it, checking a native tool's switches again;
  5. redacts secrets;
  6. collects cards (`widgets`), live progress (`progress.report`) and live state (`progress.report_state`, used by the Workers card).
- **Unavailable tools.** A call to a tool that's off returns the reason and the setting that turns it on, from the same source as the prompt section.
- **Execution evidence** (`agent/lean/evidence.py`, since 2026-10-22). Every call ends with an `outcome`, set where it's decided:
  - `ok`, `failed` or `no_effect` when it ran;
  - `refused`, `denied`, `held` or `skipped` when it didn't.

  It also records the `effects` the harness observed: a file stat after a write, copy, move or delete; the terminal's exit header; the screen change after a computer action. A reported write that isn't on disk becomes `failed`.
  - **Readers:** the refusal breaker, the claim checks, learning's tool rows, traces, tool-run records (`execution_status`, `verification`) and the step summary.
  - **Old records:** the earlier phrase matching remains only as their fallback.

  See [harness-evidence-2026-10-22.md](harness-evidence-2026-10-22.md).

## 4. Permissions, policy and approvals

These checks run in this order, all in code:

1. **Permission switches** (Settings › Permissions). A switched-off ability has no tools. Autonomy and grants can't add tools back.
2. **Blocked apps** (`agent/app_launcher.blocked_by`, and `blocked_in_command` for terminal commands). These are refused by name, executable and shortcut name, from open_application and from any shell command that names one. Nothing overrides the list.
3. **Privacy** (Standard, Private or Offline). The tool gate plus a socket backstop.
4. **Policy** (`agent/lean/policy.py`):
   - Stored secrets in arguments: always denied.
   - Rule of Two: after outside content, outward actions ask (or are refused where nobody can approve).
   - Cloud generation cost: asks.
5. **Approvals** (`agent/lean/approvals.py`, `loop._approve`).
   - **Modes:**
     - Risky only (deletes, dangerous commands, sends, desktop control, MCP actions);
     - Every action;
     - Never;
     - **Autonomous**.
   - **Grants:**
     - "For this chat": in memory, per tool.
     - "**Always allow**": persistent, per *scope* from `scope_for`, stored in `security/approval-grants.json` and listed in Settings.
   - **Autonomous mode:**
     - It approves autonomous scopes by itself (desktop control, non-dangerous terminal asks on this PC or for internet).
   - **Commands on this PC:**
     - In Auto terminal mode with Docker down (the silent fallback to this PC), ordinary commands ask like `where="host"`. That ask is covered by the "Run commands on this PC that aren't dangerous" grant and by Autonomous mode.
     - With This PC chosen explicitly, only dangerous commands ask.
     - Every other ask is *held*: not run, not waited on, and reported in `agent_done.held`.
     - App chats only.
   - **Never grantable or auto-approved:** deletes, dangerous commands, outward sends, identity changes, A2A and Rule-of-Two asks (on-screen control excepted).
   - Every automatic decision goes to `security/tool-audit.jsonl`.
6. **Loop guards.** A call the user denied, or the policy blocked, isn't asked or tried again in that turn. Three failures in a row tell the agent to change approach.

Stop (`/query/cancel`) cancels the turn, every approval waiting on it and every research worker.

## 5. Memory (`agent/memory.py`, `agent/lean/memory_quality.py`, `agent/lean/recall.py`)

- **Facts.** Saved by `memory_save`, curated, recalled by relevance into "What you know about the user". Never retrained into weights.
- **Stale facts.** Saved claims about the current EchoSpeak version are left out of recall, past-chat lines and `memory_search`. Nothing is deleted. Other time-sensitive facts are still marked only by the generic "possibly out of date" header (see limitations).
- **Past chats.** These are searched when the request points back ("last time…").

Memory holds *what is true about the user*. Learning holds *what worked in tasks*. They're separate stores with separate curators.

## 6. Learning (`agent/learning/`)

The flow:

1. **Grade.** Each finished owner request becomes an episode per agent, graded in code from tool records (V0 claimed through V4 owner-confirmed), with tamper and taint checks.
2. **Reflect.** When idle, the agent's own model proposes up to two lessons. Code vets them: no permissions, secrets, links, test-weakening or file names.
3. **Use.** Lessons are read before similar tasks, as advice.
4. **Hold back.** A matching lesson is held back at random:
   - 20% of the time if unproven, 10% if proven, never if the owner wrote it;
   - results of those tasks are its control (`held_wins`/`held_losses`).
5. **Promote or retire.** A lesson is promoted when it wins and does better than its control, and retired or demoted when tasks go clearly better without it (3+ control results).
6. **Worker episodes.** Each research worker's run is its own episode (`role="worker"`, `source="swarm"`), graded the same way. Workers don't write lessons.

This is memory-based adaptation, not recursive self-improvement: no weights, prompts, permissions or code change. Whether it helps is still to be measured with a real model (`scripts/eval_learning.py`).

## 7. Multi-agent work and research workers

- **Handoffs and groups.** `delegate_to_agent` runs a teammate with a fresh context (depth 2, at most 6 handoffs). In group chats:
  - @mentions run in parallel on a shared model, then merge;
  - Work-together uses a task board (`assign_tasks`) and a completion check that reads evidence (`agent/lean/job.py`).
- **Research workers, phase 1** (`agent/lean/swarm.py`).
  - Off by default (`lean_research_swarm`).
  - The lead's `spawn_workers` takes 2–6 independent questions, de-duplicated. Each becomes an ephemeral `LeanTurn`:
    - the lead's model, a fresh context and a research-worker prompt;
    - a toolbox that is the read-only research subset of the lead's own;
    - non-interactive (nothing to approve) and a step cap (12);
    - the request's shared taint list.
  - Workers run 1 at a time (local, configurable to 4) or 4 (cloud, up to 6), under one token budget (120k by default) and a time limit. One retry follows a model failure.
  - Findings come back as structured data: answer, the sources each worker actually opened (checked against its tool records), confidence and open questions. The lead receives them as untrusted content and writes the reply.
  - The UI shows one Workers card, fed by `tool_state` and persisted in the timeline.
  - **Benchmark:** `scripts/swarm_bench.py` (20 tasks: parallel, sequential and conflicting sources), single vs. swarm at matched worker budget. **Not yet run on a real model**, so it stays off by default.
- **A2A.** `ask_remote_agent` to pinned, signed agents. Every task asks first, in every mode.
- **Later phases, gated:** reviewer/best-of-N, the task board as a shared findings board, A2A workers.

## 7b. Routines, reminders and the tray (11.10.2)

- **Routines.** `agent/routines.py` holds them, and its scheduler checks every 60 s. Triggers are `schedule` (cron), `once` (`run_at`, disabled after it fires), `webhook` or `manual`. `agent/lean/automations.py` runs them: a `query` routine runs an agent unattended; a `remind` routine posts its message with no model call.
- **Reminders.** `agent/reminders.py` exposes `set_reminder`, `list_reminders` and `cancel_reminder` to owners. A reminder is a routine with `metadata.kind="reminder"`, and it goes to the chat it was set in.
- **Delivery.** `automations.deliver` sends to `desktop` (the `agent/notifications.py` feed), Discord and Telegram.
- **Desktop shell.** `src-tauri/src/tray.rs` claims the feed every 5 s and shows Windows notifications. It also follows `desktop_close_to_tray`: when that's on, closing the main window hides it to the tray; Quit in the tray menu exits.
- **Details:** [reminders-2026-10-23.md](reminders-2026-10-23.md).

## 8. Streaming, queue and steering

- **Events.** NDJSON events per message, reduced by `apps/web/src/lean/liveReducer.ts` and replayable (`/query/runs/{id}/events`). The new ones:
  - `tool_state`
  - `steer_applied`
  - `approval_held`
  - `auto_approved`
  - `fail_streak`
- **Queue.** A durable per-chat list (`StateStore.queued_turns`). Enter while busy queues. Items can be edited or removed (`PATCH/DELETE /query/queue/{id}`) and are claimed FIFO after the turn ends. Stop pauses claiming in the UI.
- **Steering** (`agent/lean/steering.py`).
  - `/query/steer` adds to a per-chat inbox (409 when nothing runs, and the UI queues instead).
  - The loop reads it at safe checkpoints: before the next model call, or instead of ending on a final answer. It never interrupts a tool.
  - It's appended as the user's words, with a timeline note.
  - Unread steers become queued follow-ups.

## 9. Projects, files and artifacts

- **Folders.**
  - With no project attached, the default file folder is the configured workspace: Documents in the installed app (never the install folder), the repo in a source checkout.
  - A project attached to a chat scopes file tools to its folder.
  - `project_status` labels which one it's describing.
- **Chat artifacts** (`agent/lean/artifacts.py`). Versioned, in app data, served in a sandboxed frame (scripts allowed, no network, opaque origin).
- **Project documents.** `docs/<topic>.md` is the technical record and `artifacts/<topic>.html` the visual report. They're discovered by `agent/project_workspace.py` and previewed with scripts disabled. The optional, reviewed `project-documentation` skill teaches agents the pairing.

## 10. Providers and models (`agent/lean/provider.py`, `agent/model_runtime.py`)

- One OpenAI-compatible streaming client covers local (LM Studio, Ollama) and cloud endpoints, plus adapters (Anthropic's native computer tool, Gemini Live).
- Smart model choice routes by track record when it's on.
- Research workers use their lead's model.

## 11. Security and isolation summary

These are the invariants the new features keep:

- Autonomy and grants never add a tool a switch removed.
- Blocked apps can't be overridden.
- Privacy gates every call, workers' included.
- Outside content taints the whole request, workers included.
- Approvals come only from the owner in the app.
- A2A always asks.
- Stop is authoritative.
- Learning writes advice, never settings.

## Verified, and what isn't

**Verified:**
- the full backend suite;
- the web typecheck and tests;
- the new suites `test_capabilities`, `test_self_knowledge`, `test_autonomy`, `test_steering` and `test_swarm`, plus reducer tests, all using test doubles.

**Not verified:**
- a real model following the identity and capability sections;
- worker quality and cost (bench not run);
- steering and queue in the browser UI;
- the packaged Windows build (file-root migration, Blocked apps on real Start-menu apps);
- autonomous walk-away runs end to end.

## Recommended next steps, by expected value

1. Run `swarm_bench.py` on your main local and cloud models. Turn workers on by default only where they win on parallel tasks.
2. Run `eval_learning.py` (on, off, control) to find out whether learning helps at all.
3. Put a freshness or authority signal on time-sensitive memories generally, not only version claims.
4. Build Phase 2 (fresh-context reviewer) only if the bench shows answers fail on errors a reviewer would catch.
5. Check MCP OAuth against the 2026-07-28 spec (`iss` validation, CIMD).
