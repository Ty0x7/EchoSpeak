# Search and research engine: audit and overhaul

EchoSpeak's search, retrieval and research pipeline was audited from the user's message to the cited answer, and the engine was rebuilt where the audit found structural faults. [Visual report](../../artifacts/research/research-engine.html). It builds on [Chat orchestration](chat-orchestration.md) (11.11.2).

Labels:

- **Documented:** an official source states it.
- **Production-proven:** a widely deployed technique, such as BM25, RRF or scoring profiles.
- **Experimental:** promising but not yet standard.
- **Built:** implemented here and tested.
- **Measured:** a number from the deterministic fixtures in `tests/`.

## 1. The reproduced failure and what actually caused it

The request: *"What's EchoSpeak's latest updates? https://github.com/Ty0x7/EchoSpeak/pulls"*. Gemma 4B searched broadly, read a GitHub profile, cited it and said it couldn't access the pull requests.

The audit traced every stage. The failure wasn't one bug; it was five structural faults, each sufficient to cause it on a small model.

| # | Fault | Where | Evidence |
|---|---|---|---|
| 1 | **Queries were rewritten into different queries.** `normalize_provider_query` ran the old pipeline's sentence normaliser over queries the model had already written. It cut at every dot, so `python 3.14 release notes` became `14 release notes`, `node.js` became `js`, and `site:github.com` became `com`. It also rewrote intent: `who won the 2026 world cup final` became a "matches today kickoff times" query. Supplied URLs were deleted. | `web_search_providers.run_web_search` → `research.normalize_web_search_query` | **Measured:** 12 of 20 realistic queries changed in meaning |
| 2 | **Results were never ranked.** Hits kept provider arrival order, query variants were concatenated, and dedupe was exact-URL only (tracking parameters duplicated). `web_search(read=N)` opened the first N whatever they were. | `run_web_search`, `toolbox._search_and_read` | **Measured:** the first result answered the question in 1 of 8 labelled cases |
| 3 | **A supplied URL depended on the model noticing it.** Nothing read the user's link. A 4B model had to recognise it and call `safe_web_fetch`, and Gemma searched instead. | Chat loop | Reproduced |
| 4 | **No structured retrieval.** GitHub, PyPI and npm pages were scraped as HTML. A GitHub pulls page is mostly navigation, and the fetch result also carried every link on the page (hundreds), drowning a small model. | `safe_web_fetch` | **Source** |
| 5 | **"Loaded" was treated as "read".** There was no grading of empty, JavaScript-only, sign-in, blocked, 404 or off-topic pages. The 11.11.2 audit counted any URL that appeared anywhere in a tool output as "seen", including links inside pages. Source chips listed the first 8 *search results* under the answer as if they were support. | `safe_web_fetch`, `web_search`, `chat_run` | **Source** |

## 2. Research findings and decisions

