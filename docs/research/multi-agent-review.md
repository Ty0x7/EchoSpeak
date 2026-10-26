# EchoSpeak multi-agent architecture and harness review

Research snapshot: **2026-10-26**. Source baseline: `39ec47f`, branch
`codex/model-compatibility-audit`. Scope: native Echo Chat and the shared Lean
harness. [Visual architecture report](../../artifacts/research/multi-agent-review.html).

**Status:** research and proposed design. Application code, UI, settings,
credentials, schedules and external coding integrations were not changed. No live
model testing, outbound messaging, installation, build, publication or deployment
was performed. Source inspection establishes implemented paths, not their measured
reliability on the user's installed app. Earlier reviews below are historical.

## 1. Recommendation and terminology

Keep the existing modular runtime. Add a small, explicit **work coordination
contract** connecting its current identities, runs, routines, evidence and
delivery. Avoid a second agent framework, scheduler or chat system. Echo remains
the usual conversational entry point and can do work itself. Specialists are
useful when continuity, distinct capabilities, context isolation or an assigned
responsibility justify them. Temporary workers remain bounded execution helpers.

The starting principle holds with one refinement: expertise is an agent's
configured purpose and demonstrated track record, not a guarantee supplied by its
name. Work records hold the authoritative assignment and progress; the named
agent carries out that responsibility. Coordination is deterministic application
logic where possible, with models proposing plans or interpreting ambiguous work.

| Concept | Product meaning | Lifetime / authority |
| --- | --- | --- |
| EchoSpeak | Workspace, UI, identity mapping, policy and communication | Application lifetime |
| Model | Inference engine selected for a run | A model call is neither an identity nor a task |
| Native harness | Context assembly, model/tool loop, policy checks, observations and stopping | One bounded execution, invoked repeatedly as needed |
| Persistent agent | ID, instructions, capability profile, relevant memory/lessons and role | Across conversations; does not require a resident inference process |
| Conversation/session | A user-facing interaction and its history/model binding | May expose several work items or several agents |
| Goal/work item | Outcome, owner, dependencies, authority, state, evidence and next action | May span chats, attempts and restarts |
| Responsibility | An approved continuing obligation with wake and reporting rules | Produces bounded work occurrences until paused or retired |
| Attempt | One execution of a work item through the harness | Reuses existing Turn/Execution records as evidence |
| Worker/subagent | Temporary context and capability slice for a bounded task | Ends or is cancelled when its assignment ends |
| Channel | An authenticated ingress or selected delivery surface | Does not create a new agent or confer owner authority |

### Three tiers: useful presentation, flexible technical roles

Echo is the default coordinator, persistent specialists are peers for work they
own, and workers are children of bounded assignments. This is **not** an access
hierarchy where Echo grants itself everything or a specialist must route every
message through Echo. A user may talk directly to Jarvis. Jarvis may own a recurring
research responsibility and launch a temporary helper for one occurrence. The
same persistent agent can assist Echo synchronously without transferring the
overall goal. Authority follows the authenticated user and work grant, never a
tier number or a model's decision to delegate.

Do not turn every tool or workflow into a new persistent character. Echo can use
a skill and tools directly. Propose a new specialist when there is a recurring
domain, distinct working context or a continuing job worth separating, and make
activation an explicit owner decision.

## 2. Current architecture: active paths and boundaries

### 2.1 Request and context path

Desktop Chat uses Lean API routes. Direct specialist chats and group rooms are
backed by ordinary thread/session records. The UI creates a direct room when an
agent is selected. Channel calls through `EchoAgent.process_query` resolve the
caller role and use the same Lean execution path. `run_lean_query` creates a
`LeanSession`; the session selects persona/model, loads relevant context, runs
tools, saves timeline/results and invokes learning. These are shared execution
facilities, rather than one independent engine per sidebar entry.

Source anchors: [core.py / process_query](../../apps/backend/agent/core.py),
[Lean API](../../apps/backend/api/routes/lean.py),
[runtime.py / run_lean_query, LeanSession, _build_turn](../../apps/backend/agent/lean/runtime.py),
[rooms.py / RoomStore](../../apps/backend/agent/lean/rooms.py),
[UI index.tsx / direct room creation](../../apps/web/src/index.tsx).

### 2.2 Subsystem map

