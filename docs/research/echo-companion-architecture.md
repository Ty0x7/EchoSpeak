# Echo Companion: how it works

The session-awareness work originated in issue #210. Issue #233 covers the presentation and check-in refinements described below. [Companion engineering record](echo-companion.md)

Echo Companion is an **observer**. It notices what Claude Code, Codex, Echo and Echo's agents do. When something is worth saying, it says it out loud, and it keeps reports from outside agents as project memory. It never prompts, starts, steers or approves for any agent.

```
 Claude Code ──hooks──┐                      ┌── commentary ── speech queue ── Kokoro ── speakers
 Codex ───────hooks───┤  spool (0700)        │       (narrate + SOUL + local model)
                      ├─► observe store ─────┤
 Echo, Glados, … ─────┤  (SQLite)            ├── knowledge ── report archive + AgentMemory (project)
 (in-process, queued) │                      │
 loop-13 / adapters ──┘                      └── "What's happening?" (recap) ── desktop window / API
```

## 1. Observing the agents

| Agent | Mechanism | Code |
|---|---|---|
| Claude Code | Official `hooks` in `~/.claude/settings.json`: SessionStart, UserPromptSubmit, Stop, SubagentStop, Notification, SessionEnd | `agent/companion/hook_client.py` |
| Codex | Official `~/.codex/hooks.json`: the same events plus PermissionRequest. Codex `notify` is opt-in | same |
| Echo, named agents, subagents | In-process: `LeanSession.emit` and the end of each run call `agent/companion/native.py` | `agent/lean/runtime.py` (`companion_native.observe` / `.finished`) |
| The loop-13 controller, future agents | `POST /companion/events` (registered adapter, normal API auth) | `api/routes/companion.py`, `native.post_stage` |

The **hook** is a small stdlib-only script, installed as `~/.local/share/echospeak/companion/echo-companion-hook.py`. For each event it:

- reads the agent's event JSON;
- keeps allowlisted fields only (tool inputs and outputs are dropped);
- masks key, token and password values;
- drops anything outside the folders in `scope.json` (`~/Work` and `~/Projects`);
- writes one 0600 file into `~/.local/share/echospeak/companion/spool/` (0700).

It **prints nothing and always exits 0**. Claude Code and Codex act on a hook's stdout (extra context, allow or deny) and on exit code 2 (block), and this hook produces neither, so it cannot change an agent's turn.

The backend's `agent/companion/service.py` waits on the spool with inotify, then:

- `observe.normalise` turns each file into an event;
- `CompanionStore.add` stores the event in `DATA_DIR/companion/observe.sqlite3` (tables `events`, `sessions` and `handled`; 30-day retention) and updates the session's state.

States distinguish observed work and operations, input and permission waits, completion, interruption, quiet, idle and unknown. Each session also keeps:

- the owner's **task**, taken from their real request; system notifications, "continue" and short replies don't count;
- a fixed **project**, the EchoSpeak project of the folder the session started in.

Bounded read-only reconciliation in `agent/companion/history.py` also observes local Claude Code and Codex records. The selected integration and folder must be enabled, and the original and observed paths must resolve to the same registered EchoSpeak project. It limits candidate files, processing cadence and bytes read, and discards command arguments and output. Codex turn IDs deduplicate hook and history delivery. Claude completion matching uses the same reply near the same time across sources. Late observations do not rewind a newer session state. EchoSpeak research workers contribute native status under their parent session and execution.

"Interacting" means the most recently prompted session. When two sessions were prompted close together, it reports that it isn't sure.

## 2. Understanding what happened

`agent/companion/digest.py` reads a finished turn with no model call. It decides whether it is a report, a question or a short reply. It sorts the agent's own bullets into results, failures, open items, proposals, discoveries and decisions, using section headings and a negation guard. It extracts PR, issue and commit references and pytest or unittest counts. It drops instruction-like lines ("ignore previous instructions…").

Everything an agent says stays **reported**. Only Echo's own turns carry a **measured** status from EchoSpeak's verification ledger (verified, checks_pass, partial, failing).

## 3. Deciding to speak, and wording it

