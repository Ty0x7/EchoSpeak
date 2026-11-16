# Chat: harness, intelligence and rich responses

How EchoSpeak's Chat environment works after this review, and why it changed. [Visual report](../../artifacts/research/chat-harness.html). Builds on [Chat, Agents and Code](three-environments.md).

Evidence labels:

- **Source:** read in the code.
- **Captured:** the actual request Chat sent to a model, recorded by a local fake OpenAI-compatible server. No real model was used, so there was no cost.
- **Test:** a deterministic repository test.
- **Browser:** a real browser running the production build with real Mermaid and KaTeX, driven by `apps/desktop/scripts/check-chat-render-browser.cjs`.
- **Owner:** the owner's live-model evaluation.

## 1. Summary

The model didn't get worse. Five things the harness and the renderer did made Chat look and feel worse:

| Symptom | Cause | Kind |
|---|---|---|
| Mermaid shown as source code | Mermaid rejects common model mistakes, such as unquoted `(`/`:` in labels or `A --> B: text`, and the app then showed the source | rendering |
| Mermaid shown as source, part 2 | A reply shown from its final text or a recovered turn went through a second, plain Markdown renderer with no Mermaid, math or tables | rendering |
| Diagrams only appeared after the whole reply | New Markdown renderers every token remounted each diagram, cancelling its render | rendering |
| Math shown as raw `\( … \)` / `\[ … \]` | Only `$$` was recognised, while OpenAI's Model Spec has models default to `\( \)` and `\[ \]` | rendering |
| Shallower, more "agentic" answers | Chat kept Echo's agent rules and computer-control text but lost the answer-quality guidance in Echo's SOUL | harness |

## 2. Rich responses: the pipeline and its fixes

### How it works

The reply streams as `agent_token` events:

1. The live reducer builds the message.
2. `LeanMessage` renders text segments with `RichMarkdown`: remark-gfm, remark-math, rehype-katex, and custom `code` renderers for Mermaid, chart widgets and code.
3. On reload, `/history` returns the saved assistant item (`agent_id`, `provider`, `timeline`), and `historyProjection` rebuilds the same message.

**Source.**

### What was wrong

1. **Invalid Mermaid fell back to source.** Of 20 realistic model outputs run through Mermaid 11 in the browser, 6 failed. **Browser.** The failures:
   - unquoted `( )` in labels
   - a label starting with `/`
   - `A --> B: text`
   - `→` arrows
   - a node named `end`
   - a sentence left inside the block
2. **A second renderer.** `ResponseRenderer`, used for any assistant message without live-event attribution, rendered plain `ReactMarkdown`. **Browser:** see the "before" screenshot.
   - An example is a reply that ran under another request id, such as a queued follow-up.
   - The live buffer drops events whose `request_id` differs from the turn it started (`lean/sessionLiveBuffer.ts`).
   - So the final text was appended as a plain message.
3. **Remount on every token.** `RichMarkdown` passed a new `components` object to `react-markdown` on every render. React treats each new function as a new component type, so every diagram, chart and code block remounted on every token. A diagram's 60 ms timer and asynchronous render never finished while text was still arriving. **Browser:** the mid-stream check failed before the fix.
4. **Theme.** Mermaid ran with `theme: "dark"` and `themeVariables`, but Mermaid only honours `themeVariables` with the `base` theme. Light mode showed dark nodes.
   - Re-rendering under an id already on the page also made Mermaid edit the visible SVG.
   - **Browser:** a light switch left the nodes dark.
5. **Math.** Only `$$ … $$` rendered.

### Changes

- **`widgets/mermaidRepair.ts`** fixes the common mistakes:
  - It quotes risky labels and leaves the valid shapes alone: `[( )]`, `[/ /]`, `(( ))`, `{{ }}`, `([ ])` and `[[ ]]`.
  - It converts colon edge labels to `-->|…|` (never inside a label), maps unicode arrows, renames an `end` node and drops trailing prose.
  - The original is always tried first, and Source shows what the model wrote. A repaired diagram says "syntax fixed".
  - **Test** and **Browser:** all 9 failing patterns draw after repair; valid diagrams are unchanged.
