"""`echospeak` command line: another client of the backend's runs, sessions and approvals."""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shlex
import signal
import subprocess
import sys
import time
from typing import Optional

from . import backend, control, discovery
from .client import ApiError, AuthError, Client, ConnectError, follow, new_request_id, submit
from .render import Renderer, one_line, sanitize
from .repl import HELP, App, Out, RunResult


# ── setup ─────────────────────────────────────────────────────────────
def _color(args, stream) -> bool:
    return bool(stream.isatty() and not args.plain and not os.environ.get("NO_COLOR"))


def make_app(args, out_stream=None, err_stream=None, interactive: Optional[bool] = None,
             stream_text: bool = True) -> App:
    endpoint = discovery.discover(args.url, args.key, insecure_remote=args.insecure_remote)
    client = Client(endpoint.url, endpoint.key, timeout=args.timeout)
    out_stream = out_stream or sys.stdout
    renderer = Renderer(color=_color(args, out_stream), show_thinking=args.show_thinking, stream_text=stream_text)
    if interactive is None:
        interactive = sys.stdin.isatty()
    app = App(client, renderer, Out(out_stream, err_stream), interactive=interactive,
              approval_timeout=float(getattr(args, "approval_timeout", 0) or 0), source_hint=endpoint.source)
    return app


def _ago(ts) -> str:
    try:
        sec = max(0, time.time() - float(ts))
    except (TypeError, ValueError):
        return ""
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if sec >= size:
            return f"{int(sec // size)}{unit} ago"
    return "just now"


# ── shared printers (subcommands and slash commands) ──────────────────
def print_sessions(app: App, show_all: bool = False, limit: int = 20) -> None:
    rows = [t for t in app.client.threads(limit=200) if show_all or t.get("environment", "chat") in {"chat", "agents"}]
    if not rows:
        app.out.line("no sessions")
    for t in rows[:limit]:
        mark = "*" if t.get("thread_id") == app.sid else " "
        app.out.line(f"{mark} {str(t.get('thread_id'))[:8]}  {one_line(t.get('title') or '(untitled)', 40):40}  "
                     f"{one_line(t.get('environment', ''), 6):6}  {int(t.get('message_count') or 0):3} msgs  "
                     f"{_ago(t.get('last_active_at')):>8}" + (f"  project {str(t['project_id'])[:8]}" if t.get("project_id") else ""))


def print_agents(app: App) -> None:
    app.out.line(f"  {'chat':10} (default: the selected model, no persona)")
    for a in (app.client.get("/lean/agents") or {}).get("items", []):
        model = a.get("model") or {}
        m = "/".join(x for x in (model.get("provider"), model.get("model_id")) if x) or "default model"
        mark = "*" if a.get("id") == app.agent_id else " "
        app.out.line(f"{mark} {one_line(a.get('id'), 10):10} {one_line(a.get('name'), 20):20} "
                     f"{one_line(a.get('title'), 24):24} {one_line(m, 40)}")


def print_status(app: App) -> int:
    c = app.client
    health = c.health()
    app.out.line(f"backend   {c.base_url}  version {one_line(health.get('version'), 20)}  (found via {app.source_hint})")
    try:
        ready = c.get("/startup/readiness") or {}
        degraded = ", ".join(one_line(x, 30) for x in ready.get("degraded_capabilities") or [])
        app.out.line(f"readiness {one_line(ready.get('status') or ('ready' if ready.get('core_ready') else 'starting'), 60)}"
                     + (f"  (degraded: {degraded})" if degraded else ""))
        stop = c.get("/lean/stop-status") or {}
        if stop.get("paused"):
            app.out.line(app.r.s.yellow(f"STOPPED   {one_line(stop.get('reason'), 80)}  (resume in the app or POST /lean/resume)"))
    except AuthError:
        raise
    except ApiError as exc:
        app.out.line(f"readiness unavailable: {one_line(exc.detail, 100)}")
    approvals = c.approvals()
    app.out.line(f"approvals {len(approvals)} pending")
    for a in approvals:
        app.out.line(f"  {one_line(a.get('id'), 24)}  {one_line(a.get('tool') or a.get('kind'), 24)}  "
                     f"{one_line(a.get('summary') or a.get('question'), 60)}  session {str(a.get('session_id'))[:8]}")
    running = []
    # No backend-wide run listing exists; check the most recent sessions.
    for t in c.threads(limit=10):
        for run in c.runs(str(t.get("thread_id"))):
            if run.get("status") == "running":
                running.append((t, run))
    app.out.line(f"running   {len(running)} run(s) in the 10 most recent sessions")
    for t, run in running:
        app.out.line(f"  {str(t.get('thread_id'))[:8]}  {one_line(t.get('title'), 40)}  run {one_line(run.get('id'), 48)}")
    return 0


