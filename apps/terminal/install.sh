#!/usr/bin/env bash
# Install the Echo terminal UI: a project-local venv with its pinned, hash-checked dependencies,
# and (only if you agree) a symlink so `echospeak` is on your PATH.
#
#   apps/terminal/install.sh            # venv + deps, then asks about the symlink
#   apps/terminal/install.sh --no-link  # venv + deps only
#   apps/terminal/install.sh --link     # venv + deps + symlink without asking
#   add --with-backend to also create apps/backend/.venv (the backend's own hash-checked environment),
#   which `echospeak` needs to start a backend from this checkout
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
VENV="$HERE/.venv"
BIN_DIR="${ECHOSPEAK_BIN_DIR:-$HOME/.local/bin}"
LINK="$BIN_DIR/echospeak"
MODE="ask"
WITH_BACKEND=0
for arg in "$@"; do
  case "$arg" in
    --link) MODE="link" ;;
    --no-link) MODE="skip" ;;
    --with-backend) WITH_BACKEND=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

if command -v uv >/dev/null 2>&1; then
  if [ ! -x "$VENV/bin/python" ]; then
    uv venv --python 3.12 "$VENV"
  fi
  uv pip install --python "$VENV/bin/python" --require-hashes -r "$HERE/requirements.txt"
else
  echo "uv not found; using python3 -m venv and pip" >&2
  if [ ! -x "$VENV/bin/python" ]; then
    python3 -m venv "$VENV"
  fi
  "$VENV/bin/python" -m pip install --quiet --require-hashes -r "$HERE/requirements.txt"
fi

"$VENV/bin/python" -c "import prompt_toolkit, rich; print('Echo TUI ready: prompt_toolkit', prompt_toolkit.__version__)"

if [ "$WITH_BACKEND" = 1 ]; then
  BACKEND_VENV="$HERE/../backend/.venv"
  if [ -x "$BACKEND_VENV/bin/python" ] && "$BACKEND_VENV/bin/python" -c 'import fastapi, loguru, uvicorn' 2>/dev/null; then
    echo "Backend venv already ready at $BACKEND_VENV"
  else
    # Same Linux-filtered, hash-checked lock as the desktop build (one place for the pin list).
    "$HERE/../desktop/scripts/build-sidecar.sh" --venv-only "$BACKEND_VENV"
  fi
fi

link() {
  mkdir -p "$BIN_DIR"
  if [ -e "$LINK" ] && [ ! -L "$LINK" ]; then
    echo "$LINK exists and is not a symlink; leaving it alone" >&2
    return 1
  fi
  ln -sfn "$HERE/echospeak" "$LINK"
  echo "linked $LINK -> $HERE/echospeak"
  case ":$PATH:" in *":$BIN_DIR:"*) ;; *) echo "note: $BIN_DIR is not on your PATH" >&2 ;; esac
}

case "$MODE" in
  link) link ;;
  skip) echo "run it with: $HERE/echospeak" ;;
  ask)
    if [ -t 0 ]; then
      read -r -p "Symlink $LINK -> $HERE/echospeak? [y/N] " answer
      case "$answer" in [yY]*) link ;; *) echo "not linked; run it with: $HERE/echospeak" ;; esac
    else
      echo "not linked (no terminal to ask); rerun with --link, or run: $HERE/echospeak"
    fi
    ;;
esac
