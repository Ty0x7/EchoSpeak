# Echo TUI visual polish (18 November 2026)

Branch `tui-polish`, from `env-intelligence` at `d6f6c9e`. Not pushed, merged or installed. The visual report is
[`artifacts/research/tui-visual-polish.html`](../../artifacts/research/tui-visual-polish.html). The terminal's
design spec stays in [cli-design.md](cli-design.md); §9 there points here.

## 1. Objective and method

The goal was to bring the terminal UI to a professional, cohesive standard, judged from real screenshots of the
running application, not from model scores. The cycle was **Inspect → Render → Capture → Judge → Diagnose → Improve →
Re-render → Compare → Verify**. No local model was run for grading, and no paid API was used.

### The visual lab

These files are under `apps/terminal/tests/`.

| File | What it does |
|---|---|
| `uilab.py` | Starts an isolated backend (temporary data, runtime, state and **home** folders) and a local fake SearXNG. It seeds four conversations: Echo in a project, Echo, Jarvis and GLaDOS. Then it runs plain `echospeak` in a private tmux and drives every major screen with the keyboard: startup, composer, `?`, `/`, ← once, ← twice, search, resume, a streamed answer, tools, research, memory, a question, an approval and its denial, a model failure, a stop, four sizes, and a first start in a new folder. `--tui-root` runs the TUI from another checkout, which is how the baseline ran from an export of `d6f6c9e`. `--motion off` and `--palette standard` check the fallbacks. |
| `fake_model.py` (`ScriptedModel`) | An OpenAI-compatible model that makes **real tool calls**. The backend runs the real tools and emits real events (tool lines, approval cards, failures). Every screen shows real backend events, and no screen state is faked in the client. |
| `ansishot.py` | Turns `tmux capture-pane -e` output into a PNG via headless Chromium. It uses the owner's terminal font (JetBrains Mono) and Omarchy palette, and draws block elements as exact cell quadrants, as ghostty does. It also draws the cursor where tmux reports it. |

Reference: the real Claude Code 2.1.328 TUI, captured the same way. Those captures stay local and are not
published, because they show this machine's session titles. What was taken from it:

- a three-row mascot next to three lines of identity;
- nothing else on start;
- `/resume` as a panel in the composer's place;
- ← opening a view grouped by what needs you (Needs input / Working / Completed).

## 2. Baseline (unchanged `d6f6c9e`, 46 shots)

Defects seen in the screenshots, most serious first:

1. **Startup dumped history.**
   - Plain `echospeak` in a project silently reopened the latest conversation and printed six recap lines.
   - The banner then repeated the placeholder's tagline and a hint line the footer already shows.
2. **The summary was not honest about failed reads.** "Worked for 0s · Read 2 files · … · 2 failed" appeared when
   both reads had failed.
3. **Failures read like links.**
   - A failed tool line was bold "red", which is a link-like blue in the owner's theme.
   - Its label stayed in the present tense.
   - The error appeared twice: in the summary and again in the preview.
   - A model failure was stated three times, then labelled "Answered in 7s".
4. **Decisions were said two or three times.**
   - A question showed a running step, a status line and the dialog title.
   - An answer showed "You answered: 2026" and "You chose: 2026".
   - A denial showed "Denied: …", then "… · you denied it", then the verifier's model-facing "Blocked — authorization:
     … Do not retry it…".
   - The command dialog printed the command twice, under "Why: action tool".
5. **Two model steps ran together:** "…from now on.Noted. The parser…". Text after tool lines had no paragraph break
   and no bullet.
6. **Research showed counts, not sources** ("9 results").
7. **Second ← did nothing.** The picker had a dead gap between short titles and the agent column, and its previews
   showed raw markdown backticks.
8. **The face was a flat 7 × 2 bar** with cramped eyes, wider than it was tall, and static.
9. **The input's first character sat against the `›`.** With a block cursor, the two touched.
10. **The agent's title was inconsistent:** "Personal assistant" in one place, "Personal agent" in another.

## 3. What changed

**A. Echo's face and entrance.** In `tui/avatar.py`, the face is a 14 × 6 quadrant bitmap, about 63 × 60 px in
ghostty: square, with rounded corners and two tall pill eyes, after `EchoFace.tsx`.