def slash(app: App, text: str) -> bool:
    """Run one /command. Returns False to quit."""
    parts = shlex.split(text)
    cmd, rest = parts[0].lower(), parts[1:]
    if cmd in {"/quit", "/exit", "/q"}:
        return False
    if cmd == "/help":
        app.out.line(HELP)
    elif cmd == "/sessions":
        print_sessions(app, show_all="--all" in rest)
    elif cmd == "/resume":
        if not rest:
            app.out.line("usage: /resume <id-or-prefix>")
        else:
            app.switch(app.find_session(rest[0]))
    elif cmd == "/new":
        app.session = None
        if "--agent" in rest and rest.index("--agent") + 1 < len(rest):
            app.agent_id = rest[rest.index("--agent") + 1]
        app.out.line(app.r.s.dim("new session starts with your next message"))
    elif cmd == "/agent":
        if rest:
            app.agent_id = "" if rest[0] in {"-", "none", "chat"} else rest[0]
        app.out.line(f"agent: {app.agent_id or 'default'}")
        if app.agent_note():
            app.out.line(app.r.s.yellow(app.agent_note()))
    elif cmd == "/agents":
        print_agents(app)
    elif cmd == "/project":
        if not rest:
            app.out.line(f"project: {app.project_id or '(none)'}")
        elif rest[0] == "-":
            if app.sid:
                app.client.request("POST", "/projects/deactivate", params={"thread_id": app.sid})
            app.project_id = ""
            app.out.line("project detached")
        else:
            app.project_id = app.resolve_project(rest[0])
            if app.sid:
                app.client.request("POST", f"/projects/{app.project_id}/activate", params={"thread_id": app.sid})
            app.out.line(f"project: {app.project_id}")
    elif cmd == "/status":
        print_status(app)
    elif cmd == "/approvals":
        app.answer_pending()
    elif cmd == "/stop":
        running = [r for r in app.client.runs(app.sid) if r.get("status") == "running"] if app.sid else []
        if running:
            app.cancel(running[0]["id"])
        else:
            app.out.line("nothing running in this session")
    elif cmd == "/detach":
        app.out.line("nothing to detach from")
    else:
        app.out.line(f"unknown command {sanitize(cmd)}; /help lists them")
    return True


# ── the shared backend ────────────────────────────────────────────────
def ensure_backend(args) -> discovery.Endpoint:
    """Attach to the running backend, else start one detached on the shared data folder."""
    endpoint = discovery.discover(args.url, args.key, insecure_remote=args.insecure_remote)
    if endpoint.source != "default" or getattr(args, "no_start", False):
        return endpoint
    data = discovery.data_dir()
    tty = sys.stderr.isatty()
    frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def progress(elapsed: float) -> None:
        if tty:
            sys.stderr.write(f"\r{frames[int(elapsed * 10) % len(frames)]} Starting the EchoSpeak backend "
                             f"({elapsed:.0f}s) · data {_home(str(data))}\x1b[K")
            sys.stderr.flush()

    if not tty:
        print(f"starting the EchoSpeak backend (data {data})", file=sys.stderr)
    try:
        started = backend.start_detached(progress=progress)
    except backend.BackendError as exc:
        if tty:
            sys.stderr.write("\r\x1b[K")
        # A backend we can't see the endpoint of may still be serving on the default port.
        try:
            Client(discovery.DEFAULT_URL, endpoint.key, timeout=2).health()
            print(f"echospeak: could not start a backend ({sanitize(exc)}); using {discovery.DEFAULT_URL}", file=sys.stderr)
            return endpoint
        except (ConnectError, ApiError):
            raise discovery.DiscoveryError(f"could not start the backend: {exc}") from exc
    if tty:
        sys.stderr.write("\r\x1b[K")
        sys.stderr.flush()
    return started


def _home(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home) else path


def _use_tui(args) -> bool:
    if args.plain or os.environ.get("TERM") == "dumb" or os.environ.get("ECHOSPEAK_PLAIN"):
        return False
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    from . import tui

    if not tui.available():
        print(tui.INSTALL_HINT, file=sys.stderr)
        return False
    return True


