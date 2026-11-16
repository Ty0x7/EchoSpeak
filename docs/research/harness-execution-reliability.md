# Harness execution reliability: lifecycle, tool contracts, observability, concurrency

> **Status, 16 November 2026. Research only, for review; no code was changed for it.**
>
> This was done on branch `five-model-harness`, alongside the two-model evaluation, without touching it.
>
> **Evidence labels:**
> - **Confirmed:** read in the code, with `file:line` (paths relative to `apps/backend/`). The four most serious
>   findings were re-read by hand as well as by the audit.
> - **Research:** from the primary sources linked.
> - **Proposal:** a recommendation, not done.
>
> **Out of scope** (separate work): model capability, provider differences and the terminal sandbox (see
> [provider-and-isolation.md](provider-and-isolation.md)).

## 1. Confirmed: what EchoSpeak has today

### 1.1 Lifecycle

**The loop.** One loop, `LeanTurn._run` (`agent/lean/loop.py:400-716`), with no separate planner. Each step runs:
1. an optional deterministic prefetch (`:429`);
2. steering and context fit;
3. the model call (`:742`);
4. recovery of tool calls written as text (`:480`);
5. the tools (`:897`);
6. results added as `role: tool` messages.

**Stops:**
- cancel;
- a provider error;
- an accepted final reply;
- `complete_task`;
- 10 refusals;
- a handoff;
- `max_iterations`, default 60 and at most 500 (`settings.py:30`);
- the work budget, 64k tokens or 900 s (`coordination.py:198`, `admission.py:97`).

**Claimed vs actual progress: a real strength.**
- A final reply must pass a chain of checks (`loop.py:508-592`): forged notes, promises, unbacked claims, check
  after edit, unfinished work, invented quotes, prose menus, and the final check.
- The `VerificationLedger` (`verification.py:298-682`) parses checks only from commands that actually ran. It
  discounts masked exit codes, tests edited after they failed, and hard-coded expected values.
- It sets the status (verified, checks pass, partial, failing, unverified, blocked) from evidence. It then holds the
  "done" wording in the reply against that status (`:565-597`).

### 1.2 State, crash and resume

**Three stores:**
- the query journal (SQLite `runs` and `events`, written on every event; `query_journal.py:12-29`,
  `api/routes/chat.py:522-557`);
- `state.db` (executions, tool runs, approvals; `state.py:36-56`);
- the work ledger `coordination.json` (works, attempts, actions with intent and result, approvals, deliveries; one
  JSON document rewritten atomically, `coordination.py:141-146`).

**After a crash:**
- running journal runs become `interrupted` (`query_journal.py:25`);
- `Coordinator.reconcile` turns in-flight actions into `unknown` (`coordination.py:610-630`);
- resuming is a manual owner action that requires a reconciliation note for uncertain effects
  (`coordination.py:320-327`, `api/routes/lean.py:44-58`). It starts a *new* turn from a brief (`runtime.py:1264`).

**Never persisted mid-turn:**
- the message list;
- the turn's timeline (`runtime.py:1307-1336`);
- the `VerificationLedger`, saved only in `coordinator.finish` (`runtime.py:324-333`).

No startup reconciliation was found for running `tool_runs` in `state.db`.

**File checkpoints (`agent/checkpoints.py`):**
- text only (`read_text(errors="replace")`, `:83`);
- one global index of 50 entries, read and rewritten without a lock (`:51`, `:96-112`);
- new files aren't recorded, so undo can't remove them (`:79`);
- undo overwrites without checking the current content, and not atomically (`:166-172`).

### 1.3 Cancellation

- **The model stream** is checked once per SSE line (`provider.py:650`). During a long prefill, Stop waits for the
  next line, or the 600 s read timeout.