- **`widgets/Mermaid.tsx`:**
  - One render at a time, with a fresh element id each render.
  - The `base` theme with dark and light palettes, redrawn when `data-es-theme` changes.
  - A cache of drawn SVGs by theme and source.
  - Diagnostics for a diagram that can't be drawn: the parser's message, the line with its caret, the source, and Copy source.
  - Legible sizing: the height is capped on desktop. A wide diagram that would shrink below 70% keeps a readable size and scrolls inside its card, never the page.
- **`widgets/RichMarkdown.tsx`:**
  - Stable renderers: one `components` object per message, reading the current text from a ref.
  - A diagram or widget draws as soon as its fence closes, even mid-stream. Only the block still being written waits (`endsInOpenFence`).
  - `\( … \)` and `\[ … \]` become KaTeX (`normalizeMathDelimiters`), never inside code, and a single `$` stays text.
- **`features/responseRenderer`** renders with `RichMarkdown`, so every assistant message looks the same however it reached the screen.

Charts, tables (sortable, copyable), code blocks and source chips already rendered correctly in the live path. They now behave the same in every path. **Browser.**

## 3. Chat's capabilities, enforced in trusted code

`agent/lean/chat_mode.py` defines Chat's own policy on the shared Lean loop. It has two layers.

### Layer 1: allowlist (`restrict`)

Tools outside `ALLOWED` are removed from the toolbox, including deferred ones, so `find_tools` can't surface them. New tools are denied until listed. Connected apps (`mcp`, `skill`, `connection`) are allowed, and their actions still ask first.

| Chat can | Chat can't (Agents and Code do these) |
|---|---|
| Web search, read pages, research notebook, YouTube transcripts | Run commands (`terminal`, processes) |
| Live cards: weather, sports, stocks, products, videos, images | Change files (write, edit, delete, move, undo) |
| Read files and folders the user points at | Desktop input (`computer_action`, clicks, typing, hotkeys) |
| Read and answer about images the user shares | Capture the screen (`take_screenshot`, `analyze_screen`) |
| Memory, past Chat conversations, calculate | Drive a browser, send email |
| Artifacts, image and video generation, reminders | Delegate, spawn workers, edit a persona, agent work tools |
| **Open an app by name** (optional; asks first) | |

### Layer 2: run-time guard (`call_problem`)

The guard is set on `LeanTurn.call_guard`. It runs after alias resolution and before policy and approvals:

- A call to any tool outside the allowlist is refused, including an alias ("bash" maps to terminal) and a hallucinated name. It's audited as an `environment` denial.
- `open_application` takes no arguments in Chat and never opens a command interpreter or system tool (cmd, PowerShell, Windows Terminal, Python, regedit, mmc…).
- The app's own Settings › Permissions switch and approval still apply.

**Test:** with every computer permission switched on, the Chat toolbox has none of the agent tools, and a model calling `bash` is refused without running.

## 4. The Chat harness

### Measured, not assumed

Captured: the same message on the same fake model, with the same provider, temperature 0.7 and settings.

| | Echo (Agents) | Chat in 11.11.0 | Chat now |
|---|---|---|---|
| System prompt | 14,894 chars | 10,273 | 7,104 |
| Answer-quality guidance | SOUL: answer, then why, then example; form a view; separate known, inferred and unverified | 3 generic sentences | `## How to answer` (below) |
| Agent working rules ("keep going until finished", computer control) | yes | yes | no |
| Wrong instructions | none | "find this agent under Agents… Edit" | none |
| Tools offered (local model, focused) | 27 | 24, incl. screenshot | 23, no screen capture |

### Diagnosis

Chat in 11.11.0 kept the parts of Echo's prompt that push toward acting:

- "You can act, not just talk."
- "Keep going until the task is actually finished."
- The computer-control and screenshot rules.

It dropped the parts that make answers good. Echo's SOUL carries the depth and judgment guidance ("the answer first, then the why, then an example"; "I don't hide behind 'it depends'"). The Chat persona replaced it with three sentences. The same model, with thinner answer-quality guidance and louder agent instructions, plausibly gives shorter, flatter or more tool-happy answers. This is a harness regression, not a model one. Live quality is the owner's evaluation.

### Chat's own instruction composition

`chat_mode.build_prompt` is ordered stable-first for prefix caching:

1. Identity: "You are {model}… a general-purpose assistant. Not Echo."
2. About EchoSpeak.
3. `## How to answer`.
4. `## When to use tools`.
5. `## Formatting`.
6. Environment.
7. `## What you can use in Chat`.
8. Memory, skills, project brief, chat summary, past chats and caller.

The rules follow OpenAI's public guidance, used as an engineering reference rather than evidence of ChatGPT's internals:

- **The Model Spec (CC0):**
  - Be thorough but efficient.
  - Proceed with a stated assumption unless a wrong guess is costly.
  - Express uncertainty when it would change what the user does.
  - Don't just agree.
  - Use Markdown with `\( \)` and `\[ \]` math.
- **OpenAI's web search guide:**
  - The model decides when to search.
  - It runs several searches and opens pages for harder questions.
  - Inline citations must be visible and clickable.

Chat's rules translate these into:

- Answer from stable knowledge without searching.
- Search for anything current, niche or explicitly asked.
- A simple lookup is one search; research is several searches, reading pages and cross-checking.
- Cite only pages actually read, with inline links.
- Calibrate depth. Form a view. Don't flatter.
- Use tools only when they help.
- Explain in one sentence when something belongs to Agents or Code.

The formatting rules match what the app renders:

- Mermaid with quoted labels and `-->|label|` edges.
- `\( \)` and `\[ \]` math.
- The chart, steps, timeline, comparison, stat and map JSON blocks.

### Execution policy

- **Smart model choice never swaps Chat's model.** Chat is the model the owner picked.
- **Unchanged and kept:**
  - the shared loop
  - streaming and approvals
  - the first state-changing call creating a work record (R4), which is the safety spine for receipts
  - research workers stay with Agents
  - a 30-message verbatim window plus the rolling summary

## 5. Context and memory

There's no new database. Chat gets a logical scope over the existing stores. **Test.**

- **Facts:** the owner's account and project memories. Specialist-private memories (`semantic_key` beginning `agent:<id>:`) are filtered out, so Jarvis's or GLaDOS's working facts don't leak into Chat.
- **Past chats:** both the automatic "you mentioned…" recall and the `chat_search` tool are limited to Chat conversations. Agents rooms and Code sessions stay out.
- **Continuity:** the recent turns verbatim (30 messages) and a background summary of older turns. This mirrors OpenAI's conversation-state pattern: recent context verbatim, older context compacted.
- **Saving:** only on request or for clearly lasting personal facts, and never web content.

ChatGPT's public memory model has two layers, saved memories and reference chat history. It maps onto EchoSpeak's account memories and Chat-scoped past-chat recall.

## 6. Verification

- **Backend tests:** `tests/test_chat_mode.py` (allowlist with every permission on, run-time guard with aliases and `open_application` rules, prompt composition, no model swap, memory and past-chat scope), plus `tests/test_environments.py`.
- **Web tests:** `widgets/chatRendering.test.tsx` (repair rules, error detail, math normalisation, fence detection, the fallback renderer now rich).
- **Browser:** `check-chat-render-browser.cjs` runs the production build with real Mermaid and KaTeX. It checks:
  - **while streaming:** the diagram draws as soon as its fence closes
  - **after streaming:** the repaired diagram drawn and marked, the broken one with parser line, caret and source, inline and display math, the chart, the table, and the price kept as text
  - **after a reload from saved history:** in light and dark palettes
  - **a live theme change:** diagrams redraw in the new palette
  - **phone width:** a wide diagram scrolls inside its card with labels of at least 10 px, and the page never scrolls sideways
- **Captured:** the Chat request before and after (sections, sizes, tool list).

Run the browser check from `apps/desktop` after building `apps/web`:

```powershell
node scripts/check-chat-render-browser.cjs
```

If Playwright isn't installed in the repository, set `ECHOSPEAK_PLAYWRIGHT_MODULE` and optionally `ECHOSPEAK_CHROMIUM_PATH`.

## 7. Limits and next steps

- **Repair stays conservative.** A diagram with deeper errors shows diagnostics and its source rather than a guess.
- **Live answer quality with real models is the owner's evaluation.** The changes above remove the harness causes that were measured.
- **Possible follow-ups, deliberately not built:**
  - A "fix this diagram" button that asks the model to correct its own Mermaid.
  - A Chat-only reference-chat-history summary.
  - Semantic, not just lexical, memory recall.
