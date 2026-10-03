# Tools and MCP: where Echo stands and how to give it more (and better) tools

Date: 2026-10-03 (after 10.0.1). Research only; nothing below is implemented yet.

Verdicts: **adopt** = take as-is, **adapt** = take the idea, reshaped for a local app on small models,
**skip** = not now, and why.

---

## 1. Where Echo is today

### Native tools

- **How tools reach an agent.** Each agent gets named toolsets (`apps/backend/agent/lean/toolbox.py` `TOOLSETS`).
  The default set is `core, research, terminal, vision, memory, skills`.
  - `core`: time, math, system info, file tools (list/read/find/search/edit/write/move/delete/undo), artifacts, project status.
  - `research`: web search, safe fetch, YouTube transcript, weather, sports (ESPN), browse, stock history, product search, video search, image search.
  - `terminal`: terminal plus background processes (Docker sandbox by default).
  - `vision`: screenshot, screen analysis, vision Q&A.
  - `memory`: save/search memories, search past chats.
  - Opt-in sets: `desktop` (open apps, desktop control), `comms` (email, Discord), `self` (Echo editing its own code).
  - Native lean tools: `delegate_to_agent`, `complete_task`, `create_artifact` / `update_artifact`, plus the coding tools.
- **Size.** 56 tools are registered. The default toolbox already sends about 23 registry tools (≈8.7k characters of
  schemas), and that's before the coding, terminal, data and native tools are added. On a 64k-context local model,
  tool definitions are one of the biggest fixed costs in every request.
- **Registered but in no toolset:** `code_preview_*` (superseded by artifacts), `generation_*` (ComfyUI and image
  generation), `voice_*`, `terminal_run` (legacy).

### MCP client

`apps/backend/agent/mcp_client.py` and `agent/connections.py` are already solid:

- Transports: stdio, Streamable HTTP and SSE, on the `mcp` Python SDK (>=1.27).
- It lists tools, resources and prompts, refreshes on `list_changed`, and keeps `structuredContent` and resource
  links from results.
- Each server tool's risk comes from its own `readOnlyHint` / `destructiveHint` annotations. These are treated as
  hints only: approvals and the Rule of Two policy still apply.
- MCP reads count as untrusted sources and MCP actions as external actions in `agent/lean/policy.py`.

**Gaps:**

1. **Nobody uses it.** Your install has no MCP servers configured, and adding one means editing `mcp_servers` JSON
   by hand. There's no "add server" screen, no catalogue, and no way to browse the official registry.
2. **All-or-nothing.** Every tool from every connected server goes to every agent with the `skills` toolset. You
   can't give GitHub tools to Glados only, or hide a server's 40 tools from Echo.
3. **No protection against "rug pulls".** A server can change a tool's description after you connected it, and
   nothing notices or asks again.
4. **Spec drift.** The 2026-07-28 MCP revision makes the protocol stateless (no `initialize` handshake, a new
   `server/discover` call, `subscriptions/listen`). It also deprecates Sampling, Roots, Logging and the HTTP+SSE
   transport. The SDK pin will need an upgrade, and SSE should stop being offered for new servers.
5. **MCP Apps aren't rendered.** Servers that ship interactive UI (`ui://` resources) only show text, even though
   the 10.0.1 artifact sandbox could host them.

---

## 2. What the field does now

### More tools without drowning the model

- **Anthropic: Tool Search Tool.**
  - Tools marked `defer_loading` stay out of the prompt. The model searches, and only matching tools are loaded.
  - Reported results: an "85% reduction in token usage". On Anthropic's MCP evaluation, accuracy went from 49% to
    74% (Opus 4) and from 79.5% to 88.1% (Opus 4.5).
  - Recommended once tool definitions pass ~10k tokens, or with multi-server MCP setups of 10+ tools.
  - [Advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use)