- **Tools are checked only between sequential tools** (`loop.py:982`).
  - The parallel pool isn't interrupted (`:965-973`).
  - A foreground terminal command never checks cancel (`terminal.py:924-935`): it runs until its wait ends (up to
    900 s), then turns into a background process.
  - Approval waits and computer actions do check it (`approvals.py:373`, `computer.py:404`).
- **Background processes survive Stop.** `stop_all_processes` is called only at server shutdown
  (`api/server.py:323-325`), not by `/query/cancel` (`chat.py:238-297`) or "stop everything".
  - A Docker exec that times out leaves its process running in the container (`terminal.py:506`).
  - Host processes started in their own session outlive a backend crash, and nothing re-finds them (`_PROCESSES` is
    in memory only, `terminal.py:742`).
- **`/query/cancel` marks the journal `cancelled` before the loop has actually stopped** (`chat.py:291`).

### 1.4 Retries and side effects

**Already in place (good):**
- Resubmits with the same `client_request_id` and fingerprint replay the journal instead of running again
  (`chat.py:457`, `query_journal.py:43`).
- Action IDs are derived from the attempt and the call (`coordination.py:359`).
- `previous_effect` refuses to repeat a write that completed in an earlier attempt (`:398`, `loop.py:1507`).
- Identical calls are blocked from the third time on (`loop.py:909`), and refused or denied calls are never retried
  (`:1044`).
- Exceptions are classed as certain or uncertain (`toolbox.py:235`). Uncertain ones become `unknown` actions that
  need reconciling.
- Approvals bind to an argument hash, file stat and policy snapshot, and expire after 24 h (`coordination.py:34-50`,
  `:423-479`).

**Missing:**
- **No retry for transient model errors.** 429, 5xx or a dropped connection ends the turn (`loop.py:464-471`). An
  incomplete tool-call stream also ends it, as a 502 (`provider.py:723`).
- **MCP failures are recorded as success (defect, confirmed by hand).**
  - On a transport error, timeout or unavailable server, `call_tool` returns a JSON *string*
    `{"isError":true,…}` with no `content` (`mcp_client.py:498-511`).
  - `mcp_images` passes it through unchanged (`observations.py:174-178`).
  - The success test checks `isError` only on a dict, and `looks_failed` only on prose prefixes
    (`toolbox.py:930`, `:225`).
  - Result: `ok=True`, and the action is recorded `completed`. `previous_effect` would then refuse a genuine retry,
    and a timeout (an *uncertain* outcome) is never marked uncertain.

### 1.5 Tool contracts

**Declaration.** `NativeTool` has a name, a JSON-Schema `parameters`, `parallel_safe`, `ends_turn`, … (`toolbox.py:266`).

**Validation differs by tool kind:**
- native tools: none (each function coerces its own arguments);
- registry tools: lax pydantic (`tool_decorator.py:50`);
- MCP: required keys only (`mcp_client.py:841`).

These come after a JSON repair pass (`provider.py:213`), alias normalisation (`toolbox.py:617`) and recovery of
calls written as text. An unknown tool gets an "unavailable" result rather than a crash (`toolbox.py:744`).

**Error shapes: three.**
- Prose prefixes, judged by `looks_failed` (`toolbox.py:190-225`).
- `ToolResult.outcome` (`evidence.py:39`).
- MCP's JSON `isError`.

**Timeouts.** No timeout per tool in the toolbox (`toolbox.py:774-959`). The terminal has its own (120 s, up to
900 s, then background) and MCP its own (15 s, 1–300).

**Parallel calls (defect, confirmed by hand).**
- When two or more calls in a step are on the read-only list (`toolbox.py:145`), they run first in a pool of 4
  (`loop.py:959-975`), **before** the step's other calls, whatever order the model gave. A read can observe the state
  before a write the model listed earlier.
- **That path never calls `_approve`.** An `ask` decision is recorded in `_policy_reasons` (`:930`), but it's enforced
  only inside `_approve` (`:1329`), which runs only for sequential calls (`:1043`). Policy *denials* are still
  enforced.

