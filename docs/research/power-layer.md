# The power layer — research and proposal (for discussion, nothing built)

> **Status, 14 November 2026.** Research and a proposed plan only. No code has been written for
> this. It needs the owner's decisions (§7) before any build starts. The working name is "power
> layer"; the owner picks the real name.
>
> Related: [harness-first.md](harness-first.md) (EchoSpeak's own direction) ·
> [local models on this machine](#2-what-runs-on-this-machine-today-measured).

## 1. The idea, restated

A **layer that sits on a machine's hardware** (GPU, CPU, RAM, disk, network) and **hands out AI power
to whatever program the user picks**:
- their own harness (EchoSpeak);
- Codex CLI, Claude Code, OpenCode or Hermes;
- a chat UI such as Open WebUI;
- scripts, IDE plugins, MCP clients.

The user stays in control of who gets what.

It is **not** an IDE and not a terminal coding app. Those are *clients* of the layer. The layer is
closer to an operating-system service for AI:

- **Models:** one place that serves local models, and routes to cloud models when wanted.
- **Compute:** decides who gets the GPU now, who waits, what stays loaded.
- **Tools:** one gateway that exposes tools (files, shell, browser, apps) to every client, with
  per-client permissions and approvals.
- **Memory:** one memory every client can use, separated by scope, with a record of who wrote what.
- **Identity, budgets and audit:** each client app has its own key, limits and a log.

EchoSpeak becomes **one client** of the layer, and later possibly its control panel (its approvals,
audit and UI already exist). The layer must also work without EchoSpeak.

## 2. What runs on this machine today (measured)

Machine: Omarchy (Arch), RTX 3060 Ti **8 GB VRAM**, 16 GB RAM, 16 cores, about 400 GB free.

| Piece | State |
|---|---|
| Ollama 0.40.16 | Installed in the user's home folder (`~/.local`), user systemd service, loopback only, starts at login. Detects the GPU via CUDA. One model loaded at a time; unloads after 10 idle minutes; 16K context; quantised attention cache (`q8_0`). |
| `qwen3.5:9b` | 6.6 GB download. **Runs 100% on the GPU** (5.8 GB loaded, about 7.1/8 GB used). About 15 tokens/s generation. First cold load 37 s. |
| `qwen3.8:27b` | 17 GB download, Apache 2.0, released August 2026. **Does not fit the GPU**: it splits between GPU and RAM, so expect a few tokens/s and heavy RAM use. |
| Echo bench results | See §8 |

The scarce resource here is **VRAM**, not compute. One 9B model plus its context fills the card.
"Allocating power" on this machine mostly means: who gets the GPU now, who waits, which model stays
loaded, and when to hand work to a slower or remote option.

## 3. What already exists (adopt, don't rebuild)

These findings come from source and documentation research on 14 Nov 2026. Items marked
"unverified" were seen only in secondary sources.

| Layer | Best existing piece | What it lacks for this goal |
|---|---|---|
| **Serving** | **Ollama**: easy, swaps models automatically, OpenAI-compatible API and a partial Anthropic `/v1/messages`, so Claude Code can point at it. **llama-swap + llama.cpp**: more control (per-model time-to-live, API keys). | **Ollama has no authentication at all** and must never be exposed beyond loopback. Neither has priorities, quotas or preemption: requests are first-come first-served. |
| **Gateway** | **LiteLLM proxy**: per-app virtual keys, model allow-lists, rate limits, budgets, "local first, cloud fallback" routing. | Budgets are in dollars, not GPU time. Needs Postgres. Heavy. Some features are paid. |
| **Tools** | **MCP** (spec 2026-07-28: stateless, with `Mcp-Method`/`Mcp-Name` headers that a proxy can enforce per tool). **Docker MCP Gateway** runs each tool server in its own container. **IBM ContextForge** (unverified): per-team tool permissions. | Per-client permissions are weak or paid in most gateways. A shared tool gateway concentrates risk. |
| **Memory** | **OpenMemory (mem0)**: local, per-client pause/revoke, audit. **Graphiti**: a graph with timestamps. | No shared memory with per-app scopes *and* provenance. Extraction uses an LLM call, which competes for the GPU. |
| **GPU sharing** | **systemd slices / cgroups v2** for CPU and RAM limits. | Nothing limits VRAM or GPU time on a consumer card. MIG doesn't exist on a 3060 Ti; NVIDIA MPS is fragile. |
| **"AI OS" projects** | Harbor (a launcher), LocalAI (broad, monolithic), Open WebUI (a client with user accounts). | None gives one identity per app across models, tools and memory; GPU-aware priorities; cross-app approvals; one audit trail. |

## 4. What is genuinely missing (the part worth building)

1. **A VRAM-aware broker.**
   - Interactive requests jump ahead of background jobs.
   - Batch work is deferred when the GPU is busy.
   - "Keep the coding model loaded from 9 to 6."
   - Admission is based on measured memory per model and context size.
   - It drives Ollama/llama-swap rather than replacing them.
2. **One identity and policy plane.** Per-app keys that cover models, tools and memory together,
   with limits in **GPU-seconds**, not dollars.
3. **Approvals and audit across apps.** "Claude Code wants to run `rm`" is approved in one place.
   EchoSpeak already has this machinery.
4. **Scoped memory with provenance.** App A's notes don't leak into app B's prompts unless allowed,
   and every memory records who wrote it.
5. **A clear "what's using my machine" view.**

This is also where EchoSpeak's existing strengths fit: approvals, policy taint tracking, audit
logs, scoped memory, the work ledger and the UI.

## 5. Candidate architectures

| | A. Thin composition | **B. Small broker in front (recommended)** | C. LocalAI-centred |
|---|---|---|---|
| Shape | LiteLLM + llama-swap + Docker MCP Gateway + OpenMemory, glued together | One small service is the only thing apps talk to. It issues per-app keys, queues by priority, tracks VRAM, and forwards to Ollama/llama-swap (and optionally the cloud) | Adopt LocalAI's built-in users, quotas and backends |
| Code to write | Least | Moderate, focused on the missing parts | Little |
| Footprint on 16 GB RAM | Heavy (Postgres, Qdrant, Docker) | Light | Medium |
| Priorities / GPU awareness | No | **Yes** | Unverified |
| Auth models | Four different ones | One | One |
| Risk | Glue breaks as projects change | Must keep the OpenAI/Anthropic API shapes compatible (forward traffic unchanged; only gate it) | Adopts LocalAI's opinions on agents and UI; overlaps EchoSpeak |

**Recommendation: B.**
- Build only the broker, policy and audit parts.
- Adopt Ollama now (llama-swap later if more control is needed).
- Reuse EchoSpeak's approval and audit code where possible, as a library or by porting it, not by
  making the layer depend on the EchoSpeak app.
- Keep it a separate service, likely a separate repository, so it can serve any harness.

## 6. How apps would use it (concrete)

Every app is pointed at one local address with its own key, using the API shape it already speaks.
Nothing has to change inside the apps:

| App | How it connects |
|---|---|
| EchoSpeak | `LOCAL_MODEL_URL` → the layer (OpenAI-compatible) |
| Codex CLI | its custom/OSS model provider setting → the layer (OpenAI-compatible) |
| Claude Code | `ANTHROPIC_BASE_URL` → the layer. Ollama's Anthropic support is partial: no `tool_choice`, no prompt caching. |
| Open WebUI, scripts | OpenAI-compatible base URL + key |
| Any MCP client | the layer's MCP endpoint, which shows only the tools that app is allowed |

**Phased plan** (each phase usable on its own):

1. **Broker v0 (single machine).**
   - Per-app keys.
   - Priority queue: interactive before background.
   - VRAM ledger and model pinning.
   - Per-app usage in GPU-seconds and tokens.
   - Loopback only; Ollama behind it.
   - *Done when:* EchoSpeak and one other app (e.g. Codex or Open WebUI) share the GPU at the same
     time; the interactive one is never stuck behind a long background job; usage shows per app.
2. **Tool gateway.**
   - One MCP endpoint with per-app tool scopes.
   - Side-effecting tools need approval.
   - Tool servers run sandboxed (bubblewrap or containers).
   - Every call is audited.
3. **Shared memory.** Per-app scopes, provenance, owner review of what moves between scopes.
4. **Control UI.**
   - What's loaded and who's waiting.
   - Approvals.
   - Per-app limits.
   - EchoSpeak's UI is the natural home.
5. **More machines (later).**
   - A broker on each machine.
   - Requests go to whichever machine has the model loaded.
   - Distributed inference across machines is not realistic on Linux GPUs today.

## 7. Decisions needed from the owner before building

1. **Who is it for first?** Just you on this machine, or a product other people install? The second
   adds packaging, licensing, onboarding and support.
2. **Separate project or inside EchoSpeak?** Recommendation: a separate service and repository, with
   EchoSpeak as the first client and later its control UI.
3. **Cloud fallback.** Should the layer route to cloud models when local can't do the job? With
   which accounts? Who pays?
4. **Which other apps first?** Codex CLI, Claude Code, Open WebUI or Hermes. Pick one to prove
   sharing.
5. **Tools and memory scope.** Start with models only (phase 1), or include tools from the start?
6. **Name.**

## 8. Echo bench on local models (EchoSpeak as a client, today)

The Echo bench (`apps/backend/scripts/echo_bench.py`) has 10 real tasks. Each is checked against what
actually happened (files on disk, tools used, the reply), not against the model's own claims.

Every run used:
- a fresh, isolated data folder;
- Ollama with a 16K context on both sides;
- Proton VPN on for the web case;
- 14 Nov 2026, EchoSpeak 11.13.1 + this branch.

| Model | Mode | Permissions | Score | Time | Notes |
|---|---|---|---|---|---|
| qwen3.5:9b | unified | fresh-install defaults (file write and commands **off**) | 7/10 | 81 s | Files and commands correctly refused by the permission switches. The model said so honestly instead of claiming success. |
| qwen3.5:9b | **unified** | file write + commands on | **9/10** | ~90 s | Only miss: reading a number off an image (the first run passed it, so it is variable). |
| qwen3.5:9b | environments (old three tabs) | file write + commands on | 7/10 | ~80 s | A plain chat can't write files, run commands or look up connections in this mode. |
| qwen3.8:27b | unified | file write + commands on | 5 of 8 run | 1–17 **min** per task | Correct on files, commands and the long log. 71% CPU / 29% GPU, so too slow to be interactive. Stopped after 8 tasks. |

What this shows:
1. **The one-assistant design works better than the three tabs:** 9/10 vs 7/10, same model, same tasks.
2. **A 9B model on an 8 GB card is a real working setup** for files, commands, research with sources,
   memory, recall, artifacts and ignoring planted instructions.
3. **A 27B model needs more VRAM.** On this card, "more power" means routing heavy jobs elsewhere (the
   cloud or another machine) rather than loading a bigger local model. That is exactly the routing
   decision a power layer should make.
4. **Defaults matter.** A fresh install has file write and commands off and auto-detects the *first*
   Ollama model, which was the 27B. The terminal client now asks which model to use instead.

**Gaps found while testing** (proposed fixes, not yet made):
- EchoSpeak assumes a 32K context for local models and never asks Ollama. If Ollama runs a smaller
  window, prompts are cut silently.
- Auto-detection picks the first listed Ollama model, not one that fits the GPU.
- On Linux, voice settings default to Windows' speech engine (`windows-sapi`).
- Built-in local-model suggestions are dated (`qwen2.5:3b`).

## Sources

Ollama FAQ and API docs (docs.ollama.com) · llama-swap (github.com/mostlygeek/llama-swap) ·
LiteLLM virtual keys (docs.litellm.ai) · MCP 2026-07-28 changelog (modelcontextprotocol.io) ·
Docker MCP Gateway (docs.docker.com) · OpenMemory (mem0.ai) · Graphiti (github.com/getzep/graphiti) ·
NVIDIA MPS (docs.nvidia.com) · Harbor (github.com/av/harbor) · LocalAI (github.com/mudler/LocalAI) ·
Qwen3.8-27B release coverage (eweek.com, the-decoder.com) · Ollama library pages for qwen3.5,
qwen3.8, mimo-v2.6, nemotron-3.5-bolt.
