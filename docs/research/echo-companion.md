# Echo Companion: requirements against implementation

Campaign issue #210. Status as of 2026-11-26, round 2.

**Branch stack**, in merge order:

1. #209 `voice/208-local-tts-speech-queue`
2. #211 `companion/agent-events`
3. #212 `companion/commentary-knowledge`
4. #214 `companion/desktop-window`
5. `companion/round2`

**Fixes outside the stack:**

- #215 `fix/213-endpoint-isolation`
- Issues found during the campaign: #213 (fixed by #215), #216, #217.

**Evidence ladder:** V1 is tool success, V2 is checked after the last change, V3 is two independent kinds of check, and V4 is the owner's acceptance.

| § | Requirement (original brief) | Implementation | Evidence | Status |
|---|---|---|---|---|
| 2 | Execution and companion layers stay independent; reuse infrastructure | `agent/companion/*` observes, comments and stores. It reuses `session_overview`, `AgentMemory`, the Obsidian projection, the voice runtime and the lean verification ledger. Agents never wait on it. | Companion failure tests; native publish runs on a daemon queue | Done (V2) |
| 3 | Know which agent the owner is working with; concurrent sessions; uncertainty | Most recently prompted session = interacting. Prompts within 30 s of each other set `uncertain`. States: working / your_turn / needs_permission / quiet / idle / ended. | Live: 2 concurrent Claude sessions plus `--continue`; this Claude session shown as "you're here" | Done (V3) |
| 3 | Workenv/tmux/Hyprland awareness | Not used: tmux makes the window ambiguous, so prompt recency is used instead | — | Partial (by design) |
| 4 | Official mechanisms only; compliance | Claude Code `hooks`, Codex `hooks.json` (notify opt-in) and native in-process events. No credentials, traffic interception or private endpoints. The hook prints nothing. | Hook payloads captured from Claude Code 2.1.332; Codex docs reviewed | Claude Code done (V4: owner authorized and installed). Codex: config installed, **live test blocked by usage limit until 2026-11-28** |
| 5 | Understand tasks, stages, discoveries, blockers | `digest.py`: kind, results, failures, open items, proposals, discoveries, decisions, PR/issue/commit refs, pytest/unittest counts. Section-aware, negation guard, one item per bullet. | 48 companion tests; real reports | Done (V2) |
| 6 | Integrate with the 13 loops | Native `run_outcome` → `stage_completed`. `auto_test_fix.py` posts decision/stop/halt/awaiting-fix. Hook reports are handled the same way. | `test_auto_test_fix` 42 passed; native tests | Done (V2); no live loop-13 run |
| 6 | A finished turn is not a finished objective | Only *reports* are announced. Echo's measured status (verified/checks_pass) is separate from "by its account". | Acceptance: Echo `checks_pass` carried on the event; fabricated claim (#217) hedged | Done (V3) |
| 7 | Knowledge transfer with provenance, project isolation, no duplicates | One `authorize()` decision gates archive and memory. **Default `project_only`**; broader scope only through the explicit `project_or_account` setting. `semantic_key` / `source_item_id` dedup. Type `project`, never `instruction`. Archives are 0600. | Recall test below; no-project control can't see it | Done (V3) |
| 7 | "What did Claude accomplish…", "Continue from the report…" | Echo's normal memory recall | Live (qwen3.5-9b): in-project answer cites facts and "haven't independently verified"; no-project control finds nothing; Echo continued the work and tests pass (6/6, rerun independently) | Done (V3) |
| 7 | Imported text untrusted; no SOUL/permission changes | Instruction-injection phrases dropped. Memory type `project`. The SOUL is a separate file that agents can't write. No permission changes (#207 untouched). | Tests | Done (V2) |
| 8 | Own customizable SOUL; grounded wit; efficient local processing | `DATA_DIR/companion/SOUL.md` (phrases, personality, never-list), editable in Settings. Templates by default. Optional local-model wording behind a fact guard (numbers, claims, attribution, agent name). | Live: 9/12 rewrites accepted, median 974 ms; guard caught dropped attribution | Done (V3); local model stays opt-in (one causal-overreach case seen) |
| 9 | Echo's square identity, upper-right, animations, never steals focus | GTK4 + gtk4-layer-shell overlay, a port of `EchoFace`. Modes: idle, listening, thinking, working, speaking, error, celebrating, confused, discovery, waiting, sleeping. No white glow (owner). Keyboard NONE; ON_DEMAND only while the menu is open. | Hyprland 0.56.2 screenshots; focus unchanged in every run | Done (V3); owner's own click is V4 |
| 9 | Speaking synchronized with audio | 50 Hz amplitude envelopes per chunk; the celebration waits for `speech.finished` | Frame sequences | Done (V2) |
| 10 | Repair TTS; Kokoro/Piper; CPU; shared queue; cancel; device | `kokoro-local` and Piper-in-venv; platform default; `speech_output` queue (priority, dedup, mute, interrupt, stale drop, pipelined) | Clean sink-monitor capture; owner heard it (round 1); packaged sidecar synthesizes | Done (V3) |
| 10 | Echo speaks its own responses (desktop and terminal) | Desktop read-aloud → shared speaker (`systemSpeaker.ts`); terminal `/voice on|off|say|stop` | tsc, vitest, 4310 terminal tests | Done (V2); not yet heard from the desktop app (not installed) |
| 10 | Packaging | kokoro-onnx plus 5 deps hash-pinned in the lock; PyInstaller collects espeak-ng; in-app download (privacy-gated) | `--venv-only` with `--require-hashes` installs; frozen sidecar returns Kokoro WAV | Done (V3) |
| 11 | Voice and companion settings; per-agent controls; persistence | Settings › Voice (Kokoro, speed, volume, device, test) and Settings › Echo Companion (all controls, hooks, folders, SOUL) | Headless screenshots against the live lab backend | Done (V2) |
| 11 | Autostart | XDG autostart toggle | Unit test | Done (V2); not enabled on the owner's desktop |
| 12 | Click to ask; mute, pause, move, hide | Popover menu; `/companion/ask` topics; SIGUSR1/SIGUSR2 summon | Menu screenshot on Hyprland; ask spoken | Done (V3); physical click is V4 |
| 13 | Asynchronous, milestone-only, cooldowns, resumable | Worker threads, cooldowns, quiet hours, catch-up of unhandled reports (not spoken) | Tests | Done (V2) |
| 14 | Privacy, scope control, no new privileges | Folder roots (home refused), per-agent switches, prompt capture switch, masking, 0700 spool, local-only model | Tests; endpoint isolation (#215) | Done (V2) |
| 15 | Low idle cost | Hidden companion: **0.0 % CPU**. Visible resting: 2.4 % CPU, 109 MB (Cairo). Backend idle 0 %. Hook ~21 ms. | Measured | Done (V2) |
| 17 | Integrated acceptance | Echo native task (`checks_pass`) → Claude Code real task → report spoken and stored in the project → Echo recall and continuation | Live lab run, 2026-11-25 | Done in the lab (V3); **install and owner acceptance pending merge approval** |

## Known limits

- Codex has not been tested live; its subscription is at its usage limit until 2026-11-28 17:53. No paid fallback was used.
- The installed app (701b786) has none of this. The hooks write to the spool, and the lab backend consumes it until an install.
- Hyprland focus is not used. Interacting is inferred from prompts.
- Local-model wording can imply causality that the fact guard does not catch. Templates stay the default.
- The digest is heuristic. Counts are per bullet and wording is hedged, but it can still misfile a line.
