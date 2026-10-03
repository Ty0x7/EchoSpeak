# Harness review: Groups that finish, honest state, terminal, tools, memory, scrolling

Date: 2026-10-03. Scope: the lean runtime (`apps/backend/agent/lean/`), its HTTP surface
(`apps/backend/api/`), and the chat UI (`apps/web/src/`). Branch
`claude/echospeak-harness-groups-wo2l67`.

The 2026-10-02 review (group completion part A, the Rule-of-Two security design, the
settings migration) is kept as [`harness-review-2026-10-02.md`](harness-review-2026-10-02.md).
This review builds on it.

Verdicts: **adopt** = take as-is; **adapt** = take the idea, reshaped for a local,
single-user app on small models; **skip** = not worth it here (reason given). Every
verdict below ends in a code change or says why not.

---

## 1. Summary

| # | Problem | Root cause | What changed | Tests |
| --- | --- | --- | --- | --- |
| 1 | Groups stop at ~8 steps | Discussion mode was a fixed talk cap (UI 4/6/8 messages) with no execute step; "DONE" after two agents agreed was recorded as Done. Reply mode: 4 rounds. Unclear verdicts counted as done | Work-together loop: Discuss → Decide (`assign_tasks`) → Execute → Observe → Verify → Continue; completion from evidence; turn budget, token budget, repeat and no-progress backstops; Echo's wrap-up | `test_lean_work_together.py`, `test_lean_group.py`, `test_group_completion.py` |
| 2 | Talk loops instead of action | No shared state turning decisions into owned work; planning turns had every tool and the promise guard fired on legitimate plans; the lead couldn't see who can do what | Task board with owners and evidence, capped discussion (3 views), forced decision step, read-only planning turns, capability roster, promise guard on execution turns | same |
| 3 | "Saved to Soul" when nothing was saved | No tool could write the soul at all; nothing checked claims against tool calls | `soul_update` with atomic write and read-back through the real loader; `memory_save` read-back; claim check in the loop | `test_lean_soul.py` |
| 4 | Terminal misuse | Timeout killed the work (orphaned it in the sandbox); servers blocked 120s; prompts hung on inherited stdin; stderr unlabeled; redirect writes skipped checkpoints | Yield-to-background, server detection, closed stdin + prompt-free env, interactive refusal, stderr section and hints, redirect checkpoints, when-to-use guidance | `test_lean_terminal.py` |
| 5 | Tool sprawl | 37 tools per agent, overlaps, Glados carrying shopping/stock tools | Trimmed core, `research` split into `web`/`live`, `process_start` folded into `terminal`, aliases for other harnesses' names | `test_lean_tools.py` |
| 6 | Agents act lost | Recall keyed on "[System]" briefs; stopwords in relevance; teammates without the chat summary; past chats only on request; no on-track check | Task-aware recall, stopword filter, summary for teammates, automatic past-chat lookup on back-references, periodic goal reminder | `test_lean_recall.py` |
| 7 | Scrolling unreliable | Scroll events ignored while the chat's own 80ms pin was in flight; 48px "still at bottom"; a card scrolling the chat on its own | `ChatFollower`: user intent decides; upward wheel stops before the view moves; resume near the bottom | `chatFollow.test.ts`; Chromium run 7/7 (old 3/7) |

Test totals on this branch: backend 590 passed, 3 skipped, 5 failed; the same 5 fail on `main`
in this environment (`'_IncludedRouter' object has no attribute 'path'` from a newer FastAPI,
unrelated to these changes). Web: 107 passed; app typecheck clean.

---

## 2. Sources

Primary sources only; each was read for this review.

