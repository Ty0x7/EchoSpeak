# Visual responses: research, gap analysis, and the architecture we built

Date: 2026-10-02 (EchoSpeak 10.0.1). Scope:
- the chat renderer (`apps/web/src/lean/`, `apps/web/src/widgets/`);
- the tools that feed it (`apps/backend/agent/tools.py`, `apps/backend/agent/lean/rich_tools.py`, `apps/backend/agent/sports_espn.py`);
- artifacts (`apps/backend/agent/lean/artifacts.py`, `apps/web/src/widgets/ArtifactPanel.tsx`).

Verdicts: **adopt** = take as-is; **adapt** = take the idea, reshaped for a local app running small models;
**skip** = not now, with the reason.

---

## 1. What leading assistants do

### Claude Artifacts (Anthropic)

- **When an artifact is made.** Content goes into an artifact when it is substantial and self-contained (typically
  over 15 lines), and likely to be edited, iterated on or reused outside the chat. Smaller snippets stay inline.
  [Help center](https://support.claude.com/en/articles/9487310-what-are-artifacts-and-how-do-i-use-them)
- **Types:** documents (Markdown/text), code, single-page HTML, SVG, diagrams, and interactive React components.
- **Where it shows:** a panel next to the conversation, plus an Artifacts tab that collects everything made.
- **Iteration:** changes are made through chat. Editing an earlier message creates separate versions, so earlier work
  isn't lost.
- **Publishing:** artifacts are private by default. Published ones can be copied by others. Artifacts can also call
  Claude and keep up to 20 MB of text storage, with confirmation before shared data is shown.
- **Sandboxing:**
  - Artifacts run in sandboxed iframes with strict CSPs that limit network access.
  - A CSP `<meta>` tag at the top of a `sandbox="allow-scripts"` iframe can't be removed or bypassed by the page's
    own scripts, even if the page tries to rewrite itself. Tested in Chromium and Firefox.
    ([Simon Willison, Apr 2026](https://simonwillison.net/2026/Apr/3/test-csp-iframe-escape/))
- **Verdicts:**
  - The "when to make an artifact" rule: **adopt**.
  - Side panel, versions and the Artifacts page: **adopt**.
  - Sandboxed iframe with a CSP: **adapt**. Explained in section 3.
  - React artifacts: **skip for now**. They need Babel in the sandbox (about 3 MB, no network to fetch it), and small
    local models write plain HTML more reliably.
  - Publishing and AI-powered artifacts: **skip**. This is a single-user local app.

### ChatGPT (OpenAI)

- **Canvas.** A side-by-side editor for writing and code. The user can highlight a section to target an edit, use
  shortcuts (adjust length, debug), and go back to earlier versions with a back button.
  [Help center](https://help.openai.com/en/articles/9930697-what-is-the-canvas-feature-in-chatgpt-and-how-do-i-use-it)
  - **Verdict:** versions and restore **adopt**. Direct in-panel editing and highlight-to-edit: **skip for now**
    (proposed).
- **Search results with visual designs.** OpenAI partnered with news and data providers for "new visual designs for
  categories like weather, stocks, sports, news, and maps".
  [Introducing ChatGPT search](https://openai.com/index/introducing-chatgpt-search/)
  - **Verdict:** **adopt** the categories: weather, stocks, sports, maps, and source chips.
- **Shopping.** When the query shows shopping intent, ChatGPT shows a product carousel with images, details and links
  to merchants. It is built from "structured metadata from first-party and third-party providers (e.g., price,
  product description)".
  [Help center](https://help.openai.com/en/articles/11128490-shopping-with-chatgpt-search)
  - **Verdict:** **adapt**. There is no merchant feed for a local app. Instead we read the product page's own
    schema.org data, and Walmart's page data, and stamp the price with an "as of" time.
- **Apps SDK.** Third-party UI runs in a sandboxed iframe and talks to the host over the MCP Apps bridge.
  - Display modes: inline card, inline carousel, fullscreen, and picture-in-picture.
  - `structuredContent` is the data the model sees.
  - `_meta.ui.resourceUri` links a tool to its UI template.
  - CSP allowlists (`connectDomains`, `resourceDomains`, `frameDomains`) sit under `_meta.ui.csp`.
  - Guidelines: inline cards are restrained; data tools are kept separate from render tools; and "return enough
    structured content so workflows succeed even without UI rendering".
  - [Build your ChatGPT UI](https://developers.openai.com/apps-sdk/build/chatgpt-ui)
  - **Verdicts:**
    - Text that works on its own, with structured data beside it: **adopt**. Every tool still returns text for the
      model.
    - Inline cards and carousels: **adopt**.
    - Third-party UI: **skip**. No third-party apps yet.

### Gemini (Google): generative UI

- **What it does.** "Dynamic view" designs and codes a custom interactive response for each prompt. "Visual layout"
  gives a consistent styled answer.
- **How it is built:** tool access (image generation, web search), long system instructions, and post-processing to
  fix common mistakes. It runs on Gemini 3 Pro.
- **Evaluation:** raters strongly preferred it over plain markdown answers, though not over expert-built sites.
- **Known limits:** generation can take over a minute, and outputs are sometimes inaccurate.
- Source: [Google Research](https://research.google/blog/generative-ui-a-rich-custom-visual-interactive-user-experience-for-any-prompt/)
- **Verdict:** **adapt, partly.**
  - Fully generated UI for every answer is too slow and error-prone on a 4B local model.
  - We keep generated UI for *artifacts* only, where the user asked for something to use.
  - Everything else uses fixed, fast widgets.

### Open standards

- **MCP Apps (SEP-1865, Final).** A tool declares a `ui://` resource (`text/html;profile=mcp-app`) through
  metadata. The host renders it in a sandboxed iframe, and the UI talks to the host over MCP JSON-RPC.
  - It was built from the experience of MCP-UI and the OpenAI Apps SDK.
  - The security model is mandatory iframe sandboxing, predeclared templates, auditable messages, and optional user
    consent for tool calls started from the UI.
  - [SEP-1865](https://modelcontextprotocol.io/seps/1865-mcp-apps-interactive-user-interfaces-for-mcp),
    [MCP-UI](https://mcpui.dev/)
  - **Verdict:** **adapt.** Our cards are the same idea: a tool result plus a UI. But they are built-in components,
    not server-supplied HTML. Rendering MCP Apps from connected MCP servers is proposed (P3).
- **Vercel AI SDK, generative UI.** Tool results arrive as message parts named `tool-<name>`, each with a state
  (`input-available`, `output-available`, `output-error`). The client maps each tool to a component and shows
  loading and error states.
  [AI SDK docs](https://ai-sdk.dev/docs/ai-sdk-ui/generative-user-interfaces)
  - **Verdict:** **adopt** the pattern of mapping tools to components.
    - Our `tool_end` event carries `widgets`.
    - `WidgetView` maps each type to a component.
    - The tool row shows the loading state.
    - A failed tool shows no card.

### Perplexity

- **What it does (observed, plus secondary sources):**
  - Numbered source citations on every answer.
  - Image and video results.
  - Finance pages with live quotes and charts.
- I found no primary help-center page describing the answer cards.
- **Verdict:** **adopt** numbered source chips at the end of the answer, merged across all searches in the reply.

---

## 2. Gap analysis (before this round)

| Gap | Where | Fixed by |
| --- | --- | --- |
| Tool results reached the UI only as a 1,600-character text preview | `agent/lean/loop.py` `_tool_finished` | `widgets` in `tool_end` and the timeline; `agent/lean/widgets.py` |
| Replies were plain markdown: no highlighting, math, diagrams, or table tools | `apps/web/src/lean/LeanMessage.tsx` | `widgets/RichMarkdown.tsx`, `CodeBlock.tsx`, `Mermaid.tsx`, `DataTable.tsx` |
| Weather tool returned current conditions only | `agent/tools.py` `weather_live` | Hourly and 7-day forecast, °F/mph for US locations, weather card |
| No stock, product, video or image data | — | `agent/lean/rich_tools.py` |
| `sports_live` needed `ODDS_API_KEY` and failed without it | `agent/sports_data.py` | `agent/sports_espn.py` (keyless) |
| Links opened inside the app's webview | `LeanMessage.tsx` (`target=_blank`) | `widgets/env.tsx` `openExternal` + desktop `open_external_url` |
| Remote images couldn't be shown safely (third-party hosts, desktop CSP, auth) | — | `/lean/media` proxy (`agent/lean/media_proxy.py`) |
| No artifacts; desktop CSP had `frame-src 'none'` | `apps/desktop/src-tauri/tauri.conf.json` | `agent/lean/artifacts.py`, `ArtifactPanel.tsx`, `frame-src http://127.0.0.1:*` |
| No guidance on when to use which format | `agent/lean/prompt.py` | `SHOWING_ANSWERS` section |

---

## 3. Architecture

### Tier 1: built-in widgets

There are two ways a widget gets made. Both are checked on the server (`agent/lean/widgets.py`) and again in the
browser (`apps/web/src/widgets/validate.ts`).

1. **Tool cards: the data never passes through the model.**
   - A tool calls `attach({"type", "data"})` with data straight from its source.
   - The loop sends the cards in `tool_end.widgets` and stores them in the timeline, so they also show after a reload.
   - The model gets text and writes a short summary. It cannot change a price, link or number on a card.
2. **Model blocks.**
   - Fenced code whose language names a widget (```` ```chart ````, `steps`, `timeline`, `comparison`, `stat`,
     `map`), with JSON inside.
   - ```` ```mermaid ```` diagrams and `$$…$$` math.
   - Invalid JSON or a bad schema renders as a plain code block.
   - While a reply is still streaming, blocks wait ("Preparing chart…") so half-written JSON never flashes.

| Widget | Schema (`data`) | Source |
| --- | --- | --- |
| `weather` | `location, units, current{temp, feels, code, humidity, wind, wind_unit}, hourly[{time, temp, code, precip}], daily[{date, max, min, code, precip}], as_of, source` | `weather_live` (Open-Meteo) |
| `chart` | `kind(line\|bar\|area\|pie), title, labels[], series[{name, values[]}], unit, y_label, as_of, source, source_url` | `stock_history` (Yahoo) or a ```` ```chart ```` block |
| `product_carousel` | `query, items[{title, url, image, price, currency, merchant, rating, reviews}], as_of` | `product_search` (store pages) |
| `media` | `kind(video\|image), query, items[{title, url, thumbnail, image, duration, publisher, published, width, height}]` | `video_search` / `image_search` (DuckDuckGo, YouTube) |
| `citations` | `items[{title, url, site, snippet}]` | `web_search`, `safe_web_fetch` |
| `score_card` | `title, games[{home, away, home_score, away_score, status, start, league}], as_of` | `sports_live` (ESPN) |
| `timeline` / `steps` | `title, ordered, items[{when, title, detail}]` | ```` ```timeline ```` / ```` ```steps ```` |
| `comparison` | `title, items[{name, url, image, summary, specs{}}]` (2–4 items) | ```` ```comparison ```` |
| `stat` | `title, items[{label, value, unit, change, note}], source` | ```` ```stat ```` |
| `map` | `title, places[{name, lat, lon, address, note}]` | ```` ```map ```` (coordinates from tools) |
| `code` | language, file name (```` ```py title="app.py" ```` or ```` ```py:app.py ````), diff view | any fenced code |
| `table` | markdown table; sortable, copy as TSV or Markdown | markdown |
| `diagram` | Mermaid source, strict security level, lazy-loaded | ```` ```mermaid ```` |
| `math` | KaTeX, `$$…$$` only, so prices like "$5" stay text | markdown |
| `artifact` | `id, title, kind, version, language` (the card) | `create_artifact` / `update_artifact` |

**Every widget has:**
- a loading state (the tool row spinner, or "Preparing…" for blocks);
- an empty or broken state (a placeholder for missing images, nothing shown for invalid data);
- a plain-text fallback inside an error boundary;
- a dark theme matching the app;
- a layout that wraps or scrolls at narrow widths.

The chart palette is the dataviz reference palette's dark steps, validated against the card surface (#141415): all
checks pass.

### Tier 2: artifacts

- **Tools.**
  - `create_artifact(title, type, content, language?)` takes `html`, `svg`, `mermaid`, `markdown` or `code`.
  - `update_artifact(artifact_id, content | edits[{find, replace}])`.
  - Each change saves a new version (up to 50), and any version can be restored.
  - A missing or wrong id falls back to this chat's latest artifact. Gemma sometimes misremembers ids.
- **Storage:** `DATA_DIR/lean/artifacts/<id>.json`.
- **Sandbox, for HTML and SVG.**
  - The page is served at `/lean/artifacts/{id}/frame` with this CSP header: `sandbox allow-scripts allow-modals;
    default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; connect-src
    'none'; form-action 'none'; frame-src 'none'`. The same CSP (minus `sandbox`) is also injected as the first
    `<meta>` in `<head>`.
  - The panel's iframe adds `sandbox="allow-scripts allow-modals"`.
  - Result: an opaque origin, no network, no forms, no popups or top-level navigation, and no access to the app,
    the API, cookies or files.
  - **Why not `srcdoc`?** An `about:srcdoc` (or `blob:`) iframe inherits the parent page's CSP. The desktop app's
    `script-src 'self'` would block the artifact's inline scripts, so a separate URL is required.
- **Auth.** The desktop backend requires an auth header that an `<iframe src>` can't send. The panel asks for a
  5-minute, single-artifact token (`POST …/frame-token`, which is authenticated), and the middleware accepts that
  token for that one frame URL only.
- **Panel features:** preview/source, version arrows, restore, copy, download, open in browser (still sandboxed),
  reload, full screen. It opens automatically when an agent creates or updates an artifact, and from the new
  Artifacts page.

### Selection rules (system prompt, `SHOWING_ANSWERS`)

- Plain text for simple answers.
- A card when a data tool fits the question.
- A fenced block for model-made charts, steps, comparisons, stats or maps, using numbers only from tool results or
  well-known facts.
- Mermaid for "how does X work".
- An artifact for substantial or interactive output.
- A rich block always comes with a short text answer.
- Never invent links, images, prices or figures.

### Safety and robustness

- **Links** go through `openExternal`. Only http(s) URLs are allowed, and on desktop they open in the system browser
  through the `open_external_url` command.
- **Remote images** go through `/lean/media`:
  - public addresses only, with DNS pinned (shared with `safe_web_fetch`);
  - 6 s timeout and 5 MB cap;
  - images only, with SVG served under a sandbox CSP;
  - a 200 MB disk cache.
  - On desktop, images are fetched with the auth header and shown as blobs.
- **New data tools** count as untrusted sources for the Rule of Two policy.

---

## 4. End-to-end results (Gemma 4 E4B, `scripts/eval_gemma.py --only 23-30`)

| Prompt | Expected | Result |
| --- | --- | --- |
| "What's the weather in Denver this week?" | weather card | PASS: `weather_live`, 7 days |
| "Compare Nvidia and AMD stock this year" | chart | PASS: `stock_history`, % change chart |
| "Find me a wireless controller under $80" | product carousel with real links | PASS: 8 Walmart listings, $13.99–$29.99 |
| "Show me a video on soldering SMD parts" | video card | PASS: YouTube cards with durations |
| "Build me a tip calculator" | artifact in the panel | PASS: HTML app, v1 |
| "Make it also split the bill…" | v2 of the same artifact | PASS on rerun. The first run failed: a made-up id broke the update, so missing or wrong ids now fall back to the chat's latest artifact |
| "Explain how TCP handshakes work" | text + Mermaid | PASS on rerun. The first run had no diagram; the prompt now includes a (non-TCP) Mermaid example |
| "What's 2+2?" | plain text | PASS: "4." with no card or block |

The same rerun also covered: the chart tooltip, source chips, the Artifacts page, the panel's version arrows
(v1 ↔ v2), and the sandboxed app running with its inputs.

---

## 5. Proposed, not done

| # | Proposal | Why not now |
| --- | --- | --- |
| P1 | React artifacts | Needs Babel in the sandbox (about 3 MB, no network); HTML covers the same use cases on small models |
| P2 | Editing directly in the artifact panel, and highlight-to-edit (Canvas-style) | A larger editor component; follow-up prompts already create versions |
| P3 | Render MCP Apps (`ui://` resources) from connected MCP servers | Needs the MCP Apps bridge (JSON-RPC over postMessage) and per-server CSP allowlists |
| P4 | Product search beyond Walmart and schema.org stores (Best Buy, Amazon) | Those sites block plain page fetches; would need a shopping API key |
| P5 | Places and directions tool (geocoding) to feed the map widget | Nominatim's usage policy and rate limits; today the map uses coordinates from other tools |
| P6 | Inline video playback | Needs `frame-src` for YouTube; cards open the video in the browser instead |
