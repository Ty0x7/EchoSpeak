# Multi-agent review: Grok and current group-chat patterns vs EchoSpeak

Date: 2026-10-02. Scope: how EchoSpeak's agents and group chats work today
(`apps/backend/agent/lean/runtime.py`, `rooms.py`, `prompt.py`, `personas.py`,
`apps/web/src/lean/*`) compared with xAI Grok and the main agent frameworks.
Every pattern ends in a verdict: **adopt**, **adapt** or **skip**.

Effort: **S** = under a day, **M** = a few days, **L** = a week or more.

## How EchoSpeak works today (baseline)

| Step | What happens | Where |
| --- | --- | --- |
| Who answers | `@Name` mentions pick up to 4 agents; `@all` / `@everyone` / `@team` picks everyone; otherwise one model call picks at most 2; if that fails, the room's first agent answers | `runtime.py:194` `_route`, `:204` `_model_route`, `rooms.py:145` `mentioned_agents` |
| Order | Chosen agents answer **one after another**. Each later agent sees the user message plus everything said so far, prefixed `[Name]:` | `runtime.py:99` `_run_locked` (prompt at `:156`) |
| Handoff | Any agent can call `delegate_to_agent`. The teammate runs with a fresh context and only the task brief, and its answer comes back as the tool result. Since last round the caller's message is closed first, so the chat reads A → B → A | `runtime.py:376` delegate, `loop.py` `_seal_for_handoff` |
| Limits | Nesting depth 2 (`MAX_DELEGATION_DEPTH`, `runtime.py:26`). No cap on how many handoffs a turn makes, and nothing stops B handing straight back to A | `runtime.py:26`, `:384` |
| Shared context | Group prompt says "Messages from other agents appear as [Name]: text"; delegated agents get an isolated context | `prompt.py:59` `_team` |
| UI | One message per agent turn, live thinking and tool rows, a status pill above the composer, a `delegation` event that the UI ignores (`delegatedBy` exists in `types.ts:43` but is never set) | `liveReducer.ts`, `LeanMessage.tsx`, `LiveStatus.tsx` |

---

## 1a. Grok (xAI)

**Documented (primary sources):**

