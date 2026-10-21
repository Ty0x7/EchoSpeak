# EchoSpeak full-system audit and competitive harness research, 2026-10-21

**Scope.** Every 11.x system as it exists on branch `claude/full-audit`. That branch is stacked on two open PRs:
- PR #83, `claude/autonomy-swarm`;
- PR #82, `claude/harness-audit`.

Both sit on `main` = 11.9.3.

**Other documents.**
- Visual report: [artifacts/research/full-audit-2026-10-21.html](../../artifacts/research/full-audit-2026-10-21.html)
- Companion architecture record: [echospeak-architecture.md](echospeak-architecture.md)
- Manual tests: [../TESTING_GUIDE.md](../TESTING_GUIDE.md)

**Evidence labels.**
- **Verified:** reproduced, or covered by a test in this repository.
- **Researched:** a primary source is linked.
- **Open:** not established. That includes anything needing a real model, the packaged Windows build, or a browser.

## 1. How EchoSpeak works today

One owner message goes through these steps:

1. **Composer.** Sending while an agent works queues the message or steers the run.
2. **`/query/stream`.** In the desktop app the API needs a per-launch key; there is no localhost bypass (`apps/desktop/src-tauri/src/backend.rs:301`).
3. **`LeanSession`.** A lock allows one turn per chat. It picks the agent and builds the toolbox from toolsets, permission switches and Blocked apps.
4. **System prompt.** It contains:
   - the soul;
   - identity from `version.py`;
   - rules for the tools actually offered;
   - the environment;
   - a capability report built in code;
   - memories, lessons and skills.
5. **`LeanTurn` loop.** It reads steering, then calls the model. Each tool call passes these gates, in order:
   1. permission switches;
   2. the privacy gate (the socket backstop also applies);
   3. policy (secrets, Rule of Two, generation cost);
   4. approvals (mode, Always allow, For this chat, autonomous hold);
   5. Blocked apps (inside `open_application`).

   The call then runs. Outside content is wrapped, and quarantined when the reader is on.
6. **Guards** cover:
   - promises with no action, and claims with no tool behind them;
   - a check after code edits;
   - exact repeats and refused calls;
   - failure streaks and the refusal circuit breaker;
   - compaction.
7. **Persistence.** Messages and timelines are saved. Learning grades episodes from tool records. Unread steers move to the queue.

Around the loop:
- memory (facts about the owner);
- learning (graded episodes, lessons tested against a randomized holdout);
- research workers (read-only, under the lead);
- handoffs and group work, plus A2A (outbound asks every time; inbound is public);
- MCP with OAuth, Tasks and Apps;
- project documents and artifacts in a sandbox;
- traces, voice and routines.

The diagrams and file map are in [echospeak-architecture.md](echospeak-architecture.md).

### How it got here (11.x)

| Release | What changed in the harness |
|---|---|
| 11.0 | Learning from verified experience: V0–V4 episodes, the reflector and curator, a playbook, Rule-of-Two taint |
| 11.1–11.3 | Light theme, composer and context meter; memory-quality fix (11.2 stopped copying every turn into memory) |
| 11.4 | Live tool progress, step summaries. 11.4.1 fixed builds that silently dropped web search |
| 11.5 | Ask-and-recover, Stop everything, Agent Skills (reviewed, hash-pinned), memory clean-up. 11.5.1: a security-audit pass |
| 11.6 | Echo Connections (discover, propose, approve); group work that runs until done |
| 11.7 | Private and Offline modes with a socket backstop; smart model choice from track records |
| 11.7.1 / 11.8 | Native Claude computer tool, MCP OAuth/Tasks/Apps, quarantined reader, traces, saved tool outputs, signed A2A, Echo bench, LangChain removed. Shipped inside 11.9.0 |
| 11.9.x | Project workspaces (Markdown and HTML docs), Drama-3 voice, Work polish |
| PR #82 | Native file editing and the terminal had ignored the permission switches (fixed); capability report; refused-call memory; lessons tested against a holdout |
| PR #83 | Self-knowledge, Blocked apps, Always allow and Autonomous, queue and steer, research workers, Learning tab redesign |
| this branch | Audit fixes below |

