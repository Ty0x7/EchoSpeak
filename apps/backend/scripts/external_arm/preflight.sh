#!/usr/bin/env bash
# Preflight for an external-harness arm (Hermes or raw model). Read-only: reports, changes nothing.
#   preflight.sh [HERMES_CHECKOUT] [EXPECTED_COMMIT]
set -uo pipefail
fail=0; ok(){ printf '  ok    %s\n' "$*"; }; bad(){ printf '  FAIL  %s\n' "$*"; fail=1; }; warn(){ printf '  warn  %s\n' "$*"; }
echo "tools"
for t in bwrap socat systemd-run uv python3.14 rg; do command -v $t >/dev/null && ok "$t $($t --version 2>&1 | head -1 | cut -c1-40)" || bad "$t missing"; done
[ "$(cat /proc/sys/kernel/unprivileged_userns_clone 2>/dev/null || echo 1)" = 1 ] && ok "unprivileged user namespaces" || bad "user namespaces disabled"
echo "machine"
avail=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo); [ $avail -ge 6000 ] && ok "RAM available ${avail} MB" || bad "RAM available ${avail} MB (< 6000)"
free_vram=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits); [ $free_vram -ge 6600 ] && ok "VRAM free ${free_vram} MB" || bad "VRAM free ${free_vram} MB (< 6600; close GPU-heavy windows)"
temp=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader); [ $temp -le 70 ] && ok "GPU ${temp} C idle" || warn "GPU ${temp} C"
disk=$(df -Pm "$HOME" | awk 'NR==2{print $4}'); [ $disk -ge 20000 ] && ok "disk free ${disk} MB" || bad "disk free ${disk} MB"
echo "model runtimes"
loaded=$(curl -s --max-time 2 localhost:11434/api/ps | python3 -c "import json,sys; print(','.join(m['name'] for m in json.load(sys.stdin)['models']))" 2>/dev/null)
[ -z "$loaded" ] && ok "shared Ollama idle" || bad "shared Ollama has $loaded loaded"
systemctl --user is-active --quiet echospeak-bonsai && warn "echospeak-bonsai service running (stop it; arms use a private server)" || ok "Bonsai service stopped"
flock -n "$HOME/.cache/echospeak/eval-gpu.lock" true && ok "eval GPU lock free" || bad "eval GPU lock held"
pgrep -x llama-server >/dev/null && bad "a llama-server is running" || ok "no llama-server running"
echo "evidence"
for f in Ternary-Bonsai-2.5-27B-PTQ1_0.gguf Ternary-Bonsai-2.5-27B-PTQ1_0-kv-bias.gguf; do [ -f ~/models/bonsai/gguf/$f ] && ok "$f" || bad "$f missing"; done
if [ -n "${1:-}" ]; then
  echo "hermes checkout"
  c=$(git -C "$1" rev-parse HEAD 2>/dev/null); [ "$c" = "${2:-$c}" ] && ok "commit $c" || bad "commit $c != ${2}"
  [ -z "$(git -C "$1" status --porcelain 2>/dev/null)" ] && ok "clean tree" || bad "checkout modified"
fi
[ $fail = 0 ] && echo "PREFLIGHT PASS" || echo "PREFLIGHT FAIL"; exit $fail
