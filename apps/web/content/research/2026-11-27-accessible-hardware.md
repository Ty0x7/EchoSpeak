---
slug: accessible-hardware
title: Why we build for an 8 GB graphics card
date: 2026-11-27
summary: We use consumer hardware to study what a local agent can do within an 8 GB graphics memory budget.
status: philosophy
tags: [hardware, quantization, local-first]
evidence: [docs/research/bonsai-vs-qwen-2026-11.md, docs/research/context-strategy.md]
---

EchoSpeak runs on your own computer. Our research machine is ordinary on purpose: one consumer graphics card with
**8 GB of video memory**, **16 GB of system memory**, a desktop running beside it, and no paid model APIs.

We chose that constraint for accessibility. EchoSpeak also supports larger local models and optional cloud providers.

## What a small machine forces us to answer

- **What fits.** At our tested settings, Qwen 3.5 9B in 4-bit precision ran 25 to 28 of its 33 layers on the GPU.
  The 1.76-bit Ternary Bonsai 27B ran with a 32K window by using a 4-bit cache.
- **Where the time goes.** On this card, re-reading a long prompt costs more than writing an answer. How we lay out a
  prompt changed a 13K-token follow-up turn from 5.73 s to 0.31 s of prompt processing.
- **What the software must carry.** The harness checks completion claims against the work and can request an
  additional check when the evidence is missing.
- **What counts as useful.** We report successful tasks per hour and per million tokens alongside quality scores.

## Where it goes next

We plan to reuse the same tasks and judge on larger hardware. One [open question](#/research) is how much of the
measured benefit from EchoSpeak's harness carries over as models grow.

::figure platform