**Output limits.** 14,000 characters into context, with the overflow saved for `read_output` (`loop.py:1068`,
`:1164`).

### 1.6 MCP

- **Client.** The official SDK, over stdio, streamable HTTP and SSE (`mcp_client.py:244-352`). Stdio servers get
  `child_env` and `npm_config_ignore_scripts`.
- **Trust.** A new or changed server must be trusted before it starts (`:665-687`). `destructiveHint` always asks
  (`:724-751`).
- **Defect: tools can change without re-approval.** A `list_changed` notification re-registers tools without the trust
  check startup applies (`:290-294`, `:699-722` vs `:680`). A running server can add or change tools without being
  approved again.

### 1.7 Checking that effects really happened

- **Files.** Existence and size after write, delete, copy or move (`evidence.py:80-108`). The diff comes from
  before/after sha256 (`file_changes.py:95-122`). The approval preview's `base_sha256` is never checked when the
  write happens (`file_changes.py:217`).
- **Terminal.** The exit-code header becomes a `process` effect. The ledger parses runner output.
- **Screen.** Pixel change.
- **Connectors, email, other apps.** No post-condition checks. Deliveries have receipt states and manual
  reconciliation (`coordination.py:515-547`).

### 1.8 Observability and privacy

**Events.** About 40 event types share one envelope (`request_id`, `agent_id`, `message_id`, `at`; `loop.py:269-281`),
with no schema version.

**Traces.**
- A per-turn `trace_id` with spans (`tracing.py:53-113`), kept for 7 days or 64 MB, with optional OTLP export.
- **Events carry no trace or span ID**, so a journal event can't be joined to its span.

**Audit log.** `data/security/tool-audit.jsonl` stores argument hashes only (`policy.py:300-318`). No rotation was
found.

**Journal retention.** 2 days, with `secure_delete` (`query_journal.py:34-41`). The journal stores streamed answers,
reasoning, 1,600-character tool output previews, full approval arguments and file diffs. It does not store the system
prompt or the full message list.

**Redaction leaks (defect).**
- `tool_start.input` (`loop.py:1552`) and the `tool_runs` canonical arguments (`:1558`) aren't redacted. The timeline
  copy is (`:1550`).
- `redact_secrets` knows only EchoSpeak's own keys (`policy.py:160-191`). Project secret files are masked in tool
  output (`secret_files.guard_output`), but not in arguments.

### 1.9 Deterministic tests

**Scripted clients.**
- `ScriptedClient` (`tests/test_lean_runtime.py:103-118`) plays back `ModelTurn`s and records what each call
  received. About 20 test files use it.
- At least 8 other test files define their own scripted client.

**Replays and fixtures.**
- `agent/lean/replays.py` (`ScriptedTurns`) and `tests/replays/`.
- A mock MCP server.
- One crash test (`test_native_coordination.py:66`).

**Missing:** no way to inject faults into the provider, terminal or MCP.

### 1.10 Concurrency

- **Per session.** A lock per session (`runtime.py:110`) and one running journal run per session. Admission: 4 cloud
  calls, 1 local.
- **Across sessions.**
  - Cooperative resource reservations per project (`coordination.py:64-72`, `:367-391`): a conflict is refused, not
    queued.
  - No file locks, and no check that a file changed since the agent read it.
- **Process-wide state:**
  - `_PROCESSES` (any session can read or stop any process by ID, `terminal.py:742`, `:1053`) and `MAX_BACKGROUND`;
  - the single Docker container and its network switch;
  - the approval broker;
  - the checkpoint index.
- **Multi-agent.** Handoff (`runtime.py:1597-1691`), parallel fan-out (`:961-990`), a read-only swarm with child work
  records (`swarm.py`), A2A, and external code sessions.

### 1.11 Duplication and dead code

