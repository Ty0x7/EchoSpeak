# Context, memory and long conversations on this machine (19 November 2026)

**Question.** Should EchoSpeak's qwen3.5:9b run with a 16K, 32K or 64K window on the owner's hardware? And how
should long conversations stay coherent without paying for everything on every turn?

**Answer.**

- **Keep 16K.** 32K costs 37% of generation speed and 64K costs 66%, on a card that already can't hold the whole
  model at 16K. Recall of a planted fact was fine at all three sizes, so the window isn't the bottleneck.
- **The biggest win was elsewhere.** EchoSpeak's prompt layout made the model server reprocess the tools and the
  whole history on every turn. That is fixed (§5): **5.73 s → 0.31 s** of prefill on a second turn at 13K tokens.

Method: arithmetic from the model's real metadata, then three lightweight probes on the real Ollama. There was no
evaluation campaign, and no model judged anything. Script: `apps/backend/scripts/context_probe.py`. The model was
unloaded after every probe.

## 1. The machine and the model, as configured

| | |
|---|---|
| GPU | RTX 3060 Ti, 8 GiB (about 0.7 GiB used by the desktop) |
| RAM | 15 GiB |
| Ollama | 0.40.16; `OLLAMA_CONTEXT_LENGTH=16384`, `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`, `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_KEEP_ALIVE=10m` |
| Model | qwen3.5:9b, Q4_K_M, 6.6 GB on disk, including a 456M-parameter vision projector |

The architecture (`/api/show`) is **hybrid**:

- 32 blocks, of which only every 4th (`full_attention_interval: 4`) is full attention: **8 layers**, with 4 KV
  heads of 256 dimensions.
- The other 24 are recurrent (Gated DeltaNet: `ssm.state_size 128`, `inner_size 4096`). Their state is the same
  size at any context length.

## 2. KV cache, calculated

Per token: 8 layers × 2 (K and V) × 4 heads × 256 × bytes per element.

| Window | f16 (2 B) | q8_0 (as configured) |
|---|---|---|
| 16K | 512 MiB | **256 MiB** |
| 32K | 1 GiB | **512 MiB** |
| 64K | 2 GiB | **1 GiB** |

On top of that comes a constant ~50 MB of recurrent state. A classic 32-layer 9B model with full attention in every
layer would need 4× this. **The KV cache itself is not what makes a larger window expensive here.**

## 3. What a larger window actually costs (measured)

`context_probe.py` loads the model at each window. It fills about 85% of the window with varied text that begins
with a planted access code, then asks for the code (generation capped at 64 tokens, thinking off, temperature 0).
Sizes and speeds come from Ollama's own `/api/ps` and timings.

| Window | Prompt | Prefill | Generation at that depth | Wall | Loaded | **On GPU** | Code recalled |
|---|---|---|---|---|---|---|---|
| 16K | 13,786 tok | 2,002 tok/s | **39.4 tok/s** | 10.9 s | 6.49 GB | **85.5%** | yes |
| 32K | 27,832 tok | 1,678 tok/s | **24.8 tok/s** | 21.5 s | 6.91 GB | **81.0%** | yes |
| 64K | 56,757 tok | 1,236 tok/s | **13.5 tok/s** | 51.9 s | 7.83 GB | **71.9%** | yes |

At half depth (an earlier run), generation was 43.6, 30.1 and 16.8 tok/s.

- **Ollama holds about 5.6 GB of this card for the model, whatever the window.** Even at 16K, 14.5% of the model
  runs on the CPU. A larger window doesn't use more GPU: its KV cache and compute buffers push more of the model's
  layers onto the CPU. That is why generation falls 37% at 32K and 66% at 64K, and why a full 64K prompt takes
  about 46 s just to read.
- **Recall held at every size** for a single planted fact, with this hybrid model. That's one sample of the
  easiest kind of retrieval, not proof of long-context reasoning. Multi-fact reasoning at depth wasn't tested.
- **A first run was invalid, and it's instructive.** My filler estimate was low (1.52 tokens per word, not 1.35), so
  every prompt was longer than its window. Ollama answered anyway, having kept exactly half the window plus 2
  tokens (8,194 / 16,386 / 32,770). The planted code was cut off and never recalled. This is the silent cut
  `agent/lean/window.py` exists to detect (it was measured there first).

## 4. How EchoSpeak keeps conversations within 16K

| Tier | What | Where |
|---|---|---|
| History | The last 30 messages verbatim. Older turns are covered by a per-chat summary. Tool outputs are not carried across turns, only the replies. | `runtime._history`, `agent/lean/summaries.py` |
| Within a turn | When steps outgrow the window, older steps are summarised (≤ 200 words, thinking off), then trimmed. | `loop._compact_turn` |
| Big outputs | Kept outside the context behind `out-…` IDs; `read_output` reopens any slice. | `agent/lean/tool_outputs.py` |
| Memory | Retrieved per request (top 8) and listed in the prompt; `memory_search` and `chat_search` on demand. | `runtime._recall`, `chat_search` |
| The real window | Read from Ollama (`/api/ps`). A silently cut prompt is detected, the reply discarded, and the step retried smaller. | `agent/lean/window.py` |

