"""Open files, folders, web pages and apps on a Linux desktop, and check that a window appeared.

The caller (tools.open_application) has already checked Settings > Permissions and the approval.
Everything here goes through the desktop's own mechanisms instead of guessing program names:

- apps are the desktop entries (*.desktop) in the XDG data folders, found by id, name or command;
- the default app for a file or link is what `xdg-mime` reports for its MIME type or URL scheme;
- the app runs from that entry's Exec line, in the user's graphical session (Wayland or X11),
  detached from EchoSpeak (in its own systemd scope when one is available), so it isn't
  tied to a tool call's lifetime;
- the result is checked against the compositor's window list where one can be read (Hyprland,
  Sway, or X11 through wmctrl).

Owner's GTA_CLONE session (22 and 26 November 2026): Echo ran `firefox …` (not installed) and
`xdg-open … &` in the sandbox, which has no display. Both exited 0, and both were reported as opened.
On this PC, `xdg-open` with no browser running stays the browser's parent: the host run blocked
for 62 s, and stopping it closed the browser.
"""

from __future__ import annotations

import json
import mimetypes
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import unquote, urlparse

from agent.child_env import child_env

# How long to watch for the window. Browsers that hand a page to a running instance show it in
# under a second; a cold start of a large app can take several.
VERIFY_SECONDS = 8.0
# Exec field codes (Desktop Entry Specification): file/URL lists, then deprecated or decorative ones.
_FILE_CODES = {"%f", "%F", "%u", "%U"}
_DROP_CODES = {"%i", "%c", "%k", "%d", "%D", "%n", "%N", "%v", "%m"}
_URL_SCHEMES = {"http", "https", "file"}
# "index.html" is a file name, not a web address, even though it looks like one.
_FILE_SUFFIXES = {"html", "htm", "xhtml", "js", "mjs", "ts", "tsx", "jsx", "css", "json", "md", "txt", "py", "png",
                  "jpg", "jpeg", "gif", "svg", "webp", "pdf", "wav", "ogg", "mp3", "mp4", "webm", "zip", "sh", "toml",
                  "yaml", "yml", "csv", "log", "xml", "wasm", "glb", "gltf", "lua", "rs", "go", "c", "h", "cpp"}


@dataclass
class Entry:
    id: str
    path: Path
    name: str
    exec: str
    generic: str = ""
    mime_types: list[str] = field(default_factory=list)
    hidden: bool = False
    terminal: bool = False

    @property
    def command(self) -> str:
        try:
            first = shlex.split(self.exec)[0] if self.exec else ""
        except ValueError:
            first = self.exec.split()[0] if self.exec.split() else ""
        return first

    def installed(self, env: Optional[dict] = None) -> bool:
        """The entry's program still exists: an entry can outlive the app it launched."""
        cmd = self.command
        if not cmd:
            return False
        if cmd in {"env", "flatpak"}:
            return True
        path = (env or os.environ).get("PATH")
        return bool(shutil.which(cmd, path=path) if not os.path.isabs(cmd) else os.access(cmd, os.X_OK))


# ---- the graphical session -------------------------------------------------------------------------------------

def session_env(base: Optional[dict] = None) -> tuple[dict, str]:
    """(environment for a desktop app, '' or why there is no desktop session to open on).

    A backend started by the desktop app inherits the session variables. One started some other
    way (a service, an SSH shell) may not, so the per-user runtime folder is checked for the
    session's Wayland socket and bus, the way a login session would set them.
    """
    env = dict(base if base is not None else child_env())
    runtime = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    if Path(runtime).is_dir():
        env.setdefault("XDG_RUNTIME_DIR", runtime)
        if not env.get("WAYLAND_DISPLAY"):
            sockets = sorted(p.name for p in Path(runtime).glob("wayland-*") if not p.name.endswith(".lock"))
            if len(sockets) == 1:
                env["WAYLAND_DISPLAY"] = sockets[0]
        if not env.get("DBUS_SESSION_BUS_ADDRESS") and Path(runtime, "bus").exists():
            env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={runtime}/bus"
    if not env.get("WAYLAND_DISPLAY") and not env.get("DISPLAY"):
        return env, ("there is no graphical session to open it on (neither WAYLAND_DISPLAY nor DISPLAY is set "
                     "for EchoSpeak's backend, and no Wayland socket was found)")
    return env, ""