- **Grok 4 Heavy** uses "parallel test-time compute, which allows Grok to consider
  multiple hypotheses at once"; the launch diagram shows several agents running at
  the same time. The page does **not** say how results are compared or merged.
  [x.ai/news/grok-4](https://x.ai/news/grok-4)
- xAI's launch post on X: Heavy deploys "several independent agents in parallel …
  then cross-evaluating their outputs". [x.com/xai](https://x.com/xai/status/1943786245538427028)
- **Multi-agent API** (`grok-4.20-multi-agent`): 4 agents at low/medium effort,
  16 at high/xhigh. A "designated leader agent is responsible for synthesizing the
  discussion and presenting the final answer". "Only the tool calls and the final
  response from the leader agent are sent back to the user"; sub-agent reasoning
  and tool calls stay hidden (encrypted) unless explicitly requested. Only
  built-in server-side tools (web search, X search, code execution, collections);
  **no client-side tools**; all agents' tokens are billed.
  [docs.x.ai multi-agent](https://docs.x.ai/developers/model-capabilities/text/multi-agent)
- **Grok Build sub-agents**: "independent child sessions with their own context"
  that "return a summary to the parent"; built-in types `general-purpose`,
  `explore` (read/search only) and `plan`; personas are a separate overlay.
  [docs.x.ai subagents](https://docs.x.ai/build/features/subagents)
- Tool use is a server-side loop: the model "decides what to do next: make a tool
  call, or provide a final answer". [docs.x.ai tools](https://docs.x.ai/developers/tools/overview)

**Not documented / speculation (do not rely on):** named default agents
("Grok, Harper, Benjamin, Lucas"), "debate rounds", "peer review drops
unsupported claims" and quoted hallucination rates come from third-party blogs,
not xAI docs.

**What Grok shows the user:** one answer from the leader, with the leader's tool
calls. Sub-agents are invisible by default. EchoSpeak already does the opposite
(every agent is a visible chat member), which is the point of a group chat, so
we should keep our transparency and borrow only the *fan-out + leader synthesis*
mechanic.

| Grok pattern | Verdict | Reason |
| --- | --- | --- |
| Parallel independent agents on one question | **Adapt** | Useful for `@all` questions; must stay one model in VRAM on an 8 GB card (see U4) |
| Leader synthesizes a final answer | **Adapt** | Add a short merge message after a fan-out, written by the room's lead agent |
| Hide sub-agent activity | **Skip** | Visible agents are EchoSpeak's product; hiding them would remove the group chat |
| Sub-agents return a summary, isolated context | **Adopt (already)** | `delegate_to_agent` already runs the teammate with `history=[]` (`runtime.py:384`) |
| 4 vs 16 agents by effort | **Skip** | Local models; more agents = slower answers with no quality budget to pay for it |

## 1b. Patterns across frameworks

### Turn-taking and speaker selection

- **AutoGen `SelectorGroupChat`**: a model picks the next speaker from
  `{participants}`, `{roles}` and `{history}`; `selector_func` / `candidate_func`
  can override it; `allow_repeated_speaker` controls back-to-back turns. Also
  RoundRobin and Swarm.
  [AutoGen selector](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/selector-group-chat.html)
- **LangChain/LangGraph router**: "a routing step classifies input and directs it
  to specialized agents. Results are synthesized."
  [LangChain multi-agent](https://docs.langchain.com/oss/python/langchain/multi-agent)
- **EchoSpeak:** rule first (mentions), model second, room order last. The router
  sees the last 4 messages, truncated to 300 chars each, but not **who spoke
  last**, so a short follow-up ("and tomorrow?") can be routed to a different
  agent than the one the user was talking to. On failure it falls back to the
  first member, not the last speaker.

| Pattern | Verdict | Reason |
| --- | --- | --- |
| Rule-based first (mentions) | **Adopt (already)** | `rooms.py:145` |
| LLM selector with history | **Adapt** | Tell the router who spoke last and prefer them for follow-ups; fall back to the last speaker (U2) |
| Round-robin | **Skip** | Every agent answering every message is noise in a chat |

### Topology: supervisor/router vs peer-to-peer

- **OpenAI Agents SDK**: *agents as tools* when "one agent should own the final
  answer"; *handoffs* when "the chosen specialist [should] own the remainder of
  the current turn". Code-driven patterns: chaining, evaluator loops, parallel
  `asyncio.gather`. [OpenAI multi-agent](https://openai.github.io/openai-agents-python/multi_agent/),
  [handoffs](https://openai.github.io/openai-agents-python/handoffs/)
- **Claude Agent SDK**: subagents with their own prompt, tool allow-list and
  model; only the final message returns; nesting, concurrency and spend are
  capped (defaults: depth 3, 20 concurrent).
  [Claude subagents](https://code.claude.com/docs/en/agent-sdk/subagents)
- **CrewAI**: sequential or hierarchical (a manager assigns and checks work).
  [CrewAI processes](https://docs.crewai.com/en/concepts/processes)
- **EchoSpeak:** peer-to-peer. Any agent can delegate to any other, up to depth 2,
  with no turn-level budget and no rule against handing straight back.

| Pattern | Verdict | Reason |
| --- | --- | --- |
| Agents as tools (delegate returns a result) | **Adopt (already)** | Matches `delegate_to_agent` |
| Cap depth, concurrency, budget | **Adapt** | Add a per-message handoff budget and block A→B→A ping-pong (U1) |
| Hierarchical manager that checks every answer | **Skip** | Doubles model calls on a local GPU for little gain in chat |

### Parallel fan-out and merge / vote

- **Grok Heavy / multi-agent API:** parallel agents, leader synthesizes (above).
- **Anthropic research system:** orchestrator-worker, workers in isolated
  contexts; parallelism cut research time up to 90% at ~15× tokens.
  [Anthropic multi-agent research](https://www.anthropic.com/engineering/multi-agent-research-system)
- **Mixture-of-Agents:** several proposers answer independently, an aggregator
  combines them. [arXiv 2406.04692](https://arxiv.org/abs/2406.04692)
- **EchoSpeak:** none. `@all` runs agents one after another, each told not to
  repeat the others.

| Pattern | Verdict | Reason |
| --- | --- | --- |
| Independent parallel answers for `@all` / several mentions | **Adapt** | Only when every chosen agent uses the same model endpoint, so a local server never loads a second model; otherwise stay sequential (U4) |
| Aggregator / leader merge | **Adapt** | One short "where we agree / differ" message from the room's lead agent after a fan-out (U4) |
| Voting | **Skip** | Chat answers are prose, not labels; a merge message is more useful |

### Shared vs isolated context, scratchpads, handoff payloads

- OpenAI handoffs pass full history unless an `input_filter` trims it; Claude
  subagents get only the prompt string; LangChain notes subagents/routers isolate
  context while handoffs accumulate it (links above).
- **EchoSpeak:** group responders share the transcript; delegated agents get only
  the brief. That is the recommended split. Missing: the brief does not say who
  is in the room or what the user originally asked.

| Pattern | Verdict | Reason |
| --- | --- | --- |
| Isolated context for delegated work | **Adopt (already)** | `runtime.py:384` |
| Include the user's original request in the handoff brief | **Adopt** | One line; stops the teammate answering a narrower question than the user asked (part of U1) |
| Shared scratchpad file | **Skip** | Chat transcript already is the shared state; adds a storage concept for little gain |

### Stopping conditions and runaway prevention

- AutoGen: 11 composable termination conditions (`MaxMessage`, `TextMention`,
  `TokenUsage`, `Timeout`, `Handoff`, `External`…), combined with `|` / `&`.
  [AutoGen termination](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/termination.html)
- Claude: depth / concurrency / spend caps; partial result marked when a cap hits.
- **EchoSpeak:** one round per user message (implicit termination), per-agent step
  cap (`lean_max_iterations`), delegation depth 2, Stop button. No handoff count cap.

| Pattern | Verdict | Reason |
| --- | --- | --- |
| Handoff budget per user message | **Adopt** | Cheap guard against ping-pong (U1) |
| Multi-round "discuss until done" mode with termination conditions | **Skip for now → proposal** | Real value, but it is a new room mode with UI and settings (L); written up as P1 below |

### Disagreement, critique, convergence

- Multi-agent debate improves factuality by having agents read and revise each
  other's answers. [Du et al., arXiv 2305.14325](https://arxiv.org/abs/2305.14325)
- OpenAI evaluator loops (link above).
- **EchoSpeak:** later responders are told "don't repeat what the others already
  said" — which discourages repetition but also discourages disagreement.

| Pattern | Verdict | Reason |
| --- | --- | --- |
| Second speaker explicitly asked to agree briefly, correct, or add | **Adopt** | Prompt-only change at `runtime.py:156` (U3) |
| Merge message that states agreement and disagreement | **Adopt** | Part of U4 |
| Full debate rounds | **Skip** | See P1 |

### UI for multi-agent activity

- Claude Agent SDK marks subagent messages with `parent_tool_use_id`; LangGraph
  namespaces subgraph events (`event-streaming` docs) so a UI can nest them.
- Grok shows only the leader.
- **EchoSpeak:** every agent message is separate (good), but nothing on the
  teammate's message says it was handed off, and the status pill does not show
  the handoff. The `delegation` event is dropped by the reducer.

| Pattern | Verdict | Reason |
| --- | --- | --- |
| Tag delegated messages with who handed off ("via Echo") | **Adopt** | Event already emitted; set `delegatedBy` and render it (U5) |
| Show handoff in the live pill ("Echo → Scout") | **Adopt** | Small change in `LiveStatus.tsx` (U5) |
| Show parallel agents together while a fan-out runs | **Adopt** | Pill lists every agent still working (U5) |
| Collapsed side threads for nested work | **Skip** | Nesting depth is 2; flat ordered messages read better in a chat |

---

## Ranked upgrades

| # | Upgrade | Effort | Impact | Why this rank |
| --- | --- | --- | --- | --- |
| U1 | **Handoff hygiene:** per-message handoff budget (6), refuse handing straight back to the agent that delegated to you, include the user's original request in the brief | S | High | Reliability first: removes the only runaway path left |
| U2 | **Sticky routing:** router told who spoke last and to keep them for follow-ups; failure falls back to the last speaker, not the first member | S | High | Fixes the most common "wrong agent answered" case |
| U3 | **Converge, don't just avoid repeats:** later responders agree briefly, correct, or add | S | Medium | Prompt-only |
| U4 | **Parallel fan-out + merge** for `@all` / several mentions: same-endpoint agents answer independently at the same time, then the room's lead writes a short merge. Different models → sequential, as today | M | High | The Grok mechanic, adapted to one GPU |
| U5 | **Multi-agent UI:** "via Echo" on delegated messages (live and reloaded), pill shows the handoff and all agents working in parallel | S | Medium | Makes U1/U4 legible |
| P1 | Proposal: "discussion" room mode (several rounds, AutoGen-style termination: max messages, `DONE` mention, Stop) | L | Medium | New mode; needs settings + UI; not this round |
| P2 | Proposal: per-agent model concurrency check against LM Studio's loaded models before parallel runs | M | Low | Only matters once personas use different local models |

Phase 3 implements U1–U5.
