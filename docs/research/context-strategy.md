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

**Recalled memories and matched past chats** were the remaining per-request variation in the system prompt. They now
ride on the request too (§9). Playbook lessons stay in the system prompt, because they are instructions.

## 6. Recommendation

1. **Stay at 16K** (`OLLAMA_CONTEXT_LENGTH=16384`). The window is not the bottleneck; speed and offload are.
2. **Rely on the tiers in §4**, not a larger window: summaries, compaction, `read_output` and retrieval.
3. **Keep the prompt prefix stable** (done for the clock and git status, and for recalled memories, §9). It is worth
   more per turn than any window change.
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

## 9. Context and memory campaign (19 November, afternoon)

Branch `context-memory` (from `tui-refine` 8f945da): commits `e11a1f5`, `f88f3fb`, `cf02a3d`, `d4b3dfe`, all local,
none pushed. EchoSpeak with qwen3.5:9b at 16K was the system under test; Claude Code did the engineering. No
separately billed API was used.

### 9.1 Method

- **New cases** (`scripts/harness_eval.py`, set `long`):
  - LS1: several facts, then a combining question after unrelated turns.
  - LS2: a fact that changes within one chat.
  - LS3: a remembered fact replaced in a later chat.
  - LS4: an early fact after ten unrelated turns.
  - Held-out: LSH1 (trip details) and LSH2 (an appointment moved, without saying "remember").
  - The judge fails a reply that gives an outdated value as current (`stale`).
- **Cache measurement.** `scripts/prefix_reuse.py` reads the recorder's model calls and reports:
  - how much of each prompt matched the previous one;
  - where the prompt first differed;
  - the server's prefill time.
  
  The recorder also keeps the time to the first streamed chunk.
- **A fair before/after.**
  - "Before" is the pre-campaign harness (`e11a1f5`) run with the corrected case definitions.
  - The first baseline (`ctx-baseline`) had evaluation defects, found in §9.2, so it isn't used for comparison.
  - Every run is 11 memory and long-session cases × 2, judged by `harness_judge.py`.

### 9.2 What was found, by layer

| Finding | Layer | Evidence |
|---|---|---|
| Recalled memories in the system prompt changed its start whenever recall differed | harness | 27 of 134 baseline calls first differed at the memory section, about 46% into the prompt |
| `chat_search` required every word of the model's query in one message | harness | MC1 0/2: "venue offsite meeting location" found nothing, although "offsite" was in the earlier chat |
| `points_back` missed "did I mention …" and "I mentioned … before" | harness | MC1, MCH1: automatic past-chat recall never ran |
| Chat accepted "I've saved that" with nothing saved (claims are noted, not rewritten) | harness | LS3, LSH2: the update was lost and the next chat gave the old value |
| "I've noted your …" didn't count as a claim | harness | MI1 r1 baseline |
| The builder (forge) had `memory_save` deferred and `agent_notes` listed | harness | MI1: "Remember that …" replaced its private project notes with the fact, 2 of 2 |
| A changed fact ("got moved to …") set a reminder and left the saved memory | harness and model | LSH2: the next chat gave the old day |
| Cases shared one backend, so memories leaked between cases and repetitions | evaluation | LS3 met MR1's "build server is pluto-21"; LSH2 r2 met r1's appointment |
| LS1 stated a made-up "today" while the request note gave the real date | evaluation | The model rightly asked which date was right |
| The appointment case could pick today's weekday | evaluation | "Thursday", said on a Thursday, was read as next week |
| Ollama's `prompt_n` counts the whole prompt even when it was cached | measurement | `prompt_n` = `prompt_tokens` on every call, with a 0.2 s prefill |

### 9.3 Fixed

- **Recall rides on the request.**
  - Recalled memories and matched past chats are on the current request, not in the system prompt
    (`prompt.request_context`).
  - They keep the same wording and are labelled as recalled background, not typed by the user and not instructions.
  - The same isolation filter still applies first (`chat_mode.visible_memories`, `_owner_side`).
  - Test: the system prompt is byte-identical when the recalled memories differ (`tests/test_prompt_prefix.py`).
  - Playbook lessons stay in the system prompt, as instructions.
