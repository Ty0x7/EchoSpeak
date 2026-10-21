# Harness audit, 2026-10-20

The audit covered `main` at 11.9.3 (`0ad3bec`). The changes are on branch `claude/harness-audit` and are not released. The visual summary is [artifacts/research/harness-audit-2026-10-20.html](../../artifacts/research/harness-audit-2026-10-20.html).

Scope: how well Echo understands and uses its runtime capabilities, the learning system, the Markdown/HTML artifact workflow, and the UI around them. Below, **verified** means reproduced or tested here. **Research-supported** means a published source backs it. **Hypothesis** is untested reasoning.

## Current state

- 11.8's harness work was integrated into 11.9.0 (PR #75) instead of shipping as 11.8.0, so PR #74 was closed. The work covers the native Claude computer tool, MCP OAuth/Tasks/Apps, the quarantined reader, traces, saved tool outputs, signed A2A, Echo bench and LangChain removal.
- 11.9.x added Drama-3 voice, project workspaces (an HTML-only Work view, Files, technical notes) and the `project-documentation` skill.
- The agent loop (`agent/lean/loop.py`) is mature. Its guards:
  - a promise guard and an unbacked-claim guard;
  - a check nudge after code edits;
  - goal reminders;
  - exact-repeat detection;
  - Rule-of-Two policy and the quarantined reader;
  - deferred connected-app tools behind `find_tools`.
