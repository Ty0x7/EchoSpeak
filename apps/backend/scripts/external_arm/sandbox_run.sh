#!/usr/bin/env bash
# Run one external-harness process (Hermes, or a probe) in a disposable, isolated sandbox. No sudo, no containers.
#
#   sandbox_run.sh RUN_DIR MODEL_PORT [--ro SRC:DST ...] -- COMMAND [ARGS...]
#
# Boundary: a user systemd scope (memory, swap, CPU, task limits) around bubblewrap with every namespace unshared.
# Visible inside: /usr and system libraries read-only, RUN_DIR/home as $HOME (empty), RUN_DIR/workspace (the case),
# any --ro mounts (e.g. the pinned Hermes checkout and its venv). Never mounted: the real home, /run/user (keyring,
# D-Bus), other projects, EchoSpeak data. Network: only 127.0.0.1:8095 inside, forwarded over a Unix socket to
# 127.0.0.1:MODEL_PORT on the host (the model recorder or the model server). Everything else has no route.
set -Eeuo pipefail
run=$(readlink -f "$1"); port=$2; shift 2
ro=(); while [ "${1:-}" != "--" ]; do [ "$1" = --ro ] && ro+=(--ro-bind "${2%%:*}" "${2#*:}"); shift 2; done; shift
mkdir -p "$run"/{home,workspace,sock,logs}; chmod 700 "$run"
: > "$run/logs/start.marker"
sock="$run/sock/model.sock"; rm -f "$sock"
socat UNIX-LISTEN:"$sock",fork,mode=600 TCP:127.0.0.1:"$port" 2>>"$run/logs/socat-host.log" & bridge=$!
trap 'kill $bridge 2>/dev/null || true' EXIT
for _ in $(seq 50); do [ -S "$sock" ] && break; sleep 0.1; done
systemd-run --user --scope --quiet --collect \
  -p MemoryMax=4G -p MemorySwapMax=1G -p CPUQuota=600% -p TasksMax=256 \
  bwrap --unshare-all --die-with-parent --new-session --clearenv \
    --ro-bind /usr /usr --symlink usr/lib /lib --symlink usr/lib64 /lib64 --symlink usr/bin /bin --symlink usr/sbin /sbin \
    --ro-bind /etc/ssl /etc/ssl --ro-bind /etc/ca-certificates /etc/ca-certificates --ro-bind-try /etc/localtime /etc/localtime \
    --ro-bind /etc/passwd /etc/passwd --ro-bind /etc/group /etc/group \
    --proc /proc --dev /dev --tmpfs /tmp --tmpfs /run \
    --bind "$run/home" /home/eval --bind "$run/workspace" /workspace --bind "$run/sock" /run/model \
    "${ro[@]}" \
    --setenv HOME /home/eval --setenv USER eval --setenv PATH /usr/bin:/bin --setenv LANG C.UTF-8 \
    --setenv TMPDIR /tmp --chdir /workspace \
    /bin/sh -c 'socat TCP-LISTEN:8095,bind=127.0.0.1,fork,reuseaddr UNIX-CONNECT:/run/model/model.sock 2>/tmp/socat.log & sleep 0.3; exec "$@"' sh "$@" \
  > >(tee "$run/logs/stdout.log") 2> >(tee "$run/logs/stderr.log" >&2)
status=$?
# Post-run audit: nothing outside RUN_DIR in the real home changed during the run.
find "$HOME" -xdev -newer "$run/logs/start.marker" -not -path "$run/*" -not -path "$HOME/.cache/*" -not -path "$HOME/.local/state/*" \
  -not -path "$HOME/.config/pulse/*" -not -path "$HOME/.local/share/ai.echospeak.desktop/*" -not -path "$HOME/.claude/*" \
  -not -path "$HOME/.ollama/*" -type f 2>/dev/null | head -50 > "$run/logs/home-audit.txt" || true
exit $status
