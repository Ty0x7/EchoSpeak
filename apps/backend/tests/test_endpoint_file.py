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


def test_remove_only_when_it_names_this_pid(rt):
    path = _write()
    data = json.loads(path.read_text())
    data["pid"] = os.getpid() + 1
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
    assert endpoint_file.runtime_dir() == tmp_path / "echospeak"
    monkeypatch.delenv("XDG_RUNTIME_DIR")
    assert endpoint_file.runtime_dir("/data") == endpoint_file.Path("/data/runtime")
