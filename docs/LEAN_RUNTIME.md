# Lean runtime (EchoSpeak 8.1)

The lean runtime replaces the gated v8 turn pipeline with one Hermes-style
agent loop and adds a Grok Bot-style layer of agents and group chats on top.
It is the default. Set `ECHOSPEAK_LEAN_RUNTIME=0` to fall back to the legacy
runtime while it still exists.

## The loop

```
system prompt (SOUL / persona, rules, team, environment, memory)
  + real chat history + user message
    -> model (streamed: reasoning, text, native tool calls)
    -> run the tools it asked for (read-only ones in parallel)
    -> append results, repeat
  -> done when the model replies without calling a tool
```

Code: `apps/backend/agent/lean/`

| File | Owns |
|---|---|
| `provider.py` | OpenAI-compatible streaming client for every provider; reasoning channel, `<think>` tag scrubbing across chunks, tool-call assembly, text-encoded tool-call recovery |
| `toolbox.py` | Named toolsets (Hermes style), compact schemas for small models, tool execution with a clean tool context |
| `approvals.py` | Smart approvals: only destructive, outward-facing, desktop-control, and dangerous terminal commands pause |
| `loop.py` | `LeanTurn`: the loop, streaming events, context trimming, repeat detection, nudges |
| `prompt.py` | Short system prompt tuned for 4B to 9B local models |
| `personas.py` | Agent roster (`DATA_DIR/lean/agents.json`); Echo is built in and uses `SOUL.md` |
| `rooms.py` | Direct and group chats (`DATA_DIR/lean/rooms.json`), each backed by a normal Session |
| `runtime.py` | `LeanSession`: Session lock, persistence, history, group routing, delegation, memory tools |

Nothing in the loop ends a turn because a classifier, requirement ledger, or
completion evaluator disagreed. Tool errors, bad arguments, unknown tools,
denied approvals, and repeated calls all go back to the model as tool results.

### What still stops a turn

- The user presses Stop.
- The model replies with no tool calls (it is done).
- The iteration budget (`LEAN_MAX_ITERATIONS`, default 60) runs out; the model
  then gets one tool-free call to summarize.
- The provider fails (server down, model not loaded).

### Settings (env var or settings.json key)

| Name | Default | Meaning |
|---|---|---|
| `ECHOSPEAK_LEAN_RUNTIME` | on | Use the lean runtime |
| `LEAN_MAX_ITERATIONS` | 60 | Model calls per agent reply |
| `LEAN_CONTEXT_TOKENS` | max(local context, 64000) | Context budget for trimming |
| `LEAN_MAX_OUTPUT_TOKENS` | 8192 | Per model call |
| `LEAN_APPROVAL_MODE` | smart | `smart`, `always` (every action tool), `never` |
| `LEAN_APPROVAL_TIMEOUT_SECONDS` | 600 | Unanswered approvals are treated as denied |

Approvals only wait in interactive sources (web, desktop, voice). Discord,
Telegram, and routines get an immediate "needs approval in the app" result.

## Agents and group chats

- Each agent has a name, role, description, personality, toolsets, and an
  optional model override.
- Direct chat with an agent = a `direct` room. Group chat = a `group` room.
- In a group, `@Name` (or `@all`) picks who answers. Otherwise a small routing
  call picks one or two agents from their descriptions. Routing never decides
  which tools anyone has.
- Any agent can `delegate_to_agent` a teammate (depth 2). The teammate's reply
  appears as its own message.

## Stream events (`/query/stream`, NDJSON)

`run_start`, `routing`, `agent_start`, `step_start`, `reasoning_delta`,
`agent_token`, `text_replace`, `tool_start`, `tool_end`, `approval_request`,
`approval_resolved`, `delegation`, `memory_saved`, `token_usage`,
`agent_done`, then `final` (`runtime: "lean"`). Every agent event carries
`message_id` and `agent_id`. The web client batches them per animation frame
(`apps/web/src/lean/useLeanLive.ts`), and the committed message is exactly the
streamed one, so nothing flashes or gets replaced.

