# EchoSpeak harness architecture, November 2026: review, verdicts and upgrades

> **Status, 16 November 2026.** Branch `five-model-harness`; not merged or released.
>
> **Commits:**
> - `0a06264`: measurement fixes;
> - `120ccc9`: the harness upgrades;
> - earlier the same day: `05209a6`, `0b5f324`.
>
> **Reference model:** `qwen3.5:9b` (Ollama digest `56671c2ab938`; Qwen 3.5, 9.0B, Q4_K_M; tools, thinking on/off and
> vision; Ollama's `qwen3.5` renderer and parser; 16,384-token window served by Ollama 0.40.16).
>
> **Evidence labels:**
> - **Code:** read in the source, with references.
> - **Test:** an automated test.
> - **Live:** a recorded run with the reference model, judged by `scripts/harness_judge.py` with weights
>   30/25/20/15/10.
> - **Research:** a primary source linked in the companion reports.
>
> This document joins today's separate reviews into one picture. Details stay in:
> - [harness-execution-reliability.md](harness-execution-reliability.md): lifecycle, tools, observability,
>   concurrency;
> - [provider-and-isolation.md](provider-and-isolation.md): providers, sandbox, threat model;
> - [output-budgets-and-termination.md](output-budgets-and-termination.md): limits, reply endings, memory;
> - [check-integrity-experiment.md](check-integrity-experiment.md): learning and skills, plus a controlled
>   experiment.

## 1. How a turn flows

```
 owner / channel / routine
        │ request
        ▼
 Runtime (agent/lean/runtime.py) ── environment: Chat · Code · Agents ── persona: tools, model, budget
        │ builds: system prompt (prompt.py / chat_mode.py) · toolbox (toolbox.py) · work record (coordination.py)
        ▼
 LeanTurn loop (agent/lean/loop.py) ◄────────────── steering inbox · Stop (cancel event)
   │ fit context (window from the server: window.py) · compact old steps
   │ admission (one local call at a time) · work budget check ── exhausted → stop "budget"
   ▼
 Provider (provider.py) ── Ollama / LM Studio / cloud ── SSE stream: reasoning · text · tool calls · finish · usage
   │ transient failure → retry ≤2 (nothing shown yet) · incomplete stream / empty reply → cause + bounded nudge
   ▼
 Tool calls ── policy (deny / ask / allow) ── approval card (owner) ── work ledger: intent
   │ run: reads in parallel only if nothing asks; otherwise in order
   │ terminal → bubblewrap sandbox (Linux) / Docker / host-with-approval · MCP · files · desktop
   ▼
 Results ── one outcome (ok · failed · uncertain · refused) · secrets redacted · effects observed
   │ Verification ledger: checks that actually ran (tests, builds), special-casing, masked exits
   ▼
 Final reply ── claim checks (unbacked edits, promises, forged notes, quotes) vs evidence
   │ end reasons: answered · no_answer(cause) · budget · max_steps · refusals · cancelled
   ▼
 Records: query journal (2 days) · state.db (chat, tool runs) · work ledger · traces (7 days) · episodes (learning)
```

The HTML version ([artifacts/research/harness-architecture-2026-11.html](../../artifacts/research/harness-architecture-2026-11.html))
draws this as a diagram.

## 2. The 20 areas: what exists, the verdict, what changed

The verdicts are **Retain**, **Upgrade**, **Replace** or **Remove**. "Changed" lists today's commits.

| # | Area | What EchoSpeak has (verified) | Comparison | Verdict | Changed |
|---|---|---|---|---|---|
| 1 | Model interfaces and providers | One OpenAI-compatible stream for Ollama and LM Studio, plus Anthropic, Gemini and OpenAI Responses adapters. It reads both reasoning fields, recovers text-form tool calls, refuses partial tool calls, and learns the served window. | The same split as the OpenAI and Claude SDKs. Ollama's `/v1` drops `num_ctx`; LM Studio reports the window per loaded instance. | Upgrade | Served window for LM Studio; incomplete-stream detection; transient retries |
| 2 | System instructions and agent configuration | Ordered prompt builders (`prompt.py` for Agents and Code, `chat_mode.py` for Chat); personas with toolsets, model and budget; owner AGENTS.md and project instructions. | Claude Code and Codex keep volatile parts last for prompt caching. | Upgrade | Clock and git status moved last (`120ccc9`). Chat omitting AGENTS.md and three overlapping rule sets remain (§4). |
| 3 | Context management | A window from the server, a calibrated estimate, a reserve for the reply, compaction with thinking off, silent-truncation detection, and long outputs stored for `read_output`. | Matches the frameworks' condensers; ours also detects server-side cuts. | Retain | — |
| 4 | Execution loop | One loop with bounded nudges for empty, cut or unbacked replies. | Simpler than graph runtimes, and enough. | Retain | Typed end reasons: `no_answer`, `budget` |
| 5 | Tasks and goals | Job (in memory), work ledger (`coordination.json`), `set_objective`, steering, the group task board. | LangGraph and OpenAI keep one durable run state. | Upgrade (future) | — (§4) |
| 6 | Execution state | The work ledger records intent → result → unknown; the journal; `state.db`. | The pattern LangGraph and OpenAI document for exactly-once effects. | Retain | The journal is marked cancelled only when the run has stopped |
| 7 | Tool registry and discovery | Native and registry tools, toolsets, deferred tools with `find_tools`, alias resolution. | Claude and OpenAI SDKs show all tools or use search. | Upgrade (future) | — (legacy duplicates, §4) |
| 8 | Tool execution and validation | JSON repair, aliasing, lax pydantic for registry tools, one outcome type, output limits. | MCP and OpenAI: errors as results, timeouts per tool. | Upgrade | MCP errors are failures or uncertain (C1); parallel calls ordered and approved (C2) |
| 9 | Observations | Effects observed: files by hash, exit codes, screen pixel change. Images as observations. | OpenHands: observations as events. | Retain | — |
| 10 | Outcome verification | `VerificationLedger` (checks that ran, special-casing, masked exits, tests edited after failing); final-reply claim checks; the episode V-ladder. | Stricter than any framework surveyed. | Upgrade | Fix verbs in claim checks (`05209a6`); the cause of an empty reply |
| 11 | Errors and recovery | Bounded nudges; context-overflow retry; no retry for transient provider errors. | The SDKs retry transport errors with backoff. | Upgrade | Transient retries (`120ccc9`) |
| 12 | Memory and persistence | Memory records plus vector index; `state.db` chat history with full-text search; journal (2 days); tool outputs (7 days); traces (7 days). | — | Retain; upgrade (future) | — (retention and duplicates, §4) |
| 13 | Security policy and permissions | Deny/ask/allow policy, the Rule of Two after outside content, the never-run list, download-and-run checks, secret files masked. | Claude Code: layered permissions. | Retain | Parallel path now enforces "ask" (C2) |
| 14 | Sandboxing | bubblewrap on Linux (read-only system, workspace writable, credentials hidden, no network unless allowed); Docker elsewhere; host with an approval for every command. | Claude Code and Codex use bwrap; DeepSeek Harness's loopback CVE. | Upgrade | bwrap (`05209a6`) |
| 15 | Credentials and secrets | Credential broker (DPAPI or AES-GCM); `child_env` strips keys; redaction of configured keys. | — | Upgrade | MCP env and header secrets redacted (`120ccc9`); arguments redacted (C5). Connection and OAuth tokens remain (§4). |
| 16 | Human approvals | Approval cards bound to an argument hash, file stat and policy snapshot; 24 h expiry; grants; autonomous mode. | OpenAI: approval ≠ exactly-once, the same caveat. | Retain | — |
| 17 | Orchestration | Delegation (depth 2, six handoffs), fan-out, a read-only swarm (off by default), A2A, code sessions. | Claude SDK: depth and concurrency caps. | Retain; upgrade (future) | — (A2A cancel, swarm budget, §4) |
| 18 | Resource limits | Steps, work budget, admission slots, timeouts, output caps (table in §3). | — | Upgrade | Budget exhaustion is an outcome (`120ccc9`). Approval wait still counts in work seconds (§4). |
| 19 | Logging, tracing, observability | About 40 event types; per-turn traces with optional OTLP; the journal; an audit log of argument hashes. | OpenAI spans; Claude OTel with content off by default. | Retain; upgrade (future) | `model_retry`, `processes_stopped`, `empty_reply.cause` events |
| 20 | Lifecycle, cancellation, termination | Cancel events per turn; Stop everything; approval waits honour cancel. | MCP and LangGraph: cooperative cancel, honest races. | Upgrade | Stop kills foreground commands and the turn's own processes (C3); orphans reaped after a crash (`120ccc9`) |

**Removed:** the dead `GET /stream/{request_id}` route and `agent/stream_events.py`. It read a buffer nothing wrote,
and the apps use `/query/stream`.

**Replaced:** nothing. No external framework replaces the loop. The lessons that transferred were the patterns: one
result contract, approvals on every path, cooperative cancel, and retry only without side effects.

## 3. Limits (all enforced in code)

| Limit | Default |
|---|---|
| Max steps | Agents and Code 60 (1–500); Chat 10 (compact 6); swarm workers 12 |
| Work budget | 64k tokens and 900 s per work (cap 256k and 3,600 s); 6 children. Checked before each model call; exhausting it is now stop reason `budget`. |
| Model request timeout | 600 s |
| Transient retries | 2, after 2 s and 5 s |
| Admission | 4 concurrent calls, 1 local |
| Terminal | 120 s (max 900), then background; 8 background processes |
| MCP timeout | 15 s (1–300) |
| Output caps | 14,000 characters into context (rest via `read_output`); `max_tokens` 8,192 shared by reasoning and answer |
| Context reserve | `min(max_out/2, max(2048, window/8))` |
| Compaction | Summary of 700 tokens, thinking off; stops after 2 failures |
| Approval timeout | 600 s; counted as denied |

## 4. Remaining gaps and recommended next work (ranked)

1. **Budgets.**
   - Approval waits count against work seconds.
   - A child's budget isn't sized from its parent's remainder.
   - The swarm's advertised 120k tokens can't be reached under the lead's 64k.
   - "0 = unlimited" for groups is overridden.
2. **Redaction source.** Build the redaction set from the credential broker: connection and OAuth tokens, A2A keys,
   any new provider. Derive `child_env`'s list from the settings schema instead of a hand-kept list.
3. **Prompt.**
   - Chat doesn't include the owner's AGENTS.md, though `home.py` says every agent follows it.
   - Three overlapping rule sets.
   - No cap on the system prompt against the window (worst case about 62k characters).
   - Per-section sizes are recorded only for Chat.
4. **Legacy tool duplicates.**
   - The registry `terminal_run`, used by personas with every tool;
   - the registry `file_read`;
   - two alias tables;
   - Code's denylist (new tools enter Code automatically).
5. **One durable task model.**
   - Job, the work ledger and the verification ledger overlap.
   - `work.plan` is written and never read back, so resumed group work loses its task board.
   - The coordination ledger rewrites one JSON file under a global lock on every step.
6. **Smaller items.**
   - A2A `tasks/cancel` doesn't stop the run.
   - Delegated teammates share the session's approval grants.
   - A Docker exec that times out keeps running.
   - No retention for `state.db` chat history.
   - Tool output is stored up to four times.
   - Event schema version and trace IDs.

## 5. The engineering loop used

**Inspect → Research → Evaluate → Improve → Test → Verify → Refine**, bounded:

1. **Inspect.** Read-only audits of the code (four today), with every claim cited.
2. **Research.** Primary sources only, cited in the companion reports.
3. **Evaluate.** For each finding, confirm it in code, then decide whether a change is justified: a defect, or a
   measured cost.
4. **Improve.** The smallest change, staged while an evaluation was using the worktree.
5. **Test.** A regression test that fails on the previous commit and passes on the new one; then the related suites
   (110 files).
6. **Verify.** The reference model on all 15 cases × 2, before and after, with the same tooling, judged
   independently; critical findings block acceptance.
7. **Refine.** One round, and only where verification showed a concrete problem (the Chat clock regression was
   caught by its own test and fixed before commit).

The procedure for model and harness evaluation is the repo skill `model-harness-testing`.

## 6. Verification

| Check | Result |
|---|---|
| New regression tests (`test_execution_reliability.py`: C1–C5 plus the upgrades) | 21 pass. Each new one failed on the commit before its fix. |
| Related suites (110 files) | 1,534 passed and 50 skipped. 3 pre-existing failures, the same on `05209a6`: one fails on its own, two depend on test order. |
| Qwen 3.5 9B before (`0a06264`), all 15 × 2 | 8.88 (dev 8.73, held-out 9.04); tasks done right 90%; 0 critical runs. Weakest: D7 4.59 (0%), efficiency 7.18. |
| Qwen 3.5 9B after (`120ccc9`), all 15 × 2 | 9.07 (dev 8.98, held-out 9.17); tasks done right 90%; 0 critical runs |

## 7. Results after the upgrade

Same model, window, cases, repetitions and tooling, with projects outside the repo and learning off. All 30 of 30
runs are present in each. The before run is `20261116-080108-upgrade-baseline-qwen35`; the after run is
`…-081916-upgrade-after-qwen35`.

| | Before (`0a06264`) | After (`120ccc9`) |
|---|---:|---:|
| Weighted score | 8.88 | **9.07** |
| Development / held-out | 8.73 / 9.04 | 8.98 / 9.17 |
| Tasks done right | 90% | 90% |
| Critical runs | 0 | 0 |
| Completion / tools / efficiency / recovery / security | 9.44 / 8.77 / 7.18 / 9.50 / 9.90 | 9.56 / 9.23 / 7.35 / 9.50 / 10.0 |
| Model calls, total model time | 223, 849 s | 189, 787 s |
| D7 (impossible test) | 4.59, 0% | 6.17, 0% |
| H1 (package fix) | 8.13 | 9.38 |

**Reading the results honestly:**
- **No case regressed.** The aggregate rose by 0.19 and crosses the 9.0 target. With 2 repetitions, per-case
  differences under about 1 point are within noise, so the gain isn't attributed to any one change.
- **The upgrades mostly target failures this suite rarely triggers:** transient errors, crashes, Stop, MCP and
  budgets. Their evidence is the regression tests, each failing before its fix. In the after-run, no transient
  retry was needed and no budget stop occurred. Empty replies now carry their cause, and all of them were
  `reasoning_only`, Qwen's known habit, recovered as before.
- **Prompt-cache order: no measurable change here.**
  - Overall reuse was 82% before and 77% after.
  - D6's follow-up turn reused 89–90% in both runs.
  - In the evaluation, follow-ups arrive within the same minute, so the clock didn't change. Each case also has its
    own project path near the top of the prompt.
  - The saving applies to real chats whose turns cross a minute boundary. It's justified by how prompt caches work,
    but unmeasured live.
- **D7 (hard-coding an impossible test's answer) remains the model's weakest case.** The rule-and-skill experiment
  showed advice doesn't fix it. The next candidate is a harness action (§4).


## 8. Identity/TUI follow-up across the 20 areas (16 November 2026)

This follow-up maps the owner's reported defects to the existing 20-area review. It is a scoped source and
regression review, not a new whole-system security certification. Historical scores above remain historical.
Implementation and focused live evidence: [interfaces.md](interfaces.md#16-november-2026-identity-terminal-and-memory-follow-up).

| # | Area | Follow-up disposition and relevant source |
|---|---|---|
| 1 | Providers | Retain: qwen3.5:9b tested through the existing Ollama adapter and recorder; no model-specific branch. |
| 2 | Instructions/configuration | Fixed: unified default, common Echo soul, owner/project instructions in unified Chat (`config.py`, `runtime.py`, both prompt builders). Explicit legacy mode remains. |
| 3 | Context | Retain: compact Chat now receives skill names and bounded descriptions; existing context fitting still governs the full request (`chat_mode.py`, `agent_skills.py`, `window.py`). |
| 4 | Loop | Retain: false-action checks remain; description of stored knowledge is no longer mistaken for an action (`job.py`, `loop.py`). |
| 5 | Tasks/goals | Retain: single-memory work gets evidence-based completion; durable task-board consolidation remains open (`runtime.py`, `job.py`, `coordination.py`). |
| 6 | Execution state | Upgrade: typed memory readback evidence survives into the work record (`runtime.py`). No conversation migration. |
| 7 | Registry/discovery | Fixed gap: approved skills are visible to compact Chat and were actually read live (`agent_skills.py`). Legacy tool duplicates remain open. |
| 8 | Execution/validation | Retain: native memory tool uses existing ToolResult metadata; failed and missing readbacks are not verified (`toolbox.py`, `runtime.py`). |
| 9 | Observations | Upgrade: durable memory readback supplies the receipt, instead of inferring verification from model prose. File/process/screen observations remain unchanged. |
| 10 | Verification | Fixed: read descriptions versus new saves; memory-only completion versus engineering checks. Mixed-action work cannot inherit memory verification (`job.py`, `runtime.py`). |
| 11 | Recovery | Retain: bounded retries and correction nudges; related model-compatibility tests exercised. |
| 12 | Memory/persistence | Fixed: save and duplicate-save readback metadata, receipt deduplication. Store/history retention and duplicate-store architecture remain open. |
| 13 | Security policy | Retain: common personality does not expand tools. Legacy Chat, untrusted projects and named agents have regression coverage (`chat_mode.py`, `environments.py`). |
| 14 | Sandbox | Retain: no sandbox changes. Tests needing local event-loop sockets ran outside the Codex filesystem/network sandbox. |
| 15 | Credentials | Retain: no credential changes or owner secrets in evaluation fixtures. Earlier OAuth/token-redaction findings remain separate work. |
| 16 | Approvals | Retain: approval and stopping status still visible during a running tool. One pre-existing approval-preview test fails identically on the baseline. |
| 17 | Orchestration | Retain: named agents keep their souls/rooms; group-completion regressions passed. A2A cancellation and durable-board gaps remain open. |
| 18 | Resource limits | Retain: served 16,384-token context and partial GPU fit recorded; no paid calls or downloads. No new broad campaign. |
| 19 | Observability | Fixed: one active step line, one memory receipt, truthful identity label; recorder captures model input/output. No event schema redesign. |
| 20 | Lifecycle/termination | Retain: input stays at bottom, Stop remains visible, private-tmux resize test has no duplicate composer history. Restart/rebuild paths are documented. |

The requested identity, input position, duplicate activity and memory-warning fixes are implemented. This review
also explains how soul and skills reach the model. The broader architectural gaps in §4 are not claimed fixed
by this focused change.
