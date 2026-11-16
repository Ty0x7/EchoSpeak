"""Opaque credential storage bound to the signed-in user.

Windows: the Data Protection API. Linux and other POSIX systems: AES-256-GCM under a key
held in the desktop keyring (agent/credential_posix.py), failing closed without one.

Runtime configuration and Connection records persist only ``credential://``
references.  Plaintext exists only for the duration of a broker call and is
never included in a public projection.
"""

from __future__ import annotations
from agent.atomic_replace import replace as atomic_replace

import ctypes
import json
import os
import re
import threading
import time
import uuid
from ctypes import wintypes
from pathlib import Path
from typing import Any, Optional

try:
    from config import DATA_DIR
except Exception:  # pragma: no cover - isolated tooling fallback
    DATA_DIR = Path("data")


class CredentialBrokerError(RuntimeError):
    pass


class CredentialNotFoundError(CredentialBrokerError):
    pass


# The middle part names where a credential was first sealed. The index entry's "backend" decides how it opens.
_REFERENCE = re.compile(r"^credential://(?:dpapi|keyring)/([a-f0-9]{32})$")
_WINDOWS_BACKEND = "windows_dpapi_current_user"
_SCHEMA_VERSION = 1
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _blob(value: bytes) -> tuple[_DataBlob, Any]:
    buffer = ctypes.create_string_buffer(value, len(value))
    return (
        _DataBlob(
            cbData=len(value),
            pbData=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
        ),
        buffer,
    )


def _protect_windows(value: bytes, description: str) -> bytes:
    if os.name != "nt":
        raise CredentialBrokerError("This credential was protected by Windows for another account; enter it again here")
    source, source_buffer = _blob(value)
    entropy, entropy_buffer = _blob(b"EchoSpeak.ConnectionCredential.v1")
    output = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    ok = crypt32.CryptProtectData(
        ctypes.byref(source),
        str(description or "EchoSpeak credential"),
        ctypes.byref(entropy),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output),
    )
    # Keep buffers alive until the native call returns.
    _ = source_buffer, entropy_buffer
    if not ok:
        raise CredentialBrokerError(
            f"Windows DPAPI protection failed ({ctypes.get_last_error()})"
        )
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


def _unprotect_windows(value: bytes) -> bytes:
    if os.name != "nt":
        raise CredentialBrokerError("This credential was protected by Windows for another account; enter it again here")
    source, source_buffer = _blob(value)
    entropy, entropy_buffer = _blob(b"EchoSpeak.ConnectionCredential.v1")
    output = _DataBlob()
    description = wintypes.LPWSTR()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(source),
        ctypes.byref(description),
        ctypes.byref(entropy),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output),
    )
    _ = source_buffer, entropy_buffer
    if not ok:
        raise CredentialBrokerError(
            f"Windows DPAPI unprotection failed ({ctypes.get_last_error()})"
        )
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)
        if description:
            kernel32.LocalFree(description)