- It sits in a fixed 8 × 3 canvas, so every frame has the same size and the text beside it never moves.
- The entrance lasts 1.35 s: it rises, lands with a squash, perks, glances left and right, and blinks. Frames are a
  pure function of elapsed time, so a slow redraw skips frames and never slows the animation.
- `app.py` draws the banner at the **top of the live region**, the row it is printed to afterwards. It redraws at
  ~30 fps, then commits the resting banner to scrollback.
- The live copy is dropped inside the same terminal hand-off that writes it (`_after_flush`), so no frame is ever
  without the banner and none ever shows it twice.
- Anything printed earlier commits the banner first (`emit`).
- **Fallbacks:** reduced motion, ASCII, narrower than 60 columns, shorter than 14 rows and `TERM=dumb` all print the
  static banner at once. ASCII keeps the ASCII face.

**B. The input column.** The prompt is now `›` plus two spaces (`PROMPT_W = 3`). Wrapped and continuation lines hang
at the same column. `_input_rows` and the placeholder width follow. A sent message keeps the column it was typed in.

**C. A clean start.**

- Plain `echospeak` opens no conversation and replays nothing. If the folder's project has a recent conversation,
  the banner offers it in one dim line: `Last here: “Week 53 parser” · 5 messages · 2h ago · ← to pick it up`.
- ← opens the picker on that conversation.
- `echospeak -c` / `--continue` reopens it, as the old default did, and `echospeak resume <id>` is unchanged.
- Resuming prints `⋮ N earlier messages`, then the last three exchanges.
- No data is deleted or hidden: every conversation is one key away.

**D. ← twice.**

- **First ←, the picker** (unchanged role). Its columns are now fitted to what is shown: no dead gap, agent names in
  their colour, state and age against the right edge. Previews are plain text, and the hint names "← all sessions".
- **Second ←, the sessions page** (`SessionBrowser.mode == "page"`). Conversations are grouped as *Needs you*,
  *Running*, *Today*, *Yesterday*, *This week* and *Earlier*, each with a status or agent glyph, title, agent,
  project, state and age. The selected one opens up to show its last request, its last reply, its model and its
  message count. A footer counts the conversations and names the agents.
- The page's height fits its content, up to the screen. ↑↓ follow the grouped order. Type to search, Tab to filter,
  Enter to open, Ctrl+N for a new conversation.
- **Back is one level at a time.** Esc clears a search first, then goes from page to picker to conversation; → does
  the same.
- ← while typing is still the cursor key.

**E. Conversation and tool presentation.** In `tui/state.py` and `tui/blocks.py`:

- **Finished steps are quiet** (dim). When a step failed, was denied or is held, its glyph carries the meaning (`✗ ⊘
  ⏸`, readable in any theme), its label is normal weight and its detail is dim.
- **Tool lines are tidier.** `Failed:` prefixes and `$HOME` paths are shortened, and preview lines that repeat the
  summary are dropped.
- **One line per decision.** The tool's own line says "you allowed it", "you denied it" or "You chose: 2026". The
  verifier's "Blocked" note after a denial is not repeated, and its model-facing wording never shows.
- **An open question or approval** hides the step it is about and the "needs your OK" status line. The dialog says it.
- **The command dialog** title is "Run this command" over `$ command`. A one- or two-word "Why:" is dropped.
- **Text after steps** is a new paragraph with its own bullet. A new model step after open text inserts a paragraph
  break, so replies never run together.
- **The summary counts only what succeeded.** It reads "Saved to memory" and "Asked you" rather than "Checked memory"
  and "1 other step".
- **A failure is stated once**, with no "Answered in" after it. An unverified outcome is normal weight with a dim
  check line, because it needs nothing from you. Failures and holds stay loud.
- **Web searches list their top three sources**, parsed from the tool's own numbered results with their URL lines
  (`search_sources`), so only sources the agent actually received are shown.

## 4. Verification

- **Terminal suite** (`apps/terminal/.venv`, integration excluded): 4,271 passed. This includes 62 new tests in
  `tests/test_polish.py`:
  - frame sizes and symmetry;
  - the live welcome having exactly the printed banner's rows;
  - the banner printed first, with no flicker;
  - the prompt column, wrapping and wide characters;
  - clean start and `--continue`;
  - two-level ← through the real key bindings;
  - page grouping, order and fit at 35 sizes;
  - the reducer rules above.