| Subsystem | Current source-confirmed behavior | Boundary / implication |
| --- | --- | --- |
| Agent identities | `AgentPersona` stores ID, name, instructions/soul, model override and toolsets. Echo, Jarvis (`scout`) and Glados (`forge`) seed the roster; CRUD exists for custom agents. | Saved identities exist. The persona schema does not itself own a continuing-work lifecycle. [Personas](../../apps/backend/agent/lean/personas.py), [API](../../apps/backend/api/routes/lean.py) |
| Model and tool selection | `_build_turn` builds the selected persona's toolbox and provider context. Capabilities describe actual tool availability and disabled settings. Local builder focus and deferred tool discovery reduce schema clutter. | Preserve the provider adaptations from the model compatibility audit; they are separate from who owns work. [Runtime](../../apps/backend/agent/lean/runtime.py), [provider](../../apps/backend/agent/lean/provider.py), [toolbox](../../apps/backend/agent/lean/toolbox.py), [capabilities](../../apps/backend/agent/lean/capabilities.py) |
| Conversations and groups | Direct rooms share ordinary session persistence. `_route` selects the default/current persona for ordinary Chat; group mentions or a model router select group speakers. Reply and Work Together modes have different execution behavior. | A group discussion is not automatically a durable project plan. Ordinary Chat can delegate without first running the group speaker router. [Rooms](../../apps/backend/agent/lean/rooms.py), [runtime](../../apps/backend/agent/lean/runtime.py) |
| Specialist help | `delegate_to_agent` gives the target a fresh brief, optional original-user excerpt and a result return path. Depth is capped at 2, handoffs per request at 6, and immediate hand-back is excluded. Children share the request's taint/cancellation and execution setting. | This is synchronous task help with a persistent identity, not a durable asynchronous mailbox or transfer of a standing responsibility. [Runtime / delegation](../../apps/backend/agent/lean/runtime.py) |
| Temporary research workers | Optional swarm workers receive an explicit read-only subset of the lead toolbox, fresh histories, bounded tokens/time/concurrency and shared cancellation. Research suitability gates whether the tool is offered. | This is stronger capability attenuation than merely rebuilding a target persona toolbox. Swarm availability is off by default in config; no saved user setting was read. [Swarm](../../apps/backend/agent/lean/swarm.py), [config](../../apps/backend/config.py) |
| Context and history | Recent turns remain visible; older turns are summarized. `_recall` projects memory for the current session/project. Project context and evidence join the prompt. | A summary is a derived view, not authoritative work state. A short delegated brief can omit assumptions or evidence unless handoff contracts specify them. [Summaries](../../apps/backend/agent/lean/summaries.py), [recall](../../apps/backend/agent/lean/recall.py), [project context](../../apps/backend/agent/project_context.py), [runtime](../../apps/backend/agent/lean/runtime.py) |
| Persistent facts | Memory records support account, project, session and temporary scopes with provenance and active/deleted handling. Runtime projection is scoped; owner `chat_search` can search across conversations. | There is useful scoped recall, but no universal per-specialist private knowledge boundary. Owner-global search and broader fallback retrieval must not silently become worker access. [Memory](../../apps/backend/agent/memory.py), [runtime / _recall, memory_search, chat_search](../../apps/backend/agent/lean/runtime.py) |
| Experience and learning | Per-agent episodes, evidence grades, tool reliability, reflection, lesson selection and outcome-based promotion/retirement exist. Lessons have a control/holdout path; feedback can correct outcomes. | Advisory adaptation already exists; it is not model-weight training. Grades and inferred checks are confidence signals, not proof every task succeeded. [Learning](../../apps/backend/agent/learning/__init__.py), [episodes](../../apps/backend/agent/learning/episodes.py), [playbook](../../apps/backend/agent/learning/playbook.py), [profiles](../../apps/backend/agent/learning/profiles.py) |
| Skills and MCP | Package skills expose executable capabilities; approved Markdown agent skills supply reusable workflow instructions. Agent skill proposals are inactive drafts until reviewed; content hashes invalidate approval after changes. MCP pins launch config and offered tools, including annotations. | These are complementary layers. Do not create another skills registry. A skill may guide use of tools but cannot supply authority. New tool code needs separate execution review. [Agent skills](../../apps/backend/agent/lean/agent_skills.py), [package registry](../../apps/backend/agent/skills_registry.py), [MCP trust](../../apps/backend/agent/mcp_trust.py), [MCP client](../../apps/backend/agent/mcp_client.py) |
| Task board and evidence | The current `Job` tracks subtasks, actions, limits and completion within a request. Tool results carry outcomes/effects. Completion combines deterministic checks with model judgment in applicable group paths. | Do not label a model verdict or a completed chat turn as independently verified goal completion. [Job](../../apps/backend/agent/lean/job.py), [evidence](../../apps/backend/agent/lean/evidence.py), [runtime / _judge](../../apps/backend/agent/lean/runtime.py) |
| Routines and reminders | Persisted routines track schedule, next/last run and result metadata. Task routines select an agent from metadata and run the native harness. Plain reminders post without model inference. | Keep the scheduler and model-free path. An occurrence needs a stable identity and work linkage for stronger recovery/deduplication. [Routines](../../apps/backend/agent/routines.py), [reminders](../../apps/backend/agent/reminders.py), [automation runner](../../apps/backend/agent/lean/automations.py) |
| Heartbeat/background | Heartbeat is off by default and requires explicit project/session configuration. Its tick invokes a query before deciding to be silent. Reflection, summaries and memory maintenance also have background paths. | Silence can still cost inference. Budget all background producers together; do not assume heartbeat is the only consumer. [Heartbeat](../../apps/backend/agent/heartbeat.py), [runtime reconciliation](../../apps/backend/api/deps.py), [learning](../../apps/backend/agent/learning/__init__.py) |
| Durable state | StateStore persists execution, tool, approval, item and thread records. Canonical JSON and a SQLite read/search projection coexist; corruption handling preserves authority files. Interrupted timeline state can be surfaced. | Historical TaskRun fields and persisted approvals are not evidence that the active Lean path automatically resumes arbitrary work. Do not turn a rebuildable projection into a second authority accidentally. [StateStore](../../apps/backend/agent/state.py), [Lean approvals](../../apps/backend/agent/lean/approvals.py) |
| Policy, privacy, sandbox | Persona/toolset filtering, owner switches, deterministic policy, taint, credential redaction, approvals, network privacy gates and terminal execution choices exist. Guests receive reduced tools/context. | These are application controls. They do not establish Muse-style host isolation for every local tool/process. [Policy](../../apps/backend/agent/lean/policy.py), [toolbox](../../apps/backend/agent/lean/toolbox.py), [privacy](../../apps/backend/agent/privacy.py), [terminal](../../apps/backend/agent/lean/terminal.py) |
| Projects and artifacts | The native prompt and tools already read project context and create artifacts/workspace outputs. | Reference these existing objects as evidence; do not duplicate them into a new work-document store. [Project context](../../apps/backend/agent/project_context.py), [workspace tools](../../apps/backend/agent/project_workspace.py) |