# ---- desktop entries -------------------------------------------------------------------------------------------

def data_dirs(env: Optional[dict] = None) -> list[Path]:
    env = env or os.environ
    home = env.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    dirs = [home, *(env.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":"),
            str(Path.home() / ".local/share/flatpak/exports/share"), "/var/lib/flatpak/exports/share"]
    return [Path(d) / "applications" for d in dict.fromkeys(d for d in dirs if d)]


def _parse(path: Path, entry_id: str) -> Optional[Entry]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    section = text.split("[Desktop Entry]", 1)
    if len(section) < 2:
        return None
    body = section[1].split("\n[", 1)[0]
    values: dict[str, str] = {}
    for line in body.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values.setdefault(key.strip(), value.strip())
    if values.get("Type", "Application") != "Application":
        return None
    return Entry(
        id=entry_id, path=path, name=values.get("Name", entry_id.removesuffix(".desktop")),
        exec=values.get("Exec", ""), generic=values.get("GenericName", ""),
        mime_types=[m for m in values.get("MimeType", "").split(";") if m],
        hidden=values.get("NoDisplay", "").lower() == "true" or values.get("Hidden", "").lower() == "true",
        terminal=values.get("Terminal", "").lower() == "true",
    )


def entries(env: Optional[dict] = None) -> dict[str, Entry]:
    """Desktop entries by id; the first data folder wins, so a user's entry overrides the system one."""
    found: dict[str, Entry] = {}
    for root in data_dirs(env):
        try:
            paths = sorted(root.rglob("*.desktop"))
        except OSError:
            continue
        for path in paths:
            entry_id = str(path.relative_to(root)).replace("/", "-")
            if entry_id in found:
                continue
            entry = _parse(path, entry_id)
            if entry is not None:
                found[entry_id] = entry
    return found


def _norm(text: str) -> str:
    return " ".join(str(text or "").strip().casefold().removesuffix(".desktop").split())


def find_app(name: str, env: Optional[dict] = None, table: Optional[dict[str, Entry]] = None) -> tuple[Optional[Entry], str]:
    """(entry, '') or (None, why). Exact id, name or command first, then an unambiguous partial name."""
    table = table if table is not None else entries(env)
    want = _norm(name)
    if not want:
        return None, "no app name was given"
    usable = [e for e in table.values() if e.installed(env)]

    def keys(e: Entry) -> set[str]:
        return {_norm(e.id), _norm(e.name), _norm(Path(e.command).name), _norm(e.id.rsplit(".", 2)[-2] if e.id.count(".") >= 2 else "")} - {""}

    exact = [e for e in usable if want in keys(e)]
    if not exact:
        exact = [e for e in usable if not e.hidden and (want in _norm(e.name) or want in _norm(e.generic))]
    visible = [e for e in exact if not e.hidden] or exact
    if len({e.command for e in visible}) > 1:
        names = ", ".join(sorted({e.name for e in visible})[:6])
        return None, f"several installed apps match \"{name}\" ({names}); use the exact name"
    if visible:
        return visible[0], ""
    if any(want in keys(e) for e in table.values()):
        return None, f"\"{name}\" has a desktop entry, but its program is no longer installed"
    return None, f"\"{name}\" is not installed on this computer"


# ---- what a target is and which app opens it ------------------------------------------------------------------

@dataclass
class Target:
    text: str           # what the caller asked to open
    uri: str            # what is handed to the app (a path for local files, else the URL)
    kind: str           # "file", "folder" or "url"
    mime: str           # MIME type, or x-scheme-handler/<scheme> for a URL
    path: Optional[Path] = None


