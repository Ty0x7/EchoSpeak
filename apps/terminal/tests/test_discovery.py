import json
import os
import subprocess
import sys

import pytest

from echospeak_cli import discovery


def _endpoint(directory, **over):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = {"schema": 1, "pid": os.getpid(), "host": "127.0.0.1", "port": 9123, "auth_key": "filekey",
            "auth_required": True, "version": "9.9.9"}
    data.update(over)
    path = directory / "endpoint.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh)
    return path


def test_flag_beats_env_beats_file(tmp_path):
    rt = tmp_path / "rt"
    _endpoint(rt)
    env = {"ECHOSPEAK_RUNTIME_DIR": str(rt), "ECHOSPEAK_URL": "http://127.0.0.1:7000", "ECHOSPEAK_KEY": "envkey"}
    e = discovery.discover("http://localhost:6000", "flagkey", env=env)
    assert (e.url, e.key, e.source) == ("http://localhost:6000", "flagkey", "flag")
    e = discovery.discover(env=env)
    assert (e.url, e.key, e.source) == ("http://127.0.0.1:7000", "envkey", "env")
    env.pop("ECHOSPEAK_URL")
    e = discovery.discover(env=env)
    assert (e.url, e.key, e.source, e.version) == ("http://127.0.0.1:9123", "filekey", "endpoint-file", "9.9.9")
    # An explicit --key overrides the file's key.
    assert discovery.discover(key="flagkey", env=env).key == "flagkey"


def test_default_when_nothing_found(tmp_path):
    e = discovery.discover(env={"ECHOSPEAK_RUNTIME_DIR": str(tmp_path / "none")})
    assert (e.url, e.key, e.source) == (discovery.DEFAULT_URL, "", "default")


def test_runtime_dir_order_matches_backend(tmp_path):
    env = {"XDG_RUNTIME_DIR": "/run/user/1", "ECHOSPEAK_DATA_DIR": str(tmp_path)}
    assert discovery.runtime_dirs(env) == [discovery.Path("/run/user/1/echospeak"), tmp_path / "runtime"]
    assert discovery.data_dir({"XDG_DATA_HOME": "/x"}) == discovery.Path("/x/echospeak")


def test_unsafe_or_stale_files_are_ignored(tmp_path):
    rt = tmp_path / "rt"
    env = {"ECHOSPEAK_RUNTIME_DIR": str(rt)}
    path = _endpoint(rt)
    os.chmod(path, 0o644)
    assert discovery.discover(env=env).source == "default"
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    path.unlink()
    _endpoint(rt, pid=dead.pid)
    assert discovery.discover(env=env).source == "default"
    path.unlink()
    target = _endpoint(tmp_path / "other")
    path.symlink_to(target)
    assert discovery.discover(env=env).source == "default"


def test_wildcard_host_maps_to_loopback(tmp_path):
    _endpoint(tmp_path / "rt", host="0.0.0.0")
    assert discovery.discover(env={"ECHOSPEAK_RUNTIME_DIR": str(tmp_path / "rt")}).url == "http://127.0.0.1:9123"


def test_plain_http_to_remote_host_is_refused():
    with pytest.raises(discovery.DiscoveryError):
        discovery.discover("http://192.168.1.4:8000", env={})
    with pytest.raises(discovery.DiscoveryError):
        discovery.discover(env={"ECHOSPEAK_URL": "http://example.com"})
    assert discovery.discover("http://192.168.1.4:8000", env={}, insecure_remote=True).url == "http://192.168.1.4:8000"
    assert discovery.discover("https://example.com", env={}).url == "https://example.com"
    assert discovery.discover("http://[::1]:8000", env={}).source == "flag"


def test_serve_env_mirrors_desktop_launcher(tmp_path):
    env = discovery.serve_env(8123, "k" * 10, env={"ECHOSPEAK_DATA_DIR": str(tmp_path / "d"), "ECHOSPEAK_KEY": "x",
                                                    "ECHOSPEAK_URL": "http://h"})
    assert env["API_AUTH_ENABLED"] == "true" and env["API_AUTH_LOCALHOST_BYPASS"] == "false"
    assert env["API_HOST"] == "127.0.0.1" and env["API_PORT"] == "8123" and env["API_AUTH_KEY"] == "k" * 10
    assert "ECHOSPEAK_KEY" not in env and "ECHOSPEAK_URL" not in env
    assert oct((tmp_path / "d").stat().st_mode & 0o777) == "0o700"
