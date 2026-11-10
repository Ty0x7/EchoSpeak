"""The credential broker off Windows: sealed with a keyring-held key, failing closed without one."""
from __future__ import annotations

import base64
import os
import stat
import subprocess

import pytest

from agent import credential_posix
from agent.credential_broker import CredentialBroker, CredentialBrokerError

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Windows uses DPAPI")


@pytest.fixture(autouse=True)
def fresh_keys():
    credential_posix.forget_cached_keys()
    yield
    credential_posix.forget_cached_keys()


def fake_keyring(monkeypatch, *, available=True, locked=False):
    """secret-tool, in memory. Records every argv so a test can check the key never rides on one."""
    items: dict[tuple, bytes] = {}
    calls: list[list[str]] = []

    def run(args, *, stdin=None):
        calls.append(list(args))
        if not available:
            raise credential_posix.WrappingKeyUnavailable("there is no desktop session bus")
        attributes = tuple(a for a in args[1:] if not a.startswith("--label"))
        if args[0] == "store":
            if locked:
                return subprocess.CompletedProcess(args, 1, b"", b"locked")
            items[attributes] = stdin or b""
            return subprocess.CompletedProcess(args, 0, b"", b"")
        return subprocess.CompletedProcess(args, 0 if attributes in items else 1, items.get(attributes, b""), b"")

    monkeypatch.setattr(credential_posix, "_secret_tool", run)
    return items, calls


def test_keyring_round_trip_keeps_the_key_out_of_files_and_command_lines(tmp_path, monkeypatch):
    monkeypatch.delenv("ECHOSPEAK_CREDENTIAL_KEYFILE", raising=False)
    items, calls = fake_keyring(monkeypatch)
    broker = CredentialBroker(tmp_path / "credentials")
    ref = broker.put({"OPENAI_API_KEY": "sk-secret-value"}, label="Settings")

    assert ref.startswith("credential://keyring/")
    assert broker.metadata(ref)["backend"] == credential_posix.KEYRING_BACKEND
    assert len(items) == 1
    key = next(iter(items.values()))
    assert all(key.decode() not in " ".join(call) for call in calls)
    for path in (tmp_path / "credentials").iterdir():
        data = path.read_bytes()
        assert b"sk-secret-value" not in data and base64.b64decode(key) not in data and key not in data
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "credentials").stat().st_mode) == 0o700

    # A new process: nothing cached, the key comes back from the keyring.
    credential_posix.forget_cached_keys()
    assert CredentialBroker(tmp_path / "credentials").resolve(ref) == {"OPENAI_API_KEY": "sk-secret-value"}


def test_without_a_keyring_nothing_is_stored_and_the_message_says_what_to_do(tmp_path, monkeypatch):
    monkeypatch.delenv("ECHOSPEAK_CREDENTIAL_KEYFILE", raising=False)
    fake_keyring(monkeypatch, available=False)
    broker = CredentialBroker(tmp_path / "credentials")
    with pytest.raises(CredentialBrokerError) as error:
        broker.put("token", label="MCP sign-in")
    assert "ECHOSPEAK_CREDENTIAL_KEYFILE=1" in str(error.value) and "keyring" in str(error.value)
    assert not (tmp_path / "credentials").exists() or not list((tmp_path / "credentials").iterdir())


def test_a_locked_keyring_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("ECHOSPEAK_CREDENTIAL_KEYFILE", raising=False)
    fake_keyring(monkeypatch, locked=True)
    with pytest.raises(CredentialBrokerError):
        CredentialBroker(tmp_path / "credentials").put("token", label="x")


def test_key_file_is_opt_in_private_and_labelled_honestly(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHOSPEAK_CREDENTIAL_KEYFILE", "1")
    _, calls = fake_keyring(monkeypatch)
    root = tmp_path / "credentials"
    broker = CredentialBroker(root)
    ref = broker.put("token-value", label="A2A token")
    assert calls == []  # the keyring is never consulted once the owner chose the file
    assert broker.metadata(ref)["backend"] == credential_posix.KEYFILE_BACKEND
    assert stat.S_IMODE((root / "wrapping.key").stat().st_mode) == 0o600
    credential_posix.forget_cached_keys()
    assert CredentialBroker(root).resolve(ref) == "token-value"

    # Loosened permissions are refused rather than trusted.
    credential_posix.forget_cached_keys()
    (root / "wrapping.key").chmod(0o644)
    with pytest.raises(CredentialBrokerError):
        CredentialBroker(root).resolve(ref)


def test_a_credential_stays_with_the_backend_that_sealed_it(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHOSPEAK_CREDENTIAL_KEYFILE", "1")
    fake_keyring(monkeypatch)
    root = tmp_path / "credentials"
    ref = CredentialBroker(root).put("from-file", label="x")
    # The owner later moves to the keyring: old credentials still open with the file's key.
    monkeypatch.delenv("ECHOSPEAK_CREDENTIAL_KEYFILE")
    credential_posix.forget_cached_keys()
    broker = CredentialBroker(root)
    assert broker.resolve(ref) == "from-file"
    newer = broker.put("from-keyring", label="y")
    assert broker.metadata(newer)["backend"] == credential_posix.KEYRING_BACKEND
    assert broker.resolve(newer) == "from-keyring" and broker.resolve(ref) == "from-file"


def test_ciphertext_cannot_be_swapped_between_credentials_or_tampered(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHOSPEAK_CREDENTIAL_KEYFILE", "1")
    root = tmp_path / "credentials"
    broker = CredentialBroker(root)
    first, second = broker.put("alpha", label="a"), broker.put("beta", label="b")
    a, b = (root / f"{ref.rsplit('/', 1)[1]}.bin" for ref in (first, second))
    b.write_bytes(a.read_bytes())
    with pytest.raises(CredentialBrokerError):
        broker.resolve(second)
    blob = bytearray(a.read_bytes())
    blob[-1] ^= 1
    a.write_bytes(bytes(blob))
    with pytest.raises(CredentialBrokerError):
        broker.resolve(first)


def test_windows_protected_data_is_reported_not_guessed(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHOSPEAK_CREDENTIAL_KEYFILE", "1")
    root = tmp_path / "credentials"
    broker = CredentialBroker(root)
    ref = broker.put("value", label="copied")
    identifier = ref.rsplit("/", 1)[1]
    broker._index["items"][identifier]["backend"] = "windows_dpapi_current_user"
    with pytest.raises(CredentialBrokerError) as error:
        broker.resolve(f"credential://dpapi/{identifier}")
    assert "Windows" in str(error.value)
