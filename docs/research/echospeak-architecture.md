# EchoSpeak architecture — research review, 30 October 2026

**Status: source audit and architecture proposal. No loop redesign implemented.**
The already-running folder-mode correction is a separate completed 11.13.1 change.
Workflow: **Objective → Research → Environment → Verification → Adaptation → Completion.**

[Visual architecture and teaching guide](../../artifacts/research/echospeak-architecture.html) ·
[Audit evidence](echospeak-architecture-verification.json) ·
[Current folder-mode build](../releases/v11.13.1.md)

## The decision in plain language

EchoSpeak already has **its own native harness** and **adapters for other harnesses**.
Chat, Code and Agents describe the experience and responsibilities. Echo native,
Codex and Claude Code describe who executes the work. A selected model is another
choice. These should be explicit, separate contracts.

**Provisional recommendation:** retain one shared native execution core, with
separately testable Chat, Code and Agents controllers. Keep vendor-owned runtimes
behind their adapters. Do not copy the native loop three times, replace it with
an SDK, or remove working features before comparative traces justify the change.
This recommendation is engineering judgment, not a measured model-quality result.

Sharing loop code does not mean sharing a conversation's state. Each run has its
own session, selected model, history, context, cancellation and permissions. The
native session lock is per conversation; independent conversations can run in
parallel subject to resource admission. An asyncio process-supervision loop and
the model/tool reasoning cycle are also different kinds of “loop.”

## A vocabulary we can use consistently

These are proposed product/engineering names mapped to current code. They are not
new modules or class renames that have already been implemented.

| Name | Responsibility | Current implementation |
|---|---|---|
| **Echo Core** | Shared services: persistence, tool execution, permissions, events, model access, cancellation | `agent/core.py`, `state.py`, `lean/toolbox.py`, `lean/policy.py`, stores and API services; broader than the file named core.py |
| **Echo Native** | Echo's own model/tool execution engine | `LeanSession` in `lean/runtime.py`, `LeanTurn` in `lean/loop.py`, `ChatClient` and native tools |
| **Chat Controller** | Information-first conversation, retrieval, citations and answer completion | `environments.py`, `chat_mode.py`, `chat_run.py`, Chat branches in LeanSession |
| **Code Controller** | Project context, engineering tools and observed verification | `code_mode.py`, `echo_code.py`, LeanSession Code branches and verification ledger |
| **Agent Coordinator** | Persistent identities, responsibilities, rooms, delegation, schedules and durable work | personas/rooms, coordination/work services and Agents branches in LeanSession |
| **Runtime Bridge** | Control an external harness and translate its events/capabilities | `code_sessions.py`, `code_runtimes.py`, `code_process.py`; Codex app-server, Claude CLI, ACP |
| **Model Binding** | Exact provider/endpoint/model/account selection and capability provenance | `model_runtime.py`, `cloud_providers.py`, `lean/provider.py`, native session bindings |
| **Workspace** | The files/tools the run operates on | Project folder or explicit Git worktree; workspace path is independent of a model or agent identity |

A **session** is the durable conversation/work record. A **run** is one user
request carried to a stopping point. A **step** is a model response and any tool
work it requests. A **context window** is the subset supplied to the model now;
it is not the whole session. An **agent identity** is a persistent persona and
responsibility, not the language model's weights. Echo's learning records advice
and experience; that does not train the underlying provider model's weights.
External SDKs use “turn” differently, so adapters should preserve their native IDs
while application traces use explicit run/step/session fields.

## What is actually running today

| Experience | Executor | Context and tools | Completion |
|---|---|---|---|
| Chat | Echo Native + selected local/API model | Model-first identity; Chat tool boundary; owner facts and Chat recall; research/answer controller | Answer and evidence/citation checks |
| Native Code | Echo Native + selected local/API model | Model-first coding identity; project/instructions; engineering tool boundary | File/tool evidence and engineering verification ledger |
| Agents | Echo Native + persona model/defaults and optional routing | Persistent personas, rooms, responsibilities, delegation and work | Agent/work outcomes, with engineering checks where applicable |
| Codex in Code | Official Codex harness through app-server | Codex owns its tool loop, model context, native history and permissions | Provider events/outcomes, plus separately observed workspace acceptance |
| Claude Code in Code | Installed official Claude CLI | Claude owns its tools, context and native session; permission requests reach Echo's UI | Claude result/events and separate workspace acceptance |
| ACP in Code | Connected ACP harness | Protocol-negotiated features; current Echo client does not advertise filesystem/terminal client capabilities | Only implemented protocol behavior may be offered |

