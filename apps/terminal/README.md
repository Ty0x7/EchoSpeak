# echospeak — terminal client

Another client of the same EchoSpeak backend the desktop app uses. It talks HTTP only: sessions are backend
threads, runs are the backend's journaled runs, approvals go to the same approval routes. Nothing executes in
the terminal process. Python 3.12, standard library only.

```sh
ln -s "$PWD/apps/terminal/echospeak" ~/.local/bin/echospeak   # the command is `echospeak`, not `echo`
```

## Usage

| Command | What it does |
|---|---|
| `echospeak` | Resume the latest chat for this directory's project, else start a new session on your first message |
| `echospeak new [--agent ID] [--project PATH_OR_ID]` | New session. With `--agent`, a direct agent session (as in the app's Agents view) |
| `echospeak resume <id-or-prefix>` | Resume a session; follows its running run, shows waiting approvals |
| `echospeak sessions [--all]` | List sessions (`--all` adds code sessions) |
| `echospeak agents` | List agents |
| `echospeak attach <session> [--after N] [--run ID]` | Follow a run another client (e.g. the app) started, from the beginning or after replay cursor N |
| `echospeak send "text" [--session ID] [--agent ID] [--json] [--approval-timeout S]` | One-shot. `-` reads stdin. Exit 0 on a completed run, 2 on error/cancel/unanswered approval |
| `echospeak approvals [--session ID]` | Show pending approvals; answer them when stdin is a terminal |
| `echospeak status` | Version, readiness, pending approvals, running runs |
| `echospeak stop [--session ID] [--all]` | Cancel the session's run, or stop everything (`/lean/stop-all`) |
| `echospeak serve [--port N]` | Start a private backend (see below) |

In the REPL: `/help /sessions /resume /new /agent /agents /project /status /approvals /stop /detach /quit`.
`@agent` mentions are sent as text; the backend routes them.

- **During a run**, Ctrl-C cancels that exact run (`POST /query/cancel`) and keeps the REPL; a second Ctrl-C
  detaches. Typing a line steers the run (`/query/steer`); if steering isn't possible it is queued
  (`/query/queue`) and run when the turn ends. `/detach` or Ctrl-D leaves the run going on the backend.
- **At the prompt**, Ctrl-C twice or Ctrl-D exits.
- **Reconnects** use the journal cursor (`_replay_seq`), so nothing is shown twice. Each message has its own
  `client_request_id`; a retried POST with the same body replays the same run instead of starting a second one.
- **Approvals** appear inline as a delimited block with the tool, summary, reason and the exact arguments.
  Answer `y` allow once, `n` deny, `c` allow for this chat, `a` always allow (only the choices the backend
  offers for that item). Questions take a number or text. Nothing is ever approved automatically. When stdin
  is not a terminal (`send` in a pipe) the client never answers: it says where to answer and exits non-zero.
- A successful tool call is not a verified task: `■ outcome` and `≡ verification` lines are shown prominently.
- `send` into a pipe prints only the final answer on stdout; progress and the session id go to stderr.
  `--json` prints the raw NDJSON events.

## Finding the backend

1. `--url` / `--key` flags (`--key` is visible to other users in `ps`; prefer `ECHOSPEAK_KEY`)
2. `ECHOSPEAK_URL` / `ECHOSPEAK_KEY`
3. The endpoint file the backend writes on Linux/macOS: `$ECHOSPEAK_RUNTIME_DIR`, else
   `$XDG_RUNTIME_DIR/echospeak`, else `<data dir>/runtime`, file `endpoint.json`
4. `http://127.0.0.1:8000` without a key

Plain `http://` to a non-loopback host is refused unless `--insecure-remote` (use https or an SSH tunnel).

## `echospeak serve`

Starts `apps/backend/app.py --mode api` on 127.0.0.1 with a fresh random key (`secrets.token_urlsafe(32)`),
auth required and no localhost bypass, as the desktop app does. The key goes to the child through its
environment only, never argv, and is never printed; other `echospeak` commands pick it up from the endpoint
file. Data defaults to `${XDG_DATA_HOME:-~/.local/share}/echospeak` (0700) unless `ECHOSPEAK_DATA_DIR` is set;
the backend log is `<data>/logs/terminal-serve.log`. `ECHOSPEAK_BACKEND_DIR` and `ECHOSPEAK_BACKEND_PYTHON`
override where the backend and its interpreter are. Ctrl-C (or SIGTERM) shuts it down cleanly, which also
removes the endpoint file.

## tmux

Output is appended line by line: no alternate screen, no cursor movement. Scrollback, copy-mode, search and
`tmux capture-pane` all work. Run the REPL in one pane and `echospeak attach <session>` in another to watch
the same run. Colors are off when stdout is not a terminal, with `--plain`, or with `NO_COLOR`.

## Security

- Every string from the backend (model text, tool output, agent names, approval arguments) is passed through
  `render.sanitize()`: escape sequences (CSI, OSC including title-setting and OSC 52 clipboard writes, DCS,
  8-bit C1 forms), control characters and bidi overrides are removed, and a lone CR becomes a newline so text
  cannot overwrite what came before it. Approval arguments are shown as JSON, so control bytes appear as
  visible `\u001b` escapes.
- The endpoint file is `0600` in a `0700` directory owned by you; the client ignores it if it is a symlink,
  has a different owner or looser mode, or names a dead process. On Linux a same-user process can already read
  the backend's key from `/proc/<pid>/environ`, so the file adds no new exposure. Windows never writes it.

## Tests

```sh
apps/backend/.venv/bin/python -m pytest apps/terminal/tests -q -p no:cacheprovider
```

`test_integration.py` boots the real backend as a subprocess with isolated directories, auth on, and a local
scripted OpenAI-compatible model (`tests/fake_model.py`), then does a full message round trip.
