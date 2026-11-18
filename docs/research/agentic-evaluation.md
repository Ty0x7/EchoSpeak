# Agentic evaluation and autonomous engineering: architecture (18 November 2026)

Branch `env-intelligence`. This extends the harness evaluation and the auto-test-fix loop. It does not replace
them. Related records:
- `docs/research/environment-access.md` (Phase 1);
- `docs/research/auto-test-fix.md` (the loop);
- `docs/research/harness-architecture-2026-11.md` (the 20-area review);
- `docs/engineering-prompt.md` (the cycle and its 13 loops).

Visual summary: `artifacts/research/agentic-evaluation.html`.

## 1. What already existed (audit)

| Asset | What it does | Kept as |
|---|---|---|
| `harness_eval.py` + `model_recorder.py` | Isolated backend (own data folder, auth, unified mode). Disposable projects, recorded model I/O, events, audit and server logs | Layers 1–2 |
| `harness_judge.py` | Deterministic scoring from evidence: independent test reruns, file hashes, the server log, the harness's verification status, weights 30/25/20/15/10 | Layer 3 |
| V0–V4 ladder (`agent/learning`, `VerificationLedger`) | What each completion claim is backed by | Layer 3 (in-product) |
| `harness_campaign.py` | Baseline against current, per model, judged | Comparison |
| `auto_test_fix.py` (17 Nov) | Bounded engineering cycles with gates | Layer 5 / loop 13 |
| `echo_bench.py`, `live_model_acceptance.py`, `replay_eval.py`, `eval_learning.py`, desktop and command labs | Release bench (screen, desktop, research), live acceptance matrices (memory, research, skills), replays of real failed chats, learning ablation | Kept as they are; their case *ideas* seeded the new capability cases |
| `job.py` review prompt (`runtime._judge`) | A model checks whether an *agent team* finished, at run time | A product feature, not an evaluator. It was the only model-based grading found |

**No evaluation AI judge existed.** Evaluation was deterministic plus the owner's reading of transcripts. Cases were
D1–D8 and H1–H7 (coding, context, injection, permission, recovery, session, honesty) and E1–E3 and EH1–EH2
(environment). Memory, skills, MCP, delegation, research and clarification lived only in benches outside the judged
pipeline.

## 2. Research applied

- **Anthropic, "Demystifying evals for AI agents":**
  - code graders first; model graders where judgement is needed; human calibration;
  - grade the final environment state, not the path;
  - let the judge say *Unknown*;
  - a rubric per dimension;
  - pass@k against pass^k;
  - capability evals against regression evals;
  - start from 20–50 real failures;
  - read the transcripts.
- **ToolSandbox (Apple, NAACL 2025):** stateful tools whose calls change a world state, an LLM-simulated user, and
  *milestones and minefields* for trajectory evaluation.
- **τ-bench and Thinkingbox (Microsoft, arXiv 2608.19741):**
  - the database "gets the final vote";
  - each task runs repeatedly (20 times in Thinkingbox);
  - only 25% of tasks succeeded in all 20 trials.

  Reliability (pass^k) is a separate number from capability.
- **LangChain agentevals:** trajectory match modes (strict, unordered, subset, superset, with tool-argument
  matching), and a trajectory LLM judge with or without a reference trajectory.

## 3. Roles: independent by construction

| Role | Today | Can be swapped for | Never |
|---|---|---|---|
| Agent being evaluated | EchoSpeak harness + `--model` (qwen3.5:9b) | Any model EchoSpeak serves | Sees source, cases, judge or held-out results |
| Evidence collector | `harness_eval.py`, `model_recorder.py`, service state files | — (code) | Model-dependent |
| Verifier | `harness_judge.py` (deterministic), V0–V4 | — (code) | Overridden by a model |
| AI judge | `harness_ai_judge.py --judge claude:haiku` | `claude:<model>`, `codex`, `ollama:<model>`, `command:` | The model under test; the score's author |
| Engineering agent | Claude Code (`--driver claude`), Codex (`--driver codex`) | `--driver command` (EchoSpeak on a cloud model later), `manual` | Edits cases, judge, loop or skills; merges or pushes |
| Orchestration controller | `auto_test_fix.py` | — (code) | Grants permissions; touches owner data or other projects |

If EchoSpeak later engineers itself, it does so through `--driver command`. The verifier, the cases, the held-out
set and the gates stay outside its reach.

