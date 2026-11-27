# Ternary Bonsai 2.5 27B as EchoSpeak's local model (8 GB GPU)

Validated on an RTX 3060 Ti 8 GB with 16 GB RAM, 26–27 Nov 2026. Evidence and scores:
[bonsai-vs-qwen-2026-11.md](../research/bonsai-vs-qwen-2026-11.md) (#240).

## Why it needs its own server

Bonsai's `PTQ1_0` weights need the Prism ML llama.cpp fork; Ollama's bundled llama.cpp can't load them (and the stock
`Q2_0` file is 1.7 GB larger, which doesn't fit beside a desktop at 32K). EchoSpeak talks to it as an
OpenAI-compatible server (Settings → Models → provider **LocalAI**). The `llama.cpp` provider in Settings is the
in-process binding and is not used.

## Files (`~/models/bonsai`, hashes in `MANIFEST.md`)

| File | Source |
|---|---|
| `gguf/Ternary-Bonsai-2.5-27B-PTQ1_0.gguf` | huggingface.co/prism-ml/Ternary-Bonsai-2.5-27B-GGUF |
| `gguf/Ternary-Bonsai-2.5-27B-mmproj-Q8_0.gguf` | same repository (vision) |
| `gguf/Ternary-Bonsai-2.5-27B-PTQ1_0-kv-bias.gguf` | built locally with the fork's `llama-kv-mean-center` on the Bonsai-demo calibration text |
| `runtime/prism-b12232-cuda12.4/` | github.com/PrismML-Eng/llama.cpp release `prism-b12232-f702476`, linux CUDA 12.4 archive |
| `runtime/cuda12-libs/` | symlinks to the NVIDIA CUDA 12.8 runtime Ollama ships (`~/.local/lib/ollama/cuda_v12`); the archive ships none |

## Service

`apps/backend/scripts/local_runtimes/echospeak-bonsai.service` → `~/.config/systemd/user/`, then
`systemctl --user enable --now echospeak-bonsai`. It serves `Ternary-Bonsai-2.5-27B` on `127.0.0.1:8095`, sleeps after
120 s idle (VRAM back to the desktop's) and wakes on the next request in about 3 s. Every flag is explained in the unit.

## EchoSpeak settings

```json
"local": {"provider": "localai", "base_url": "http://127.0.0.1:8095", "model_name": "Ternary-Bonsai-2.5-27B",
          "context_length": 32768},
"model_capability_profiles": {"localai:Ternary-Bonsai-2.5-27B": {"thinking_supported": true,
  "thinking_toggle_supported": true, "reasoning_effort_map": {"minimal": "xhigh", "low": "xhigh", "medium": "xhigh",
  "high": "xhigh", "extra_high": "xhigh", "max": "xhigh", "ultra": "xhigh"}}}
```

The chat template accepts only `xhigh`, `medium` and `low` (`low` behaves like `xhigh`); the evaluation's best
setting was `xhigh` with the server's 4,096-token thinking cap. Thinking-off calls (titles, routing, the Companion)
send `reasoning_effort: "none"`.

## Switching to Qwen 3.5 9B (or another Ollama model)

Settings → Models → provider **Ollama**, model `qwen3.5:9b`. Both runtimes can stay running: before each call to
Bonsai, EchoSpeak asks Ollama to unload what it holds (`agent/lean/gpu_handoff.py`, `LOCAL_GPU_HANDOFF=0` disables
it), and Bonsai frees the GPU after two idle minutes. Qwen on this card fits 25–28 of its 33 layers; set
`OLLAMA_KV_CACHE_TYPE=q4_0` for the best fit at 32K.

## Limits

- About 300–600 MB of VRAM headroom at 32K beside the desktop; a GPU-heavy browser or video window can make a request
  fail. Larger windows need the KV cache in system RAM (`-nkvo`, 4.8–13 tok/s); MTP doesn't fit.
- The GPU runs at its 199 W limit under load.
- The Companion's local wording passed its guard 11/15 at the 4 s budget (Qwen 15/15); it falls back to the grounded
  sentence when it doesn't.

## Rollback

`systemctl --user disable --now echospeak-bonsai`, then set the provider back to Ollama in Settings.