- **Dead:** `agent/stream_events.py` and `/stream/{request_id}` have a reader and no writer (`chat.py:430-448`).
- **Legacy, probably still reachable** (inferred): `agent/sandbox.py`'s container sits behind the registry tool
  `terminal_run`, for personas with every tool (`tools.py:1277`, `toolbox.py:368`).
- **Overlaps:**
  - Approvals are stored three ways: the in-memory broker, `state.db` and `coordination.json`.
  - Two cancel registries: `stop._running` and `api.deps._ACTIVE_QUERY_CANCELLATIONS`.
  - Four verification layers: the ledger, `evidence.py` outcomes, `job.py` claim checks and the episode V-ladder.
- **Staged, not wired** (accurate as of this writing): the bubblewrap sandbox (`agent/lean/linux_sandbox.py`) is
  imported only by its tests. Its terminal wiring is staged until the running evaluation finishes, and
  provider-and-isolation.md describes the wired state.

### 1.12 Windows and Linux

- **Shells and processes.**
  - PowerShell `-NonInteractive` vs `bash -lc`, with Git Bash detection.
  - `CREATE_NEW_PROCESS_GROUP` vs `start_new_session`.
  - `taskkill /T /F` vs `killpg(SIGTERM)` (`terminal.py:600-808`).
- **Gap on Linux.** The follow-up `popen.kill()` hits only the group leader, so children that ignore SIGTERM survive.

## 2. Research: what other harnesses do

