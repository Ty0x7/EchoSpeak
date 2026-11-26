# Echo terminal UI: design spec (from Claude Code, Codex CLI and Hermes)

The current TUI uses a full-height alternate-screen viewport with a scrollable app-owned transcript
and a bottom composer. Sessions shares that viewport and animates the same Echo face.
Older implementation sections below describe the prior inline terminal and its resize experiments.
See the [terminal guide](../../apps/terminal/README.md) for current keys, terminal behavior and the
shared deletion endpoint's storage and retention rules.


Status: design spec, **implemented** in `apps/terminal/echospeak_cli/tui/` (14 November 2026; merged in 11.14.0).
§5 records what was built, what was verified and what is left. §6 (16 November 2026, branch `tui-responsive`)
makes the live region and scrollback fit any terminal size and fixes leftover rules after a resize.
Scope: the interactive `echospeak` client in `apps/terminal/`. The plain, stdlib-only reducer
(`apps/terminal/echospeak_cli/render.py`) stays as the non-TTY path.

---

## 1. Evidence summary

**Captured locally** (detached tmux at 120×40, no prompt sent, sessions killed afterwards). Files are in
`~/.claude/jobs/a5f38c9b/tmp/cli-research/`:

| File | What |
|---|---|
| `claude-trust-dialog.{ansi,txt}`, `codex-trust-dialog.{ansi,txt}` | First-run trust dialogs in an untrusted folder (nothing was accepted) |
| `claude-start.{ansi,txt}` | Claude Code v2.1.325 idle screen (trusted folder) |
| `claude-slash.*`, `claude-shortcuts.*` | After typing `/`, and after typing `?` (no Enter) |
| `codex-start.*`, `codex-slash.*`, `codex-shortcuts.*` | Codex CLI v0.169.0, same three states |

```text
 ▐▛███▛█   Claude Code v2.1.325
▝▜██████▀  Opus 5.5 with high effort · Claude Pro
 ▝▝   ▝▝   ~/Work
…(empty rows)…
───────────────────────────────────────────── echospeak-harness-rearchitecture ─
❯ Try "refactor server.py"
────────────────────────────────────────────────────────────────────────────────
  ⏵⏵ auto mode on (shift+tab to cycle)
```
ANSI: rules `\e[34m` (palette blue), logo `38;5;153`/`48;5;23`, dim text `38;5;145`, placeholder `\e[2m`.

```text
  >_ OpenAI Codex (v0.169.0)
     ~/.claude/jobs/…/cli-research

  To get started, describe a task or try one of these commands:
  /init - create an AGENTS.md file with instructions for Codex
  /status - show current session configuration
…
› Ask Codex to do anything                    ← full-width row, bg 48;2;49;62;70

  GPT-6.1-Sol xhigh · ~/.claude/jobs/…/cli-research
  ← for agents · ? for shortcuts
```
`>_` is truecolor `38;2;99;168;248`. The composer is a tinted block with blank padding rows and no border.

Slash menus, as captured: Claude lists `❯ /add-dir   Add a new working directory …` (5 rows) **above** the input rules.
Codex lists `› /model   choose what model …` (8 rows) directly above the composer.

**Codex source**: shallow clone of `openai/codex` at `…/cli-research/codex-src/codex-rs/tui/src/`. Files read:
`status_indicator_widget.rs`, `shimmer.rs`, `summary_shimmer.rs`, `motion.rs`, `tui.rs`, `transcript_mode.rs`,
`insert_history.rs`, `transcript_reflow.rs`, `markdown_stream.rs`, `streaming/*`, `style.rs`, `ui_consts.rs`,
`bottom_pane/{chat_composer,pending_input_preview,approval_overlay,command_popup,footer,popup_consts}.rs`,
`history_cell/{exec,patches,messages,plans,separators,notices}.rs`, `exec_cell/render.rs`, `chatwidget/snapshots/*.snap`,
`config/src/types.rs`, and `protocol/src/config_types.rs`.

**Claude Code**: the installed binary is a Bun bundle (`~/.local/share/mise/installs/claude/2.1.325/claude`). I read its
embedded JS with a byte search, read-only. I also read the docs at `code.claude.com/docs/en/interactive-mode`.

**Hermes** (`ref-hermes/ui-tui/src`): `entry.tsx`, `theme.ts`, `components/{prompts,thinking,loaders}.tsx`,
`content/verbs.ts`, `i18n/en/overlays.ts`.

**Echo**: `apps/web/src/theme/tokens.css`, `lean/{LiveStatus.tsx,steps.ts,lean.css}`, `components/EchoFace.tsx`,
`dashboard/WelcomeTypewriter.tsx`, `apps/terminal/echospeak_cli/{render,repl,client}.py`,
`apps/backend/agent/lean/{approvals,settings,job,personas,loop,runtime}.py`, and `agent/lean/verification.py`.

**User terminal**: Omarchy's live theme is at `~/.local/state/omarchy/current/theme/`; `~/.config/omarchy/current/theme`
does not exist. The current theme is **lumon**. Its ANSI palette is entirely blue: `red=#4d86b0`, `green=#5e95bc`,
`yellow=#6fa4c9`, `blue=#6fb8e3`, `accent=#8bc9eb`. Ghostty includes that theme (`~/.config/ghostty/config` →
`config-file = ?"~/.local/state/omarchy/current/theme/ghostty.conf"`). Alacritty, kitty and foot are also themed.
Sessions run inside tmux (`TERM=tmux-256color`, `COLORTERM=truecolor`).

Omarchy also generates `claude.json` and `hermes.yaml` themes, so those apps re-theme with the desktop.
**Takeaway:** red and green cannot carry meaning on this machine. Glyphs and weight must carry it.

---

## 2. Side-by-side

