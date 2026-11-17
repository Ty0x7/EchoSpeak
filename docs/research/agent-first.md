# One agent, several capabilities: the agent-first architecture and the terminal session manager

> 16–17 November 2026, branch `agent-first-sessions` (from `aefc279`, version 11.14.0). This follows the owner's
> conversation audit ([conversation-audit-2026-11-16.md](conversation-audit-2026-11-16.md)) and
> [interfaces.md](interfaces.md). Visual summary: [agent-first.html](../../artifacts/research/agent-first.html).

## 1. What was there (research)

All three "modes" already ran in **one** harness: `agent/lean/runtime.py` (`LeanSession`), one loop
(`loop.py`), one toolbox, one approval broker, one memory store, one journal. What differed was decided per turn
by `agent/environments.py`:

| Who answers | When (unified mode, the default) | Prompt builder | Tools | Safeguards on the answer |
|---|---|---|---|---|
| Echo, "conversation" profile | the owner's conversation, no trusted project | `chat_mode.build_prompt` | Chat allowlist, enforced twice (`chat_mode.restrict`, `call_problem`) | Chat run: evidence ledger, citation review, 6–10 step budget, final answer from findings |
| Echo, "engineering" profile | the same conversation with a **trusted** project attached | `prompt.build_system_prompt` + `code_mode` | core, web, terminal, vision, memory, skills, minus live data, reminders, artifacts | verification ledger for checks; **no** research safeguards; 40-step budget |
| a named agent (Jarvis, Glados …) | its own room, routines, channels | `prompt.build_system_prompt` + its soul | its configured toolsets | verification ledger; team section with room members only |

The audit's failures came from these divisions, not from missing instructions:

1. **Facts about the app were profile-local.** The roster and the app description were only in the conversation
   profile; the model name only where an identity sentence happened to include it. Each profile guessed the rest.
2. **Attaching a project swapped the assistant's profile instead of adding capabilities.** The research
   safeguards were wired to the conversation profile, so they disappeared with the swap, along with
   `sports_live`, reminders and artifacts.
3. **Three different things shared one surface.** A disabled capability (a switch), an approval (a card the
   broker shows when a tool needs one) and a clarification (a question card) were not distinguished, so a
   question could offer a permission.
4. **Completion was the run ending.** Nothing compared what was asked to be created with what tools created.

How Codex CLI and Claude Code organise the same things, used as reference:

- **Codex CLI 0.169**, captured in a private tmux on this machine (`codex resume --all`). It has a full-screen
  resume picker:
  - sessions ordered by last update, with a relative time and the first request as the title;
  - filters chosen with the keyboard (Cwd/All, Active/Archived, Updated/Created);
  - type to search, ↑/↓ to browse, Enter to resume, Esc for a new session;
  - a position counter (`1 / 5`).
  
  A session there is one agent and one working directory.
- **Claude Code** has `--resume` / `/resume` (a searchable list of past conversations) and `--continue`.
  Project context comes from the working directory and CLAUDE.md, and the model and permission mode are shown
  in the UI. This is from documentation and use, not captured here.

Neither has named persistent agents, so neither has to show *who* a conversation belongs to. EchoSpeak does.

## 2. Decisions: which boundaries stay

| Boundary | Kept? | Why |
|---|---|---|
| Named agents (identity, soul, model, toolsets, rooms) | **Kept** | Real user configuration; each has its own conversations and responsibilities |
| Permission switches, toolsets, policy, approvals, Private/Offline | **Kept, untouched** | Security boundary; enforced in trusted code, independent of prompts |
| Trusted project ⇒ file and terminal tools | **Kept** | Engineering tools still need a trusted project, so they never appear silently |
| Trusted project ⇒ a *different* assistant (tools removed, research safeguards gone) | **Removed** | A project now adds engineering to Echo and takes nothing away |
| App facts per profile | **Removed** | One section, `agent/lean/app_facts.py`, for every profile |
| Research safeguards per profile | **Removed** | The same `ChatRun` runs beside the engineering ledger |
| Chat/Agents/Code labels in the desktop sidebar | **Kept for now** | A GUI redesign was out of scope. The backend now offers one session description both clients can use (§4) |
| Legacy `assistant_mode = environments` | **Kept** | Explicit owner choice; behaviour unchanged (tested) |

The result is one execution path:

```
request ─► LeanSession ─► who answers?  Echo (owner's conversation) │ named agent (its room) │ guest
                          │
                          ├─ facts      app_facts.section(name, model)           — same for all
                          ├─ tools      persona toolsets ∩ switches ∩ policy      — trusted code
                          │             + file/terminal while a trusted project is attached
                          ├─ safeguards ChatRun (evidence, citations, research budget, stale-date note)
                          │             + VerificationLedger when engineering tools exist
                          └─ outcome    run status + deliverable.assess(objective, tools that ran)
```

## 3. What changed (backend)

| Area | Files | Behaviour |
|---|---|---|
| Shared app facts | `agent/lean/app_facts.py`; `prompt.identity_section`; `chat_mode.app_facts` | Every profile is told the same things: its own name, the model writing its replies (never the app version), EchoSpeak = the harness, the whole roster, the real terminal commands and desktop places, and how the app is organised |
| Project adds, never swaps | `code_mode.IDENTITY_UNIFIED`, `rules()`, `toolsets()`, `focus()`, `restrict()`; `personas.code_persona` | Echo with a project keeps live data, reminders, artifacts and media. The workflow applies "when the request is work in the project". Legacy mode is unchanged |
| Research safeguards beside a project | `runtime._build_turn` (`research_run`); `chat_run.ChatRun.step_limit`, `advise`, `stale_years`; `loop.LeanTurn.step_limit`, `result_note` | Evidence ledger, citation review and URL prefetch with or without a project. The research budget holds until the first file or terminal call, and a turn that hits it still answers. A search for a past year nobody asked about gets today's date |
| Repeats and narration | `loop.LeanTurn._sig`, `_mark_interim`, `_answer_text` | A page is one call whatever its "objective". Short narration beside tool calls stays in the timeline but not in the saved answer, unless it is long or holds code |
| Questions are not permissions | `ask.asks_for_permission`, `permission_refusal`, `answer_caveat`; `capabilities.off_summary`; `loop._ask_user` | A question offering a grant is not shown; the model learns what is off and where to turn it on |
| One announcement per card | `attention._approvals`, `_work` | "A question is waiting for your answer" once, not also "an action is waiting for approval" |
| Deliverables | `agent/lean/deliverable.py`; `runtime._run_execution`, `_run_locked` | Delivered / fallback accepted / not delivered, from the tools that ran; on the work record, the execution and under the answer |
| Code in replies | `agent/lean/code_check.py`; `loop` (after answer review) | Syntax check (Python, JSON, JS/HTML via `node --check`, nothing executed); "Not run" on an unexecuted program described as working |
| Real session logs | `agent/lean/session_logs.py` (`agent_sessions`) | Lists and reads Claude Code and Codex logs. Read-only and outside content; credential-shaped strings redacted; Echo profiles only, never guests |
| Session overview | `agent/session_overview.py`; `GET /sessions/overview`, `GET /app/facts` | One description of every conversation: who answers, project, status (running / needs your answer / needs approval / failed), last request and reply, model |

## 4. The terminal session manager

**Open it** with **←** on an empty input, or with `/sessions` or `/resume`. With text in the input, ← is the
ordinary cursor key, so editing is never interrupted. The footer says `← sessions` and `?` lists the key.

**Inside it:**

- **Browse:** ↑↓, PgUp/PgDn, Home/End.
- **Filter:** Tab and Shift+Tab step through All, Echo, Agents and This project. Shift+Tab doesn't change the
  approval mode while the list is open.
- **Search:** typing searches titles, agents (including group members), projects and the last exchange.
  Matches on what a conversation is called come first.
- **Act:** Enter resumes the conversation, → or Esc goes back (Esc clears a search first), and Ctrl+N starts
  a new one.

**What a row shows,** at any width:

- when it was last active;
- the title;
- who answers there (Echo, the agent, or the group);
- the project;
- its state (running, needs your answer, needs approval, last run failed, or here).

The selected conversation's last request and reply appear under the list, with the model that wrote it.

**Resuming restores:**

- the conversation and who answers in it (a direct room sets the agent);
- the attached project;
- the model;
- the last few messages as a recap;
- a running task, which is followed;
- waiting approvals and questions, which are shown.

What belonged to the conversation you left is reset: its token count, context meter, file changes and queued
follow-ups. If an approval arrives, it takes the manager's place.