def cmd_tui(args) -> int:
    from .tui.app import run_tui
    from .tui.theme import detect

    endpoint = ensure_backend(args)
    client = Client(endpoint.url, endpoint.key, timeout=args.timeout, stream_idle=90)
    client.get("/threads", limit=1)  # fail fast on a bad key, before the screen is drawn
    caps = detect(no_anim=args.no_anim, ascii_only=True if args.ascii else None)
    return run_tui(client, endpoint, caps, verbose=args.show_thinking, agent=getattr(args, "agent", "") or "",
                   resume=getattr(args, "session", "") if args.command == "resume" else "",
                   new=args.command == "new", project=getattr(args, "project", "") or "",
                   continue_last=bool(getattr(args, "continue_last", False)))


def cmd_preview(args) -> int:
    """A read-only change viewer for a tmux companion pane (/preview), or on its own."""
    from . import tui

    if not tui.available():
        print(tui.INSTALL_HINT, file=sys.stderr)
        return 2
    from .tui.preview import read_key_file, run_viewer
    from .tui.theme import detect

    if args.key_file:
        url, key = read_key_file(args.key_file)
        args.url, args.key = args.url or url, args.key or key
    endpoint = discovery.discover(args.url, args.key, insecure_remote=args.insecure_remote)
    client = Client(endpoint.url, endpoint.key, timeout=args.timeout, stream_idle=90)
    app = App(client, Renderer(color=False), Out(open(os.devnull, "w")), interactive=False)
    session = app.find_session(args.follow)
    caps = detect(no_anim=args.no_anim, ascii_only=True if args.ascii else None)
    return run_viewer(client, str(session.get("thread_id") or args.follow), caps)


# ── subcommands ───────────────────────────────────────────────────────
def cmd_repl(args) -> int:
    interactive = sys.stdin.isatty()
    if interactive and _use_tui(args):
        return cmd_tui(args)
    if interactive and not args.url:
        endpoint = ensure_backend(args)
        args.url, args.key = endpoint.url, endpoint.key
    app = make_app(args)
    if interactive:
        plain_model_setup(app)
    if args.agent:
        app.agent_id = args.agent
    if args.command == "resume":
        app.switch(app.find_session(args.session))
    elif args.command == "new":
        if args.project:
            app.project_id = app.resolve_project(args.project)
        app.out.line(app.r.s.dim("new session starts with your first message"))
    else:
        found = app.default_session(os.getcwd())
        if found:
            app.switch(found)
    if not app.interactive:
        app.out.line("stdin is not a terminal; use `echospeak send` for scripts")
        return 2
    return app.repl(slash)


def plain_model_setup(app: App) -> None:
    """First run in the plain REPL: the same check as the TUI, as a numbered prompt. Never switches silently."""
    try:
        settings = control.settings_of(app.client)
        provider, model = control.current_model(settings)
        if provider not in {"", "ollama"}:
            return
        base = control.ollama_base(settings)
        models = control.with_hints(control.ollama_models(base), control.gpu_vram_mib())
    except (ApiError, ConnectError, OSError, ValueError):
        return
    found = discovery.read_endpoint_file(discovery.runtime_dirs()[0] / "endpoint.json") or {}
    data = str(found.get("data_dir") or discovery.data_dir())
    reason = control.model_setup_reason(settings, [m.name for m in models], control.confirmed_model(data))
    if not reason or not models:
        return
    app.out.line(reason + " Choose Echo's model (saved for the app too):")
    for i, m in enumerate(models, 1):
        app.out.line(f"  {i}. {m.name}  {m.hint}" + ("  (configured)" if m.name == model else ""))
    try:
        raw = input("number (Enter to decide later) > ").strip()
    except (EOFError, KeyboardInterrupt):
        app.out.line()
        return
    if not raw.isdigit() or not 1 <= int(raw) <= len(models):
        app.out.line("no model confirmed; it stays as it is")
        return
    name = models[int(raw) - 1].name
    control.save_default_model(app.client, name, base)
    control.confirm_model(data, f"ollama:{name}")
    app.out.line(f"model: {name} (context {control.DEFAULT_CONTEXT:,} tokens)")


