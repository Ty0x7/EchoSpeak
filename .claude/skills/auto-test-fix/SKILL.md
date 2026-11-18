---
name: auto-test-fix
description: Run bounded, evidence-gated engineering cycles on EchoSpeak — test the harness with a real model, judge, diagnose the failing layer, let an engineering agent (Claude Code, Codex, or any CLI agent) fix it with a failing-first test, retest dev and held-out cases, and keep only what the gates prove. Use when the owner asks for autonomous improvement cycles; never as a side effect of other work.
---

Read the canonical skill at `.agents/skills/auto-test-fix/SKILL.md` in this repository. It builds on
`.agents/skills/model-harness-testing/SKILL.md`; the entry point is `apps/backend/scripts/auto_test_fix.py`.
