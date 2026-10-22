# Model versus harness: execution evidence for EchoSpeak, 2026-10-22

**Scope**
- **Question:** how much of an agent's reliability should rest on the model, and how much can EchoSpeak's harness carry through environmental awareness, execution control and evidence of real outcomes?
- **Branch:** `claude/harness-evidence`, stacked on PR #88 (`claude/live-test-fixes`).
- **Visual report:** [artifacts/research/harness-evidence-2026-10-22.html](../../artifacts/research/harness-evidence-2026-10-22.html)
- **Related records:**
  - [echospeak-architecture.md](echospeak-architecture.md)
  - [full-audit-2026-10-21.md](full-audit-2026-10-21.md)
  - [live-test-2026-10-22.md](live-test-2026-10-22.md)

**Evidence labels**
- **Repository:** read in code at the paths given.
- **Researched:** a source is linked; secondary sources are marked.
- **Speculative:** a direction not implemented or measured.

## 1. The recent failures are one problem

The 11.10 work fixed a list of failures:
- a claimed file that was never written;
- a Start-menu click that missed, reported as success;
- a Blocked app reached through the terminal;
- a refusal graded as the agent's failure;
- worker spend missing from budgets.

Taken one by one, each fix added a check. Taken together, they show one structural weakness (repository).

**The harness knows what happened to each call, then throws that knowledge away as prose.**

Where an outcome was decided, and how its consumers had to re-learn it:

| Decided by | Written as | Re-learned by |
|---|---|---|
| Policy deny (`loop._run_tools`) | "Blocked by EchoSpeak's safety policy: …" | Refusal breaker: `loop._REFUSAL_MARKERS`. Learning: `episodes._NOT_RUN` |
| Owner denial or timeout (`loop._approve`) | "The user denied permission…" | The same two phrase lists |
| Autonomous hold | "Not run: … autonomous mode…" | The same |
| Privacy gate (`toolbox.run`) | "Blocked by the user's privacy setting…" | The same |
| Switched-off tool (`capabilities.unavailable_message`) | "… is not available right now…" | `_NOT_RUN_ANYWHERE` |
| Tool success | Any text not starting with an error word (`toolbox._FAILURE_PREFIXES`) | `ok` |
| A file written | "Wrote mics.md", or nothing at all | Claim checks: regex over the reply, plus tool names (`job.unbacked_claim`) |
| A click with no visible result | Nothing until PR #88 | Nothing |

**Consequences:**
- **Fragile meaning.** A reworded message silently changed what counted as a refusal or a failure.
- **No ground truth.** The model's own report was the only evidence that a change happened, and the claim checks could only compare reply text against tool names.

Research says this is the central failure mode, not an edge case:
- **False success.** "From Confident Closing to Silent Failure" (arXiv 2606.09863, June 2026, single-author preprint) finds false success in 45–48% of failures in tau2-bench single-control domains, and 75.8% of AppWorld coding-agent trajectories with explicit status claims.
- **LLM judges don't fix it.** No LLM-judge configuration exceeded AUROC 0.65 at detecting it, because judges key on confident closing language rather than state changes. [Researched](https://arxiv.org/abs/2606.09863)
- **Fix the evidence, not the judge.** The lesson for EchoSpeak is to make evidence come from the environment, not to add a model that grades the model.

## 2. What other systems do with the same problem

