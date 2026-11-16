# Echo terminal UI: edit and diff preview

Date: 14 November 2026. How the inline TUI (`apps/terminal/echospeak_cli/tui/`) should show file edits. This
extends `cli-design.md` §3.6 and §3.7. All evidence is read-only, and no prompt was sent to either CLI.

- **Claude Code 2.1.325**: strings from the Bun binary `~/.local/share/mise/installs/claude/2.1.325/claude`
  (extract at `~/.claude/jobs/a5f38c9b/tmp/edit-research/claude-strings.txt`), plus the code.claude.com docs.
- **Codex CLI**: the `main` clone at `…/tmp/cli-research/codex-src/codex-rs/tui/src/`. The quoted strings are present
  in the installed v0.169.0 binary.

---

## 1. What the reference interfaces actually do

### 1.1 Claude Code

Terminal means both renderers unless marked fullscreen.

| Feature | Where | Evidence |
|---|---|---|
| Edit block `Update(path)` (`Create` when `old_string` is empty) with the summary `Added N lines, removed M lines`, counts bold. A new file gets `Wrote N lines to path` and a preview of its first **10** lines | Terminal | Bundle: `return"Update"`, `"Added ",bold(t)," ",t>1?"lines":"line"`, `var w=10` |
| Diff rows: **one** right-aligned number (new side for `+`, old side for `-`), a space, the sign, then the code. `+`/`-` rows are full-width **background** tints and context rows are dim. Lines **wrap**, and the number appears on the first row only | Terminal | `padStart(R)+" "`, `backgroundColor:"diffAdded"`, `dimColor:L==="nochange"`, `Ac(W,x,"wrap")` |
| **Word-level** highlight on paired `-`/`+` rows when ≤ 40 % of the pair changed | Terminal | `var de=0.4`, `diffAddedWord`/`diffRemovedWord` |
| ANSI themes: `diffAdded:"ansi:green"`, `diffAddedWord:"ansi:greenBright"`. Colour-blind themes use blue | Terminal | Theme tables |
| Approval: `Edit file`, the path, the **full diff inside the prompt**, then `Do you want to make this edit to <bold file>?`. An oversized edit gets "Proposed edit is too large to show — cannot be reviewed, so approval is one-time only (deny unless expected)." | Terminal | `verbPhrase:"make this edit to"`, `kind:"file-edit-diff"` |
| `diffTool` `terminal`/`auto`. With `auto` and a connected IDE, the prompt shows `Opened changes in <IDE> ⧉` | Terminal → IDE | `options:["terminal","auto"]`, `showingDiffInIDE` |
| "a side-by-side comparison of the original and proposed changes", editable, with per-change **Accept/Reject this change** | **VS Code only** | docs/en/vs-code |
| **Diff panel**: "`/diff` opens a panel beside the conversation … watch the changes accumulate". It shows a file list with +/− counts and each file's diff below, refreshed on every edit. Turn views T1, T2…, a base cycle session → uncommitted → branch (`Ctrl+X B`), and tests and generated files hidden. It needs git, **≥ 110 cols**, and auto-opens at **≥ 144** | **Terminal, fullscreen (alt-screen) renderer only** | docs/en/interactive-mode#diff-panel. Bundle: `var Ze=110`, `var no=144` (`AUTO_OPEN_MIN_COLUMNS`), `MAX_LINES_PER_FILE=400`, `MAX_FILES=50`, "Resize your terminal to at least 110 columns to show the diff panel" |
| Fullscreen "draws the interface on the terminal's alternate screen buffer … tmux search don't see the conversation" | — | docs/en/fullscreen |
| Classic `/diff`: a **bordered dialog above the prompt** with the file list and counts, Enter for a file's diff, Esc back | Terminal, inline | docs/en/interactive-mode#diff-dialog |
| Questions: "the UI switches to a side-by-side layout with a vertical option list on the left and preview on the right" | Terminal | AskUserQuestion tool text |
| Arrangeable panes "chat, diff, browser, terminal, file…", toggled with `Cmd Shift D` | **Desktop only** | docs/en/desktop |

