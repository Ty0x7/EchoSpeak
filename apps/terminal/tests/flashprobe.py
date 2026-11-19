"""Flash probe: what the TUI writes to the terminal while it starts, with no input at all.

A "flash" is the whole live region being erased and drawn again (prompt_toolkit erases from the region's top
to the end of the screen, ESC [ J, then redraws). Screenshots can't show a one-frame flash; the byte stream
can. This runs the real TUI in a pseudo-terminal against an isolated backend and reports, after the first
frame, every full erase with its time and how many bytes followed it.

    apps/terminal/.venv/bin/python apps/terminal/tests/flashprobe.py [--tui-root DIR] [--seconds 4]
"""
from __future__ import annotations

import argparse
import fcntl
import os
import pty
import re
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import sizelab as lab  # noqa: E402
from fake_model import FakeModel  # noqa: E402

ERASE = re.compile(rb"\x1b\[0?J")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tui-root", default=str(lab.ROOT))
    ap.add_argument("--seconds", type=float, default=4.0)
    args = ap.parse_args()
    root = Path(args.tui_root)
    with tempfile.TemporaryDirectory() as tmp, FakeModel("hi") as model:
        proc_b, dirs, client = lab.start_backend(Path(tmp), model.url, {"HOME": str(Path(tmp) / "home")})
        try:
            project = dirs["cwd"] / "p"
            project.mkdir()
            tid = client.create_thread("Earlier")["thread_id"]
            client.post("/projects/attach-folder", {"path": str(project), "trust_state": "trusted", "session_id": tid})
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 32, 110, 0, 0))
            env = {k: v for k, v in os.environ.items() if not k.startswith(("ECHOSPEAK_", "TMUX", "ECHO_"))}
            env.update({"ECHOSPEAK_RUNTIME_DIR": str(dirs["rt"]), "TERM": "xterm-256color", "LANG": "C.UTF-8",
                        "ECHOSPEAK_STATE_DIR": str(Path(tmp) / "st"), "HOME": str(dirs["home"])})
            t0 = time.monotonic()
            child = subprocess.Popen([str(lab.ROOT / "apps/terminal/.venv/bin/python"), str(root / "apps/terminal/echospeak")],
                                     stdin=slave, stdout=slave, stderr=slave, env=env, cwd=str(project),
                                     start_new_session=True)
            os.close(slave)
            chunks: list[tuple[float, bytes]] = []
            stop = threading.Event()

            def reader() -> None:
                while not stop.is_set():
                    try:
                        data = os.read(master, 65536)
                    except OSError:
                        return
                    if not data:
                        return
                    chunks.append((time.monotonic() - t0, data))
                    if b"\x1b[6n" in data:
                        os.write(master, b"\x1b[10;1R")   # the cursor-position report prompt_toolkit asks for

            threading.Thread(target=reader, daemon=True).start()
            time.sleep(args.seconds + 2.0)
            stop.set()
            child.send_signal(signal.SIGKILL)
        finally:
            proc_b.send_signal(signal.SIGTERM)
            proc_b.wait(timeout=30)
    first = next((t for t, d in chunks if b"EchoSpeak" in d), None)
    if first is None:
        print("the banner never appeared")
        return 2
    window = [(t, d) for t, d in chunks if first < t <= first + args.seconds]
    erases = [(round(t - first, 2), len(d)) for t, d in window if ERASE.search(d)]
    print(f"first frame at {first:.2f}s; {len(window)} writes, {sum(len(d) for _t, d in window):,} bytes "
          f"in the next {args.seconds:.0f}s")
    print(f"full erases (ESC[J) after the first frame: {len(erases)}  {erases}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