class CredentialBroker:
    """Store and resolve opaque credential references.

    The index contains labels and timestamps only. Each payload is a
    current-user DPAPI ciphertext stored in a separate binary file.
    """

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = Path(root or (DATA_DIR / "credentials")).expanduser().resolve()
        self.index_path = self.root / "index.json"
        self._lock = threading.RLock()
        self._index: dict[str, Any] = {
            "schema_version": _SCHEMA_VERSION,
            "revision": 0,
            "items": {},
        }
        self._load()

    def _load(self) -> None:
        if not self.index_path.exists():
            return
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
            if (
                not isinstance(raw, dict)
                or raw.get("schema_version") != _SCHEMA_VERSION
                or not isinstance(raw.get("items"), dict)
            ):
                raise ValueError("unsupported credential index")
            self._index = raw
        except Exception as exc:
            raise CredentialBrokerError(
                f"Credential index is unreadable at {self.index_path}; it was not overwritten ({exc})"
            ) from exc

    def _persist_index(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temp = self.index_path.with_suffix(f".tmp.{os.getpid()}.{time.time_ns()}")
        try:
            with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8", newline="\n") as handle:
                json.dump(self._index, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            atomic_replace(temp, self.index_path)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def _seal(self, identifier: str, payload: bytes, label: str) -> tuple[bytes, str]:
        if os.name == "nt":
            return _protect_windows(payload, label), _WINDOWS_BACKEND
        from agent import credential_posix
        try:
            return credential_posix.seal(self.root, identifier, payload)
        except credential_posix.WrappingKeyUnavailable as exc:
            raise CredentialBrokerError(str(exc)) from exc

    def _open(self, identifier: str, ciphertext: bytes, backend: str) -> bytes:
        if backend == _WINDOWS_BACKEND:
            # A data folder copied from Windows: DPAPI ciphertext only opens for that Windows user.
            return _unprotect_windows(ciphertext)
        from agent import credential_posix
        try:
            return credential_posix.open_sealed(self.root, identifier, ciphertext, backend)
        except credential_posix.WrappingKeyUnavailable as exc:
            raise CredentialBrokerError(str(exc)) from exc

    @staticmethod
    def _identifier(reference: str) -> str:
        match = _REFERENCE.fullmatch(str(reference or "").strip())
        if not match:
            raise CredentialBrokerError("Invalid credential reference")
        return match.group(1)

    def put(
        self,
        value: Any,
        *,
        label: str,
        reference: str = "",
    ) -> str:
        identifier = self._identifier(reference) if reference else uuid.uuid4().hex
        ref = str(reference).strip() if reference else f"credential://{'dpapi' if os.name == 'nt' else 'keyring'}/{identifier}"
        payload = json.dumps(
            {"schema_version": 1, "value": value},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        with self._lock:
            ciphertext, backend = self._seal(identifier, payload, label)
            self.root.mkdir(parents=True, exist_ok=True)
            if os.name != "nt":
                os.chmod(self.root, 0o700)
            path = self.root / f"{identifier}.bin"
            temp = path.with_suffix(f".tmp.{os.getpid()}.{time.time_ns()}")
            try:
                # 0600 from creation: POSIX has no per-user ACL default like %LOCALAPPDATA%.
                with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as handle:
                    handle.write(ciphertext)
                    handle.flush()
                    os.fsync(handle.fileno())
                atomic_replace(temp, path)
            finally:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass
            now = time.time()
            prior = dict(self._index["items"].get(identifier) or {})
            self._index["items"][identifier] = {
                "label": str(label or "EchoSpeak credential")[:200],
                "backend": backend,
                "created_at": float(prior.get("created_at") or now),
                "updated_at": now,
            }
            self._index["revision"] = int(self._index.get("revision") or 0) + 1
            self._persist_index()
        return ref

    def resolve(self, reference: str) -> Any:
        identifier = self._identifier(reference)
        with self._lock:
            if identifier not in self._index["items"]:
                raise CredentialNotFoundError("Credential reference is not registered")
            path = self.root / f"{identifier}.bin"
            if not path.is_file():
                raise CredentialNotFoundError("Credential ciphertext is missing")
            ciphertext = path.read_bytes()
            backend = str((self._index["items"].get(identifier) or {}).get("backend") or _WINDOWS_BACKEND)
        try:
            envelope = json.loads(self._open(identifier, ciphertext, backend).decode("utf-8"))
            if not isinstance(envelope, dict) or envelope.get("schema_version") != 1:
                raise ValueError("unsupported credential payload")
            return envelope.get("value")
        except CredentialBrokerError:
            raise
        except Exception as exc:
            raise CredentialBrokerError("Credential payload could not be decoded") from exc

    def delete(self, reference: str) -> bool:
        identifier = self._identifier(reference)
        with self._lock:
            existed = identifier in self._index["items"]
            if not existed:
                return False
            (self.root / f"{identifier}.bin").unlink(missing_ok=True)
            self._index["items"].pop(identifier, None)
            self._index["revision"] = int(self._index.get("revision") or 0) + 1
            self._persist_index()
            return True

    def contains(self, reference: str) -> bool:
        try:
            identifier = self._identifier(reference)
        except CredentialBrokerError:
            return False
        with self._lock:
            return (
                identifier in self._index["items"]
                and (self.root / f"{identifier}.bin").is_file()
            )

    def metadata(self, reference: str) -> dict[str, Any]:
        identifier = self._identifier(reference)
        with self._lock:
            item = self._index["items"].get(identifier)
            if not isinstance(item, dict):
                raise CredentialNotFoundError("Credential reference is not registered")
            return {
                "reference": reference,
                "configured": (self.root / f"{identifier}.bin").is_file(),
                **dict(item),
            }


_BROKER: Optional[CredentialBroker] = None
_BROKER_LOCK = threading.Lock()


def get_credential_broker() -> CredentialBroker:
    global _BROKER
    with _BROKER_LOCK:
        if _BROKER is None:
            _BROKER = CredentialBroker()
        return _BROKER