| Finding | Status | Decision |
|---|---|---|
| OpenAI web search: the model chooses when to search; actions are search, open_page and find_in_page; citations are inline, visible and clickable, with a full `sources` list ([guide](https://developers.openai.com/api/docs/guides/tools-web-search)) | Documented | Keep the model in charge of *whether* and *what* to search. Make opening and verifying pages deterministic and visible. Chips come only from pages actually read |
| GitHub REST: `GET /repos/{owner}/{repo}/pulls` with `state`, `sort=updated`, `direction`, `per_page` ≤ 100; fields `number, title, state, html_url, merged_at, user, body, draft` ([docs](https://docs.github.com/en/rest/pulls/pulls)) | Documented | Read GitHub sources through the API. Anonymous access is rate-limited; an optional owner token goes only to api.github.com. Fall back to the page |
| Azure AI Search: BM25 as the first ranking stage, Reciprocal Rank Fusion to merge result lists, scoring profiles (field weights, freshness), a semantic cross-encoder reranker over the top 50 as a second stage ([overview](https://learn.microsoft.com/en-us/azure/search/search-relevance-overview)) | Production-proven | Field-weighted BM25 (title > URL > snippet), with IDF over the result set. RRF (k = 60) across query variants and providers. Scoring adjustments for authority, freshness and update intent. A cross-encoder rerank was **not** added: it needs a model per query, so it's listed as an opportunity |
| Tavily: `search_depth`, `time_range`, include/exclude domains, `published_date`, `raw_content`, relevance `score` ([API](https://docs.tavily.com/documentation/api-reference/endpoint/search)) | Documented | Providers stay pluggable; ranking is provider-independent so free DuckDuckGo results get the same quality treatment |
| Anthropic web search and web fetch tools: `max_uses`, `allowed_domains`, citations on fetched content (via SDK references) | Documented (secondary sources) | The same split as EchoSpeak: search for leads, fetch for evidence, a bounded number of uses (Chat's budget) |
| BrowseComp: persistence and creative re-searching matter for hard browsing questions | Page blocked (HTTP 403) when fetched; not used as evidence | Multihop research stays model-driven within Chat's budget; recovery is engineered (next candidate, specific failure reasons) rather than repeated blindly |
| Small models can't be relied on to notice URLs, pick authoritative sources or recover from predictable failures | Measured (the regression) | Deterministic code does those; the model interprets and synthesises |

## 3. The upgraded pipeline

```
user message
 ├─ supplied URLs → read before the first model call (LeanTurn.prefetch, the normal tool path)
 │                  └─ safe_web_fetch → source_router.resolve → official API? → structured document
 │                                                             └─ else page fetch → grading
 └─ model decides: answer, or web_search(query, read=N), or safe_web_fetch(url)
     web_search:
       prepare_query (faithful: whitespace, "search for", typo fixes; versions, operators, intent kept)
       providers (cascade, privacy-gated) → per-variant lists
       rank_hits: canonical dedupe → BM25F → RRF → official domain or repo, docs host, update pages, freshness
       read: only results that mention the question, best first; skip failed, blocked, sign-in, JavaScript-only,
             thin or off-topic pages and read the next candidate (2 spares); report each skip with its reason
     safe_web_fetch:
       API first (GitHub PRs, issues, releases, commits, repo, files; PyPI; npm; Reddit) → fall back to the page
       grade: status, relevance to the objective, published date and age; keep the 20 most relevant links
       chips only for usable, on-topic pages
 └─ ChatRun evidence: pages read (usable), records listed by an API, results seen, failures with reasons
     answer check: supported, seen only, unsupported (opened but useless) or unknown → one correction
```

### Why each piece exists

- **`agent/search_rank.py`** (new):
  - query preparation that keeps the query as written
  - BM25F, RRF, authority, update-intent and freshness scoring
  - canonical URLs
  - relevance of fetched text to the question

  No model and no dependencies; it runs in microseconds.
- **`agent/source_router.py`** (new):
  - URL classification and structured API adapters
  - page grading (`assess`) and published-date extraction

  It uses `safe_web_retrieval.fetch_public_bytes` for every request, so the SSRF pinning, redirect re-validation and Private/Offline blocking are the same as for any page. **Test:** privacy blocks API requests too.
- **`safe_web_fetch`** (`agent/tools.py`):
  - source-aware; grades every page
  - adds `quality=` and a plain-language `note=` the model reads
  - keeps links relevant to the objective
  - attaches source chips only for usable, on-topic pages
- **`web_search`:**
  - fuses and ranks multiple queries
  - no longer shows search results as source chips: results are leads, not support
- **`toolbox._search_and_read`:**
  - reads URLs in the query directly
  - only opens results that mention the question
  - recovers with spare candidates
  - reports exactly why each page wasn't used
- **Chat:**
  - **Prefetch:** `ChatRun.prefetch` runs before the first model call through the normal tool path (Chat guard, policy, privacy, approvals), recorded as step 1.
  - **Graded evidence:** a page is support only if it loaded, has content and covers the question (relevance ≥ 0.34). An API listing's records count as read.
  - **The answer check** adds `unsupported_links` for cited pages that couldn't support the claim.

## 4. Verification

All tests are deterministic. Real tools, the real loop and real ranking run; the network, search providers and model turns are fixtures. Only the tests for changed components were run.

| Required case | Test (`tests/test_research_engine.py` unless noted) | Result |
|---|---|---|
| The exact EchoSpeak PR request | `test_the_supplied_github_url_is_read_through_the_api_before_the_model_answers`: the PRs are in context before the model's first call; one API call; no scraping; both cited PRs supported | pass |
| Same question without a URL | `test_same_question_without_a_url_ranks_the_repository_over_the_profile_and_reads_it`: the repository or releases outrank the profile and the namesake shop, are read through the API, and the citation is supported | pass |
| Official documentation and release info | `test_official_package_release_info_comes_from_the_registry_api` (PyPI JSON) | pass |
| A source needing an API or another extraction path | the PyPI and GitHub adapters; `test_a_rate_limited_api_falls_back_to_reading_the_page_and_says_so` | pass |
| Irrelevant results rejected or reranked | `test_ranking_puts_an_answering_source_first_and_beats_provider_order`, `test_duplicates_collapse_and_off_topic_results_are_not_opened` | pass |
| Failed pages, empty content, provider outages | `test_failed_blocked_and_empty_pages_are_skipped_for_the_next_good_one`, `test_a_provider_outage_falls_through_to_the_next_provider`, `test_page_grading`, `test_a_failed_page_is_not_evidence_even_if_opened`, `test_privacy_mode_blocks_structured_apis_like_any_page` | pass |
| Conflicting sources and grounded citations | `test_conflicting_sources_are_both_evidence_and_an_off_topic_page_is_not_support` | pass |
| Small-model budget, follow-ups, source reuse | `test_small_model_budget_counts_the_prefetch_and_needs_no_url_noticing`; `tests/test_chat_paths.py` (earlier sources, budget) | pass |
| Queries reach providers as written | `test_model_written_queries_reach_the_provider_as_written` | pass |

**Quality criteria** (**Measured**, labelled fixture `tests/fixtures/search_relevance.py`, 8 cases with realistic failure shapes):

| Metric | Before (provider order) | After (ranked) |
|---|---|---|
| Top result answers the question | 12.5% (1 of 8) | **100%** (8 of 8) |
| nDCG@3 (graded 0, 1, 2) | 0.638 | **0.935** |
| Queries changed in meaning before reaching the provider (20 realistic) | 12 | **0** |
| Reproduced request: model calls before the PRs are in context | ≥ 2 and failed | **0** (prefetched) |
| Recovery with 2 bad pages among 4 results, `read=2` | read the 2 bad ones | read the 2 good ones; skipped with reasons |
| Unrelated page cited | accepted | **one correction** (`unsupported_links`) |

Also re-run and passing (existing suites for the changed components): `test_web_search_providers.py`, `test_live_steps.py` (fixtures updated to realistic results), `test_lean_harness.py`, `test_check_step.py`, `test_lean_tools.py`, `test_privacy.py`, `test_ask_and_recover.py`, `test_chat_mode.py`, `test_chat_paths.py`, `test_environments.py`, `test_visual_responses.py`.

## 5. Limits, costs and opportunities

- **Costs:**
  - Ranking and grading are pure Python (microseconds), with no model calls.
  - Prefetch adds one fetch per supplied URL (at most 3), replacing a model round trip.
  - Recovery reads at most 2 extra pages per search.
  - Anonymous GitHub API access is limited to 60 requests an hour per IP; a configured token raises it.
- **Limits:**
  - The labelled fixture is small (8 cases) and synthetic in shape. It measures regressions, not absolute web quality.
  - Relevance is lexical: a page that answers in other words can score low, and the model still reads the "barely mentions" note.
  - JavaScript-only sites are detected but not rendered (rendering means a browser, which is an Agents capability).
  - Contradiction detection is the model's job; the engine keeps both sources as evidence.
- **Opportunities:**
  - A small local cross-encoder or embedding rerank over the top 10, as a second stage (production-proven, but it costs a model).
  - Use Tavily or Brave `published_date` and `time_range` natively when those providers are configured.
  - More structured adapters: Wikipedia REST, arXiv, Stack Exchange, official changelog feeds.
  - A user-visible "why this source" line from `rank_reason`.
