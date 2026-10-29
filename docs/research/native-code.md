# Native Echo Code: its own environment on the shared harness

EchoSpeak 11.11.8 makes native Echo Code a coding environment instead of an Agents conversation. Before, a native Code session ran as **Echo the agent**, with Echo's persona, team, delegation, soul editing and agent-work tools, and Echo's own configured model. Now the model you picked answers *as itself, as a coding assistant*, with an engineering workflow, engineering tools, project and git context, and 11.11.5's verification. Chat, Code and Agents still share one runtime, loop, toolbox, ledger, approvals and learning. [Visual comparison](../../artifacts/research/native-code.html).

## 1. Audit: where Code inherited Agents

| Area | Before | Problem |
|---|---|---|
| Routing (`agent/environments.py`) | `model_first` was true only for Chat; everything else, including native Code, went to the Agents path | Code answered as **Echo**: persona, soul, "your team" roster, delegation |
| Model | Echo's persona model when Echo has one configured; otherwise the conversation's | The model picked in the Code composer could be overridden by Echo's own setting |
| Tools | Echo's full toolbox: reminders, media generation, live-data cards, shopping, desktop control, agent work and notes, `soul_update`, `delegate_to_agent`, research workers | Noise and wrong affordances for engineering; small models choose worse from long lists |
| Prompt | Echo's soul, rich-answer rules (charts, cards), a team section, past chats from all environments | Nothing about the engineering workflow; git rules only when the request mentioned git |
| Activity | A command that exited non-zero kept its present-tense label ("Running `pytest`") | A failing test run looked unfinished in the timeline |
| Learning | Episodes credited to Echo | Code's engineering experience mixed into Echo's agent record |
| Provider rule | "Plans for code, APIs for chat" (28 Oct): native Code never calls an API-key provider | Correct, and kept (§3) |
| External runtimes | Codex, Claude Code and ACP through `agent/echo_code.py` sessions | Correct, and untouched |

## 2. Research: how coding harnesses work, and what Echo takes