- **Past chats are found.**
  - A `chat_search` with no exact match ranks messages by the content words they share.
  - `points_back` recognises "did I mention …" and "the … I mentioned before".
- **Saves happen.**
  - In Chat, an unbacked memory claim gets one step to call `memory_save`. The streamed answer is never rewritten.
  - "I've noted your X" counts as a claim to keep it, unless it names a file.
  - "Remember that …" lists `memory_save` for any agent whose focus deferred it.
  - Recalled memories show their key, and the section asks for a change to be saved under the same key, which
    replaces the old value.
  - The repair step also runs for an unbacked "I've updated …" when the user asked to remember, or when the claim
    is about a fact recalled for this request. `ctx-after2` showed this gap: LS3 r2, and held-out LSH2 r1.
  - **Held-out caveat:** the recalled-fact rule was informed by a held-out failure (LSH2), so LSH2 is no longer a
    clean held-out measure of it.
- **The step limit** (owner report during the campaign; see §9.5).

### 9.4 Measured

| Run | Harness | LS1 | LS2 | LS3 | LS4 | LSH1 | LSH2 | MC1 | MCH1 | MH1 | MI1 | MR1 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| before (`ctx-before`) | e11a1f5 | 9.5 | 9.8 | 9.49 | 9.4 | 9.53 | 7.83 (50%) | 6.61 (0%) | 9.61 | 9.7 | 7.38 (0%) | 9.7 |
| after (`ctx-after`) | cf02a3d | 9.73 | 9.46 | 9.37 | 9.71 | 9.78 | 5.87 (0%) | **10.0** | **10.0** | 9.7 | 7.53 (0%) | 9.7 |
| after (`ctx-after2`) | d4b3dfe | 9.73 | 9.72 | 8.82 (50%) | 9.79 | **10.0** | 7.78 (50%) | 9.85 | **10.0** | 9.7 | **8.98** | 9.55 |
| **after (`ctx-after3`, final)** | fccd02f | **9.8** | 9.11 | 9.38 | **9.76** | 9.61 | **9.29** | **10.0** | **10.0** | 9.7 | **9.28** | 9.7 |

| | Before | Final |
|---|---|---|
| Trials that succeeded | 17 / 22 | **22 / 22** |
| Mean case score | 8.96 | **9.60** |
| Completion (30%) | 8.18 | **10.0** |
| Efficiency (20%) | 8.34 | 8.15 (extra save and repair calls: 156 model calls vs 139) |
| Recovery (15%) | 8.91 | 9.82 |

Scores are means of two trials out of 10; a success rate below 100% is shown in brackets.

**Cache and latency** (`prefix_reuse.py`, main-agent calls):

| Run | Calls | Mean reused share | First difference in the system prompt (memory) | Mean / median prefill | Mean first chunk |
|---|---|---|---|---|---|
| before | 137 | 0.85 | 27 | 269 / 208 ms | 0.31 s |
| after | 143 | **0.94** | **0** | 297 / 210 ms | 0.35 s |
| after2 | 132 | **0.94** | **0** | 290 / 244 ms | 0.33 s |
| final | 154 | **0.94** | **0** | 328 / 228 ms | 0.39 s |

**Honest reading:**

- The prompt is structurally more stable: no call breaks at the memory section any more.
- Measured latency did not improve. Calls that differed from the previous prompt 46% of the way in, or even from
  its first character (switching between Echo and GLaDOS), still prefilled in about 0.2–0.3 s.
- This Ollama build therefore keeps and restores more than the single last prompt. The memory-section breaks were
  being absorbed already.
- The move is kept for its structural benefit:
  - it costs nothing;
  - it protects servers that cache only the last prompt;
  - it protects runs where many different prompts compete for the cache.
- It is not a measured latency win on this machine. §5's clock fix was a real one, because a new minute changed
  *every* turn's prompt.

### 9.5 The step limit (owner report, 19 November)

The owner saw "I ran out of steps (60) before finishing" and had to say "continue". In the live data (read only),
the two most recent such turns were research questions that stopped after **6** steps, not 60.

