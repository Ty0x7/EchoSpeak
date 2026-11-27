# EchoSpeak research and planning report (27 November 2026)

For owner review. It covers: what is integrated now; what our accumulated research has found; which results can be
reused; the proposed harness comparison; the secure test environment; the website and blog; and the research direction.
Companion documents: [harness-comparison-plan.md](harness-comparison-plan.md) and [../blog/README.md](../blog/README.md).

## 1. Current state

| | State |
|---|---|
| main | `28f410b`: #241 (llama-server context window from `/props`) and #242 (eval tooling + Bonsai report) merged at their reviewed heads |
| PR #243 (open, **needs owner review**) | memory corrections with consent (closes #239), GPU handoff between Ollama and llama-server, Bonsai systemd service, `docs/local-models/bonsai.md` |
| Installed for testing | install `4daae1e39183-bonsai-primary-…` (PR #243 head). CLI, shared backend and Companion use it; data backup and rollback steps in its `local-build.json`. AppImage unchanged (attaches to the shared backend) |
| Default model | **Ternary Bonsai 2.5 27B** (`localai`, `127.0.0.1:8095`, 32K, xhigh effort with a 4K thinking cap). The previous default was Gemma 4 12B at 65K, not Qwen. Qwen 3.5 9B and Gemma stay selectable under Settings → Models → Ollama |
| Model runtime | `echospeak-bonsai.service` (user systemd): sleeps after 2 idle minutes (VRAM 7.7 → 1.4 GB), wakes in ~3 s. EchoSpeak unloads Ollama before calling it |
| Open | #243 review/merge · #207 #223 #225 backlog · Windows release not published · website not published (§6) |

Verified after install: `/provider` reports Bonsai ready with thinking controls; a live chat through EchoSpeak answered
correctly (66 s from cold); the service passed 6/6 screenshot tasks and a native tool call; the five memory-correction
cases passed 5/5 with an approval card each time (previously 0/10).

## 2. What our research has established

Status labels: **V** verified on this workstation with the independent judge; **M** measured but narrow (one machine,
few repetitions); **H** hypothesis.

1. **The harness carries a large share of agent reliability (V).** The same Qwen 3.5 9B went from 7.94 to 8.95, with
   held-out cases rising from 7.41 to 9.38 and execution success from 48% to 83%, from harness changes alone (14 cases
   × 3, `harness-evaluation.md`). Environment-access work took the E cases from 6.69 to 9.25 (`environment-access.md`).
   Memory reliability reached 60/60 mainly through an end-of-turn repair the harness performs, not the model
   (`context-strategy.md` §9).
2. **Models lie about completion; harnesses must check (V).** False success claims are the most common critical
   finding. Qwen 9 of 177 trials and Bonsai 1 of 177 in the latest campaign. The V0–V4 ladder plus independent
   reruns catch them (`verified-engineering.md`, `bonsai-vs-qwen-2026-11.md`).
3. **Model size still matters for autonomy (V).** At equal harness, Bonsai 27B (ternary, 1.76 bpw) beat Qwen 9B in every
   category, 9.25 vs 8.77, with agentic success 96% vs 69% and vision 18/18 vs 15/18.
4. **Quality and responsiveness can disagree (V).** Bonsai decodes 2.6× faster but reasons 4–5× more, so its median task
   is slower. The pre-registered combined score preferred Qwen. A reasoning budget (4,096) removed output-limit stops
   without lowering quality on dev cases (M).
5. **On 8 GB, memory layout decides what is possible (V).** Qwen 9B never fully fits beside a desktop (25–28/33 layers).
   Bonsai fits 32K only with a 4-bit KV cache and a small micro-batch; MTP and >32K don't fit on the GPU. Desktop VRAM
   use (1.0–1.35 GB) is a live variable that can crash a run.
6. **Prompt layout is a performance feature (V).** Keeping volatile content (time, recall, git status) out of the
   system prompt cut second-turn prefill from 5.73 s to 0.31 s (`context-strategy.md` §5). Hybrid recurrent models need
   context checkpoints for the same benefit (Bonsai `-cent`).
7. **Bigger windows aren't free and weren't the bottleneck (M).** For Qwen, 32K cost 37% generation speed and 64K 66%;
   recall was fine at 16K. Multi-file work failed at 16K mainly from prompt overhead and compaction behaviour, which
   the harness fixed (`conversation-audit-2026-11-19.md`).
8. **Rules and skills don't help small models by default (V, negative).** In the Harness²-style experiment a 9B model
   never opened a listed skill (0 `read_skill` in 182 requests); nothing was kept (`check-integrity-experiment.md`).
9. **Security boundaries hold when they are structural (V).** Planted instructions were never executed in any
   campaign. "Never readable" paths and the bubblewrap sandbox held even when a model probed alternatives (Bonsai E3).
   Consent is enforced by cards the harness raises, not by model obedience (#239).
10. **Evaluation discipline matters as much as the harness (V).** Private model servers per arm, held-out cases,
    pre-registration and dev-only tuning changed conclusions twice. Examples: the vision fixture bug caught before it
    biased results; the combined score disagreeing with quality.

**Open questions:**
- **(H)** How much of points 1–3 is specific to EchoSpeak's harness design, and how much any competent harness gives?
  The Hermes comparison measures exactly this.
- **(H)** Does the harness lift shrink or grow with model size and better hardware?
- **(H)** Can learned lessons (the learning loop) produce measurable gains on held-out cases with 27B-class models,
  where 9B ignored them?
- **(H)** Is a 64K window with the KV cache in RAM worth its speed cost for long-document work?

## 3. Reusable evidence (no re-runs needed)

| Asset | Where | Reuse |
|---|---|---|
| 59-case suite (34 dev, 25 held-out), fixtures, owner answers | `apps/backend/scripts/harness_eval.py` | identical tasks for Hermes and raw arms |
| Independent judge, weights 30/25/20/15/10 | `scripts/harness_judge.py` | the portable subset is used for all arms |
| Model-call recorder | `scripts/model_recorder.py` | the same evidence for Hermes |
| Private-server arm runner, resource sampler, watchdog | `scripts/harness_arm.py` | wraps the Hermes and raw arms |
| Bonsai/EchoSpeak results: 2 matched reps + 1 best-practical rep × 59; vision; Companion; acceptance | local evaluation folder (`evaluations/2026-11-26-bonsai-vs-qwen/runs`, not in Git) | **Arm E; re-judged on the portable subset only** |
| Qwen 3.5 9B results under the same harness | same folder | reference for model-vs-model lift |
| Earlier milestones (five-model, harness-first, memory ×4, env access) | `docs/research/*.md`, vault Evaluations/ | blog evidence and historical trend |

## 4. Proposed Hermes and raw-model comparison (summary)

Full method in [harness-comparison-plan.md](harness-comparison-plan.md).

- **Three arms:** E (EchoSpeak, reused), H (Hermes Agent v0.23.4, MIT), R (raw model: R0 intelligence-only, R1
  single-shot code). All use the same Bonsai weights and the same llama-server flags.
- **Coverage:** about 45 of the 59 cases map to Hermes; 14 are EchoSpeak-specific product features, reported as N/A.
  About 25 map to the raw arm. Nothing is scored zero for a capability it lacks by design.
- **Judging:** a portable judge subset, applied to **all** arms, including re-judging E's saved runs.
- **Main incompatibility:** Hermes requires a context of at least 64K, and Bonsai fits 32K on the GPU. The primary
  configuration serves 32K, with honest overflow errors if the startup check is configuration-based. A 64K
  KV-in-RAM fidelity subset runs for both H and E.
- **Cost:** about 7–8 GPU hours, compared with about 20 for re-running the EchoSpeak matrix.

## 5. Secure evaluation environment (summary)

Each Hermes run sits inside a user-level systemd scope (memory, CPU and task limits) wrapping a bubblewrap sandbox. No
sudo, containers or host changes are needed, and every tool involved is already installed (bwrap 0.13.1, socat,
systemd-run, uv, Python 3.14, ripgrep).

- **What the sandbox can see:** only its own disposable home, the case workspace, and a read-only Hermes checkout and
  venv. Nothing else under `~` is mounted, and neither is `/run/user`, so the keyring and D-Bus are out of reach.
- **Network:** the only route out is the model endpoint, through a Unix socket that `socat` forwards to the recorder.
  Update checks, telemetry and package installs therefore fail closed.
- **Installation:** the installer script is never run. The plan is a pinned tag checkout and `uv sync --frozen`,
  without the Chromium and computer-use components.
- **Resources:** the existing GPU lock, 2-second resource samples, memory watchdog and Bonsai idle sleep all apply.
- **Cleanup:** a post-run audit checks that nothing outside the run folder changed. Deleting the run folder restores
  the machine.

## 6. Website status and restoration plan

**Status.**
- The public site is a React/Vite app (`apps/web/site`; Home plus Docs, hash routes).
- The `Website` workflow builds it on every push to main that touches `apps/web` (last runs succeeded), but it
  **publishes only when GitHub Pages is enabled. It isn't** (`GET /repos/Ty0x7/EchoSpeak/pages` → 404).
- The repository is now **public**, and its homepage field already points to `https://ty0x7.github.io/EchoSpeak/`.

**Restoration** (needs owner approval, because it publishes):
1. Review the current site content for outdated claims. It was written for 11.15.0; check the model guidance against
   the latest results.
2. Enable Pages with source **GitHub Actions**: Settings → Pages, or
   `gh api -X POST repos/Ty0x7/EchoSpeak/pages -f build_type=workflow`.
3. Re-run the `Website` workflow, verify the deployment URL, and check the Home and Docs routes and the asset paths
   under the `/EchoSpeak/` base.
4. Optional later: a custom domain (set `SITE_BASE=/`).

## 7. Research blog

The structure, editorial rules and ten initial articles are in [../blog/README.md](../blog/README.md).

- **Where:** a `#/research` route in the existing site, Markdown posts with a status badge (verified,
  measured-on-one-machine, hypothesis, negative-result), rendered with the already-installed `react-markdown`.
- **Content:** every article draws on existing reports; none needs new measurements.
- **Rules:**
  - Publishing waits for Pages to be restored and for your approval of each post.
  - Comparative claims about other harnesses wait for the Hermes comparison.

## 8. Research direction

- **Constraint, deliberately:** 8 GB VRAM and 16 GB RAM, local inference only, no paid APIs. The question is how far
  harness design, quantization and verification can take ordinary hardware.
- **Themes:**
  - consumer-hardware accessibility;
  - extreme quantization (ternary, KV-cache quantization);
  - harness intelligence (how much of the capability is the loop's);
  - agent reliability and honest completion;
  - consent-based memory;
  - structural security;
  - efficiency (prefill reuse, reasoning budgets);
  - scalability (the same harness on larger hardware and models).
- **Scaling study (proposed, after the Hermes comparison):**
  - Run the same 59 cases with the same judge on a 24 GB-class GPU and on a larger model. Rented or borrowed
    hardware is acceptable only if it involves no paid inference API and no private data.
  - Measure whether harness lift persists as raw capability rises.

## 9. Recommended next steps (in order)

1. **Review and decide on PR #243.** It includes a security-relevant memory consent path. Use Bonsai day to day and
   note anything that feels slow or wrong.
2. **Approve the Hermes comparison plan,** or amend the case mapping, the 64K handling or the sandbox. Then: clone,
   prompt-size probe, runner, smoke and escape tests, pre-registration, runs.
3. **Approve website restoration.** First a content review, then enable Pages.
4. **Approve the blog route and the first 3–4 articles** (1, 2, 3 and 5 are the strongest-evidenced).
5. **Later:** the scaling study; learning-loop experiments on a 27B model; a managed model-runtime launcher in the
   EchoSpeak UI (start, stop and VRAM preflight) to replace the hand-installed service.
