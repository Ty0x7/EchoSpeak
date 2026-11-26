"""endpoint.json: private runtime dir, atomic write, stale/unsafe files ignored, no-op off POSIX."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys

import pytest

from agent import endpoint_file

pytestmark = pytest.mark.skipif(os.name != "posix", reason="endpoint file is POSIX-only")


@pytest.fixture
def rt(tmp_path, monkeypatch):
    path = tmp_path / "rt"
    monkeypatch.setenv("ECHOSPEAK_RUNTIME_DIR", str(path))
    return path


def _write(**kw):
    return endpoint_file.write_endpoint("127.0.0.1", 8123, auth_key="k" * 20, auth_required=True,
                                        version="1.2.3", data_dir="/tmp/x", **kw)


def test_writes_private_file_and_reads_it_back(rt):
    path = _write()
    assert path == rt / "endpoint.json"
    assert stat.S_IMODE(os.stat(rt).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    data = endpoint_file.read_endpoint()
    assert data["pid"] == os.getpid() and data["port"] == 8123 and data["auth_key"] == "k" * 20
    assert data["auth_required"] is True and data["schema"] == 1 and data["version"] == "1.2.3"
    assert not [p for p in rt.iterdir() if p.name.endswith(".tmp")]


def test_loose_existing_dir_is_tightened(rt):
    rt.mkdir(mode=0o755)
    os.chmod(rt, 0o755)
    assert _write() is not None
    assert stat.S_IMODE(os.stat(rt).st_mode) == 0o700


def test_symlinked_dir_is_refused(tmp_path, monkeypatch):
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    monkeypatch.setenv("ECHOSPEAK_RUNTIME_DIR", str(link))
    assert _write() is None
    assert not (real / "endpoint.json").exists()


def test_dir_that_cannot_be_made_private_is_refused(rt, monkeypatch):
    rt.mkdir(mode=0o700)
    real_chmod = os.chmod
    real_chmod(rt, 0o770)
    monkeypatch.setattr(endpoint_file.os, "chmod", lambda *a, **k: None)
    try:
        assert _write() is None
        assert not (rt / "endpoint.json").exists()
    finally:
        monkeypatch.undo()
        real_chmod(rt, 0o700)


def test_foreign_owner_dir_is_refused(rt, monkeypatch):
    rt.mkdir(mode=0o700)
    real_uid = os.getuid()
    monkeypatch.setattr(endpoint_file.os, "getuid", lambda: real_uid + 4242)
    assert _write() is None


def test_group_readable_file_is_rejected(rt):
    path = _write()
    os.chmod(path, 0o640)
    assert endpoint_file.read_endpoint() is None


def test_symlinked_file_is_rejected(rt, tmp_path):
    path = _write()
    real = tmp_path / "elsewhere.json"
    real.write_text(path.read_text())
    os.chmod(real, 0o600)
    path.unlink()
    path.symlink_to(real)
    assert endpoint_file.read_endpoint() is None


def test_stale_pid_is_ignored(rt):
    path = _write()
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    data = json.loads(path.read_text())
    data["pid"] = dead.pid
    path.write_text(json.dumps(data))
    assert endpoint_file.read_endpoint() is None


def test_rewrite_replaces_atomically_even_with_leftover_temp(rt):
    _write()
    leftover = rt / f".endpoint.json.{os.getpid()}.tmp"
    leftover.write_text("junk")
    endpoint_file.write_endpoint("127.0.0.1", 9999)
    assert endpoint_file.read_endpoint()["port"] == 9999
    assert not leftover.exists()


def _dead_pid() -> int:
    """A pid that certainly names no live process: a child we started and reaped.
    (os.getpid() + 1 is often a live process on a busy machine, and #215 rightly refuses to replace a live owner.)"""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def test_remove_only_when_it_names_this_pid(rt):
    path = _write()
    data = json.loads(path.read_text())
    data["pid"] = _dead_pid()
    path.write_text(json.dumps(data))
    assert endpoint_file.remove_endpoint() is False and path.exists()
    _write()
    assert endpoint_file.remove_endpoint() is True and not path.exists()


def test_noop_when_not_posix(rt, monkeypatch):
    monkeypatch.setattr(endpoint_file, "IS_POSIX", False)
    assert _write() is None
    assert not rt.exists()
    assert endpoint_file.read_endpoint() is None


def test_xdg_runtime_dir_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("ECHOSPEAK_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    owner = tmp_path / "share" / "ai.echospeak.desktop" / "runtime"
    # #213: only the owner's data folder publishes to the shared XDG location
    assert endpoint_file.runtime_dir(owner) == tmp_path / "echospeak"
    assert endpoint_file.runtime_dir("/data") == endpoint_file.Path("/data/runtime")
    monkeypatch.delenv("XDG_RUNTIME_DIR")
    assert endpoint_file.runtime_dir("/data") == endpoint_file.Path("/data/runtime")


# ---- #213: a backend for another data folder must not take over the owner's endpoint ----

def _xdg(tmp_path, monkeypatch):
    monkeypatch.delenv("ECHOSPEAK_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    (tmp_path / "xdg").mkdir(exist_ok=True)
    owner = tmp_path / "share" / "ai.echospeak.desktop" / "runtime"
    owner.mkdir(parents=True, exist_ok=True)
    return owner


def test_only_the_owners_data_folder_publishes_to_the_shared_location(tmp_path, monkeypatch):
    owner = _xdg(tmp_path, monkeypatch)
    lab = tmp_path / "lab-data"
    lab.mkdir()
    assert endpoint_file.runtime_dir(owner) == tmp_path / "xdg" / "echospeak"
    assert endpoint_file.runtime_dir(lab) == lab / "runtime"
    path = endpoint_file.write_endpoint("127.0.0.1", 39321, data_dir=str(lab))
    assert path == lab / "runtime" / "endpoint.json"
    assert not (tmp_path / "xdg" / "echospeak" / "endpoint.json").exists()


def _foreign_owner_file(directory, pid, data_dir):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / "endpoint.json"
    path.write_text(json.dumps({"schema": 1, "pid": pid, "host": "127.0.0.1", "port": 39117, "auth_key": "k",
                                "auth_required": True, "version": "x", "data_dir": str(data_dir), "started_at": 1}))
    os.chmod(path, 0o600)
    return path


def test_never_replaces_another_live_backends_endpoint(tmp_path, monkeypatch):
    owner = _xdg(tmp_path, monkeypatch)
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        path = _foreign_owner_file(tmp_path / "xdg" / "echospeak", sleeper.pid, owner)
        assert endpoint_file.write_endpoint("127.0.0.1", 1, data_dir=str(owner)) is None
        assert json.loads(path.read_text())["pid"] == sleeper.pid
        # ensure_endpoint leaves a live owner alone, and takes over only once it is gone
        assert endpoint_file.ensure_endpoint("127.0.0.1", 1, data_dir=str(owner)) is None
    finally:
        sleeper.kill()
        sleeper.wait()
    assert endpoint_file.ensure_endpoint("127.0.0.1", 1, data_dir=str(owner)) == path
    assert json.loads(path.read_text())["pid"] == os.getpid()


def test_ensure_endpoint_restores_a_deleted_file(tmp_path, monkeypatch):
    owner = _xdg(tmp_path, monkeypatch)
    path = endpoint_file.write_endpoint("127.0.0.1", 39117, data_dir=str(owner))
    path.unlink()
    assert endpoint_file.ensure_endpoint("127.0.0.1", 39117, data_dir=str(owner)) == path
    assert json.loads(path.read_text())["port"] == 39117
