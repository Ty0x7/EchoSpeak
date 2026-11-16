# Providers, the Linux terminal sandbox, and the harness-testing skill

> **Status, 16 November 2026.** This work is on branch `five-model-harness` and is not merged or released. It covers:
> - the Ollama vs LM Studio comparison;
> - the provider adapter audit;
> - the execution-environment and isolation audit after the move from Windows to Linux (Omarchy/Arch);
> - the changes made as a result;
> - the reusable `model-harness-testing` skill.
>
> **How each finding was established:**
> - Ollama findings were measured live (Ollama 0.40.16).
> - LM Studio isn't installed on this machine, so its findings come from the documentation. Its one code change is
>   tested against a local stub that serves the documented response shape.
> - Sandbox findings were checked live on this machine (kernel 7.2.15, bubblewrap from Arch).
>
> **Companions:**
> - [harness-evaluation.md](harness-evaluation.md): the method.
> - [five-model-evaluation.md](five-model-evaluation.md): the five-model milestone.
> - [new-model-evaluation.md](new-model-evaluation.md): the two new models.

## 1. Results

| Area | Finding | Action |
|---|---|---|
| Linux terminal | Auto mode fell back to running every command directly on the PC, each behind an approval prompt. The Docker sandbox can't be used: the owner's account isn't in the `docker` group, and joining it would be root-equivalent. | **Changed.** Added a bubblewrap sandbox (`agent/lean/linux_sandbox.py`), used by Auto on Linux. |
| Unsafe fallback | Auto's fallback to this PC was already disclosed and approval-gated. Explicit "Sandbox" mode refuses when Docker is down. | **Kept.** The new `bwrap` mode refuses too and never falls back silently. |
| Docker sandbox | Not privileged, no Docker socket, `no-new-privileges`, workspace-only mounts, memory and CPU limits. On Linux it runs as root, so files it writes into the project become root-owned. | **Kept** for Windows and macOS, and as an explicit choice on Linux. Auto on Linux prefers bubblewrap. |
| LM Studio window | The harness learned the served context window only from Ollama. LM Studio sets the window when it loads a model, often 4,096 tokens, and its OpenAI endpoint doesn't report it. | **Changed.** `window.served()` now reads LM Studio's `/api/v1/models` `loaded_instances[].config.context_length`. |
| Reasoning streams | Both servers' reasoning fields (`reasoning`, `reasoning_content`) are already read. | Kept. |
| `LM_STUDIO_TOOL_CALLING` | Only a diagnostic flag from the legacy agent; the Lean loop doesn't use it. | Kept. Documented here so nobody tunes it expecting an effect. |
| Testing procedure | The procedure behind three milestones lived only in reports. | **New** skill `.agents/skills/model-harness-testing/` (with a Claude pointer), plus an Obsidian evaluation-note template. |

## 2. Ollama and LM Studio

Both servers wrap llama.cpp for GGUF models. They differ in where the window, the template and the reasoning
controls are set, which is where a harness gets surprised.