## 2. Findings

### Confirmed and fixed on this branch

| # | Severity | Finding | Fix |
|---|---|---|---|
| F1 | High | **Always allow grants reached channels and routines.**<br>The grant check ran before the non-interactive refusal. An untainted owner Discord DM, or a routine, could run commands on this PC that previously needed approval in the app. | `2d61d52`: grants apply only to chats in the app. Test: `test_a_grant_never_reaches_channels_or_unattended_runs`. |
| F2 | High | **The host-command detector was bypassable.**<br>It only matched commands after spaces or operators, so a destructive command quoted after `cmd /c`, `pwsh -c`, `node -e` or `python -c` ran without asking. So did `[IO.File]::Delete`, `fs.rmSync`, `os.system` and `Invoke-Expression`. Reproduced: `cmd /c "rd /s /q C:\Users\me\Documents"` → RUN. | `4adc356`: a quote starts a command, and those forms are listed. 10 cases tested both ways. |
| F3 | Medium-High | **MCP action results and `find_tools` descriptions were trusted text.**<br>Only MCP *read* tools tainted the request, so an action's result, or a server-written tool description (tool poisoning), could steer later sends without the Rule of Two. | `4017f78`: all third-party server output, and `find_tools`, count as outside content. |
| F4 | Medium | **A steer could vanish into a helper.**<br>Research workers and delegated teammates share the chat's session id and consumed the inbox. | `7b6f5bd`: only the agent the user is talking to reads steering. Reproduced first: the test failed before the fix. |
| F5 | Medium | **The queue pause after Stop lived in browser memory.**<br>After Stop plus a restart or reconnect, queued messages started by themselves. | `e3e27e6`: `queue_paused` is kept in `StateStore`. Set by Stop and Stop everything, cleared by the next message or Run next. Survives a simulated restart in a test. |
| F6 | Medium | **Research-worker episodes skewed track records.**<br>They fed agent and model routing records and reflection contrasts. Their cloud spend was missing from the router's daily budget. | `f110d79`: excluded from records and contrasts (still shown in Learning). Spend is counted when the router picked the model. |
| F7 | Medium | **Learning graded refusals as the agent's failures.**<br>A switched-off, privacy-blocked, held or Blocked-apps call counted as the agent's failed action, hurting lessons, track records and tool reliability. | `7d5f9a5`: these are "not run". |
| F8 | Low-Med | **Nothing stopped an agent probing past "no" with new arguments.** | `7d5f9a5`: one firm note after 3 refusals in a row; after 10 in a turn, it hands back to the user. Follows Claude Code (3 in a row / 20) and Codex auto-review (3 in a row / 10 of 50). |
| F9 | Low | **A worker at its step cap lost its findings.** | `7d5f9a5`: one tool-less "grace" call to report them, after Gemini CLI's subagent grace turn. |
| F10 | Low | **Composer and paths** (reported 2026-10-20 from a 4B model run): the queue notice squeezed the composer, and sandbox `/work/…` paths were refused by file tools. | `e6ff2a6`, on the base branch. |

Earlier in this series:
- In PR #82, native `file_edit` and `terminal` had ignored Settings › Permissions.

### Checked and holding (no change needed)

- **Desktop API.** A random 64-hex key per launch, `API_AUTH_LOCALHOST_BYPASS=false`, proxy headers not trusted. Dev mode keeps the localhost bypass by design, behind DNS-rebinding and Origin checks (`api/auth.py`).
- **No HTTP route runs tools outside the loop.** Routes only call the session; capability routes only read the registry.
- **Callers.**
  - A2A inbound and Twitch/Twitter resolve to *public* (look-up tools only).
  - Unknown sources fail closed (`agent/adapters.py`).
  - Approvals and autonomy only apply to app chats.
- **Frames and views.**
  - Artifact frames have a CSP sandbox with no network.
  - MCP App views can reach only declared `https`/`wss` origins. That excludes the local `http` API.
  - App-initiated tool calls are limited to read-only tools of the view's own server.
  - Private and Offline modes still hold through the socket backstop.