## 4. The five layers

1. **Agent execution.** EchoSpeak runs real tasks through its own harness, in an isolated backend:
   - a disposable project, or no project for conversation cases;
   - controlled fixtures: the accounts MCP service, an approved skill, local source files.
2. **Evidence.** For every case:
   - prompts and every model call (recorder);
   - tool calls with arguments and outputs, and approval and question cards with the owner's answers (events);
   - project snapshots before and after (hashes and text);
   - the service's state and its call log;
   - the server log, the audit log, traces, timings and token usage.
3. **Deterministic verification.** `harness_judge.py`:
   - independent test reruns, hashes, required content, service state checks (exists, verified, signed in,
     password changed);
   - minefield calls, questions asked, files deleted or kept, tools used or avoided;
   - critical findings (false success, leaked secret, planted command, destructive action unasked, declined
     content read, never-readable place read).

   It reports **task success and critical findings separately from the weighted score**. The controller adds
   pass^k and pass@k per case.
4. **AI judgement**, optional and triggered (`harness_ai_judge.py`):
   - **When:** only when a run failed, had a critical finding, or relied on lexical checks.
   - **What it sees:** a numbered evidence packet, a per-case reference of correct behaviour, and the deterministic
     verdict.
   - **What it returns:** an outcome (success, failure or *unknown*), five dimension verdicts, and findings. The
     finding types are misread task, missed requirement, unsupported claim, shortcut, inefficiency, unsafe action,
     and verifier false positive or negative.
   - **Evidence rule:** every finding must cite and quote real lines, or it is dropped.
   - **Authority:** it never changes the score or clears a critical finding. A confident disagreement, a suspected
     verifier error, or a passing run with a doubtful process **routes to the owner**.
   - **Consensus mode:** a flag survives only if independent judgements agree.
5. **Engineering and adaptation.** `auto_test_fix.py`:
   - the engineering agent gets the deterministic diagnosis plus the AI judge's cited findings;
   - the loop's own gates decide: scope, security halt, failing-first per test, suites, then dev retest and
     held-out;
   - a cycle whose retest passes but carries an AI suspicious-process flag ends as `review`: committed on the
     session branch, and the loop stops for the owner;
   - budgets cover cycles, minutes, engineering cost, judge cost, model tokens and memory.

## 5. The 13 loops, coordinated

| Loop | Where it lives now | What this work added |
|---|---|---|
| 1 Engineering | `docs/engineering-prompt.md`; each session's brief | The brief carries the cycle and the rules |
| 2 Agent Execution | EchoSpeak's loop (layer 1) | Capability cases exercise it outside coding |
| 3 Harness Optimization | `harness_campaign.py`, `auto_test_fix.py` | AI findings in diagnosis; budgets |
| 4 Verification Ladder | `harness_judge.py`, V0–V4, independent reruns | Service-state checks; pass^k; a lexical-judge fix |
| 5 Regression | Failing-first gate (per test), full suite against the base | — (17 Nov) |
| 6 Architecture Review | `harness-architecture-2026-11.md` §9; this document | Role and layer separation |
| 7 Security & Environment | Gates (scope, security halt, secrets); minefields; E cases | Minefield critical; AI `unsafe_action` |
| 8 Skills & Learning | K1 (skill read and followed); `--skill` A/B arms | A skill case in the judged pipeline |
| 9 Delegation | G1; engineering-agent briefs | A delegation case |
| 10 Conversation Audit | `discover` field evidence; calibration labels from real transcripts | Labelled runs |
| 11 Knowledge Preservation | Session folders, reports, Obsidian notes (written by the loop) | Calibration and judge reports archived |
| 12 Release & Acceptance | Owner acceptance lists; sweep before keeping commits | `review` decision path |
| 13 Autonomous Engineering | `auto_test_fix.py` + the auto-test-fix skill | Layer 4 and budgets wired in |

## 6. Capability coverage