**Code:**

- `echospeak_cli/tui/sessions.py`: model and drawing, no I/O.
- `echospeak_cli/tui/app.py`: `open_sessions`, `_browser_key`, `_resume_row`, `_switch`.
- Layout: the manager takes priority 0 in the height plan, so it always fits (`layout.allocate`).
- An older backend without `/sessions/overview` gets the plain thread list.

The desktop and terminal use the same backend, conversations and journal, so a conversation resumed in the
terminal is the same record the desktop shows. The desktop sidebar does not yet read `/sessions/overview`
(§7).

## 5. Verification (what was actually run)

**Automated:**

- **Backend:** `tests/test_agent_first.py` has 40 cases built from the audit. Related suites on the final code (chat, environments, capabilities, unified, ask/recover, harness, runtime,
  self-knowledge, soul, groups, tools, terminal, model compatibility, check-ins): 329 passed, 2 skipped, 3 failed.
  The 3 are check-in tests that fail identically on `aefc279`: they expect "Claude Code is signed out" notices,
  which unified mode no longer gives. The approval-preview test passes in that run and fails in the full suite, so
  that failure depends on test order (it also failed on `aefc279`).
  - The whole backend suite: 1,777 passed, 48 failed, 51 skipped.
  - Those **48 fail identically on the base commit `aefc279`** (the same tests and the same errors, compared in a
    clean copy).
  - Most are external-runtime tests (`test_code_sessions`, `test_code_folder_sessions`) that unified mode
    deliberately refuses ("EchoSpeak no longer starts Codex, Claude Code or ACP agents"). Others are check-in and
    approval-preview tests already failing before this work. They are not counted as passing.
- **Terminal:** 4,201 passed, 1 skipped, with the terminal's own virtualenv.
  - `tests/test_sessions.py` has 121 cases: the model, every width × height, and the real key bindings with a
    recording client.
  - `test_layout_matrix.py` now includes six session-manager states at 231 sizes each.
  - With the backend's virtualenv the TUI tests silently skip (no prompt_toolkit). Use `apps/terminal/.venv`.
  - One stale integration assertion was corrected: it expected the model name as the speaker in unified mode.
- **Session lab** (`apps/terminal/tests/sessionlab.py`): the real TUI in a private tmux, a real backend and a
  scripted model. All 14 checks passed:
  - the overview lists Echo, project and Jarvis conversations correctly;
  - ← while typing moves the cursor;
  - ← on an empty input opens the manager, and search, the Agents filter and Enter resume the chosen conversation
    with its recap and project;
  - **a message sent afterwards is in that conversation's history and no other's** (read from the backend);
  - → returns;
  - it fits at 40×16.

  The first runs found two problems:
  - a lab script error;
  - search ranking: a reply mentioning "iso_week" outranked the conversation titled "Week 53". Title matches
    now rank first.

**Live model** (qwen3.5:9b): §6.

## 6. Live results

**How it was run:**

- **Script:** `agent-first-live.py` in the local evaluation folder
  `evaluations/2026-11-16-agent-first/`. It uses `harness_eval.Stack`: an isolated backend, data folder, home
  and model-call recorder; never the owner's.
- **Model:** qwen3.5:9b (Q4_K_M, Ollama renderer/parser qwen3.5), served context 16,384.
- **Settings:** unified mode, learning off, **file writing and terminal off** as in the recordings. Question cards
  were answered with their first option, as the owner did.
- **Runs:** one per case, on 17 November 2026. The baseline is the same script against a clean copy of `aefc279`.

| Case (from the recording) | Before (`aefc279`) | After (run 1) | After-2 (two fixes from run 1) |
|---|---|---|---|
| Which model, which version | pass; 2 needless release fetches, 11 s | pass; same 2 fetches, 21 s | pass; no tools, 7 s |
| Which agents | pass | pass | pass |
| Permissions in the TUI | answer vague; 6 steps, 56 s | `/permissions`; 1 step, 7 s | `/permissions`; 6 file reads first, 41 s |
| Is EchoSpeak the harness | **fail**: "not entirely sure" | pass | pass |
| "hey" with a trusted project | pass | pass, no tools | pass, no tools |
| Oilers with the project | 7 steps, 178 s, "lost to Vancouver today, next vs Calgary on *Wednesday* Nov 19" | 1 `sports_live`, 11 s, Florida tonight 6 PM CST, Tampa Bay Thu, Carolina Sat | 1 `sports_live`, 8 s, same |
| hello.txt while writing is off | pass | pass; outcome "not created" | pass; outcome "not created" |
| Jarvis: model? Echo? | **fail**: "EchoSpeak version 11.14.0"; can't confirm Echo | pass | pass (addressed the owner as "Jarvis": wording fixed after) |
| Glados: Pong in /projects | **fail**: artifact, 157 s, work "completed" | **fail**: artifact, 80 s, counted as delivered | pass: code in the reply, no tool wrote anything, outcome "not created" |
| **Cases passed** | **6 / 9** | **8 / 9** | **9 / 9** |

