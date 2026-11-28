---
slug: research-methodology
title: "How we evaluate EchoSpeak"
date: 2026-11-27
summary: Controlled experiments, an independent judge and a formal proof of the memory-visibility rule.
status: method
tags: [methodology, evaluation, formal-verification]
evidence: [docs/research/agentic-evaluation.md, docs/research/bonsai-vs-qwen-2026-11.md, docs/research/formal-verification.md]
---

We test how reliably and efficiently EchoSpeak completes tasks, and how specific architectural changes affect the result.

## Controlled experiments

- **One variable at a time.** A model comparison keeps the harness, tasks, tools, permissions and context budget the
  same. A harness change keeps the model and tasks the same.
- **Private, isolated runs.** The recent model comparison used private model servers, disposable EchoSpeak data,
  synthetic accounts and a sandboxed terminal.
- **Decided before results.** We write down the arms, the task groups and how scores combine before we see held-out
  results. We change settings only on development tasks, then freeze them.
- **Fresh tasks.** Development tasks guide changes, regression tasks guard what already works, and fresh or held-out
  tasks are used once, after everything is frozen.

::figure eval-pipeline

## Independent evaluation

- **A judge that reruns the work.** The deterministic scorer reruns tests in a copy of the project,
  compares file hashes, reads the service's state, and reads the model server's own logs.
- **Comparable outcomes.** For comparisons with other agent software, we use checks that each arm can satisfy.
- **Dimensions, not just a number.** Every report lists completion, tool use, efficiency, recovery and security
  separately, beside task success and any composite score.
- **Security stands apart.** A security violation (running a planted command, leaking a secret, writing outside the
  project) is reported case by case. It is never averaged away.
- **Disputes get a second look.** An ambiguous result can get an independent review, recorded next to the
  deterministic score, never replacing it.

The [V0–V4 verification ladder](#/research/evidence-based-execution) tracks how far the evidence reaches: the
agent's claim (V0), observed tool output (V1), a check after the final change (V2), independent corroboration (V3)
and owner confirmation (V4).

## Reproducibility

Every result names the model file and its hash, the runtime version, the server flags, the hardware, the context
window, the repetitions and the code revision. Raw model calls, judge output and resource measurements are kept, so a
result can be re-scored without running a model again.

## Formal verification, stated narrowly

One part of EchoSpeak has a machine-checked proof. The rule that decides **which saved memories are visible** in a
given chat and project is modelled in **Lean 4**. Five theorems are proved:
- a memory must be active and belong to the current owner to be read;
- a project's memory doesn't leak into another project;
- legacy project memories need the matching path;
- one chat's private memory isn't visible from another;
- a retired memory is invisible everywhere.

The proof uses only Lean's standard axioms and is replayed independently. **11,664** decisions generated from the Lean
model match the Python code exactly.

The theorem concerns visibility in the Lean model. Agreement with Python was checked over the 11,664 generated
cases. Other recall routes, text handling and concurrent changes require separate checks.

## Limits we state

- **One machine, few repetitions.** Our matched model comparison used two runs per task on one workstation.
- **Limited coverage.** The 59 cases sample capabilities the judge can check. Small differences require more evidence.
- **External comparison planned.** We will compare equivalent tasks with the same model weights where the actual
  runtime and available capabilities allow.
