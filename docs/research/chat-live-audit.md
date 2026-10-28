# Chat live-test audit: generation lifecycle and reliability

This report starts from the owner's Gemma 4B Chat live test. It traces the full lifecycle (input → context → model and streaming → tools → retrieval → review → memory → answer → UI) and fixes the confirmed causes. [Visual report](../../artifacts/research/chat-live-audit.html). It builds on [the research engine](research-engine.md) (11.11.3).

**Evidence used:** the transcript itself didn't reach this session; neither message carried it and no file was found. Findings come from the owner's written observations, the code, and deterministic reproductions in `tests/test_chat_lifecycle.py` using the real loop, a scripted model and recorded stream events. Each claim is labelled:

- **Confirmed:** reproduced or read in code.
- **Fixed.**
- **Model-limited:** depends on model quality.
- **Uncertain.**

## 1. The double-thinking behaviour

**What the owner saw:** a long thinking phase. Then the answer starts streaming, is cut off, a second thinking phase begins ("reminding itself how to respond"), and a new answer repeats or changes the first, sometimes apologising for criticism nobody gave.

**What triggered it.** Three separate code paths, all in the shared Lean loop (`agent/lean/loop.py`), all **Confirmed**:

| # | Trigger | What happened in the stream | Intentional? |
|---|---|---|---|
| 1 | **The 11.11.2/11.11.3 citation check** (`ChatRun.check_answer` via `final_check`) found an unknown or unsupported link in a finished answer | `_retract_text` sent `text_replace` with empty text, which erased the visible answer. Then a correction ("These links in your answer weren't…") was appended **as a user-role message**, and the model was called again with thinking on: second thinking phase, second answer | Intentional correction pass, **exposed badly and phrased as the user** |
| 2 | **The claim guard** (`unbacked_claim`): "I've saved it" with no `memory_save` | Same pattern: erase, a user-role "You wrote … but … isn't true", regenerate | Intentional, same flaw |
| 3 | **The output limit** (`finish_reason == "length"`; a 4B model on an 8k context gets about 4k output tokens) | With text: the half answer was accepted as final (cut off mid-sentence). Without text (reasoning used the budget): the turn was **discarded**, a user-role "Your last reply was cut off…" was appended, and the model **reasoned again from scratch** | Unintended |

The apologies follow from #1 and #2. To the model, a user-role "You wrote X, but…" is the user objecting. A small model apologises and often rewrites everything, sometimes changing topic.

**It wasn't a frontend defect.** The UI rendered exactly what the backend sent: an erase, then a new step. It also wasn't a provider bug. Request ids and event ordering were correct.

### What was corrected

- **Answers are reviewed, never regenerated.** A new `LeanTurn.answer_review` hook replaces Chat's `final_check`. It runs after the answer is complete and returns notes plus, at most, a light in-place fix: links to pages that weren't retrieved, or that couldn't support the answer, lose their link target and keep their words. It never erases the answer and never calls the model again. **Fixed and tested:** exactly 2 model calls for fetch + answer, and no empty `text_replace`.
- **The claim guard in Chat notes instead of rewriting** (`claim_nudge_limit = 0`). It uses the existing "Not verified" note path. **Fixed and tested.**
- **The output limit continues instead of restarting:**
  - A partial answer gets a continuation call with **thinking off**. The text continues in the same answer, up to two times.
  - If reasoning used the whole budget, the model is asked to answer *from where its reasoning ended* (the tail is included), with thinking off.

  **Fixed and tested:** the calls run with thinking `[True, False]`, and the provider's reasoning setting is restored.
- **App instructions are never framed as the user.** Every loop-injected note is wrapped in `<echospeak-note>…</echospeak-note>`: promise, claim, check, empty, length, reminder, grace. Chat's and Agents' rules say these notes come from EchoSpeak and are never to be apologised for. **Fixed and tested.**
- **Role alternation is kept.** The old empty-reply path popped the assistant message and appended a user message after the user's own, giving two consecutive user messages, which Gemma-style templates reject or mishandle. `add_note` merges a note into a preceding user or tool message. **Confirmed and fixed.**

## 2. Findings by area