External runtimes are currently exposed in Code, not as general selectable
executors for all three tabs. That future possibility should not be confused with
an implemented feature.

Native path: `index.tsx → /query/stream → EchoSpeakAgent.process_query → LeanSession
→ environment policy → LeanTurn → ChatClient → local/API model → Toolbox → results`.
External Code path: `ExternalSession → external-session API → supervisor/adapter
→ vendor process → vendor-owned model/tool loop → native events and approvals`.
Echo's native model does not sit in control of Codex or Claude on that path.

Source walk: [routing](../../apps/backend/agent/environments.py),
[native controller](../../apps/backend/agent/lean/runtime.py),
[step engine](../../apps/backend/agent/lean/loop.py),
[external supervisor](../../apps/backend/agent/code_sessions.py),
[runtime adapters](../../apps/backend/agent/code_runtimes.py).

The native transport layer already includes Responses, Anthropic Messages,
Gemini-specific handling and Chat Completions. Responses opaque items are bound
to the originating provider, endpoint, model and protocol. Calling the Responses
API does not make the application an OpenAI-managed harness. Calling Anthropic
Messages does not make it the Claude Agent SDK. These are separate layers.

## What the other systems actually demonstrate

| System | Who owns the loop? | Implication for Echo |
|---|---|---|
| [OpenAI Agents SDK](https://developers.openai.com/api/docs/guides/agents/sdk) | SDK runner in the application's process; the application supplies its infrastructure and tools | Useful runner/orchestration primitives, not an automatic replacement for Echo's product policies |
| [Codex app-server](https://learn.chatgpt.com/docs/app-server) | Codex; clients drive threads/turns and receive events/approvals | Correct family of integration for a rich Code client; keep its session and permission semantics |
| [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview) | Claude Code engine, exposed through a library, with tools/context/permissions | It is a full harness option; using it would not be the same as using a Claude model inside Echo Native |
| [T3 Code](https://github.com/pingdotgg/t3code/blob/main/docs/user/faq.md) | Connected provider harnesses | Its useful comparison is the control/orchestration surface. Echo additionally owns an independent native engine |

OpenAI's [runner documentation](https://developers.openai.com/api/docs/guides/agents/running-agents)
describes a repeated model/tool cycle with explicit stopping and continuation.
Claude's [loop documentation](https://code.claude.com/docs/en/agent-sdk/agent-loop)
similarly separates model responses, tool execution, results and budgets. Those
mechanics can support multiple specialized experiences; different tabs do not
in themselves require different copies of that cycle.

T3's [provider integration rules](https://github.com/pingdotgg/t3code/blob/main/docs/internals/adding-a-provider.md)
are especially applicable: adapter-specific behavior belongs in adapters, and the
UI should offer only capabilities the adapter and provider actually support.
An available upstream API is not proof that Echo has implemented its UI path.
For example, native steering exists, but active-turn steering is not exposed by
the current external-session API. Do not present all runtimes as feature-identical.
T3's local-model product stance is not a universal capability benchmark; Echo's
local support should be evaluated on representative tasks and retained.

## What better models change

Better models can reduce the need for corrective prompts, name repair, forced
planning and repeated reminders. They do not decide the application's permission
boundary, which files are available, who owns history, how work resumes after a
crash, or what evidence counts as completion. Those remain software contracts.

Anthropic's [April 2026 architecture discussion](https://www.anthropic.com/research/managed-agents)
describes a context-reset workaround becoming unnecessary after a model change,
and separates durable sessions, the harness and execution environments. The
lesson is to revisit model-specific scaffolding and keep interfaces stable, not
to assume every model needs the same workaround or that reliable execution needs
no infrastructure. Its [long-running application study](https://www.anthropic.com/research/harness-design-long-running-apps)
also shows why task grading and independent evidence matter on substantial work.
Neither study establishes the best loop for Echo without Echo-specific tests.

## Concrete findings in this checkout

1. **Environment separation already exists.** Chat/Code suppress Agents identity,
   team routing and delegation; persistent Agents retain them. Shared mechanics
   are already a foundation, so a rewrite would duplicate existing systems.
2. **Chat is not strictly read-only.** Its native boundary denies terminal/file
   mutation/desktop tools, but permits artifacts, media, reminders, optional app
   opening and connected tools by origin. Your information-first intent needs an
   explicit action/effect contract, while preserving deliberate creation features.
3. **Some adaptation remains deployment-based.** Chat uses compact/full profiles
   (6/10 steps; compact for identified local models up to 9B). Code tool focus uses
   the local-provider distinction, including large local models. Capability
   metadata/context overrides already exist; they are not one uniformly enforced
   model-performance policy. Treat model size/name as a hint, not a quality test.
4. **Old contracts remain in tests.** The read-only baseline passed 47/48 tests.
   One unchanged cloud test still demands the obsolete Code/API prohibition.
   Current implementation allows API binding. This is not evidence that API Code
   is blocked, nor evidence of live API quality; it is a stale test to reconcile.
5. **Defaults need explicit ownership.** The corrected Git requirement was a
   workspace/provisioning-policy error, not evidence that the reasoning loop must
   be copied. Folder mode and optional worktrees now have distinct contracts.
6. **Environment inference has edge cases.** Thread/source/room and resumed work
   determine native mode; unknown threads and non-live sources use Agents rules.
   Audit API/voice/resume contracts before treating every entry point as Chat.
7. **The native controller is concentrated.** LeanSession contains substantial
   Chat, Code and Agents branching. Extracting controllers later could improve
   testability without copying transport, policy or persistence implementations.
8. **MCP is a transport/control boundary.** The current control API creates native
   sessions or agent rooms; it can control existing external sessions through
   status/send/stop/triage. It is not yet a universal external creation/fork/steer
   interface, and it does not replace any harness's reasoning loop.

## One loop or three?

| Option | Benefit | Cost | Decision |
|---|---|---|---|
| Three copied native loops | Independent edits | Permissions, retries, traces and fixes drift; three maintenance paths | No evidence currently justifies it |
| One growing conditional controller | Small initial change | Environment behavior and model heuristics become harder to reason about | Current concentration should be measured and bounded |
| Shared step engine/services + explicit environment controllers | Shared reliability with independently testable behavior | Requires clear contracts and careful extraction | Recommended direction, after baseline/acceptance agreement |

Share transport adapters, tool execution, state/event schemas, cancellation,
privacy/approval primitives and capability metadata. Specialize identity,
retrieval/memory scope, tool/effect policy, budgets, orchestration and completion
criteria. Shared stores must not imply unfiltered shared histories or persona
memories. Vendor-owned loops remain separate execution backends.

Separate native engines become justified if measured control requirements really
diverge—for example realtime audio, long-running durable workflows or a different
deployment/trust boundary. A third UI tab alone is not that evidence.

## Testing before changing the architecture

Use the unit of comparison **task + model binding + harness + workspace + policy**.
Changing several at once cannot isolate why a result improved.

| Test family | Representative task | Evidence |
|---|---|---|
| Chat | Explain a supplied document; answer a current question with sources; brainstorm without side effects | Grounding/citation correctness, relevance, unexpected actions, model/identity preservation |
| Code | Repair a seeded failing test in a plain folder; repeat in an optional worktree; add Git later | Actual file diff, test exit/result, unrelated files preserved, accurate workspace state |
| Agents | Resume a named agent's responsibility; coordinate two bounded mocked tasks | Identity/memory isolation, task ownership, durable outcome, cancellation and duplicate prevention |
| Cross-cutting | Long context, model switch, denial, interruption, reconnect | Provider-state affinity, no unintended writes/replay, clear capability/error states |

First reuse deterministic tests/traces. Then choose a small set of capable API
models and a local comparison, pin exact IDs, approve a fixed spend/request limit,
and run the same tasks through Echo Native. Compare a vendor harness where the
model/task match is meaningful; otherwise label model/harness differences as
confounded. Record every call, retry, input/output/cached token count when exposed,
latency and actual task outcome. Subscription windows are not API token bills.

Both [Anthropic's eval guide](https://www.anthropic.com/research/demystifying-evals-for-ai-agents)
and [OpenAI's workflow eval guide](https://developers.openai.com/api/docs/guides/agent-evals)
support task-level evaluation and trace inspection. Use deterministic outcome
checks first; a model grader's praise is not a passing build or a correct source.
Include failed runs, not just selected demonstrations. This pass did not run a
paid API benchmark, install an SDK or tune the loop.

## Credentials and SDK strategy

Provider API keys, Echo's local owner API key, vendor CLI sign-in and scoped MCP
control grants are different credentials. Native cloud models use Echo's provider
bindings; subscription harnesses keep their native authentication. Do not route
one provider's opaque history or credentials through another by default.

The [Claude SDK overview](https://code.claude.com/docs/en/agent-sdk/overview) documents restrictions on third-party claude.ai login
unless approved. [Codex app-server documentation](https://learn.chatgpt.com/docs/app-server) distinguishes existing local
integrations from hosted/commercial authentication and recommends its documented
Sign in with ChatGPT path. T3 describes its own approved integrations; their
approval is not inherited by copying code. Treat supported authentication as a
provider contract to verify before a new distribution/integration—not as proof
that a technically successful local connection licenses a hosted service.

No SDK migration is recommended yet. OpenAI's generic Agents SDK, Claude's full
Agent SDK and managed agent services own different amounts of the runtime. Any
adoption needs a measured benefit and a plan to retain Echo's local models,
permissions, persistent Agents, stores, interruption and evidence semantics.

## Next review, before implementation

1. Agree on the glossary and ownership map; keep the two execution families clear.
2. Specify Chat's information-first action policy and the three completion contracts.
3. Reconcile stale tests and inventory each model/runtime capability with provenance.
4. Select representative tasks/models and an explicit small test budget; benchmark
   the existing architecture before changing it.
5. Extract environment controllers only where traces show policy coupling or
   wasted work. Keep native mechanics shared unless measured needs diverge.

Mobile/remote access would add authenticated client pairing, durable event replay
and remote execution/approval transport. It does not by itself require a new
reasoning loop. No remote system, scheduler replacement or harness fork is proposed
for immediate implementation.

## Audit scope and evidence

Reviewed native/external entry points, environment routing, controller policies,
model capabilities/transports, provider history, tools, coordination/persistence
boundaries, workspace modes, UI paths, tests and existing architecture reports.
This is not an exhaustive security audit of every file or a live API quality test.
Source hashes and exact source paths are in the paired evidence file.

Research baseline: **47 passed, 1 stale-policy test failed** across environments,
native Code, Chat policy and cloud contracts. These use scripted models/mock HTTP,
not paid providers. The separate folder-mode delivery has 102 passing backend
tests, 39 web tests, 16 desktop contracts and zero-prompt real/compiled startup
checks; it is not an installed-window or inference success claim.

---

## Earlier platform walkthrough (historical)

The material below records the earlier architecture snapshot. Its agent-centric
message/prompt description does not apply unchanged to model-first Chat/Code.
Use the ownership map above for the current 11.13.1 interpretation. Earlier test
and model observations remain historical, not newly verified in this review.

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

## 7c. Screen work, skills and your folder (11.10.3)

- **Looking vs doing.** `take_screenshot`, `vision_qa` and `read_image` only look; `computer_action` acts. `vision_qa` asked to "click" says it didn't (outcome `no_effect`). A captured window that names nothing but its title bar is marked as drawn (games, canvases), with the coordinate-click route.
- **Stalls.** `agent/lean/loop.py` `_screen_progress` adds a note after 3 looks in a row without an action, and after 2 screen actions in a row that failed or changed nothing. Desktop tools' failure texts count as failures (`toolbox._FAILURE_PREFIXES`).
- **Skills.** `propose_skill` (owner, interactive) drafts a SKILL.md that waits for review. Settings › Skills offers bundled skills (`resources/agent-skills`, including `desktop-apps-and-games`) and ones in `~/.echospeak/skills`, `~/.agents/skills`, `~/.claude/skills`, `~/.codex/skills`.
- **`~/.echospeak`** (`agent/home.py`): your `AGENTS.md` and `skills/`, like `~/.codex` and `~/.claude`. The attached project's `AGENTS.md` (else `CLAUDE.md`) is read too, up to 32 KiB together.
- **This PC terminal.** Lists installed programs, PowerShell equivalents for bash habits, and runs Git Bash with `shell="bash"` when it's installed.
- **Details:** [computer-control-2026-10-25.md](computer-control-2026-10-25.md).

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
