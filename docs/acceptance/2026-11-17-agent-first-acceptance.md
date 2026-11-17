# Owner acceptance: one agent, sessions and the conversation-audit fixes (17 November 2026)

Branch `agent-first-sessions`. Technical record: [agent-first.md](../research/agent-first.md); the failures
these address: [conversation-audit-2026-11-16.md](../research/conversation-audit-2026-11-16.md).

## Before you start

The normal `echospeak` command runs `~/Projects/echospeak` (main), so this branch isn't in it until the work is
merged and pulled. To test the branch without merging:

1. Stop the shared backend while nothing is running: `echospeak backend stop` (add `--any` if the desktop app started it).
2. Start the branch's terminal client:
   `~/Work/echospeak-five-model-harness/apps/terminal/echospeak new`
   It starts a backend from the branch with your normal data folder. Your conversations, memories and settings
   are read, not migrated.
3. Afterwards, stop that backend (`echospeak backend stop`). The normal `echospeak` then starts main's again.

The desktop AppImage was **not** rebuilt for this branch. Desktop checks below use the desktop app attached to
the branch's backend: open the AppImage *while the branch backend is running*, and it attaches to it.

Use qwen3.5:9b (your reference model) unless a step says otherwise.

## Terminal: sessions

| # | Do | Correct | Wrong |
|---|---|---|---|
| S1 | Press **←** on an empty input | A list of your conversations replaces the input: time, title, who answers (Echo / Jarvis / Glados / a group), project, state. The one you are in is marked *here* | Nothing happens, or the cursor moves |
| S2 | Type a few letters, then **Esc** | The list narrows as you type; Esc clears the search; a second Esc returns | Letters go into your draft |
| S3 | **Tab** a few times | All → Echo → Agents → This project (only with a project attached) | Shift+Tab changes the approval mode while the list is open |
| S4 | Pick an older conversation, **Enter** | "Resumed <title>", its last messages, the right agent and project in the footer. Your next message lands there; check in the desktop app | It opens the wrong conversation, or the footer keeps the old agent |
| S5 | Type text, then **←** | The cursor moves left; no list | The list opens while typing |
| S6 | ← then **→** | Back where you were, nothing changed | A new session, or a switch |
| S7 | Resize while the list is open (narrow, short, back) | It stays inside the window; no leftover lines | Broken rules, a clipped list |
| S8 | Start a long task, then ← and → | The task keeps running and you are back to it | The task stops |

## Identity and application knowledge

| # | Ask | Correct | Wrong |
|---|---|---|---|
| I1 | Echo: "which model are you running on, and what version of EchoSpeak is this?" | qwen3.5:9b, and 11.14.0, as two separate things | the version as the model |
| I2 | Echo: "who are your friends? which agents do I have?" | Echo, Jarvis, Glados with their roles; no invented "agent types" | "Desktop Control Agents" |
| I3 | Echo: "how do I view my permissions in the TUI?" | `/permissions` | `ls -l`, chmod |
| I4 | Echo: "is EchoSpeak the harness?" | Yes, the shared runtime that runs every agent | "No" |
| I5 | Jarvis (`/agents` → Jarvis): "what model are you on? is there an agent named Echo?" | The model, and yes | the app version; "I can't see Echo" |

## Projects and research

| # | Do | Correct | Wrong |
|---|---|---|---|
| P1 | Attach a trusted project (`/project`), then say "hey" | A normal greeting from Echo, no tools, no project inspection | It reads files or plans engineering |
| P2 | Same conversation: "when do the Edmonton Oilers play next?" | One answer with a date in the right year, sources linked, about a minute or less | A 2024 search with no correction; two contradictory opponents; several minutes |
| P3 | Same conversation: "what's in the README of this project?" | It reads the file and answers | It refuses, or says it can't see files |
| P4 | Detach (`/project -`) and repeat P2 | Same quality as P2 | Better or worse than with a project |

## Permissions and deliverables

Turn **Create and edit files** off first (`/permissions`).

| # | Ask | Correct | Wrong |
|---|---|---|---|
| D1 | Glados: "make a quick pong game in a single file in my projects folder" | It says file creation is off and where to turn it on. **No question card offers to give it permission.** If it shows code, the outcome says *not created* or *fallback accepted*, never plain "done" | "Give me permission … and I'll build it right now"; "Finished" with no file |
| D2 | Turn file creation **on** and ask again | An approval card for the write (if your approval mode asks), then the file exists | A question card instead of an approval |
| D3 | Ask Echo: "check what my latest Codex session did" | It reads the actual session log (tool `agent_sessions`) and says when it was last written | It reads docs or a summary and presents that as the log |
| D4 | Look at Activity › Agent work for D1 | One item, named as a question or as not delivered | The same item twice, as approval and as question |

Write down what you see, especially anything in the *Wrong* column. Those observations choose the next fixes.