**Checking the evidence:**

- **The Oilers answers were checked against ESPN's schedule,** fetched separately on 17 November. The Oilers are
  at Florida Tue Nov 17 at 7:00 PM ET (6:00 PM CST), at Tampa Bay Thu Nov 19 and at Carolina Sat Nov 21. Both
  "after" answers match; the baseline's opponents aren't on the schedule and its weekday was wrong.
- **What changed between run 1 and run 2:**
  - *Version question:* the self-update prefetch no longer fires on a bare "version".
  - *Deliverable check:* an artifact made in place of a file asked for "in /projects" is now "not where asked"
    instead of delivered (`test_what_became_of_the_requested_file_…`).
- **Glados in after-2** wrote "I'll package it as an interactive web app artifact" and then only pasted code.
  The deliverable note said "Not created", from the tools that ran: the reply was contradicted by the evidence,
  as intended. The unkept promise itself is model behaviour the promise guard didn't catch, because code followed
  it.
- **After-3,** on the final code (with the roster wording "this is you, the agent writing this reply"), passed
  **9 / 9**:
  - Jarvis answered "Jarvis (Researcher) — that's me" and named qwen3.5:9b;
  - `/permissions` took 5 s;
  - the Oilers took one `sports_live` call and 10 s, with the same schedule as ESPN;
  - Pong: no permission question, code pasted, nothing written, outcome "not created".

  After that run, the code check's wording match was widened: "Here's a complete, playable Pong game" was not
  labelled "Not run". Applied offline to the recorded after-3 reply, the label now appears, and `node --check`
  parsed its script. That is a re-check of a recorded reply, not another live run.
- **The checks are strict on the audit's failure modes and lenient elsewhere.** The `/permissions` case passes
  once the command is named, whatever the time. Times are reported next to each result so a slow pass is visible.

## 7. Limits and what is still open

- **Desktop:** the sidebar still groups by Chat/Agents/Code and doesn't read `/sessions/overview` or show which
  profile answered. That is the next step for UI consistency, together with the desktop trust question on attach
  ([interfaces.md](interfaces.md) table, items 1–3).
- **At 40 columns and below,** session rows show only the title (and the highlight). The agent, project and state
  columns need 60+.
- **Search** is substring, not fuzzy.
- **The deliverable check** recognises requests by wording (a file name, "a single file", a folder, "save it").
  An unusual phrasing is not tracked, in which case the old behaviour applies. It does not judge whether a
  created file is *correct*; the verification ledger and the code check cover part of that.
- **The code check** parses; it does not run. It can't tell that Pong's scores never change. The label says so.
- **The roster is listed whole,** at most 12 agents. Many more agents would need a summary.
- **Without a project, the backend's file root is the EchoSpeak source checkout** (open item 9 in
  [interfaces.md](interfaces.md)). In after-3, Glados said "I can only work in …/echospeak-five-model-harness".
  Not changed here.
- **The roster lists names and roles, not what each agent can do right now.** In run 1, Echo suggested Glados
  "might have file creation permission"; file writing is a global switch.
- **A promise followed by code isn't caught** ("I'll package it as an artifact", then only code). The deliverable
  note contradicts it.
- **Runtime completion still does not establish answer correctness** in general (audit finding 31).
- **Not tested:**
  - paid cloud models;
  - LM Studio;
  - other local models (only qwen3.5:9b);
  - the installed AppImage (not rebuilt for this branch);
  - real ghostty/alacritty drag-resizing (tmux only).

## 8. Owner acceptance

See §6 of the HTML or the list in the session summary; the same list is in
`docs/acceptance/2026-11-17-agent-first-acceptance.md`.