This is the right shape for a 16K budget. The conversation's *meaning* is carried by the summary and memory, and
the full text is only one tool call away. Retaining whole conversations in a 64K window would cost 3× slower
replies on every turn for a benefit only very long single-document tasks need.

## 5. Prefix caching, and the bug that defeated it

llama.cpp reuses the longest unchanged start of the previous prompt, and this works for the hybrid model.

**Probe.** I sent a 9.4K-token prompt, then the same prompt with only its last line changed. Prefill took
**4.09 s, then 0.31 s**.

**Bug.** EchoSpeak's prompt is [system prompt] [tool definitions, which Qwen's template places after the system
text] [history] [new request]. Both prompt builders ended the system prompt with the time, to the minute; the
Agents and Code builder also added the git status there. The code's comment intended "last, so it never breaks
the cached prefix". But the last line *of the system prompt* still comes before the tools and the whole history.

- Any turn in a new minute changed the prompt about 15K characters in.
- The server then reprocessed about 3.4K tokens of tool definitions, plus all of the history, before the first
  token of every answer.
- A three-turn probe in the isolated lab looked stable only because its turns fell within one minute.

**Fix.**

- `chat_mode.build_prompt` and `prompt.build_system_prompt` take `clock=False`.
- The runtime puts the time (and the git status) on the **current request** as a bracketed note
  (`prompt.request_note`, `LeanTurn.request_note`), the convention the loop already uses for compaction summaries.
- The stored user text, the turn's goal and the history are unchanged.

**Measured A/B on the real server.** I used EchoSpeak's real system prompt and 23 tool schemas, captured from an
isolated backend, plus about 6K tokens of history, for 13.2K tokens in all. There were two turns, a minute apart.

| Layout | Turn 1 prefill | **Turn 2 prefill** |
|---|---|---|
| Clock in the system prompt (before) | 5.72 s | **5.73 s** |
| Clock on the request (now) | 5.77 s | **0.31 s** |

That saves about 5.4 s of time-to-first-token per turn at this size, and the saving grows with the history. Tests:
`tests/test_prompt_prefix.py` checks that the system prompt is byte-identical across minutes (Chat, and Agents or
Code on a cloud model) and that the time reaches the model on the request only. `tests/test_chat_paths.py` was
updated: it had asserted the old placement.

**Still in the system prompt and still varying per request:** recalled memories, matched past chats and playbook
lessons. They change only when retrieval returns something different, not every minute. Moving them to the request
is the next step if traces show they change often. That needs a small model check, because it moves
instructions-adjacent content.

## 6. Recommendation

1. **Stay at 16K** (`OLLAMA_CONTEXT_LENGTH=16384`). The window is not the bottleneck; speed and offload are.
2. **Rely on the tiers in §4**, not a larger window: summaries, compaction, `read_output` and retrieval.
3. **Keep the prompt prefix stable** (done for the clock and git status; memories are next). It is worth more per
   turn than any window change.
4. **If a task really needs more** (one long document read whole), 32K is the ceiling worth considering. It takes
   about 512 MiB more KV, 37% slower generation, and a model reload (about 5 s) to switch. A per-request
   `num_ctx` reloads the model, because `OLLAMA_MAX_LOADED_MODELS=1`. Ollama's OpenAI-compatible endpoint ignores
   a per-request size anyway (`window.py`), so it would be a server setting, not a per-chat switch.
5. **To win GPU back:** close GPU-heavy desktop apps while chatting. Ollama's fit leaves 14.5% of the model on the
   CPU even at 16K. Not tested: `num_gpu` overrides and a text-only build without the vision projector.

## 7. Agent awareness and reliability (same pass)

These are grounded in the owner's session `f75dae98` (18 November).

- **Guessed output IDs.**
  - At the start of a turn, the model called `read_output` with invented IDs (16 and 64 hex characters, where real
    ones have 24). No real ID was in its context.
  - It then made 76 more calls.
  - A wrong ID is now answered with this chat's real saved outputs, newest first, or "this chat has no saved
    outputs" (`tool_outputs.read`, `saved_in`). Tests: `tests/test_tool_outputs.py`.
- **A tool it didn't know to ask for.**
  - "look at codex and claude sessions" never reached `agent_sessions`. Focus defers it behind `find_tools`, and
    the model didn't call `find_tools`.
  - `Toolbox.suggest` runs the `find_tools` keyword match on the request before the first step. It lists at most
    two strong matches: a word of the tool's name, plus two *distinctive* words (ones appearing in at most two
    tools).
  - Over all 52 evaluation prompts plus the owner's, it adds a tool only for the two Claude/Codex session questions.
    An earlier, looser rule matched "project", "check" and "create"; those cases are now regression tests
    (`tests/test_request_focus.py`).
- **Permissions it described correctly.**
  - The Pong refusal was accurate: every action switch was off. `agent/lean/capabilities.py` already tells the
    model what is off, from the live settings.
  - The terminal's `/permissions` hint (cli-design §10) closes the loop for the owner.

## 8. Reproduce

```
apps/backend/.venv/bin/python apps/backend/scripts/context_probe.py --sizes 16384,32768,65536
```

It takes about 90 s and unloads the model at the end. Close EchoSpeak chats first, since it evicts the loaded
model. The clock A/B and the prefix probe are described in §5; their numbers come from single runs on this machine.