- A look-up turn has a research budget, and when it is used up the turn makes one tool-free call for the answer.
- In the 13:04 turn, that call wrote the whole answer (1,922 characters) as reasoning and returned no reply.
- The turn then showed the out-of-steps note, with the engineering limit's number.

**Fixed** (`agent/lean/loop.py`, tests in `tests/test_auto_continue.py`):

- **The answer call runs with thinking off.** If the model still writes only reasoning, one more call asks for the
  reply from it.
- **The note gives the real limit.**
- **The agent the user talks to carries on past its limit while it is still making progress.**
  - Progress means new actions, not the same calls again, and at least a third of the stretch's tool calls working.
  - Each stretch is the limit again, up to `lean_auto_continue_stretches` (default 5, so 300 steps at 60).
  - The context is already compacted as it fills (§4).
  - A turn going in circles still stops, and so do Chat (whose small budget ends in an answer) and helpers.
  - The terminal shows "Kept going: still making progress".
- **Real-model probe** (`scripts/grace_probe.py`, 3 trials each, on the owner's question with four search and
  page results):

  | Answer call | Empty replies | Time | Reasoning |
  |---|---|---|---|
  | Thinking on | 0 of 3 | 7.6, 16.4, **86.5** s | up to 12,652 characters before answering |
  | Thinking off | 0 of 3 | 6.6–7.7 s | none |

  The empty reply did not recur in this smaller context, so it is intermittent. Thinking off removes the wait and
  the risk.

### 9.6 Regressions and limits

- **Regressions.**
  - The backend suite has the same 21 failures, all pre-existing: they fail identically on `e11a1f5`.
  - The terminal suite passed 4,288.
  - Efficiency is a little lower (8.34 → 8.15) because saves now actually happen, and LS2 dipped from 9.8 to 9.11
    while still passing.
  - Prefill and time-to-first-chunk are flat to slightly higher (mean 269 → 328 ms). The final run made more and
    longer calls; the medians were 208 and 228 ms.
- **Two trials per case is a small sample.** A single failure moves a case by 50 points. Read the means, not
  single rows.
- **Not repeated:** the 16K/32K/64K benchmarks (§3). 16K stays the default.

### 9.7 Next priorities

1. Re-run the long and memory set with more trials (×4), and add a fresh held-out supersession case, since LSH2
   informed a fix.
2. Supersession without a key: the curator derives keys for only a few patterns. A key derived from "my X is …"
   would let an unkeyed update replace the old value.
3. Watch `auto_continue` in owner sessions. The progress test is conservative and untested on a real long
   engineering task.
4. Merge carefully with the other worktree's campaign (`campaign-2026-11-18.md` §10), which fixed `chat_search`
   and "remember" listing independently.

## 10. Verification round: live long task, memory ×4, branch integration (19 November, evening)

The owner asked for three checks before moving on, through the engineering cycle. Branch
`context-recall-integration` (from `context-memory` cff1f0f): commits `a6c3e9c` → `7bed1a3` plus this report, local, not pushed.
Every evaluation used its own isolated backend; the owner's backend (the `five-model-harness` checkout, up 20 h) was
never restarted or touched.

### 10.1 Branch integration

`search-recall-candidate` (f452559, five commits; campaign-2026-11-18 §10.5) was merged (`a6c3e9c`). Overlaps:

| Area | context-memory | candidate | Kept |
|---|---|---|---|
| `chat_search` | any-word fallback when nothing matched | partial top-up below three hits, marked "(partial match)"; this turn's own messages excluded; full matches first, newest first, with the time | **the candidate's** (a superset) |
| "remember" lists `memory_save` | the memory curator's explicit-request test | `REMEMBER_REQUEST` regex, not for guests | **both**, in one block |
| Eval topic rotation | `53a3882` | `f452559` | the same change, already present |

Both branches' tests pass together (120 in the overlapping files); backend suite: the same 21 pre-existing
failures, nothing new.

### 10.2 Live long task (LT1)

A new case: ten text functions, each in its own module with a protected failing test, "keep going until they all
pass". The fixture fails 10/10 as shipped and passes 10/10 with a reference implementation. Run with
`LEAN_MAX_ITERATIONS=12` so the limit is crossed several times in minutes (same code path as the default 60).

