# echospeak — the EchoSpeak terminal

Another client of the same EchoSpeak backend the desktop app uses: the place to talk to Echo and your
agents, and to control EchoSpeak itself (model, permissions, approvals, projects, sessions) from a terminal.
It talks HTTP only: sessions are backend threads, runs are the backend's journaled runs, approvals go to the
same approval routes. Nothing executes in the terminal process.

- **Echo TUI** (default on a terminal): an inline terminal UI in the style of Claude Code and Codex CLI, built
  with `prompt_toolkit` and `rich`. Output goes into real scrollback (no alternate screen); only a small live
  region at the bottom (status, steps, composer, footer) is redrawn. Design: `docs/research/cli-design.md` §3.
- **Plain REPL** (`--plain`, or automatically when stdin/stdout is not a terminal, `TERM=dumb`, or the TUI
  dependencies are missing): the standard-library line client. Every subcommand (`send`, `attach`, …) is
  standard library only and unchanged.

## Install

```sh
apps/terminal/install.sh            # creates apps/terminal/.venv (Python 3.12) with pinned, hash-checked deps,
                                    # then asks before linking ~/.local/bin/echospeak
apps/terminal/install.sh --no-link  # venv only; run apps/terminal/echospeak directly
```

The launcher (`apps/terminal/echospeak`) uses `apps/terminal/.venv/bin/python` when it exists and falls back to
the system `python3` (plain REPL, with a one-line hint about `install.sh`). Dependencies: `prompt_toolkit 3.0.53`,
`rich 15.0.0`, `wcwidth 0.9.4` (+ `pygments`, `markdown-it-py`, `mdurl`), pinned with hashes in
`requirements.txt` (`requirements.in` is the source; recompile with
`uv pip compile requirements.in --generate-hashes --python-version 3.12 -o requirements.txt`).

## One backend, shared with the app

`echospeak` uses the desktop app's data folder, `${XDG_DATA_HOME:-~/.local/share}/ai.echospeak.desktop/runtime`
(override with `ECHOSPEAK_DATA_DIR`), so the app and the terminal see the same agents, chats, memory and
settings. The backend holds a single-writer lock on that folder, so there is only ever one.