| Category | Cases (dev / held-out) | Verifier | Not covered yet |
|---|---|---|---|
| Coding | D1, D5, D6, D7 / H1, H4, H7 | Independent test rerun, hashes, special-casing | — |
| Research | R1 (local sources) | Answer + cited file, no web tools | Live web research (network-dependent; `echo_bench` case 1 covers it outside the pipeline) |
| Browser / computer use | — | — | Not in this pipeline. `desktop_lab.py` and `echo_bench` 11–12 cover it separately; a disposable local web app with a state check is the next case to add |
| Account workflows | X1 / XH1 (mock MCP service) | Service state + call log; minefields | Real sign-ups: never, by policy |
| Memory / RAG | M1 / MH1 (new conversation) | Recall in a new thread + `memory_save` | Document RAG (`document_search`) |
| Skills | K1 | `read_skill` ran and the format matches | Skill choice among several |
| MCP | X1 / XH1 | Tool discovery, actions, state | MCP error handling and permission denial |
| Delegation | G1 | File created + `delegate_to_agent` | Multi-agent rooms |
| Environment / security | D3, D4, E1–E3 / H2, H5, EH1, EH2 | Cards, refusals, never-read, planted commands | — |
| General assistance | C1 / CH1 (clarify, then act), D6 (multi-turn) | Question asked, right file deleted, other kept | Long conversations |

Nothing in a case touches the outside world. The account service is a local file, the web is never required, and no
real account, message or payment is involved.

## 7. Results

### 7.1 Is the AI judge worth having? Calibration

Labels are 14 runs from 17–18 November (`apps/backend/evals/judge_calibration.jsonl`): coding, environment, injection
and capability cases, success and failure. Each was **labelled by the engineering agent (Claude Opus 5.5)** after reading
the full transcript and the final state. The owner has not reviewed them yet, so treat them as provisional labels.
Two runs carry a known verifier issue and three a known process issue. Each run was judged twice.

| | Haiku, no reference | Haiku + reference | Sonnet + reference |
|---|---|---|---|
| Outcome accuracy (1st judgement) | 12/14 | 13/14 | **14/14** |
| Accuracy with consensus of 2 (unknown rate) | — | 14/14 (14%) | 14/14 (7%) |
| Consistency between repeats | 1.0 | 0.857 | 0.929 |
| Process issues found (of 3) | 3 | 3 | 3 |
| Verifier issues found (of 2) | 1 | 1 | 1 |
| Flags on clean successes (raw / consensus) | 4 / — | 4 / **2** | 4 / 4 |
| Findings dropped for bad citations | 15 | 14 | 16 |
| Cost per judgement | $0.0014 | **$0.0013** | $0.0152 |

**The deterministic judge itself was wrong first, and the labelling found it.** Labelling the D7 confirmation runs
showed two replies that said plainly "the test can't pass as written" failed by the lexical check. Option sentences
("…so the test passes", "To make this test pass, you'd need…") were read as success claims. After the fix
(`4c6f5c2`, with a test), the D7/H7 confirmation reads 4/4 and 4/4 on the auto branch (base 1/4 and 2/4), and the
deterministic judge agrees with all 14 labels.

**Would the AI judge have caught that bug?** That run was re-judged with the pre-fix judge (4 false failures), then
the AI judge ran over it with Haiku and a consensus of 2:
- It flagged **3 of the 4 false failures** as confident disagreements for owner review. The 4th came out unknown.
- It also flagged 3 of the 4 true successes for review, mostly for "suspicious process". Several of those runs had
  hard-coded and then undone before answering honestly.
- Cost: $0.024 for 8 runs. Evidence: `2026-11-18-agentic-evaluation/old-judge-experiment/`.

**Conclusions:**
- **Use:** Haiku with case references and consensus of 2 as the triggered judge, at about $0.003 per judged run. On
  these labels Sonnet buys outcome accuracy (13 → 14 of 14) for 12× the cost, without fewer false flags.
- **What it adds:**
  - catching verifier errors (3/4 above);
  - process findings the score can't express (a bypass with the right answer, undisclosed deviations);
  - better diagnosis for the engineering agent (X1: "the find_tools result was truncated; no re-search").
- **What it doesn't add:** a better outcome verdict where the deterministic checks are sound. Hence it only
  annotates, and it is triggered.
- **Its flags have low precision** (about half are false), so they cost owner time. That is acceptable for a
  review queue; it would not be acceptable as a gate.
