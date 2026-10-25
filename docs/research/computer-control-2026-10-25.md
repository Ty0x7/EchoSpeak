# Computer control that notices it's stuck, skills Echo can draft, and one terminal everywhere (11.10.3)

Three pieces of work from the owner's 11.10.1 live test and follow-up questions:

1. Why Echo looped on Minecraft's main menu for ten minutes, and a general fix (not a Minecraft one).
2. Whether this needed a skill, and how Echo should get and make skills.
3. How Claude Code and Codex lay themselves out on Windows (`~/.claude`, `~/.codex`), and making
   "This PC" in the terminal work as well as the sandbox.

## 1. The Minecraft run

**Request:** "open up minecraft and start playing it! … make sure it's in peaceful!" Model:
`google/gemma-4-e4b` on LM Studio, 64 tools offered, Autonomous mode. Evidence is the saved trace
and chat records of that run (`lean/traces/…/msg_4e4c3c51b31a.json`, the `tool_runs` rows and
the screenshots in `lean/observations/`), read directly; nothing was re-run.

| Step | Call | What came back | Recorded as |
|---|---|---|---|
| 1 | `open_application(Minecraft)` | "Shortcut launch requested…" | ok |
| 2 | `desktop_list_windows` | no Minecraft window yet | ok |
| 3 | `take_screenshot` (monitor) | menu visible; named controls: taskbar only | ok |
| 4 | `desktop_click(control_name="Singleplayer")` | **"Control not found."** | **ok** |
| 5–10 | `vision_qa(question="Click the Singleplayer button…")` ×6 | a screenshot, nothing else | ok |
| 11 | reply | "I'm running into a loop… every time I click Singleplayer it brings me back" | — |

Its reasoning at step 7 says "I have clicked Singleplayer (based on the previous successful
`vision_qa` call)". It never called `computer_action`.

### Root causes (all in the harness)

1. **A failure recorded as success.** The desktop tools (`agent/tools.py`) report failure only in
   their text. `"Control not found."`, `"Click failed: …"` and `"Window not found."` didn't start
   with any of the toolbox's failure prefixes, so they became `ok`. The model's own words show it
   took that as "clicked".
2. **Looking was allowed to stand in for doing.** `vision_qa` is a screenshot with a `question`
   the tool ignores. Asked to "click", it captured and reported success. Nothing said it can't act.
3. **No sign that this window draws its own buttons.** Games and canvas apps expose nothing to
   Windows UI Automation. The screenshot result listed no controls for the Minecraft window and
   didn't say why, so the model kept looking for a name to click.
4. **No stall detection for screen work.** The repeat check skips screenshot tools (screens
   change), and Minecraft's animated panorama made every capture's hash different. The fail
   streak counts failures, and every look "succeeded". Nothing noticed seven looks in a row with
   no action.

## 2. What changed

### Computer control (general)

- **Truthful outcomes.** `_FAILURE_PREFIXES` (`agent/lean/toolbox.py`) now includes the desktop
  tools' failure texts: control/window not found, click/type/hotkey/activate failed, and others.
- **A useful "not found".** `desktop_click` and `desktop_type_text` say why (`_control_not_found`
  in `agent/tools.py`). A window whose only named parts are its title bar "draws its own
  interface… click the button with `computer_action` at the point you see it". Otherwise the
  message points to the screenshot's list of named controls.
- **`vision_qa` says it didn't click.** Its description is now "It only looks: it never clicks,
  types or changes anything". A question phrased as an action ("Click…", "Press…", "Open…") gets
  "vision_qa only looks: nothing was clicked or typed…" with the exact `computer_action` call to
  make, and the outcome `no_effect` (`agent/lean/computer.py` `vision_qa`).
- **Drawn windows are named as such.** When a captured window reports no controls beyond its
  title bar, the screenshot result says so and how to act (`DRAWN_NOTE`, `WINDOW_CHROME`).
- **Stall detection** (`agent/lean/loop.py` `_screen_progress`):
  - Three looks in a row with no action between them add: "you've looked at the screen 3 times in
    a row without acting. Looking never clicks or types… do it now with `computer_action`… or tell
    the user what is blocking you". Without desktop control, the note says to tell the user instead.
  - Two screen actions in a row that failed or changed nothing visible add a change-of-approach
    note: newest coordinates, focus the window, keyboard (tab, enter, esc), double-click, then stop.
  - Each note also emits a `screen_stall` event. An action that visibly changes the screen resets
    both counts, and any other successful tool resets the look count.