| Practice | Claude Code / Codex | Echo Code now |
|---|---|---|
| **The model drives a tool loop** | One agent: read, edit, run, iterate until done | The same Lean loop, with the selected model as the answerer (`code` persona) and no agent identity |
| **Project instructions** | `CLAUDE.md` / `AGENTS.md`, nearest file wins ([Claude Code tips](https://support.claude.com/en/articles/14554000-claude-code-power-user-tips), [Codex](https://learn.chatgpt.com/docs/customization/overview)) | Project instructions (`home.instructions_section`), the project overview, and git status in every Code prompt |
| **Precise edits** | Exact-string replace, plus new-file writes | `file_edit` (exact text replace) and `file_write`, checkpointed for `checkpoint_undo` |
| **Terminal, tests, git** | Run tests and builds; inspect with git status and diff; don't commit unless asked | Terminal with Echo's permissions; git rules always on in a repo; "don't commit, push, rebase or discard unless asked" |
| **Verification** | Run the check after changing code; report honestly | 11.11.5's ledger: parsed checks, progress against the baseline, stalls, a false-success nudge, and completion under the answer |
| **Observable activity** | A timeline of tool calls (read, edit, ran, output) | Lean tool steps: "Read", "Edited", "Wrote", "Ran `cmd`" with "Tests: 5 passed" / "Exit code 1", plus the verification note |
| **Context management** | Compaction, progressive skill loading | The loop's compaction and saved long outputs (`read_output`). On local models, engineering tools up front and the rest through `find_tools` (`CODE_FOCUS`) |
| **Long work and recovery** | Resumable sessions, checkpoints | Work records, resume with the saved ledger (11.11.5), checkpoints, steering and cancellation, all unchanged |
| **Skills and corrections that compound** | Skills; corrections recorded in instruction files | Approved skills; Code learns as its own learner and can use proven coding lessons from Echo (measured transfer, 11.11.7) |

Not copied: Code doesn't run a separate "plan mode" or a second model for planning. The rules ask for a plan when the change isn't trivial, inside the same loop, so there are no extra calls.

## 3. Architecture

```
                  ┌── Chat   : chat persona (the model) · chat_mode rules/tools · ChatRun answer checks
LeanSession ──────┼── Code   : code persona (the model) · code_mode rules/tools · verification ledger
 (one harness)    └── Agents : Echo & agents · souls, team, delegation, routines, work
        shared: Lean loop · toolbox · approvals/policy · checkpoints · steering/cancel · ledger · learning · memory
```

- **`environments.turn_environment`** decides who answers. It's `code` for the owner, live, in a Code conversation, not resuming agent work. Routines, channels, guests and resumed work stay with their agent.
- **`code_mode.py`** defines Code's identity ("you're the model the user picked, not one of EchoSpeak's agents"), the workflow rules, the toolsets, the tool boundary (`restrict`) and the local-model focus.
- **The runtime** gives Code:
  - no hand-off, teammates, coordination tools, research workers or soul editing
  - no smart model routing: the model is the one you picked
  - project-scoped context, with no past chats from other environments
  - git rules whenever there's a repo
  - its own learner (`code`)
- **The provider rule is kept.** Native Code uses a model on this PC. ChatGPT runs in Codex and Claude in Claude Code with your plans; API-key models are for Chat. That was your 28 October decision, and the brief asks to preserve billing boundaries, so `code_provider_problem` still applies to the `code` persona.
- **External harnesses are untouched.** Codex, Claude Code and ACP sessions never enter the Lean loop.

## 4. Verification

These checks ran on this branch. No paid models were used, and only tests for the changed components ran.

- **New: `apps/backend/tests/test_native_code.py` (6 tests):**
  - Each conversation runs in its environment, and routines, guests and resumed work stay with their agent.
  - Code is the selected model with engineering tools: no agent-only or non-engineering tools, the Code workflow in the prompt, no team or cards, and the ledger attached.
  - **An end-to-end native Code run** with a scripted model and terminal: failing test → fix → passing test → answer. It makes exactly 4 model calls. The answer comes from `code` named after the model. Steps read "Ran `pytest …`", "Wrote dates.py", "Ran `pytest …`", and the note under the answer is "Checks pass — …". The episode is recorded under `code` / `code`.
  - Chat and Agents are unchanged.
  - The provider rule is kept.
  - Code can use Echo's proven coding lessons through measured transfer.
- **Re-run and passing (323):**
  - `test_environments`, `test_lean_runtime`, `test_lean_harness`, `test_lean_group`, `test_lean_soul`
  - `test_chat_mode`, `test_chat_paths`, `test_chat_lifecycle`
  - `test_check_step`, `test_live_steps`, `test_execution_evidence`
  - `test_learning`, `test_learning_research`, `test_autonomous_learning`, `test_verified_engineering`
  - `test_capabilities`, `test_screen_progress`, `test_ask_and_recover`, `test_echo_code`

## 5. Needs your live test (Code, native runtime, a local model)

1. Open a project in Code with the native runtime and ask it to fix a failing test. Expect:
   - messages headed by the model's name, not Echo
   - steps "Read …", "Edited …", "Ran `…`" with test counts
   - "Checks pass" or "Verified" under the answer
2. Ask for a feature in a git repo. It should inspect `git status` and diff, but not commit unless asked.
3. Small model (≤9B): only the engineering tools are listed. Others load through `find_tools` if needed.
4. Check Agents still feels like Echo (persona, delegation) and Chat still feels like Chat.

## 6. Limits

- **Mentions don't switch agents in Code.** An `@agent` in a Code conversation is answered by Code; agents live in Agents.
- **`file_edit` follows Settings › Permissions,** like everywhere else. Without file-write permission, Code can read and run but not edit.
- **The Code UI shows the existing Lean step timeline.** Inline diffs per edit aren't added in this release; the "Edited x" step and the file's checkpoint are the record.