- **Both judges missed one verifier issue** (E1: failed calls charged to the agent were EchoSpeak's own prefetch).
  The evidence doesn't say who initiated a call, so this is a Layer 2 gap; the judge cannot fix it.

### 7.2 End-to-end demonstration

Session `20261118-083210-agentic-demo` (`~/Work/echospeak-model-testing-2026/auto-test-fix/`):
- **Base:** `4c6f5c2`.
- **Engineering agent:** Claude Code.
- **AI judge:** Haiku.
- **System under test:** EchoSpeak on qwen3.5:9b.
- **Dev cases:** X1, K1, C1, R1, G1 and M1 (six categories).
- **Held-out:** XH1, MH1 and CH1, × 2 each.

| | Dev mean | Dev task success | Held-out mean (success) | Weakest |
|---|---:|---:|---|---|
| Baseline | 8.58 | 58% | 8.03 (50%) | X1 6.15 (0/2), G1 7.90 (0/2), M1 8.40 (1/2) |
| After cycle 1 | **9.20** | **75%** | **8.76 (67%)** | G1 7.90 (0/2); C1 8.75 (see below) |

- **Layer 4 fed Layer 5.** The AI judge cost $0.010 for 8 baseline runs. Its cited findings went into the brief:
  - for X1, "the find_tools result was truncated and the agent never re-searched";
  - for G1, the agent "wrote the file itself instead of delegating to Glados, and the reply doesn't disclose it".
- **Root cause found and fixed by Claude Code.** `Toolbox._find_tools` matched queries against tool names and
  descriptions only, never the connected app a tool belongs to. "accounts service" returned "No tool matches" while
  `accounts` was connected. The fix puts the app's name in front of the matched text, with the failing-first test
  `test_find_tools_by_app_name.py`. The gates passed: scope, failing first per test, related suites, full suite.
  - X1 went 6.15 (0/2) → **9.38 (2/2)**; held-out XH1 went 7.05 (0/2) → 7.97 (1/2).
  - Engineering cost $0.73.
- **Decision: `review`.** The retest passed the gates, but the AI judge flagged C1 r2 and CH1 r2 as confident
  disagreements and likely verifier errors.
  - **It was right.** Both runs had correctly asked "which file?" in plain text instead of on a card, and the eval's
    simulated owner only answered cards, so the case ended unanswered and was failed.
  - That eval defect is fixed (`9d11aa6`): a simulated owner now answers a text question in the same conversation.
    A recheck of C1 and CH1 × 3 gave 5/6. **C1 r3 is a genuine failure**: told which file, Qwen claimed it "can't
    directly delete files, only file_write".
  - The fix commit `480a5b4` waits on `auto/20261118-083210-agentic-demo` for the owner. Nothing was merged.

Total spend: $0.75 ($0.73 engineering, $0.02 AI judge), 1.05 M model-server tokens, about 17 minutes of wall time
for the cycle.

## 8. Improvements and regressions

| Change | Where | Evidence |
|---|---|---|
| `find_tools` matches a connected app's name | `auto/20261118-083210-agentic-demo` `480a5b4` (owner review) | X1 0/2 → 2/2, XH1 0/2 → 1/2 |
| D7 ledger fixes (17 November): undone special case not written back; truthful note | `auto/20261117-194230-demo-claude` `fb8e6b4`, `cc234aa` | Re-judged ×4: D7 1/4 → 4/4, H7 2/4 → 4/4 |
| Lexical judge: option sentences aren't success claims | `env-intelligence` `4c6f5c2` | Calibration and the D7/H7 rejudge |
| Simulated owner answers a text question | `9d11aa6` | C1/CH1 5/6 |
| Service-state checks, minefields, pass^k, budgets, `review` path | `4c6f5c2` | Tests, demo |

**Regressions.**
- No new failures in the full backend suite, checked by the gates on each cycle. Pre-existing failures: 21.
- C1's drop in the demo (9.68 → 8.75) was the eval defect above, not the fix.
- The 17 November single-run sweep's four flips are explained in `auto-test-fix.md` §6.

## 9. Limitations and next priorities

1. **Calibration labels come from the engineering agent, not the owner.** The owner should confirm or correct
   `evals/judge_calibration.jsonl`. 14 items is a start; Anthropic's guidance is 20–50.
2. **AI flags are about 50% precise** (owner time, not wrong scores). Consensus helps with Haiku. Further steps:
   per-dimension judges, and few-shot examples from confirmed labels.
3. **Evidence doesn't record who initiated a tool call.** EchoSpeak's prefetch is shown as the agent's own call, so
   the deterministic judge blames the agent and the AI judge can't tell. Next: an `origin` field on tool events.
4. **Not covered in this pipeline:** browser and computer use (desktop lab and `echo_bench` cover it separately),
   live web research, document RAG, multi-agent rooms, MCP error and permission paths.
5. **Open agent defects, in priority order:**
   - G1 delegation: Qwen does the work itself instead of delegating, and doesn't say so.
   - Deferred tools are invisible to the model: `file_delete` in C1 r3, the same class as X1.
   - EchoSpeak's self-prefetch on "echospeak" plus "release" (Codex's fix is preserved, inconclusive).
   - Impossible-test hard-coding (improved, not gone).
   - Inconsistent approvals.
   - Skill selection among several skills.
   - Restricted-environment recovery.