| Harness | Source read | Used for |
| --- | --- | --- |
| **Claude Code** (Anthropic) | [How Claude Code works](https://code.claude.com/docs/en/how-claude-code-works), [Hooks](https://code.claude.com/docs/en/hooks), Bash tool behaviour | Loop phases (gather context → act → verify), Stop hooks that block a premature stop, checkpoints, compaction order (clear tool output first, then summarize), subagents with their own context, system reminders, `run_in_background`, 30k-char output cap, dedicated file tools over `cat`/`sed`/`grep` |
| **Codex CLI** (OpenAI) | Source, `openai/codex` `codex-rs/core`: `gpt_5_1_prompt.md`, `gpt_5_codex_prompt.md`, `tools/handlers/shell_spec.rs`, `unified_exec/mod.rs`, `unified_exec/head_tail_buffer.rs` | `exec_command` yields a session id after `yield_time_ms` (10s default) instead of killing; `write_stdin` to poll or feed; head+tail output buffer, 10k-token default; `apply_patch` for edits; "persist until the task is fully handled"; validate from specific to broad |
| **Hermes Agent** (Nous Research) | Source, [`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent): `tools/memory_tool.py`, `memory_tool_store.py`, `file_tools_write_guards.py`, `delegate_tool.py`, `todo_tool.py`, `terminal_tool_background.py`, `terminal_hints.py` | Memory writes: lock, re-read, atomic temp+fsync+rename, explicit success JSON, "do not repeat it"; SOUL.md/AGENTS.md writes always need a human (prompt-injection persistence), fail closed; `terminal(background=true)`; failure hints keyed on output; subagents return only a summary with a typed exit reason; todo list re-injected after compaction |
| **OpenHands** | Source, `OpenHands/software-agent-sdk`: stuck detector (`clients/typescript/src/conversation/stuck-detector.ts`, mirrors `openhands/sdk/conversation/stuck_detector.py`) | Rule-based stuck detection: same action+observation ×4, same action+error ×4, agent monologue ×4, alternating pattern ×6 |
| **Magentic-One** (Microsoft AutoGen) | Source, `microsoft/autogen` `teams/_group_chat/_magentic_one/_prompts.py`, `_magentic_one_orchestrator.py` | Progress ledger per round: `is_request_satisfied`, `is_in_loop`, `is_progress_being_made`, `next_speaker`, `instruction_or_question`; stall counter (+1 no progress or in loop, −1 otherwise) triggers re-planning |
| Anthropic engineering | [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents), [Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents), [Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) | Agents declare victory early; explicit pass/fail task state; context rot; fewer, non-overlapping tools |

Hermes was verified as Nous Research's open-source agent by reading its repository. Its docs site
(hermes-agent.nousresearch.com) is blocked from this build environment, so every Hermes claim here
comes from the source.

---

## 3. Patterns compared with Echo

### Agent loop and stopping

| Pattern | Who | Verdict | What changed in Echo |
| --- | --- | --- | --- |
| A turn ends when the model answers without tool calls | Claude Code, Codex, Hermes | already there | `loop.py`: unchanged for one agent's turn |
| A *job* doesn't end on text: keep going until verified, or a backstop trips | Claude Code Stop hook (`decision: block`, `stop_hook_active`); Codex "persist until fully handled"; Magentic-One outer loop | **adapt** | `runtime.py` `_run_discussion` / `_close_job`: the run continues while the goal is unverified; every stop has a stated reason |
| Progress ledger: satisfied? looping? progressing? next speaker, instruction | Magentic-One | **adapt** | `job.py` `review_prompt` asks for `done`, `in_loop`, `next`, `instruction`; `in_loop` adds a stall; the instruction becomes a new owned task |
| Stall counter → re-plan | Magentic-One | **adapt** (stop instead of re-plan) | `Job.note_progress`: 2 rounds with no new successful tool call and no task finished → "Stopped: N rounds in a row made no progress". Re-planning once before stopping is proposed (P3) |
| Rule-based stuck detection | OpenHands | **adapt** | Repeats: `near_duplicate` (≥2 near-identical replies); loop: identical calls short-circuited after 2 (`loop.py`); failures: the on-track reminder (§6) says when most recent calls failed |
| Promise guard ("I'll do it" with no call) | Echo (2026-10-02) | kept, scoped | Off in planning turns (a plan isn't a broken promise); the nudge names only a handoff tool the agent has |

### Verification and honesty

| Pattern | Who | Verdict | What changed in Echo |
| --- | --- | --- | --- |
| Done means the evidence says so | Anthropic long-running harness (pass/fail per feature); Claude Code "verify results" phase | **adopt** | `Job.evidence`: every non-bookkeeping tool call (agent, label, ok, output head). The completion check reads the tool log; a task that needed work and was "done" with zero successful calls is reopened (`unbacked_tasks`); an unreadable verdict is decided from evidence, never defaulted to done |
| Writes confirm themselves | Hermes memory store (explicit success JSON, "Write saved"); Codex ("the tool call will fail if it didn't work") | **adopt + read-back** | `soul_update` and `memory_save` re-read from disk through the path the next chat uses before saying "Saved and verified" |
| Claims checked against actions | none of the four (they rely on prompts) | **new** | `job.py` `unbacked_claim`: "I've saved / sent / created…", "it's saved in my soul", "I'll remember that" with no successful matching tool this turn → one nudge; if it stays, a visible "Not verified" note |
| Identity files are a persistence vector | Hermes (`_PROTECTED_INSTRUCTION_BASENAMES`: always ask, fail closed) | **adopt** | `soul_update` asks in smart mode and is on the Rule-of-Two list (refused when nobody can approve after outside content) |

### Long-running tasks and the terminal

| Pattern | Who | Verdict | What changed in Echo |
| --- | --- | --- | --- |
| Don't kill at the timeout: yield and keep running | Codex `exec_command` + `yield_time_ms`; Claude Code `run_in_background`; Hermes `terminal(background=true)` | **adapt** | `terminal.py`: one launcher for host and sandbox; still running at `timeout` → background process with `process_id` and output so far; `background=true` starts it that way |
| Interactive input | Codex `write_stdin` (PTY sessions) | **skip** (refuse instead) | Small models drive interactive sessions badly. stdin is closed, prompts disabled (`GIT_TERMINAL_PROMPT=0`, `GIT_EDITOR=true`, `PAGER=cat`, `PIP_NO_INPUT=1`); vim/less/REPLs/`npm init`/`git add -p`/`Read-Host` refused with the non-interactive form |
| Head + tail output | Codex `HeadTailBuffer`; Claude Code 30k cap | already there | `_clip` keeps head ⅓ + tail ⅔; now says how to see the middle |
| Failure hints from output | Hermes `terminal_hints.py` | **adopt** | `failure_hint`: not installed, PowerShell 5.1 `&&`, empty commit message, no internet |
| Dedicated file tools over shell for files | Claude Code; Codex (`apply_patch` for edits) | **adopt** (guidance + safety net) | Terminal description: programs in the terminal, files with file tools (scoped, undoable). Redirect writes (`>`, `tee`, `Set-Content`, `Out-File`) are checkpointed first so undo still covers them |
| Checkpoints before edits | Claude Code | already there | Fixed a collision: backups named `<ms>_<name>.bak` overwrote each other for same-named files saved in one millisecond (now with a random suffix) |

### Tools: selection and orchestration

| Pattern | Who | Verdict | What changed in Echo |
| --- | --- | --- | --- |
| Few, non-overlapping tools | Claude Code (~15), Codex (~6); Anthropic tool-writing guide | **adapt** | Audit in §7: Glados 37 → 26 tools (−20% schema), Echo 37 → 33 |
| Tool search / deferred loading | Claude Code (MCP tools deferred by default) | **skip for now** | Schemas are ~3–4k tokens after the trim, below the ~10k point where Anthropic recommends it. Revisit with MCP (see `tools-and-mcp.md` T1) |
| Names models were trained on | (observed: small models emit `bash`, `read_file`, `edit_file` with `old_string`) | **new** | `toolbox.TOOL_ALIASES` / `ARG_ALIASES`: run the matching tool instead of losing a step to "unknown tool"; only if the target is in that agent's toolbox |
| Plan / todo tool | Claude Code TodoWrite, Codex `update_plan`, Hermes `todo` | **adapt for groups, skip for one agent** | Groups get a task board (owners, status, evidence) shown to every agent. A single-agent plan tool costs a call per step on 4B–9B models; the goal reminder covers drift. Proposed with an eval (P5) |

### Context, memory and sub-agents

| Pattern | Who | Verdict | What changed in Echo |
| --- | --- | --- | --- |
| Memory injected at session start | Hermes (frozen MEMORY.md/USER.md snapshot), Claude Code (MEMORY.md first 200 lines) | already there | Recall is now keyed on the task and the user's request, with stopwords removed (§6) |
| Sub-agents: fresh context, focused brief, summary back | Claude Code subagents, Hermes `delegate_task` | already there + fixed | Teammates also get the chat summary; recall uses their task |
| System reminders during long work | Claude Code | **adapt** | Every 8 steps, the goal (and a "change approach" line when most recent calls failed) is appended to the latest tool result: no extra model call, no extra message (Gemma needs strict alternation) |
| Retrieval at the moment it's needed | Anthropic context engineering ("just in time") | **adapt** | A request that points back ("last time", "we discussed", "you told me") pulls matching messages from other chats into the prompt (FTS, any keyword, ≤5 lines) |
| Compaction: clear tool output first, then summarize | Claude Code | already there | `loop.py` `_fit_context` (2026-10-02) |

---

## 4. Item 1–2: Groups that finish the work

### Why they stopped (reported before changing anything)

- **Discussion mode** (what "continuous" groups were): `runtime.py` `_run_discussion` on `main`.
  - Ran `room.max_messages` turns; the dialog offered 4, 6 or 8 (`Dialogs.tsx:259`), the API clamped 2–12 (`rooms.py` `_clean_cap`).
  - Each turn: "Keep it under 120 words. If the group has reached a good answer, end your message with the word DONE", with `allow_handoff=False` and `allow_complete=False`.
  - When two agents had spoken and one said DONE, the lead wrote a conclusion and the job was marked **Done** (`job.finish`). There was no step in which anyone did the work: discuss → agree → stop, recorded as success.
- **Reply mode:** `settings.group_max_rounds` defaulted to 4 (`settings.py:86`). A round is usually an agent plus a teammate's message, so about 8 messages.
- **Completion:** decided by a model reading the transcript; if its JSON couldn't be parsed, the job counted as done (`runtime.py:368` on `main`).

### The loop now

`_run_discussion` (shown as **Work together** in the UI; rooms keep `mode: "discussion"`):

1. **Discuss:** up to 3 views, under 100 words each. Planning turns get only look-up tools (`Toolbox.restrict_to_read_only`) and no promise guard.
2. **Decide:** the lead calls `assign_tasks` with owned, checkable tasks, or answers a plain question directly. The tool shows each teammate's abilities ("Jarvis (Researcher; can: search the web, …; can't: read and write files, run terminal commands)").
3. **Execute:** each open task goes to its owner with the task board; `complete_task` closes it. Every tool call becomes evidence on the job, credited to the active task.
4. **Observe / Verify:** when the board is clear, the completion check reads the request, the board and the tool log. A task done with no successful tool call, when it needed work, is reopened.
5. **Continue:** what's missing becomes a new owned task for the agent the check names (a copied roster line still resolves).
6. **Stop:** verified done → Echo's wrap-up (what was done, what's open, "anything else?"); or a visible reason: turn budget (default 30; 15/30/60 in the dialog; old caps ≤12 map to 30), token budget (200k), repetition, or two rounds without progress.

**Progress** is measured in what the goal needs (`Job.note_progress`): for work, a new successful tool call or a newly finished task; for a question, a new answer that doesn't repeat an earlier one also counts. The decide step only sets the baseline. Two bugs found while finishing the WIP: the decide step counted as a stalled round (a team that only agreed was stopped before anyone was asked to act), and reply-mode questions were cut after two rounds because talk never counted.

Reply mode keeps its shape (`_close_job`), with the evidence-based check, `max_rounds` 8, and the same backstops.

### Tests

- 11+ agent messages, two failed fixes corrected mid-way, verified done, wrap-up asks "anything else?" (`test_group_runs_well_past_eight_turns_with_corrections_and_finishes_verified`).
- Agents that only agree are made to do the work (`test_agents_that_only_agree_are_made_to_do_the_work`).
- No-progress backstop stops early with a reason; a run that never verifies stops at its turn budget (15) with what's missing.
- A plain question gets answered without tasks; planning turns can't write files.
- Unit: unbacked tasks, progress ≠ new tasks, action detection.

---

## 5. Item 3: Soul and persistent state

### Write path, traced

| Step | Before | Now |
| --- | --- | --- |
| Tool exposure | none: no tool wrote SOUL.md or a persona's soul | `soul_update` (add / replace / remove) in the `memory` toolset |
| Target | — | the soul the prompt is actually built from: the persona's own text if set, else SOUL.md for Echo |
| Write | Settings only (`PUT /soul`, plain `write_text`) | atomic temp + fsync + rename (`soul.write_atomic`), also for `PUT /soul` |
| Read-back | — | through `EchoSpeakAgent._load_soul` (what the next chat reads); cache key now includes file size |
| Report | — | "Saved and verified" only on a matching read-back; otherwise "Failed: …" |
| Gate | — | approval card with the exact change; Rule of Two |

`memory_save` now re-reads `records.json` and reports "Saved and verified", "Already remembered" or a failure.
The SOUL.md path resolver was duplicated three times (`core.py`, twice in `server.py`); there is one now
(`agent/lean/soul.py`).

### Tests

Update the soul, start a brand-new session with a fresh agent, and the new instruction is in its system
prompt. A failed write (disk full) is reported as failed, the model's "I've updated my soul" is challenged,
the reply corrects itself, and SOUL.md is unchanged. A stale read-back fails. A teammate edits its own
soul in the persona store. A claim with no tool call is challenged, then flagged "Not verified", and the
false text is retracted on screen. `memory_save` on disk vs lost.

---

## 6. Item 4: Terminal

| Area | Before | Now |
| --- | --- | --- |
| Timeout | killed; output lost. Sandbox: the docker client was killed, the command kept running orphaned | still running at `timeout` → background process with id and output so far; one `docker exec` launches and waits (no extra round trip for quick commands) |
| Servers / watchers | blocked the full 120s | detected (`npm run dev`, `vite`, `uvicorn`, `--watch`, `tail -f`, `docker compose up` without `-d`, …) → background after 6s |
| Prompts | host commands inherited the backend's stdin and could hang | stdin closed; prompt-free env; interactive commands refused with the alternative |
| Output | stdout+stderr mixed | stdout, then `[stderr]`; exit code; one-line hint for common failures |
| Background processes | no exit code; `terminate()` left children | exit code reported; process group killed; at most 8 (past that a slow command is stopped and the running ones are listed); sandbox internet released when the last command that needed it ends |
| Shell file writes | no checkpoint | redirect targets checkpointed first |
| When to use it | "run a shell command" | programs (builds, tests, git, package managers, scripts); files through file tools |

Tests (`test_lean_terminal.py`, real bash): output/exit/stderr; missing-program hint; a slow command
moves to the background and finishes its work (`done.txt` written); a server returns in <3s and can be
stopped; `read` gets EOF instead of hanging; `git commit` without `-m` fails fast with a hint; redirect
overwrite undone via checkpoints; background cap; the sandbox launch script run under local bash
(output, exit code, stderr, still-running case); classification tables.

---

## 7. Item 5: Tool audit

Measured with file writes enabled (the desktop default), schemas as sent to the model:

| Agent | Before (main) | After |
| --- | --- | --- |
| Echo | 37 tools, 15,860 chars | 33 tools, 15,747 chars (adds `soul_update`, longer terminal guidance) |
| Jarvis | 14 tools, 6,477 chars | 15 tools, 7,453 chars (adds `soul_update`) |
| Glados | 37 tools, 15,863 chars | 26 tools, 12,665 chars (−20%) |

| Tool | Action | Reason |
| --- | --- | --- |
| `get_system_time` | **remove** from default | The prompt carries date, time and zone, rebuilt every turn |
| `file_mkdir` | **remove** from default | `file_write` creates parent folders; the terminal covers empty folders |
| `artifact_write` | **remove** from default | Third "write" tool overlapping `file_write` and `create_artifact` |
| `project_update_context` | **move** to `self` | EchoSpeak's own changelog: for Discord guests (they keep it) and the opt-in self set |
| `process_start` | **combine** into `terminal(background=true)` | Same as Claude Code's `run_in_background`; terminal already backgrounds servers; old name aliased |
| `research` set | **simplify**: split into `web` + `live` | Glados needs search and fetch, not shopping, stocks or video cards; `research` stays as an alias; persona store v3 migrates once |
| `terminal` | **improve** | §6 |
| `memory_save` | **improve** | Read-back from disk |
| `soul_update` | **add** | §5: the missing write path |
| `assign_tasks` | **add** (groups only) | Decisions become owned tasks |
| `delegate_to_agent`, `assign_tasks` description, completion check | **improve** | Show each teammate's abilities |
| other harnesses' names | **improve** selection/recovery | `bash`, `read_file`, `edit_file` + `old_string`, `grep`, `glob`, `web_fetch`, … run the matching tool |
| `file_list` / `file_find` | keep | Descriptions already separate them; merging changes behaviour the eval relies on |
| `file_edit` / `file_write` | keep | Same split as Claude Code (Edit / Write); `file_edit` returns a diff, `file_write` the body, so no re-read is needed to verify |
| `calculate` | keep | Small models get arithmetic wrong; Claude Code doesn't need it, Gemma does |
| `system_info` | keep | Cheap; answers hardware questions without a terminal |
| `memory_search` / `chat_search` | keep | Different stores; descriptions say which |
| plan/todo tool | skip (P5) | §3 |
| tool search | skip (P6) | §3 |

Tests (`test_lean_tools.py`): default sets drop the redundant tools; aliases map names and arguments,
never overwrite real arguments, apply only when the target exists; an end-to-end loop run where the
model calls `edit_file` with `old_string`/`new_string` and `file_edit` runs; `terminal(background=true)`.

---

## 8. Item 6: Memory, recall and the harness

### Map

| Context | Source | Reaches | Before | Now |
| --- | --- | --- | --- | --- |
| Soul | SOUL.md / persona soul | every turn's system prompt | read-only | writable, verified (§5) |
| Saved memories | `records.json` via `runtime_memory_projection` | system prompt, top 8 | keyed on the turn's message (often a "[System]" brief); stopwords counted | keyed on the agent's task + the user's request; stopwords dropped |
| Chat summary | `summaries.py` | system prompt | depth 0 only | teammates too |
| Recent history | state store timeline | messages | unchanged | unchanged |
| Past chats | SQLite FTS (`search_messages`) | `chat_search` tool only | only if the agent thought to search | also automatic when the request points back |
| Task board + tool log | `Job` | work-together briefs, completion check | new in this branch | shared state for every agent on the job |
| "Am I on track?" | — | — | nothing in single-agent turns | goal reminder every 8 steps, with failure count |
| Conversation memory | `_remember_async` | session-scoped records | unchanged | unchanged |

### Tests (`test_lean_recall.py`)

A delegated teammate's recall query starts with its task and includes the user's request (no "[System]",
no "handed you this task"), and its prompt has the chat summary. "What is my dog's name?" recalls the dog
memory only. A back-reference pulls a past chat's message into the prompt; a plain question doesn't. The
8th step of a failing run carries the reminder with "8 of the last 8 tool calls failed".

---

## 9. Item 7: Chat auto-follow

**Reproduced in Chromium** (Playwright, a harness page with the same pinning as `index.tsx`: pin on DOM
mutation and resize, and every 80ms while streaming; two agents streaming plus bursts of messages):

| Check | Old logic | New |
| --- | --- | --- |
| At the bottom: follows a token stream | pass | pass |
| At the bottom: follows a burst of 30 group messages | **fail** (67px behind) | pass |
| Wheel up mid-stream stops following | **fail** (yanked back to the bottom) | pass |
| Reading position kept through the stream and a burst | **fail** | pass |
| Scrolling back down resumes | pass | pass |
| …and keeps following | pass | pass |
| A 15px drag up (inside the 48px slack) stops following | **fail** | pass |

Causes and fix are in §1 and in `apps/web/src/app/chatFollow.ts`. The tool-activity card in
`chatComponents.tsx` no longer scrolls the chat. Unit tests (`chatFollow.test.ts`) cover streaming, rapid
and group messages at the bottom; wheel/drag/keys/touch up; shrinking content; resuming; sending a message.

---

## 10. Live-model eval

No model server is available where this was built, so everything above was tested with scripted model
turns, real bash, and real Chromium. `apps/backend/scripts/eval_gemma.py` gained cases for a live check on
your machine:

- 19 now checks a work-together question ends Done without busywork.
- 31: work together builds `count.py`, runs it, assigns tasks, ends Done.
- 32–33: `soul_update` succeeds, and a new chat follows it (the runner snapshots SOUL.md first and restores
  it afterwards).
- 34–35: `python -m http.server` returns in under 100s (was ≥120s) and can be stopped.

Run: `python scripts/eval_gemma.py --only 19,31-35`.

---

## 11. Proposed, not built

| # | Proposal | Why not now |
| --- | --- | --- |
| P1 | Stream terminal output into the tool card while a command runs | Needs a progress channel from tools to the UI (toolbox has none); commands now return within their timeout anyway |
| P2 | Persist the job (task board, evidence) so a restart resumes a group run | Runs are in-process; same gap as pending approvals (2026-10-02 P7) |
| P3 | Re-plan once on stall before stopping (Magentic-One outer loop) | One extra model call per stall on a local model; measure with eval 31 first |
| P4 | A "jump to latest" button when following is off and new messages arrive | UI addition beyond the fix; the follower already exposes the state |
| P5 | Single-agent plan/todo tool (TodoWrite / `update_plan`) | Costs a call per step on 4B–9B models; try behind a flag with the eval |
| P6 | Tool search / deferred schemas | Below the size where it pays; needed once MCP servers add many tools |
| P7 | Codex-style `write_stdin` for interactive programs | Small models drive them poorly; refusing with the non-interactive form is more reliable |