- **Prompt.** A new rule, only when `computer_action` is offered: "Looking and doing are different
  tools… Games and apps that draw their own interface have no named controls… if nothing changed,
  change approach instead of repeating it." The `computer_action` description now says games list
  no controls, so click the middle of what you see.

Approvals are unchanged. Every click still goes through `computer_action`'s existing screen-control
approval, fresh-capture check and geometry checks.

### Does this need a skill?

No: the loop was the harness's fault, so it's fixed in the harness. A skill is instructions an
agent reads when a task matches; it can't fix a tool that reports a failure as success. With the
fixes above, the same 4B model is told at step 4 that the button can't be clicked by name, and at
step 7 that it has looked three times without acting.

Skills still help, for know-how: a general one for apps and games, and app-specific ones that
Echo learns by doing.

- **`desktop-apps-and-games`** (bundled, `apps/backend/resources/agent-skills/`). It covers the
  look → one step → act → check loop, recovery when nothing changes, menus and toggles, and game
  input (holds, `move_relative` for cameras, `esc`). It's offered in Settings › Skills and, like
  every skill, works only after the owner approves it.
- **`propose_skill`** (`agent/lean/agent_skills.py`): Echo drafts a SKILL.md from steps that
  worked (for example `minecraft-new-world`). It's saved as *needs review* and agents never see it
  until the owner approves it, so an agent can't give itself instructions. It's offered only to
  the owner's agent in an interactive chat, never in routines or to guests. A prompt rule tells
  Echo to offer this after working something out.
- **Skills from other agents' folders.** Settings › Skills lists skills you could add: EchoSpeak's
  own, plus folders in `~/.echospeak/skills`, `~/.agents/skills`, `~/.claude/skills`,
  `~/.codex/skills` and the workspace's `.agents/skills`. Adding one copies it in for review
  (`available()`, `GET /lean/agent-skills` → `available`).

## 3. How Claude Code and Codex live on Windows, and what EchoSpeak now mirrors

Research from the official docs (sources below):

| | Claude Code | Codex | EchoSpeak 11.10.3 |
|---|---|---|---|
| Your folder | `%USERPROFILE%\.claude` (`CLAUDE_CONFIG_DIR`) | `%USERPROFILE%\.codex` (`CODEX_HOME`) | `%USERPROFILE%\.echospeak` (`ECHOSPEAK_HOME`) |
| Your instructions | `~/.claude/CLAUDE.md` | `~/.codex/AGENTS.md` | `~/.echospeak/AGENTS.md` |
| Project instructions | `CLAUDE.md` (or `AGENTS.md`) | `AGENTS.md`, root → cwd, 32 KiB cap | the attached project's `AGENTS.md`, else `CLAUDE.md`; 32 KiB cap |
| Skills | `~/.claude/skills`, `.claude/skills` | `~/.agents/skills`, `.agents/skills` | all of those, plus `~/.echospeak/skills`, offered for review |
| App data | `~/.claude/projects`, `~/.claude.json` | `~/.codex/sessions` | unchanged: `%LOCALAPPDATA%\ai.echospeak.desktop` |
| Shell on Windows | Git Bash for Bash, plus a PowerShell tool | PowerShell, with a Windows sandbox | PowerShell, plus Git Bash with `shell="bash"` |

`agent/home.py` creates `~/.echospeak` with `skills/` and a commented `AGENTS.md` template when
the backend starts. It never overwrites a file, and an untouched template adds nothing to the
prompt. Instructions enter the prompt after Skills as "Instructions from AGENTS.md files", with
the project file labelled as written by whoever made the project. They never override safety
rules or approvals. Chats, memory and settings deliberately stay in the app data folder.

### This PC vs the sandbox

The sandbox told agents what it had ("bash… Node 22, Python 3, git"). "This PC" said only
"PowerShell", so models wrote bash and Linux habits: `python3`, `grep`, `ls -la`, `&&` in
Windows PowerShell 5.1. Now (`agent/lean/terminal.py`):

- **What's installed.** This PC's description lists the programs found on PATH (git, node, npm,
  pnpm, python, py, pip, uv, rg, gh, docker, winget, curl) and what's missing. It flags
  the Microsoft Store `python.exe` shortcut and suggests `py`. These are PATH lookups only, done
  once per run.
- **How PowerShell differs.** It explains chaining for 5.1 (`;`, `if ($?) {}`) and gives a short
  table of bash → PowerShell equivalents, the way Claude Code's PowerShell tool does.