| | Claude Code 2.1.325 | Codex CLI 0.169 | Hermes TUI |
|---|---|---|---|
| Rendering | Inline by default. Docs list an optional "fullscreen rendering" (`/tui`) | `tui.alternate_screen = auto\|always\|never`, default `auto` = alt screen with an *owned* transcript (`transcript_mode.rs`). In inline mode, history goes into real scrollback via escape sequences (`insert_history.rs`) | Alt screen on desktop. Clears the screen at start (`entry.tsx`: `\x1b[2J\x1b[H\x1b[3J`) |
| Composer | Dim horizontal rules above and below. Session name right-aligned on the top rule. `❯ ` prompt, dim placeholder `Try "…"` | No border. Full-width bg tint (fg/bg blend) with padding rows. `›` bold, `›` dim when disabled, `!` light-red in shell mode. Placeholder `Ask Codex to do anything`. Pastes over 1000 chars become `[Pasted Content N chars]` (`chat_composer.rs` `LARGE_PASTE_CHAR_THRESHOLD`) | Ink text input, skinnable prompt glyph |
| Spinner glyph | `· ✢ * ✶ ✻ ✽` (on `xterm-ghostty`, `✳` replaces `*`), played forward then reversed. Main spinner: frame = `round((1−cos(2πt/2000))/2 · 5)`, a 2 s breathing ease. Sub-spinners: `floor(t/120) % 12`, 120 ms. Reduced motion: `●` pulsing over 2000 ms. Stall: glyph blends toward `rgb(171,43,63)` | Status row `•`, shimmered in truecolor, otherwise blinks `•`/`◦` every 600 ms (`motion.rs`). Loading glyph braille `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏` at 100 ms. Redraws every 32 ms while animating, otherwise every 1000 ms | Random braille animation per turn (`thinking.tsx` THINK: helix, breathe, orbit…; TOOL: cascade, scan…) |
| Status text | `{glyph} {Verb}… ({elapsed} · ↓ {n} tokens · thinking)`. Escalates "still thinking" (10 s) → "thinking more" (20 s) → "thinking some more" (30 s) → "deep in thought" (45 s). Then "thought for Ns". About 180 whimsical verbs (Pondering, Reticulating, …) | `• Working (0s • esc to interrupt)`, plus optional `└` details (≤3 lines). Elapsed shows `0s`, `1m 00s`, `1h 00m 00s` | Verbs: pondering, contemplating, musing… |
| Shimmer | Glimmer index moves one cell per 200 ms (50 ms while "requesting"), padding 10. Band = index ±1 in `shimmerColor` | `summary_shimmer.rs`: waits 600 ms, then sweeps for 1 s every 4 s. Half-width max(3, 10 %). Truecolor fg/bg blend 0.5→1.0, static dim otherwise | Block loaders with a 7-cell band |
| Message glyphs | `●` (Linux) / `⏺` (macOS) before assistant text and tools. Results `  ⎿  ` dim. Thinking `∴` | `• ` dim before agent text. User `› `. `• Ran cmd` with a green/red bullet by exit code. `  └ ` output. `■ Conversation interrupted` | `●` tool bullet in the `tool` colour |
| Tool output | Collapsed. `ctrl+o` opens the transcript viewer | Head and tail, `TOOL_CALL_MAX_LINES = 5`, `… +N lines (ctrl+t to view transcript)`. `• Explored └ Read x / Search y` | Collapsible accordions |
| Diffs | Coloured diff bg (`diffAdded`…) | `• Edited f (+a -r)`, green/red, line-numbered | `diffAdded/Removed(Word)` theme slots |
| Approvals | "Do you want to proceed?" `❯ 1. Yes / 2. Yes, and don't ask again… / 3. No, and tell Claude what to do differently (esc)`. Guards against stray clicks and too-short terminals | "Would you like to run the following command?", Reason, `$ cmd`, `› 1. Yes, proceed (y)`, `2. Yes, and don't ask again for commands that start with … (p)`, `3. No, and tell Codex what to do differently (esc)`, "Press enter to confirm or esc to cancel" | "⚠ approval required": Allow once / Allow this session / Always allow / Deny / **No, and tell Hermes what to do instead**, which steers the text and then sends a plain deny. Esc = deny, `v` pages the command |
| Slash menu | Above the input, `❯` marks the selection, name and description in two columns | Above the composer, `MAX_POPUP_ROWS = 8` | Overlay hubs |
| Footer | Mode line `⏵⏵ auto mode on (shift+tab to cycle)`, `? for shortcuts` | `model effort · cwd` and `? for shortcuts`. `N% context left`. While running: `tab to queue message` | Status rule with model, usage gauge, session |
| Interrupt / queue / steer | Esc interrupts and sends what is queued. Enter queues, delivered after the current tool calls. `ctrl+enter` sends now. ↑ takes it back | Esc interrupts. Enter steers (sent after the next tool call). Tab queues (`↳ …`, `⇧← edit last queued message`) | Queued-messages panel. Steering via "tell" |
| Colours | Named theme roles (`claude`, `promptBorder`, `permission`…). The capture uses 256-colour and ANSI 34 | ANSI palette for semantics (`style.rs` `status_style`: Green, Red, Yellow; yellow is dropped on light or unknown bg). Truecolor accent `#63A8F8`. Blends using the terminal's real fg/bg | Skins (`theme.ts` ThemeColors). Omarchy ships `hermes.yaml` |
| Resize | Ink reflows the live area | Rebuilds scrollback from source on width change (`transcript_reflow.rs`) | Owns the screen |

---

## 3. The Echo TUI spec

### 3.1 Principles
1. **Inline, never alt screen.** Finished output goes into real terminal scrollback, so tmux copy mode, search
   and `less` all work. Only a small live region at the bottom is redrawn.
2. **Echo's voice for status.** Reuse the GUI's honest wording (`LiveStatus.tsx`, `steps.ts`) in place of random verbs.
3. **Monochrome plus one accent.** This matches the web tokens (`--es-accent #4c72df`, greys `--es-text-*`).
   Meaning comes from glyph and weight, never hue alone.
4. **Tool success ≠ task verified.** The end of a run states the verification level explicitly.

### 3.2 Layout (top → bottom)

```text
┊ … terminal scrollback (immutable): banner, user turns, agent blocks, tool lines, outcomes …
┊
├─ LIVE REGION (prompt_toolkit, redrawn) ──────────────────────────────────────
│ [stream tail]     ≤ 8 lines of the answer block still streaming (uncommitted markdown)
│ [running steps]   one line per running tool (≤ 3, then "+N more")
│ [status line]     spinner · activity · (elapsed · tokens · esc to stop)
│ [pending input]   steers/queued follow-ups (≤ 3 lines)
│ [popup]           slash/@ menu (≤ 8 rows)   — OR — approval dialog (replaces composer)
│ [composer]        top rule (title right) · › input (1–8 rows) · bottom rule
│ [footer]          status items left · hints right   (1 line; 2 when a mode banner is on)
└──────────────────────────────────────────────────────────────────────────────
```
When idle, the live region is just the composer and footer: 4 lines.

### 3.3 Welcome banner (printed once into scrollback)

Echo's face is a light rounded square with two tall pill eyes (`EchoFace.tsx`, `.lm-face`). The terminal version is
three rows of block characters. The face uses the terminal's default foreground in bold. The eyes are background gaps.

```text
 ▗▄▄▄▄▄▖  EchoSpeak  v0.11 · terminal
 ▐█ █ █▌  Echo · Personal agent · gpt-5.2 (OpenAI)
 ▝▀▀▀▀▀▘  ~/Work/echospeak · project echospeak · ask for risky actions

 Build a feature, fix a bug, or explore your code.
 /help for commands · ? for shortcuts · ctrl+o for details
```
- Line 1: `EchoSpeak` in bold, version in dim. Line 2: active agent name in bold, title, model. Line 3: cwd (home shown as `~`),
  project, approval mode. The tagline is a WelcomeTypewriter phrase (`CODE_PHRASES[0]` in project sessions,
  `PHRASES[0]` in chat, `AGENT_PHRASES[0]` for agent sessions). It is printed whole; scrollback is not animated.
- When resuming, add one dim line: `Resumed "Fix parser" · 14 messages · last active 2h ago`.
- Below 60 columns, use a single line: `● EchoSpeak v0.11 · Echo · gpt-5.2`.

### 3.4 Composer and footer

```text
──────────────────────────────────────────────────────────── Fix the parser ─
› Build a feature, fix a bug, or explore your code.▏
──────────────────────────────────────────────────────────────────────────────
  gpt-5.2 · Echo · echospeak · ◇ ask for risky actions          ? for shortcuts
```
- **Rules:** `─` in dim (ANSI bright-black). The top rule carries the session title right-aligned,
  `─ {title} ─`, in dim, matching Claude's capture. We use rules rather than a box so copying input never picks up side borders.