- **`/work` path mapping can't escape.** `..` paths are refused by the existing folder checks (tested with three traversal forms).
- **Message history is rebuilt as text only**, so an interrupted turn can't leave an orphaned tool call (OpenHands needs an explicit backfill for this).
- **Ping-pong loops** (A/B/A/B) are caught, because repeat counts are per turn and signature-based, not just consecutive.
- **Compaction** never rewrites the system message (identity, capabilities), and clears old tool output before summarizing.
- **MCP OAuth** passes `iss` through to the SDK for RFC 9207 issuer checks.

### Open risks and debt

- **R1, highest risk: Auto terminal mode falls back to the host when Docker isn't running.** On most Windows PCs that means always. On the host, only commands the detector flags ask first, and a denylist can't judge a script by its name (`python cleanup.py`).
  - Codex, Claude Code and Cursor rely on OS sandboxes. Codex ships a native Windows sandbox; Claude Code runs unsandboxed on native Windows and points to WSL2.
  - **Owner decision:** keep the fallback, ask for every host command when the sandbox is unavailable, or require an explicit choice.
- **R2: Grants never expire and aren't scoped by mode.** Goose gives saved grants an expiry. Gemini scopes "allow for all future sessions" by the mode it was granted in. Claude Code keeps file-edit grants to the session.
- **R3: Prompt injection has no classifier.** Defenses are:
  - the Rule of Two (enforced in code);
  - wrapping;
  - the quarantined reader (email and apps by default; web pages only in "all").

  That matches documented practice (wrapping plus confirmation at the point of risk). Commercial systems add trained classifiers, e.g. Claude Code auto mode's reasoning-blind classifier: 0.4% false positives, 17% false negatives ([C3]).
- **R4: Drift.**
  - The legacy `/capabilities` route computes "allowed" with `core._action_configured`, separate from the toolbox. The UI uses `/health/capabilities`, so there's no user impact.
  - `INTERACTIVE_SOURCES` lists `desktop`, `chat` and `api`, which no route sends.
  - 23 unused imports.
  - `core.py` has legacy helpers.
- **R5: Unverified with real models.**
  - Whether models use the capability report.
  - Swarm benefit: `scripts/swarm_bench.py` hasn't been run.
  - Learning benefit: `scripts/eval_learning.py` hasn't been run.
- **R6: Unverified in the packaged build and browser.**
  - The Documents default folder.
  - Blocked apps against real Start-menu names.
  - Live UI behavior of the queue, steering and the Workers card.

## 3. Competitive research

Two research passes on 2026-10-21 read primary sources:
- **Commercial:** OpenAI ChatGPT agent/Work, Codex, Claude Code and Agent SDK, Gemini CLI and ADK, Cursor, GitHub Copilot, Devin.
- **Open source, from code:** OpenHands SDK, LangGraph and LangChain v1, Microsoft Agent Framework (AutoGen is archived), CrewAI, Letta and letta-code, Open Interpreter (now a Codex fork), Codex CLI, Gemini CLI, Goose, SWE-agent, smolagents.

Proprietary internals are treated as unknown.