**First live run (`lt1-live`, commit b9f912a):** automatic continuation worked: 12 → 24 → 36 → 48 → 60 steps, no
"Continue", 0/10 → **9/10** tests passing. Two harness limits then ended it:

| Defect | Evidence | Fix (`21ea7f4`) |
|---|---|---|
| The work budget (64k tokens / 900 s) wasn't raised with the steps | "Work budget exhausted" at step 59; twelve compactions were ~35% of the charge | `Coordinator.extend_budget`: each continuation adds the work's original allowance, capped at 256k tokens and one hour |
| The repeat guard refused re-reads of files the agent had just edited, or whose earlier result compaction had summarised away ("the result is above") | 21 refused reads, a 9-call failure streak | read counts reset after a successful change and after compaction; identical reads with nothing changed are still stopped |

**Retest (`lt1-retest`, ×2, commit 4630be0):** lt1-retest showed the budget stop gone (0 of 2) and the repeat refusals down from 21 to 0, but
both runs were confounded by the **environment**. The sandbox's `python3` (system 3.14) has no pytest, and
`pip install pytest` reports success into a different interpreter. So r2 ran `python3 -m pytest` nine times, then
said honestly that it couldn't verify (the judge read "I've completed implementations" as a false success; the reply
and the harness's "Not verified" note say otherwise). r1 reached 10/10 but could never confirm it, and kept inspecting
until the step ceiling.

The fixture now ships a plain-Python `run_tests.py`, which the judge doesn't use (it still runs pytest):

| Run (commit) | Continuations | Ended | Tests | Steps | Repeat refusals | Compactions |
|---|---|---|---|---|---|---|
| lt1-live r1 (b9f912a) | 12→24→36→48→60 | **work budget**, step 59 | 9/10 | 59 | 21 | 12 |
| lt1-retest r1 (21ea7f4) | 24, 36, 48, 60 | step ceiling | 10/10, unconfirmed (no pytest) | 60 | 0 | 22 |
| lt1-retest r2 | 24, 36 | answered, "not verified" | 7/10 | 32 | 0 | 6 |
| lt1-runner r1 (7bed1a3) | 24, 36, 48, 60 | step ceiling ("Press Continue") | 9/10 | 60 | 7 | 18 |
| **lt1-runner r2** | 24, 36 | **answered: all 10 pass** | **10/10** | 30 | 1 | 2 |

**Verdict:**

- Automatic continuation works live. It fired in every run, and no turn stopped for "Continue" before its ceiling.
- The two harness limits it exposed are fixed.
- With 12-step stretches the ceiling is 60 steps; at the default limit of 60 it is 300.
- The remaining failure (runner r1) is a small model at 16K, losing the thread over 18 compactions on the hardest
  function (`camel_to_snake` with acronyms). That is a model and context limit, not continuation.

### 10.3 Memory reliability ×4, including keyless updates

New cases: LS5 (a code reset "in passing"), LS6 (a commute that changed), held-out LSH3 (a friend who moved), and
after LSH3 informed a fix, a fresh held-out LSH4 (a new manager). None of them says "key".

**What ×4 showed (`mem-x4`, 51 of 56 trials; the eval backend was then killed by memory pressure from my own
concurrent test suite, an environment fault, and the cut-off trial is excluded):**

- The model *does* key keyless facts, and reuses the key on update (`tundra_build_server` twice,
  `riverbend_gym_locker_code` twice): supersession by key works when a save happens.
- The failures were updates that **were never saved**: LS6 r2 ("I switched to the 7:40 train" → "sounds like a good
  move"), LSH3 r2 ("Ines moved to Gdansk" → "I've updated that in my notes", nothing saved), LSH2 r1 ("Remember: …"
  → only a reminder).
- LS2 r1 read "Remind me: when and where is the design review?" as a request for a reminder (model, and a wording
  ambiguity in the case).
