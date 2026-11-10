"""Credential protection on Linux and other POSIX systems (the non-Windows half of credential_broker).

Windows binds ciphertext to the signed-in user with DPAPI. The closest equivalent on a Linux desktop
is the Secret Service (GNOME Keyring, KWallet's bridge, KeePassXC): unlocked with the login session,
readable only through the user's session bus. So:

- Each credential is sealed with AES-256-GCM under one 32-byte wrapping key. The credential id is
  the associated data, so a ciphertext file can't be swapped for another credential's.
- The wrapping key lives in the Secret Service, one item per data folder, reached through
  ``secret-tool`` (libsecret). The key travels over stdin/stdout, never on a command line.
- No Secret Service (a bare TTY, SSH, a container): storing fails closed with a message that says
  how to fix it. The owner can opt into a key file with ``ECHOSPEAK_CREDENTIAL_KEYFILE=1``. That
  file is 0600 beside the ciphertext, so it is protection by file permission only, and the stored
  backend name says so (``posix_keyfile_0600``). It is never chosen silently.

Neither backend defends against code already running as the same user. DPAPI doesn't either.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import shutil
import subprocess
import threading
from pathlib import Path

MAGIC = b"ESK1"
KEYRING_BACKEND = "secret_service_aes256gcm"
KEYFILE_BACKEND = "posix_keyfile_0600"
_AAD = b"EchoSpeak.ConnectionCredential.v1|"
_TIMEOUT = 15
_KEYS: dict[str, tuple[bytes, str]] = {}
_LOCK = threading.Lock()


class WrappingKeyUnavailable(RuntimeError):
    pass


def keyfile_opted_in() -> bool:
    return str(os.getenv("ECHOSPEAK_CREDENTIAL_KEYFILE", "")).strip().lower() in {"1", "true", "yes", "on"}


def _attributes(root: Path) -> list[str]:
    # One key per data folder: a dev folder, a test folder and the installed app never share one.
    scope = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:32]
    return ["service", "echospeak", "kind", "credential-wrapping-key", "scope", scope]


def _secret_tool(args: list[str], *, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    tool = shutil.which("secret-tool")
    if not tool:
        raise WrappingKeyUnavailable("secret-tool (libsecret) is not installed")
    if not os.getenv("DBUS_SESSION_BUS_ADDRESS") and not os.getenv("XDG_RUNTIME_DIR"):
        raise WrappingKeyUnavailable("there is no desktop session bus")
    try:
        return subprocess.run([tool, *args], input=stdin, capture_output=True, timeout=_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WrappingKeyUnavailable(f"the keyring did not answer ({type(exc).__name__})") from exc


def _decode(raw: bytes) -> bytes | None:
    try:
        key = base64.b64decode(raw.strip(), validate=True)
    except Exception:
        return None
    return key if len(key) == 32 else None


def _keyring_key(root: Path, *, create: bool) -> bytes | None:
    found = _secret_tool(["lookup", *_attributes(root)])
    key = _decode(found.stdout) if found.returncode == 0 else None
    if key or not create:
        return key
    key = secrets.token_bytes(32)
    stored = _secret_tool(["store", "--label=EchoSpeak credential key", *_attributes(root)],
                          stdin=base64.b64encode(key))
    if stored.returncode != 0:
        raise WrappingKeyUnavailable("the keyring refused to store the key (is it locked?)")
    # Read it back: a keyring that accepted the write but can't return it would lose every credential.
    if _decode(_secret_tool(["lookup", *_attributes(root)]).stdout) != key:
        raise WrappingKeyUnavailable("the keyring did not return the key it was given")
    return key


def _keyfile_key(root: Path, *, create: bool) -> bytes | None:
    path = root / "wrapping.key"
    try:
        info = path.lstat()
    except FileNotFoundError:
        info = None
    if info is not None:
        import stat
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise WrappingKeyUnavailable(f"{path} must be a regular file owned by you with mode 0600")
        key = _decode(path.read_bytes())
        if key is None:
            raise WrappingKeyUnavailable(f"{path} is not a valid key; it was not overwritten")
        return key
    if not create:
        return None
    key = secrets.token_bytes(32)
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(base64.b64encode(key))
        handle.flush()
        os.fsync(handle.fileno())
    return key


def wrapping_key(root: Path, backend: str = "", *, create: bool) -> tuple[bytes, str]:
    """The key for this data folder and which backend holds it.

    ``backend`` pins the lookup to where an existing credential was sealed; empty means "choose":
    the Secret Service, or the key file only if the owner opted in.
    """
    root = Path(root)
    cache_id = f"{root}|{backend}"
    with _LOCK:
        if cache_id in _KEYS:
            return _KEYS[cache_id]
        if backend == KEYFILE_BACKEND or (not backend and keyfile_opted_in()):
            key, used = _keyfile_key(root, create=create), KEYFILE_BACKEND
        else:
            try:
                key, used = _keyring_key(root, create=create), KEYRING_BACKEND
            except WrappingKeyUnavailable as exc:
                raise WrappingKeyUnavailable(
                    f"EchoSpeak keeps keys and sign-ins in your desktop keyring, and {exc}. Unlock or install a "
                    "Secret Service keyring (GNOME Keyring, KeePassXC), or set ECHOSPEAK_CREDENTIAL_KEYFILE=1 to "
                    "keep the key in a file only you can read."
                ) from exc
        if key is None:
            raise WrappingKeyUnavailable("The key that protects this credential is missing; it can't be read.")
        _KEYS[cache_id] = _KEYS[f"{root}|{used}"] = (key, used)
        return key, used


def seal(root: Path, identifier: str, plaintext: bytes) -> tuple[bytes, str]:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key, backend = wrapping_key(root, create=True)
    nonce = secrets.token_bytes(12)
    return MAGIC + nonce + AESGCM(key).encrypt(nonce, plaintext, _AAD + identifier.encode("ascii")), backend


def open_sealed(root: Path, identifier: str, blob: bytes, backend: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + 12 + 16:
        raise ValueError("not an EchoSpeak POSIX credential")
    key, _ = wrapping_key(root, backend, create=False)
    nonce, body = blob[len(MAGIC):len(MAGIC) + 12], blob[len(MAGIC) + 12:]
    return AESGCM(key).decrypt(nonce, body, _AAD + identifier.encode("ascii"))


def forget_cached_keys() -> None:
    with _LOCK:
        _KEYS.clear()
