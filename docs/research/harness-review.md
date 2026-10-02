# Harness review: how EchoSpeak's lean runtime compares to current agent frameworks

Date: 2026-10-01. Scope: the lean runtime (`apps/backend/agent/lean/`), its HTTP surface
(`apps/backend/api/lean_routes.py`) and the chat UI that renders it (`apps/web/src/lean/`).
This is a research report. Nothing below has been implemented.

Effort: **S** = under a day, **M** = a few days, **L** = a week or more.
Impact: what changes for the user (reliability, speed, clarity), not code tidiness.

---

## 1. What the reference systems do

### Claude Agent SDK / Claude Code (Anthropic)

- The loop is "gather context → take action → verify → repeat". A turn is one model
  call plus the tools it asked for; the loop ends when the model answers without tool
  calls. `max_turns` and a spend cap are optional and **off by default**; hitting one
  returns a typed result (`error_max_turns`) instead of a fake answer.
  [Agent loop](https://code.claude.com/docs/en/agent-sdk/agent-loop),
  [Building agents with the Claude Agent SDK](https://claude.com/blog/building-agents-with-the-claude-agent-sdk)
- Read-only tools run in parallel; tools that change state run one at a time.
  Custom tools opt in to parallelism with a read-only hint.
  [Agent loop → Parallel tool execution](https://code.claude.com/docs/en/agent-sdk/agent-loop)
- Permissions are a separate layer from the loop: allow / deny lists, scoped rules such
  as `Bash(npm *)`, permission modes (`default`, `acceptEdits`, `plan`, `dontAsk`,
  `bypassPermissions`), and a `canUseTool` callback. A denial goes back to the model as
  a tool result, and the model adapts.
- Hooks (`PreToolUse`, `PostToolUse`, `Stop`, `SubagentStart/Stop`, `PreCompact`) run
  outside the context window and can allow, deny, or ask.
  [Hooks](https://code.claude.com/docs/en/agent-sdk/hooks)
- Context: automatic compaction near the limit, with a `compact_boundary` event in the
  stream; persistent rules live in a file that is re-injected every request (CLAUDE.md).
  Subagents get a fresh context and return only their final answer.
  [Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Sessions can be resumed and forked by id; `interrupt()` stops a running turn.
  [SDK overview](https://code.claude.com/docs/en/agent-sdk/overview)
- Multi-agent research system: orchestrator–worker, workers in isolated contexts,
  parallel tool calls cut research time by up to 90%, multi-agent runs use about 15× the
  tokens of a chat; start evaluation with about 20 real queries; resume from checkpoints
  rather than restarting.
  [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)

### OpenAI Agents SDK and Codex

- Handoffs are tools named `transfer_to_<agent>`. When one fires, the receiving agent
  **takes over the conversation** (it sees the history unless an `input_filter` trims
  it). This is different from "agent as tool", where the caller gets a result back.
  [Handoffs](https://openai.github.io/openai-agents-python/handoffs/)
- Guardrails run alongside the agent and can trip a run; sessions store history;
  tracing records every model call, tool call and handoff as spans; `max_turns` is
  configurable. [Agents SDK](https://openai.github.io/openai-agents-python/)
- Codex separates **sandbox** (what a command can technically touch: `read-only`,
  `workspace-write`, `danger-full-access`) from **approval policy** (when to ask:
  `untrusted`, `on-request`, `never`). In `workspace-write`, writes are confined to the
  workspace and network access is off unless enabled.
  [Agent approvals & security](https://developers.openai.com/codex/agent-approvals-security)

### LangGraph

- Human-in-the-loop is `interrupt()` inside a node or tool, persisted by a checkpointer
  keyed by `thread_id`, and resumed with `Command(resume=...)`. A paused run survives a
  process restart. [Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- Streaming has named modes (`values`, `updates`, `messages`, `tools`, `custom`,
  `checkpoints`, `tasks`, `debug`) and subgraph events carry a namespace so a UI can tell
  nested agents apart. [Event streaming](https://docs.langchain.com/oss/python/langgraph/event-streaming)

### AutoGen / AG2

- `SelectorGroupChat`: a model picks the next speaker from `{participants}`, `{roles}`
  and `{history}`; `selector_func` / `candidate_func` override it; `allow_repeated_speaker`
  controls back-to-back turns; termination conditions (`MaxMessageTermination`,
  `TextMentionTermination`) are composable. RoundRobin and Swarm are the other patterns.
  [Selector group chat](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/selector-group-chat.html)

### CrewAI

- Two processes: sequential (tasks in order) and hierarchical (a manager LLM or manager
  agent assigns and checks work). [Processes](https://docs.crewai.com/en/concepts/processes)

### OpenHands

- Everything is an event: Actions from the agent, Observations from the runtime, in one
  EventStream that the controller loop, UI and persistence all read. Commands run in a
  sandboxed Runtime (Docker, local or remote) through an action-execution server with
  bash, Jupyter and a browser. [Backend architecture](https://docs.openhands.dev/usage/architecture/backend)

### Hermes Agent (local reference clone used earlier in this project)

- Very high iteration budget by default, static toolsets, reasoning shown by default with
  a streaming think-tag scrubber, approvals only for dangerous terminal patterns, SQLite
  sessions with compression lineage and full-text session search.

---

## 2. Where EchoSpeak stands

What already matches the reference systems:

| Practice | EchoSpeak |
| --- | --- |
| Loop runs until the model answers without tools | `apps/backend/agent/lean/loop.py` `LeanTurn.run` (no completion gate, no intent gate) |
| Denials return to the model as a tool result | `loop.py` `_approve`, `apps/backend/agent/lean/approvals.py` |
| Read-only tools in parallel, others sequential | `loop.py` `_run_tools` + `toolbox.py` `is_parallel_safe` |
| Repeated identical calls are caught, not looped | `loop.py` `call_counts` (third repeat gets a nudge) |
| Typed event stream with per-message ids | `loop.py` `emit`, `apps/web/src/lean/liveReducer.ts` |
| Reasoning streamed and shown | `provider.py` think-tag handling, `LeanMessage.tsx` |
| Group chat speaker selection by model | `runtime.py` `_model_route` (≈ AutoGen selector) |
| Delegation depth limit | `runtime.py` `MAX_DELEGATION_DEPTH` |
| Context fitting before each call | `loop.py` `_fit_context` |

Gaps, with the source that shows the better practice:

1. **Context is trimmed, never summarized.** `_fit_context` shrinks old tool results
   and drops messages; nothing writes a summary, and there is no boundary event. Long
   coding sessions on a 64k local model lose early decisions silently. (Claude Agent SDK
   compaction + `compact_boundary`; Anthropic context-engineering post.)
2. **The step budget ends with a forced answer, not a typed stop.** When
   `lean_max_iterations` (default 60, `settings.py`) is hit, `loop.py` asks for a final
   answer without tools. The user cannot tell "finished" from "ran out". (Agent SDK
   `error_max_turns`; Agents SDK `max_turns` error.)
3. **Delegation is a nested call that looks like a handoff.** `delegate_to_agent`
   (`runtime.py` `_native_tools`) runs the teammate inside the parent's tool call and
   returns the text. That is "agent as tool" in OpenAI's terms, but the UI shows the
   teammate as a separate chat message, so the transcript mixes both models. The ordering
   bug fixed in Task 5 came from exactly this. (OpenAI handoffs vs agents-as-tools.)
4. **Approvals live only in memory.** `ApprovalBroker` (`approvals.py`) blocks a thread
   with a timeout. If the app restarts while an approval is pending the turn is lost, and
   there is no "resume this run". (LangGraph `interrupt()` + checkpointer.)
5. **No sandbox layer separate from approvals.** `terminal_run` is guarded by a regex of
   dangerous commands (`approvals.py` `_DANGEROUS_COMMAND`). That is Hermes' approach and
   works, but a regex cannot see what a script does once it runs. Codex and OpenHands put
   commands in a sandbox (workspace-write, no network) and ask only when leaving it.
   Docker Desktop is now installed on this machine, so this is possible. (Codex sandboxing; OpenHands Runtime.)
6. **No tracing or eval set.** Events stream to the UI and tool runs are persisted
   (`agent/state.py` `create_tool_run`), but there is no per-turn trace view and no fixed
   set of real prompts to re-run after a harness change. (Anthropic multi-agent post:
   "start with ~20 queries"; Agents SDK tracing.)
7. **Group-chat termination is implicit.** `runtime.py` `_route` caps at two responders
   (four with @mentions) and each speaks once. There is no "keep talking until X" mode and
   no explicit termination condition. (AutoGen termination conditions.)
8. **Two runtimes still ship.** The legacy path (`agent/semantic_runtime.py`,
   `agent/turn_understanding.py`, `agent/model_control_plane.py`, `agent/task_runs.py`) is
   still imported for non-lean sources and fallbacks. Every behaviour has two
   implementations. (No reference system keeps a second loop.)
9. **Session search is weak.** History for the model comes from the last N timeline rows
   (`runtime.py` `_history`); finding an old conversation depends on vector memory
   (`agent/memory.py`). Hermes uses SQLite FTS5 over sessions, which is cheap and exact.
10. **No hooks.** Behaviour like "log every terminal command" or "block writes outside the
    project" needs code changes in `loop.py`. (Agent SDK hooks.)

---

## 3. Recommendations

### Upgrade

| # | Change | Where | Effort | Impact |
| --- | --- | --- | --- | --- |
| U1 | Replace pure trimming with **summarizing compaction**: when `_fit_context` would drop messages, ask the same model for a short summary of the dropped span, insert it as one message, emit a `compact` event, and show a divider in the chat | `loop.py` `_fit_context`, `liveReducer.ts`, `LeanMessage.tsx` | M | High: long coding sessions keep their goals and decisions |
| U2 | Make budget exhaustion a **typed stop**: `agent_done` carries `stop_reason: "max_steps"` and the UI offers "Continue" which resumes with the same messages | `loop.py` `run`, `LeanMessage.tsx` | S | Medium: honest "ran out" instead of a rushed answer |
| U3 | **Persist pending approvals** with the turn's message list so a restart can resume the run from the approval point | `approvals.py`, `agent/state.py`, `lean_routes.py` | L | Medium: no lost work after a crash or restart |
| U4 | Pick one delegation model and make the UI match it. Recommended: keep "agent as tool" for the model, but render the teammate's reply as a **nested card inside the delegator's message** for background work, and use a true **handoff** (delegator stops, teammate continues the chat) when the user should see the teammate speak | `runtime.py` `_native_tools`, `LeanMessage.tsx` | M | High: transcript always reads in the order things happened |
| U5 | Add FTS5 **session search** as a native tool (`session_search`) over persisted assistant/user items | `agent/state.py`, `runtime.py` `_native_tools` | S | Medium: "what did we decide last week" works reliably |

### Remove

| # | Change | Where | Effort | Impact |
| --- | --- | --- | --- | --- |
| R1 | Retire the legacy semantic runtime once channels (Discord, Telegram, routines) all call `run_lean_text` | `agent/semantic_runtime.py`, `agent/turn_understanding.py`, `agent/model_control_plane.py`, `agent/task_runs.py`, call sites in `api/server.py` | L | High: one loop to debug; smaller, faster backend |
| R2 | Drop the regex-only safety for terminal commands **after** S1 lands (keep it as a fallback when Docker is not running) | `approvals.py` `_DANGEROUS_COMMAND` | S | Low alone; fewer false prompts with S1 |
| R3 | Remove the forced no-tools "final answer" call at budget end (superseded by U2) | `loop.py` `run` `else:` branch | S | Low: one less model call; clearer state |

### Add

| # | Change | Where | Effort | Impact |
| --- | --- | --- | --- | --- |
| A1 | **Sandboxed terminal** option: run `terminal_run` in a Docker container with the project mounted read-write and network off; ask only for commands that need the host or network (Codex `workspace-write` model) | `lean/terminal.py`, `approvals.py`, settings | L | High: coding agents can run tests and scripts freely without risking the machine |
| A2 | **Eval set**: 20 real prompts (chat, coding, group chat, delegation) with checks on events (did it finish, which tools, message order). Run against Gemma 4 E4B after each harness change | new `apps/backend/tests/evals/` | M | High: catch harness regressions before the user does |
| A3 | **Trace view**: a per-turn panel listing each model call (tokens, duration) and tool call from the existing NDJSON events | `apps/web/src/lean/`, events already emitted | M | Medium: easier to see why a turn went wrong |
| A4 | **Hooks**: a small `pre_tool` / `post_tool` / `turn_end` callback list in `LeanTurn`, configured from settings (log, block, rewrite args) | `loop.py`, `settings.py` | M | Medium: policy changes without editing the loop |
| A5 | Group-chat **termination modes**: "one reply" (today), "discuss until someone says DONE or N messages", round-robin. Reuse `_model_route` as the selector | `runtime.py` `_run_locked`, room settings | M | Medium: real back-and-forth between agents when wanted |
| A6 | **Parallel delegation**: when an agent delegates to two teammates in one step, run them concurrently (each in its own context) like the Anthropic research system | `runtime.py` delegate tool marked parallel-safe with its own emit stream | M | Medium: faster multi-agent answers; watch VRAM on local models |

### Suggested order

U2 and R3 (small, honest stops) → U1 (compaction) → A2 (eval set, so the rest can be
measured) → U4 → R1 → A1. Leave A6 until the machine can run two models comfortably.

---

## 4. Caveats

- Numbers quoted from Anthropic's multi-agent post (90% faster, ~15× tokens) are for
  frontier hosted models; local 4–9B models will see smaller gains and higher failure
  rates from long contexts.
- Docker-based sandboxing (A1) needs WSL2 and Docker Desktop running; the install is
  done but requires a reboot before first use.
- Effort estimates assume one developer familiar with the codebase.