- **Git Bash for bash commands.** When Git for Windows is installed, `terminal` takes
  `shell="bash"` and runs Git Bash on this PC. Those are the same commands the sandbox runs, and
  PowerShell stays the default for Windows things. WSL's `System32\bash.exe` is never used, and
  `ECHOSPEAK_GIT_BASH_PATH` overrides the location. If Git Bash is missing, the command is refused
  with the PowerShell equivalents.
- **Failure hints.**
  - A bash command run in PowerShell gets its PowerShell equivalent (or `shell="bash"`).
  - Bash flags given to cmdlets ("A parameter cannot be found…") get the equivalents table.
  - Exit code 1 with no output from grep, rg, findstr or `git diff` is explained as "nothing
    matched", not "it broke".
- **Same safety.** Bash commands on this PC go through the same approval check. `find … -delete`,
  `shred`, `truncate` and `unlink` were added to the dangerous-command list so they ask on this PC
  as they already did in the sandbox.

## Files

- `apps/backend/agent/lean/computer.py`, `loop.py`, `toolbox.py`, `prompt.py`, `terminal.py`,
  `approvals.py`, `agent_skills.py`, `runtime.py`, `capabilities.py`
- `apps/backend/agent/tools.py` (`_control_not_found`), `apps/backend/agent/home.py` (new)
- `apps/backend/api/server.py` (creates `~/.echospeak`), `api/routes/lean.py` (available skills)
- `apps/backend/resources/agent-skills/desktop-apps-and-games/SKILL.md` (new)
- `apps/web/src/settings/AgentSkillsSection.tsx`, `apps/web/src/lean/api.ts`
- Tests: `tests/test_screen_progress.py`, `tests/test_home_and_parity.py` (new);
  `tests/conftest.py` isolates `ECHOSPEAK_HOME`

## Verification

- **New tests (24).** These include the Minecraft sequence replayed through the real loop with a
  scripted model. `desktop_click` is now `failed`, and the third look without an action gets the
  note.
- **Related suites.** Capabilities, evidence, terminal, computer and swarm: 271 passed, 10 skipped.
- **Full backend suite:** 1287 passed, 17 skipped, 0 failed. Getting there fixed a date-dependent
  11.10.2 reminder test that mixed the real clock with its fixed date.
- **Web:** `npm run check`, typecheck plus 175 tests, passes.
- **On this PC, outside the test suite.** Git Bash ran `ls -la | head -3 && pwd` in the requested
  folder. `grep` with no match got the exit-1 hint, and `ls -la` in PowerShell got the
  equivalents hint.
- **Not verified.** A live run on Minecraft or another game with a real model is owner testing
  (TESTING_GUIDE 4.7–4.10). Whether a 4B model then clicks the right point is model behaviour, not
  something tests can show.

## Limits and next steps

- **Coordinate accuracy.** A small vision model can still misplace a click on a small button.
  The new notes stop it repeating; they don't make it see better. Set-of-marks style grounding (OCR
  boxes for drawn text) is the next step if live tests show misses.
- **Typing into AGENTS.md.** Echo can't edit `~/.echospeak/AGENTS.md` itself. File tools don't
  reach it, on purpose, because an agent writing its own standing instructions is the persistence
  risk memory writes are guarded against. Edit it yourself.
- **Imported skills are copies.** A skill added from `~/.agents/skills` doesn't follow later edits
  there. Import it again to update, and it needs approving again.
- **Project instructions.** Only the attached project's root `AGENTS.md`/`CLAUDE.md` is read, not
  nested ones per subfolder as Codex does.

## Sources

- [Claude Code: Advanced setup (Windows, Git Bash, PowerShell tool)](https://code.claude.com/docs/en/setup)
- [Claude Code: Settings files and scopes](https://code.claude.com/docs/en/settings)
- [Claude Code: The .claude directory](https://code.claude.com/docs/en/claude-directory)
- [Claude Code: Tools reference (Bash and PowerShell tools)](https://code.claude.com/docs/en/tools-reference)
- [Codex on Windows: Windows sandbox](https://learn.chatgpt.com/docs/windows/windows-sandbox)
- [Codex: Agent Skills](https://developers.openai.com/codex/skills)
- [AGENTS.md and skills in Codex (discovery order)](https://developertoolkit.ai/en/codex/tips-tricks/agents-md-optimization/)