### 2.3 Communication paths are uneven today

- **Discord:** inbound messages have access/role resolution and stable channel/user
  thread identifiers, then enter the core/native path. Owner, trusted and public
  distinctions matter; a DM alone is not an owner credential.
  [Discord bot](../../apps/backend/discord_bot.py),
  [core adapter entry](../../apps/backend/agent/core.py).
- **Telegram:** access checking precedes processing. The current handler uses
  `telegram_<username>` as thread identity and marks allow-listed users as owner;
  an open bot caller is public. That is a different trust mapping from Discord,
  and username-derived continuity should be reviewed before shared cross-channel
  work. [Telegram handler](../../apps/backend/telegram_bot.py).
- **WhatsApp:** the repository has an optional skill using an external
  `whatsapp-web.js` bridge for sending/reading/listing chats. This is not evidence
  of a completed native inbound agent/session adapter or a Meta production
  integration. [WhatsApp tool implementation](../../apps/backend/skills/whatsapp/tools.py).
- **Notifications/delivery:** automation results can reuse chat text and route to
  configured desktop, Discord-owner or Telegram destinations. The desktop feed
  has in-process claim/read semantics; it is not a durable delivery outbox.
  [Automation delivery](../../apps/backend/agent/lean/automations.py),
  [notification feed](../../apps/backend/agent/notifications.py).

No messages were sent and no connection status, credentials or saved accounts
were inspected. Source capabilities do not establish working live integrations.

## 3. External research through 26 October 2026

Evidence labels here mean **documented** (vendor description or official source),
**research result** (published study, limited to its setup), and **recommendation**
(our synthesis). No external product was installed or independently benchmarked.

### Muse: continuity with an independent enforcement boundary

