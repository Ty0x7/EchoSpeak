# Harness comparison plan: Bonsai 2.5 27B with EchoSpeak, with Hermes Agent, and with no harness

Status: **prepared for review, 27 Nov 2026.** Hermes source was read at the pinned tag. Hermes is not installed and no
external arm has run. The sandbox and preflight were built and tested with a probe. Campaign issue to be opened on
approval.
Builds on [bonsai-vs-qwen-2026-11.md](bonsai-vs-qwen-2026-11.md) (#240), whose EchoSpeak results are reused, not
repeated.

## 0. Findings from preparation (27 Nov 2026)

**Hermes' context floor is enforced in code and measured honestly.**
- Source: v0.23.4, commit `8fc0374`. `agent/model_metadata.py` sets `MINIMUM_CONTEXT_LENGTH = 64_000`, and
  `agent/agent_init.py` `_enforce_minimum_context` raises at startup when the window is smaller.
- For a llama.cpp server it reads the allocated `n_ctx` from `/props` (`_apply_llamacpp_props`), so at 32K it sees
  32K and refuses.
- Its own remedy text suggests overriding the window in config. We **won't**: that would misrepresent a 32K runtime as
  64K. The only built-in exception is an explicit `model.context_length` with the **LM Studio** provider, which makes
  LM Studio-specific API calls. Using it against llama-server would also misidentify the runtime.

**Consequence: there is no faithful Hermes configuration on this GPU at 32K.** A faithful arm needs a genuine 64K
server. On an 8 GB card with Bonsai that means the KV cache in system RAM (`-nkvo`), measured at 4.8–13 tok/s with
exact recall at 48K. The arms therefore become:

| Arm | Configuration | Compared with |
|---|---|---|
| **H64** Hermes | Bonsai at a true 64K, KV in RAM | **E64** only |
| **E64** EchoSpeak | the same server and flags as H64, on the same cases | H64 (quality and outcomes; speed reported but caveated) |
| **E32** EchoSpeak (existing) | 32K, GPU KV: the saved Bonsai campaign | R32, and the E64 subset (does 64K-in-RAM change EchoSpeak's outcomes?) |
| **R32** raw model | same server as E32 | E32 |

**H64 is never compared with E32.** The window, KV placement and speed differ. Results from different
configurations go in separate tables.

**Dependency and installer review (read only):**
- **Packages:** 355 locked in `uv.lock`; Python `>=3.11,<3.15`. Optional extras pull web-search, TTS and image
  providers; the evaluation installs none of them.
- **Installer** (`scripts/install.sh`, 1,320 lines): downloads a pinned uv, may run `sudo` to install libatomic for
  Node, clones into `~/.hermes`, adds `~/.local/bin/hermes`, installs Chromium unless told not to, and writes a local
  install receipt under `$HERMES_HOME/telemetry/`. **Not used.** A pinned checkout plus `uv sync --frozen` into the
  run folder replaces it.
- **Telemetry:** `telemetry.shared_metrics.enabled` and `send` both default to `False` (opt-in). The endpoint
  `https://telemetry.nousresearch.com/v1/telemetry` can't be overridden through the environment.
- **Updates:** `updates.check` defaults to `True` (a passive GitHub API call). The run config sets it to `false`, and
  the sandbox has no route out anyway.

**Portable scorecard** (`apps/backend/scripts/portable_score.py`, tested):
- **Excluded checks:** 12 kinds that only EchoSpeak's machinery can satisfy (its approval and read cards, harness
  status, usage accounting, nudges, stop reasons) or that name a tool rather than an outcome.
- **Reported:** task success, per-dimension pass rates, security violations listed separately, and successes per
  million tokens and per hour.
- **Saved EchoSpeak runs re-scored**, with no new compute:

| Arm (existing evidence) | Success (all / held-out) | Completion · tools · efficiency · recovery · security pass rates | Security trials | Successes / 1M tokens | Successes / hour |
|---|---|---|---|---|---|
| E32 Bonsai, matched (118 trials) | 0.88 / 0.86 | 0.89 · 0.98 · 0.90 · 0.99 · 1.00 | 0 | 12.4 | 26.2 |
| E32 Bonsai, best practical (59) | 0.86 / 0.88 | 0.90 · 0.98 · 0.89 · 0.99 · 1.00 | 0 | 11.8 | 22.0 |
| E32 Qwen 9B, matched (118) | 0.81 / 0.80 | 0.85 · 0.96 · 0.85 · 0.98 · 1.00 | 1 | 6.5 | 20.5 |
| E32 Qwen 9B, best practical (59) | 0.80 / 0.72 | 0.86 · 0.95 · 0.83 · 0.97 · 0.99 | 1 | 6.0 | 21.7 |

**Sandbox and preflight, built and tested:**
- `apps/backend/scripts/external_arm/sandbox_run.sh` was run with `escape_probe.py` against the live model service.
- **Blocked:** the real home, `~/.ssh`, EchoSpeak data, `/run/user`, writes outside the workspace, DNS, the
  internet, and host Ollama.
- **Allowed:** the model endpoint and the workspace only.
- **Audit:** the post-run home check was empty.
- **Preflight:** `preflight.sh` checks tools, namespaces, RAM, VRAM, temperature, disk, idle runtimes, the GPU lock,
  model files and the pinned, clean Hermes checkout. It fails when anything competes for the GPU.

## 1. Question

How much of an agent's capability on this workstation comes from the model, and how much from the harness around it?
Three configurations of the **same weights** (Ternary Bonsai 2.5 27B `PTQ1_0`, sha256 `929560ec…`) on the **same
runtime** (Prism llama.cpp `b12232`, same server flags):

| Arm | What runs | Status |
|---|---|---|
| **E** EchoSpeak | the EchoSpeak harness (`localai` provider → llama-server) | **done**: 59 cases, matched (2 reps) and best practical (1 rep); reused |
| **H** Hermes Agent | Nous Research Hermes Agent v0.23.4 (MIT, released 23 Nov 2026) with a custom OpenAI-compatible provider → the same llama-server | to run |
| **R** Raw model | chat completions to the same server, no agent loop | to run |

## 2. What we know about Hermes (docs and README, read 27 Nov; not installed)

- **Shape:** Python agent (pinned Python 3.14, uv) with a terminal UI, a gateway, an API server, a Python library
  (`AIAgent`), and a batch runner that writes ShareGPT-style trajectories.
- **Tools:** 70+ tools in about 28 toolsets: terminal, file, web, browser, memory, skills, delegation, code execution,
  clarify, cron and MCP. They're enabled per toolset (`hermes chat --toolsets …`, `agent.disabled_toolsets`).
- **Memory:** `MEMORY.md` and `USER.md` under `~/.hermes/memories` (2,200 and 1,375 characters), as a frozen
  snapshot per session. Plus `session_search` over `state.db`.
- **Skills:** SKILL.md skills in agentskills.io format. Our existing skill fixtures are format-compatible.
- **Providers:** any OpenAI-compatible endpoint (`provider: custom`, `model.base_url`). llama-server is documented
  with `--jinja -fa -c 64000`.
- **Context: at least 64,000 tokens for agent use with tools; smaller windows are rejected at startup.** This is the
  main compatibility constraint (§4).
- **Execution and approvals:**
  - **Terminal backends:** local (no isolation; the docs say "same filesystem access as your user"), docker, ssh,
    modal, daytona and others.
  - **Approvals:** `approvals.mode` is smart (an auxiliary model judges risk), manual or off. Timeouts deny.
  - **Built-in protections:** a hard blocklist, and a write denylist the docs call "defense-in-depth, not a hard
    boundary".
- **Network and installation:**
  - **Network guards:** SSRF guards on web tools only; the terminal has no URL guard.
  - **Passive outbound calls:** an update check to the GitHub API once a day.
  - **Installer:** a curl-to-bash script that clones into `~/.hermes`, adds `~/.local/bin/hermes`, and installs
    Chromium and a computer-use driver unless you opt out.
- **Small-model limitation (from the docs):** models under ~30B often claim a memory save without making the call.
  We measured the same behaviour on Qwen 9B.
- **Not yet verified:**
  - whether the 64K check reads configuration or probes the server;
  - the size of Hermes' system prompt and tool schemas with a reduced toolset;
  - how `clarify` and approvals are answered in library mode;
  - whether there is a telemetry switch;
  - the contents of the `evals/` directory.

## 3. Fairness rules

1. **Same weights, runtime and flags** as Arm B of the Bonsai campaign (q4_0 KV with bias, `-ub 256`, reasoning budget
   4096, `-cent 2048`, temperature from the client). Every difference is listed in §4.
2. **Same tasks and fixtures.** Reuse `harness_eval.py` case definitions (`Case.setup`, turns, owner answers), the same
   project generators and seeds. No new tasks except where a case cannot be expressed (§5).
3. **Same evidence.** Hermes' model calls go through `model_recorder.py` (base_url → recorder → llama-server), so
   tokens, timings, reasoning and tool calls are captured exactly as for EchoSpeak.
4. **Same judge, portable subset.** `harness_judge.py` checks that need EchoSpeak internals (harness status, the V0–V4
   ladder, EchoSpeak approval records, its memory store) are marked *not applicable* for H and R. **EchoSpeak's saved
   runs are re-judged on the same portable subset**: no new model compute, and a like-for-like score for all arms.
   The full EchoSpeak score stays reported separately.
5. **Pre-registration** before any H/R run, as in the Bonsai campaign: case mapping, portable checks, weights, primary
   outcome (held-out portable score and task success), and which differences count as noise.
6. **No penalty for absent design capabilities.** The raw arm is scored only on tasks it can do by design (§5.3).
   Capabilities are reported as *not applicable*, not as zero.
7. **Repetitions:** 2 per case for H and R, to match Arm A. Report all trials. Never rerun a failure selectively.

## 4. Unavoidable differences and how each is handled

| Difference | Handling |
|---|---|
| **Hermes needs ≥64K context; Bonsai fits 32K on this GPU** | **Primary:** serve 32K and set Hermes' `model.context_length` to the value its startup check accepts, if it is config-based; requests that overflow get an honest llama-server error, recorded as *context-limited*, never silent truncation. **Control:** a small subset at a true 64K (`-nkvo`, KV in RAM; 5–13 tok/s) for both H and E, to check that the 32K primary doesn't change outcomes. If the check probes the server, the 64K configuration becomes primary for H, and E is re-run on the same subset at 64K. |
| Hermes system prompt and tool schemas differ in size | Measured offline first (render the prompt, count with `llama-tokenize`); reported per arm as "prompt overhead" |
| Toolsets differ | Hermes is restricted to the toolsets the tasks need (terminal, file, memory, skills, todo, clarify, MCP). Web, browser, image, TTS, cron and messaging stay off, as EchoSpeak's evals ran without web |
| Reasoning effort and temperature | Hermes `agent.reasoning_effort` set to match each EchoSpeak arm (medium for A, xhigh for B); temperature pinned where Hermes exposes it, otherwise server default (recorded per call by the recorder) |
| Approvals and owner answers | EchoSpeak cases answer approvals and question cards "as the owner would". Hermes runs through the library or API server with approval and clarify callbacks that return the case's scripted answer. Where Hermes cannot ask, the case is marked |
| Memory model (frozen snapshot vs live recall) | Cross-chat cases run as separate Hermes sessions (its documented semantics). Same-chat recall uses the conversation |
| Retries and compaction | Each harness keeps its own; that is part of what is measured |

## 5. Case mapping (59 EchoSpeak cases)

### 5.1 Portable to Hermes (about 45)

- **Coding and recovery:** D1, H1, T1, TH1, PJ1, PJH1, D5, H4, FC1, FCH1, LT1, FA1, FAH1.
- **Context:** D2, H3, D8, H6.
- **Honesty:** D7, H7.
- **Injection:** D3, H2.
- **Permission:** D4 and H5 (a destructive request the owner denies, through the approval callback); W1 (a write
  outside the workspace; the sandbox boundary decides, and the outcome is checked on disk).
- **Clarification:** C1 and CH1 (through `clarify`).
- **Research from given files:** R1 and RH1.
- **MCP account workflows:** X1, XU1 and XH1 (Hermes supports MCP; the same disposable accounts service).
- **Skill following:** K1 (the same SKILL.md, imported into the run's `HERMES_HOME/skills`).
- **Memory across chats:** M1, MH1, MC1, MR1, LS1–LS6, LSH1–LSH4 (Hermes `memory` tool and new sessions).
- **Multi-turn continuation:** PC1 and PCH1.

### 5.2 Not applicable to Hermes

These test EchoSpeak-specific product features, so they're reported as N/A:

- **G1, GH1:** delegation to project teammate personas.
- **MI1:** per-agent memory isolation.
- **PA1:** the project-offer flow.
- **E1, E2, EH1, EH2:** reads outside the project that need an owner grant. EchoSpeak's grant model has no Hermes
  equivalent; the sandbox simply doesn't mount those paths.
- **E3:** computer specs. It needs `system_info`; Hermes would use the terminal. Run it, but label it.

### 5.3 Raw model arm: two variants

- **R0, intelligence only.** Everything the task needs is placed in the prompt, and the single answer is judged by the
  same reply checks.
  - Cases: D2, H3, D8, H6 (logs inline); D3, H2 (injection in inline text); R1, RH1 (sources inline); C1, CH1 (does
    it ask before acting?); D7, H7 (honesty about an impossible test, code inline); same-chat LS cases (multi-turn
    chat, no memory between chats by design).
- **R1, single-shot code.** File contents go in the prompt; the evaluator writes the model's fenced files into a copy
  of the fixture and runs the independent tests once. No iteration, no tools.
  - Cases: D1, H1, T1, TH1, FC1, FCH1, plus the acceptance RW3.
- **Not applicable to the raw model** (design, not failure): anything that needs execution, file discovery,
  approvals, MCP, cross-chat memory or recovery after a failed command.

### 5.4 Read-outs

- **Harness lift per task:** E − R and H − R on the overlap.
- **Harness difference:** E − H on the portable set.
- **Capability coverage:** the share of the 59 tasks each arm can attempt at all.
- **Reliability profile:** false-success claims, critical findings, tool-call validity, timeouts, recovery rate.
- **Cost and latency:** model calls, prompt and completion tokens, median and p90 task time.

All of these are computed per category with standard errors, keeping dev and held-out separate.

## 6. Secure, disposable test environment (no sudo, no host changes)

**Boundary.** Each H run is one bubblewrap sandbox inside a user-level systemd scope.

```
systemd-run --user --scope -p MemoryMax=4G -p MemorySwapMax=1G -p CPUQuota=600% -p TasksMax=256 \
  bwrap --unshare-all --die-with-parent --new-session \
        --ro-bind /usr /usr --symlink usr/lib /lib --symlink usr/lib64 /lib64 --symlink usr/bin /bin \
        --ro-bind /etc/ssl /etc/ssl --ro-bind <hermes checkout> /opt/hermes --ro-bind <uv venv> /opt/venv \
        --bind <run>/home /home/eval --bind <run>/workspace /workspace --bind <run>/model.sock /run/model.sock \
        --tmpfs /tmp --proc /proc --dev /dev --setenv HOME /home/eval --setenv HERMES_HOME /home/eval/.hermes \
        --clearenv ... /opt/venv/bin/python /opt/harness/hermes_runner.py ...
```

- **Filesystem:**
  - Visible: the run's own home (empty, disposable), the case workspace, a read-only Hermes checkout and venv, and
    system libraries.
  - Never mounted: `~`, `~/.ssh`, `~/.config`, `~/Projects`, `~/Work`, EchoSpeak data, Obsidian, identity VMs,
    `/run/user` (the D-Bus and keyring sockets).
  - The write denylist and approvals become defense in depth; the mount table is the boundary.
- **Network:**
  - `--unshare-net` leaves only the sandbox's own loopback.
  - Inside, `socat` listens on `127.0.0.1:8095` and forwards to a bind-mounted Unix socket. Outside, a second
    `socat` connects that socket to the model recorder, and the recorder talks to llama-server.
  - The model endpoint is the **only** route out. Update checks, telemetry, pip and web tools fail closed. Every
    attempt is visible in the recorder and socat logs.
- **Credentials:** `--clearenv` plus an allowlist. No API keys exist in the sandbox. `.env` is empty, and Hermes
  provider, OAuth and portal setup is never run.
- **Installation (no installer script):**
  - Shallow-clone the `v0.23.4` tag into the evaluation folder and record its commit.
  - Review `scripts/install.sh` and `pyproject.toml`, then run `uv sync --frozen` with Python 3.14 into a venv under
    the run folder (uv cache local to it).
  - Skip Chromium and the computer-use driver. Node is used only if a needed toolset requires it.
  - The one network step is `uv sync`. Its lock file and package hashes are recorded.
- **Hermes configuration** (in the run's `HERMES_HOME/config.yaml`):
  - `provider: custom`, `base_url: http://127.0.0.1:8095/v1`, `terminal.backend: local` (inside the sandbox);
  - updates off, the browser, web, cron and messaging toolsets disabled;
  - approvals in manual mode, answered by the runner's callbacks from the case script.
- **Resources:**
  - The existing `harness_arm.py` pattern applies: GPU lock, idle check, 2-second samples of VRAM, RAM, swap,
    temperature and power, plus a memory watchdog (stop below 1.5 GB available).
  - The scope caps Hermes at 4 GB of RAM and 256 tasks, and Bonsai's service sleeps between runs.
- **Evidence per case:**
  - recorder JSONL and the Hermes session database (copied out);
  - workspace snapshots before and after;
  - stdout and stderr, the socat log, resource samples and the judge output.
- **Cleanup and rollback:**
  - A run touches only `<eval>/runs/<id>/`. A post-run audit lists files in `$HOME` newer than the run's start
    marker, outside the run folder; it must be empty, or the run is flagged.
  - Removing the run folder, the uv cache and the clone restores the machine exactly. Nothing was installed system-
    wide, and no service or profile was changed.
- **Raw arm R:** no sandbox needed (no tools). It talks to the recorder directly, and R1's file application happens
  in a disposable copy run under the existing bwrap test runner.

## 7. Work breakdown (after approval)

1. **Clone and pin:** clone Hermes, review it, build the venv (about 15 min). Then measure prompt size and the 64K
   check offline, no model calls (about 30 min).
2. **Build:** `hermes_runner.py`, a library-mode runner with scripted approvals and clarify, multi-turn sessions and
   case setup reuse; plus the portable-judge flag in `harness_judge.py`, and the re-judge of saved E runs.
3. **Smoke test:** 3 cases on H, 3 on R, and a sandbox escape test (try to read `~/.ssh`, reach the network, write
   outside the workspace), all expected to fail.
4. **Pre-register.** Then run H on 45 portable cases × 2 (about 4 h) and R on about 25 cases × 2 (about 1 h). Then
   the 64K fidelity subset (about 2 h).
5. **Report:** a repo document plus HTML, Obsidian, and a blog post if you approve.

Total GPU time is about 7–8 h, compared with about 20 h for repeating the full EchoSpeak matrix, which this avoids.
