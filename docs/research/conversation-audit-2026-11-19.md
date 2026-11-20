# Conversation audit: the owner's Pong and GTA-clone sessions (19 November 2026)

**Question.** What really happened when the owner asked Echo (qwen3.5:9b) to build Pong and then a GTA-style game,
which weaknesses do those sessions show, and which of them are the harness's to fix?

**Answer, short.**

- **The biggest failure was the harness, not the model.** On 16K, compaction ran after almost every step: about
  9K tokens of system prompt and tools left ~2K for the work, and each compaction *appended* another summary to the
  request. The model could only ever see one file. It read the same five files again and again (37 of the last
  run's 49 steps) until the work budget ended the turn mid-sentence.
- **Turns couldn't see each other's work.** The history carries replies only. A turn that wrote a Python package
  ended on "Now let me continue building…". The next turn ("why stopped? and continue") wrote a JavaScript game
  beside it without ever learning the package existed, and never said why it had stopped.
- **Opening the game looped.** An opener that hands a file to the running browser exits at once; the harness said
  "nothing running", and Echo launched it five times behind six approvals.
- All three are fixed in the harness, with regression tests. Measured live (§5), with honest results:
  - **Continuation works:** after "why did you stop? keep going", Echo looked at the existing project before writing
    in 4 of 4 runs, against 2 of 4.
  - **The context changes didn't help enough:** they lower the prompt and the summary pile-up, but the multi-file
    cases still fail in both arms (0 of 9 runs). About 2K tokens of working room is too little for this model to
    hold two modules side by side.
  - **One change hurt and was reverted:** counting re-reads against progress cut an LT1 run short.
  - **The eval had two faults, both fixed:** D8 couldn't run, and the runner reported "0 failed" for tests it never
    ran.
- Several other failures in these sessions were already fixed on the unmerged branch line, **but the backend the
  owner runs is about two days behind it** (§2).

Branch `conversation-audit-1119` (based on `context-recall-integration` 0f31370). Not pushed, merged or installed.

- `adbffb9`: the eval cases.
- `162f291`: the harness fixes.
- `b79293b`: two eval faults fixed.
- `a130f12`: the harness revised after the live run.

## 1. Method

Conversation Audit Loop (`docs/engineering-prompt.md`, loop 10) with loops 3, 4, 5 and 11.

- **Evidence, read-only.** The owner's runtime data (`~/.local/share/ai.echospeak.desktop/runtime`) was copied to a
  scratch folder and only the copies were read: `phase3/state.db` (with its WAL: executions, items, 366 tool runs),
  `query-streams.sqlite3` (every stream event of 88 runs), `lean/traces` (per-call timings and token counts),
  `threads.json`, and a copy of `~/Work/GTA_CLONE`. Nothing of the owner's was changed; the shared backend was not
  restarted.
- **Reconstruction.** A script rebuilt every thread as a transcript: the request, each tool call with its arguments
  and result, harness events (compactions, reminders, empty replies, approvals, steering) and the reply. A second
  script tabulated those signals for all 88 runs.
- **What the owner runs.** The backend serving these chats (`:38551`, started 18 November 18:05) runs from
  `~/Work/echospeak-five-model-harness`: `agent-first-sessions` 9c0c208 plus Codex's 29 uncommitted files (the same
  pass preserved as `6228e0d`). The TUI runs from `~/Work/echospeak-env-intelligence`. So none of the 64 commits on
  `context-recall-integration` were live in these sessions.

## 2. What happened, and what was claimed

Thread `cf8e79fb` (19 November, 16:08–16:38), Code environment, project root `/home/dev/Work`:

| Request | What Echo said | What the records show |
|---|---|---|
| "whats my name and whens the next oilers game" | The game time | The name was never answered (dropped sub-question) |
| "anything new with cyber leek" (after a GTA 6 leaks question) | Cyberpunk 2077 patch notes | A steer, "not cyberpunk cyberleek", arrived at step 3; the next search's first hit was the GTA 6 leaker Cyberleek; the model searched Cyberpunk again and answered about it |
| "create a pong game in /work … open it up so i can play it" | "Perfect! I've created a complete Pong game … and opened it in your browser" | 17 steps, 7.5 min. `index.html` rewritten whole three times (11,065 → 8,364 → 7,609 chars); `xdg-open` in the sandbox printed a bus error and exited 0; `chromium …` three times and `gio open` twice on this PC, six approvals; compaction after nearly every step; harness note: "Not verified — nothing checked the work" |
| "delete the pong game … make another folder called GTA CLONE" | Done | Correct (`rm -rf`, `mkdir GTA_CLONE`) |
| "in GTA_CLONE lets start development, first get research and then start coding" | "Great! Now let me continue building out the map system and input handler…" | Two searches, two pages, then a **Python** package: `src/core/engine.py`, `src/vehicle/car.py`, `src/game/player.py`; the reply hit the window (`finish: length` at 16,384), then ended on a promise: stop reason `no_answer`, recorded as success |
| "why stopped? and continue" | "I see the issue … Let me read the remaining files properly and get the full picture:" (failed) | Five **JavaScript** files at the folder root (`index.html`, `game.js`, `map_system.js`, `physics.js`, `input_handler.js`) via `cat > … << 'EOF'`; then steps 6–49 read the same five files over and over, 20 compactions, 10 refused repeats, an invented `read_output` ID; stopped by the work budget. The Python package was never mentioned. "Why stopped?" was never answered |