**No old|new split-column diff exists in Claude Code's terminal.** The bundle's only "split" is
`renderSplit(gutters, contents)`, which separates the gutter from the code for selection. The "conversation left,
diff right" layout is real, but only in the alt-screen renderer, which gives up native scrollback.

### 1.2 Codex CLI (`diff_render.rs`, `history_cell/patches.rs`, `app/event_dispatch.rs`)

- **Header**: `• Edited path (+a -r)`, using `Added`/`Deleted` for whole files. Several files get
  `• Edited N files (+a -r)` plus `  └ path (+a -r)` for each. Counts are green and red.
- **Rows**: indented 4. A right-aligned number sized to the largest line number (new side for insert and context, old
  side for delete), a space, the sign, the code. Hunks are separated by a dim **`⋮`**. Lines hard-wrap with a blank
  gutter on continuation rows. Example from `apply_update_block_line_numbers_three_digits_text.snap`:
  ```
  • Edited hundreds.txt (+1 -1)
       99  line 99
      100 -line 100
      100 +line 100 changed
  ```
- **Colour**: truecolor and 256-colour terminals get tinted backgrounds (`#213A2B`/`#4A221D`, idx 22/52).
  **ANSI-16 uses foreground colour only** (`ansi16_disables_line_and_gutter_backgrounds`). Syntax is highlighted per
  hunk and skipped above 512 KiB or 10k lines.
- **Truncation**: none in scrollback. The alt-screen "Owned" mode previews 3 rows.
- **Approval**: the diff goes into history above, then the bottom pane asks `Would you like to make the following edits?`
  with `Destination:` lines and `1. Yes, proceed (y)` / `2. … don't ask again for these files (a)` /
  `3. No, and tell Codex what to do differently (esc)`. **Ctrl+A** opens an alt-screen `/ P A T C H /` pager.
- **`/diff`**: "show git diff (including untracked files)", which is `git diff --color` in an alt-screen `D I F F`
  pager.
- **Side by side** appears only in the theme picker: the preview sits on the right at "≥ 44-column side panel and
  ≥ 40-column list", otherwise it is stacked. There is no diff panel and no split diff.

### 1.3 What this means for Echo

Both tools write a **unified diff with one line-number gutter** into scrollback, use a **pager or overlay** for full
views, and put a **side panel** only on a screen they own. Echo should do the same: unified in scrollback, two columns
only in the live region, and a real right-hand panel from tmux.

---

## 2. Options evaluated

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **(a)** Unified block in scrollback, split old\|new at ≥ 150 cols | Permanent and searchable with tmux copy-mode. The same idiom as both references | A split frozen into scrollback breaks when tmux reflows on resize, and copying picks up both columns. Word-diff reads better for 2–5 line edits | **Unified: adopt.** Split in scrollback: **skip**. Split only in redrawable views |
| **(b)** Two-column live region while an approval is pending | The "left/right" feel with no alt screen. Precedent: Claude's question-preview layout. Redrawn, so resizing is safe | Height is capped. Visible only while pending | **Adopt** at ≥ 120 cols, stacked below that |
| **(c)** tmux companion pane `echospeak preview --follow` | A real persistent panel. The main pane keeps true scrollback, and the viewer may use its own alt screen | Needs tmux and a change feed. Opening narrows the main pane, and tmux rewraps old output | **Adopt** as `/preview`, falling back to (d) |
| **(d)** Pager (ctrl+o, `/diff`, ctrl+g) | Already exists (`_page_text` → `less -R`). Works everywhere | Modal | **Adopt** for the full view and as the fallback |

---

## 3. Recommendation

### 3.1 Scrollback block (every completed edit)