def classify(target: str, env: Optional[dict] = None) -> tuple[Optional[Target], str]:
    text = str(target or "").strip().strip("\"'")
    if not text:
        return None, "nothing to open was given"
    parsed = urlparse(text)
    if parsed.scheme and len(parsed.scheme) > 1:        # not a Windows drive letter
        scheme = parsed.scheme.lower()
        if scheme not in _URL_SCHEMES:
            return None, f"{scheme}: links aren't opened by this tool; it opens files, folders and http(s) pages"
        if scheme != "file":
            if not parsed.netloc:
                return None, f"{text} is not a complete web address"
            return Target(text, text, "url", f"x-scheme-handler/{scheme}"), ""
        text = unquote(parsed.path)
    host = re.fullmatch(r"(?i)((?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.([a-z]{2,24}))(/\S*)?", text)
    if host and host.group(2).lower() not in _FILE_SUFFIXES and not Path(text).exists():
        url = "https://" + text
        return Target(text, url, "url", "x-scheme-handler/https"), ""
    path = Path(os.path.expanduser(text))
    if not path.is_absolute():
        return None, f"{text} is not a full path; give the file's full path (or an http(s) address)"
    if not path.exists():
        return None, f"{path} does not exist"
    if path.is_dir():
        return Target(str(path), str(path), "folder", "inode/directory", path), ""
    return Target(str(path), str(path), "file", file_mime(path, env), path), ""


def file_mime(path: Path, env: Optional[dict] = None) -> str:
    out = _run(["xdg-mime", "query", "filetype", str(path)], env)
    mime = out.split(";")[0].strip() if out else ""
    return mime if "/" in mime else (mimetypes.guess_type(str(path))[0] or "application/octet-stream")


def _run(argv: list[str], env: Optional[dict], timeout: float = 5) -> str:
    if not shutil.which(argv[0], path=(env or os.environ).get("PATH")):
        return ""
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def default_app(mime: str, env: Optional[dict] = None, table: Optional[dict[str, Entry]] = None) -> Optional[Entry]:
    """The desktop's default app for this MIME type or URL scheme, if its program is installed."""
    table = table if table is not None else entries(env)
    wanted = [mime]
    if mime == "application/xhtml+xml":
        wanted.append("text/html")
    for kind in wanted:
        entry_id = _run(["xdg-mime", "query", "default", kind], env)
        for one in entry_id.split(";"):
            entry = table.get(one.strip())
            if entry and entry.installed(env):
                return entry
    return None


def handlers(mime: str, env: Optional[dict] = None, table: Optional[dict[str, Entry]] = None) -> list[Entry]:
    """Installed apps that say they open this type (their MimeType lines), visible ones first."""
    table = table if table is not None else entries(env)
    family = mime.split("/")[0] + "/*"
    found = [e for e in table.values() if (mime in e.mime_types or family in e.mime_types) and e.installed(env)]
    return sorted(found, key=lambda e: (e.hidden, e.name.lower()))


def exec_argv(entry: Entry, uri: str = "") -> list[str]:
    """The entry's Exec line as argv, with the target in place of its file/URL code (spec field codes)."""
    try:
        words = shlex.split(entry.exec)
    except ValueError:
        words = entry.exec.split()
    argv: list[str] = []
    placed = False
    for word in words:
        if word in _FILE_CODES:
            if uri and not placed:
                argv.append(uri)
                placed = True
            continue
        if word in _DROP_CODES:
            continue
        argv.append(word.replace("%%", "%"))
    if uri and not placed:
        argv.append(uri)
    return argv


# ---- pages that need a web server -----------------------------------------------------------------------------

_MODULE_SCRIPT = re.compile(r"""<script\b[^>]*\btype\s*=\s*["']?module\b""", re.I)
_SCRIPT_SRC = re.compile(r"""<script\b[^>]*\bsrc\s*=\s*["']([^"']+)["']""", re.I)
_BUNDLER_ENTRY = re.compile(r"""\bsrc\s*=\s*["']/?src/main\.(?:t|j)sx?["']""", re.I)
# Reads other files at run time. Browsers refuse these from file:// pages.
_RUNTIME_LOAD = re.compile(r"""\bfetch\s*\(\s*[`'"](?!https?:|data:|blob:)|new\s+Worker\s*\(|\bimport\s*\(\s*['"`]\.|"""
                           r"""XMLHttpRequest|\.load\s*\(\s*['"`](?!https?:)[^'"`]+\.(?:json|gltf|glb|obj|wasm)""")
