---
slug: same-model-better-harness
title: "Same model, better harness: what software changed"
date: 2026-11-27
summary: With the model held fixed, changes to the surrounding software raised our judge's score from 7.94 to 8.95 and overall execution success from 48% to 83%.
status: verified
tags: [harness, evaluation, reliability]
evidence: [docs/research/harness-evaluation.md, docs/research/environment-access.md, docs/research/context-strategy.md, docs/research/check-integrity-experiment.md]
---

An agent is a model inside a loop: something has to choose tools, run them, read results, keep context and decide
when the work is finished. We call that the **harness**. This post collects what we measured when we changed only the
harness and kept the model, the hardware and the tasks the same.

## The setup

- **Model:** Qwen 3.5 9B (4-bit), served locally.
- **Tasks:** 14 tasks, each run 3 times: 8 development tasks used while making changes, and 6 held-out tasks never
  looked at while changing anything.
- **Judge:** a separate program that checks claims by rerunning tests, comparing files and reading server logs.

::figure harness-lift

| | Before | After |
|---|---:|---:|
| Judge score (out of 10) | 7.94 | **8.95** |
| Held-out score | 7.41 | **9.38** |
| Tasks actually completed (independent checks) | 48% | **83%** |
| Model calls ending with no usable answer | 31% | 15% |
| Prompt tokens processed | 1.90 M | 1.89 M |
| Critical findings | 6 security failures | 2 unsupported completion claims |

The largest gains came from the harness knowing what really happened in each step and acting on it:
- noticing an answer hidden in the model's reasoning;
- recovering instead of stopping;
- refusing to report a result the evidence contradicts.

## More of the same pattern

- **Reading files outside a project:** clearer grants and refusals raised that task group from 6.69 to **9.25**
  (3 of 15 tasks succeeded before, 14 of 15 after).
- **Memory:** a check at the end of each turn (did the agent actually save what you asked it to?) took a memory suite
  from 51 of 56 to **60 of 60**. The fix is in the harness; the model alone still forgets.
- **Speed:** keeping things that change every turn (the time, recalled memories) out of the start of the prompt let the
  server reuse its work. A follow-up turn's preparation dropped from **5.73 s to 0.31 s**.

## A negative result

We also tried teaching a 9B model a written procedure (a "skill") it could open when relevant. Across 182 requests it
**never opened it**. We kept nothing from that experiment.

## Limits

The result covers this model, workstation and task set. Two final runs still had unsupported completion claims,
which the harness marked unverified. The planned comparison with Hermes will look at how EchoSpeak performs with
the same weights and equivalent tasks.
