#!/usr/bin/env bash
# Linux counterpart of build-sidecar.ps1: builds the one-folder PyInstaller backend and
# stages it at src-tauri/backend-dist/, which tauri.conf.json bundles as resources/backend.
#
#   scripts/build-sidecar.sh                     # build with the resolved Python (below)
#   scripts/build-sidecar.sh --make-venv [DIR]   # first create a hash-checked build venv
#   scripts/build-sidecar.sh --python /path/to/python
#
# Python resolution: --python, then $ECHOSPEAK_PYTHON, then apps/desktop/.build/venv-linux,
# then apps/backend/.venv. The installed app ships whatever packages that Python has, so
# the build refuses one whose packages differ from apps/backend/requirements.lock.txt
# (set ECHOSPEAK_ALLOW_UNLOCKED_BUILD=1 to downgrade that to a warning).
#
# The lock is resolved on Windows. A few of its pins are Windows-only (pywin32, pywinpty,
# pywinauto); --make-venv installs the rest with --require-hashes and the lock check
# ignores exactly those names on Linux.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd -- "$DESKTOP_ROOT/../.." && pwd)"
BACKEND_ROOT="$REPO_ROOT/apps/backend"
LOCK="$BACKEND_ROOT/requirements.lock.txt"
SPEC="$DESKTOP_ROOT/backend/echospeak_backend.spec"
TAURI_ROOT="$DESKTOP_ROOT/src-tauri"
BUILD_ROOT="$DESKTOP_ROOT/.build/sidecar"
DIST_ROOT="$BUILD_ROOT/dist"
WORK_ROOT="$BUILD_ROOT/work"
STAGE="$TAURI_ROOT/backend-dist"
PYINSTALLER_VERSION="6.21.0"
# Locked packages with no Linux distribution (or no use on Linux).
WINDOWS_ONLY="pywin32,pywinpty,pywinauto"
# Same list as python-env.ps1, minus winpty (Windows terminal backend).
BUNDLE_MODULES="PyInstaller,numpy,faiss,cryptography,faster_whisper,onnxruntime,tokenizers,ddgs,primp,lxml,trafilatura"

die() { echo "build-sidecar: $*" >&2; exit 1; }

[[ "$(uname -s)" == "Linux" ]] || die "this script builds the Linux backend; use build-sidecar.ps1 on Windows"