def cmd_attach(args) -> int:
    app = make_app(args)
    app.session = app.find_session(args.session)
    runs = app.client.runs(app.sid)
    run = next((r for r in runs if r["id"] == args.run), None) if args.run else \
        next((r for r in runs if r.get("status") == "running"), runs[0] if runs else None)
    if run is None:
        app.out.line("no runs in this session (replays are kept for two days)")
    else:
        app.out.line(app.r.s.dim(f"run {run['id']} ({run.get('status')}) from event {args.after}"))
        result = app.follow_run(run["id"], after=args.after)
        if not app.interactive:
            return 0 if result.status in {"completed", "detached"} else 2
    if app.interactive:
        for item in app.client.approvals(app.sid):
            app.on_approval(item, verified=True)
        return app.repl(slash)
    return 0


def cmd_send(args) -> int:
    text = sys.stdin.read() if args.text == "-" else args.text
    if not text.strip():
        print("nothing to send", file=sys.stderr)
        return 2
    tty_out = sys.stdout.isatty()
    if args.json:
        return _send_json(args, text)
    # Into a pipe: progress goes to stderr, only the final answer to stdout.
    app = make_app(args, out_stream=sys.stdout if tty_out else sys.stderr, interactive=False, stream_text=tty_out)
    if args.agent:
        app.agent_id = args.agent
    if args.session:
        app.session = app.find_session(args.session)
        if app.agent_note():
            print(app.agent_note(), file=sys.stderr)
    result = app.send(text)
    if not tty_out:
        print(app.r.text_so_far())
        print(f"session {app.sid}", file=sys.stderr)
    return 0 if result.ok else 2


def _send_json(args, text: str) -> int:
    app = make_app(args, out_stream=sys.stderr, interactive=False)
    app.agent_id = args.agent or ""
    sid =app.find_session(args.session)["thread_id"] if args.session else app.ensure_session(text)
    rid = new_request_id()
    result = RunResult()
    try:
        first = submit(app.client, sid, text, rid, agent_id=args.agent or "")
    except ApiError as exc:
        print(json.dumps({"type": "error", "message": exc.detail, "status": exc.status}))
        return 2

    def emit(evt: dict) -> None:
        print(json.dumps(evt, ensure_ascii=True), flush=True)  # ensure_ascii: raw control chars stay escaped
        if evt.get("type") == "final":
            result.success = bool(evt.get("success"))
        elif evt.get("type") == "error":
            result.error = str(evt.get("message"))
        elif evt.get("type") == "approval_request":
            result.unanswered.append(evt.get("id"))
        elif evt.get("type") == "approval_resolved" and evt.get("id") in result.unanswered:
            result.unanswered.remove(evt.get("id"))

    status, cursor = follow(app.client, sid, rid, first=first, on_event=emit)
    result.status = status
    print(json.dumps({"type": "journal_done", "status": status, "_replay_seq": cursor, "session": sid}), flush=True)
    return 0 if result.ok else 2


def cmd_simple(args) -> int:
    app = make_app(args)
    if args.command == "sessions":
        print_sessions(app, show_all=args.all, limit=args.limit)
    elif args.command == "agents":
        print_agents(app)
    elif args.command == "status":
        return print_status(app)
    elif args.command == "approvals":
        if args.session:
            app.session = app.find_session(args.session)
        app.answer_pending() if app.interactive else [app.out.write(app.r.approval_block(a)) for a in app.client.approvals(app.sid)]
    elif args.command == "stop":
        if args.all:
            res = app.client.post("/lean/stop-all")
            app.out.line("stopped everything: " + one_line(json.dumps(res, default=str), 300))
            return 0
        session = app.find_session(args.session) if args.session else app.default_session(os.getcwd())
        if not session:
            app.out.line("no session for this directory; pass --session ID or --all")
            return 2
        app.session = session
        running = [r for r in app.client.runs(app.sid) if r.get("status") == "running"]
        if not running:
            app.out.line("nothing running in that session")
            return 0
        app.cancel(running[0]["id"])
    return 0


