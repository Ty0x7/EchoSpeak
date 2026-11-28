---
slug: evidence-based-execution
title: "Evidence-based execution: why completion claims need verification"
date: 2026-11-27
summary: EchoSpeak checks completion claims against what happened. Our evaluations measure when the claims and the evidence disagree.
status: verified
tags: [verification, reliability, safety]
evidence: [docs/research/verified-engineering.md, docs/research/bonsai-vs-qwen-2026-11.md, docs/research/harness-evaluation.md]
---

An agent can claim success after completing only part of a task.

- The judge flagged **9 of 177** runs for the 9B model and **1 of 177** for the 27B model. An evidence review
  found that the 27B run completed the task in an alternate location approved by the scripted owner, so that flag
  is disputed.
- In a synthetic account workflow, the agent created the account and reported completion before verifying the
  account or logging in. The service state recorded zero logins.

## Claims are tied to evidence

EchoSpeak records what each step actually did and grades any completion claim on a ladder:

::figure verification-ladder

A reply that claims more than its evidence supports is labelled as unverified. The agent is also asked once to either
do the work or correct the claim.

## What verification looks like in practice

Asked to build a small browser game and "make sure it runs":
- **27B model:** started a headless browser inside its sandbox, pressed the arrow keys programmatically, and checked
  that the snake grew, the score rose and the game-over message appeared.
- **9B model:** checked the JavaScript syntax and said so. That's honest, but it is a weaker level of evidence.

Both games loaded without errors in our independent check. The 27B model also verified game behaviour before reporting back.

## Consent is evidence too

When you correct a saved fact in passing, EchoSpeak can update its memory after **you approve a card** showing the
change. The card is required in every approval mode, and the write is bound to the exact recalled fact.

## Limits

Tests and live checks cover specific outcomes. A claim outside the evidence stays at V0 until a check supports it.
