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

_Below, after the session._