```text
  ✓ Edited src/dates.py (+3 −1)                                         0.1s
      41   def iso_week(d):
      42       y, w, _ = d.isocalendar()
      43  -    if w == 53: return (y, 52)
      43  +    if w == 53 and not _long_year(y):
      44  +        return (y + 1, 1)
      45       return (y, w)
          ⋮
      88   def _long_year(y):
      89  +    """ISO years with 53 weeks."""
    … +6 lines · ctrl+o

  ✓ Edited 2 files (+9 −3)
    └ src/dates.py (+3 −1)
      …
    └ tests/test_dates.py (+6 −2)
      …
  ✓ Created src/weeks.py (+24)
      1  +"""Week helpers."""
      …  (first 8 lines, then … +16 lines · ctrl+o)
  ✓ Deleted old/legacy_dates.py (−112)
```

- **Header**: the §3.6 glyph, a bold verb (`Edited`/`Created`/`Overwrote`/`Deleted`/`Moved a → b`), the
  project-relative path, and `(+N −M)` with `+N` bold and `−M` dim.
- **Gutter**: indent 6, the number right-aligned to `len(str(max_line))`, two spaces, then the sign. The new number
  goes on `+` and context rows, the old number on `-` rows, and continuation rows are blank. Under 60 cols the
  numbers are dropped.
- **Hunks**: 3 lines of context, separated by a dim `⋮`.
- **Truncation**: 16 rows inline (8 for a create, header only for a delete), then `… +N lines · ctrl+o`. `/verbose`
  raises this to 400 rows per file. A source line wraps to at most 3 rows. Tabs become 4 spaces.
- **Syntax**: Pygments by extension. Each hunk side is lexed as one block (as Codex does). Off above 2,000 lines or
  256 KiB.

### 3.2 Glyphs and colour (lumon-safe)

lumon maps red, green and yellow to blues (`red=#4d86b0`, `green=#5e95bc`), so **the sign and the weight carry the
meaning, and colour only reinforces it.**

| Role | ANSI default | Truecolor (opt-in, §3.10) | `NO_COLOR` / mono |
|---|---|---|---|
| Added row | sign `+` **bold** green (32). Code in the default fg | bg = mix(theme bg → fg, 10 %), **lighter** | `+` bold |
| Removed row | sign `-` **bold** red (31). Code **dim** (2) | bg = mix(theme bg → black, 35 %), **darker** | `-` bold, code dim |
| Changed words (pair ≤ 40 % changed) | added: bold + underline (4). removed: strike (9) | stronger tint (22 % / 55 %) | the same SGRs |
| Context row | dim, sign is a space | — | dim |
| Line numbers | bright-black (90) | — | dim |
| `⋮`, `└`, `… +N lines` | dim | — | dim |
| Header path | bold | — | bold |

Tints are derived from the theme's background (Omarchy `colors.toml`, or an OSC 11 query), so they differ in
**luminance, not hue**, and stay distinct on lumon. ANSI-16 sets no backgrounds, as in Codex.

### 3.3 Approval with preview (option b)

At ≥ 120 cols and ≥ 20 rows the live region splits into two columns: the left is 44 wide (40 minimum) and the
diff takes the rest, at most `rows − 8` high.

```text
─ Echo needs your OK ─────────────────────┬─ src/dates.py (+3 −1) ─────────────────────── rows 1–9 of 14 ─
 Edit a file · 1 of 1                     │   41   def iso_week(d):
 src/dates.py                             │   42       y, w, _ = d.isocalendar()
 Why: fix the ISO week rollover           │   43  -    if w == 53: return (y, 52)
                                          │   43  +    if w == 53 and not _long_year(y):
 ❯ 1. Yes, allow once               (y)   │   44  +        return (y + 1, 1)
   2. Yes, allow edits this chat    (c)   │   45       return (y, w)
   3. No                            (n)   │       ⋮
   4. No, and tell Echo instead   (esc)   │   88   def _long_year(y):
 ↑↓ choose · enter · pgup/pgdn diff · ctrl+g full diff
```

Below 120 cols it stacks: the §3.7 card with `args` replaced by a diff of at most 10 rows.

```text
──────────────────────────────────────────── Echo needs your OK ─
 Edit a file · src/dates.py (+3 −1)
      43  -    if w == 53: return (y, 52)
      43  +    if w == 53 and not _long_year(y):
      44  +        return (y + 1, 1)
    … +6 lines · ctrl+g
 ❯ 1. Yes, allow once (y) …
```