`agent/companion/narrate.py` has three steps.

1. **`facts`**: who (Claude, Codex, Echo, Glados…), the task, how long the turn ran, the outcome, the single most useful detail (a finding, a problem or the headline, told in the third person), what is still open, what needs the owner, and measured versus reported. The owner's name is included only if EchoSpeak has learned it (`text.owner_name()`): either the display-name setting or a profile memory such as "User's name is Ty".
2. **`worth_saying`**: silence by default. It speaks for:
   - reports, questions and permission waits from sessions the owner isn't in;
   - delegated work that EchoSpeak measured;
   - fresh substantial reports, including reports in the owner's most recently prompted session;
   - fresh completed operations during a long task, when its configured interval and cooldown have elapsed.

   It never speaks for idle prompts, follow-ups, notifications, short replies the owner is reading, stale (>10 min) events or repeats. Cooldowns and quiet hours apply.
3. **`compose`** writes one or two natural sentences deterministically. **`hybrid`** (the owner's choice: `companion_soul_style=local_model`) sends the same facts to the owner's **local** model. `guard` keeps the result only if it adds nothing:
   - no new numbers, claims or agents;
   - no dropped "says / by its account";
   - no "he/she" for agents;
   - no offer to act;
   - no third-person "Echo";
   - no unplanned name.

   Otherwise the deterministic line is spoken.

The **SOUL** is `DATA_DIR/companion/SOUL.md`. It holds a personality, phrases and a never-list, and is editable in Settings › Echo Companion. It is separate from Echo's SOUL, and nothing an agent reports can write to it.

**Speech** goes through `agent/speech_output.py`, one queue shared with Echo's read-aloud (priority, dedup, mute, cancel, stale drop). Kokoro (`agent/kokoro_voice.py`) synthesises on the CPU and PipeWire plays it. Chunk events carry amplitude envelopes, which the desktop window (`apps/companion`, a GTK4 layer-shell overlay) uses to move Echo's eyes. Captions disappear 2.5 s after the speech ends.

### Active work, completed work, and overall check-ins

- **Active work.** Permission waits and explicit milestones can surface during a turn. A fresh observed operation can prompt a progress update after the existing long-task threshold. An operation establishes progress on that operation.
- **Completed work.** Fresh substantive reports use the task and a useful finding, check, open item or decision. Native measured results retain their verification status. External results stay attributed to their reporter. Existing authorized report and memory storage runs independently of speaking.
- **Overall check-ins.** The existing observer loop schedules a review at Off, 30 or 60 minutes. The commentary worker reads a bounded recent window from SQLite and the active session snapshot. Recorded announcement IDs suppress repeats across restarts. Quiet hours, mute, a busy speech queue, the global cooldown and a lack of meaningful new evidence keep the review silent. Check-ins enter the speech queue below individual commentary.

The review checks the current external observer allowlist. It keeps project identities distinct and adds fresh concurrent work when it helps explain a new event. Only structured native parent-worker evidence establishes collaboration. Local-model wording mode can rank a few jointly presented evidence cards and use bounded project-memory context for ranking. It selects validated card IDs and never supplies the facts that are spoken. The memory lookup uses the canonical scoped projection and can rank those results with local ONNX semantic retrieval. Invalid or unavailable model replies fall back to deterministic selection.

Caption and speech share the same prepared text. The presentation layer removes Markdown, URLs, verbose paths and opaque identifiers and expands common abbreviations. The original reports and canonical memories keep their provenance and text. The caption fades within a fixed-size GTK overlay slot, while the body blends from the last rendered pose. Only the face and visible caption accept input. Reduced motion switches directly to the final pose. [GTK overlay sizing](https://docs.gtk.org/gtk4/method.Overlay.set_measure_overlay.html)

## 4. Local-model inference is isolated

`soul.reword` makes **one** HTTP request to the local model's OpenAI-compatible `/chat/completions`. The request has a system message (facts, SOUL personality and rules) and a user message ("Say it."). It has no tools, no conversation history, no session and no streaming. Thinking is switched off through the provider's own mapping.

The Companion uses `resolve_endpoint` and `reasoning_effort_for` from `agent.lean.provider`. It requires a local endpoint and the normal model privacy policy, and uses nonblocking admission to yield to existing or waiting model work. It never calls Echo's runtime, harness, tool loop, history, delegation or learning. Sharing a model server doesn't mean sharing an agent session.

## 5. Knowledge: where it goes

Knowledge transfer applies to completed **reports** from **external** agents: Claude Code and Codex through hooks, and registered adapters. Echo's own conversations are already in memory through Echo's normal pipeline. Re-importing Echo's replies would copy imported reports back in, which was seen live and fixed in #224.

Each report goes through `knowledge.authorize` (default `project_only`). The verbatim archive and the memory item are only written if it allows them:

1. **Captured report archive** at `DATA_DIR/companion/reports/<date>/<time>-<agent>-<session>-<event>.md` (0600 files in 0700 folders). Front matter records:
   - agent, session and turn;
   - project and folder, and time;
   - transcript path and event id;
   - kind, outcome, evidence, and PRs, issues and commits.

   The body preserves the captured report under an untrusted reference banner. Hook masking and size limits apply. Archive creation precedes canonical memory filtering.
2. **One canonical memory** through `AgentMemory.add_memory_item` (`agent/memory.py`):
   - `memory_type="project"`, `source="echo-companion"`, scope `project`, and the project id and path;
   - `source_item_id` set to the event id and `semantic_key` to `companion:<agent>:<session>:<turn>`;
   - text such as: "Codex reported on 2026-11-25 … in project Harness (session cx-y2): … Found: … Still open: … Status: reported by Codex, not independently verified by EchoSpeak."

Memory then does what it does for any item:

- **Sensitive-text filter.** It currently refuses any text containing "token" or "secret"; #223, awaiting review, makes it value-aware.
- **Dedup.** The same semantic key with the same text is never stored twice; text at least 94% similar is skipped.
- **Indexing.** Canonical records live in `DATA_DIR/memory/conversation_index/records.json`. Local ONNX embeddings are stored in NumPy `vectors.npy` and `store.json`; FAISS naming survives in compatibility code.
- **Retirement.** Items nobody recalls for months are retired by the normal memory tidy.

Further properties:

- **Contradictions.** Newer reports are separate items with their own dates. Echo sees both and their dates. Nothing silently overwrites an earlier report.
- **Learning.** Imported items are never lessons, rewards or episodes. `agent/learning` grades only Echo's own executions, and companion items never enter it.
- **Obsidian.** Companion memories are ordinary project memory items, so they reach Obsidian when the owner runs the projection (`GET /memory/obsidian/plan`, `POST /memory/obsidian/apply`). That projection is never automatic.
- **Speech off.** Knowledge is still kept when spoken commentary is off. Turning `companion_knowledge` off stops it.
- **Restarts.**
  - While EchoSpeak is closed, hook files wait in the spool (up to 5,000).
  - Startup catch-up quietly processes already-stored, unhandled reports from the last 24 hours. Pending hook or history events enter normal ingestion and still pass the commentary freshness rules.
  - The `handled` table prevents double processing, and event ids prevent duplicates.

## 6. How Echo finds it later

There are two paths. Both use Echo's existing memory infrastructure, and neither is specific to the companion.

- **Automatic.** On every turn, `LeanSession._recall(<the message>)` (`agent/lean/runtime.py`) calls `AgentMemory.runtime_memory_projection`. It returns up to 8 memories matched to the request's words, from account memory, the conversation and **the conversation's active project**, and supplies them as labelled background alongside the current request before any tool call.
- **On demand.** The `memory_search` tool runs `retrieve_relevant`, a keyword plus vector search over the same scopes.

Echo also has a separate, older tool, `agent_sessions` (`agent/lean/session_logs.py`). It reads Claude Code's and Codex's **raw logs** directly; it is owner-only and read-only. Its description tells Echo to prefer it for "what did that session do", so "What did Claude accomplish…" questions often go there.

### Verified in conversations

Run on 2026-11-26 with the local qwen3.5-9b, in an isolated lab backend. A fresh conversation was opened for each question, and two "yesterday" reports were fed through the real hook pipeline.

| Question | Source Echo used | Result |
|---|---|---|
| What did Codex discover about our harness? | automatic context, no tool | ✅ correct, "reported by Codex, not independently verified" |
| What problems are still unresolved? | automatic context + folder check | ✅ all open items from both reports |
| Continue from Claude's previous engineering report | automatic context, no tool | ✅ right campaign, results and gaps |
| What did Claude accomplish yesterday? | `agent_sessions` (raw logs) | ⚠️ described a real Claude session from the logs; the synthetic report existed only in memory |
| What have my agents been working on? | status tools | ❌ stopped without an answer (#225) |
| (no project) What did Claude accomplish yesterday? | — | project memory not visible; isolation held |

On the installed build, a Work-project conversation answered "What did Claude Code report…" from the imported report, with "not yet independently verified".

**Automatic versus explicit.** In a conversation attached to the right project, relevant reports usually arrive automatically, through word-matched recall. Conversations without a project can't see project-only knowledge. Account-wide storage (`project_or_account`) is the owner's explicit choice; it is off.

## 7. Why the companion can't drive an agent

Enforced by `tests/test_companion_boundaries.py`:

- **Imports.** Companion code and the desktop app cannot import Echo's runtime, loop, core, approvals, swarm, job, steering, toolbox, threads, Echo Code, learning, routines, heartbeat, a2a, coordination, tools or the loop-13 controller. Only the two provider helpers above are allowed. No agent endpoint is called (`/query`, `/threads`, `/approvals`, `/lean`, `/code`, `/tasks`, `/routines`, `/a2a`). The desktop app calls only `/companion/*`, `/avatar/config`, `/settings` and `/media-runtime/voice/speak/mute`.
- **Traps.** With `LeanSession.run`, `run_lean_query`, `run_lean_text`, `ApprovalBroker.resolve`, `code_sessions.manager` and `ThreadManager.create_thread` set to fail, the whole pipeline runs (report, archive, memory, wording, speech, "what's happening?", native events) and reaches none of them.
- **No recursion.** The observer persists source observations. Commentary, speech, and memory writes do not publish new agent observations. Native observation comes from Echo's own turns, which the companion never starts. Echo's replies are not re-imported.
- **Live check on the install.** Across a spoken "What's happening?" and a "latest report" request, Echo's state store was identical before and after: 134 executions, 554 tool runs, 262 items, 35 thread states. Echo's thread count stayed at 15.

The only things the companion changes are its own store, the report archive, memory items, its SOUL, settings the owner sets, and, when the owner clicks Install or Remove in Settings, the hook entries in `~/.claude/settings.json` and `~/.codex/hooks.json`.

## 8. Automatic versus owner action

**Automatic:**

- observing the authorised folders;
- deciding to stay quiet;
- speaking milestones;
- keeping project-scoped reports;
- catch-up after a restart;
- the companion starting at login (stable launcher `~/.local/share/echospeak/companion/echo-companion`, which follows whichever install the `echospeak` command points at).

**Owner action:**

- installing or removing hooks;
- the observed folders;
- prompt capture;
- account-wide memory (`project_or_account`);
- wording style;
- quiet hours;
- the SOUL;
- muting, pausing or moving the window;
- the Obsidian projection;
- anything Echo then *does* with the knowledge, which follows Echo's normal approvals.

## Known limits

- The normal Codex subscription is available. Live hook delivery depends on owner review in Codex’s `/hooks`. Authorized local-record recovery is independent of that decision.
- The memory secret filter drops reports that mention "token" or "secret" until #223 is decided.
- Task titles are heuristic (#226 refines short replies).
- The local-model guard can't catch an implied cause and effect.
- `agent_sessions` and companion memory can describe the same work from two sources: raw logs versus condensed, project-scoped reports.

## Interactive activity checks

Companion selects from the observation store, native session overview and scoped report history. Agent questions can show current status and recent accomplishments together. Optional local-model wording follows source selection. The installed check recovered a real Codex final report, and isolated tests exercised eight topics, native Echo/Jarvis/Glados attribution and a different-project control. The ordinary Echo chat gap remains tracked in #225.
