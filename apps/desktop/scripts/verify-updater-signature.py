"""Verify Tauri's base64-wrapped Minisign signature against the shipped public key.

Uses cryptography already required by the release Python environment. Follows
Minisign's Ed25519 signature and authenticated trusted-comment format:
https://github.com/jedisct1/minisign/blob/master/src/minisign.c
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path


def verify(artifact: Path, signature: str, public_key: str):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    key_lines = base64.b64decode(public_key.strip(), validate=True).decode("utf-8").splitlines()
    sig_lines = base64.b64decode(signature.strip(), validate=True).decode("utf-8").splitlines()
    if len(key_lines) != 2 or len(sig_lines) != 4 or not sig_lines[2].startswith("trusted comment: "):
        raise ValueError("Invalid updater signing format")
    key = base64.b64decode(key_lines[1], validate=True)
    packet = base64.b64decode(sig_lines[1], validate=True)
    global_signature = base64.b64decode(sig_lines[3], validate=True)
    if len(key) != 42 or key[:2] != b"Ed" or len(packet) != 74 or len(global_signature) != 64:
        raise ValueError("Invalid updater signing key or signature")
    if packet[2:10] != key[2:10]:
        raise ValueError("Updater signature belongs to a different signing key")
    if packet[:2] == b"ED":
        with artifact.open("rb") as handle:
            message = hashlib.file_digest(handle, lambda: hashlib.blake2b(digest_size=64)).digest()
    elif packet[:2] == b"Ed":
        message = artifact.read_bytes()
    else:
        raise ValueError("Unknown updater signature algorithm")
    verifier = Ed25519PublicKey.from_public_bytes(key[10:])
    verifier.verify(packet[10:], message)
    verifier.verify(global_signature, packet[10:] + sig_lines[2][len("trusted comment: "):].encode("utf-8"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    verify(args.artifact, Path(str(args.artifact) + ".sig").read_text().strip(), config["plugins"]["updater"]["pubkey"])
    print("Updater signature and trusted comment verified:", args.artifact.name)