_DEV_SCRIPTS = ("dev", "start", "serve", "preview")


def server_needed(path: Path) -> tuple[str, str]:
    """(why a local web server is needed, the command that starts one) for an HTML file, else ('', '').

    Browsers block ES modules, fetch(), workers and WebAssembly loads for pages opened from file://
    (the page's origin is null), so the page shows up blank or half-built. A bundler project
    (Vite, webpack, Next…) must be built or served by its own dev server.
    """
    if path.suffix.lower() not in {".html", ".htm", ".xhtml"}:
        return "", ""
    folder = path.parent
    package = _package_scripts(folder)
    try:
        html = path.read_text(encoding="utf-8", errors="replace")[:400_000]
    except OSError:
        return "", ""
    reason = ""
    if _BUNDLER_ENTRY.search(html) and package:
        reason = "it is a bundler project's page (it loads /src/main), which only works through its dev server"
    elif _MODULE_SCRIPT.search(html):
        reason = "it loads JavaScript modules (<script type=\"module\">), which browsers block for file:// pages"
    else:
        for src in _SCRIPT_SRC.findall(html)[:20]:
            if re.match(r"(?i)https?:|//", src):
                continue
            script = (folder / src.split("?")[0]).resolve()
            try:
                if folder.resolve() in script.parents and script.is_file() and script.stat().st_size < 2_000_000:
                    code = script.read_text(encoding="utf-8", errors="replace")
                    if _RUNTIME_LOAD.search(code) or re.search(r"(?m)^\s*import\s.+\sfrom\s", code):
                        reason = (f"{src} loads other files at run time (fetch, modules, workers or WebAssembly), "
                                  "which browsers block for file:// pages")
                        break
            except OSError:
                continue
        if not reason and _RUNTIME_LOAD.search(html):
            reason = "the page loads other files at run time (fetch, workers or WebAssembly), which browsers block for file:// pages"
    if not reason:
        return "", ""
    script = next((s for s in _DEV_SCRIPTS if s in package), "")
    if script:
        return reason, f"npm run {script}" if script != "start" else "npm start"
    return reason, f"python3 -m http.server 8000 --bind 127.0.0.1 --directory {shlex.quote(str(folder))}"


def _package_scripts(folder: Path) -> dict:
    for parent in [folder, *list(folder.parents)[:3]]:
        package = parent / "package.json"
        if package.is_file():
            try:
                return dict(json.loads(package.read_text(encoding="utf-8")).get("scripts") or {})
            except (OSError, ValueError, AttributeError):
                return {}
    return {}