def cmd_backend(args) -> int:
    action = args.action
    if action == "status":
        st = backend.status()
        if not st.running:
            print(f"no backend running for {st.data_dir}" + (f" (data folder locked by pid {st.lock_pid})" if st.lock_pid else ""))
            return 1
        who = "started by the terminal (detached)" if st.started_by_terminal else "started by the app or `serve`"
        print(f"running  {st.url}  pid {st.pid}  version {st.version or '?'}  {who}")
        print(f"data     {st.data_dir}")
        if st.started_by_terminal:
            print(f"log      {st.log}")
        return 0
    if action == "stop":
        try:
            print(backend.stop(any_backend=args.any))
        except backend.BackendError as exc:
            print(f"echospeak: {exc}", file=sys.stderr)
            return 2
        return 0
    if action == "start":
        st = backend.status()
        if st.running:
            print(f"already running: {st.url} (pid {st.pid})")
            return 0
        endpoint = ensure_backend(argparse.Namespace(url="", key="", insecure_remote=False, no_start=False))
        print(f"started {endpoint.url} (pid {endpoint.pid}); it keeps running after this terminal closes. "
              "Stop it with: echospeak backend stop")
        return 0
    return 2


def cmd_serve(args) -> int:
    root, python = discovery.backend_paths()
    port = args.port or discovery.free_port()
    key = secrets.token_urlsafe(32)
    env = discovery.serve_env(port, key)
    log_path = os.path.join(env["ECHOSPEAK_LOGS_DIR"], "terminal-serve.log")
    print(f"starting backend on 127.0.0.1:{port}  data {env['ECHOSPEAK_DATA_DIR']}  log {log_path}", flush=True)
    with open(log_path, "ab") as log:
        os.chmod(log_path, 0o600)
        # The key travels in the child's environment only, never argv.
        child = subprocess.Popen([python, "app.py", "--mode", "api", "--host", "127.0.0.1", "--port", str(port)],
                                 cwd=str(root), env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    client = Client(f"http://127.0.0.1:{port}", key, timeout=3)
    deadline = time.monotonic() + args.wait
    while True:
        if child.poll() is not None:
            print(f"backend exited with code {child.returncode}; see {log_path}", file=sys.stderr)
            return 2
        try:
            client.get("/threads", limit=1)  # proves auth works, not only /health
            break
        except (ConnectError, ApiError):
            if time.monotonic() > deadline:
                child.terminate()
                print("backend did not become ready in time", file=sys.stderr)
                return 2
            time.sleep(0.5)
    version = client.health().get("version", "")
    found = discovery.discover(env={k: v for k, v in env.items() if k in {"ECHOSPEAK_RUNTIME_DIR", "XDG_RUNTIME_DIR", "ECHOSPEAK_DATA_DIR", "XDG_DATA_HOME"}})
    where = "endpoint file written" if found.source == "endpoint-file" and found.pid == child.pid else \
        "no endpoint file (other clients need ECHOSPEAK_URL/ECHOSPEAK_KEY)"
    print(f"ready: version {version}, pid {child.pid}; {where}. Ctrl-C stops it.", flush=True)

    def stop(*_a) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        return child.wait() and 2
    except KeyboardInterrupt:
        child.send_signal(signal.SIGTERM if os.name == "posix" else signal.CTRL_BREAK_EVENT)
        try:
            child.wait(timeout=20)
        except subprocess.TimeoutExpired:
            child.kill()
        print("backend stopped", flush=True)
        return 0


# ── argparse ──────────────────────────────────────────────────────────
def _add_common(parser: argparse.ArgumentParser, top: bool) -> None:
    # Subcommands accept the same options after the subcommand name; SUPPRESS keeps them from
    # overwriting values given before it.
    d = (lambda v: v) if top else (lambda _v: argparse.SUPPRESS)
    parser.add_argument("--url", default=d(""), help="backend URL (default: ECHOSPEAK_URL, endpoint file, 127.0.0.1:8000)")
    parser.add_argument("--key", default=d(""), help="API key. Visible to other users in `ps`; prefer ECHOSPEAK_KEY")
    parser.add_argument("--insecure-remote", action="store_true", default=d(False), help="allow plain http to a non-loopback host")
    parser.add_argument("--plain", action="store_true", default=d(False),
                        help="the plain line REPL instead of the terminal UI, without colors (NO_COLOR: colors only)")
    parser.add_argument("--no-anim", action="store_true", default=d(False), help="no animation (also ECHO_REDUCED_MOTION=1)")
    parser.add_argument("-c", "--continue", dest="continue_last", action="store_true", default=d(False),
                        help="reopen the latest conversation in this folder's project (a plain start is a fresh one)")
    parser.add_argument("--ascii", action="store_true", default=d(False), help="ASCII glyphs only (also ECHO_TUI_ASCII=1)")
    parser.add_argument("--no-start", action="store_true", default=d(False),
                        help="never start a backend; only attach to a running one")
    parser.add_argument("--show-thinking", action="store_true", default=d(False), help="stream model reasoning")
    parser.add_argument("--timeout", type=float, default=d(15.0), help="request timeout seconds (default 15)")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    _add_common(common, top=False)
    p = argparse.ArgumentParser(prog="echospeak", description="Terminal client for a running EchoSpeak backend.")
    _add_common(p, top=True)
    p.set_defaults(command="", func=cmd_repl, agent="", project="")
    sub = p.add_subparsers(dest="command")
    sp = sub.add_parser("sessions", parents=[common], help="list sessions")
    sp.add_argument("--all", action="store_true", help="include code sessions")
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(func=cmd_simple)
    sp = sub.add_parser("resume", parents=[common], help="resume a session by id or prefix")
    sp.add_argument("session")
    sp.set_defaults(func=cmd_repl)
    sp = sub.add_parser("new", parents=[common], help="start a new session")
    sp.add_argument("--agent", default="")
    sp.add_argument("--project", default="", help="project id or folder path")
    sp.set_defaults(func=cmd_repl)
    sub.add_parser("agents", parents=[common], help="list agents").set_defaults(func=cmd_simple)
    sp = sub.add_parser("attach", parents=[common], help="follow a run another client started")
    sp.add_argument("session")
    sp.add_argument("--after", type=int, default=0, help="replay cursor (_replay_seq) to start after")
    sp.add_argument("--run", default="", help="a specific run id (default: the running one, else the latest)")
    sp.set_defaults(func=cmd_attach)
    sp = sub.add_parser("send", parents=[common], help="send one message and print the answer")
    sp.add_argument("text", help='message text, or "-" to read stdin')
    sp.add_argument("--session", default="")
    sp.add_argument("--agent", default="")
    sp.add_argument("--json", action="store_true", help="print the raw NDJSON events")
    sp.add_argument("--approval-timeout", type=float, default=0, help="stop waiting on an unanswered approval after N s")
    sp.set_defaults(func=cmd_send)
    sp = sub.add_parser("approvals", parents=[common], help="show (and on a TTY answer) pending approvals")
    sp.add_argument("--session", default="")
    sp.set_defaults(func=cmd_simple)
    sub.add_parser("status", parents=[common], help="backend version, readiness, approvals, runs").set_defaults(func=cmd_simple)
    sp = sub.add_parser("stop", parents=[common], help="cancel the run in a session, or --all")
    sp.add_argument("--all", action="store_true", help="stop everything (POST /lean/stop-all)")
    sp.add_argument("--session", default="")
    sp.set_defaults(func=cmd_simple)
    sp = sub.add_parser("backend", parents=[common], help="the shared backend: status, start (detached) or stop")
    sp.add_argument("action", choices=["status", "start", "stop"])
    sp.add_argument("--any", action="store_true", help="stop: also stop a backend the terminal did not start")
    sp.set_defaults(func=cmd_backend)
    sp = sub.add_parser("preview", parents=[common], help="follow a session's file changes (the /preview tmux pane)")
    sp.add_argument("--follow", required=True, metavar="SESSION", help="session id or prefix")
    sp.add_argument("--key-file", default="", help=argparse.SUPPRESS)   # private hand-off from /preview, deleted once read
    sp.set_defaults(func=cmd_preview)
    sp = sub.add_parser("serve", parents=[common], help="run a backend in the foreground on 127.0.0.1 with a fresh key")
    sp.add_argument("--port", type=int, default=0)
    sp.add_argument("--wait", type=float, default=180, help="seconds to wait for readiness")
    sp.set_defaults(func=cmd_serve)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except AuthError:
        print("echospeak: the backend refused the API key (401). Set ECHOSPEAK_KEY, or attach to a backend "
              "started by the app / `echospeak serve` so the endpoint file supplies it.", file=sys.stderr)
    except discovery.DiscoveryError as exc:
        print(f"echospeak: {exc}", file=sys.stderr)
    except ConnectError as exc:
        print(f"echospeak: cannot reach the backend ({exc}). Start the app or run `echospeak serve`.", file=sys.stderr)
    except ApiError as exc:
        print(f"echospeak: {sanitize(exc)}", file=sys.stderr)
    except LookupError as exc:
        print(f"echospeak: {sanitize(exc)}", file=sys.stderr)
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130
    return 2


if __name__ == "__main__":
    sys.exit(main())