- **Prompt:** `› ` in bold accent. Dim when input is disabled. Shows `⠿ ` while an approval dialog has focus.
- **Placeholder:** dim, shown only while the buffer is empty. When idle with an empty buffer, it types on
  using the GUI's timings: 34 ms per character, 2200 ms hold, 18 ms per character deleting, 350 ms before the next phrase.
  It stops on the first keystroke. Reduced motion shows phrase 0 statically.
- **Multiline:** Enter submits. `\`+Enter, Ctrl+J and Alt+Enter insert a newline (prompt_toolkit cannot tell
  Shift+Enter apart, so we don't advertise it). Height grows to 8 rows, then scrolls.
- **Paste:** bracketed paste is atomic. Pastes over 1000 chars or 12 lines become a chip
  `[Pasted 2,431 chars · 58 lines]`, which is expanded on submit. Pastes never auto-submit.
- **History:** ↑/↓ on the first or last row recalls history (FileHistory at `~/.local/state/echospeak/history`). Ctrl+R searches.
  Esc Esc clears the draft and saves it to history.
- **Footer, left:** `model · agent · project · approval mode`, plus `context 64%` once usage passes 50 % (bold
  at ≥ 85 %). Context = last `token_usage.prompt / context_limit` (`loop.py:708`). Also `⏸ 2 held` if approvals are held.
  **Right:** `? for shortcuts` when idle. While running: `esc to stop · enter to steer · tab to queue`.
- **Mode line** (an extra footer row shown only when the mode is not the default), in the style of Claude's `⏵⏵`:
  `  ◆ autonomous: routine approvals won't interrupt (shift+tab to cycle)` in accent.

### 3.5 Live status (spinner)

**Glyph: the Echo ping.** A ripple that grows and fades, standing for Echo's name. It replaces Claude's star.
```
FRAMES = ["·", "•", "●", "◉", "◎", "○", "◌", "◌"]   # 120 ms each → 960 ms cycle
style:    accent-bold ×4 (0–3), accent ×3 (4–6), dim (7)
frame = int(monotonic_ms / 120) % 8               # time-based, never tick-counted
```
- ASCII fallback (`ECHO_TUI_ASCII=1`, or when the locale is not UTF-8): `. o O 0 O o . .`.
- Reduced motion (`ECHO_REDUCED_MOTION=1`, or `--no-anim`): a static `●` in accent. No shimmer. Elapsed updates at 1 Hz.
- Stall: when no event has arrived for 10 s, the glyph goes dim and ` · waiting on the model` is appended.
  At 30 s the text turns bold: `· still waiting (Ns)`.

**Text template:**
```text
◉ Echo is thinking it through… (14s · ↓ 1.2k tokens · esc to stop)
◎ Searching the web · "lumon theme omarchy" (3s · esc to stop)
● Echo needs your OK                                   ← no spinner; attention style
```
- The activity comes from `describeLive`/`activityOf` (`LiveStatus.tsx`) and `phaseOf` (`steps.ts`). Port these
  as pure functions:
  "Choosing who should answer", "Connecting to the model", "{Name} is starting", "{Name} is thinking it through",
  `{tool label} · {detail}`, "Looking over the results", "Checking the output", "Working out what went wrong",
  "Reading through it", "Checking the changes", "Waiting on the team", "Deciding what to do next",
  "{Name} is writing the answer". For several agents at once: "Echo and Jarvis are working". For a handoff:
  "Echo → Jarvis · …".
- Thinking escalation (after Claude): "thinking it through" → 20 s "still thinking" → 45 s "deep in thought".
- Echo flavour: when nothing more specific is known for more than 2 s, rotate one of `Listening…`, `Tuning in…`,
  `Sounding it out…`, `Picking up the signal…`, `Reverberating…`. Each holds for 4 s. `ECHO_TUI_VERBS=plain` turns this off.
- Elapsed shows `0s`, `59s`, `1m 05s`, `1h 02m 03s` (Codex `fmt_elapsed_compact`). Tokens show `↓ 1.2k tokens` (completion tokens from `token_usage`).
  `(esc to stop)` stays in a fixed position. Optional parts go before it and are dropped first when space runs out.

