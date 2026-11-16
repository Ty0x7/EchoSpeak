---
title: "<YYYY-MM-DD> <topic>"
date: <YYYY-MM-DD>
branch: <branch>
harness: <commit>
baseline: <commit or none>
models: [<ollama tags>]
provider: <Ollama x.y / LM Studio x.y / API>
context: <served window>
evidence_status: live-recorded
tags: [echospeak, evaluation]
---

# <topic>

**Result in one line:** <who met the target, who didn't, the main finding>.

## Setup

- **Models.** For each model:
  - tag and digest;
  - quantization;
  - fit (on GPU %, size, cold load, generation tok/s).
- **Provider and configuration:**
  - window;
  - sampling sent;
  - renderer and parser;
  - approvals mode;
  - sandbox mode.
- **Cases:** D1–D8 and H1–H7, × <n> repetitions. The judge is `harness_judge.py`, with weights 30/25/20/15/10.

## Scores

| Model | Baseline | Final | Held-out | Tasks done right | Critical runs | Target |
|---|---:|---:|---|---|---|---|

## What failed, and why

| Model | Case | Evidence (trace) | Cause: model / provider / sandbox / harness / eval | Fix or status |
|---|---|---|---|---|

## Harness changes

- `<commit>`: <what, the evidence for it, the regression test>.

## Security and isolation

- <approvals, refusals, secrets, planted instructions, sandbox confinement as observed>.

## Regressions and limits

- <regressions; what wasn't run, such as live LM Studio, cloud models, or other windows>.

## Links

- Report: `docs/research/<topic>.md` (repo, branch <branch>).
- Artifacts: [evaluations/<campaign>](file:///<home>/Work/echospeak-model-testing-2026/evaluations/<campaign>/) (the vault is local, so a file link is fine here).
- Index: [[EchoSpeak/27 Live model evaluation|27 Live model evaluation]].
