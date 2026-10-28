# Chat, Agents and Code: three environments in one app

EchoSpeak is now split into three environments that share one sidebar and one runtime. [Visual report](../../artifacts/research/three-environments.html).

- **Chat** is model-first: you talk to the model you chose.
- **Agents** is where you message your persistent agents.
- **Code** is project-focused development.

Evidence labels:

- **Source**: read in the code.
- **Test**: a deterministic test in the repository.
- **Preview**: checked in a browser preview with a throwaway data folder.
- **Owner**: still needs the owner's live-model and installed-app check.

## 1. What was there

The source showed four problems. **Source.**

- **One sidebar mixed everything.** Agents, chats and a Code toggle, plus six pages, all sat in one sidebar. Code had its own page with a project dropdown and no sidebar organization.
- **Every ordinary chat was a conversation with Echo.** A web chat outside a room ran Echo's persona. That meant SOUL.md, teammates, delegation tools, the agent-work ledger tools, research workers and `soul_update`.
- **Echo had no conversation of its own.** "Echo's chat" was whichever plain chat was most recent. Other agents each had a direct room (`agent/lean/rooms.py`).
- **Agent-started conversations looked like owner chats.** Routine results, reminders and check-ins were created as `source="web"` threads.

## 2. The decision: one rule, no new harness

`agent/environments.py` classifies every conversation from fields every thread already has. **Test** (`tests/test_environments.py`).

| Environment | Rule | Who answers |
|---|---|---|
| Code | `thread.surface == "code"` | Echo native in Code, or an external runtime in its own harness (unchanged) |
| Agents | a room (direct or group), or a thread an agent or channel started (`source` not web/desktop/voice/chat) | the room's agents, with their own soul, memory, tools and work history |
| Chat | everything else the owner started | the selected model, model-first |

A thread with no record keeps Echo, as before.

### Why derive the environment instead of storing it

- **Nothing is migrated.** Old conversations keep their records and land where they belong. Old web chats were with Echo; they now show in Chat and continue model-first. Their earlier Echo replies stay labelled Echo.
- **It can't drift.** The runtime and the session list read the same function. `GET /threads` returns `environment`, and the web app trusts it (`app/environments.ts:environmentOf`).

### Why reuse the Lean harness for Chat

Chat needs everything the harness already does: streaming, tools, approvals, receipts, memory, files and skills. It only needs *less identity and orchestration*. So `LeanSession.model_first` (`agent/lean/runtime.py`) swaps the persona and removes features. It doesn't add a second loop.

- **The persona** is `chat_persona()` (`agent/lean/personas.py`):
  - id `chat`, named after the model (for example `qwen3-8b`), with a plain-assistant soul that says it isn't Echo or an agent
  - not in the roster, and nothing can delegate to it
  - `get("chat")` resolves it so old messages still have a name
- **Removed in Chat** (**Test**):
  - teammates and `delegate_to_agent`
  - the agent-work tools (`work_status`, `agent_notes`, `propose_agent_change`)
  - research workers (`spawn_workers`)
  - `soul_update`
- **Kept, as for Echo:**
  - account-level memory, past-chat search and memory saves (`_owner_side`)
  - web, files, terminal, vision, skills and reminders
  - approvals
  - the R4 rule that the first state-changing tool call creates a durable work record. That record is the safety spine for receipts and approvals, not orchestration.
- **When a turn is model-first:** only when the owner is talking live (source web/desktop/voice/chat) and isn't resuming agent work (`environments.model_first`). Routines, check-ins and channels keep their agent in any conversation.

### Chat shows the model, not Echo

- Each reply records its `provider`. It's added to the `agent_start` event in `agent/lean/loop.py` and to the persisted assistant item, then projected in `agent/state.py`.
- The UI draws the maker's mark: OpenAI, Claude, or a chip for local models (`components/ModelMark.tsx`). It shows the model's name (`modelDisplayName`, for example `GPT-5.5` or `Claude Sonnet 5.5`).
- The composer says the model is working, not "Echo". **Test.**

### Agents: persistent teammates as contacts

- **Every agent has one conversation, Echo included.** `RoomStore.direct(agent_id)` gets or creates it, and the sidebar and Agents home open it.
- **Tiers:**
  - **Tier 1:** Echo, the coordinator, listed first.
  - **Tier 2:** Jarvis, GLaDOS and the owner's specialists, most recently messaged first (`lean/AgentsHome.tsx:contactOrder`).
  - **Tier 3:** temporary workers. They're unchanged and appear inside the turn.