**Shimmer** (activity text only, after the GUI's `.lm-shimmer`, which uses a 1.8 s `lm-sheen`):
- The band centre moves left → right across `len + 2·h` cells in 1.8 s, looping, with `h = max(3, 0.1·len)`.
  Intensity is `t = ½(1+cos(π·d/h))`.
- **Default (any terminal):** three attribute levels, as in Codex `color_for_level`: `t < 0.2` → dim,
  `< 0.6` → normal, else bold. This follows the Omarchy palette without any colour queries.
- **Truecolor option** (`COLORTERM=truecolor` and the fg/bg are known): blend fg into bg at alpha `0.45 + 0.55·t`.
- Repaint interval: 50 ms while shimmering, otherwise 120 ms. Nothing animates while idle.

### 3.6 Message rendering (scrollback)

```text
› Fix the date parser and make sure the tests pass                 ← user (› dim-bold, text normal)

● Echo                                                             ← agent header (only when >1 agent in run,
  I'll look at the parser first.                                      or the agent changed); else "● " inline
  ✓ Read 2 files · src/dates.py, tests/test_dates.py          0.1s
  ✓ Ran a command · pytest -q tests/test_dates.py             2.3s
    ⎿  2 failed, 14 passed
       FAILED tests/test_dates.py::test_iso_week - AssertionError
       … +18 lines (ctrl+o for details)
  ✓ Changed 1 file · src/dates.py  (+6 −2)
  ✗ Searched the web · "iso week 53"  timeout after 15s
  ∴ Thought for 8s
  The bug was the ISO week rollover. …markdown answer…

● Jarvis · Researcher                                              ← teammate (header colour per agent)
  …

↳ You added: also handle week 53                                   ← steer_applied
→ Echo handed work to Jarvis: compare libraries                    ← delegation
─ Earlier steps summarized to fit the context window ─             ← context_compacted (dim rule)

◆ Done: Fixed ISO week rollover; tests updated.                    ← run_outcome
  ≡ Checks pass: pytest 16 passed (tests/test_dates.py)            ← work_state.work.verification
── Worked for 42s · Read 2 files · Ran 2 commands · Changed 1 file ──
```
- **User:** `› ` dim-bold, text in the default colour. In truecolor mode, an optional full-width bg tint (fg/bg blend at 8 %).
- **Assistant:** markdown via rich, indented 2. Headings are left-aligned (override rich's centred h1). Code blocks use
  `code_theme="ansi_dark"`/`"ansi_light"`, so syntax colours come from the terminal palette. No panel borders around code.
- **Agent headers** (`agent_start`): `● Name` in bold plus ` · title` in dim. Bullet colour: Echo = default fg bold (Echo's
  light face), Jarvis = ANSI blue, Glados = ANSI magenta, custom agents = a stable hash into {cyan, green,
  yellow, blue}. Names stay readable in mono because the name is always printed.
- **Tools** (`tool_start`/`tool_end`): while running, the tool sits in the live region's step list (with spinner).
  When it finishes, one line goes to scrollback: `✓`/`✗`/`⊘` (denied)/`⏸` (held) + `done_label` + ` · ` +
  `one_line(detail)`, with the duration right-aligned and dim. Labels use the GUI wording from `steps.ts` `bucketOf`.
  - Output (`⎿` continuation, dim, indent 4): for commands, the last 3 lines. For failures, the first error line plus
    3 lines. For everything else, only the `summary`. Over-limit output ends with `… +N lines (ctrl+o for details)`.
    Line width is truncated with `…`.
  - `✗` lines are bold. `✓` lines are dim. On mono and blue themes that weight difference is the signal.
- **Thinking:** collapsed to `∴ Thought for 8s` in dim italic. With `/verbose` on, the reasoning is printed dim under it.
- **Errors:** `✗ {message}` bold, with an error colour where it reads. Interrupts: `■ Stopped: you stopped it`.
- **Answer revised** (`text_replace`): `↺ Answer revised` dim, then the new block.
- **Notes** (`claim_unverified`, `verification_nudge`, `answer_note`, `security_note`): `! {note}`, bold.
- **Outcome / verification.** This must be explicit:
  - `run_outcome.status == "done"` → `◆ Done: {summary}`. The `◆` is accent **only if** verification ∈ {Verified, Checks pass}.
  - Otherwise `◇ Done (not verified): {summary}` in bold, followed by `≡ {label}: {detail}`, where the labels come from
    `verification.STATUS_LABELS`: Verified / Checks pass / Partly verified / Not verified: the last check failed /
    Not verified / Blocked.
  - `stopped` → `■ Stopped: {reason}`. `job_continue` → `… Not done yet: {reason}. {Agent} continues.`
  - Every tool showing ✓ never implies Done. Only `run_outcome` plus verification says that.
- **Turn separator** (Codex `Worked for`, GUI `activityHeadline`): a dim rule
  `── Worked for 42s · {stepsSummary} ──`, or `Thought for 8s` when no tools ran.
- **Task board** (`task_board`): `○ Jarvis → research X` / `◉` in progress (accent) / `✓` done (dim, struck through).

### 3.7 Approval dialog (replaces the composer; the run keeps streaming above it)

```text
──────────────────────────────────────────────────────── Echo needs your OK ─
 Run a terminal command  ·  1 of 2
 $ pytest -q tests/test_dates.py
 in ~/Work/echospeak
 Why: check the fix before calling it done

 ❯ 1. Yes, allow once                                          (y)
   2. Yes, and allow terminal for this chat                    (c)
   3. Yes, always allow: commands in this project              (a)
   4. No                                                       (n)
   5. No, and tell Echo what to do instead                   (esc)

 ↑↓ choose · enter confirm · ctrl+g full arguments
```
- The rule and title use the attention style (bold, accent). The body is `summary`, then `args` (sanitized, ≤ 12 lines,
  `… N more lines · ctrl+g`, where Ctrl+G opens the full JSON in the pager), then `reason`.
- Mapping to `POST /lean/approvals/{id}` (`approvals.py:391`): 1 → `allow`. 2 → `always`, shown only if `grantable`.
  3 → `persist`, shown only if `persistable` and `scope_label` (label = `scope_label`). 4 → `deny`.
  5 → opens an inline field `Tell Echo what to do instead: ▏`. Enter sends the steer (`POST /query/steer`) and then
  `deny`, in the same order as Hermes. An empty Enter, or a second Esc, sends a plain `deny`. Recovered
  approvals show only 1, 4 and 5 (`repl.py decision_choices`).
- Keys: digits pick directly, y/c/a/n are shortcuts, ↑↓ then Enter. **Input guard:** keys are ignored for 300 ms after
  the dialog appears, so typing ahead never approves anything (after Claude's stray-click guard).
  Nothing ever defaults to allow.
- Questions (`kind == "question"`): title `Echo is asking`, numbered options, plus `Other (type your answer)` when
  `allow_other`. Enter sends `answered`.
- If the terminal is under 12 rows, show only the title and options, with `ctrl+g` to view.
- After a decision, print to scrollback: `  ✓ Allowed once: pytest -q …` / `  ⊘ Denied` / `  ↳ You said: …`.
- `approval_held` (autonomous mode): scrollback `⏸ Held for you: {tool}: {summary}`, footer `⏸ N held · /approvals`.

### 3.8 Slash commands

Menu: opens when `/` is the first character. Shown above the composer, ≤ 8 rows, two columns
`❯ /name   description` (selected row in bold accent, others with a dim description). Filter is prefix-first, then
substring. Tab completes, Enter runs, Esc dismisses until the token changes. During a run, a command runs immediately
if it is marked **live**. Otherwise it is held and runs after the turn.

| Command | Action (backend) |
|---|---|
| `/help` | Commands and keys |
| `/new [--agent ID]` | New session on the next message |
| `/resume [id]`, `/sessions` | Picker or list (`/threads`); follows a running run |
| `/agents`, `/agent [ID\|-]` | List agents; answer with an agent in this session |
| `/model [id]` | Picker from `/settings/catalog`; set via `PUT /settings` (global; say so) |
| `/project [PATH\|ID\|-]` | Attach a project (`/projects/attach-folder`) |
| `/mode [chat\|code]` | Switch session kind (persona Chat/Code in `personas.py`) |
| `/permissions [smart\|autonomous\|always\|never]` | `PUT /settings {lean_approval_mode}`. Global, so confirm first. `never` always needs a typed confirmation |
| `/approvals` **live** | Answer pending approvals, one dialog each |
| `/memory [query]` | Search or list memory |
| `/status` **live**, `/usage` **live** | Backend, runs, approvals / session token totals and context % |
| `/transcript` **live** (= ctrl+o) | Full session in the pager (`less -R`) |
| `/verbose` **live** | Toggle expanded tool output and thinking for future blocks |
| `/copy` **live** | Copy the last answer (OSC 52; works through tmux with `set-clipboard on`) |
| `/clear` | Clear the screen and start a new session. Scrollback stays in tmux |
| `/stop` **live**, `/detach` **live**, `/quit` | Cancel run / stop following (run continues) / exit |

`@` opens path completion relative to the project root, which inserts a path. The backend reads it.

### 3.9 Key bindings

| Key | Idle | Running |
|---|---|---|
| Enter | Submit | **Steer** (`/query/steer`). On 400/409, falls back to queue (current `repl.py` behaviour) |
| Tab | Complete (menu) | Queue as follow-up (`/query/queue`) |
| Esc | Close menu; Esc Esc clears draft | **Stop** (`/stop`). Queued items stay queued |
| Ctrl+C | Clear input; again within 2 s exits | Stop; again within 2 s **detaches** |
| Ctrl+D | Exit when empty | Detach when empty |
| Shift+Tab | Cycle approval mode smart → autonomous → always (ask everything) → smart. `never` is not in the cycle | Same |
| ↑ / ↓ | History (from first/last row) | ↑ on empty input pulls back the last queued message |
| Ctrl+O | Transcript pager | Same |
| Ctrl+R | History search | Same |
| Ctrl+G | Edit draft in `$EDITOR` / full args in dialog | Same |
| Ctrl+L | Redraw the live region | Same |
| Ctrl+Z | Suspend | Same |
| `?` (empty input) | Toggle shortcut list (2–3 columns above the composer, like the captures) | Same |

### 3.10 Colour mapping

ANSI palette by default, so every Omarchy theme applies automatically. The truecolor accent is opt-in:
`ECHO_TUI_ACCENT=#4c72df|echo|auto`, where `auto` reads `accent` from the Omarchy `colors.toml` if present.

| Role | ANSI default | Truecolor (`echo`, dark / light) |
|---|---|---|
| text | default fg | — |
| dim (hints, rules, tool ✓ lines, durations) | `dim` (SGR 2). Rules use bright-black (90) | `#888891` / `#70707b` |
| accent (prompt, ping, selection, ◆) | blue (34) + bold | `#4c72df` / `#2f7df2` |
| attention (approval title, held, warn) | bold + yellow (33) | `#d6a955` / `#a86a00` |
| error (✗, failures) | bold + red (31) | `#e5484d` / `#d63c3c` |
| success (◆ verified, ✓ in pager) | green (32), never the sole signal | `#41c77a` / `#14955a` |
| agent: Echo / Jarvis / Glados | default bold / blue / magenta | — |
| code | rich `ansi_dark`/`ansi_light` | — |

All roles keep their glyph and weight when there is no colour (NO_COLOR, mono, or lumon's all-blue palette).

### 3.11 Degradation
- **Not a TTY** (pipe or file): use the existing `render.py` reducer, which is unchanged. No live region, no animation.
- **`NO_COLOR` set:** no SGR colours. Bold, dim, reverse and all glyphs remain. **`TERM=dumb`:** plain line REPL (current `repl.py`).
- **Any width or height:** superseded by §6 (16 November 2026). The fixed cliffs that were here (footer reduced to
  `model · context%` under 60 columns, `glyph Working… esc` under 40, a 3-line tail under 12 rows) are replaced by
  width tiers, step-by-step degradation ladders and a height budget for the live region.

### 3.12 Implementation sketch (prompt_toolkit 3.0.4x + rich 13)

New dependencies, added as an optional extra so that the stdlib REPL stays available as a fallback.

```python
app = Application(layout=Layout(HSplit([
        ConditionalContainer(Window(FormattedTextControl(tail_text), height=D(max=8)), has_tail),
        ConditionalContainer(Window(FormattedTextControl(steps_text), height=D(max=4)), has_steps),
        ConditionalContainer(Window(FormattedTextControl(status_text), height=1), is_running),
        ConditionalContainer(Window(FormattedTextControl(pending_text), height=D(max=4)), has_pending),
        ConditionalContainer(Window(FormattedTextControl(menu_text), height=D(max=8)), menu_open),
        ConditionalContainer(approval_container, approval_open),
        ConditionalContainer(composer_container, ~approval_open),   # rule, Window(BufferControl), rule
        Window(FormattedTextControl(footer_text), height=D(min=1, max=2)),
      ])),
      key_bindings=kb, full_screen=False, mouse_support=False, erase_when_done=False)
app.ttimeoutlen = 0.05      # lone Esc is recognised in 50 ms, not 500 ms
with patch_stdout(raw=True):  # print() of ANSI goes above the live region (internally run_in_terminal)
    await app.run_async()
```
- **Printing to scrollback:** a single `Printer` coroutine takes finished blocks from an `asyncio.Queue`. It renders
  each with `Console(file=StringIO(), force_terminal=True, color_system=…, width=app.output.get_size().columns,
  markup=False, emoji=False, highlight=False)` and writes the result with one `sys.stdout.write`. `StdoutProxy` batches
  output and erases and redraws the live region once per flush. Emit only whole lines.
- **Streaming markdown:** the newline-gated idea from Codex `markdown_stream.rs`, at block level. Accumulate
  `agent_token` text per `message_id`. **Commit** a block to scrollback when it is closed: a blank line outside
  a ``` fence, or a closing fence. Render the uncommitted remainder as plain text in the live tail (last 8 lines).
  Flush everything on `agent_done`. Throttle tail invalidation to 30 Hz.
- **Animation:** one ticker task, `while running: app.invalidate(); await asyncio.sleep(0.05 if shimmer else 0.12)`.
  Every frame derives from `time.monotonic()`. prompt_toolkit diffs screen cells, so only the status cells repaint and
  there is no flicker. Stop the ticker when idle.
- **Events:** keep the sync `client.follow()` and run it in `asyncio.to_thread`. Each event is handed over with
  `loop.call_soon_threadsafe(events.put_nowait, evt)`. A reducer, refactored from `Renderer._feed`, returns
  `(scrollback_blocks, live_state_changes)`. Never touch `app` from the thread.
- **Input during runs:** the composer is always focused. Enter or Tab is routed by `is_running` (see 3.9). Pending items
  are listed `↳ text` under the header "Sent at the next checkpoint" / "Queued for after this turn".
- **Resize:** prompt_toolkit redraws the live region. Scrollback is **not** reflowed. Codex's rebuild clears
  history, which would wipe tmux scrollback.
- **Pitfalls:**
  1. Never pass server text through rich markup. Use `markup=False` and `Text()`, and still run `sanitize()` first.
  2. rich's centred h1 needs a custom heading element.
  3. Pass the width explicitly; rich mis-detects it under `patch_stdout`.
  4. Ambiguous-width glyphs (`● ◉ ◎ ○ ◌ ∴`) assume narrow width. Offer `ECHO_TUI_ASCII=1`.
  5. Printing while a menu is open is fine, but keep menus inside the HSplit (Floats misbehave in non-fullscreen).
  6. Throttle printing to at most ~20 flushes per second. Each flush redraws the live region.
  7. Esc versus Alt sequences: keep `ttimeoutlen` low, and bind Esc with `eager=True` only in the dialog.
  8. Optional: wrap flushes in synchronized output (`CSI ?2026 h/l`) via a `Vt100_Output` subclass. Measure in tmux first.

---

## 4. Deliberately not copied
- **Alternate screen or owned transcript** (Codex default, Hermes): it breaks tmux scrollback, search and copy, which
  this owner relies on. Codex's scrollback rebuild on resize is out for the same reason.
- **Whimsical verb firehose** (Claude's ~180 verbs, Hermes's random spinner per turn): it hides what is actually
  happening. Echo's status reports real state, with a small optional flavour set.
- **Hue-only semantics** (Codex green/red bullets): on the user's lumon theme, red and green are both blue.
- **`!` local shell mode:** the terminal client executes nothing (`apps/terminal/README.md`). Commands go through the agent
  and its approvals.
- **Bypass or "yolo" in the Shift+Tab cycle:** `never` needs an explicit, typed `/permissions never`.
- **Promising Shift+Enter, vim mode, voice strip, pets, effort ignition, agent command center:** not supported by
  prompt_toolkit, or with no backend counterpart, or too much chrome.
- **Large logos and tips banners:** a 3-row face and one tagline is enough.
- **Defaulting any dialog to allow** or accepting keys typed before the dialog appeared.

---

## 5. Implementation record (14 November 2026)

**Built.** `echospeak` opens the Echo TUI when stdin and stdout are terminals and `prompt_toolkit`/`rich` are
installed (`apps/terminal/install.sh` → `apps/terminal/.venv`, pinned with hashes in `requirements.txt`). The
plain REPL stays as `--plain` and the automatic fallback; every subcommand is unchanged.

| Part | Where |
|---|---|
| Event reducer (port of `liveReducer.ts`, `steps.ts`, `LiveStatus.tsx` wording; outcome + verification) | `tui/state.py` |
| Spinner, shimmer, typewriter, status line and footer at every width | `tui/motion.py` |
| Scrollback blocks rendered with rich (`markup=False`, sanitized) | `tui/blocks.py`, `tui/mdstream.py` |
| Approval / question / confirm / picker dialogs (300 ms guard, nothing preselected) | `tui/dialog.py` |
| Slash command registry and menu filter | `tui/commands.py` |
| prompt_toolkit app, key bindings, backend calls | `tui/app.py` |
| Shared backend (desktop data folder, detached start, `backend status/start/stop`) | `echospeak_cli/backend.py` |
| Models (Ollama tags + `nvidia-smi` fit hints), permissions, approval modes | `echospeak_cli/control.py` |

**Decisions made while building.**
- prompt_toolkit's inline renderer claims every row below the cursor and never shrinks. `EchoTUI._fit_live_region`
  ignores the CPR height and erases once when the region gets shorter, so the live region is exactly as tall as
  its content and closed menus/dialogs leave nothing behind.
- The banner is printed before the app starts, so the live region never begins at row 0: tmux copies the screen
  into history when it is erased from the top-left corner (`scroll-on-clear`).
- Background tasks are created in `pre_run`, inside the app's context; tasks created earlier make `in_terminal()`
  a no-op and printed blocks overwrite the live region.
- Approvals open with nothing highlighted: Enter does nothing until a choice is made (stricter than §3.7's mock).
- A Ctrl+C that stopped a run does not arm "press again to exit".
- The end-of-turn line reads `Worked for …` (tools ran) or `Answered in … · thought for …`. Without a
  `run_outcome`, a `work_state` record still gets an outcome line: `◆ Done · Verified` only for
  Verified/Checks pass, otherwise `◇ Finished (not verified)` with the label (`observed` → "steps ran, but no check
  measured the result").
- The backend's `verification` notes are written for the model; the owner sees the first sentence, dim.
- Single line breaks in answers are kept (hard breaks outside code fences), so poems and lists of lines read as
  written.
- First-run model setup asks until a model is confirmed *in the terminal* for that data folder
  (`~/.local/state/echospeak/terminal.json`), because the backend auto-selects the first Ollama model on start.

**Verified.**
- Unit tests: `apps/terminal/tests/test_tui.py`, 46 tests (reducer, wording, spinner/shimmer/typewriter math,
  width fallbacks, approval mapping and guard, sanitizing incl. `[bold red]` / `[link=…]`, model hints).
- Integration: `test_integration.py::test_tui_in_a_real_pty_round_trip` drives the TUI in a pseudo-terminal
  against the real backend (scripted model). All terminal tests: 72 passed (TUI venv) and 30 passed (backend venv).
- Live, in tmux at 120×40 against a real backend on an isolated data folder with `qwen3.5:9b` (captures kept with
  the job, not in the repository): first-run picker, slash menu, streaming tail, tool run with approval,
  deny with "tell Echo", steering, Tab queue, Esc stop, detach and `/resume` following a running task,
  `/model`, `/agents` + Jarvis, `/permissions` with confirmation, Shift+Tab cycle, autonomous held approval →
  `/approvals` → resume offer, question dialog, paste chip, Ctrl+R, `/clear`, 70 columns, `NO_COLOR`, ASCII,
  the no-dependency fallback, and the detached backend surviving its terminal. Screenshots:
  `artifacts/media/echo-tui/`.

**Not done / known gaps.**
- After a denial the backend blocks the work (`PermissionError: Work is blocked`), so "tell Echo what to do
  instead" is delivered (`↳ You added`) but the run still ends; the TUI reports it as
  `⊘ Stopped: you denied a step it needed`.
- `POST /lean/work/{id}` (resume after a held approval) runs without streaming; the TUI shows only its final reply.
- Scrollback is not reflowed on resize (by design, §3.12); lines printed at a wider width wrap.
- `/mode` switches `assistant_mode` (unified/environments) instead of §3.8's chat/code persona switch.
- No `@` path completion, no `$EDITOR` round-trip test, no truecolor fg/bg-blend shimmer (attribute levels only).

---

## 6. Any terminal size (16 November 2026)

**Goal.** The live region and scrollback look deliberate at every size from a split pane to an ultrawide window,
keep the same information in the same places (model, context, mode, what is running), and never leave debris
behind when the window is resized.

### 6.1 What was broken (measured before changing anything)

| Check | Before | After |
|---|---|---|
| Headless layout matrix: 11 UI states × 21 widths (8–240) × 11 heights (4–60) | **1,421 of 2,541** cases broken | **0 of 2,541** |
| Real TUI in tmux, shrink → grow sequence (`tests/sizelab.py`) | stale rule rows left above the composer after every narrowing (1 → 3 rule-only lines) | 1 rule-only line at every step, including mid-stream |
| Scrollback blocks at widths 8–140 | every block type over-wide under 20 columns | all fit |

Causes, from source inspection:
1. **Resize ghosts.** prompt_toolkit erases the old live region by moving up the number of rows it drew. Terminals
   that re-wrap lines on resize (kitty, ghostty, foot, alacritty, wezterm, VTE, tmux) have already turned each
   full-width rule into 2–3 rows, so the erase stops short and the old rules stay on screen.
2. **No height budget.** Tail (8) + steps (4) + pending (8) + menu (8) + dialog + composer (10) + footer (2) could
   exceed the terminal; prompt_toolkit then clips the region (or prints "Window too small").
3. **A hard 20-column floor.** `width()` and `blocks.render` used `max(20, width)`, so narrower panes got lines
   the terminal wrapped again.
4. **Cliffs instead of degradation.** The footer dropped agent, project, mode and context together under 60
   columns, the status line became `Working… esc` under 40, and long notices or the "Queued for after this
   turn" header were never cut.

### 6.2 Design

- **Width tiers** (`tui/layout.py`): micro < 30, narrow < 60, regular < 100, wide ≥ 100. They replace scattered
  thresholds; inside a tier everything degrades one step at a time.
- **Height budget.** The live region gets `rows − 1` rows (it must not start at row 0, see §5). Every section asks
  for a natural and a minimum height with a priority, and `layout.allocate` hands out rows: minimums by priority,
  then growth by priority, then decorations (blank spacer, mode line) from leftovers only.

  | Priority | Sections |
  |---|---|
  | 0 | the composer input · an open dialog (rule, first body line, every option up to 6, hint) |
  | 1 | status line · footer · history search |
  | 2 | slash menu (≥ 3 rows, scrolled to the selection) · shortcuts |
  | 3 | pending steers / queued messages (collapses to `↳ 2 to steer · 1 queued` + newest) |
  | 4 | running steps (collapses to `N steps running`) |
  | 5 | the composer's rules |
  | 6 | stream tail (newest lines) |
  | 7, 9 | mode line, blank spacer: leftovers only |

  Each prompt_toolkit window takes exactly its planned rows; text is cut or wrapped by us, never by
  prompt_toolkit (`wrap_lines=False` on the dialog). The plan is computed once per frame and dropped after it.
- **Dialogs fit their rows.** Options first (scrolled to the selection, `… N more · ↑↓ to see them`), then the
  title rule, then the body: the stacked diff shrinks before the text, the first line (the question, or "Edit a
  file") is kept whole on one line, a command's `$ …` line is kept at two rows, otherwise `… ctrl+g for
  everything`. The blank line above the hint is the first thing to go. Option keys `(y)` drop per option only
  when that option's label would otherwise be cut.
- **Footer ladder** (`motion.FOOTER_LADDER`). Items keep their order (`model · context · agent · project · mode ·
  held`) and shorten one step at a time: hints → short hints, project/agent cut, mode `◇ ask if risky`, project and
  agent hidden, held `⏸ 2 held`, hints hidden, `ctx 64%`, model cut to 16/10/6, mode `◇`, `64%`, `⏸2`. **Context is
  shown whenever it is known** (it used to appear only past 50 %), at every width from 12 columns. A notice
  outranks everything but the model and is cut, never dropped.
- **Status ladder** (`motion.status_line`). Drops tokens → detail → elapsed → shortens `esc to stop` to `esc` →
  cuts the activity. The real activity stays (`· Echo is thinking it t… (esc)` at 30 columns), never a generic
  "Working…".
- **Reflow-safe resize** (`EchoTUI._reflow_on_resize`). On a narrowing resize it measures the drawn length of every
  row of the old region (what prompt_toolkit actually wrote), computes how many rows they take after re-wrapping
  at the new width (`layout.reflowed_rows_above`), moves up that far and erases. Widening keeps prompt_toolkit's
  own erase. Terminals that crop instead of re-wrapping (plain xterm, the Linux console) are detected by
  `layout.terminal_reflows`; `ECHO_TUI_REFLOW=0/1` overrides the guess.
- **Scrollback at the real width.** No 20-column floor. Tool lines drop their indent and duration before cutting
  the label; previews use a 2-cell gutter under 30 columns; turn rules shrink to fit.
- **Readable measure.** Prose (answers, notes, thinking, outcomes) wraps at 100 columns on wider terminals; code,
  diffs, tables and tool lines keep the full width. `ECHO_TUI_MEASURE=0` turns it off, `=140` changes it. Besides
  readability, text printed at ≤ 100 columns survives narrowing to 100 without the terminal re-wrapping it.
- **Stream tail looks like the answer.** It word-wraps at the same measure and renders closed `**bold**`,
  `` `code` ``, `*italic*` and `#` headings (half-streamed markers stay literal; code fences stay literal), so a
  block no longer changes appearance when it is committed to scrollback.

### 6.3 Where

| Part | File |
|---|---|
| Tiers, budget, allocator, reflow arithmetic, reflow detection, measure | `apps/terminal/echospeak_cli/tui/layout.py` |
| Height plan, section renderers, dialog fitting, resize handler, tail markdown | `apps/terminal/echospeak_cli/tui/app.py` |
| Status and footer ladders, rule | `apps/terminal/echospeak_cli/tui/motion.py` |
| Scrollback width fixes | `apps/terminal/echospeak_cli/tui/blocks.py` |
| `tail.code` style | `apps/terminal/echospeak_cli/tui/theme.py` |

### 6.4 Verification (what was actually run)

- `tests/test_layout_matrix.py`: the real `EchoTUI` layout rendered headlessly into a W×H screen buffer for 11
  states (idle, autonomous mode, a 12-line draft, slash menu, shortcuts, a long notice, running, running with 6
  tools and 6 pending, approval with diff, command approval, question) × 21 widths × 11 heights, plus ASCII/no-colour.
  Invariants: the region fits `rows − 1`; no "Window too small"; no line wider than the terminal; the prompt is
  visible; the footer is visible from 8 rows (12 with a dialog open). `python tests/test_layout_matrix.py`
  prints a per-state report.
- `tests/test_layout.py` (allocator, reflow arithmetic, terminal detection), `tests/test_blocks_widths.py` (every
  block type at widths 1–140, UTF-8 and ASCII), updated footer/status tests in `tests/test_tui.py`.
- `tests/layout_gallery.py`: prints any state at any size for review by eye (used for every decision above).
- `tests/sizelab.py`: the real TUI in a private tmux server against the real backend and the scripted model, at
  120×40, 100×30, 80×24, 60×20 and 40×16; captures idle, slash menu, streaming, a resize mid-stream and back,
  the answer, then shrink to ½ and ⅓, back, half height, wider, back. Ghost counts as in §6.1.
- Suites: `apps/terminal/.venv`: 2,675 passed, 1 skipped. `apps/backend/.venv`: `tests/test_integration.py` 4
  passed (including the real-PTY TUI round trip).
- Test fixture fix: `tests/fake_model.py` reported `prompt_tokens: 10` for every request, so the backend's
  truncation check (`agent/lean/window.py`, added in 11.14) decided the server had cut the prompt and failed the
  run. Three integration tests failed for that reason, including two (`send --json`, attach replay) on code paths
  this branch does not touch. It now reports the prompt size counted the way the backend counts it.

**Not verified.** No screenshots in a real GUI terminal emulator yet. tmux reflows like kitty/ghostty/foot, but
ghostty and alacritty themselves should be checked by hand (drag-resize while a task streams). Plain xterm (no
reflow) was not tried. Scrollback printed before a resize is still not re-rendered (by design, §3.12/§4).



## 7. Short conversations and identity follow-up (16 November 2026)

The earlier content-height override in §6 put the composer immediately below the first messages. It now
preserves the cursor-position report's available height and grows an empty region above live content, keeping
input at the bottom. The rows-minus-one budget and responsive allocation remain. tmux's clear-screen behavior
requires per-line erasing during redraws so a vertical shrink does not copy the live input into scrollback.

A single running tool owns its live status; a separate status line is retained for stopping, approvals and
multi-agent coordination. Memory's intermediate save event is folded into the completed tool receipt. The
header follows an explicit legacy mode's selected model; unified mode calls the assistant Echo.

The architectural connection between model, identity, soul, skills, memory and permissions, plus verification
and source-versus-package update instructions, is recorded in [interfaces.md](interfaces.md#16-november-2026-identity-terminal-and-memory-follow-up).

## 8. Session manager (17 November 2026)

**←** on an empty input (or `/sessions`, `/resume` without an id) opens the session manager in the composer's
place. It follows Codex CLI's resume picker (a list by activity, keyboard filters, type to search, Enter resumes,
a position counter), adapted to named agents:

- each row shows who answers there, the project and the state;
- the filters are All / Echo / Agents / This project;
- the selected conversation's last exchange is shown under the list.

With text in the input, ← stays the cursor key. Esc clears a search, then returns; → returns. Shift+Tab steps the
filter instead of the approval mode while the list is open.

- **Code:** `tui/sessions.py` (model and drawing) and `tui/app.py` (`open_sessions`, `_browser_key`, `_switch`).
- **Data:** `GET /sessions/overview` (`agent/session_overview.py`), with a fallback to `/threads` on older backends.
- **Height:** the manager is priority 0 in the height plan, so the layout matrix covers it at every size.
- **Live check:** `tests/sessionlab.py` drives the real TUI in a private tmux against a real backend (14 checks,
  including that a message after resuming lands in the chosen conversation).

Design and results: [agent-first.md](agent-first.md) §4–§5.


## 9. Visual polish (18 November 2026)

Branch `tui-polish`. The method, the screenshots, the scorecard and the limits are in
[tui-visual-polish.md](tui-visual-polish.md). These parts of this spec are superseded:

- **§3.3 Banner.** Echo's face is now a square 14 × 6 quadrant face (`tui/avatar.py`). Its entrance plays in the live
  region and is then committed to scrollback; it is static under reduced motion, ASCII and small terminals. The
  tagline and hint lines are gone: the placeholder and footer already say them.
- **Startup.** Plain `echospeak` reopens nothing. The banner offers `Last here: … ← to pick it up`, and `-c` /
  `--continue` reopens. The plain REPL still resumes.
- **§3.4 Composer.** The prompt is `›` plus two spaces (`PROMPT_W = 3`), and sent messages keep that column.
- **§8 Session manager.** ← again opens the sessions page: grouped, fitted to its content, with the selected
  conversation's last exchange. Esc and → go back one level at a time.
- **§3.6 Message rendering.** Steps, decisions and failures are said once, failed or denied steps are not counted as
  work, and searches list their real sources.

The visual lab (`tests/uilab.py`, `tests/ansishot.py`) is the screenshot loop for the next round.


## 10. Settings and agent permissions in the terminal (18 November 2026)

**Cause.** The owner's session `f75dae98` (18 November, 22:27–22:31) asked Echo to build a Pong game in the project:

- Echo answered that file creation was turned off in settings.
- That was true: every action switch was off.
- "Write files" also needs the master switch "Actions on this computer".
- The terminal never said which switch, or how to turn it on.
- `/permissions` changed one switch at a time and allowed "Write files: on" while the master switch was off. The
  backend flags that state as an error and blocks the switch anyway.

**What the terminal does now:**

- **`/settings` (alias `/config`) is a hub** that stays open. Esc returns to it from each section:
  - Permissions;
  - Agents and their tools;
  - Model;
  - Privacy (standard, private, offline);
  - Web search (auto, duckduckgo, searxng, brave, tavily);
  - Assistant mode.
- **`/permissions`:**
  - The list stays open after a change.
  - Each switch shows `[on]`, `[off]` or `[on]` *blocked: Actions on this computer is off*.
  - Turning a switch on asks once and turns on the master switch with it, in a single `PUT /settings`.
  - Turning off never asks.
  - `/permissions files on|off` (and `terminal`, `actions`, `apps`, `browser`, `playwright`, `desktop`, `self`) skip
    the list.
- **`/tools [agent]`:**
  - It lists an agent's tool groups with the app's names (`TOOLSET_INFO`) and toggles them through
    `PATCH /lean/agents/{id}`. Other groups are left unchanged.
  - Run commands, Desktop, Email and Discord, and Edit EchoSpeak ask before turning on.
  - A group that's on but still gated by a global switch says so ("also needs: Terminal commands").
- **The hint.** After a run, if the answer or a failed step says a permission stopped it *and* names this agent or
  EchoSpeak's settings, the terminal reads the settings fresh. It then prints one line naming only the switches that
  are really off: `! Write files and Actions on this computer are off · /permissions to turn them on`. An explanation
  of, say, chmod doesn't trigger it.

**Code:**

- `echospeak_cli/control.py`: `switch_state`, `enable_plan`, `switch_key`, `blocked_hint`, `toolset_rows`,
  `put_setting_many`;
- `tui/app.py`: `cmd_settings`, `cmd_permissions`, `_change_switch`, `cmd_tools`, `_agent_tools`, `_settings_hint`.

**Checks:**

- `tests/test_settings_tui.py`: 11 tests, failing-first, with the screens driven through the real key bindings
  against a recording client;
- `tests/settingslab.py`: 13/13. It replays the owner's session on an isolated backend with every switch off:
  - the hint;
  - `/settings` → Permissions → Write files, which turns on both switches and nothing else (checked through the API);
  - the same request now writes `pong.html`;
  - `/tools glados` → Desktop, which persists and keeps her other groups;
  - `/permissions files off`.
- Terminal suite: 4,282 passed. Command lab: 34/34.

**Not done:**

- Per-agent models (the app's agent editor has them; `/model` still sets the session's model).
- Terminal command allow and deny lists, and voice and memory settings.
- The model hallucinating `read_output` ids in the same session (16-character ids where 24 are real) is a model and
  harness issue, recorded, not fixed here.

## 11. Round 2: square Echo, no flash, a full-screen session browser (19 November 2026)

Branch `tui-refine`. The face is a 7 × 3-cell square that slides in from the left. The banner is no longer
printed when the entrance ends: that print erased and redrew the live region, which was the flash. It goes out
with the next output instead (measured by `tests/flashprobe.py`).

The second ← opens `tui/sessionpage.py`, a full-screen page on the alternate screen after Codex's resume
picker, with a details pane on the right. Details: [tui-visual-polish.md](tui-visual-polish.md) §9.

The context-window research and the prompt-prefix fix are in [context-strategy.md](context-strategy.md).

## 12. Answers grow downward; the slash menu closes without a gap (24 November 2026)

The owner asked for new conversations to start at the top of the conversation area and grow downward, and
reported two defects. Both were reproduced on main `594d50f` with `apps/terminal/tests/layoutlab.py`.

- **Two movements after `/clear`.** §7's bottom-pinned region kept every row the cursor report offered, so after
  `/clear` the composer sat on the last row and the streaming answer appeared just above it, then jumped to the
  top when committed.
- **A gap under the composer after Esc.** With a full screen, the slash menu grew the region and scrolled the
  terminal. prompt_toolkit's `erase()` resets the rows it knows are below the region, so the region was redrawn
  8 rows higher with blank rows beneath, after every open and close.

`layout.LiveRegionFit` now tracks the rows from the region's top to the bottom row: the cursor report sets them,
and a render that scrolls raises them. A region with free rows under it is as tall as its content, so an answer
streams under the question and stays put. Once the region reaches the bottom row, the composer stays there.

The slash menu and the shortcuts are passing lists. Opened at the bottom, they borrow the rows above the region
and draw over the last committed lines instead of scrolling them away. `layout.CommittedRows` records what the
output pump printed, one string per screen row, and writes those rows back when the list closes or before the
next output. A wrapped line, a cursor sequence or a resize makes the record unusable, and the region then
grows and scrolls as before. Dialogs and the session browser never borrow, so the conversation above an
approval stays visible.

Checks: `layoutlab.py` (slash, full-screen slash, stream, `/clear`, five open/Esc cycles each) failed before the
change and passes after it. The full `uilab.py` run (44 screens) shows the same content as baseline apart from
timestamps.

Selection in Workenv is tmux's: Echo leaves the mouse to tmux, so a drag copies through OSC 52 like Claude and
Codex. The Workenv launcher (`~/.local/bin/ai-workspace`, not in this repository) adds a "Copied to clipboard"
confirmation and turns off tmux's right-click pane menu, for the Echo window only.