def local_server_down(url: str) -> str:
    """For an http://localhost URL: why nothing will load (no server listening), else ''."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
        return ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((parsed.hostname if parsed.hostname != "0.0.0.0" else "127.0.0.1", port), timeout=1):
            return ""
    except OSError:
        return f"nothing is listening on {parsed.hostname}:{port}, so the page would not load"


# ---- launching and checking ----------------------------------------------------------------------------------

def windows(env: Optional[dict] = None) -> Optional[list[dict]]:
    """Open windows as {id, title, app, pid, where}, or None when this desktop's windows can't be read."""
    env = env or os.environ
    if env.get("HYPRLAND_INSTANCE_SIGNATURE"):
        out = _run(["hyprctl", "clients", "-j"], env)
        try:
            rows = json.loads(out) if out else None
        except ValueError:
            rows = None
        if isinstance(rows, list):
            try:
                monitors = {m.get("id"): m.get("name") for m in json.loads(_run(["hyprctl", "monitors", "-j"], env) or "[]")}
            except (ValueError, AttributeError):
                monitors = {}
            return [{"id": str(c.get("address")), "title": str(c.get("title") or ""), "app": str(c.get("class") or ""),
                     "pid": c.get("pid"), "where": f"monitor {monitors.get(c.get('monitor')) or c.get('monitor')}, workspace "
                     f"{(c.get('workspace') or {}).get('name') or (c.get('workspace') or {}).get('id')}",
                     "floating": bool(c.get("floating")), "monitor": monitors.get(c.get("monitor")),
                     "workspace": (c.get("workspace") or {}).get("id"),
                     "rect": tuple((c.get("at") or [0, 0])[:2]) + tuple((c.get("size") or [0, 0])[:2]),
                     "mapped": bool(c.get("mapped", True)) and not c.get("hidden")}
                    for c in rows if isinstance(c, dict)]
    if env.get("SWAYSOCK"):
        out = _run(["swaymsg", "-t", "get_tree", "-r"], env)
        try:
            tree = json.loads(out) if out else None
        except ValueError:
            tree = None
        if isinstance(tree, dict):
            found: list[dict] = []

            def walk(node: dict, workspace: str = "") -> None:
                if node.get("type") == "workspace":
                    workspace = str(node.get("name"))
                if node.get("pid"):
                    r = node.get("rect") or {}
                    found.append({"id": str(node.get("id")), "title": str(node.get("name") or ""),
                                  "rect": (r.get("x", 0), r.get("y", 0), r.get("width", 0), r.get("height", 0)),
                                  "visible": bool(node.get("visible")),
                                  "app": str(node.get("app_id") or (node.get("window_properties") or {}).get("class") or ""),
                                  "pid": node.get("pid"), "where": f"workspace {workspace}"})
                for child in (node.get("nodes") or []) + (node.get("floating_nodes") or []):
                    walk(child, workspace)
            walk(tree)
            return found
    if env.get("DISPLAY") and not env.get("WAYLAND_DISPLAY"):
        out = _run(["wmctrl", "-lp"], env)
        if out:
            rows = []
            for line in out.splitlines():
                parts = line.split(None, 4)
                if len(parts) >= 4:
                    rows.append({"id": parts[0], "pid": int(parts[2]) if parts[2].isdigit() else None,
                                 "title": parts[4] if len(parts) > 4 else "", "app": "", "where": f"desktop {parts[1]}"})
            return rows
    return None


def _spawn(argv: list[str], env: dict, log: Path) -> subprocess.Popen:
    """Start the app outside EchoSpeak's process group (and cgroup, through a transient systemd scope when
    available), so neither a finished tool call nor a backend restart closes it."""
    scope = shutil.which("systemd-run", path=env.get("PATH"))
    if scope and env.get("DBUS_SESSION_BUS_ADDRESS"):
        argv = [scope, "--user", "--scope", "--quiet", "--collect",
                f"--unit=echospeak-open-{os.getpid()}-{time.time_ns()}", "--", *argv]
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "wb") as handle:
        return subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT,
                                start_new_session=True, close_fds=True)


def _title_hints(target: Optional[Target], entry: Entry) -> list[str]:
    hints = []
    if target and target.path is not None:
        if target.path.suffix.lower() in {".html", ".htm"}:
            try:
                match = re.search(r"(?is)<title[^>]*>(.*?)</title>", target.path.read_text(encoding="utf-8", errors="replace")[:200_000])
                if match and match.group(1).strip():
                    hints.append(" ".join(match.group(1).split()))
            except OSError:
                pass
        hints.append(target.path.name)
    elif target and target.kind == "url":
        host = urlparse(target.uri).hostname or ""
        hints.append(host.removeprefix("www.").split(".")[0])
    return [h.casefold() for h in hints if h]


@dataclass
class Launch:
    ok: bool
    entry: Entry
    detail: str            # what happened, for the report
    window: Optional[dict] = None
    verified: bool = False


def _app_words(entry: Entry) -> set[str]:
    return {_norm(entry.name), _norm(Path(entry.command).name), _norm(entry.id).split(".")[-1]} - {""}


def launch(entry: Entry, target: Optional[Target], env: dict, log: Path, *, wait: float = VERIFY_SECONDS,
           sleep: Callable[[float], None] = time.sleep, also: Optional[set[str]] = None) -> Launch:
    """also: other app names whose window counts, e.g. the browser behind a link-routing launcher script."""
    argv = exec_argv(entry, target.uri if target else "")
    before = windows(env)
    known = {(w["id"], w["title"]) for w in before or []}
    try:
        proc = _spawn(argv, env, log)
    except OSError as error:
        return Launch(False, entry, f"{entry.name} could not start: {error}")
    hints = _title_hints(target, entry)
    app_words = _app_words(entry) | (also or set())
    deadline = time.monotonic() + wait
    code: Optional[int] = None
    while True:
        if code is None:
            code = proc.poll()
        if code not in (None, 0):
            return Launch(False, entry, f"{entry.name} exited with code {code} straight away: {_tail(log)}")
        now = windows(env)
        if now is not None:
            fresh = [w for w in now if (w["id"], w["title"]) not in known]
            match = next((w for w in fresh if any(h in w["title"].casefold() for h in hints)), None) if hints else None
            match = match or next((w for w in fresh if proc.pid and w.get("pid") == proc.pid), None)
            if match is None and not target:
                match = next((w for w in fresh if _norm(w["app"]) in app_words or any(a and a in _norm(w["title"]) for a in app_words)), None)
            if match is None and target and target.kind == "url":
                match = next((w for w in fresh if _norm(w["app"]) in app_words), None)
            if match is not None:
                return Launch(True, entry, "", match, True)
        if time.monotonic() >= deadline:
            break
        sleep(0.4)
    if code not in (None, 0):
        return Launch(False, entry, f"{entry.name} exited with code {code}: {_tail(log)}")
    if hints and before is not None:
        # Opened again where it was already showing (a browser reusing the tab's window): no title changes, but a
        # window showing it is there.
        # Only the launched app's own windows: another browser can have a tab with the same title.
        showing = next((w for w in (windows(env) or []) if any(h in w["title"].casefold() for h in hints)
                        and (_norm(w["app"]) in app_words or any(a in _norm(w["title"]) for a in app_words))), None)
        if showing is not None:
            return Launch(True, entry, "", showing, True)
    if before is None:
        return Launch(True, entry, "this desktop's window list can't be read here, so the window wasn't checked")
    return Launch(True, entry, f"no new {entry.name} window showing it appeared within {int(wait)} s")


def _tail(log: Path) -> str:
    try:
        text = log.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return "(no output)"
    lines = [line for line in text.splitlines() if line.strip()][-3:]
    return " | ".join(lines)[-400:] or "(no output)"


def open_on_desktop(app: str = "", target: str = "", *, log_dir: Path, blocked: Callable[[Entry, list[str]], str],
                    env: Optional[dict] = None, wait: float = VERIFY_SECONDS) -> str:
    """Open target (file, folder or http(s) URL) in app, or in the desktop's default app; or start app alone.

    blocked(entry, argv) returns the Blocked apps entry that refuses this launch, or ''.
    """
    env, no_session = session_env(env)
    if no_session:
        return f"Error: nothing was opened: {no_session}."
    table = entries(env)
    item: Optional[Target] = None
    if target:
        item, problem = classify(target, env)
        if item is None:
            return f"Error: nothing was opened: {problem}."
        if item.kind == "url":
            if down := local_server_down(item.uri):
                return (f"Error: nothing was opened: {down}. Start the project's server first (terminal, in the "
                        "background), confirm it is listening, then open the URL.")
        elif item.path is not None:
            reason, command = server_needed(item.path)
            if reason:
                return (f"Error: not opened as a file: {reason}. Serve the folder instead: start `{command}` with the "
                        "terminal in the background, confirm it is listening, then call open_application with the "
                        "http://127.0.0.1:<port>/ address it prints (add the page's file name if it isn't index.html).")
    chosen: list[tuple[Entry, str]] = []           # (entry, why this one)
    problems: list[str] = []
    if app:
        entry, why = find_app(app, env, table)
        if entry is None:
            problems.append(why)
        else:
            chosen.append((entry, "requested"))
    if not app or (item is not None and not chosen):
        default = default_app(item.mime, env, table) if item else None
        if default is not None:
            chosen.append((default, f"the default app for {item.mime}"))
        if item is not None:
            for other in handlers(item.mime, env, table):
                if all(other.command != c.command for c, _ in chosen):
                    chosen.append((other, f"installed and opens {item.mime}"))
    if not chosen:
        if item is None:
            return f"Error: nothing was opened: {problems[0] if problems else 'no app was named'}."
        return (f"Error: nothing was opened: {'; '.join(problems) + '; ' if problems else ''}no installed app is set up "
                f"to open {item.mime}. Ask the user which app to use; don't install one without their go-ahead.")

    attempts: list[str] = []
    for index, (entry, why) in enumerate(chosen[:3]):
        argv = exec_argv(entry, item.uri if item else "")
        if blocked_by := blocked(entry, argv):
            attempts.append(f"{entry.name} is in Blocked apps ({blocked_by})")
            continue
        if entry.terminal:
            attempts.append(f"{entry.name} runs in a terminal, not a window")
            continue
        also: set[str] = set()
        if item is not None and item.kind == "url":
            # A link handler can be a launcher script (Omarchy's "Browser" runs omarchy-launch-browser): the window
            # that appears is the web browser's, which the desktop names as the default for HTML.
            browser = default_app("text/html", env, table)
            also = _app_words(browser) if browser else set()
        result = launch(entry, item, env, log_dir / f"open-{time.time_ns()}.log", wait=wait, also=also)
        if not result.ok:
            attempts.append(result.detail)
            continue
        what = f"{item.text} in {entry.name}" if item else entry.name
        lead = (f"Tried first: {'; '.join(problems + attempts)}. " if problems or attempts else "")
        if result.verified and result.window:
            w = result.window
            mode = " (floating)" if w.get("floating") else ""
            page = _page_note(item, env)
            return (f"{lead}Opened {what} ({why}). Verified: window \"{w['title'][:120]}\" is open on {w['where']}{mode}. "
                    "Don't open it again. An open window doesn't show that a game or interface works: before saying "
                    f"it does, look at it with take_screenshot (window_title \"{w['title'][:60]}\") and check the "
                    "image (a blank canvas, an error, missing parts)." + page)
        return (f"{lead}Launched {what} ({why}); it is still running, but {result.detail}. Don't launch it again: "
                "look with take_screenshot (the monitor, or the window's title) to see whether it is showing and "
                "working, and say what the screenshot showed." + _page_note(item, env))
    return "Error: nothing was opened: " + "; ".join(problems + attempts) + "."


# ---- looking at it ---------------------------------------------------------------------------------------------

def visible_workspaces(env: Optional[dict] = None) -> Optional[set]:
    """Hyprland: the workspace shown on each monitor (a window elsewhere isn't on screen), else None."""
    env = env or os.environ
    if not env.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return None
    try:
        return {(m.get("activeWorkspace") or {}).get("id") for m in json.loads(_run(["hyprctl", "monitors", "-j"], env) or "[]")}
    except (ValueError, AttributeError):
        return None


def find_window(title: str, env: Optional[dict] = None) -> tuple[Optional[dict], str]:
    """The one window whose title contains title, with what may hide it: (window, note) or (None, why)."""
    rows = windows(env)
    if rows is None:
        return None, "this desktop's windows can't be listed here"
    want = title.casefold()
    found = [w for w in rows if want in w["title"].casefold() and w.get("rect")]
    if len(found) != 1:
        names = "; ".join(f'"{w["title"][:60]}"' for w in rows[:12])
        return None, (f"{'several' if found else 'no'} windows match \"{title}\"; open windows: {names or 'none'}")
    win = found[0]
    shown = visible_workspaces(env)
    if win.get("visible") is False or (shown is not None and win.get("workspace") not in shown):
        return None, (f"\"{win['title'][:60]}\" is on workspace {win.get('workspace')}, which isn't on screen, so a "
                      "capture would show something else. Ask the user to switch to it")
    x, y, w, h = win["rect"]
    over = [o for o in rows if o is not win and o.get("floating") and o.get("workspace") == win.get("workspace")
            and o.get("rect") and _overlap(win["rect"], o["rect"]) > 0.1 * w * h]
    note = ("partly covered by " + ", ".join(f'"{o["title"][:40]}"' for o in over) + "; look past it or ask the user"
            if over else "")
    return win, note


def _overlap(a: tuple, b: tuple) -> int:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return max(0, min(ax + aw, bx + bw) - max(ax, bx)) * max(0, min(ay + ah, by + bh) - max(ay, by))


def wayland_capture(region: tuple[int, int, int, int], env: Optional[dict] = None) -> bytes:
    """PNG of a region of a Wayland desktop, from the compositor's screencopy (grim), in layout pixels."""
    env = env or os.environ
    grim = shutil.which("grim", path=env.get("PATH"))
    if not grim:
        raise ValueError("Screen capture on this Wayland desktop needs grim (the compositor's screenshot tool), "
                         "which isn't installed. Ask the user before installing anything")
    x, y, w, h = (int(v) for v in region)
    result = subprocess.run([grim, "-s", "1", "-t", "png", "-g", f"{x},{y} {w}x{h}", "-"], capture_output=True,
                            timeout=15, env=env)
    if result.returncode or not result.stdout:
        raise ValueError("The screen capture failed: " + result.stderr.decode(errors="replace").strip()[:300])
    return result.stdout


# ---- does the page run -----------------------------------------------------------------------------------------

_PAGE_CHECK = r"""
import json, sys
from playwright.sync_api import sync_playwright
url, wait_ms = sys.argv[1], int(sys.argv[2])
errors = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.on("pageerror", lambda e: errors.append("Uncaught " + str(e).splitlines()[0]))
    page.on("console", lambda m: m.type == "error" and errors.append(m.text.splitlines()[0]
                                                                     + (f" ({m.location.get('url', '').rsplit('/', 1)[-1]}:{m.location.get('lineNumber')})" if m.location.get("url") else "")))
    page.on("response", lambda r: r.status >= 400 and errors.append(f"{r.status} loading {r.url.rsplit('/', 1)[-1]}"))
    page.on("requestfailed", lambda r: errors.append(f"failed to load {r.url.rsplit('/', 1)[-1]}"))
    page.goto(url, wait_until="load", timeout=15000)
    page.wait_for_timeout(wait_ms)
    browser.close()
print(json.dumps(list(dict.fromkeys(errors))[:6]))
"""


def page_errors(target: Target, env: Optional[dict] = None, wait_ms: int = 1500) -> Optional[list[str]]:
    """JavaScript errors and failed loads when this page runs, from a separate headless browser (EchoSpeak's own
    Playwright, never the user's browser or profile). Local files and localhost pages only: an internet page
    isn't fetched a second time. None when the check can't run."""
    if target.kind == "file" and target.path is not None and target.path.suffix.lower() in {".html", ".htm", ".xhtml"}:
        url = target.path.as_uri()
    elif target.kind == "url" and urlparse(target.uri).hostname in {"localhost", "127.0.0.1", "::1"}:
        url = target.uri
    else:
        return None
    try:
        result = subprocess.run([sys.executable, "-c", _PAGE_CHECK, url, str(wait_ms)], capture_output=True, text=True,
                                timeout=30, env=env)
        found = json.loads(result.stdout.strip().splitlines()[-1]) if result.returncode == 0 and result.stdout.strip() else None
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None
    return [str(e)[:240] for e in found] if isinstance(found, list) else None


def _page_note(target: Optional[Target], env: Optional[dict]) -> str:
    if target is None:
        return ""
    errors = page_errors(target, env)
    if errors is None:
        return ""
    if not errors:
        return " Page check (a separate headless browser): no JavaScript errors or failed loads."
    return (f" Page check (a separate headless browser) found {len(errors)} problem{'s' if len(errors) > 1 else ''}: "
            + "; ".join(errors) + ". The page doesn't work as built: fix this before saying it works, and say what "
            "the screenshot shows.")