- Learning (`agent/learning`) is graded from what actually ran (V0–V4 from tool records and tamper checks, never the model's self-report). Code vets lessons, owners can review them, and every change has a history.

## Findings

### 1. Permission switches were bypassed by native tools (verified, fixed)

Settings › Permissions has three switches here: **Allow actions on this computer**, **Create and edit files** and **Run terminal commands**. All three are off by default (`config.py`).

The switches do filter registry tools such as `file_write` and `terminal_run` (via `policy_flags`). They did not filter the lean runtime's own `file_edit` (`agent/lean/coding.py`) or `terminal` / `process_*` (`agent/lean/terminal.py`), which are the tools Echo actually uses.

Reproduction on 11.9.3 with every switch off:
- `Toolbox` offered `file_edit` and `terminal`.
- `file_edit` changed a file.
- `terminal` ran `echo hi`.

In Auto mode without Docker, the terminal falls back to this PC. That contradicts the guide ("turns whole abilities on or off") and the master switch's help text ("agents can still chat, search the web, and read files").

Fix (`agent/lean/toolbox.py`):
- `NATIVE_POLICY_FLAGS` names the switches each native tool needs.
- Tools whose switches are off are withheld when the toolbox is built.
- `_run` checks the switches again on every call, since a switch can change mid-turn.

Tests: `tests/test_capabilities.py` (withheld, offered, master switch alone not enough, switched off mid-turn leaves the file unchanged).

**Behavior change:** installs that never turned these switches on lose `file_edit` and `terminal` until the owner enables them. That is what the switches always said they did. Echo now tells the user which switch to turn on (finding 2).

### 2. Echo didn't know what was off, or why (verified, fixed)

`Toolbox` silently drops tools whose switches are off, that aren't in the agent's toolsets, or that aren't available. The model never learned they existed. Some prompt text was wrong:
- `WORKING_RULES` and `SHOWING_ANSWERS` taught `take_screenshot`, `research_notebook`, `create_artifact` and the card tools to agents without them.
- The Environment said "You may read and write files" even when writing was off.

Only desktop control had an accurate note (`Toolbox.computer_context`). A model calling a missing tool got "unknown tool" plus a list, with no reason. That invites workarounds, for example using the terminal to send email. Anthropic's 9 Oct report on unintended model actions describes exactly that pattern (research section).

Change: a new module, `agent/lean/capabilities.py`.
- `report(toolbox)` produces "## What you can use right now", built in code. Inputs:
  - the live tool names;
  - each tool's switches (`policy_flags` / `NATIVE_POLICY_FLAGS`);
  - `privacy.decide` for Private/Offline mode (web search checks the SearXNG host the way the search provider does);
  - the agent's requested toolsets;
  - the approval mode.

  It lists what is ready, what is off with the exact setting that turns it on, and when approvals apply. Owners only; guests never see the owner's settings. It costs about 250 tokens. It describes; it never grants.
- `unavailable_message(name, toolbox)` replaces "unknown tool" when the tool exists but is off, giving the reason and "don't retry it or work around it". Truly unknown names still get "unknown tool".
- `prompt._for_tools` drops tool-specific rules and card lists for tools the turn doesn't offer. The Environment says read-only when writing is off.

Example: default settings, Echo's default toolsets:

```text
## What you can use right now
Checked by EchoSpeak for this turn. Trust this over assumptions about your tools.
- Ready: Web search and reading pages; Live data cards (...); Reading files; Screenshots and reading images; Artifacts (...).
- Off right now (don't promise these or work around them; if the user needs one, say what turns it on):
  - Creating and editing files: turned off in Settings › Permissions (Allow actions on this computer)
  - Terminal commands: turned off in Settings › Permissions (Allow actions on this computer)
  - Desktop control (mouse and keyboard): not in this agent's toolsets (the owner can add "Mouse, keyboard and app control" under Agents, Edit)
  - Email: not in this agent's toolsets (...)
- Approvals: Risky actions ask the user first: ... A denied or blocked action is final for this request.
```

Why a section built in code rather than more prompt rules: it changes with the actual state each turn. The same source also produces the tool-call errors, so the prompt and the errors can't disagree.

### 3. The loop could re-ask a denied approval and keep varying a failing call (verified, fixed)

Exact repeats were caught at the third identical call. Two gaps remained:
- A call the user had just denied, issued again with the same arguments, opened a second approval card.
- A tool failing with different arguments each time was never flagged.

Changes in `agent/lean/loop.py`:
- Denied and policy-blocked calls are remembered by signature for the turn. The same call is answered "Not run: … already denied by the user" without asking again.
- After 3 failures in a row of one tool, with any arguments, the result says to stop varying it, and a `fail_streak` event is emitted.

Different arguments still go through normal approval: denying `rm -rf build` doesn't block `npm test`. Tests are in `tests/test_capabilities.py`.

### 4. Learning counted wins without a control (research-supported, changed)

The design is sound and is kept:
- Code-based grading, not self-assessment. This is the "independent verifier" condition in arXiv 2608.00017.
- Lessons from untrusted episodes and lessons about permissions are refused or held for review.
- Lessons are advice, not rules.
- Updates are deltas, not whole-playbook rewrites (avoiding ACE's context collapse).
- `scripts/eval_learning.py` has on/off/control ablations.

The weakness was credit assignment. A lesson earned a "win" on any checked success while it was in the prompt. If the agent succeeds at 85% of a task kind anyway, almost any lesson reaches 3 wins at 75%+ and becomes "proven". arXiv 2610.18427 (13 Oct 2026, verified on arXiv) found:
- self-written playbooks (ACE, Dynamic Cheatsheet) vary from helping to scoring up to 69.6 points below no playbook;
- agent self-selection of lessons scored 53.8 on average, against 55.5 for the whole playbook and 54.1 for none;
- randomized include/exclude curation scored 66.9.

Change (`agent/learning/playbook.py`, `__init__.py`, `store.py`, `episodes.py`, `lean/runtime.py`):
- `playbook.holdout` holds back a selected lesson at random: 20% if unproven, 10% if proven, never an owner-written lesson.
- The episode records it in `lessons_withheld`. Its checked result counts as the lesson's control (`held_wins` / `held_losses`): the same task kind, chosen by the same selection.
- `lifecycle` rules once there are at least 3 control results:
  - **No promotion** when the lesson's win rate is ≤ the control's.
  - **Retire** (or demote a proven lesson) when its rate trails the control's by more than 25 points.
  - With fewer control results the old rules apply, so lessons can still be promoted at personal-assistant volumes.
- Owner feedback (Worked / Didn't work) corrects control counts exactly as it corrects lesson counts.
- The Learning page shows "without it: n of m worked".

This is a simplification of the paper's block-level method: it works per lesson and online, with no practice set. At one person's task volume, the comparison will take weeks to decide anything. It is a safeguard against lessons that hurt, not proof that learning helps. Whether learning beats no learning is still open until `scripts/eval_learning.py` runs with a real model.

Not changed:
- Memory and learning remain separate stores with separate curators. That separation is correct.
- Calling this RSI would be wrong. It is memory-based adaptation: advisory text, code-decided, with no weight training and no self-modification. Owner-approved skills from proven lessons are workflow improvement.

### 5. Artifact workflow: sound, keep it (verified by inspection)

There are two complementary stores:
- **Chat artifacts** (`agent/lean/artifacts.py`) live in the app's data folder. They are versioned and interactive, served from their own URL with a sandbox CSP (scripts allowed, no network, opaque origin).
- **Project documents** live in the project: `docs/<topic>.md` is the technical record and `artifacts/<topic>.html` the visual report. `project_workspace.py` discovers them. Previews run with scripts disabled, and only bounded local images are inlined.

The `project-documentation` skill splits the work: Markdown for agents, developers and GitHub; HTML for people. It is optional, reviewed and hash-pinned (`agent_skills.import_documentation_skill`). The PyInstaller spec bundles it to `resources/agent-skills`, and dev builds fall back to `.agents/skills`.

Checked here:
- No repo report loads remote scripts, styles or fonts.
- The release reports pair 1:1 with their Markdown (32 HTML / 33 MD, counting the extra `10.x-history.md`).

Gap (hypothesis-level, not changed): Echo follows the pairing only when the skill is imported and approved. Without it, project write-ups become chat artifacts in app data, visible in the project's Work but not in its Git history. That trade-off is deliberate (no unrequested file writes). A non-model pairing check (Markdown without HTML, broken relative media, remote URLs) would be a cheap next step.

### 6. UI

- The Learning page shows control results on each lesson. `npm run check` passed (typecheck, 153 tests in 33 files).
- 11.9.3's Work cards were left as they are: you merged that layout in PR #81.
- Not verified in a browser. This worktree's session can't add a preview config to the shared `.claude/launch.json`, and the existing configs run the main checkout.

## Research, past week (13–20 Oct 2026) and recent background

Collected by a research pass on 2026-10-20. Only 2610.18427 was re-opened and checked here; treat the others as leads.

| Development | Status for Echo |
| --- | --- |
| Self-written playbooks are unstable; randomized curation beats self-selection ([arXiv 2610.18427](https://arxiv.org/abs/2610.18427), 13 Oct) | **Adopted** in reduced form (finding 4) |
| Memory reward inflation: self-graded episodes reinforce confident mistakes ([arXiv 2608.00017](https://arxiv.org/abs/2608.00017)) | **Already supported**: grading is from tool records, never self-report |
| Anthropic, unintended model actions: agents work around tool restrictions ([anthropic.com](https://www.anthropic.com/research/investigating-unintended-model-actions), 9 Oct) | **Adopted**: "don't work around it" reasons, refused-call memory (findings 2–3) |
| Evidence-first verification of agent claims ([Sauron, arXiv 2610.17724](https://arxiv.org/abs/2610.17724)) | **Already supported** in kind: unbacked-claim guard, V-ladder; no change |
| Loop detection should count failures beyond identical calls (community consensus; no primary source this week) | **Adopted**: 3-in-a-row failure streak |
| Claude Code: tool search on by default; tool availability kept correct across compaction and late MCP connections ([changelog](https://code.claude.com/docs/en/changelog)) | **Could benefit**: test `find_tools` state across `_compact_turn` and late MCP connects |
| MCP spec 2026-07-28: stateless transport, `iss` validation (RFC 9207), CIMD instead of DCR, Tasks as an extension ([blog](https://blog.modelcontextprotocol.io/posts/2026-07-28/)) | **Needs checking**: `agent/mcp_oauth.py` and `mcp_tasks` predate it |
| LangChain Deep Agents: offload large tool results to files, keep full history on disk ([blog](https://www.langchain.com/blog/context-engineering-deep-agents-remember), 14 Oct) | **Already supported**: `tool_outputs` handles plus `read_output` |
| Agents improving their own harness (RSIFlow, Self-HPO, HarnessPro) | **Avoid for now**: unreplicated; no regression gating |
| Tool interface shape drives consistency more than note-taking tools ([arXiv 2608.11386](https://arxiv.org/abs/2608.11386)) | **Supports** current direction: compact schemas, `TOOL_OVERRIDES` |

## Verification

- `tests/test_capabilities.py`: 17 tests on permission enforcement, reasons, the report, prompt filtering, refused calls and failure streaks.
- `tests/test_learning.py`: 4 new tests on control-gated promotion, harm retirement, holdout rates and control attribution with feedback. Existing fixtures now enable the switches their fake terminal needs and turn the holdout off for determinism.
- The report was printed from a real `Toolbox` with registry tools, under default settings and under Offline mode with permissions on (the example above).
- Full backend suite: 1080 passed, 13 skipped (baseline before changes: 1060 passed, 13 skipped). Four existing tests that drove fake native `terminal`/`file_edit` tools with the switches off now turn on the switches they rely on.
- `npm run check` (web typecheck and tests): 153 passed.
- **Not verified:** a live model reading the report, the packaged app, the UI in a browser, and any learning effect.

## Next steps

1. Show the same capability report in Settings › Permissions or the agent editor, so the owner sees what Echo sees.
2. Run `scripts/eval_learning.py` with a real model (on / off / control, `--repeat 3`). Keep learning on by default only if "on" wins.
3. Check MCP OAuth against the 2026-07-28 spec (`iss`, CIMD).
4. Test that tool availability is correct after in-turn compaction and after a late MCP connection.
5. Add a pairing/linter check for project docs.