| Area | EchoSpeak | Strongest comparable practice | Assessment |
|---|---|---|---|
| Loop guards | Exact and per-turn repeats, refused-call memory, failure streak, refusal breaker, promise/claim/check guards | OpenHands StuckDetector: 5 patterns, nudge once per streak (`stuck_detector.py`). Gemini CLI LoopDetectionService: hash, text chunks, LLM check, two strikes then abort (`loopDetectionService.ts`) | **On par.** Claim/promise guards are uncommon. No text-repetition or LLM stuck check (not needed for most local models) |
| Context | Clear old tool output, then summarize; spill to `out-…` handles; system message untouched | Codex: stateless prefix-cached loop plus encrypted compaction. Claude Code: clear, then summarize, and stops if thrashing. Gemini: compress at 50%, verify pass, reject growth | **Sound.** Missing a self-check pass and an explicit thrash stop |
| Tool discovery | Deferred connected-app tools behind `find_tools` (more than 12); capability report built in code | Claude tool search: defer, regex/BM25, >85% fewer definition tokens. Gemini: registry plus `BeforeToolSelection` hook | **On par.** EchoSpeak also tells the model why a tool is *off*, which no source documents |
| Permissions and autonomy | Switches, Blocked apps, policy, approvals (smart/always/never/autonomous), scoped grants, holds for the owner | Claude Code: deny → ask → allow precedence; auto mode = classifier; 3/20 fallback. Codex: sandbox × approval policy; auto-review "a reviewer swap, not a permission grant". Gemini: TOML tiers | **Sound and coherent.** Autonomy never widens boundaries. Weaker: no grant expiry, no OS sandbox |
| Sandboxing | Docker sandbox (Auto falls back to host); file tools scoped to folders | Codex: Seatbelt / bubblewrap / native Windows sandbox, network off. Claude Code: OS sandbox plus egress proxy, 84% fewer prompts | **Largest gap (R1)** |
| Steering and queue | Steer at safe checkpoints; durable queue, editable, paused by Stop | Codex: Enter injects, Tab queues. Claude Code: queued messages delivered after the current tool batch, Ctrl+Enter sends now, Up edits | **On par** (now durable across restarts) |
| Memory | Owner facts, relevance recall, stale-version filtering | Claude Code: typed auto memory plus a capped index. ChatGPT: Sources UI. Gemini: Auto Memory inbox for review | **On par.** A "Sources used" view would help |
| Learning from outcomes | Episodes graded from tool records, vetted lessons, randomized holdout control | **No surveyed product documents outcome-based adaptation**; letta-code reflection writes skills with git history | **Unusual and principled, but unproven** until `eval_learning.py` runs |
| Multi-agent | Read-only workers under a lead, shared budget, local 1 / cloud 4 parallel, grace turn | Claude subagents: depth 3, 20 at once, ~15× tokens. Codex subagents inherit the sandbox. Gemini: no nesting, grace turn | **Sound** (matches the research guidance); benefit unmeasured |
| Verification and evals | Evidence-based completion check; Echo bench; swarm bench; learning eval | OpenHands API-compliance plus behavior tests. Gemini ALWAYS / USUALLY passes evals. SWE-agent reviewer loops | **Good tooling, not yet run on real models** |
| Injection defense | Rule of Two in code, wrapping, quarantined reader, taint shared across helpers | Classifiers, RL training, GET-only fetches, no constructed URLs, memory off while browsing (ChatGPT agent card) | **Deterministic core is strong; no classifier** |
| Observability | Per-turn traces, OTLP, audit log of policy decisions | Claude Code OTel `tool_decision` with verdicts; Codex logs | **On par** |

**Strong decisions worth keeping:**
- The capability report built in code.
- The Rule of Two enforced outside the model.
- A boundary that autonomy can't widen.
- Evidence-graded learning with a control group.
- Read-only workers with a single writer.
- Text-only rebuilt history.
- Deterministic gates instead of an LLM approver.

**Over-engineering to avoid** (from the code review):
- LangGraph-style durable replay, which needs every tool to be idempotent.
- Supervisor or group-chat libraries; AutoGen is archived and LangChain archived its supervisor library.
- An LLM "guardian" approver for a single user.
- A hand-built Windows sandbox at Codex scale, about 100 files.
- An LLM call per memory save.

## 4. Verification on this branch

- **New and updated tests:**
  - autonomy grants on channels;
  - 10 command-detector cases;
  - MCP taint;
  - helper steering;
  - durable queue pause (including a restart);
  - worker exclusion and cloud spend;
  - not-run grading;
  - the refusal breaker;
  - worker grace;
  - an end-to-end LeanSession test across context, swarm, policy and learning.
- **Static analysis** (pyflakes over `agent/` and `api/`): no undefined names. 23 unused imports and 4 cosmetic warnings remain.
- **Full suites, run at `49b159e` on Windows with Python 3.12:**
  - backend `python -m pytest -q`: 1153 passed, 13 skipped, no failures;
  - web `npm run check` (typecheck plus vitest): 165 passed.
- **Probes** (scratch scripts, not committed):
  - The command detector was run against the bypass forms before and after the fix.
  - Three `/work/..` traversal forms were refused.