Earlier sessions with the same mechanisms: 18 November "summarize what codex and claude did" (59 steps, 10
compactions, folders listed twice, two invented output IDs in the first step, and worktrees named `demo-claude` /
`demo-codex` summarised as if they were the sessions); 17 November Pong (Echo said file writing was off and pasted
the code; the logs don't show whether writing was really off then).

**Already fixed on the target revision, not in the owner's backend:** a refused re-read of a just-edited file
(`21ea7f4`); "codex and claude sessions" now lists `agent_sessions` (`toolbox.list_suggested`); a wrong
`read_output` ID now gets this chat's real IDs (`tool_outputs._unknown`); the work budget grows with automatic
continuation (`21ea7f4`). Installing the integrated branch would bring those to the owner.

## 3. Root causes

| # | Weakness | Cause (layer) | Evidence |
|---|---|---|---|
| 1 | Re-reading the same files; compaction every 2 steps | **Harness, context.** ~9K of 16K is system prompt (16.7K chars) and 22 tool schemas (14K chars); the work budget is `0.8 × 16,384 − 2,048` ≈ 11K tokens. Each compaction **appended** a summary (17 of them, 15.5K chars, by call 72 of live LT1). Under pressure every older tool result was cut to its first 250 characters, so a file the model had read was gone | GTA trace: step 1 prompt 8,960 tokens, then 9.5–13.5K every step; 20 compactions = 112 s of 436 s model time; 37 of 49 steps re-reads |
| 2 | Re-reads counted as progress | **Harness.** Automatic continuation counts distinct calls; a re-read with another offset is "new". `_forget_reads` (correctly) re-allows reads after compaction, which removed the only brake | `_continue_past`; GTA steps 12–49 |
| 3 | The next turn didn't know what the last one built | **Harness, history.** `runtime._history` passes replies only; the reply said nothing about files or language | GTA: Python package, then a JavaScript game beside it |
| 4 | "why stopped? and continue" | **Harness.** The request became the work's objective and every goal reminder; nothing told the model why the last turn stopped | `work_state.objective` = "why stopped? and continue" |
| 5 | Opening the game five times | **Harness, environment.** `open_application` is Windows-only and not in Code; the sandbox has no desktop session (xdg-open exits 0 anyway); a launcher that hands off to a running browser was reported as "nothing running to check on" | Pong turn: 6 approvals |
| 6 | Dropped sub-question; steer ignored after one step | **Model** (the steer was applied and is in the goal; the next search followed it, the one after reverted) | Turns 1 and 4 |
| 7 | "Perfect! … complete" over unchecked code | **Model**, with the harness already labelling it "Not verified" | Pong and GTA turns |

## 4. Fixes (`162f291`)

**Context at 16K** (`agent/lean/loop.py`, new `agent/lean/read_outline.py`):

- Compaction **replaces** its summary, and writes the new one from the old one plus the steps since. One summary, not a pile.
- Compaction keeps a deterministic list of the files read in the request, each with its size and outline.
- An older `file_read` result that must shrink keeps its path, size, range read and **outline** (classes, functions
  and methods with line numbers; for HTML, the scripts it loads and element ids), not its first characters. On the real
  GTA files: 6,142 → 343 chars for `game.js`, and the outline still says where `update()` starts.
- The third *whole* read of an unchanged file (size and mtime) in one request gets one note: the window holds about
  one file, read the lines you need (offset/limit), make the change, check it. Ranged reads never count.
  - **Changed by `a130f12`.** The first version counted ranged reads and fired on every re-read. It also didn't count
    re-reads as progress for automatic continuation; live runs showed both were wrong (§5).

**Across turns** (new `agent/lean/work_record.py`, `runtime._history`, `_goal`):

- Each turn's reply in the history carries a short record when the turn changed the project or stopped early, built
  from its tool runs: "[EchoSpeak's record of this turn: wrote GTA_CLONE/src/core/engine.py, …; ran `mkdir -p
  GTA_CLONE/{src/…}`; it ended without a final answer, in the middle of the work]". Owner only; deterministic, so the
  cached prefix holds. A look-up that hit its step limit but answered gets no record.
- A request that only asks to carry on ("continue", "keep going", "why stopped? and continue", ≤ 80 characters)
  inherits the newest real objective as the turn's goal and the work's objective, and its request carries a note:
  what is being continued, how the last turn ended, "if asked why it stopped, say so", and build on what exists in the
  same language and structure.

**Opening things** (`agent/lean/terminal.py`): an opener (`xdg-open`, `gio open`, a browser, an image or PDF viewer)
that exits 0 with no output counts as opened: "Don't launch it again". One run in the sandbox that reports no
display or session bus says nothing opened and to run it once with `where="host"`. Approvals are unchanged.

**Tests:** `tests/test_real_conversation_audit.py`.

- 19 tests at `162f291`, written failing first for the context, history and continuation fixes (7 and 4 failures
  before the code); 20 at `a130f12`.
- Full backend suite at `162f291`: 1,938 passed, 22 failed. The same 22 fail on the base revision (`0f31370`), none
  new.
- At `a130f12`: the related test files, 147 passed.

## 5. Verification

**New cases** (`scripts/harness_eval.py`, `adbffb9`), generalised from the sessions rather than copied from them:

- **PJ1 / PJH1 (held-out):** a five-module project (an arcade game; a shop checkout) whose three bugs span modules: a
  renamed method, a key or action-name mismatch, a wrong data shape. About 400 and 250 lines of source, more than a 16K
  window holds beside the prompt. Fixtures checked: 3/3 tests fail as shipped, 3/3 pass with the reference fixes.
- **PC1 / PCH1 (held-out):** a small CLI app built over three turns, the first cut short (`LEAN_MAX_ITERATIONS=8`,
  automatic continuation off), then "why did you stop? keep going" and "continue" (held-out: "ok carry on", "finish
  it"). The judge runs the app's command line itself in a copy (`cli_checks`) and fails a project that grew files in
  another language (`allowed_extensions`). Both judge checks were tested on a reference app, a broken one, a missing
  package and a stray `.js` file.
- PJH1 and PCH1 were written before any run and not used to shape a fix.

**Live baseline** (qwen3.5:9b, 16K, `adbffb9` = the target revision plus the cases; isolated backend and recorder;
judged by `harness_judge.py`). Run: `evaluations/2026-11-19-conversation-audit/20261119-174800-audit-pj-base`.

| Case | Score | Tests (independent) | Compactions | File reads (files) | Refused repeats | Calls · time |
|---|---|---|---|---|---|---|
| PJ1 r1 | 5.81 | 3 failed | 17 | 39 (7) | 2 | 56 · 348 s |
| PJ1 r2 | 5.01 | 2 failed, 1 passed | 15 | 25 (9) | 3 | 56 · 288 s |
| PJH1 r1 (held-out) | 5.47 | 3 failed | 20 | 65 (13) | 10 | 65 · 423 s |

The GTA session's pattern reproduced on code the model had never seen: 0 of 3 runs fixed the project, compaction
came every third call, and the recorded prompts show **20 stacked summaries (18.1K characters, ~4.5K tokens) in one
request** and **479 tool results cut to their first 250 characters**: the two mechanisms fix 1 removes.

A sixth baseline run, made later with a third PJ1 sample, agreed: PJ1 3.45 and PJH1 5.46, 20 and 18 compactions.

**Live after-arm, round 1** (`162f291`, the owner's go-ahead, 18:39–19:48). All of today's runs are in the evidence
folder (`fixed-*`, `baseline-*`).

| Case | Baseline | Fixes (`162f291`) | What changed |
|---|---|---|---|
| PJ1 (×3 / ×2) | mean 4.76, 0/3 | mean 4.32, 0/2 | Peak prompt 12.3–13.6K → 11.1–11.9K (one summary). Compactions unchanged (15–20 → 17–18). The re-read note fired 26–40 times per run |
| PJH1 (×2 / ×2) | mean 5.47, 0/2 | mean 4.81, 0/2 | Same |
| PC1 (×2 / ×2) | mean 4.42, 0/2 | mean 5.00, 0/2 | In turn 2 ("why did you stop? keep going"), Echo **looked at the existing files before writing in 4 of 4 runs**, against 2 of 4. One reply said why it had stopped ("I hit the step limit before running the final test check"); none did before |
| PCH1 (×2 / ×2) | mean 4.88, 0/2 | mean 4.95, 0/2 | (counted with PC1 above) |
| LT1 (×2 / ×2, `LEAN_MAX_ITERATIONS=12`; baseline = `lt1-runner`, same harness code) | mean 7.19, 1/2 | mean 7.29, 1/2 | Compactions 18 and 2 → 5 and 8. One fixed run stopped at step 24 with 5/10 tests passing |
| D6 (×2) | 9.49, 2/2 | 9.45, 2/2 | No change |
| D8 (×2) | 8.97, 2/2 | 9.61, 2/2 | One baseline run took 50 calls and 15 compactions; the fixed ones took 4–5 calls, none. A single outlier |

**What round 1 showed, and what changed because of it:**

1. **The re-read note contradicted itself.** It asked for ranged reads (offset/limit) and then counted them. On PJ1
   it fired on nearly every read (`world.py` 15 times).
   - **Revised (`a130f12`):** only whole reads of an unchanged file count, and the note comes once.
2. **"Re-reads aren't progress" hurt.** LT1 r2's continuation saw a stretch of reads as no progress and stopped at
   step 24 with 5/10 tests; the baseline run that kept going reached 9/10.
   - **Reverted (`a130f12`).**
3. **The eval itself had two faults.** Both are fixed in `b79293b`, in both arms:
   - **D8 couldn't run.** Since `e11a1f5` a second `_FILLER` shadowed its report text, so every D8 crashed on setup.
   - **The runner misled the model.** The plain `run_tests.py` printed "0 failed" for tests it never ran (`unittest`
     classes, tests outside `tests/`). In two PC1 runs the model reported that as "all tests pass", and the judge
     correctly failed it. The runner now runs `unittest` classes, reports how many tests ran, and fails with "no tests
     found".
4. **Why PJ didn't improve.** Step 4 read all five modules at once (~14K characters). Prompts then sat at the ~11K
   budget, so compaction came after nearly every step even for 20-line reads. The one bug fixed (`step` → `update`)
   came from an error message. The `move_up` vs `up` mismatch needs `controls.py` and `entities.py` side by side,
   which ~2K tokens of working room never allowed.

**Round 2** (`a130f12`) was stopped by Claude Code on low memory again at ~20:05 (available memory 1.1 GB; unloading
the model restored 8.6 GB). Two runs finished:

- PJ1 5.67 and PJH1 6.21, both 0/1.
- The note came 5–6 times instead of 26–40, as designed.
- Compactions still 16–19.

The round-2 LT1 and PC runs, with the corrected runner, didn't run.

**Verdict:**

| Change | Decision | Why |
|---|---|---|
| One merged compaction summary | Keep | Mechanism verified: peak prompt down ~1.5K, LT1 compactions fewer. No regression in D6/D8/LT1 |
| Outlines for trimmed reads; files list in compaction | Keep | Unit-verified. Harmless live, but not enough on its own to move PJ |
| Re-read note | Keep, revised | Fires as designed after `a130f12` |
| Re-reads not counted as progress | Reverted | Hurt LT1 |
| Work record and continuation note | Keep | The one behavioural gain measured live: turn 2 inspects before writing, 4/4 against 2/4 |
| Launcher note | Keep | Unit-tested against the real failure. Live check needs a desktop session (the eval has none) |

**Nothing here made qwen3.5:9b succeed where it failed before:** 0 of 9 PJ runs and 0 of 8 PC runs succeeded across
both arms.

## 6. Model limits and remaining harness weaknesses

**Model (qwen3.5:9b), seen in these sessions:**

- Drops the second half of a two-part question ("whats my name and …").
- Follows a steer for one step, then drifts back to its first reading.
- Declares unchecked work "Perfect! … complete"; the harness already labels it "Not verified".
- Game code that wouldn't run well. The GTA map loops over 2048 × 2048 tiles every frame, and `ctx` is undefined
  in `map_system.js`.

**Harness, still open:**

1. **Fixed overhead at 16K.** About 9K of 16K tokens are always taken. The system prompt is 16.7K chars ("How you
   work" alone is 4.8K; the owner's personality sections 3.5K), and the 22 tool schemas are 14K. Shrinking that is
   the next lever for every long task, but it changes all behaviour, so it needs its own A/B.
2. **Written web code is never checked.** `code_check` checks code in replies; files the agent writes get no
   `node --check`. A cheap syntax check would have caught the GTA files.
3. **No way to open a file on Linux except the terminal.** `open_application` is Windows-only and not in Code. The
   launcher note makes the terminal route work in one call; a native opener would need no approval-heavy host
   commands.
4. **Research turns often end at their step limit.** 5 of 11 research runs stopped there, answering from what they
   had found.
5. **The sandbox Python has no pytest** (known, context-strategy §10.5).
6. **Deployment gap.** The owner's backend lacks the 64 commits of `context-recall-integration`, including four
   fixes for failures seen in these very sessions (§2). That's the owner's decision.

## 7. Next priorities

1. **Working room at 16K is the bottleneck for multi-file work**, not compaction's form. Run an A/B on a leaner
   system prompt and tool set for Code on a local 16K model, aiming for ~4K tokens of room instead of ~2K. Measure
   on PJ1/PJH1 and LT1.
2. **Finish round 2:** LT1 and PC ×2 on `a130f12`, with the corrected runner on both arms. Run it with the browser
   closed, or with `CLAUDE_CODE_DISABLE_BG_SHELL_PRESSURE_REAP=1`. The model plus llama-server's cache takes ~8.5 GB,
   and Claude Code stopped the evaluation twice.
3. **Decide the integration:** `context-recall-integration` plus this branch, then install, so the owner's Echo gets
   the fixes.
4. **A syntax check for written JS, HTML and Python files** on the "Not verified" path.

## 8. Reproduce

```bash
cd apps/backend
# multi-file cases (baseline tree = adbffb9, fixed = 162f291)
python scripts/harness_eval.py --model qwen3.5:9b --set audit-pj --repeat 2 --label audit-pj
# continuation cases: the first turn is cut short on purpose
python scripts/harness_eval.py --model qwen3.5:9b --set audit-pc --repeat 2 --label audit-pc \
  --env LEAN_MAX_ITERATIONS=8 --env LEAN_AUTO_CONTINUE_STRETCHES=1
python scripts/harness_judge.py data/evals/harness/<run>
python -m pytest -q tests/test_real_conversation_audit.py
```