Meta's launch describes continuing goals, proactive suggestions, cross-app work
and user-controlled memory. Muse is the agent product; Muse Spark is its model.
[Launch, 8 September 2026](https://about.fb.com/news/2026/09/introducing-muse-personal-ai-agent/).

Its technical account describes a runtime container separated from credential
storage, connector workers, durable state and a host-side Sentinel. Actions and
network egress pass through independent authority; approved requests receive
credentials at the boundary. It also describes concurrent subagents, custom
tools and self-editing, plus a separately brokered browser worker. These are
documented mechanisms, not proof of attack immunity. The announced Confidential
VM is distinct from the launch security design and was described as forthcoming.
[Technical account, 8 September 2026](https://research.meta.ai/blog/security-and-safety-for-ai-agents-our-approach-with-muse).

**Adapt:** separate agent proposals from permission enforcement; scope credentials
and tool execution. **Do not infer:** copying a Python policy function reproduces
kernel/network isolation, or self-editing is necessary for learning. Echo should
first strengthen existing policy/skill-review seams; a Windows security broker or
remote isolated host is a separate engineering project.

### Grok Bot: identities, skills and explicit work ownership

xAI documents named persistent bots on a shared account-level cloud computer.
Their histories/roles persist while files and browser logins are shared resources.
That is not per-bot isolation.
[Overview, updated 22 October 2026](https://docs.x.ai/grok-bot/overview).

Its collaboration documentation describes asynchronous messages that wake another
bot, recommends one owner at each stage and gives foreground user messages
priority. **Adapt:** explicit sender, recipient, task and return route, with
duplicate suppression; do not copy every chat to every specialist.
[Collaboration](https://docs.x.ai/grok-bot/chat-and-collaboration).

Skills describe procedures, while routines specify when an owning bot runs them.
Narrow triggers, visible history and pause controls make ongoing work inspectable.
**Adapt:** reuse Echo's routine scheduler and approved skills with an explicit
responsibility record. Cloud execution while the laptop is off requires actual
hosting; it does not follow from a persistent identity.
[Skills and routines](https://docs.x.ai/grok-bot/skills-routines-and-automations).

### OpenAI: distinguish helper calls, handoffs and state

Official orchestration guidance separates specialist-as-tool calls, where a
manager retains the response, from handoffs, where the specialist takes over a
branch. **Adapt:** retain Echo for synthesis by default and make responsibility
transfer explicit when intended. Echo's current synchronous delegation more
closely resembles bounded helper execution, despite its handoff label.
[Orchestration and handoffs](https://developers.openai.com/api/docs/guides/agents/orchestration).

Run guidance distinguishes history replay, SDK sessions and hosted conversation
state. **Adapt:** a stored transcript, an agent identity and a recoverable work
checkpoint are separate contracts. No OpenAI runtime migration is recommended.
[Running agents](https://developers.openai.com/api/docs/guides/agents/running-agents).

### Anthropic: reconcile useful workers with fewer unnecessary agents

The research-system write-up supports bounded parallel research with clear
briefs and output contracts, and warns about token overhead and dependent work.
Its results concern its own research setup and models, not Echo's 4B deployment.
[Multi-agent research system, June 2025](https://www.anthropic.com/research/multi-agent-research-system).

The newer **22 October 2026** guidance recommends consolidating unnecessary
routers/planners/domain subagents into one loop using skills and deferred tools;
workers still help when context isolation, parallelism or permission separation
justify them. **Adapt:** keep user-valued specialists while avoiding an inference
hop for every domain. The advice was developed for capable Claude deployments;
tool selection and context size still need local-model evaluation.
[When to fold subagents into the main loop](https://claude.com/resources/articles/building-agents-when-to-fold-subagents-into-your-main-agent-loop).

Long-running harness work uses incremental outcomes and explicit artifacts to
bridge context windows. **Adapt:** restart from checked work state and inspect
the actual environment rather than trusting the last summary.
[Long-running harnesses](https://www.anthropic.com/research/effective-harnesses-for-long-running-agents).

### Open-source durability patterns and published research

| Reference | Evidence and applicable lesson | Limitation |
| --- | --- | --- |
| [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) and [checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers) | Documented separation of thread checkpoints and cross-thread stores. Replay can rerun downstream calls; persistence modes trade latency against crash safety. | A checkpoint does not automatically deduplicate an external side effect. Reuse these concepts without replacing Lean. |
| [Temporal activity definitions](https://docs.temporal.io/activity-definition) | Official guidance makes retries and service-enforced idempotency explicit; a completed effect whose acknowledgement is lost may execute again. | Do not promise exactly-once messaging from a local outbox alone. Operating Temporal would add infrastructure Echo does not currently need. |
| [Why Do Multi-Agent LLM Systems Fail? v3, October 2025](https://arxiv.org/abs/2503.13657v3) | Research identifies design, inter-agent alignment and verification failures. Use clear briefs, bounded protocols and observable completion criteria. | Taxonomy and sampled traces are not a measured failure rate for Echo. |
| [Towards a Science of Scaling Agent Systems v3, April 2026](https://arxiv.org/abs/2512.08296v3) | Controlled evaluations show architecture/task alignment matters; dependent planning can suffer from coordination overhead. | Benchmark outcomes and model capability do not establish the best concurrency for this machine. |
| [Small Language Models are the Future of Agentic AI v3, September 2026](https://arxiv.org/abs/2506.02153v3) | Position paper argues specialized, repeatable invocations can suit small models and heterogeneous routing. | It is an argument, not certification that a particular 4B model reliably handles general autonomy. |

## 4. Architectural gaps and priority

| ID | Finding and evidence | Proposed response |
| --- | --- | --- |
| G1 | `Job` is request-local; routines, execution records and chat are separate owners of different fragments of work. | P0: define work identity, attempt linkage and transitions before more autonomy. |
| G2 | Named delegation rebuilds the target's configured toolbox; research workers explicitly intersect the lead toolbox. No general work-grant intersection is passed through the named-delegation path inspected. | P0: decide and enforce child/transfer authority. This is a design gap, not a demonstrated bypass of global owner switches. |
| G3 | Lean pending approvals/session grants live in its in-memory broker. Noninteractive approval-gated actions return not-run/blocked guidance. Other durable approval record types do not prove active Lean continuation. | P0: durable waiting state plus exact-action approval/resume reconciliation; never infer approval from a group reply. |
| G4 | Facts can be scoped, lessons are agent-specific, but owner chat search spans sessions. Persona ID is not a universal memory ACL. | P1: one context-access contract for automatic recall, explicit search, delegation and channel results. |
| G5 | `run_automation` returns execution outcome separately from `delivered`; `success` does not require successful delivery. Notifications are in memory. | P1: keep work, verification and delivery outcomes separate; add a durable outbox where guarantees are needed. |
| G6 | Reminder creation stores `agent_id`, while `post_to_chat` hardcodes Echo authorship. | P1: preserve agent identity and work provenance through rendering/delivery. |
| G7 | Telegram and Discord use different trust/thread mappings; WhatsApp is a bridge skill, not a demonstrated unified inbound surface. | P1: canonical principal/channel binding and adapter contract before cross-channel continuity. |
| G8 | Persona CRUD and skill proposals exist, but no model-facing specialist proposal/approval lifecycle was established by the inspected path. | P2: reviewable draft specialist plus task/capability preview; reuse existing CRUD after approval. |
| G9 | Heartbeat can infer on quiet ticks; summaries and learning also consume background resources. Existing worker caps are local to their subsystem. | P1: shared model admission/budget policy and foreground preference, introduced around current entry points. |
| G10 | Learning has verified-outcome heuristics, holdouts and review, but source grades are not semantic correctness guarantees. | P2: task-kind acceptance, outcome correction and evaluation before promoting new workflows. |

## 5. Proposed work coordination layer

### 5.1 Keep one owner for each kind of state

Use `StateStore` as the product-state facade. Add versioned work/occurrence and
delivery records through it; reference existing personas, sessions, routines,
executions, artifacts and learning episodes. Do not add another conversation
database or recreate the retired coordinator wholesale.

Persistence needs an explicit design decision: current JSON authority and SQLite
projection cannot jointly provide a new multi-record transaction by implication.
For the first single-backend version, use the established authoritative storage
with a versioned occurrence journal and reconciliation under its process/write
locks. Claim this as **recoverable at-least-once processing**, not distributed
exactly-once execution. If cross-process claiming or transactional outbox writes
become necessary, select a single transactional authority for the new record
types, with migration/rebuild rules, before enabling those modes. Do not store
critical work only in a rebuildable search index.

### 5.2 Minimal proposed records

| Record | Minimum useful content |
| --- | --- |
| Agent definition (extend existing) | Existing persona ID; role/instruction revision; approved capability profile; context policy; active/paused/archived state |
| Work item | ID; requesting principal; owning agent; objective and acceptance criteria; optional project; dependencies; status/revision; work grant; budgets; evidence refs; next action |
| Attempt (link existing Execution) | Work ID; occurrence ID; actor; execution ID; model/config/context revisions; start/stop reason; observed results; parent attempt |
| Responsibility (link existing Routine) | Owner agent; objective; approved sources/skill version; schedule or event filter; timezone/missed-run rule; budget; reporting destination/quiet rule; pause/end condition |
| Context manifest | Session/project/agent/work refs; allowed memory scopes; provenance/trust labels; selected evidence; bounded summary; retrieval revision |
| Approval request | Exact operation/arguments hash; principal; destination/resource; work/attempt; expiry and preconditions; decision; revalidation result |
| Delivery record | Work event and recipient identity; destination; originating agent; payload/artifact refs; dedupe key; queued/accepted/confirmed/failed/unknown status |

Do not create a work plan graph for “hello” or a simple answer. Materialize work
when the request needs delegation, multiple meaningful steps, a wait, a lasting
obligation or durable tracking. Dependencies can start as a small validated list;
reject cycles and keep one owner at a time. A schedule is a trigger, a skill is
procedure, a work item is progress, an execution is an attempt.

### 5.3 Lifecycle and recovery

```text
proposed --owner accepts--> ready --due + grant + budget--> running
running --> verifying --> completed
running/verifying --> waiting_input | waiting_approval | waiting_dependency
running --> retry_wait --> ready              [bounded, safe to retry]
running --> interrupted --> reconcile --> ready | blocked | failed
any nonterminal --> paused | cancelled
waiting_* --> ready                          [condition met, authority rechecked]
```

A continuing responsibility remains active and emits occurrences; a completed
occurrence does not complete the standing responsibility. Store occurrence IDs
from responsibility ID + scheduled UTC occurrence/event identity. Keep schedule
timezone/DST and missed-run coalescing explicit. Avoid replaying every missed
daily job after several days asleep unless the owner chose catch-up behavior.

Before an effect: persist the proposed operation identity and relevant
preconditions. After it: persist observed outcome and external receipt if one
exists. On restart, inspect unfinished records and the actual source system.
Retry pure reads freely within budgets. Retry writes only with verified
idempotency or a safe reconciliation rule. If a send may already have happened,
record **unknown outcome** and reconcile or ask; do not send again merely because
the final acknowledgement is missing. Approval waiting is a persisted state,
not a Python thread sleeping indefinitely.

Cancellation stops new actions and propagates to children; it does not undo
completed effects. Pause prevents future wakes and needs a clear policy for an
active attempt. Do not mark either as successful completion. A resource claim
must guard shared desktop input and writable project resources as well as model
concurrency; Git worktrees alone do not isolate mouse input or account logins.

## 6. Delegation, context and memory contracts

### 6.1 Two operations with different ownership

**Request help:** Echo retains the work item; a child receives one bounded
assignment and returns evidence/results. **Transfer work:** an authorized
coordinator changes the owner with a revision check and recipient acknowledgement.
The sender remains responsible until acceptance. A natural-language promise in a
chat does not transfer authority or progress.

Both carry objective, constraints, acceptance criteria, relevant context/evidence
references, allowed capabilities/data destinations, budgets, cancellation and
return destination. Persist child status so a lost reply does not cause duplicate
assignments. Gate dependency completion on accepted evidence, not an agent merely
saying “done.” No automatic broadcast to every teammate.

### 6.2 Bounded context across conversations

One agent may participate in several sessions. Each run assembles a manifest
from current user input, that session's summary/recent messages, authorized
project/work state, relevant shared facts and selected agent lessons. A new chat
does not load every previous conversation. A work item can link to several chats
without merging their entire histories.

Use **access filtering before relevance scoring**, including explicit search and
fallback paths. Account facts are shared only under the user's policy; project
facts stay within permitted projects; agent notes express role-specific working
knowledge; task evidence remains attached to its work. Mark uncertain/stale facts,
source/updated time, and corrections. Do not silently upgrade a model-generated
summary into a verified fact or instruction. A worker receives an expiring context
slice, not the parent's memory handle or unrestricted cross-chat search.

Shared group context should use one underlying event/evidence reference with
per-agent read cursors and a common compact brief. Full context injection into
every agent on every turn remains costly; cursors reduce duplication, while each
bounded run may still need its own selected context. Keep model caching an
optimization, not a permission or correctness mechanism.

### 6.3 Learning and adaptation

Reuse episodes, reliability statistics, lesson review/holdouts and skill drafts.
Connect work outcome and acceptance evidence back to the exact attempt, model,
skill version and specialist. A useful workflow may be proposed as a skill;
activation retains the current review and content-pinning behavior. Proposed new
specialists and expanded responsibilities need their own reviewable configuration.

Application code controls identity creation, tool grants, privacy, protected
instructions, verifier logic and active schedules. Agents may propose changes
within their authority; learned advice cannot expand it. Tool creation means
generating a draft package plus tests and declared effects for review, not loading
arbitrary code into the trusted backend. Use rollbackable versions for adopted
skills/lessons and preserve owner correction. None of this requires changing
model weights or allowing the agent to edit its security layer.

## 7. Security and authority

Proposed effective authority for a run is the intersection of owner policy,
authenticated principal, approved agent capabilities, work-specific grant,
current privacy mode and resource constraints. Workers inherit a subset of that
authority. A transfer to a stronger specialist needs an explicit eligible
work grant validated by trusted application code; choosing that specialist in a
prompt does not broaden the original caller's rights.

Preserve current taint/cancellation across helpers, and persist relevant trust
labels across deferred continuations and saved evidence. External pages, mail,
MCP outputs and other agents' claims remain data. Recheck privacy and grants at
execution and delivery, not only when scheduled. A trusted destination does not
authorize sending every piece of retrieved context there. Local-first also means
that fallback to a cloud model requires an allowed data destination.

Reuse the existing approval UI and policy enforcement. Bind approvals to exact
actions, scope, expiry and preconditions; a later changed file, recipient or
permission revision invalidates the old decision. Preserve guest restrictions,
Stop everything, blocked applications, external-content controls and audit
records. Do not claim that instructions, model guardrails, redaction or Python
socket hooks isolate arbitrary host commands or external MCP executables.

Credential handles should be passed to trusted connector code, not model context.
The current shared local process/host arrangements do not establish per-agent
credential isolation. A stricter broker/sandbox can be evaluated separately for
high-risk execution; it is not a reason to replace the current Chat runtime.

## 8. One agent environment across communication surfaces

Proposed ingress envelope: stable channel account + platform user ID, resolved
principal, conversation ID, message/event ID, requested agent and authorized
work references. Authenticate before retrieving owner context. Do not equate a
username, channel membership, agent name or DM with the user's identity.

Adapters map this envelope into the same work/harness path. The same work event
may render in Chat, a specialist conversation, a group or a notification without
another model call. Deliveries retain agent ID, principal, work ID and evidence
links. A specialist may report directly when the selected destination allows it;
Echo need not rephrase every update. Echo should receive a targeted work event
when it owns a dependency or the user chose it as coordinator.

Use explicit subscriptions/delivery targets with dedupe keys based on work event
and recipient. Retry transport independently of reasoning. Record work outcome,
verification and delivery separately. Rate limits, quiet hours and sensitive
content previews belong to delivery policy. A lost desktop toast can leave a
durable unread chat/work event; an uncertain external send requires reconciliation.

Keep the current UI. Useful small additions, if implemented later, are work-owner
and state labels in existing activity/details, an explicit pause/next-wake view,
reviewable agent proposals and a clear “waiting for approval” link. There is no
proposal for a new dashboard, another chat feed or an Echo Code redesign.

## 9. Concrete workflows and expected behavior

| Workflow | Proposed sequence and ownership | Evidence / failure condition |
| --- | --- | --- |
| New specialist | Echo notices recurring investment research, offers a draft role, sources, budget and read-only capabilities. Owner reviews; existing persona CRUD activates the approved definition. A separate accepted responsibility enables future wakes. | Declining leaves no active agent/job. Research permissions never grant transaction authority. No financial advice or transactions are performed in this example. |
| Direct specialist conversation | User opens Jarvis's existing direct room and asks a question. Jarvis uses relevant session/project context and its own lessons; no mandatory Echo hop. | Any durable work references retain Jarvis as actor/owner. Unrelated chats are not bulk-loaded. |
| Multi-agent help | Echo owns a comparison, gives Jarvis source research and a temporary worker one bounded lookup. Echo checks cited results and synthesizes. | Child completion includes source/effect evidence; duplicates/empty promises stay incomplete. A helper cannot gain extra capabilities through its name. |
| Work transfer | User assigns a continuing project investigation to GLaDOS. Trusted coordination validates the grant, creates an ownership revision and records acceptance. | Echo retains responsibility until acceptance. Rejected/expired transfers do not create two owners. |
| Continuing responsibility | Jarvis checks project release status each morning. Deterministic source/change checks precede reasoning; relevant changes produce one bounded analysis and selected update. | Stable occurrence ID, current source, evidence and next wake. Missing source becomes blocked/failed, not an invented “no change.” |
| Interrupted execution | Backend stops after a draft is saved or a message may have been sent. On restart, reconcile recorded effects and source state, then choose a safe continuation. | Do not repeat the send blindly. Stale approvals are revalidated; unknown outcome remains explicit. |
| Cross-channel result | An authenticated owner asks from Discord for an existing work status; the adapter resolves identity, then reads authorized work state. An approved update can route to Chat and that destination. | No need to rerun research to reformat status. Wrong principal/destination is denied; transport retries do not regenerate work. WhatsApp/mobile remain future adapters until established. |
| Skill adaptation | Verified repeated work suggests a reusable procedure. Echo drafts a skill using existing proposal tools; owner reviews its version. | Drafts remain inactive; failures can retire a lesson or roll back a skill without modifying protected policy. |

## 10. Efficiency for local 4B and cloud models

Start with one admitted foreground inference and a bounded background queue as
a proposed default, not a setting changed here. Foreground preference should
take effect at safe boundaries; some providers cannot preempt an in-flight GPU
request. Keep pure data fetches parallel only when safe. Admit model work from
Chat, groups, workers, heartbeat, summaries and reflection through one budget
policy; avoid one unlimited queue per subsystem. Use bounded aging/deadlines so
foreground activity cannot starve an important scheduled job indefinitely.

Deterministic scheduling, authorization, state transitions, change detection,
deduplication and simple status rendering require no inference. Invoke a model
for ambiguity, planning, analysis and synthesis. Use short briefs, minimal tool
sets, structured arguments with validation and capped repair attempts. Escalate
capability or ask when needed; never silently substitute a cloud model or pretend
a bigger model eliminates state/permission problems.

Measure completed-and-accepted outcomes, schema/executable-call validity, evidence
quality, unauthorized-action blocks, duplicate effects, foreground p50/p95
latency, tokens, queue wait and RAM/VRAM. Compare direct Echo, Echo+skill and
bounded-worker execution on the same tasks with separate 4B/cloud results. Do not
transfer vendor benchmark percentages into a product performance promise.

## 11. Incremental engineering roadmap and acceptance

All rows are **proposed**; no implementation is authorized by this report itself.

| Order | Scope / reuse | Acceptance before expansion |
| --- | --- | --- |
| P0a — contracts | Define work/attempt/occurrence IDs, ownership and outcome vocabulary around StateStore, Lean Job and execution records. Map active versus legacy authority paths. | Simple Chat remains simple; one work item can link attempts in two sessions; old data still loads; no competing completion owner. |
| P0b — authority | Add immutable run/work context and explicit helper/transfer grants at `_build_turn`, delegation and worker boundaries. Reuse toolbox/policy/privacy. | Restricted caller cannot gain tools/data through delegation; permission revocation, changed privacy and taint still apply after a wait. |
| P1a — recoverable occurrence | Bridge the existing routine scheduler to stable occurrence/work records; retain model-free reminders. Journal claims/results and restart reconciliation. | Duplicate wake/manual run dedupes; crash before/after an effect is handled; stale grants stop; cancellation remains visible; reminder attribution is preserved. |
| P1b — durable waiting and delivery | Persist exact-action approval state and introduce delivery records/outbox via the same state owner. Reuse approval UI and channel senders. | Approval survives restart without authorizing changed arguments; transport failure does not falsify work success; uncertain sends do not blindly repeat. |
| P1c — context and channel identity | Enforce a consistent access scope for recall/search/delegation and stable principal bindings across native adapters. Reuse rooms, projects and memory records. | User A/channel guest cannot read owner work; specialist contexts stay relevant; corrections/forgetting apply; one event has one delivery per target where supported. |
| P2 — responsibility and adaptation | Owner-reviewed specialist proposals and assigned responsibilities; reuse persona CRUD, skills, lessons and existing activity/details. | Declined proposals create no active behavior; skill/tool changes require review; all active responsibilities expose owner, grant, next wake and pause. |
| P2 — measured scaling | Add shared admission, budgets, bounded worker policies and a representative offline/mock then live acceptance suite. | No background LLM starvation of Chat; budgets enforced; zero-model reminder/status paths; local/cloud behavior reported separately. Live tests remain a later user-authorized step. |
| Later — remote host / more channels | Optional hosted runtime, genuine mobile/WhatsApp inbound adapters and stronger process/credential brokers. | Explicit hosting/security/transport design and new acceptance; no claim that closing a local laptop leaves its jobs running. |

Before shipping code, use failure-injection tests for crashes around each effect,
duplicate events, concurrent claims, expired approvals, renamed agents, deleted
sessions, malformed work records, unavailable sources, blocked privacy modes and
channel delivery failures. Mock tools/model outputs for deterministic contracts;
then owner-approved live tests verify actual tool use and local-model quality.
These are a proposed validation plan, not tests run during this research.

## 12. Boundaries: Echo Code and later subscriptions

Echo Native in Code may benefit from shared identity, memory and policy work.
Original Codex, Claude and ACP runtimes retain their own sessions, accounts,
tool loops and execution rules. Link an external task/result through its adapter;
do not claim control of its internal reasoning or silently import its credentials
into native agents. [Existing adapters](../../apps/backend/agent/code_runtimes.py).

The current Claude worker requires an API key. Official SDK authentication and
running an unmodified original Claude Code binary have distinct conditions; the
current limitation is not established as a general beta-access wait. No changes
to that integration are proposed in this pass.
[Worker](../../apps/backend/agent/code_claude_worker.py),
[SDK guidance](https://code.claude.com/docs/en/agent-sdk/overview),
[original binary conditions](https://code.claude.com/docs/en/legal-and-compliance).

A managed Echo subscription remains a separate future hosting, billing and
capacity decision. This report establishes neither a $12 price nor unlimited
models, provider resale rights, or use of consumer subscription quotas.

## 13. Verification performed and remaining uncertainty

- Traced the current source paths linked above, including native runtime/loop,
  persona/room APIs, context/memory, learning/skills, tool policy, routines,
  reminders/heartbeat, StateStore, notifications and channel implementations.
- Read the official product/engineering documentation and paper records linked
  in section 3, including the 22 October 2026 guidance. Vendor implementation
  descriptions were not independently reproduced; full private internals remain
  unknown. Paper results are limited to their published setups.
- Preserved the user's untracked projects and older collaboration document.
  The original 2026-10-02 review remains below, unchanged and labeled historical.
- No model calls, API-key use, account inspection, sends, new routines, builds or
  live runtime acceptance. Existing installed settings were not read, so defaults
  do not imply what is currently enabled on this computer.
- Documentation checks passed at 1440 × 1000, 768 × 1024 and 390 × 844 in dark
  and light themes, with workflow details collapsed and expanded (12 states).
  Eight diagrams were present, section/local links resolved, no horizontal
  overflow or out-of-viewport cards were found, and the report made no network
  requests or loaded scripts/external resources. Desktop role/ownership and phone
  context/lifecycle renders were visually inspected. Current technical-record
  local references resolved; the historical review matched its original Git text.
  `git diff --check` passed. No backend regression suite is claimed for a
  documentation-only pass.

Discussion decisions: choose the first real responsibility; decide how Echo may
suggest new specialists; agree on work-grant scopes and useful update rules.
Local-only execution remains the initial assumption unless hosting is separately
chosen. The roadmap does not require rebuilding the interface or replacing Lean.

---

# Historical review: 2026-10-02

The following original review is retained unchanged. Read its “today” references
as that date, not as the current source baseline.

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