- A diff that is too large or can't be computed says so ("too large to show — approve once only if expected") and
  hides the persistent options.
- After the decision the region collapses, and the §3.1 block prints once the tool ends. A denial prints
  `⊘ Denied edit · src/dates.py`.

### 3.4 tmux companion pane (option c)

Inside tmux (`$TMUX` set) on a window of at least 140 cols, `/preview` runs
`tmux split-window -h -d -l 40% "echospeak preview --follow <session_id>"` and keeps the pane id, and a second
`/preview` runs `kill-pane`. When the window is narrower, or outside tmux, Echo says "Widen to 140 columns for the
preview pane, or use /diff". It never opens on its own, because a split rewraps the conversation pane.

```text
 main pane (inline, native scrollback)             │ echospeak preview · full_screen app in its own pane
 › Fix the date parser                             │ Changes · this session ▾   2 files (+9 −3)
 ● I'll look at the parser first.                  │ ▸ src/dates.py              +3 −1   T2
   ✓ Read 2 files · src/dates.py, tests/…          │   tests/test_dates.py       +6 −2   T2
   ✓ Edited src/dates.py (+3 −1)                   │ ───────────────────────────────────────────
       43  -    if w == 53: return (y, 52)         │ src/dates.py (+3 −1)            unified
       43  +    if w == 53 and not _long_year(y):  │   41   def iso_week(d):
   ◐ Running pytest -q (3s · esc to stop)          │   43  -    if w == 53: return (y, 52)
 ────────────────────────────────────────────────  │   43  +    if w == 53 and not _long_year(y):
 ❯ _                                               │ ↑↓ file · pgup/pgdn scroll · s split · b base · q
```

- **Viewer**: a prompt_toolkit `full_screen=True` app, since this pane's scrollback is disposable. Up to 5 file rows
  (Claude `MAX_VISIBLE_FILES=5`). The selection follows the newest edit until the user moves it. `b` cycles the base:
  session, then turn Tn, then uncommitted. A pending edit is tagged `PROPOSED`. It is read-only and never approves.
- **Split** (`s`, here and in the pager): when the view is ≥ 150 cols, paired rows sit side by side and unmatched rows
  are padded. Otherwise it says "too narrow" and stays unified.

```text
 src/dates.py (+3 −1)                        split
   42      y, w, _ = d.isocalendar()          │   42      y, w, _ = d.isocalendar()
   43 -    if w == 53: return (y, 52)         │   43 +    if w == 53 and not _long_year(y):
                                              │   44 +        return (y + 1, 1)
```

### 3.5 Pager and commands (option d)

- **ctrl+o** works as it does today (`_pager`, `verbose=True`), so edits show at full length. **`/diff`** pages the
  session changes (the file list, then each diff) with `less -R`, and `/diff uncommitted` pages git's diff, like
  Codex. **ctrl+g** in an approval pages the proposed diff.
- One renderer serves every view: `tui/diffview.py` with `unified(change, width, caps, max_rows)` and
  `split(change, width, caps)`.

### 3.6 Width thresholds (summary)

| Columns | Scrollback | Approval | `/preview` |
|---|---|---|---|
| < 60 | sign only, no numbers | stacked, 6 rows | refuse |
| 60–119 | unified + numbers | stacked, 10 rows | refuse |
| 120–139 | unified | two-column | refuse (window < 140) |
| ≥ 140 | unified | two-column | 40 % pane. Split in a view ≥ 150 cols |

---

## 4. Backend contract per edit

**Today** (`agent/lean/loop.py`, `coding.py`, `evidence.py`):

- `tool_start.input` is a 400-char args preview.
- `tool_end.output` is cut to 1,600 chars and contains a `difflib` diff (`n=2`, `a/<basename>` header, at most 60 lines).
- `effects` gives `{kind:"file", action, path, exists, bytes}`.
- `file_write` returns no diff.
- `approval_request.args` has the raw `old_text`/`new_text` but no line numbers.