- **Agents message you there.**
  - Check-ins (`agent/checkins.py:session`) and the friendly heartbeat now post into Echo's own conversation. Before, they went to a "Check-ins with Echo" chat, which stays as history.
  - Routine and reminder chats are created as `source="agent"`, so they appear under Agents › Conversations.
- **Unchanged:** coordination, delegation, routines, approvals, learning and the security architecture (11.10.7). They key on agent and work ids, not on the sidebar.

### Code: projects and sessions

- **`GET /echo-code/overview`** returns each usable project with its Echo sessions and its external runtimes' sessions. It also returns "unorganized" sessions whose project was removed, archived or detached.
- **`POST /echo-code/sessions/{id}/project`** moves an Echo session into another trusted project, re-scoping its next turn to that folder. It refuses mid-turn, refuses an untrusted target and refuses a non-code thread. **Test.**
- **External sessions never move.** Codex, Claude Code and ACP sessions own a worktree inside their project. They keep their harness, sign-in, tools and boundaries, and open in the existing workspace (`ExternalSession.initialSessionId`).
- **The sidebar tree** (`code/CodeTree.tsx`):
  - drag and drop, with a keyboard "Move to…" alternative
  - folding remembered per project
  - a "+" on each project for a new session

### One sidebar, three environments

- `components/ProjectSidebar.tsx` keeps its width, position, tokens and rows, and adds a Chat · Agents · Code switcher. The switcher is labelled segments when the sidebar is open and icons in the collapsed rail.
- Each environment shows its own content:
  - **Chat:** New chat, Projects, Artifacts, Creations, and chat history.
  - **Agents:** New agent, Group chats, Routines, Learning, contacts with their last message, and Conversations (groups and agent-started threads).
  - **Code:** New coding session and the project tree.
- Search covers everything and goes wherever the hit lives. The environment is remembered on the device. Opening a conversation switches to its environment.

### Chat → Agents without copying histories

A reply in Chat has **Send to an agent**. It opens that agent's conversation with the reply as a quoted draft (`handoffDraft`, capped at 4,000 characters). The owner edits and sends it.

- The agent receives only what the owner sends.
- No history is copied.
- No permission is granted.

The agent's own grants, approvals and taint rules apply as for any owner message. This is deliberately the simplest safe bridge. A future "assign as work" can build on the same entry point.

## 3. Files

- **Backend:**
  - New: `agent/environments.py`, `tests/test_environments.py`.
  - Changed: `agent/lean/personas.py`, `agent/lean/runtime.py`, `agent/lean/loop.py`, `agent/state.py`, `agent/lean/rooms.py`, `agent/checkins.py`, `agent/lean/automations.py`, `agent/echo_code.py`, `api/routes/echo_code.py`, `api/routes/sessions.py`, `tests/test_checkins.py`.
- **Web:**
  - New: `app/environments.ts`, `app/environments.test.tsx`, `code/CodeTree.tsx`, `lean/AgentsHome.tsx`, `components/ModelMark.tsx`.
  - Changed: `index.tsx`, `components/ProjectSidebar.tsx`, `lean/LeanMessage.tsx`, `lean/Roster.tsx`, `lean/liveReducer.ts`, `lean/types.ts`, `dashboard/historyProjection.ts`, `dashboard/MessageActions.tsx`, `dashboard/ComposerInput.tsx`, `dashboard/WelcomeTypewriter.tsx`, `code/EchoCodeWorkspace.tsx`, `code/ExternalSession.tsx`, plus CSS.

## 4. Verification

- **Test:** new backend tests for the environment rule, model-first Chat (toolbox and prompt), Agents and Code keeping their harness, the Code overview and moves, and check-ins landing in Echo's conversation. New web tests for the environment model, sidebar adaptation in all three environments, model identity, the hand-off draft, contact order and drop rules. Full suites: see the release notes.
- **Preview** (throwaway data, no model calls):
  - All three environments rendered and switched.
  - Agents home listed Echo as coordinator, then the specialists. Opening Jarvis created and showed its conversation with a profile button.
  - Code's tree showed two projects. A real drag-and-drop moved an Echo session between them through the API.
  - The collapsed rail worked at phone width.
  - No console errors.
- **Owner:**
  - A live Chat reply showing the model's name and mark.
  - An agent check-in arriving in Echo's conversation.
  - The installed app.

## 5. Limits and next steps

- Old web chats continue model-first in Chat. To keep talking to Echo about one, use Send to an agent.
- Chat can still attach a project folder. Coding work belongs in Code.
- A session's title isn't renamed when it moves projects.
- When the app opens, it shows the last-saved environment for a moment, until the remembered conversation's environment arrives.
- A mobile layout with a full-screen contact list is the next step for Agents. The rail and contact list already work at phone width.
