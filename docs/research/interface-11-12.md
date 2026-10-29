# EchoSpeak 11.12.0: a quieter, clearer interface, and coding agents that follow the rules

This release covers ten owner requests: how work is shown, following new content, reasoning effort in Code, compliance of the external coding harnesses, a richer workspace details panel, removing "Files & sessions" from Code, delete behaviour, the Agents screen, the "Opening project…" bug, and the sidebar. [Visual summary](../../artifacts/research/interface-11-12.html).

## 1. Showing work like Claude and ChatGPT (request 1)

**What they do:**
- Activity is a quiet line of text, not a card.
- Thinking is the one framed element, collapsing to "Thought for 12s".
- Tool use reads as plain lines ("Searched the web", "Read 3 files").
- Once the answer is written, the work before it folds into one summary line you can open.

**Before:** every tool step and step group was a bordered box with a boxed status icon. Notes were a centred rule. The outcome and "stopped" were boxes too.

**Now** (`lean/LeanMessage.tsx`, `lean/steps.ts`, `lean/lean.css`):
- Steps are plain lines. The running step's label shimmers like "Thinking", and the summary follows on the same line after a dot.
- Consecutive thinking and steps between parts of the answer form one **activity**. When finished, it folds into **"Worked for 42s · Read 3 files · Ran 2 commands"**. The chevron shows the thinking box and every step, and result cards stay visible.
- While a reply is streaming, the trailing activity stays open, so lines don't fold between steps.
- Notes are small lines with a status mark: a check for "Verified" or "Checks pass", a warning for "Not verified" or "Blocked".
- `calculate` gets a real label ("Calculated 21*2" / "Did a calculation") instead of the raw call.

## 2. Following new content (request 2)

**Causes found:**
- **The scroller is keyed by conversation,** so switching chats replaces the element. The resize and mutation observers stayed attached to the old element, so following silently stopped after any switch.
- **Wheel events inside the thinking box bubbled up to the chat.** The chat read them as "the user scrolled up" and stopped following, so the view tracked the inside of the thinking box but not what appeared below it.

**Fixes** (`index.tsx`, `app/chatFollow.ts`, `dashboard/ChatThread.tsx`):
- The scroller attaches through a callback ref, so the observers follow the element itself.
- `innerScrollerTakes()` ignores wheel movement an inner scroller consumes. Reaching the top of the thinking box and scrolling again still releases the chat, as it should.
- A **jump to latest** button appears whenever you've scrolled away from the bottom.

Checked in the browser with a scripted model (thinking → tool → a long streamed answer):
- the view followed the thinking box and the answer below it
- scrolling up stopped following and showed the button
- the button returned to the latest message

## 3. Reasoning effort in Code (request 3)

Each agent's own documented control is used:

| Runtime | Control | Levels offered |
|---|---|---|
| Echo native | Echo's reasoning effort (the same setting as the composer) | Shown when the model has a reasoning control; otherwise disabled with the reason |
| Codex | `turn/start` `effort` ([app-server](https://learn.chatgpt.com/docs/app-server)) | The levels `model/list` reports for the chosen model, with its default. Anything else isn't sent |
| Claude Code | `--effort low\|medium\|high\|xhigh\|max` ([CLI reference](https://code.claude.com/docs/en/cli-reference), [model config](https://code.claude.com/docs/en/model-config)) | The five levels, with the documented default per alias (medium for Opus, Sonnet and Haiku 5.5; high for Fable). Claude Code falls back to the highest supported level itself |
| ACP | none standard | Disabled ("this agent manages its own effort") |

The choice is remembered per runtime. A remembered level the model doesn't offer falls back to the model's default instead of being sent.

**Backend:** `Task.effort` (validated) → `code_sessions.prompt(..., effort)` → `Codex.prompt` / `ClaudeCli.argv` (`agent/code_runtimes.py`).

## 4. Following Claude's and OpenAI's rules (request 4)

Read from the primary documents on 29 October 2026.

**Anthropic** ([Legal and compliance](https://code.claude.com/docs/en/legal-and-compliance)):
- **Forbidden:** third parties offering Claude.ai login, or routing requests through Free, Pro or Max credentials on users' behalf. Also collecting, storing or intermediating Claude credentials.
- **Explicitly allowed:** "an end user signing in to the unmodified Claude Code binary with their own Claude subscription".
- **Conditions for running Claude Code in a product:**
  - an unmodified binary, with no authentication method removed
  - each user authenticates with their own plan or key
  - no reselling or intermediating usage
- **Names and logos:** naming Claude Code in plain text is fine. Any other use of Anthropic's names or logos needs written permission.

**OpenAI** ([app-server](https://learn.chatgpt.com/docs/app-server)):
- `app-server` is for "a deep integration inside your own product". Identify the client with `clientInfo.name`.
- It's marked experimental.

| Requirement | EchoSpeak | Status |
|---|---|---|
| Unmodified Claude Code, user's own install | Runs the user's installed `claude` as published (`agent/code_runtimes.ClaudeCli`) | ✓ |
| Sign-in through Anthropic's own flow | Opens `claude auth login` in its own console (`code_accounts.claude_login`); Echo never sees it | ✓ |
| No collecting or intermediating credentials | Reads only `claude auth status --json`, keeping signed-in, method and plan; passes no API key; child environments drop tokens | ✓ |
| No auth method removed or restricted | Flags used: `-p`, `--permission-mode acceptEdits`, `--setting-sources user`, `--strict-mcp-config`, `--settings` (permission hook), `--model`, `--effort`, `--resume`. None touch authentication | ✓ |
| Each user's own plan; no reselling | Local app; every user signs in to their own plan; EchoSpeak bills nothing | ✓ |
| Ordinary individual use | Runs only when you send a task in Echo Code; no unattended loops | ✓ |
| Names and logos | **Fixed:** the Claude and OpenAI logos were used as runtime icons; they're now neutral monograms ("CC", "Cx"). Plain-text names stay | ✓ (11.12.0) |
| Codex sign-in through OpenAI | `account/login/start` → only an openai.com or chatgpt.com URL is opened | ✓ |
| Codex client identification | `clientInfo.name = "echospeak"` | ✓ |
| Codex integration surface | `app-server`, its documented product integration | ✓, and experimental per OpenAI |

**For you to decide:**
- **Commercial terms:** Anthropic's page says running Claude Code "in your products or services (e.g. in hosted sandboxes or other agent infrastructure)" requires its Commercial Terms. EchoSpeak runs the user's own local install rather than hosting it. If you distribute EchoSpeak commercially, confirm with Anthropic whether that applies.
- **Chat's model picker** shows provider marks (`ModelMark`). The same trademark caution applies; I didn't change it, as it's outside Code.

## 5. Workspace details (request 5)

The details popover (title bar) is rebuilt from your screenshot's structure, in EchoSpeak's styling. It's wider (380 px) and scrolls, with collapsible sections that remember their state:

- **Environment:**
  - the computer (with Open in VS Code)
  - the project and its path (with Add folder)
  - the branch and short commit, or "No Git repository"
  - the runtime (with Usage & limits)
- **Context:** the open conversation's context use as a percentage and bar (the same count as the composer's meter), the model, and a note that long conversations are summarized automatically.
- **Outputs:** the conversation's artifacts, newest first. Each opens in the side panel; "Show more" reveals the rest.
- **Changes:**
  - `+added −removed` lines from the working-tree diff
  - the changed-file count (opens Review & commit)
  - the latest pull requests with their state, and All pull requests
- **Servers & scripts:** running workspace terminals, project scripts with Run, and Add a project script.

## 6–10. Smaller fixes

- **6. Code:** the "Files & sessions" button and panel are removed, along with the file-listing requests behind them. Sessions are in Code's sidebar; files are in the workspace panel.
- **7. Deletes:**
  - **Don't jump environments.** Deleting the open conversation moves to the most recent conversation in the same environment (in Code, the same project), or to that environment's new-chat screen.
  - **Deleted chats stay deleted.** Two causes, both fixed:
    - `deleteThread` built the new list from a stale copy, so quick successive deletes could restore a removed chat.
    - `refreshThreads` opened "the first Session" whenever the open one was gone, which could be a deleted or cross-environment chat.
  - **Refreshes don't open conversations.** Only the first load opens the most recent one; deleted IDs are filtered from refreshes that race the delete.
  - **Failed deletes aren't hidden.** A delete that fails keeps the conversation in the list.
  - **Other deletes:** deleting a room clears it if it was open. Deleting a project clears Code's and the project page's selection.
  - **Checked in the browser:** deleting the open chat went to the other Chat conversation; deleting the last one left the new-chat screen, which stayed through a background refresh.
- **8. Agents:** entering Agents reopens the agent conversation you left (remembered), else the most recent one. With none, a short start screen offers **Create agent**, New group and Message Echo, instead of a second copy of the sidebar roster.
- **9. "Opening project…":** shown only while loading. A loaded folder without Git says "No git repository", and a failed load says so. The cause was `git_summary || "Opening project…"`: a non-repository's summary is empty.
- **10. Sidebar:**
  - 248 px wide (was 216).
  - A conversation with a turn running shows a pulsing dot with "Working" in Chat and Agents, and a dot in Code's tree. Motion is off under reduced-motion settings.

## Verification

Only tests for the changed components ran; no paid models.

- **Web:**
  - `src/lean/activity.test.tsx` (new, 6):
    - activity grouping and duration
    - "Worked for 7s · Read 1 file · Ran 1 command", folded while finished and open while live
    - verification note tones
    - inner-scroller wheel handling
    - diff stats
    - the Agents start screen without the roster
  - `src/code/model.test.ts`: effort levels kept, malformed ones dropped, model row selection.
  - `runtimeLayout.test.ts`: the new width.
  - All 31 test files in `src/lean`, `src/app`, `src/code`, `src/workbench`, `src/components`, `src/dashboard`, `src/widgets`, `src/settings` and `runtimeLayout` pass (160 tests). `tsc --noEmit` is clean.
- **Backend:**
  - `tests/test_claude_code_runtime.py`: effort reaches Claude Code as `--effort` and Codex as `turn/start effort` (only levels the model offers).
  - Re-run: `test_code_sessions`, `test_echo_code`, `test_live_steps`, `test_lean_harness`. 90 passed.
- **Browser:** the dev build with a scripted model, checking §1, §2, §6, §7, §8 and §10 live.

## Needs your test

- A real reasoning model in Chat: the thinking box, then steps, then the answer folding into "Worked for …".
- Code with Codex and with Claude Code signed in: pick a model, then an effort, then send. Codex should show only that model's efforts.
- Workspace details on a real Git project: diff stats, pull requests (with `gh` signed in), and scripts.
