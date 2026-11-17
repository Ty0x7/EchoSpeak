# Owner acceptance: one agent, sessions and the conversation-audit fixes (17 November 2026)

Branch `agent-first-sessions`. Technical record: [agent-first.md](../research/agent-first.md); the failures
these address: [conversation-audit-2026-11-16.md](../research/conversation-audit-2026-11-16.md).

## Before you start

The branch is already installed for testing (17 November):

- **Terminal:** `echospeak` runs the branch (`~/.local/bin/echospeak` → this worktree's launcher).
- **Desktop:** the Apps-menu EchoSpeak entry opens the AppImage built from the branch.
- **Backend:** the shared backend was restarted from the branch. Both clients use it.
- **Your data is untouched:** the same conversations, memories and settings, read by the new code. Nothing was
  migrated.
- **To go back:** run
  `~/Work/echospeak-model-testing-2026/evaluations/2026-11-17-agent-first-campaign/rollback-owner-testing.sh`.
  It restores the previous `echospeak` link and AppImage, both kept as `*.before-agent-first`.

Reopen any terminal client that was already open. Use qwen3.5:9b unless a step says otherwise.

## Desktop

| # | Do | Correct | Wrong |
|---|---|---|---|
| W1 | Open EchoSpeak from the Apps menu | The sidebar has **New conversation**, the pages, **Workbench**, the Agents list, and one **Conversations** list with filters All / Echo / Agents / This project. No Chat / Agents / Code switch | The old three-way switch |
| W2 | Look at the rows | Each says who answers (Echo, Jarvis, a group) and the project; waiting ones say "Needs your answer" or "Needs approval" | Rows without who/project |
| W3 | Open a conversation | The header reads e.g. "Echo · qwen3.5:9b · Work · Ask for risky actions" | "Chat / title" |
| W4 | Open a Jarvis conversation | The header starts with Jarvis | It still says Echo |
| W5 | Attach a folder (composer folder button or drop) | "Let Echo work in “folder”?" — OK trusts it; Cancel attaches it read-only | It attaches without asking |
| W6 | Start a conversation in the terminal, then look at the desktop | It appears in the list within about 15 seconds | It never appears |
| W7 | Send a message in the desktop, then `echospeak` → ← → that conversation | The same messages in both | Different histories |

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

## Agents, restarts and folders

| # | Do | Correct | Wrong |
|---|---|---|---|
| G1 | Glados (`/agent Glados`): "ask Jarvis to find the latest stable Python release" | Glados hands it to Jarvis (a delegation step), Jarvis answers with a source | "I can't delegate", or Glados researches it herself |
| G2 | A group with Jarvis and Glados: "each of you, say who you are" | Two short answers, each in its own voice | A third "merged" reply |
| G3 | With the terminal open, run `echospeak backend stop` in another terminal, then `echospeak backend start`, then send a message in the first | "the backend restarted; reconnected" (send it again if asked), then a normal answer | "cannot reach the backend" until you restart the terminal |
| G4 | Without a project: "which folder can you work in?" | Your Documents folder | The EchoSpeak source folder |

Write down what you see, especially anything in the *Wrong* column. Those observations choose the next fixes.
