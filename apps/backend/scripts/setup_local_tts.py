"""Install a local CPU voice for EchoSpeak: Kokoro (default) or Piper.

    python scripts/setup_local_tts.py kokoro          # kokoro-onnx + fp16 model (164 MB) + voices (28 MB)
    python scripts/setup_local_tts.py kokoro --int8   # smaller int8 model (114 MB), a little slower here
    python scripts/setup_local_tts.py piper           # piper-tts (GPL-3.0) + en_US-lessac-medium (63 MB)
    python scripts/setup_local_tts.py --check         # report what is installed; changes nothing

Runs only when the owner asks: EchoSpeak never downloads voices on its own.
Files land in ~/.local/share/echospeak/models/tts and are verified by SHA-256.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(os.path.expanduser("~/.local/share/echospeak/models/tts"))
KOKORO_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1"
KOKORO_FILES = {
    "kokoro-v1.0.fp16.onnx": "f3a290d384fbb27966d462905c71a46cef9e5fd00516b40df32a0b4afe77ac96",
    "kokoro-v1.0.int8.onnx": "ae315a79b623f244700e4afb9246c46a26066782e049ba174bf3ba433970ee9c",
    "voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
}
PIPER_VOICE = "en_US-lessac-medium"
PIPER_SHA = "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(url: str, target: Path, expected: str) -> None:
    if target.is_file() and sha256(target) == expected:
        print(f"ok      {target.name} (already present)")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    print(f"fetch   {target.name}")
    with urllib.request.urlopen(url, timeout=60) as response, partial.open("wb") as handle:
        while block := response.read(1 << 20):
            handle.write(block)
    actual = sha256(partial)
    if actual != expected:
        partial.unlink(missing_ok=True)
        raise SystemExit(f"checksum mismatch for {target.name}: {actual}")
    partial.replace(target)
    print(f"ok      {target.name}")


def pip_install(*packages: str) -> None:
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", *packages], check=True)


def check() -> int:
    kokoro = importlib.util.find_spec("kokoro_onnx") is not None
    piper = importlib.util.find_spec("piper") is not None
    print(f"kokoro-onnx installed: {kokoro}")
    for name in KOKORO_FILES:
        print(f"  {name}: {(ROOT / 'kokoro' / name).is_file()}")
    print(f"piper-tts installed:   {piper}")
    print(f"  {PIPER_VOICE}.onnx: {(ROOT / 'piper' / f'{PIPER_VOICE}.onnx').is_file()}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("engine", nargs="?", choices=["kokoro", "piper"], default="kokoro")
    parser.add_argument("--int8", action="store_true", help="Kokoro: use the smaller int8 model")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        return check()
    if args.engine == "kokoro":
        if importlib.util.find_spec("kokoro_onnx") is None:
            pip_install("kokoro-onnx==0.6.1")
        model = "kokoro-v1.0.int8.onnx" if args.int8 else "kokoro-v1.0.fp16.onnx"
        for name in (model, "voices-v1.0.bin"):
            fetch(f"{KOKORO_RELEASE}/{name}", ROOT / "kokoro" / name, KOKORO_FILES[name])
        print("Kokoro is ready. Choose 'Kokoro' under Settings › Voice.")
    else:
        if importlib.util.find_spec("piper") is None:
            pip_install("piper-tts==1.8.0")
        target = ROOT / "piper"
        target.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, "-m", "piper.download_voices", "--data-dir", str(target), PIPER_VOICE], check=True)
        if sha256(target / f"{PIPER_VOICE}.onnx") != PIPER_SHA:
            raise SystemExit("checksum mismatch for the Piper voice")
        print(f"Piper is ready. Set the voice model to {target / (PIPER_VOICE + '.onnx')} under Settings › Voice.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