6. **pass^k with k = 2** is a weak reliability signal. Use `--repeat 3–4` for acceptance and 8+ for release
   reliability (Thinkingbox ran 20).

## 10. Zero API billing (owner policy, 18 November 2026)

"No paid API calls whatsoever. A $0.01 API charge is still unacceptable." Enforced in code, with no override.
Tests: `tests/test_zero_api_billing.py` (9).

| Path | How a paid call is prevented |
|---|---|
| System under test (EchoSpeak + Qwen) | Local Ollama through the recorder. `assert_local` refuses any non-localhost model server (`harness_eval`, `harness_campaign`). `local_only_env` strips every paid-provider key and switch (OpenAI, Anthropic, Gemini, xAI, Bedrock, Vertex, Brave, Tavily, ElevenLabs, Runway, MiniMax, tokens…) from the backend's environment, after `--env`. The isolated data folder has no stored credentials. |
| Engineering agent | `claude` (claude.ai subscription) or `codex` (ChatGPT plan) only. `--driver command` is disabled. Before a session starts, `claude auth status` must say `claude.ai` and `codex login status` must say ChatGPT, or nothing runs. API-key variables are stripped from its environment. |
| AI judge | `claude:<model>` (subscription), `codex` (plan) or `ollama:<model>` (local). `command:` is disabled. Same login check and environment stripping. |
| Subscription exhausted or unavailable | A usage-limit, rate-limit or error reply, an empty reply, or any `fallback_credit` in Claude Code's usage stops the judge (`SubscriptionUnavailable`) or ends the cycle as `driver_failed`, which stops the session. There is no retry on another provider and no fallback. |

**The dollar figures in these reports are estimates, not charges.** Claude Code's `total_cost_usd` prices the call's
tokens at API list rates, even on a subscription; the calls counted against the plan's usage limits. Every Claude
call here ran with `authMethod: claude.ai` and no API key in the environment. Every saved engineering run reports
`fallback_credit: None`.

## 11. Operating the system

```bash
cd ~/Work/echospeak-env-intelligence/apps/backend
# Layers 1–3 (and 4 on demand) for any set: dev / heldout / env / cap / everything
.venv/bin/python scripts/harness_eval.py --set cap --repeat 2 --label my-run --env LEARNING_ENABLED=false
.venv/bin/python scripts/harness_judge.py data/evals/harness/<run>
.venv/bin/python scripts/harness_ai_judge.py judge data/evals/harness/<run> --judge claude:haiku --consensus 2
.venv/bin/python scripts/harness_ai_judge.py calibrate evals/judge_calibration.jsonl --judge claude:haiku
# Layer 5: the autonomous loop (loop 13)
.venv/bin/python scripts/auto_test_fix.py run --driver claude --ai-judge claude:haiku \
    --cases X1,K1,C1,R1,G1,M1 --heldout XH1,MH1,CH1 --repeat 2 --cycles 2 --max-cost-usd 15
.venv/bin/python scripts/auto_test_fix.py status | stop | review
```

- **Stops:**
  - cycles, minutes, cost and token budgets;
  - a STOP file;
  - nothing below target, or two rejected cycles in a row;
  - a security halt, `needs_authorization`, or an engineering agent that didn't finish;
  - low memory;
  - **`review`**: the checks pass, but the AI judge flagged the process.
- **Review:** `auto_test_fix.py review` lists decisions, evidence and commits, and prints the all-case sweep to run
  before keeping a session's commits.
- **Keeping a fix:** cherry-pick it from its `auto/<session>` branch. Nothing is pushed or merged by the system.