- If a backend is running (the app's, or one the terminal started), `echospeak` attaches through its endpoint
  file.
- Otherwise it **starts one detached**: loopback only, auth on, a fresh random key passed in the child's
  environment only (never argv), `ECHOSPEAK_RUNTIME_KIND=desktop`, its own session (closing the terminal does
  not stop it), log at `<data>/logs/terminal-backend.log`. `--no-start` disables this.

```sh
echospeak backend status        # url, pid, version, who started it, data folder, log
echospeak backend start         # start the detached backend without opening the UI
echospeak backend stop          # stop the backend the terminal started (never the app's; --any to force)
echospeak serve [--port N]      # a backend in the foreground (Ctrl-C stops it), same data folder
```

## First run: choosing the model

If no local model is chosen, the chosen one is not installed in Ollama, or it has not been confirmed in the
terminal yet (the backend may pick one automatically), Echo asks you to choose. The list comes from Ollama
(`/api/tags`) with size and a hint from `nvidia-smi`: *fits your GPU* or *slow: spills to RAM*. The choice is
saved with `PUT /settings` (provider `ollama`, context 16,384 tokens, or `ECHOSPEAK_CONTEXT_LENGTH`). Nothing is
switched without you; Esc decides later. `/model` changes it at any time.

## Usage

| Command | What it does |
|---|---|
| `echospeak` | Start clean: nothing is reopened or replayed. The banner offers the latest chat in this folder's project (← to pick it up); a new session starts with your first message. (`--plain` still resumes that chat.) |
| `echospeak -c` / `--continue` | Reopen the latest chat for this directory's project, with its last exchanges |
| `echospeak new [--agent NAME] [--project PATH_OR_ID]` | New session; with `--agent`, a direct session with that agent |
| `echospeak resume <id-or-prefix>` | Resume a session; follows its running task and shows waiting approvals |
| `echospeak sessions [--all]` · `agents` · `status` · `approvals` | Lists and status (plain output) |
| `echospeak attach <session> [--after N] [--run ID]` | Follow a run another client (e.g. the app) started |
| `echospeak send "text" [--session ID] [--agent ID] [--json]` | One-shot, pipe-friendly; exit 0/2 |
| `echospeak stop [--session ID] [--all]` | Cancel the session's run, or stop everything |
| `echospeak backend status\|start\|stop` · `serve` | The shared backend (above) |

Options: `--plain` (line REPL, no colours) · `--no-anim` · `--ascii` · `--no-start` · `--show-thinking`
(TUI: start in verbose mode) · `--url/--key` (see *Finding the backend*).

### In the TUI

```text
 ▗▄▄▄▄▄▖  EchoSpeak  v11.14.1 · terminal
 ▐█ █ █▌  Echo · Personal agent · qwen3.5:9b (ollama)
 ▝▀▀▀▀▀▘  ~/Work/proj · project proj · ask for risky actions

› create hello.py that prints hi and run it

● I'll create hello.py and run it.
  ✓ Wrote hello.py · +1 line                                         0.0s
  ✓ Allowed: Running `python hello.py`
  ✓ Ran `python hello.py` · hi                                       0.0s
    ⎿  hi

◇ Finished (not verified)
  ≡ Not verified: steps ran, but no check measured the result
── Worked for 12s · Changed 1 file · Ran 1 command ──

──────────────────────────────────── create hello.py that prints hi and run it ─
› Build a feature, fix a bug, or explore your code.
────────────────────────────────────────────────────────────────────────────────
  qwen3.5:9b · proj · ◇ ask for risky actions                    ? for shortcuts
```

- **Status line** while a task runs: the Echo ping (`· • ● ◉ ◎ ○ ◌`, time-based), what is happening in the
  app's own words ("Echo is thinking it through", "Running `pytest`", "Echo: Checking the output"), elapsed,
  tokens, `esc to stop`. No events for 10 s: "waiting on the model"; 30 s: "still waiting".
- **Answers** stream into the live tail and are committed to scrollback as rendered markdown, block by block.
  Thinking is collapsed (`∴ Thought for 8s`; `/verbose` shows it). Tool steps: `✓` done (dim), `✗` failed
  (bold), `⊘` denied, `⏸` held, with `⎿` previews; Ctrl+O has the full output.
- **File edits** print as a diff block: `✓ Edited src/stats.py · +4 −2` (or Created / Wrote / Deleted), then
  numbered rows with a sign column (`+` bold, `-` bold with dim code, context dim) and `⋮` between hunks; 16 rows
  inline (8 for a new file, none for a delete), then `… +N lines · ctrl+o`. Hue is never the only signal. With
  `COLORTERM=truecolor` and a known theme background, rows also get lighter/darker tints.
- **Outcome**: the end of a task says *Done* (`◆`) only when the backend's verification says Verified or
  Checks pass. Otherwise `◇ Done (not verified)` / `◇ Finished (not verified)` with the verification label.
  A ✓ on every step never means Done.
- **Approvals** replace the composer: `1 Yes, allow once (y)` · `2 …for this chat (c)` (only if the backend
  offers it) · `3 Yes, always allow: <scope> (a)` (only if persistable) · `4 No (n)` · `5 No, and tell Echo what
  to do instead (esc)`. Nothing is highlighted when it opens, keys typed in the first 300 ms are ignored, and
  nothing ever defaults to allow. "Tell Echo" sends your note as a steer, then denies. Questions from an agent
  show their options and "Other". Held approvals (autonomous mode) show `⏸ N held · /approvals` in the footer;
  allowing one offers to resume the paused work.
  A file edit's approval shows the proposed diff instead of its JSON arguments: options on the left and the diff
  on the right (PgUp/PgDn scroll it) at 120+ columns, stacked with at most 10 diff rows below that. Ctrl+G
  pages the full diff and the arguments.

### Keys

| Key | Idle | While a task runs |
|---|---|---|
| Enter | send | steer the task (sent at its next checkpoint; queued if it can't be) |
| Tab | complete a command | queue a follow-up (runs after this turn) |
| Esc | close menu · Esc Esc clears the draft (kept in history) | stop the task |
| Ctrl+C | clear input; twice exits | stop; twice detaches |
| Ctrl+D | exit on an empty line | detach (the task keeps running) |
| Shift+Tab | approval mode: smart → autonomous → always ask (never "never") | same |
| ↑ / ↓ | history | ↑ on empty input takes back the last queued message |
| Ctrl+O · Ctrl+R · Ctrl+G | transcript pager · history search · edit in `$EDITOR` (in a dialog: full arguments) | same |
| `\`+Enter, Ctrl+J, Alt+Enter | newline | newline |
| `?` on empty input | shortcuts | shortcuts |
| ← on empty input | your sessions (picker); ← again: the sessions page, grouped by what needs you, running, then by day. Esc or → go back one level | same |

**Any size.** The live region fits every terminal from a 20-column split to an ultrawide window: sections get
rows by priority (input and dialogs first, the stream tail last), the footer shortens one item at a time and
always keeps the model and context %, and resizing never leaves old rules behind (the terminal's re-wrap is
accounted for). Answers wrap at 100 columns on wide terminals (`ECHO_TUI_MEASURE=0` for full width);
`ECHO_TUI_REFLOW=0` for terminals that crop instead of re-wrapping on resize (plain xterm, detected
automatically). Design and verification: `docs/research/cli-design.md` §6.

Pastes over 1,000 characters or 12 lines become a chip (`[Pasted 2,431 chars · 58 lines #1]`), expanded when
sent. Never auto-submitted. History: `~/.local/state/echospeak/history`.

### Slash commands

The menu opens on `/` and lists only commands whose backend routes answered at startup. During a task, the
commands marked *live* run at once; the others run when the turn ends.

| Command | Backend route |
|---|---|
| `/model [name]` | Ollama `/api/tags` (+ `nvidia-smi` hints); `PUT /settings` (default) and/or `POST /provider/switch` (this chat) |
| `/agents`, `/agent <name\|->` | `GET /lean/agents`; a direct session with the agent via `POST /lean/rooms` |
| `/new [--agent NAME]`, `/resume [id]` (picker), `/sessions` | `/threads`, `/history`, `/query/runs` |
| `/project [PATH\|-]` | asks "Trust this folder?" first; `POST /projects/attach-folder`, `/projects/{id}/activate`, `/projects/deactivate` |
| `/mode [unified\|environments]` | `PUT /settings {assistant_mode}` |
| `/settings` (`/config`) | one hub: permissions, agents' tools, model, privacy, web search, assistant mode; Esc returns to it |
| `/permissions [smart\|autonomous\|always\|never]` · `/permissions <files\|terminal\|actions\|…> on\|off` | the switches and the approval mode; turning one on also turns on "Actions on this computer" when it needs it (asks once); `never` needs typing `never` |
| `/tools [agent]` | an agent's tool groups (files, commands, web, desktop…) on/off: `PATCH /lean/agents/{id}`; says which global switch a group still needs |
| `/approvals` *live* | `GET /lean/approvals`, `POST /lean/approvals/{id}`, `POST /lean/work/{id}` (resume) |
| `/memory [query]` | `GET /memory` (filtered locally) |
| `/status`, `/usage` *live* | `/health`, `/settings`, `/provider`, `/lean/stop-status`, token totals and context % |
| `/diff [session\|git]` *live* | the project's `git diff` incl. untracked files in the pager; else this session's edits from `/query/runs/{id}/events` |
| `/preview` *live* | in tmux (pane ≥ 140 columns): open or close a right-hand pane running `echospeak preview --follow <session>`; elsewhere it opens `/diff` |
| `/stop`, `/detach`, `/transcript`, `/verbose`, `/copy` (OSC 52), `/clear`, `/help`, `/quit` | |

Settings changed here are global: the desktop app uses them too. The TUI never turns anything on by itself.

## Finding the backend

1. `--url` / `--key` flags (`--key` is visible to other users in `ps`; prefer `ECHOSPEAK_KEY`)
2. `ECHOSPEAK_URL` / `ECHOSPEAK_KEY`
3. The endpoint file the backend writes on Linux/macOS: `$ECHOSPEAK_RUNTIME_DIR`, else
   `$XDG_RUNTIME_DIR/echospeak`, else `<data dir>/runtime`, file `endpoint.json`
4. Interactive `echospeak`: start the shared backend detached (above). Subcommands: `http://127.0.0.1:8000`.

Plain `http://` to a non-loopback host is refused unless `--insecure-remote` (use https or an SSH tunnel).

## Environment

| Variable | Effect |
|---|---|
| `ECHOSPEAK_DATA_DIR` | data folder (default: the desktop app's) |
| `ECHOSPEAK_STATE_DIR` | history and terminal prefs (default `~/.local/state/echospeak`) |
| `ECHOSPEAK_BACKEND_DIR`, `ECHOSPEAK_BACKEND_PYTHON` | where the backend and its interpreter are |
| `NO_COLOR` | no colours; bold, dim and glyphs remain |
| `ECHO_TUI_ASCII=1` | ASCII glyphs (also when the locale is not UTF-8) |
| `ECHO_REDUCED_MOTION=1` | static `●`, no shimmer, no typewriter (`--no-anim`) |
| `ECHO_TUI_VERBS=plain` | no "Listening…/Tuning in…" flavour while nothing specific is known |
| `ECHO_TUI_ACCENT=#4c72df\|echo\|auto` | truecolor accent (`auto` reads Omarchy's theme); default is the ANSI palette |
| `ECHO_TUI_BG`, `ECHO_TUI_FG` | theme colours for truecolor diff tints (default: Omarchy's `colors.toml`) |
| `ECHO_TUI_DIFF_TINT=0` | no diff background tints |
| `ECHOSPEAK_PLAIN=1` | always the plain REPL |

Narrow terminals: under 60 columns the banner is one line, the footer is `model · context%` and option keys are
hidden; under 40 the status is `● Working… esc`; under 12 rows dialogs show only their options.

## tmux

Inline rendering: scrollback, copy-mode, search and `capture-pane` work. `/copy` uses OSC 52, which tmux
forwards with `set -g set-clipboard on`. Run the TUI in one pane and `echospeak attach <session>` in another to
watch the same run.

`/preview` splits the current pane (`split-window -h -l 40%`) and runs `echospeak preview --follow <session>`
there: a read-only, full-screen viewer (its own alternate screen; the conversation pane keeps its scrollback)
with the session's changed files and the selected file's diffs, newest first, including edits waiting for
approval (`PROPOSED`). Keys: j/k or arrows scroll, n/p file, f follow the newest edit, s split old│new (pane ≥ 150
columns), q quit. `/preview` again closes it. It finds the backend like every other client; the key is never put on
a command line.

## Security

- Every string from the backend (model text, tool output, agent names, approval arguments) is passed through
  `render.sanitize()`: escape sequences (CSI, OSC including title-setting and OSC 52 clipboard writes, DCS,
  8-bit C1 forms), control characters and bidi overrides are removed, and a lone CR becomes a newline. The TUI
  renders it with rich as `Text`/markdown with `markup=False`, so server text like `[bold red]` or
  `[link=…]` stays literal; links are printed as text, never as OSC 8 hyperlinks.
- The endpoint file is `0600` in a `0700` directory owned by you; the client ignores it if it is a symlink,
  has a different owner or looser mode, or names a dead process.
- Approvals are never answered automatically; a non-TTY client never answers them.

## Troubleshooting

- *"using the plain REPL"* hint: run `apps/terminal/install.sh`.
- *"could not start the backend … holds this data folder"*: another backend (often the app's, without an
  endpoint file) has the lock; `echospeak backend status` shows the pid. Quit the app or point `--url` at it.
- *401*: the key is wrong; attach via the endpoint file (`echospeak backend status`) or set `ECHOSPEAK_KEY`.
- The backend log: `<data>/logs/terminal-backend.log` (detached) or `terminal-serve.log` (`serve`).
- Odd glyphs: `ECHO_TUI_ASCII=1`. Too much motion: `--no-anim`.
- A model that is too big for the GPU makes everything slow; `/model` shows which ones fit.

## Tests

```sh
apps/terminal/.venv/bin/python -m pytest apps/terminal/tests -q -p no:cacheprovider       # TUI unit tests (+ plain)
apps/backend/.venv/bin/python -m pytest apps/terminal/tests -q -p no:cacheprovider         # + the real-backend tests
```

`test_tui.py` covers the pure parts: the event reducer, spinner/shimmer/typewriter math, status and footer
composition at each width, outcome wording, approval option mapping and the input guard, markdown streaming,
command filtering, model-fit hints and sanitizing (including rich markup injection). `test_integration.py`
boots the real backend (`app.py --mode api`, isolated directories, auth on, a scripted OpenAI-compatible model)
and does a full round trip with the plain client, and with the TUI in a real pseudo-terminal.