- **RAG-MCP (research paper).** Retrieving the relevant tools before the call, instead of listing all of them, cut
  prompt tokens by over 50% and more than tripled tool-selection accuracy (43.13% vs 13.62%) on their benchmark.
  [arXiv 2505.03275](https://arxiv.org/abs/2505.03275)
- **Agent Skills (Anthropic).** "Progressive disclosure": only each skill's name and description sit in context, and
  the full instructions load when the skill is relevant.
  [Equipping agents with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)
- **Verdict:** **adapt.** Small local models need this more than Claude does: tool-selection quality drops fast with
  long tool lists. See recommendation T1.

### Doing more per call

- **Programmatic tool calling and code execution with MCP (Anthropic).**
  - The model writes code that calls tools inside a sandbox, so intermediate data never passes through the context.
  - Examples: 150k → 2k tokens (98.7%) by loading tool definitions on demand; average tokens 43,588 → 27,297 with
    programmatic calls.
  - Caveat from the post: it needs a secure sandbox, resource limits and monitoring.
  - [Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp)
  - **Verdict:** **adapt later.** We already have the Docker sandbox. Best suited to Glados for bulk work (hundreds of
    rows or files). Small models write that glue code less reliably. Recommendation T6.
- **Tool Use Examples (Anthropic).** One or two example calls in a tool's definition raised accuracy on complex
  parameters from 72% to 90%.
  - **Verdict:** **adopt** for the tools Gemma gets wrong. Recommendation T7.

### Managing MCP servers

- **OpenAI Agents SDK.**
  - Per-server tool filters (static allow/block lists, or dynamic).
  - `cache_tools_list` to avoid re-listing.
  - Approval policies per server or tool.
  - [Agents SDK: MCP](https://openai.github.io/openai-agents-python/mcp/)
  - **Verdict:** **adopt** filters per agent and approvals per server. Recommendation T2.
- **Official MCP Registry.** A searchable catalogue of public servers at registry.modelcontextprotocol.io. Each entry
  has a `server.json` (package or remote endpoint, transport, required configuration) under a verified namespace,
  with a REST API.
  [Registry docs](https://modelcontextprotocol.info/tools/registry/)
  - **Verdict:** **adopt** as the source for an in-app "Add a server" browser. Recommendation T2.
- **MCP spec, 2026-07-28 revision.**
  - Stateless requests carrying version and capabilities in `_meta`, plus `server/discover`.
  - Multi Round-Trip Requests replace server-initiated sampling and elicitation.
  - Cacheable list results (`ttlMs`) and deterministic tool order, for prompt-cache hits.
  - Tasks become an official extension.
  - Roots, Sampling, Logging and HTTP+SSE are deprecated.
  - [Changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)
  - **Verdict:** **adopt** when the Python SDK supports it, keeping the 2025-11-25 fallback. Recommendation T4.
- **MCP Apps.** Tools can return interactive UI (`ui://`, sandboxed iframe, JSON-RPC bridge). Supported by Claude,
  ChatGPT, VS Code and Goose.
  [SEP-1865](https://modelcontextprotocol.io/seps/1865-mcp-apps-interactive-user-interfaces-for-mcp)
  - **Verdict:** **adapt.** Reuse the artifact sandbox and side panel. Recommendation T5.

### Security

- **Tool poisoning:** hidden instructions in a tool's description, which the model reads and the user never sees.
- **Rug pulls:** a tool changes after you approved it. `tools/list_changed` re-sends definitions, with no
  re-approval, version pin or hash.
- **Name squatting:** a server impersonating a trusted one.
- Lab tests across 45+ real servers showed attack success rates above 60%.
- Sources: [CSA research note](https://labs.cloudsecurityalliance.org/research-rb/csa-whitepaper-mcp-security-tool-poisoning-20260506-csa-styl/),
  first public proof of concept by Invariant Labs (April 2025).
- **Verdict:** **adopt** pinning, change detection and description scanning before MCP becomes easy to add.
  Recommendation T3.

---

## 3. Recommendations

| # | Change | Why | Effort | Impact |
| --- | --- | --- | --- | --- |
| **T1** | **Tool search for small models.** Keep a small always-on core (≈12 tools: time, math, file read/write/list, web search, memory, `complete_task`, delegate). Add a native `find_tools(need)` that searches a local index of every native and MCP tool (BM25 over name + description + examples, no new model) and makes the matches callable on the next step. Agents' toolsets become "what they're allowed to find", not "what's always in the prompt". | Tool Search Tool and RAG-MCP show large token savings and accuracy gains; Gemma at 64k feels both | M | **High** |
| **T2** | **MCP that people can use.** Connections gets "Add MCP server": paste a command or URL, or search the official registry. Show the server's tools before connecting, then pick which agents get which tools (per-agent allow list) and an approval mode per server (ask / ask for writes / never). Ship a short list of known-good servers as one-click entries (§4). | Today MCP needs hand-edited JSON and gives all-or-nothing access | M | **High** |
| **T3** | **MCP safety.** Store a hash of each tool's name, description and schema when the user connects. If it changes, disable the tool and show "this tool changed, review it". Scan descriptions for instruction-like text ("ignore", "read ~/.ssh", "do not tell the user") and flag them. Reject a server whose tool names shadow built-in tools. | Tool poisoning and rug pulls are the main MCP attacks; must land before T2 | S–M | **High** |
| T4 | **Spec currency.** Upgrade the `mcp` SDK when it supports 2026-07-28; call `server/discover` first, fall back to the 2025-11-25 handshake; stop offering SSE for new servers; cache tool lists with `ttlMs`. | Keep working with new servers; cheaper re-listing | S | Medium |
| T5 | **MCP Apps in the side panel.** When a tool declares a `ui://` resource, open it in the right panel using the artifact sandbox (CSP, token, no network except the server's declared `connectDomains`), bridged with JSON-RPC over postMessage. | Rich third-party UI (Figma, Notion, maps) without new widget code | M | Medium |
| T6 | **Code mode for Glados.** A `run_tools_script` tool: Python in the Docker sandbox with a generated `echo_tools` module wrapping selected tools; only the script's printed result comes back. | Bulk work (rename 300 files, summarise a CSV) in a few steps instead of hundreds | M–L | Medium |
| T7 | **Tool use examples.** Add 1–2 example calls to the descriptions of tools Gemma gets wrong (`update_artifact` ids, `product_search.max_price`, `sports_live.operation`, `file_edit`). | 72% → 90% on complex parameters in Anthropic's tests; cheap | S | Medium |

### New built-in tools worth adding (keyless where possible)

| Tool | What it adds | Source | Card |
| --- | --- | --- | --- |
| `places_search` + `directions` | "Coffee near Union Station", route time and distance | OpenStreetMap Nominatim / Overpass, OSRM (respect usage policies) | `map` |
| `convert` | Currency (daily ECB rates) and units, with an "as of" date | Frankfurter API (keyless), local unit table | `stat` |
| `news_search` | Recent headlines with source and time | DuckDuckGo news (already in `ddgs`) | `citations` / news cards |
| `wiki_lookup` | Quick factual summary with an image | Wikipedia REST summary API | link preview |
| `read_document` | Read PDFs, Word and Excel files in a project (today `file_read` is text only) | Existing document pipeline | — |
| `set_reminder` | "Remind me in 20 minutes" without making a full routine | Routines engine | — |
| `generate_image` | Local image generation (ComfyUI support exists but isn't in any toolset) | Existing `generation_*` tools | `media` |

### Native tools vs MCP: how they fit together

- **One toolbox.** Native and MCP tools register in the same `ToolRegistry` and pass through the same loop.
  Policy, approvals, the audit log, widgets and the Activity panel are identical, so the model never needs to know
  where a tool comes from.
- **Native** for anything core, fast or safety-critical: files, terminal, memory, completion, delegation, web
  search, and the data cards. These are tested in the eval.
- **MCP** for other people's services and the long tail: GitHub, Notion, calendars, smart home, design tools.
  They're opt-in per agent and pinned (T3).
- **Tool search (T1)** works across both, which is what lets the MCP catalogue grow without slowing Gemma down.

---

## 4. MCP servers to offer first

These are reference or vendor-maintained servers. Check the current version and permissions at install time.

| Server | Gives Echo | Notes |
| --- | --- | --- |
| Reference servers ([modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers)): `git`, `time`, `sequential-thinking` | Git history and diffs, time zones, structured planning | `filesystem`, `fetch` and `memory` duplicate native tools; skip those |
| [GitHub MCP server](https://github.com/github/github-mcp-server) | Issues, PRs, code search, Actions | Pairs with Glados; token in the credential store |
| [Playwright MCP](https://github.com/microsoft/playwright-mcp) | Real browser control from the accessibility tree | Overlaps `browse_task`; better for multi-step sites |
| Notion (hosted remote MCP) | Pages and databases | OAuth; replaces part of the native Notion tool |
| Home Assistant (MCP Server integration) | Lights, sensors, scenes | Local network; actions need approval |

---

## 5. Suggested order

1. **T3 + T2 together:** MCP safety first, then the "Add server" screen, so the first servers you connect are
   already pinned and filtered.
2. **T1 tool search,** measured with the eval set: add a few cases where the needed tool isn't in the core set.
3. **New built-in tools** (places, convert, news, wiki, read_document), each with its card and an eval case.
4. **T7 examples**, **T4 spec upgrade**, then **T5 MCP Apps** and **T6 code mode**.