That is not enough. Add a structured **`file_change`** record to `tool_end` (applied) and to `approval_request`
(proposed, computed as a dry run):

```text
change_id, tool_call_id, session_id, turn, seq      follower stream, turn views, de-dup
op: create|edit|overwrite|delete|move (+move_from)  verb and body rule
path (absolute), display_path (project-relative)    header, viewer list
language (optional lexer hint)                      syntax highlighting
hunks: [{old_start, old_lines, new_start, new_lines,
         lines: [" ctx", "-old", "+new"]}]  context 3   numbers, ⋮, word pairs, split
added, removed                                      header counts
total_lines, head (first 8 lines, create only)      create preview
truncated, too_large, binary, withheld_reason       honest "can't show" states
base_sha256, mtime (proposed only)                  stale-preview check before apply
status: proposed|applied|denied|failed              viewer tags
```

Also needed: `GET /sessions/{id}/changes?base=session|turn:N|uncommitted`, which returns net per-file hunks. The
session baseline can come from the checkpoint each `coding._atomic_write` already makes
(`coding.py`: "Every write checkpoints the old file first"). The `file_change` events travel on the existing NDJSON
run stream. A separate `--follow` process can poll the run journal (`GET /query/runs/{request_id}/events?after=N`
in `api/routes/chat.py`), though a per-thread subscribe endpoint would be cleaner. Cap the hunks at 400 rows per
file and 50 files, as Claude does, and set `truncated`. Raise `coding._diff` to 3 lines of context, and leave the
model-facing text unchanged.

---

## 5. Implemented (15 November 2026)

| Part | Where |
|---|---|
| `file_change` record: snapshot before a file tool runs, diff from the disk after (applied), or a dry run from the arguments (proposed). 3 lines of context, ≤ 400 rows, ≤ 200 chars a line, ≤ 64 KB, secrets redacted, binary/too-large/folder withheld with a reason | `apps/backend/agent/lean/file_changes.py`, `toolbox.py` (`run`, `file_change_preview`) |
| Applied change on `tool_end.meta.file_change` (+ `tool_call_id`); kept out of the saved timeline | `agent/lean/loop.py` `_tool_finished` |
| Proposed change on `approval_request.file_change` and in `/lean/approvals` items | `loop.py` `_approve`, `approvals.py` `PendingApproval` |
| Scrollback diff block, unified/split renderer, syntax via Pygments, luminance-only tints | `apps/terminal/echospeak_cli/tui/diffview.py`, `blocks.FileDiff`, `state.py` |
| Approval with diff: two columns at ≥ 120×20, stacked (10 rows) below; PgUp/PgDn, Ctrl+G full diff | `tui/app.py` (`_two_col`, `_diff_pane_text`, `_stacked_diff`) |
| `/diff [session\|git]`, `/preview`, `echospeak preview --follow` | `tui/app.py`, `tui/preview.py`, `main.py` |

Fields as built: `change_id, tool_call_id, op (create|edit|write|delete|move), path, display_path, move_from,
language, hunks[{old_start, old_lines, new_start, new_lines, lines: [[tag, text]]}], added, removed, total_lines,
truncated, too_large, binary, withheld_reason, status (applied|proposed), base_sha256` (proposed only). Not built:
`session_id/turn/seq` (the viewer numbers turns from the journal), `head`, `mtime`, a stale-preview check before
apply, and `GET /sessions/{id}/changes`: `/diff session` and the viewer read `/query/runs` and its events (two-day,
ten-run retention).

Decisions: the approval keeps every existing option (a withheld diff says so but does not hide "for this chat");
the left column widens to fit the longest option label; `/preview` checks the current *pane* width (≥ 140); the
viewer orders entries by (run, event seq), since runs are read in parallel. Found while testing: `always` mode
never asked for the native `file_edit` or `terminal` tools (they have no registry entry); `approvals.NATIVE_ACTIONS`
now covers them. Not done: word-level highlights, the `b` base cycle and turn views, `Edited N files` grouping.