| | Ollama 0.40 (measured here) | LM Studio 0.4 (documentation) |
|---|---|---|
| **Architecture** | A server with an OpenAI-compatible `/v1`, a native `/api` and one loaded model here (`OLLAMA_MAX_LOADED_MODELS=1`). | A desktop app and headless `lms` server, with OpenAI-compatible `/v1` and native REST `/api/v1`. GGUF runs on llama.cpp, MLX on Apple machines. |
| **Context window** | Set where the server starts (`OLLAMA_CONTEXT_LENGTH`, 16,384 here). `/v1` ignores a per-request size, and an over-long prompt is cut silently with HTTP 200. `/api/ps` reports the served window. | Set when a model is loaded (`context_length` on `/api/v1/models/load`). `/api/v1/models` reports it per loaded instance; `max_context_length` is the trained maximum, not the served window. The overflow policy (rolling window, truncate middle, or stop) is a setting. |
| **Templates** | Ollama's own renderer and parser per family (`RENDERER qwen3.5`, `gemma4`). A wrong or missing one drops tools from the prompt: DeepSeek R1, measured in the five-model run. | Jinja chat templates from the GGUF. |
| **Reasoning** | `think` (boolean or level) and `reasoning_effort` on `/v1`. The reasoning stream is `reasoning`. | `reasoning_content` or `reasoning`, depending on version. `capabilities.reasoning.allowed_options` lists what a model supports. Turning thinking off on `/v1` is reported unreliable for Qwen 3.5 ([bug #1990](https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/1990)). |
| **Tools** | `tools` on `/v1`, parsed by the family parser. Text-form calls are recovered by EchoSpeak's lenient parser. | `tools` on `/v1` (0.3.6 and later). `capabilities.trained_for_tool_use` is reported per model. |
| **Structured output** | `response_format` / `format` (grammar). | `response_format` with a JSON schema (grammar for GGUF). Thinking models may put the JSON into reasoning (one third-party report). |
| **Streaming and usage** | SSE; usage and timings in the last chunk. EchoSpeak's recorder keeps llama.cpp `timings`. | SSE; usage in the last chunk. |
| **Embeddings and RAG** | `/api/embed` and `/v1/embeddings`. | `/v1/embeddings`; embedding models are listed with `type: "embedding"`. EchoSpeak lists them via `lmstudio_embedding_models()`. |
| **Discovery** | `/api/tags` (installed), `/api/show` (capabilities, template), `/api/ps` (loaded, window, VRAM share). | `/api/v1/models`: installed, loaded instances, capabilities, quantization. `/api/v1/models/load` loads a model. EchoSpeak's `ensure_selected_model_ready` already loads the session's model and never substitutes another. |

**How to tell a provider problem from a model or harness problem:**
- Compare the server's own prompt count with EchoSpeak's estimate. A server count far below the estimate is a
  dropped template part or a cut prompt.
- Read the server log for `truncating input prompt`.
- Replay the recorded call (`scripts/replay_call.py`) with one variable changed.

These checks are written into the skill.

**Sources:**
- [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)
- [LM Studio REST: list models](https://lmstudio.ai/docs/developer/rest/list)
- [LM Studio REST: load](https://lmstudio.ai/docs/developer/rest/load)
- [LM Studio tools](https://lmstudio.ai/docs/developer/openai-compat/tools)
- [LM Studio structured output](https://lmstudio.ai/docs/developer/core/structured-output)
- [LM Studio API changelog](https://lmstudio.ai/docs/developer/api-changelog)

## 3. Execution environment and threat model

### What runs commands, and what it can reach

The agent's terminal (`agent/lean/terminal.py`) had two back ends, both designed on Windows:

- **host:** PowerShell or bash, directly on the PC.
- **docker:** a persistent `echospeak-sandbox` container with the workspace mounted at `/work`.

Auto used Docker when it was running, and this PC otherwise. File tools never go through the terminal: they have
their own root checks and approvals.

**Measured on this machine (Omarchy, kernel 7.2.15):**
- The Docker service runs, but the owner's account isn't in the `docker` group (`permission denied` on
  `/var/run/docker.sock`). So Auto resolved to **host**, and every command asked first.
- `bwrap`, `systemd-run` and `unshare` are installed; `podman`, `firejail` and `nsjail` aren't.
- Unprivileged user namespaces are enabled.
- Active LSMs: Landlock, Yama, BPF, IMA/EVM and lockdown. AppArmor and SELinux are absent.

**Threat model.** The adversary is a model that is mistaken, or steered by planted text (README overrides, poisoned
issues, hostile dependencies), running commands as the owner. It is not a kernel exploit author.

**Trust boundaries:**
1. **The owner's files outside the workspace.** Shell startup files, `~/.config`, other projects and VMs.
2. **Credentials.** SSH and GPG keys, cloud CLIs, keyrings, browser profiles, and EchoSpeak's own data and keys.
3. **The network.** Exfiltration, downloads.
4. **The owner's other processes.** Signals, `/proc/<pid>/environ`, the session bus, the ssh-agent.
5. **EchoSpeak itself.** Its local API: a command that reaches it could approve its own actions.

The last boundary is the one [CVE-2026-82533](https://www.ox.security/blog/cve-2026-82533-deepseek-harness-ai-agent-sandbox-escape/)
broke in DeepSeek Harness. Its OS sandbox restricted writes but shared the host network, so the agent called the
harness's own API.

**The existing defences, all kept:**
- Approvals, including for destructive workspace commands, "this runs on your PC", and internet.
- The never-run list.
- `child_env` drops EchoSpeak's keys from every child.
- `secret_files` masks secrets in output.
- Privacy offline mode turns the sandbox's network off.

Approvals alone can't judge `python cleanup.py`, though. That is why Auto's host fallback asked for every command,
which made Linux noisy without making it safer.

### How other harnesses do it

| Harness | Linux mechanism | Defaults | Lesson for EchoSpeak |
|---|---|---|---|
| Claude Code ([sandboxing](https://code.claude.com/docs/en/sandboxing)) | bubblewrap, plus a host proxy (socat) for allowed domains, plus optional seccomp to block Unix sockets. | Writes: the working directory and temp. Reads: the whole machine unless denied. Protected paths include `.git/hooks` and settings. Fails open unless `failIfUnavailable`. | bubblewrap is the established tool. Fail closed. Protect paths that run later. |
| Codex ([sandboxing](https://learn.chatgpt.com/docs/sandboxing)) | bubblewrap, with Landlock and seccomp in its helper. | `workspace-write` plus `on-request`: writes to the workspace, network off, asks to go further. | The same split EchoSpeak uses: the sandbox sets the boundary, approvals decide when to cross it. |
| Hermes Agent ([security](https://hermes-agent.nousresearch.com/docs/user-guide/security/)) | Pluggable back ends: local, docker, ssh, singularity, modal and others. | Dangerous-command approval on local and SSH; skipped inside containers. Forwarded environment variables are readable inside. | Keep approvals for workspace-destructive commands even inside the sandbox: the workspace is still real files. |
| DeepSeek Harness (CVE-2026-82533 reports) | bubblewrap, Landlock or Seatbelt. | Restricted writes, but loopback shared with the host. | Network off must mean a separate network namespace, and the local API must require a key. |

[bubblewrap](https://github.com/containers/bubblewrap) itself is a toolkit; its safety is in the arguments. It needs
`--new-session` against terminal injection (CVE-2017-5226), and it relies on unprivileged user namespaces, which add
kernel attack surface. Containers alone aren't a complete boundary either: a rootful Docker daemon is root, and the
`docker` group is root-equivalent.

### What changed

`agent/lean/linux_sandbox.py` builds the `bwrap` command line, and `terminal.py` uses it when the resolved mode is
`bwrap`.

| Boundary | Inside the sandbox | Checked by |
|---|---|---|
| Files | `/` is read-only. Writable: the session's workspace folders, a private `/tmp`, `~/.cache` and `~/.npm` (so installs work). Mounts are ordered so that a workspace set to the home folder still can't read `~/.ssh`, and a workspace inside a hidden folder stays usable. | Live tests: a write to the home folder or `/etc` fails, a project write lands. |
| Credentials | Hidden folders that exist (empty tmpfs): `~/.ssh`, `~/.gnupg`, cloud CLIs, `gh`, keyrings, `pass`, browser profiles, `~/.claude`, `~/.codex`, EchoSpeak's data folder, `/run/user/<uid>` (D-Bus, ssh-agent, keyring and Wayland sockets). Credential files (`.netrc`, `.git-credentials`, `.pgpass`, `.pypirc`, `.npmrc`, …) can't be opened. Socket variables are dropped. | Live tests: `.ssh` is empty, `.netrc` is unreadable, other home files are readable. |
| Network | Its own network namespace (loopback only) unless the command asked with `network=true` and was allowed. The policy (`ask`, `on`, `off`) and privacy offline mode are unchanged. With the network allowed, the command shares the host network, including loopback. | Live test: one interface. Probe: host Ollama is unreachable. |
| Processes | Its own PID namespace, a new session, `no_new_privs`, all capabilities dropped, killed when EchoSpeak stops. | Live tests: fewer than 10 PIDs are visible, `NoNewPrivs: 1`. Probe: `sudo` refuses. |
| Failure | If bwrap can't start (not installed, or user namespaces blocked by AppArmor or a sysctl), a `bwrap` command is **refused** with the reason. Auto moves on to Docker, then to host with an approval for every command, as before. | Unit test: refusal, nothing launched. |
| Approvals | Inside: none for ordinary commands. Asked for `where=host`, for `network=true` under `ask`, and for workspace-destructive commands (`rm`, `git reset --hard`, `git push`, …). | Unit test. |

**Retained, modified, retired:**
- **Retained:**
  - The Docker sandbox: Windows and macOS, and an explicit choice on Linux.
  - Host mode.
  - Approvals, the never-run list, `child_env`, `secret_files`, privacy modes, and the network policy.
- **Modified:**
  - Auto's order on Linux: bubblewrap, then Docker, then host.
  - The terminal's self-description and tool schema in sandbox mode.
  - The status endpoint now reports `bwrap` and `resolved`.
- **Retired:** nothing. Docker on Linux stays available, but it isn't recommended, because of the root-equivalent
  group and root-owned files.

**Not added, and why:**
- **A proxy with a domain allowlist** (as in Claude Code). The `ask` policy already gates the network per command,
  and a proxy is more infrastructure than the measured risk justifies today.
- **seccomp.** Network-off already removes inet and abstract Unix sockets. The hidden runtime folder removes the
  path-based sockets that matter.
- **Landlock.** It duplicates the mount namespace here.
- **Rootless Podman.** Not installed, and it duplicates the sandbox.

### Limits

- **Readable files.** Files in the home folder that aren't on the hidden list stay readable, as in Claude Code's
  and Codex's defaults. A model can read other projects; it can't change them.
- **The local API, with network allowed.** A command allowed onto the network can reach the host's loopback,
  including EchoSpeak's API. The desktop app requires a per-launch key on loopback, and commands never get that
  key. A source checkout run with defaults (`API_AUTH_LOCALHOST_BYPASS=true`) doesn't: run it with auth on, or keep
  the network policy on `ask`.
- **The kernel.** User namespaces enlarge the kernel's attack surface. bubblewrap is no defense against a kernel
  exploit.
- **Host mode.** It is unconfined by design. A host command could read EchoSpeak's environment from `/proc`, which
  is why it always asks.
- **Earlier evaluations.** Evaluation runs before this change ran terminal commands on the host after an approval.
  From now on, Auto on Linux runs them in the sandbox, so later rounds aren't strictly comparable on approval
  counts.

## 4. The `model-harness-testing` skill

`.agents/skills/model-harness-testing/SKILL.md`, with the Claude pointer in `.claude/skills/`, holds the procedure
used for three milestones:

**Research → Baseline → Execute → Capture → Verify → Judge → Diagnose → Improve → Retest → Document.**

It reuses the existing tools:
- the recorder;
- `harness_eval.py`;
- `harness_campaign.py`;
- the judge, with the same weights and critical findings;
- the replay and estimate tools;
- the V0–V4 ladder.

It states when not to run (unrelated work, owner's model loaded, low memory), how to add a model safely, how to
diagnose model vs provider vs sandbox vs harness vs eval causes, and the rules for fixing:
- no model names in code;
- a regression test per fix;
- never weaken a safeguard;
- no edits while a run imports the worktree.

It ends with documentation (repo report, artifact copy, an Obsidian note from
`assets/obsidian-evaluation-note.md`) and an owner acceptance list.

## 5. Verification

- **Wired in `05209a6`.** The changes were made after the two-model campaign finished, so its runs used one code
  revision.
- **Sandbox, live on this machine.** A bwrap probe plus the tests in `tests/test_linux_sandbox.py` (8 module tests,
  4 terminal wiring tests).
- **Writes:** project write lands; home and `/etc` are read-only.
- **Credentials:** `.ssh` empty; `.netrc` unreadable.
- **Network:** a loopback interface only; host Ollama unreachable.
- **Processes:** fewer than 10 PIDs visible; `NoNewPrivs` set; `sudo` refused.
- **Escape attempt:** `systemd-run --user` couldn't start anything outside the sandbox.
- **Fail closed.** An explicit `bwrap` mode refuses when the sandbox can't start (unit test, nothing launched).
- **Auto order.** bwrap, then Docker, then host (unit test). The older Auto test now pins bubblewrap as unavailable
  for its Docker and host cases.
- **LM Studio window.** Tested only against a local stub of the documented `/api/v1/models` response. LM Studio isn't
  installed here.
- **Test suites.** 1,195 passed and 37 skipped across the 75 related test files. The same 3 failures occur on the
  previous commit.
- **Not verified:**
  - an owner session in the desktop app under bubblewrap;
  - Windows and macOS (unchanged code paths);
  - a distro that blocks user namespaces (only the refusal path is tested).

## 6. Outstanding

- Live LM Studio verification, once it's installed.
- Settings UI text: "Auto" still describes Docker Desktop. Status now reports `resolved`, so the screen can say
  which sandbox is in use; the copy change is left to the owner's UI pass.
- A domain allowlist proxy, if the owner wants unattended network commands.