| Area | Finding | Status |
|---|---|---|
| Conversation integrity (1, 2) | Corrections as user-role messages led to phantom criticism and rewrites | Confirmed, fixed (§1) |
| Factual reliability (3) | No claim-level check: figures in an answer weren't compared with what was read | **Fixed:** after research, figures (money, %, decimals, scaled and large numbers) not found in any retrieved page or result are listed under the answer as unverified. Years, small counts and the user's own numbers are excluded |
| Regulatory authority (4) | Government and regulator sites had no ranking advantage; blogs could outrank the governing source | **Fixed:** an official-source scoring boost (government domains, `alberta.ca`, `canada.ca`, regulators) of +0.6 for regulatory, tax, rate or time-change questions. Tested: `alberta.ca` outranks blogs for daylight-saving rules |
| EchoSpeak identity (6) | With no link, a web search for "EchoSpeak" found other apps with the name | **Fixed:** "what's new in EchoSpeak" reads the app's own `pulls` and `releases` through the GitHub API before the first model call. The app knows its own source; this is not general keyword routing |
| Tool discovery (7) | The capability list said "Live data cards (weather, …)", and a 4B model tried to load it as a skill | **Fixed:** capabilities name the actual tools ("Live data cards: weather_live, sports_live, …") and say they're tools, not skills |
| Sports (8) | Times were converted to the PC's clock but unlabelled, so the model could convert them again or call them Eastern | **Fixed:** the model's text says "7:00 PM Alberta Daylight Time (your time)"; cards stay short. Official verification of scores still depends on ESPN's data (model and source limited) |
| Social statistics (9) | Third-party stats sites are inconsistent; field meaning (followers vs views) is often ambiguous | **Model- and source-limited.** The figure check now flags numbers not found in what was read |
| App roster (10) | Chat guessed EchoSpeak's agents and capabilities | **Fixed:** an "About this app" section from the live persona store (roster, environments, own source) |
| Memory (11) | Recalled facts had no date or scope | **Fixed:** each recalled fact shows "(saved 2026-10-20, this project)" so the model can say when it was told and notice staleness. `memory_save` already verified on disk; unchanged |
| Mermaid `valueOf` (12) | A Mermaid *runtime* exception was shown as "the source has a syntax error" | **Fixed:** parse first. A parse failure is a syntax error (repair may apply); an exception after a clean parse is reported as Mermaid's own failure, and repair isn't attempted. Real-browser check passes |
| PDF and spreadsheet (13) | No PDF or Excel generator exists anywhere in EchoSpeak; Chat silently made HTML instead | **Fixed:** Chat's rules and capability list say it can't make PDF or Office files. It offers Markdown, printable HTML or CSV (opens in Excel), or sending to an agent, and never substitutes silently |
| Latency and visible thinking (14) | Second thinking phases from §1; reasoning shown in the thinking panel is the provider's reasoning channel | The regenerations are fixed. Reasoning length itself is the model and the thinking setting (**model-limited**); the panel is collapsible as before |

## 3. Also found during the audit

- **Gemma-style alternation broken by the empty-reply nudge** (§1).
- **Partial answers at the output limit were accepted silently**, so users saw answers cut off mid-sentence.
- **The reasoning-exhaustion path threw away the model's reasoning** and paid for it twice.
- **The citation check would also have run on answers to plain questions with links,** where it couldn't help; it already skipped those, and still does.
- **Unknown or invented links stayed clickable** after a correction failed. They're now de-linked in the final, persisted text.

## 4. What worked and is preserved

Each of these is covered by tests that still pass:
- direct GitHub URL retrieval (`test_research_engine`)
- search across categories
- follow-ups using earlier sources (`test_chat_paths`)
- memory save verification
- `find_tools` recovery
- tables, HTML artifacts and rich rendering (browser check)
- the Chat, Agents and Code boundaries (`test_chat_mode`, `test_environments`)

## 5. Verification

Focused tests only, for the components changed:

- **New:** `tests/test_chat_lifecycle.py` (10):
  - an unverified link fixed in place with no second generation
  - an untrue claim noted, not rewritten
  - output-limit continuation without thinking again
  - reasoning-spent answered from, not restarted
  - app notes never look like the user and keep alternation
  - figure check
  - real tool names and truthful file formats
  - official sources ranked first
  - memory provenance
  - sports zone label
- **Updated:**
  - `test_research_engine.py`: no-URL EchoSpeak updates now read the app's own repository; the ranking case moved to the search tool itself.
  - `test_check_step.py`: the note is wrapped.
  - `test_environments.py`: the roster is described, but no team section.
- **Re-run and passing:** `test_chat_paths`, `test_chat_mode`, `test_lean_harness`, `test_group_completion`, `test_lean_soul`, `test_lean_work_together`, `test_live_fixes`, `test_memory_quality`, `test_sports_espn`, `test_live_steps`, `test_web_search_providers`.
- **Web:** `liveReducer.test.ts` (answer notes keep the answer in place), `chatRendering.test.tsx` (renderer vs syntax errors), typecheck, and the production-build browser check `check-chat-render-browser.cjs` (5 of 5).

## 6. Limits and owner live-test instructions

**Limits:**

- The figure check is lexical. A correct figure written differently from the source (for example "1.2 billion" vs "1,200 million") is flagged.
- Contradictions between sources are weighed by the model.
- Reasoning length on a 4B model is a model property; the fix removes *repeated* reasoning, not long reasoning.

**Owner tests, on Gemma 4B in Chat:**

1. A long question with thinking on (for example "compare three mortgage options with current rates"). Expect one thinking phase and one answer, with any unverified figures or links listed as a note under it.
2. "What's new in EchoSpeak?" with no link. Expect the pull requests and releases, without other apps.
3. "Is Alberta keeping daylight saving time?" Expect alberta.ca cited.
4. "Weather in Calgary tomorrow." Expect weather_live, and no skill lookup.
5. "Make me a PDF and an Excel sheet of this." Expect a plain statement of what's possible, and a CSV or Markdown offer.
6. A Mermaid Gantt chart. Expect a drawn chart, or a renderer-error message instead of "syntax error".
7. "Remember I prefer metric," then later "what do I prefer?" Expect the fact, with when it was saved.
