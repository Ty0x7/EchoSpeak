# Codebase cleanup report

Date: 2026-10-02. Branch `echospeak-8.0`.

Rule for this pass: **behaviour stays the same.** Every removal was found by a
tool and then confirmed with a repo-wide reference search (`git grep`), and the
checks below were run before and after each commit.

| Check | Before | After |
| --- | --- | --- |
| Backend `pytest tests --continue-on-collection-errors` | 42 failed / 683 passed / 1 collection error | 42 failed / 683 passed / 1 collection error (same tests) |
| Backend sidecar `--self-check` | ok | ok |
| Web `tsc --noEmit` | clean | clean |
| Web `vitest` | 66 / 67 (1 stale test) | 63 / 63 (duplicates gone, stale test updated) |
| Web `vite build` | ok | ok |
| Desktop contract tests | 10 / 11 (1 stale test) | 11 / 11 |

The 42 backend failures and the collection error are pre-existing, almost all
in tests for the legacy runtime (see "Left alone").

## Tools used

Run on demand with `uvx` / `npx`. Nothing was added to the project's dependencies.

- **knip 5** (web): unused files, exports and dependencies.
- **vulture** (backend, ≥ 80% confidence): unused code and unreachable code.
- **ruff** (backend, `F401 F811 F841 ERA001 T201`): unused imports and leftovers.
- **An import-graph script** (backend): modules no entry point or test can reach.
  vulture cannot see a file that is dead as a whole, so this filled that gap.
- **md5** (assets): files that are byte-for-byte copies of each other.

## Removed

| What | Why it was dead | Commit |
| --- | --- | --- |
| 25 compiled `.js` / `.d.ts` files next to their `.ts` / `.tsx` sources in `apps/web/src` (`marketing.js`, `features/research/*.js`, `components/*.js`, `*.d.ts` …) | `vite.config.ts` resolves `.tsx`/`.ts` first, so none of them ran. Two `.test.js` copies made vitest run the research tests twice. They also misled knip into flagging the real sources as unused | `Web cleanup: remove stale compiled …` |
| `apps/web/sys` | An ImageMagick PostScript dump, almost certainly from a mistyped `import` shell command | same |
| `components/TodoPanel.tsx`, `components/WorkspaceExplorer.tsx`, `features/research/ResearchPanel.tsx` (about 1,000 lines) | Not imported anywhere. Only old docs mention them | `Web cleanup: remove unused components …` |
| `getToolCategory`, `getToolIcon`, `getToolDisplayDetails`, `TOOL_MAP` (`echoAnimationUtils.ts`); `desktopWorkspaceLabel` (`workspaceState.ts`); `sanitizeUserFacingText` (`chatPresentation.ts`) | No callers anywhere, including inside their own files | same |
| The duplicate `export default OperationalStateCard` | Only the named export is imported | same |
| The `gsap` dependency | Never imported | same |
| `apps/backend/io_module/personaplex_client.py` (413 lines) | Imported by nothing. The PersonaPlex *settings* entry stays, because the settings UI shows it as a disabled option | `Backend cleanup: remove two unreferenced modules …` |
| `apps/backend/agent/proactive.py` (360 lines) | Its own docstring says it was a retired compatibility module "while historical references are migrated". None remain | same |
| 84 unused imports across 30 backend files | ruff F401, excluding tests and `__init__.py` re-exports. Checked against every attribute the tests monkeypatch (`server.get_state_store` and others) | same |
| An unreachable `as_dict()` method in `agent/active_work.py` | It sat after the `return` inside a module-level function | `Backend: remove unreachable method …` |
| `json.dumps(...) if False else f"..."` in `agent/core.py` | The condition is always false; only the `else` branch was ever used | same |
| A 410-line block comment in `apps/web/src/index.tsx` holding the old browser-owned voice code (kept "as migration evidence") | It never ran. Voice capture and playback live in `voiceTransport.ts`. Found by a follow-up scan for large block comments | `Web cleanup: drop 410 lines of commented-out …` |
| `assets/86a2bb89-…_removalai_preview.png` | Byte-identical to `apps/web/public/logo.png`, and referenced nowhere | `Repo cleanup: …` |
| `test/index.html` | A "Hello World" page referenced nowhere | same |
| `apps/web/public/REAL1.png` (564 KB) | An old v0.2.0 terminal screenshot, referenced nowhere, yet shipped in every web and desktop build | same |