HTTP: `GET /lean/status`, `GET|POST /lean/agents`, `PATCH|DELETE
/lean/agents/{id}`, `GET|POST /lean/rooms`, `PATCH|DELETE /lean/rooms/{id}`,
`GET /lean/toolsets`, `GET /lean/approvals`, `POST /lean/approvals/{id}`
with `{"decision": "allow" | "deny" | "always"}`.

## Native coding harness (`agent/lean/coding.py`)

Echo codes itself; nothing is delegated to Codex or OpenCode.

| Tool | Purpose |
|---|---|
| `file_read` | Line-numbered read with `offset`/`limit` paging |
| `file_edit` | Exact-text replace (unique match or `replace_all`), returns a diff; tolerates pasted line numbers and CRLF |
| `file_search` | Regex search across a project, skipping `node_modules`, `.git`, build output |
| `file_find` | Glob file finder |
| `file_write`, `file_mkdir`, `file_move`, `file_copy`, `file_delete`, `checkpoint_undo` | Existing tools; every overwrite is checkpointed |

When a project folder is attached, the system prompt includes a top-level listing
so the agent knows where things are before its first tool call.

## Terminal (`agent/lean/terminal.py`)

`terminal`, `process_output`, `process_stop` (`process_start` still works as an alias for
`terminal` with `background: true`).

- **host**: PowerShell on this PC (pwsh 7 if installed). Pipes, `$variables`, and `;` chaining work.
- **docker**: one persistent container `echospeak-sandbox` built from
  `node:22-bookworm` + Python, git, ripgrep. Only the workspace folders are
  mounted, at `/work/<folder>`. Windows paths in commands are translated. The
  container survives between commands, so installs persist. Network on by
  default (`terminal_docker_network`). Docker Desktop is started automatically
  if it is installed but not running.
- A short never-run list blocks disk/boot/OS destruction. Everything else risky
  goes through approvals.
- A command waits up to `timeout` seconds (default 120, max 900). Still running then,
  it is not killed: it keeps going as a background process and the agent gets its
  `process_id` and the output so far. Servers and watchers (`npm run dev`, `vite`,
  `uvicorn`, `--watch`, ...) go to the background after a 6s look. At most 8 run at once.
- No keyboard input: stdin is closed and git/pip prompts, pagers and editors are off.
  Interactive commands (vim, less, a bare REPL, `npm init`, `git add -p`) are refused
  with the non-interactive form to use.
- Results: exit code, stdout, a separate `[stderr]` section, and a one-line hint for
  common failures. Files overwritten by shell redirection are checkpointed first.

Settings > Terminal shows live status and has Set up / Reset buttons
(`GET /lean/terminal`, `POST /lean/terminal/setup`, `POST /lean/terminal/reset`).

## Thinking controls

The composer's Think toggle and Effort picker map to `reasoning_effort` on the
request: off sends `none` (LM Studio honors this for Gemma and Qwen), on sends
low/medium/high. Cloud models only receive it if they support it; a provider
that rejects it is retried without it.

## Automations (`agent/lean/automations.py`)

Routines (schedule, manual, webhook) and the heartbeat run on the lean loop
with `source="routine"` / `"heartbeat"`. Results land in the routine's own
chat ("Routine · Name", created on first run) or the "Heartbeat" chat, and can
be delivered to Discord (owner DM) and Telegram (allowed users). Schedules are
cron expressions in local time. No Project binding is needed.
Routes: `GET|POST /lean/routines`, `PATCH|DELETE /lean/routines/{id}`,
`POST /lean/routines/{id}/run`.

## Settings (`apps/web/src/settings/`)

A sidebar of sections (General, Models, Agents, Personality, Permissions,
Terminal, Web search, Memory, Automations, Channels, About) with rows that save
instantly through `PUT /settings`. Rare panels (MCP, connections, skills,
voice, avatar, projects, advanced) open in "Classic settings".

## Removing the legacy runtime

Once the lean path is confirmed in the desktop app, the following become dead
and can be deleted together: `semantic_runtime.py`, `turn_understanding.py`,
`model_control_plane.py`, `model_contracts.py`, `model_conformance.py`,
`task_runs.py`, `execution_graph.py`, the requirement/sufficiency parts of
`research_runtime.py`, and the `_pq_*` / control-plane half of `core.py`,
plus their tests. Automations and specialist continuation still call into
TaskRun and need a small port to `LeanSession` first.