PYTHON=""
MAKE_VENV=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --python) PYTHON="${2:?--python needs a path}"; shift 2 ;;
    --make-venv)
      if [[ $# -gt 1 && "${2:0:1}" != "-" ]]; then MAKE_VENV="$2"; shift 2
      else MAKE_VENV="$DESKTOP_ROOT/.build/venv-linux"; shift; fi ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

make_venv() {
  local dir="$1" base="${ECHOSPEAK_BASE_PYTHON:-python3.12}"
  command -v "$base" >/dev/null 2>&1 || die "Python 3.12 not found ($base); set ECHOSPEAK_BASE_PYTHON"
  "$base" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)' \
    || die "$base is not Python 3.12 (the lock is resolved for 3.12)"
  echo "Creating the build venv at $dir"
  "$base" -m venv "$dir"
  local filtered="$dir/requirements.linux.lock.txt"
  # Drop the lock blocks that cannot install on Linux; every other pin keeps its hashes.
  "$dir/bin/python" -I - "$LOCK" "$filtered" "$WINDOWS_ONLY" <<'PY'
import re, sys
source, target, skip = sys.argv[1], sys.argv[2], set(sys.argv[3].split(","))
blocks, current = [], []
for line in open(source, encoding="utf-8").read().splitlines():
    if line and not line[0].isspace() and not line.startswith("#"):
        if current:
            blocks.append(current)
        current = [line]
    elif current:
        current.append(line)
if current:
    blocks.append(current)
kept = []
for block in blocks:
    name = re.split(r"[=<>; ]", block[0], 1)[0].lower().replace("_", "-")
    windows_marker = re.search(r'(platform_system\s*==\s*"Windows"|sys_platform\s*==\s*"win32")', block[0])
    if name in skip or windows_marker:
        print(f"  skipping Windows-only pin: {block[0].split(' ')[0]}")
        continue
    kept.append("\n".join(block))
open(target, "w", encoding="utf-8").write("\n".join(kept) + "\n")
PY
  # Linux-only dependencies the Windows-resolved lock cannot list, pinned with hashes
  # (pyautogui and mouseinfo need python3-Xlib on Linux).
  cat >> "$filtered" <<'LINUX'
python3-xlib==0.15 \
    --hash=sha256:dc4245f3ae4aa5949c1d112ee4723901ade37a96721ba9645f2bfa56e5b383f8
    # via mouseinfo, pyautogui (Linux only)
LINUX
  "$dir/bin/python" -m pip install --disable-pip-version-check --require-hashes -r "$filtered"
}

if [[ -n "$MAKE_VENV" ]]; then
  make_venv "$MAKE_VENV"
  PYTHON="$MAKE_VENV/bin/python"
fi

if [[ -z "$PYTHON" ]]; then
  if [[ -n "${ECHOSPEAK_PYTHON:-}" ]]; then PYTHON="$ECHOSPEAK_PYTHON"
  elif [[ -x "$DESKTOP_ROOT/.build/venv-linux/bin/python" ]]; then PYTHON="$DESKTOP_ROOT/.build/venv-linux/bin/python"
  elif [[ -x "$BACKEND_ROOT/.venv/bin/python" ]]; then PYTHON="$BACKEND_ROOT/.venv/bin/python"
  else die "no build Python found; run with --make-venv, --python or ECHOSPEAK_PYTHON"
  fi
fi
WHERE="$("$PYTHON" -c 'import sys; print(sys.executable)' 2>/dev/null)" \
  || die "Python '$PYTHON' could not be started"

# Packages the installed app cannot work without.
MISSING="$("$PYTHON" -c 'import importlib.util as u, sys; print(",".join(m for m in sys.argv[1].split(",") if u.find_spec(m) is None))' "$BUNDLE_MODULES")"
[[ -z "$MISSING" ]] || die "the Python at $WHERE is missing: ${MISSING//,/, }. Create a build venv: $0 --make-venv"

# MCP: the app must ship SDK 2.3+ (stateless 2026-07-28 spec).
"$PYTHON" -c 'import sys, importlib.metadata as m; from packaging.version import Version as V; v = V(m.version("mcp")); sys.exit(0 if V("2.3") <= v < V("3") else 1)' \
  || die "the Python at $WHERE needs MCP SDK >=2.3,<3"

# Lock discipline (same check as python-env.ps1, ignoring the Windows-only pins).
set +e
"$PYTHON" -I - "$SCRIPT_DIR/check-build-env.py" "$WINDOWS_ONLY" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("check_build_env", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
skip = set(sys.argv[2].split(","))
want = {name: version for name, version in module.locked().items() if name not in skip}
problems = module.differences(want, module.installed())
if problems:
    print(f"{len(problems)} packages differ from {module.LOCK.name} (Windows-only pins ignored):")
    for line in problems[:40]:
        print("  " + line)
    sys.exit(1)
print(f"Build Python matches {module.LOCK.name} (Windows-only pins ignored: {', '.join(sorted(skip))}).")
PY
LOCK_OK=$?
set -e
if [[ $LOCK_OK -ne 0 ]]; then
  if [[ "${ECHOSPEAK_ALLOW_UNLOCKED_BUILD:-}" == "1" ]]; then
    echo "WARNING: building from packages that differ from the lock (ECHOSPEAK_ALLOW_UNLOCKED_BUILD=1)" >&2
  else
    die "the build Python's packages don't match $LOCK. Create EchoSpeak's build venv once: $0 --make-venv"
  fi
fi

FOUND_PYI="$("$PYTHON" -m PyInstaller --version 2>/dev/null || true)"
[[ "$FOUND_PYI" == "$PYINSTALLER_VERSION" ]] || die "PyInstaller $PYINSTALLER_VERSION is required; found '${FOUND_PYI:-none}'"
echo "Building with Python: $WHERE"

mkdir -p "$DIST_ROOT" "$WORK_ROOT"
"$PYTHON" -m PyInstaller --noconfirm --clean --distpath "$DIST_ROOT" --workpath "$WORK_ROOT" "$SPEC"

SOURCE_FOLDER="$DIST_ROOT/echospeak-backend"
SOURCE_BINARY="$SOURCE_FOLDER/echospeak-backend"
[[ -f "$SOURCE_BINARY" ]] || die "expected backend was not produced at $SOURCE_BINARY"
[[ -x "$SOURCE_BINARY" ]] || die "$SOURCE_BINARY is not executable"

# Fail the build if the bundle is incomplete (a module PyInstaller silently left out).
SELFCHECK_DATA="$BUILD_ROOT/selfcheck-data"
mkdir -p "$SELFCHECK_DATA"
ECHOSPEAK_DATA_DIR="$SELFCHECK_DATA" ECHOSPEAK_TESTING=1 "$SOURCE_BINARY" --self-check \
  || die "packaged backend failed its self-check; fix the import error above and rebuild"

# Stage the whole folder as the Tauri resource (bundle.resources: backend-dist/ -> backend/).
if [[ -e "$STAGE" ]]; then
  [[ "$(cd -- "$STAGE" && pwd -P)" == "$(cd -- "$TAURI_ROOT" && pwd -P)/backend-dist" ]] || die "unexpected staging path $STAGE"
  rm -rf -- "$STAGE"
fi
cp -a -- "$SOURCE_FOLDER" "$STAGE"
echo "Backend ready: $STAGE ($(du -sh "$STAGE" | cut -f1), $(find "$STAGE" -type f | wc -l) files)"