## Refactored (same behaviour)

| What | Change | Commit |
| --- | --- | --- |
| Design tokens in `apps/web/src/lean/lean.css` | The `--es-*` tokens were defined three times: `.echo-root`, the mention menu and the modal. The menu and modal needed their own copies because they render outside `.echo-root`. Now there's one `:root` block, with values unchanged | `Consolidate: one design-token block …` |
| Tool-call repeat detection in `agent/lean/loop.py` | The signature hash was written out inline and also in `_sig()`. Both places now call `_sig()` | same |
| Stale tests | `studioNavigation.test.ts` expected the section order from before the settings redesign. The desktop contract test looked for the old `aria-label="Ask Echo anything"` | `Web cleanup: …`, `Fix unstyled desktop app …` |

## Left alone on purpose

| What | Why |
| --- | --- |
| **Legacy gated runtime**: `agent/core.py` (19.7k lines), `agent/semantic_runtime.py`, `agent/turn_understanding.py`, `agent/model_control_plane.py`, `agent/task_runs.py` | Still reachable. The lean runtime is the default, but `ECHOSPEAK_LEAN_RUNTIME=0`, some API routes and most of the 42 failing tests go through it. Removing it is core architecture, so it's a proposal (below), not a cleanup |
| `apps/web/src/index.tsx` (12.9k lines) | Works, but too big to review safely. Splitting it touches every screen, so it's a proposal |
| Placeholder modules `io_module/stt_engine.py`, `pocket_tts_engine.py`, `wake_listener.py`, `voice.py` | Each one raises a clear "this was removed" error. `tests/test_echospeak.py` and `scripts/test_v660_cleanup.sh` check that they exist and say so |
| PersonaPlex config (`config.py`, `settings_catalog.py`, `voice_runtime.py`) | Shown in Settings as a disabled option. Settings is out of scope this round |
| Exports knip flags that are still used inside their own file (`describeLive`, `AvatarStack`, `LEAN_EVENT_TYPES`, `mergeDeep`, `dedupeAllowlist`, `LocalVoicePlayback`, `DESKTOP_PROTOCOL_VERSION`) and 19 unused exported *types* | Removing them would change nothing at runtime, and some are handy for tests |
| Unused parameters vulture flags (`manage_background_services`, `force`, `on_wake`, `on_transcript` …) | They're part of public signatures that callers or tests pass |
| 106 `print()` calls (ruff T201) | All in `scripts/` command-line tools, where printing is the output |
| Older design docs in `docs/` and the root `ARCHITECTURE.md`, `AUDIT.md`, `ROADMAP.md`, `CHANGES.md` | Historical records. `docs/ARCHITECTURE.md` (this round) is now the current description, and links to them are not rewritten |
| `assets/` brand images (`logo.png`, `logo (1).png`, `echospeak-logo.png`, `echospeak-real-logo.png`, `echospeak-ui.png`) | Unreferenced, but they're source artwork, not build output. Your call |
| `apps/web/src/index.tsx.safety-copy-*`, `index.tsx.safetycopy` | Untracked and git-ignored, so they're your local backups |
| `apps/tui`, `apps/onboard-tui` | Separate apps on separate stacks (Go, Ink). Not part of the desktop/web build, so not audited this round |

## Proposals (too large for a cleanup)

1. **Retire the legacy runtime.** Move the remaining routes and channels onto
   `run_lean_text`, delete the modules listed above, and delete or rewrite
   their tests. This would cut roughly 25k backend lines and most of the 42
   failing tests. **L**.
2. **Split `index.tsx`** into `chat/`, `composer/`, `sidebar/` and `history/`
   modules behind the same props. Do it after (1), when the legacy activity UI
   can go too. **M–L**.
3. **Python dependency audit.** Several heavy packages (`langchain_openai`, which
   pulls in `transformers` and `torch`) are only needed by legacy paths. A
   dependency audit after (1) would shrink the sidecar and speed up the first
   import. **M**.
