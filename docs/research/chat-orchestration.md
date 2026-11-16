# Chat orchestration: an evidence-grounded execution layer

How EchoSpeak's Chat executes a turn, compared with OpenAI's publicly documented patterns, and the execution layer built from that comparison. [Visual report](../../artifacts/research/chat-orchestration.html). Builds on [Chat harness and rich responses](chat-harness.md) (11.11.1).

Labels:

- **Documented:** stated in OpenAI's public docs (sources in §7).
- **Inferred:** a reasonable reading of ChatGPT's observable behaviour, not a documented internal.
- **Built:** implemented here, with tests.
- **Model-limited:** depends on the selected model's quality.

ChatGPT's own harness isn't public, and nothing here claims to reproduce it.

## 1. What a Chat turn does now

```
user message
 └─ LeanSession (environment = chat → model_first)
     ├─ ChatRun: profile from the selected model (compact ≤9B local / full), budget, earlier sources
     ├─ toolbox: Chat allowlist (chat_mode) → focused set for local models (COMPACT_FOCUS or CHAT_FOCUS)
     ├─ prompt: stable sections → volatile context → clock last (cacheable prefix), section sizes recorded
     └─ LeanTurn loop (shared with Agents)
         model call ── no tools? ── final_check (citation audit) ── accept, or 1 correction
              │
              └─ tool calls → call_guard (Chat boundary) → policy/approval → run (parallel when safe)
                   └─ tool_observer → evidence ledger (searches, results seen, pages read, files, memory)
         budget reached → grace answer without tools (never "press Continue")
 └─ execution.metadata.chat_run = trace (path, profile, context sizes, calls, evidence, citations, stop, usage)
```

**Built:**

- `agent/lean/chat_run.py`
- hooks in `agent/lean/loop.py`: `tool_observer`, `final_check`, `grace_prompt`
- wiring in `agent/lean/runtime.py`
- profiles and prompt order in `agent/lean/chat_mode.py`

## 2. Gap analysis

| Concern | OpenAI documented pattern | EchoSpeak through PR #105 | Gap | Now |
|---|---|---|---|---|
| Loop | Runner: model, then tool calls, outputs returned by `call_id`, repeat until a final output. **Documented** | Same cycle in `LeanTurn._run` | none in the cycle | kept |
| Turn limit | `max_turns`, with an error handler returning a controlled final output. **Documented** | One global `max_iterations`; at the limit: "I ran out of steps… press Continue" | no answer at the limit; agent-sized budget | per-profile budget (10 or 6 steps); grace answer from evidence. **Built** |
| Tool choice | The model decides (`tool_choice: auto`); fewer than ~20 tools up front; tool search defers the rest. **Documented** | The model decides; a Chat allowlist; focus for local models | small models still saw the general focus set | `COMPACT_FOCUS` for ≤9B local models; `find_tools` for the rest. **Built** |
| Unknown or forbidden tools | `tool_not_found_behavior` returns an error to the model. **Documented** | Chat `call_guard` refuses with a model-visible reason (11.11.1) | none | kept |
| Search actions | `search`, `open_page`, `find_in_page`; `search_context_size`. **Documented** | `web_search(read=N)` searches and opens the top pages; `safe_web_fetch(objective)`; research notebook | no record of what was read in the turn | evidence ledger: searches, results seen, pages read. **Built** |
| Citations | `url_citation` annotations inline, visible and clickable; a full `sources` list. **Documented** | Links in text; source chips from search results | no provenance: any link accepted | citation audit (read / seen-only / unknown); one correction for invented links or uncited research. **Built** |
| Output checks | Output guardrails; the `invalid_final_output` handler. **Documented** | Agent guards (promise, claim, check nudges) | no answer-level evidence check for Chat | `final_check` hook (one correction, then accept). **Built** |
| Conversation state | Client-managed input list, `previous_response_id` or Conversations; compaction at a threshold, keeping recent items. **Documented** | 30 verbatim messages, a rolling background summary, in-turn tool-output trimming (`_fit_context`) | follow-ups lost the turn's sources | pages read earlier carried into the prompt (notebook, inspected only). **Built** |
| Prompt caching | Providers cache the stable prefix. **Documented** (OpenAI: automatic prefix caching; `allowed_tools` exists to keep the tool list stable) | The clock ("Now: … 11:58") sat mid-prompt and changed every minute | the prefix changed every minute | clock last; stable sections first; section sizes traced. **Built** |
| Model fit | The same contract across models; reasoning effort tunes depth. **Documented** | One Chat prompt for every model | a 4B model got the full contract | compact profile (shorter rules, fewer tools, smaller budget); model never swapped. **Built** |
| Memory | Saved memories and reference chat history (ChatGPT). **Documented** (help centre) | Account and project facts (no specialist notes); Chat-only past-chat recall (11.11.1) | no relevance ranking beyond lexical | kept; semantic recall remains a next step |
| Tracing | Traces and spans per agent, generation and function, `group_id` per conversation, a sensitive-data switch. **Documented** | Lean tracing spans; tool runs persisted | no per-turn Chat record of context, evidence and stop | `chat_run` record on the execution: refs and sizes only, no prompts, replies or reasoning. **Built** |
| Deep research | Multi-minute background runs over many sources. **Documented** | Research workers live in Agents | not in Chat by design | out of scope (Agents) |