- **Two existing tests changed on purpose**, both asserting baseline behaviour this campaign replaced:
  - `test_tool_lines_statuses_and_previews` now asserts one line per denial, and that a failed search isn't counted
    as "Searched the web";
  - `test_banner_width_fallback` asserts the new face and alignment.
- **Integration tests** (backend venv, real backend plus scripted model): 4/4 passed.
  - The PTY round trip had waited for "? for shortcuts". That text came from the banner's old hint line, which was
    removed as a duplicate of the footer; prompt_toolkit's diff rendering never writes the footer's copy of it
    contiguously.
  - It now waits for the composer's "new session" title.
  - Against the `d6f6c9e` snapshot, the TUI drew its first frame at 0.6 s, the same as before.
- **Existing labs:**
  - `sessionlab.py`: 14/14 checks, including that a message after resuming lands in the chosen conversation;
  - `commandlab.py`: 34/34, covering every slash command, the main keys, Esc stopping a run, and a backend restart;
  - `sizelab.py` at 100 × 30: no ghosts.
- **Visual lab** at 110 × 32, 80 × 24, 60 × 20, 40 × 16 and 160 × 44:
  - baseline: 46 shots;
  - round 1: 59;
  - round 2, after review fixes: 59;
  - final round, after more fixes and with a hermetic search: 59;
  - a reduced-motion, neutral-palette run: 19.

  The labs were keyboard-driven throughout, and every screen was judged from the PNGs. Curated before/after shots
  are in `artifacts/media/echo-tui/polish/`.
- **Fixes the screenshots prompted after round 1:**
  - a rich base style tinted every span of a failed tool line;
  - "Last here" didn't align with the header;
  - the page left a dead void under a short list;
  - ages were worded differently in the picker and the page;
  - an unverified outcome was the loudest line on screen;
  - an `ask_user` step was summarised as "1 other step";
  - the recap's prompt column didn't match;
  - a denied-only turn said "Answered in";
  - numbered lists had no period;
  - a half-risen entrance frame read as legs.

## 5. Scorecard

The scores come from the screenshots, on a demanding scale: **10** means nothing a careful reviewer would flag. The
target was 10 in every category. **It was not reached in every category**, and the reasons are given rather than
the scores raised.

