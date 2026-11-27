"""One evaluation arm on a private model server: start it, measure it, run the suites, judge, stop it.

``harness_campaign.py`` drives models on the shared Ollama service. A comparison between an Ollama model and a
model served by llama-server (an OpenAI-compatible host) needs both on equal private footing, with the same
resource evidence for each:

    python scripts/harness_arm.py --label qwen-32k --server ollama --model qwen3.5:9b --context 32768 \
        --sets everything,fail,audit,long-task --repeat 3
    python scripts/harness_arm.py --label bonsai-32k --server llama --model Ternary-Bonsai-2.5-27B \
        --llama-bin ~/models/bonsai/runtime/prism/llama-server --context 32768 \
        --llama-args "-m ~/models/bonsai/gguf/X.gguf -ngl 99 -fa on -np 1 --jinja --min-p 0" \
        --profile '{"thinking_supported": true}' --sets dev --repeat 1

What it does, in order:

1. takes the machine-wide evaluation lock (eval_server.py) and refuses to start while the shared Ollama service
   has a model loaded or another GPU workload is running;
2. starts the private server with its log in the arm folder, and measures the cold load (process start to a
   first completed request) and the warm reply speed from the server's own timings;
3. samples GPU memory, utilisation, temperature and power, available memory, swap and the server's resident memory
   every two seconds (``resources.jsonl``) and runs a watchdog: available memory under ``--min-available-mb`` for
   ten seconds, or the shared service loading a model, stops the arm cleanly with the reason recorded. Completed
   case files stay valid; the missing ones can be resumed with ``--only``;
4. runs ``harness_eval.py`` once per suite against the server (through the recorder) and judges each run at the
   served window;
5. stops the server and records how long the GPU memory took to return.

Everything lands in ``data/evals/harness/<stamp>-arm-<label>/`` (``arm.json`` plus each run folder). Nothing is
downloaded, no paid endpoint is reachable (harness_eval's zero-billing rules apply), and the owner's shared
service is never touched.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "evals" / "harness"
sys.path.insert(0, str(ROOT / "scripts"))

import eval_server  # noqa: E402


def _json(url: str, body: Optional[dict] = None, timeout: float = 600) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as reply:
        return json.load(reply)


def meminfo() -> dict[str, int]:
    values = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, _, rest = line.partition(":")
        if key in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
            values[key] = int(rest.split()[0]) // 1024
    return values


def group_rss_mb(group: int) -> int:
    total = 0
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if os.getpgid(int(entry.name)) != group:
                continue
            for line in (entry / "status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    total += int(line.split()[1])
        except (OSError, ProcessLookupError, ValueError):
            continue
    return total // 1024


def cpu_times() -> tuple[int, int]:
    parts = [int(v) for v in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
    idle = parts[3] + parts[4]
    return sum(parts), idle


def gpu_sample() -> dict[str, Any]:
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw",
                          "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10).stdout
    used, total, util, temp, power = (v.strip() for v in out.splitlines()[0].split(","))
    return {"vram_used_mb": int(used), "vram_total_mb": int(total), "gpu_util": int(util), "gpu_temp_c": int(temp),
            "gpu_power_w": float(power) if power.replace(".", "", 1).isdigit() else None}


class Sampler(threading.Thread):
    """Resource samples every ``period`` seconds, plus the watchdog that stops the arm."""

    def __init__(self, path: Path, group: int, min_available: int, stop_arm: threading.Event, period: float = 2.0):
        super().__init__(daemon=True)
        self.path, self.group, self.min_available, self.stop_arm, self.period = path, group, min_available, stop_arm, period
        self.done = threading.Event()
        self.reason = ""
        self.peak: dict[str, Any] = {}
        self.events: list[dict[str, Any]] = []

    def run(self) -> None:
        low_since = 0.0
        last = cpu_times()
        with open(self.path, "a", encoding="utf-8") as out:
            while not self.done.wait(self.period):
                try:
                    now = time.time()
                    mem = meminfo()
                    total, idle = cpu_times()
                    cpu = 100.0 * (1 - (idle - last[1]) / max(1, total - last[0]))
                    last = (total, idle)
                    row = {"t": round(now, 1), **gpu_sample(), "mem_available_mb": mem["MemAvailable"],
                           "swap_used_mb": mem["SwapTotal"] - mem["SwapFree"], "server_rss_mb": group_rss_mb(self.group),
                           "cpu_pct": round(cpu, 1)}
                except Exception as exc:  # a failed sample is recorded, never fatal
                    row = {"t": round(time.time(), 1), "error": str(exc)[:200]}
                out.write(json.dumps(row) + "\n")
                out.flush()
                for key in ("vram_used_mb", "server_rss_mb", "swap_used_mb", "gpu_temp_c", "gpu_util"):
                    if key in row:
                        self.peak[key] = max(self.peak.get(key, 0), row[key])
                if "mem_available_mb" in row:
                    self.peak["mem_available_min_mb"] = min(self.peak.get("mem_available_min_mb", 1 << 30),
                                                            row["mem_available_mb"])
                    if row["mem_available_mb"] < self.min_available:
                        low_since = low_since or now
                        if now - low_since >= 10 and not self.reason:
                            self.reason = f"available memory {row['mem_available_mb']} MB < {self.min_available} MB for 10 s"
                    else:
                        low_since = 0.0
                shared = eval_server.busy()
                if shared and not self.reason:
                    self.reason = shared
                if self.reason and not self.stop_arm.is_set():
                    self.events.append({"t": round(now, 1), "watchdog": self.reason})
                    self.stop_arm.set()


def start_llama(args: argparse.Namespace, log: Any) -> tuple[subprocess.Popen, str]:
    port = eval_server._free_port()
    binary = os.path.expanduser(args.llama_bin)
    argv = [binary, "--host", "127.0.0.1", "--port", str(port), "-c", str(args.context),
            *[os.path.expanduser(a) for a in shlex.split(args.llama_args)]]
    env = {**os.environ, "LD_LIBRARY_PATH": os.pathsep.join(
        [str(Path(binary).parent), *[os.path.expanduser(p) for p in args.lib_path], os.environ.get("LD_LIBRARY_PATH", "")]).strip(os.pathsep)}
    log.write("argv: " + shlex.join(argv) + "\n")
    log.flush()
    server = subprocess.Popen(argv, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    return server, f"http://127.0.0.1:{port}"


def wait_ready(server: subprocess.Popen, url: str, kind: str, timeout: float = 600) -> None:
    deadline = time.monotonic() + timeout
    route = "/health" if kind == "llama" else "/api/version"
    while True:
        if server.poll() is not None:
            raise RuntimeError(f"the private {kind} server exited ({server.returncode})")
        try:
            status = _json(url + route, timeout=3)
            if kind != "llama" or status.get("status") == "ok":
                return
        except OSError:
            pass
        except ValueError:
            pass
        if time.monotonic() > deadline:
            raise RuntimeError(f"the private {kind} server did not become ready in {timeout:.0f}s")
        time.sleep(0.5)


def probe(url: str, model: str, kind: str, extra: dict[str, Any]) -> dict[str, Any]:
    """A short real request through the OpenAI-compatible route: the load cost on first use, the speed after."""
    body = {"model": model, "stream": False, "max_tokens": 1024,
            "messages": [{"role": "user", "content": "Write one sentence about the sea."}], **extra}
    started = time.time()
    reply = _json(url + "/v1/chat/completions", body)
    choice = (reply.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    return {"seconds": round(time.time() - started, 2), "finish_reason": choice.get("finish_reason"),
            "content": (message.get("content") or "")[:300], "reasoning_chars": len(message.get("reasoning_content") or message.get("reasoning") or ""),
            "usage": reply.get("usage"), "timings": reply.get("timings")}


def wait_vram_release(baseline_mb: int, timeout: float = 60) -> float:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if gpu_sample()["vram_used_mb"] <= baseline_mb + 200:
            return round(time.monotonic() - started, 2)
        time.sleep(0.5)
    return -1.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--label", required=True)
    parser.add_argument("--server", choices=("ollama", "llama"), required=True)
    parser.add_argument("--model", required=True, help="The model name sent to the server")
    parser.add_argument("--context", type=int, default=32768)
    parser.add_argument("--kv", default="q8_0", help="Ollama KV cache type (llama-server: pass --cache-type-* in --llama-args)")
    parser.add_argument("--llama-bin", default="")
    parser.add_argument("--llama-args", default="")
    parser.add_argument("--lib-path", action="append", default=[], help="Extra LD_LIBRARY_PATH entries for llama-server")
    parser.add_argument("--profile", default="", help="The model's capability profile (JSON), as Settings would hold it")
    parser.add_argument("--provider", default="", help="Backend adapter (default: ollama, or localai for llama)")
    parser.add_argument("--sets", default="dev")
    parser.add_argument("--only", default="")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--env", action="append", default=[])
    parser.add_argument("--probe-extra", default="{}", help="JSON merged into the probe request")
    parser.add_argument("--min-available-mb", type=int, default=1500)
    parser.add_argument("--no-judge", action="store_true")
    args = parser.parse_args()

    arm_dir = OUT / f"{time.strftime('%Y%m%d-%H%M%S')}-arm-{args.label}"
    arm_dir.mkdir(parents=True)
    provider = args.provider or ("ollama" if args.server == "ollama" else "localai")
    record: dict[str, Any] = {"label": args.label, "server": args.server, "model": args.model, "context": args.context,
                              "provider_adapter": provider, "argv": sys.argv, "started": time.time(),
                              "baseline": {"gpu": gpu_sample(), "mem": meminfo()}, "runs": [], "events": []}
    stop_arm = threading.Event()
    log_path = arm_dir / "server.log"
    lock_file = eval_server._lock_path()
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_file, "a+") as lock, open(log_path, "a", encoding="utf-8", errors="replace") as log:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("another private evaluation holds the GPU lock") from None
        if reason := (eval_server.busy() or eval_server.gpu_busy(None, min_free_mib=5000)):
            raise SystemExit(f"not starting: {reason}")
        t0 = time.time()
        if args.server == "ollama":
            port = eval_server._free_port()
            url = f"http://127.0.0.1:{port}"
            server = subprocess.Popen(["ollama", "serve"], env=eval_server.environment(port, args.context, args.kv),
                                      stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        else:
            server, url = start_llama(args, log)
        sampler = Sampler(arm_dir / "resources.jsonl", server.pid, args.min_available_mb, stop_arm)
        sampler.start()
        try:
            wait_ready(server, url, args.server)
            record["server_ready_s"] = round(time.time() - t0, 2)
            extra = json.loads(args.probe_extra)
            record["probe_cold"] = probe(url, args.model, args.server, extra)
            record["load_to_first_reply_s"] = round(time.time() - t0, 2)
            record["probe_warm"] = probe(url, args.model, args.server, extra)
            if args.server == "ollama":
                record["served_context"] = eval_server.served_context(url)
            else:
                props = _json(url + "/props", timeout=10)
                record["served_context"] = int((props.get("default_generation_settings") or {}).get("n_ctx") or 0)
                record["props"] = {k: props.get(k) for k in ("total_slots", "chat_template_caps", "modalities", "build_info")
                                   if k in props}
            record["after_load"] = {"gpu": gpu_sample(), "mem": meminfo(), "server_rss_mb": group_rss_mb(server.pid)}
            (arm_dir / "arm.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
            if record["served_context"] and record["served_context"] != args.context:
                raise RuntimeError(f"served window {record['served_context']} != requested {args.context}")
            env_args: list[str] = []
            for item in args.env:
                env_args += ["--env", item]
            if args.profile:
                profiles = {args.model: json.loads(args.profile), f"{provider}:{args.model}": json.loads(args.profile)}
                env_args += ["--env", "MODEL_CAPABILITY_PROFILES=" + json.dumps(profiles)]
            env_args += ["--env", f"LOCAL_MODEL_CONTEXT={args.context}"]
            for suite in [s for s in args.sets.split(",") if s]:
                if stop_arm.is_set():
                    break
                label = f"{args.label}-{suite}"
                cmd = [sys.executable, str(ROOT / "scripts" / "harness_eval.py"), "--model", args.model,
                       "--upstream", url, "--provider", provider, "--server-log", str(log_path),
                       "--set", suite, "--repeat", str(args.repeat), "--label", label, *env_args]
                if args.only:
                    cmd += ["--only", args.only]
                started = time.time()
                with open(arm_dir / f"{suite}.log", "w", encoding="utf-8") as out:
                    proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
                    while proc.poll() is None:
                        if stop_arm.wait(2):
                            os.killpg(proc.pid, signal.SIGINT)
                            try:
                                proc.wait(120)
                            except subprocess.TimeoutExpired:
                                os.killpg(proc.pid, signal.SIGKILL)
                            break
                text = (arm_dir / f"{suite}.log").read_text(encoding="utf-8", errors="replace")
                run_dir = next((line.split(" · ")[-1].strip() for line in text.splitlines() if line.startswith("Harness eval ")), "")
                run = {"suite": suite, "run_dir": run_dir, "exit": proc.returncode, "seconds": round(time.time() - started, 1),
                       "stopped_by_watchdog": stop_arm.is_set()}
                record["runs"].append(run)
                if run_dir and not args.no_judge:
                    judge = subprocess.run([sys.executable, str(ROOT / "scripts" / "harness_judge.py"), run_dir,
                                            "--window", str(args.context)], cwd=str(ROOT), capture_output=True, text=True)
                    run["judge_exit"] = judge.returncode
                    (Path(run_dir) / "judge.stdout").write_text(judge.stdout[-20000:] + judge.stderr[-5000:], encoding="utf-8")
                (arm_dir / "arm.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            sampler.done.set()
            sampler.join(10)
            record["peak"] = sampler.peak
            record["events"] += sampler.events
            record["watchdog_reason"] = sampler.reason
            stop_t = time.time()
            eval_server.stop(server)
            record["stop_s"] = round(time.time() - stop_t, 2)
            record["vram_release_s"] = wait_vram_release(record["baseline"]["gpu"]["vram_used_mb"])
            record["after_stop"] = {"gpu": gpu_sample(), "mem": meminfo()}
            record["ended"] = time.time()
            (arm_dir / "arm.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
            fcntl.flock(lock, fcntl.LOCK_UN)
    print(json.dumps({"arm": str(arm_dir), "runs": record["runs"], "error": record.get("error"),
                      "watchdog": record.get("watchdog_reason")}, indent=1))
    return 0 if not record.get("error") else 1


if __name__ == "__main__":
    sys.exit(main())