## 3. Execution paths

Real traces from `tests/test_chat_paths.py` (scripted model, real loop):

| Path | What happens | Trace |
|---|---|---|
| Simple question | One model call, no tools | `path: direct`, 1 step, no calls |
| Current information | `web_search`, then `safe_web_fetch` on the best result, then an answer linking it | `path: search`, 3 steps; `citations.read` = the page; no corrections |
| Complex research | 2 searches, 2 pages read, then an answer with an invented link; one correction; then an answer citing both pages | `path: research`, 6 steps; `corrections: ["unknown_links"]`; `unknown: []` |
| Memory-dependent follow-up | No tools; the prompt carries memories, the summary and "Pages read earlier in this chat" (notebook ids); the cited earlier page counts as read | `path: direct`; `citations.read` = the earlier page |
| File-grounded explanation | `file_read`, then an explanation of what's actually in the file | `path: files`, 2 steps; `evidence.files` = the path |
| Budget exhausted | 6 searches on a compact model; then one call without tools, answering from findings | `stop_reason: max_steps`; the answer is from evidence, not "ran out of steps" |

Orchestration isn't keyword-routed. The model chooses among direct answer, search, research or files, as in the documented `tool_choice: auto` pattern, guided by Chat's rules: stable knowledge needs no search, current or niche facts do, and research means several angles, reading and cross-checking. The execution layer constrains and checks the outcome (budget, boundary, evidence), but it doesn't pick the path.

## 4. Why a new layer, and why so small

The documented runner loop is the cycle Lean already runs, so a second loop would duplicate it.

What Chat needed are the runner's surroundings, each a narrow, documented concept:

- a turn limit with a fallback output
- an output check
- per-model input shaping
- tracing

So Lean gained three generic hooks, off unless set; Agents and Code are unchanged. `ChatRun` is the one new abstraction, one object per turn holding the evidence, the policy and the trace. The Lean loop's own tests (`tests/test_lean_harness.py`) and the check-step tests pass unchanged.

## 5. Context engineering

- **Order (cache):** identity, about, rules, format, capabilities, environment, skills and project brief are stable across a chat. Then memory, summary, earlier sources, past chats and caller. The clock goes last. A test asserts the clock is the final section.
- **Selection:**
  - Memories are the owner's facts, without specialist notes.
  - Past chats are recalled only when the user points back, and only from Chat conversations.
  - Earlier sources are only pages actually inspected, never search snippets, at most 8.
- **Within a turn:** `_fit_context` trims old tool outputs once the context is half full, then drops old history (still covered by the summary), then summarises earlier steps.
- **Across turns:** 30 messages verbatim plus a rolling summary. This is the documented compaction idea (keep recent items, carry older state compactly), implemented locally because local models have no server-side compaction.

## 6. What is verified, approximated or model-limited

| Item | Status |
|---|---|
| Loop, budget fallback, guard, evidence ledger, citation audit and one correction, profiles, prompt order, earlier sources, trace persistence | **Verified** by deterministic tests (`test_chat_paths.py`, `test_chat_mode.py`, `test_environments.py`, Lean loop tests) |
| Citations are links in text, audited after the fact | **Approximated:** OpenAI attaches `url_citation` annotations with character offsets during generation; EchoSpeak checks links against retrieved evidence and asks for one correction |
| Search actions | **Approximated:** `web_search(read=N)` and `safe_web_fetch(objective)` cover search and open_page; find-in-page is `research_notebook` reading a passage |
| Compaction | **Approximated:** a background summary plus in-turn trimming, not the provider's encrypted compaction items |
| Deciding when evidence is sufficient, query refinement, contradiction handling | **Model-limited:** guided by rules and bounded by the budget; a small model will search less well than a frontier model |
| Live answer quality | **Owner's evaluation** with real models; no paid benchmarks were run |

## 7. Sources

- OpenAI: [Tools](https://developers.openai.com/api/docs/guides/tools), [Function calling](https://developers.openai.com/api/docs/guides/function-calling), [Web search](https://developers.openai.com/api/docs/guides/tools-web-search), [Conversation state](https://developers.openai.com/api/docs/guides/conversation-state), [Compaction](https://developers.openai.com/api/docs/guides/compaction), [File search](https://developers.openai.com/api/docs/guides/tools-file-search)
- Agents SDK: [Running agents](https://openai.github.io/openai-agents-python/running_agents/), [Tracing](https://openai.github.io/openai-agents-python/tracing/)
- [OpenAI Model Spec](https://github.com/openai/model_spec) (CC0); [ChatGPT memory FAQ](https://help.openai.com/en/articles/8590148-memory-faq)

## 8. Next steps

- Show the trace in Research & activity: the path, pages read and cited, and why the turn stopped.
- Cite with offsets (annotations) when the provider returns them natively. OpenAI's web search tool does, but EchoSpeak calls models through its own tools.
- Semantic memory recall (embeddings) in place of lexical overlap.
- An explicit `find_in_page` on inspected sources.