- **Not verified:** real-model behavior, the packaged Windows build, live browser UI, and paid or cloud benchmarks.

## 5. Roadmap

**Before the next release (reliability):**
1. Decide R1. Options:
   - ask for every host command when the sandbox is unavailable; or
   - require an explicit choice of "this PC"; or
   - offer WSL2 as the sandbox.
2. Run `swarm_bench.py` and `eval_learning.py` on one local and one cloud model. Keep research workers and learning defaults only where they win.
3. Run [TESTING_GUIDE.md](../TESTING_GUIDE.md) on the packaged build (sections 1–3, 8–10, 14 first).
4. Version bump and release notes for PR #82, PR #83 and this branch.

**Near term:**
5. Grant expiry, and grants scoped by the mode they were made in.
6. Message-shape tests for steering, compaction and parallel tool results (OpenHands a01–a08 style).
7. Retire the legacy `/capabilities` "allowed" logic in favor of `capabilities.report`; remove dead sources and unused imports.
8. A compaction self-check and thrash stop (Gemini, Claude Code).

**Research (gated on evidence):**
9. An OS-level sandbox on Windows (restricted tokens, ACLs, WFP) or WSL2.
10. A reasoning-blind reviewer for Autonomous mode, measured for false positives and negatives before adoption.
11. An egress allowlist proxy for the terminal.
12. If learning proves useful: block-level randomized curation (arXiv 2610.18427).

## Sources

**Commercial:**
- Anthropic, Claude Code auto mode (2026-03-25): https://www.anthropic.com/engineering/claude-code-auto-mode
- Claude Code permissions: https://code.claude.com/docs/en/permissions
- Claude Code sandboxing: https://code.claude.com/docs/en/sandboxing and https://www.anthropic.com/engineering/claude-code-sandboxing
- Claude Code interactive mode: https://code.claude.com/docs/en/interactive-mode
- Tool search: https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool
- Multi-agent research system: https://www.anthropic.com/engineering/multi-agent-research-system
- OpenAI, unrolling the Codex agent loop (2026-01-23): https://openai.com/index/unrolling-the-codex-agent-loop/
- Codex sandboxing: https://learn.chatgpt.com/docs/sandboxing
- Codex auto-review: https://learn.chatgpt.com/docs/sandboxing/auto-review
- Codex steering PR: https://github.com/openai/codex/pull/10690
- ChatGPT agent system card: https://cdn.openai.com/pdf/6bcccca6-3b64-43cb-a66e-4647073142d7/chatgpt_agent_system_card_launch.pdf
- Gemini CLI policy engine: https://geminicli.com/docs/reference/policy-engine/
- Gemini CLI auto memory: https://geminicli.com/docs/cli/auto-memory
- Cursor run modes: https://cursor.com/docs/agent/security/run-modes
- Copilot agent risks and mitigations: https://docs.github.com/en/copilot/concepts/agents/cloud-agent/risks-and-mitigations

**Open-source code:**
- OpenHands stuck detector: https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/conversation/stuck_detector.py
- OpenHands confirmation policy: https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/security/confirmation_policy.py
- OpenHands condenser: https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/context/condenser/README.md
- Gemini CLI loop detection: https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/services/loopDetectionService.ts
- Gemini CLI subagent executor: https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/agents/local-executor.ts
- Codex protocol: https://github.com/openai/codex/blob/main/codex-rs/protocol/src/protocol.rs
- Codex exec policy: https://github.com/openai/codex/blob/main/codex-rs/execpolicy/README.md
- Goose permission store: https://github.com/aaif-goose/goose/blob/main/crates/goose/src/permission/permission_store.rs
- Microsoft Agent Framework tool approval: https://github.com/microsoft/agent-framework/blob/main/python/packages/core/agent_framework/_harness/_tool_approval.py
- LangGraph supervisor (archived): https://github.com/langchain-ai/langgraph-supervisor-py
- letta-code reflection: https://github.com/letta-ai/letta-code/blob/main/src/agent/subagents/builtin/reflection-v2.md

**Research:**
- Reliable Memory from Unstable Growth (randomized curation, 2026-10-13): https://arxiv.org/abs/2610.18427