| Technique | Who | Status for a local, single-user agent | Source |
|---|---|---|---|
| Append-only event log as the record; resume by ID | Claude Code sessions, Codex rollouts, OpenHands `events/` | Proven | [Claude sessions](https://code.claude.com/docs/en/agent-sdk/sessions), [OpenHands persistence](https://docs.openhands.dev/sdk/guides/convo-persistence) |
| "Sessions persist the conversation, not the filesystem" | Claude Agent SDK | Proven (stated limit) | [sessions](https://code.claude.com/docs/en/agent-sdk/sessions) |
| File snapshots per turn with rewind; Bash edits not covered | Claude Code | Proven; pair with git | [checkpointing](https://code.claude.com/docs/en/checkpointing), [file checkpointing](https://code.claude.com/docs/en/agent-sdk/file-checkpointing) |
| Net diff per turn from first-touch baselines (git blob hashes) | Codex `TurnDiffTracker` | Proven; patch-tool edits only | [source](https://github.com/openai/codex/blob/main/codex-rs/core/src/turn_diff_tracker.rs) |
| Durable idempotency key **before** the side effect; lost acknowledgement means outcome unknown; check before compensating | LangGraph | Proven principle | [fault tolerance](https://docs.langchain.com/oss/python/langgraph/fault-tolerance.md) |
| "Approval permits a tool call to execute; it does not guarantee exactly-once execution" | OpenAI Agents SDK | Proven (documented caveat) | [human in the loop](https://openai.github.io/openai-agents-python/human_in_the_loop/) |
| Serializable paused run (approve or reject, then resume) | OpenAI `RunState`, LangGraph `interrupt` | Proven | [interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts.md) |
| Store sibling writes that succeeded so a retry doesn't redo them | LangGraph | Proven; too heavy to copy wholesale | [checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers.md) |
| Tool failure returned as a result (`isError`); protocol errors separate | MCP 2026-07-28, Claude SDK, OpenAI `failure_error_function` | Proven | [MCP tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools.md), [Claude custom tools](https://code.claude.com/docs/en/agent-sdk/custom-tools) |
| Per-tool timeout returned as an error result | OpenAI SDK (`timeout`, `timeout_behavior`), LangGraph node timeouts; MCP says SHOULD | Proven | [OpenAI tools](https://openai.github.io/openai-agents-python/tools/) |
| Unknown tool returned to the model as an error | OpenAI `tool_not_found_behavior` | Proven | [running agents](https://openai.github.io/openai-agents-python/running_agents/) |
| Output schema; client validates `structuredContent` | MCP, Codex `--output-schema` | Proven | [MCP tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools.md) |
| Read-only tools in parallel, mutating ones in order | Claude Agent SDK | Proven | [agent loop](https://code.claude.com/docs/en/agent-sdk/agent-loop) |
| A parallel step is all-or-nothing; reducers for shared keys | LangGraph | Proven | [control flow](https://docs.langchain.com/oss/python/langgraph/graph-api-control-flow.md) |
| Tool annotations from an untrusted server never drive safety decisions | MCP spec | Rule | [schema](https://modelcontextprotocol.io/specification/2026-07-28/schema.md) |
| Cancellation: cooperative, a maximum timeout always applies, tolerate the cancel/complete race | MCP, LangGraph `request_drain`, OpenAI `cancel(mode="after_turn")` | Proven | [MCP cancellation](https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/cancellation.md) |
| Typed ends of a run (success, max turns, budget, error during execution) | Claude SDK, OpenAI | Proven | [agent loop](https://code.claude.com/docs/en/agent-sdk/agent-loop) |
| Stuck detection (repeat, error loop, monologue, ping-pong) | OpenHands | Proven, simple | [stuck detector](https://docs.openhands.dev/sdk/guides/agent-stuck-detector) |
| Scripted model with failure injection and drift detection | OpenAI `ScriptedModel` / `ModelStep.raise_error()` | Proven | [testing](https://openai.github.io/openai-agents-python/testing/) |
| Typed spans (agent, generation, function, guardrail, handoff) | OpenAI SDK; Claude Code (beta) | Proven / beta | [tracing](https://openai.github.io/openai-agents-python/tracing/), [monitoring](https://code.claude.com/docs/en/monitoring-usage) |
| Content redacted by default, opt-in per category | Claude Code OTel; GenAI semconv (Development) | Proven / experimental | [monitoring](https://code.claude.com/docs/en/monitoring-usage), [GenAI agent spans](https://raw.githubusercontent.com/open-telemetry/semantic-conventions-genai/main/docs/gen-ai/gen-ai-agent-spans.md) |
| Subagent caps (depth, concurrency, budget); only the final output returns | Claude SDK | Proven | [subagents](https://code.claude.com/docs/en/agent-sdk/subagents) |
| Durable tasks for long operations | MCP Tasks extension | Experimental | [tasks](https://modelcontextprotocol.io/extensions/tasks/overview.md) |
| Risk rating by an LLM or classifier for auto-approval | OpenHands analyzer, Claude auto mode | Experimental, non-deterministic | [OpenHands security](https://docs.openhands.dev/sdk/guides/security) |
| Workflow-engine durable execution | Temporal, DBOS, Restate | Overly complex here | [Temporal + Agents SDK](https://github.com/temporalio/ai-integrations/tree/main/python/openai_agents) |
| Time-travel replay that re-runs side effects | LangGraph | Unsuitable for side-effecting tools | [time travel](https://docs.langchain.com/oss/python/langgraph/use-time-travel.md) |
| Sensitive data in traces by default | OpenAI SDK (`trace_include_sensitive_data=True`) | Avoid for local privacy | [tracing](https://openai.github.io/openai-agents-python/tracing/) |

**Research conclusions:**
1. **No framework provides exactly-once tool execution.** The proven pattern is the one EchoSpeak's work ledger
   already half-implements:
   - record the intent durably before the effect;
   - record the result after;
   - treat anything in between as *unknown*;
   - reconcile before retrying.
2. **Resume means resuming the conversation, not replaying effects.** Claude, OpenAI and LangGraph all restart from
   the last recorded point and re-run what came after, which is why they all warn about side effects. EchoSpeak's
   refusal to auto-resume uncertain work is stricter than most and should stay.
3. **The common contract for parallel calls:** reads run concurrently, writes run in order, and approvals apply to
   every call.
4. **MCP's 2026-07-28 revision is a breaking change** (stateless, multi-round-trip requests instead of
   server-initiated requests, no SSE resumability). EchoSpeak's client depends on the SDK following it. No
   framework documents file-conflict detection between agents beyond serialising writes and using worktrees.

## 3. Proposal: prioritised improvements

**Critical:** correctness or safety defects confirmed in code. Each is small and testable with the existing
`ScriptedClient`.

| # | Change | Why | Size |
|---|---|---|---|
| C1 | **One tool-result contract.** Every tool returns `{status: ok \| error \| timeout \| uncertain \| refused, output, effects}`. Fix MCP: a transport error is `error`, and a timeout is `uncertain` (an `unknown` action, never `completed`). | MCP failures are recorded as success, and `previous_effect` then blocks real retries (§1.4) | S |
| C2 | **Parallel calls keep order and approvals.** Run in parallel only *consecutive* read-only calls with no `ask` decision; everything else goes through `_approve`, in the model's order. | Approvals are skipped and calls reordered (§1.5) | S |
| C3 | **Stop stops.** Kill the process group of a running foreground command when Stop is pressed. On Stop or "stop everything", stop the turn's background processes (or list them and ask, for dev servers). Mark the journal `cancelled` only after the loop acknowledges. Persist process IDs and start times so orphans are found after a crash. | Work continues after Stop (§1.3) | M |
| C4 | **Re-trust tools when an MCP server changes them.** Apply the startup `tools_ok` check to `list_changed`. | A running server can add or change tools unapproved (§1.6) | S |
| C5 | **Redact tool arguments** in `tool_start` and `tool_runs` the same way as the timeline. | Secrets in arguments reach the journal and `state.db` (§1.8) | S |

**Valuable:**

| # | Change | Why | Size |
|---|---|---|---|
| V1 | **Retry transient model errors** (429, 5xx, a connection dropped before any tool call), 2–3 times with backoff, honouring `Retry-After` and cancel. Model calls have no side effects. | One hiccup ends a long task (§1.4) | S |
| V2 | **Write only what was read.** File writes and edits carry the hash the agent last read (or the approval's `base_sha256`). A mismatch is a `stale` error telling the model to read again. | Concurrent sessions or the owner can be overwritten silently (§1.7, §1.10) | S–M |
| V3 | **Per-tool timeout and schema validation in the toolbox**, *after* the existing repair and coercion, returning a structured error to the model. Measure its effect on the local models with the harness eval before keeping it. | Three validation regimes, no uniform timeout (§1.5) | M |
| V4 | **Better checkpoints.** Record created files (undo deletes them), store bytes rather than text, an index per session with a lock, atomic restore, and refuse to undo over a file changed since. | Undo can corrupt or miss changes (§1.2) | M |
| V5 | **Resumable turns.** After each step, persist the message list and the ledger next to the journal run. "Continue" resumes from the last completed step. Actions in `intent` or `unknown` still need reconciling first, as now. | Crash recovery is a fresh turn without the work so far (§1.2) | M–L |
| V6 | **Joinable records.** Add `schema_version`, `trace_id` and `span_id` to events. Add typed end reasons (`completed`, `max_steps`, `budget`, `cancelled`, `provider_error`, `tool_error`). | Debugging requires guessing joins; separating provider, harness and model failures is manual (§1.8) | S |
| V7 | **One scripted client with fault injection** (provider error, a tool timeout, an uncertain tool, cancel at step N), replacing the 8 local copies. | No fault tests; drift between copies (§1.9) | S–M |

**Optional:**
- O1. Stuck-detection patterns from OpenHands (an error loop, ping-pong) on top of the identical-call block.
- O2. OTel GenAI attribute names for the existing OTLP export, with content off by default.
- O3. MCP 2026-07-28 support (multi-round-trip elicitation, Tasks extension), when the Python SDK ships it.
- O4. Audit-log rotation.

**Not recommended:**
- A workflow engine (Temporal, DBOS);
- LLM risk analysers for auto-approval;
- time-travel replay of side effects;
- replacing the loop with any framework.

Each would add infrastructure or nondeterminism for problems EchoSpeak either doesn't have or already handles more
strictly.

## 4. Keep, improve, replace, remove

| Keep | Improve | Replace | Remove |
|---|---|---|---|
| The single loop and its final-reply checks; the `VerificationLedger`; the work ledger's intent/result/unknown and manual reconciliation; `previous_effect` and approval binding; journal replay of resubmits; `ScriptedClient`; per-session locks and resource reservations; `child_env` and `secret_files`; the 2-day journal retention | Tool-result contract (C1); parallel batching (C2); cancellation (C3); MCP re-trust (C4); redaction (C5); model retries (V1); checkpoints (V4); event schema (V6) | Three tool error shapes → one (C1). Two cancel registries → one. Approval storage: `coordination.json` is authoritative and the others are views. | `agent/stream_events.py` and `/stream/{request_id}` (dead). The registry `terminal_run` route to `agent/sandbox.py` (alias it to the native terminal everywhere). |

## 5. Risks, dependencies and tradeoffs

- **Stopping background processes on Stop** can kill a dev server the owner wants running. List them and ask, or keep
  the ones started with `background=true` until the session ends. Decide with the owner.
- **Schema validation can hurt local models** that send near-miss arguments today and are rescued by coercion.
  Validate after repair, return errors the model can act on, and measure with the harness eval (skill
  `model-harness-testing`).
- **Resumable turns persist more private content.** The message list includes file contents. Keep it under the
  journal's 2-day retention and `secure_delete`, exclude it in privacy offline mode if required, and never resume an
  `unknown` action without reconciling it.
- **Ordered parallel batches** make some read batches slower (only consecutive reads overlap). Accept it.
- **Stale-write checks** can bounce agents in a loop if the owner edits continuously. Cap retries and surface the
  conflict to the owner.
- **MCP's breaking revision** depends on the upstream Python SDK. Pin and test with `tests/fixtures/mock_mcp_server.py`.
- **Windows.** Process-group killing (C3) must use `taskkill /T`, and children that ignore SIGTERM on Linux need a
  follow-up SIGKILL to the group. Both need tests per platform.

## 6. Proposal: the lifecycle with these changes

```
 owner request ─► admission (slots, budget) ─► model call ──(429/5xx: retry V1)──┐
                                                   │                             │
                                     reply ◄───────┘◄────────────────────────────┘
                   ┌───────────────────┴───────────────────┐
             text only                                tool calls
                   │                                       │
   final-reply checks + ledger              validate (V3) ─► policy (deny/ask/allow)
   (claims vs evidence)                                    │
        │ pass        │ fail → note, next step   approval for every "ask" (C2)
        ▼                                                  │
   done (typed end reason, V6)           intent recorded (work ledger, durable)
                                                           │
                                         run: reads in parallel, writes in order (C2),
                                         per-tool timeout (V3), cancel kills (C3)
                                                           │
                                         one result contract (C1): ok/error/timeout/uncertain/refused
                                                           │
                                         effects checked (hashes, exit codes, stale writes V2)
                                                           │
                                         result recorded; ledger updated; step persisted (V5)
                                                           │
                                         observation → model (redacted log, C5) ─► next step
 crash ─► journal "interrupted", actions "unknown" ─► owner reconciles ─► resume from last step (V5)
```

The HTML version ([artifacts/research/harness-execution-reliability.html](../../artifacts/research/harness-execution-reliability.html))
draws this as a diagram.

## 7. Suggested order, after review

1. C1, C2, C4 and C5 together: small, each with a scripted-client regression test.
2. C3, after the owner decides about dev servers.
3. V1, V6 and V7.
4. V2 and V4.
5. V3, measured on the local models.
6. V5 last: it's the largest, and it depends on V6.