| System | Relevant approach | What EchoSpeak takes from it |
|---|---|---|
| **Microsoft UFO2** (arXiv 2504.14603) | Hybrid GUI and native-API actions. Windows UI Automation fused with vision for control detection. Speculative multi-action planning, checked with cheap UIA state reads (enabled, visible) instead of more inference. [Researched](https://arxiv.org/html/2504.14603v2); speculative-execution details partly from secondary reviews | Use what Windows already knows. PR #88 lists named UIA controls with exact points. Here, cheap local observation (a stat, an exit code, a pixel diff) replaces model judgement of outcomes |
| **Meta Muse** (Sept 2026) | Each user's agent runs in its own cloud VM. A separate **Sentinel** agent approves anything that leaves the VM ("Nothing Muse does reaches the internet unless the Sentinel approves it"). Credentials are used without the model seeing them. An audit trail of completed and planned actions. [Meta announcement](https://about.fb.com/news/2026/09/introducing-muse-personal-ai-agent/); press coverage for later details | Boundaries live outside the acting model. EchoSpeak already has the deterministic equivalents: the privacy socket backstop, policy, approvals and the audit log. The gap was where a boundary depended on circumstance (section 4) |
| **Claude Code** | PreToolUse can block; PostToolUse receives `tool_response` and can only report back (the tool already ran). Sandboxing applies to Bash. [Hooks docs](https://docs.anthropic.com/en/docs/claude-code/hooks) | Two phases: decide before (EchoSpeak's gates), observe after (now `ToolResult.effects`) |
| **Codex** | The sandbox is the technical boundary; approval policy is a separate layer deciding when to stop and ask. The Windows sandbox uses restricted tokens and synthetic SIDs. [Sandboxing docs](https://developers.openai.com/codex/concepts/sandboxing); Windows details from a third-party blog | The approval layer must not quietly widen when the technical boundary disappears. That is exactly EchoSpeak's Auto fallback |
| **LangChain Deep Agents** | A harness of middlewares on LangGraph: `write_todos` with statuses, a pluggable filesystem with permission rules, and a `task` tool whose subagents return one report. [Docs](https://docs.langchain.com/oss/javascript/deepagents/middleware); some details from secondary guides | Statuses as data rather than text. EchoSpeak's research workers already return one report, with status and sources per worker |

Unknown proprietary internals (Muse's Sentinel model, Codex's server side) are treated as unknown.

## 3. What EchoSpeak already does well (keep)

These are repository-verified:
- **Deterministic gates outside the model.** Permission switches, Blocked apps, privacy (with a socket backstop), policy (secrets, Rule of Two, cost), then approvals. No LLM approves anything.
- **Capability awareness from code.** The prompt section and the "unavailable" result come from the same source (`capabilities.py`).
- **Learning graded from tool records**, never from self-report: the V0–V4 ladder, tamper detection and a randomized holdout.
- **Small-model aids that don't depend on the model**:
  - edit matching passes;
  - grounded control coordinates;
  - screen change measurement;
  - focused toolboxes for local builders.
- **Local-first and owner-controlled autonomy.** Always allow grants are scoped and revocable. Autonomous mode holds what it may not approve.

## 4. Confirmed weaknesses, and what changed

### 4.1 Stringly-typed outcomes. Implemented: execution evidence

`agent/lean/evidence.py` defines the stages a call goes through:

```
attempted -> executed (outcome) -> observed (effects) -> verified (learning's V-ladder)
```

**Outcome** is set by whoever decides it:
- **`ok` / `failed`:** whether the tool ran successfully.
- **`no_effect`:** it ran but changed nothing visible.
- **`refused`:** policy, privacy, a switch, Blocked apps or a send limit.
- **`denied`:** the owner said no, or didn't answer.
- **`held`:** autonomous mode kept it for the owner.
- **`skipped`:** a repeat, bad arguments, an unknown tool name, an earlier computer step failed, or Stop.

| Producer | Change |
|---|---|
| `ToolResult` | `outcome` (defaulting from `ok`) and `effects` |
| `Toolbox.run` | The privacy block and switched-off tools are `refused`; unknown tool names are `skipped` (`_unavailable`) |
| `Toolbox._observe` | After each call, `evidence.observe` stats the files a write, edit, copy, move, mkdir or delete named, under the same folder rules. A reported success that isn't on disk becomes `failed`, with a note to the model. Terminal results record where they ran and the exit code (or the background process). `computer_action` records the measured screen change; under 0.5% the outcome is `no_effect` |
| `LeanTurn._approve` | Returns `(allowed, text, outcome)` |
| `LeanTurn._run_tools` | Every early return records its outcome |

| Consumer | Change |
|---|---|
| Refusal breaker | Counts `refused` / `denied` / `held` outcomes. Phrase matching only for refusals a registry tool reports in its own words |
| Claim checks | `_written` comes from observed files. A path the harness can't resolve falls back to the call's own arguments. A new `unbacked_screen_claim` flags "has opened" after an action with no visible effect. `succeeded` counts only `ok`, so a no-effect click backs no claim |
| Learning (`episodes.tool_rows`) | `ok` and `not_run` come from the outcome. A no-effect action is not a success. The phrase lists grade older records |
| Traces | `tool.run` spans carry `outcome` and the kinds of effects |
| Tool-run records | The existing, previously unused `execution_status` and `verification` fields of `ToolRunRecord` carry the outcome and effects. Infrastructure that was already there |
| Chat | A `no_effect` row's summary reads "No visible change on screen", whatever the reply claims |

**Tradeoffs**
- **Stat cost.** A stat per file change is negligible. Observation never re-runs anything or calls a model.
- **Text checks remain.** The claim checks still read the reply's text: they compare a *claim* against evidence. That is unavoidable for free-form replies, but the evidence side is now observed.
- **Shallow checks.** "Exists with N bytes" is not "correct content". Correctness stays with the V-ladder's checks (read-backs, runs, tests). Effects make V1 "observed" honest; they don't replace V2+.
- **Tests had to change.** Some fakes reported writes they never made; the harness now catches that, as intended.

### 4.2 A security boundary that depended on circumstance. Implemented

**The inconsistency (repository: `terminal.approval_for`):**
- A command the model sent with `where="host"` asked first.
- The same command, when Auto mode had silently fallen back to the host because Docker wasn't running, ran unasked unless the dangerous-command denylist matched.

A denylist can't judge `python cleanup.py`. The earlier audit called this the largest open risk and left it for an owner decision.

**Decision.** The boundary follows the destination and the owner's intent, not the accident of whether Docker is up:
- **Auto falling back to this PC:** ordinary commands ask, with the reason "outside the sandbox". That keeps the existing "Run commands on this PC that aren't dangerous" Always allow grant, and Autonomous mode, applicable. The owner can restore the old behavior in one click.
- **This PC chosen explicitly:** unchanged. Only dangerous commands ask.
- **The agent is told.** The terminal description now says the sandbox isn't running and that commands will ask.

**Tradeoff:** more approval cards on PCs without Docker, until the owner chooses Always allow. That is the cost of not running unreviewed scripts on the host by accident. Codex and Claude Code both separate the technical boundary from the approval policy for this reason.

### 4.3 Overlaps worth knowing (not changed)

- **Two "done" sources.** `ok` (tool success) and V-levels (verified outcome) overlap at V1. With effects, V1 now means "ran, and its stated change was observed". No change to the ladder was needed.
- **Text prefixes still decide success.** `_FAILURE_PREFIXES` still decides `ok` for registry tools that only return strings. Converting each registry tool to return a structured result is broad, low-value churn. The observation step now catches the most consequential false successes (files) regardless of wording.
- **Two outcome vocabularies.** `retrieval_contracts.result_state` (data_found, no_data, …) describes *what a lookup found*; `outcome` describes *what happened to the call*. They are complementary, so they stay separate.

## 5. Speculative directions (not implemented)

Ranked by expected value. Each extends the evidence record rather than adding a subsystem.

1. **Precondition checks before clicks** (UFO2-style). Before a click on a listed control, read its UIA `is_enabled` / `is_visible` and refuse a click on a control that is gone or disabled. Cheap. Needs live testing on real apps.
2. **Effects for app launches.** Record that a window or process appeared after `open_application`; `no_effect` if nothing did within a few seconds.
3. **MCP structured results.** Map `structuredContent` and `isError` to effects when servers provide them.
4. **Evidence in learning's V-ladder.** Count an observed file effect as a read-back check (V2) only when combined with content verification. Needs care to avoid inflating levels; measure first.
5. **Egress per destination** (Muse's Sentinel). EchoSpeak's privacy backstop already gates sockets by component. A per-destination allowlist for the terminal sandbox would complete it. Medium effort.
6. **Windows OS sandbox** (Codex-style restricted tokens) or WSL2. This is the real fix for host execution. Large effort; the approval boundary above is the interim answer.

Deliberately not pursued:
- an LLM judge of outcomes (the research above);
- a second planning agent for small models;
- durable graph replay;
- more prompt instructions for things code can observe.

## 6. Verification

- **New tests:** `tests/test_execution_evidence.py`, 17 tests:
  - outcomes for denial, a repeated denied call, an unknown tool and success, through a real `LeanTurn`, both in the timeline and in `tool_end` events;
  - privacy and unknown-tool outcomes from the toolbox;
  - the refusal breaker counting outcomes with no refusal wording;
  - on-disk observation of writes and deletes, and a reported write that isn't there becoming a failure;
  - terminal effects for ok, non-zero and background runs;
  - `no_effect` from the screen measurement;
  - the Start-menu claim challenged after a missed click, with "No visible change on screen" on its row;
  - learning rows from outcomes with the text fallback;
  - the host-fallback boundary in both modes, including that its ask is grantable and autonomous.
- **Updated tests** (behaviour now intentionally stricter):
  - `test_learning` fixture: its fake write now writes;
  - `test_lean_tools` alias test: allows the harness note after a fake edit that wrote nothing;
  - `test_lean_harness`: pins This PC mode explicitly;
  - `test_security_policy` and `test_computer_workflow`: use `_approve`'s new return value;
  - `test_check_step`, `test_group_completion`, `test_lean_work_together`: their fake file tools never touch disk, so those tests turn the disk observation off and say why.
- **Full backend suite:** 1220 passed, 13 skipped. Pyflakes on the changed modules shows only the old shadowed `field` warning in `toolbox.py`.
- **Not verified:**
  - live model behavior with these signals;
  - the packaged app;
  - the approval cadence on a PC without Docker.

## 7. Manual checks

1. **Docker stopped.** Ask Echo to run `python --version` in the terminal. Expected: an approval card saying the sandbox isn't running. Choose Always allow; later ordinary commands run without asking.
2. **Start button.** Take a screenshot, then ask Echo to click somewhere empty. Expected: the row says "No visible change on screen", and a reply claiming success is challenged.
3. **Not-run grading.** Deny a delete, then open Learning. Expected: the denied call isn't counted against the agent.