- Evaluation: repetitions still collided (LS6 r3 answered with r1's train; LS3 drew one project three times).

**Fixes (`4630be0`):** at the end of a turn with no `memory_save`, one repair step asks for it when the user asked to
remember, or stated a change ("moved", "switched", "reset", "it's … now", "instead") to a recalled fact; the note
names the recalled key. Questions and unrelated changes never trigger it (tests). It now applies to any agent the
owner talks to. Eval: each trial is redrawn until its identifying values are new in the run.

| Case | ×4 before the fix (`mem-x4`) | ×4 after (`mem-x4b`) |
|---|---|---|
| LS1 | 9.82 (100%) | 9.73 (100%) |
| LS2 | 9.44 (75%) | **9.62 (100%)** |
| LS3 | 9.38 (100%) | 9.47 (100%) |
| LS4 | 8.75 (75%) | **9.69 (100%)** |
| LS5 | 9.46 (100%) | 9.41 (100%) |
| LS6 | 8.55 (67%) | **9.4 (100%)** |
| LSH1 (held-out) | 9.74 (100%) | 9.51 (100%) |
| LSH2 (held-out) | 9.07 (67%) | **9.39 (100%)** |
| LSH3 (held-out) | 8.23 (67%) | **9.38 (100%)** |
| LSH4 (held-out) | not in that run | 9.34 (100%) |
| MC1 | 10.0 (100%) | 10.0 (100%) |
| MCH1 | 10.0 (100%) | 9.88 (100%) |
| MH1 | 9.7 (100%) | 9.54 (100%) |
| MI1 | 9.27 (100%) | 9.36 (100%) |
| MR1 | 9.7 (100%) | 9.7 (100%) |
| **Mean of case means** | 9.37 | **9.56** |

### 10.4 Reading and limits

- **Memory, ×4:**
  - 51 of 56 trials passed before the follow-through fix, and **60 of 60** after it.
  - That includes four held-out cases. LSH4 is fresh: written before any run and never used for a fix.
  - The mean of case means went from 9.37 to 9.56.
- **How much is the harness:**
  - The repair step fired in **25 of 60** trials, 17 of the 24 supersession ones.
  - qwen3.5:9b often does not save an update on its own. The harness now makes it happen, at a cost of one or two
    model calls: supersession efficiency is 6.7–7.4.
  - With a stronger model the step would fire less, and it costs nothing when it doesn't fire.
- **Keyless updates:** the model keys keyless facts itself and reuses the key, so supersession works whenever a save
  happens. The failures were saves that didn't happen, not missing keys.
- **Held-out caveat:** LSH2 and LSH3 informed fixes, so LSH4 is the clean held-out measure (4/4).
- **Cache:** reused share 0.95, mean prefill 251 ms, mean first chunk 0.29 s, as in §9.
- **Environment faults, both self-inflicted or local:**
  - A concurrent full test suite pushed the 15 GB machine into an out-of-memory kill of one eval backend. The
    owner's backend was unaffected, and evals now run with nothing else heavy.
  - The sandbox Python has no pytest.
- **Regressions:** the backend suite has the same 21 pre-existing failures and nothing new (1,920 passed). No case
  lost a trial between the ×4 runs.

### 10.5 Next

1. **Python environment awareness:** the agent's sandbox has no pytest and `pip` installs elsewhere. The owner's
   projects without a venv hit the same thing. Tell the agent how to run Python tests there, for example by creating
   a project `.venv`, or detect pytest and say so in the environment note.
2. **Long tasks at 16K:** 18–22 compactions in one turn lose the thread. Keep test results and the file list in the
   compaction summary, and consider 32K for long engineering turns only (§3: about 37% slower).
3. Run LT1 at the default limit (60, ceiling 300) in the owner's real setup, and watch `auto_continue` in real sessions.
4. **Branch decision for the owner:** `context-recall-integration` contains `context-memory` and the search-recall
   candidate, so `search-recall-candidate` can be retired.

### Release follow-up, 20 November

The Linux release includes the sandbox pytest diagnostic and the measured context-fill change. At 16K, two of three completed five-module trials fully passed. The last held-out trial hit the memory guard. In the default-budget LT1 run the local model finished in 34 model calls, below the first 60-step boundary. An independent pytest run passed all ten tests. Echo marked its own result unverified after missing the final check. The earlier 12-step trials remain the evidence for crossing continuation boundaries. [Full release results](integration-11.15.md)