| Category | Before | After | Evidence, and what still keeps it from 10 |
|---|---|---|---|
| Visual design | 6 | 9 | Square face, a three-line header, a quiet canvas, a restrained palette. Not yet seen in the owner's real ghostty window, and in the Ion Blue theme the agents' hues converge. |
| Typography | 7 | 9 | Prompt column, `1.` lists, plain-text previews, home paths as `~`. A model failure still shows the backend's raw `Details: {"error": …}` JSON in the answer text. |
| Layout | 7 | 9 | Five sizes, no ghosts (size lab), the page fitted to its content, the composer pinned. At 80 columns and below, the footer's hint drops to `? help` before ← sessions; the tested footer ladder decides that, and changing it was reverted. |
| Conversations | 6 | 9 | No run-together replies, a paragraph and bullet after steps, the recap in the same column. Agent names are the backend's ("Glados"). |
| Tool presentation | 5 | 9 | Honest summaries (`1 failed`, `1 denied`), one line per decision, no leaked model wording, failures that read as failures. A failed or denied step keeps the backend's present-tense label ("✗ Reading …"). |
| Research presentation | 5 | 8 | Each search names its real top sources. Deep research with spawned workers, and delegation (`message_agent` wasn't offered in the lab), were not exercised live; only reducer tests cover their rendering. |
| Sessions | 6 | 9 | Two-level ← with a grouped page, agent colour, project, state, age and the last exchange. The live lab only had idle conversations; the *Needs you* and *Running* groups are verified by tests, not screenshots. There is no rename or archive. |
| Interaction | 7 | 9 | Keyboard-driven labs: session 14/14, command 34/34, the PTY round trip, and 62 tests through the real key bindings. Not yet hand-checked by the owner. |
| Animation | 5 | 9 | A time-based entrance at ~30 fps, reduced motion verified (one static frame), committed without a shift or a duplicate. Seen through tmux captures; the real ghostty frame pacing is unverified. |
| Consistency | 6 | 9 | One prompt column, one age format, one agent title, help text matching behaviour. The footer gap above remains. |

## 5a. Lab correction

The baseline and the first two "after" rounds made real web searches. They ran generic ISO-week queries against
DuckDuckGo, because the backend always merges DuckDuckGo results unless privacy mode is private. The lab now sets
`PRIVACY_MODE=private`, and the final round's searches returned only the fake SearXNG's results (3 and 1). No
private data was in those queries. The baseline also showed file reads resolving under the real `~/Documents`,
because the backend inherited the real `HOME`; that was a missing-file read, and nothing was touched. The lab now
gives the backend its own home.

## 6. Limits and open items

- **Not seen on the owner's real ghostty or alacritty window.** tmux plus Chromium painting was the test terminal.
  The renderer reproduces ghostty's metrics and quadrant drawing, but the final hand check is the owner's.
- **Delegation (`message_agent`) wasn't exercised live.** The backend didn't offer the tool for "tell jarvis …",
  either in a project chat or in GLaDOS's room. `agent_reach.message_target` reads `self.job.goal`. This was not
  investigated, because it's backend delegation, not UI. The rendering of delegation, agent headers and task boards
  is covered by reducer tests with real-shaped events.
- **Scrollback printed before a resize keeps its old wrapping.** Every inline terminal app shares this; Claude Code
  does the same.
- **In the owner's monochrome Ion Blue theme, hue barely separates agents or states.** Names, glyphs and weight
  carry the meaning; hue is a second signal by design.
- **Agent names come from the backend** ("Glados").
- **The plain line REPL (`--plain`) is unchanged.** It still reopens the latest chat; only the TUI starts clean.

## 7. EchoSpeak capabilities on this branch

This branch carries everything on `env-intelligence` up to `d6f6c9e`, including:

- **Delegation, as three systems:** A, primary agents in a shared project; B, spawned sub-agents; C, an explicit
  `message_agent`;
- **G1:** Echo's Chat and Code profiles count as Echo for project delegation;
- **read grants;**
- **the auto-test-fix loop;**
- **the evaluation cases.**

It does **not** include these unmerged candidate branches, which were left untouched:

- `connected-apps-candidate`: the `find_tools` app name, the connected-app step budget, and the app names in the
  focus note;
- `search-recall-candidate`: the `chat_search` top-up, and "remember" keeping `memory_save` listed;
- the `auto/…` campaign branches: W1 and the D7 fixes.

The installed `echospeak` on PATH still runs `agent-first-sessions`, so this TUI is only seen by running this
branch's launcher.

## 8. Owner acceptance

Run `apps/terminal/echospeak` from this branch (or `echospeak` after installing it), in a project folder, then check:

1. **Startup.** Echo arrives, and the banner settles where it played, with no flicker, no history and one "Last here"
   line.
2. **Typing.** The first character sits two cells right of `›`. ← moves the cursor while you type.
3. **← on an empty input.** The picker opens on the last conversation.
4. **← again.** The grouped page opens. Esc goes back to the picker, and Esc again to your conversation.
5. **Picking up a conversation.** Enter resumes it: `⋮ N earlier messages` and the last exchanges appear.
6. **Running work.** Ask something that reads files or searches the web, and something that needs approval, and judge
   the lines.
7. **`echospeak -c`.** It reopens the last conversation directly.
8. **Fallbacks.** `ECHO_REDUCED_MOTION=1 echospeak` and `ECHO_TUI_ASCII=1 echospeak` show a static banner.


## 9. Round 2 (19 November 2026, branch `tui-refine`)

The owner's brief for this round:

- a truly square avatar that slides in from the left, with no flash when it lands;
- a real full-screen session browser on the second ←, after Codex;
- context research;
- agent awareness.

The context and agent findings are in [context-strategy.md](context-strategy.md).

**Avatar.**

- **Shape.** The face is now a solid 14 × 6 quadrant square: 7 cells × 3 rows, about 63 × 60 px. It has straight
  sides; the earlier face's notched top and bottom rows made its middle row stick out. The eyes are one cell wide
  and one row tall, three half-cells from each side.
- **Entrance.** Echo slides in from the left edge in whole half-cell steps, about 4.5 px each, easing out over
  520 ms, then blinks once at about 820 ms. The canvas is fixed, so the text beside it never moves.
- **Tests.**
  - Every frame is 7 × 3 cells.
  - The right edge moves monotonically, at most two half-cells per 10 ms.
  - The face is symmetric with straight sides.
  - Reduced motion and ASCII are still static.

**The flash.**

- **Cause.** Printing the banner when the entrance ended handed the terminal back to prompt_toolkit, which erased
  the whole live region (`ESC [ J`) and drew it again.
- **Fix.** The banner now stays in the live region at rest and is printed by the next output, or at exit. Both
  redraw anyway.
- **Measured** with `tests/flashprobe.py`: the real TUI in a PTY, every byte it writes in the 4 s after the first
  frame, with no input.
  - Previous commit `cdbb5ed`: **2 full erases**, at 1.67 s, followed by a 960-byte redraw.
  - Now: **0**.
- **On a quiet quit**, the banner is in scrollback exactly once (checked in tmux).

**A bug the lab caught, and its fix.**

- Lazy printing exposed an old race: several outputs before the flush (a resume prints a rule and its recap at
  once) each printed the banner again. The live lab showed three banners between the recap lines.
- The banner is now committed exactly once (`_welcome_sent`); test
  `test_several_outputs_at_once_print_the_banner_once`. The lab, re-run, shows one.

**The full-screen session browser** (`tui/sessionpage.py`).

- **Reference.** Codex 0.170's real resume picker, captured locally with 42 sessions and not published:
  - a title, the filter, status and sort row, a search line, a dense list of age and title;
  - a position rule ("3 / 6 · 100%") and two hint lines;
  - details (session and ID, created, updated, directory, branch, conversation) only on ^e.
- **What the page does differently.** The second ← now opens a page on the **alternate screen**: a separate
  full-screen prompt_toolkit app, run inside the main app's `in_terminal`. Leaving it restores the conversation and
  its scrollback untouched (prototyped first).
- **Layout.**
  - A title with a count of conversations and agents, the filters, the search line, then the list grouped as *Needs
    you*, *Running* and by day. Each row has a status or agent glyph, title, agent and age.
  - The details pane sits on the right from 96 columns: the agent (and group members), the project and its
    folder, status, updated and created (relative and absolute), message count, model, session ID, and the last
    exchange, wrapped.
  - Below 96 columns the details go under the list, compact when short. Then come a position rule and hints.
  - The first ← (the picker) is unchanged.
- **Keys.**
  - ↑↓ PgUp PgDn Home End, Tab and Shift+Tab to filter, type to search, Backspace.
  - Enter resumes, Ctrl+N starts a new conversation.
  - Esc clears a search first, then goes back to the picker; → also goes back.
  - Ctrl+C closes the sessions and returns to the conversation. ← on the page does nothing.
- **Tests.**
  - It fills exactly width × height at 48 sizes.
  - The details pane shows the real fields.
  - The narrow layout is right.
  - The app routes each result (back, resume, new, close).
- **Live lab.** Search, clear, back to the picker, back to the composer, and Enter on the page resuming another
  conversation.

**Checks this round:**

- Terminal suite: 4,286 passed. Integration: 4/4.
- Session lab: 14/14. Command lab: 34/34. Flash probe: 0 erases.
- Backend: 1,890 passed and the same 21 pre-existing failures (unchanged).
- Visual lab: four rounds (41, 36, 36 and 36 shots).

**Scorecard after round 2.** The scores are honest, and still not 10.

| Category | R1 | R2 | Change, and what still keeps it from 10 |
|---|---|---|---|
| Visual design | 9 | 9 | A truly square face; still not seen in the owner's ghostty |
| Typography | 9 | 9 | Unchanged; raw backend JSON in model-failure text |
| Layout | 9 | 9 | The page fits 20 × 3 up to 200 × 50; the footer hint gap at 80 columns or less remains |
| Conversations | 9 | 9 | Unchanged |
| Tool presentation | 9 | 9 | Unchanged |
| Research presentation | 8 | 8 | Unchanged: no live deep-research or delegation evidence |
| Sessions | 9 | 9 | Now a real full-screen browser with details. No rename, archive or sort, which Codex has |
| Interaction | 9 | 9 | Every page key routed and tested. Awaiting the owner's hands |
| Animation | 9 | 9 | The slide-in and the flash fixed, with the latter measured. Ghostty's own frame pacing unverified |
| Consistency | 9 | 9 | One model for picker and page |

**Open:**

- Rename, archive and sort on the page.
- Hand checks in ghostty and alacritty.
- The footer's ← hint at 80 columns.
