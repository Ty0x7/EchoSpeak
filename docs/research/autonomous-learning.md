# Autonomous learning across Chat, Agents and Code

EchoSpeak 11.11.6 makes learning a mechanic of the shared harness instead of a page you manage. Echo learns from ordinary work in Chat, Agents and Code, with whichever local or API model you picked. Routine lessons no longer wait for your approval, and a correction in your next message counts as feedback. Proven lessons can be shared between agents, measured apart from their home results. The Learning page left the navigation and is now diagnostics under Settings › Advanced.

It builds on [verified engineering](verified-engineering.md) (11.11.5). **Revised in 11.11.7** after a research audit: web-learned lessons now wait for confirmation from other work, implicit praise isn't proof, transfer has a control group, and more. See [learning-research.md](learning-research.md). [Visual explanation](../../artifacts/research/autonomous-learning.html).

## 1. Audit: what already happened automatically, and what didn't

Audited from source on `claude/autonomous-learning`, stacked on 11.11.5 (PR #109).

| Lifecycle step | Before 11.11.6 | Gap |
|---|---|---|
| **Execute → observe → verify** | Every owner request in all three environments became graded episodes (V0–V4, plus 11.11.5's ledger and causes). Shared harness: `runtime._learn` → `learning.record_request` | No record of *where* the work happened or which project it was about |
| **Understand** | Background reflection with the agent's own model, only when no chat is running, capped at 30 a day | First come, first served: the budget went to whatever finished first, not to the most informative episodes |
| **Retain** | Code-side curator. Trusted lessons start unproven (probation) automatically | **Anything learned in a request that read outside content waited for the owner.** That's almost every Chat research answer and most Agents research, so in practice Chat learned nothing without manual approval. Facts about the user could become "lessons" |
| **Apply** | `prepare_turn` picks matching lessons, with random holdouts | **Bug:** Chat's prompt never included the lessons, yet they were counted as used, which corrupted their holdout comparison. Each agent's lessons were isolated; nothing was project-scoped |
| **Adapt** | Promotion and retirement by checked results vs holdouts | Lessons learned from web content were treated like any other once approved. Unreviewed lessons waited forever |
| **Feedback** | Worked / Didn't work buttons only | A correction in conversation ("that's wrong", "that worked") was ignored |
| **User experience** | A Learning page in Agents' navigation, with review queues and three settings (on/off, reviews per day, lessons per task) | You had to manage learning |

**How the environments differed:**
- **Agents and Code** share one learner: Echo is the same agent in both.
- **Chat** is its own agent (`chat`). It recorded episodes but never read its lessons, and needed approval for almost everything it learned.

## 2. Research

| Work | What it shows | What EchoSpeak takes |
|---|---|---|
| ReasoningBank ([Ouyang et al. 2025](https://arxiv.org/abs/2509.25140)) | Distilling reusable reasoning strategies from an agent's own judged successes *and failures*, then retrieving them for new tasks, improves agents over time | Lessons are strategies, not transcripts. Failures produce "avoid" lessons. Retrieval is per task |
| ACE, Agentic Context Engineering ([Zhang et al. 2025](https://arxiv.org/abs/2510.04618)) | A generate → reflect → curate cycle with small incremental edits avoids "context collapse"; dedupe by meaning | Already the shape here: reflector → curator → playbook with merge-by-meaning. 11.11.6 adds value-ordered reflection and scope |
| Reflexion ([Shinn et al. 2023](https://arxiv.org/abs/2303.11366)) | Verbal feedback helps when grounded in a real signal | Lessons come only from graded episodes and the ledger, never from the model's view of itself |
| Memory injection, MINJA ([Dong et al. 2025](https://arxiv.org/abs/2503.03704)) | Ordinary interactions can plant malicious records in an agent's memory | Defence in depth: the reflector sees the tool log, not page text. Lessons from untrusted runs that name sites or say whom to trust still need the owner; others are untrusted, never shared and retired after 2 losses |
| Self-generated playbooks can hurt (the existing playbook notes; negative-transfer findings) | More lessons ≠ better agents | Holdouts stay. Transfer between agents is measured separately and switched off when it fails |

**Production-proven vs experimental:**
- **Production-proven:** grading from execution evidence, holdout comparisons, code-side vetting.
- **Experimental:** automatic transfer of lessons across agents, and implicit feedback. Both are bounded and measured, so they can be judged on real use.

## 3. Architecture: one learner in the shared harness

```
Chat ┐                         ┌─ episodes (environment, project, cause, verification)
Agents├─ LeanSession._learn ──►├─ reflector (agent's own model, idle-only, capped, most informative first)
Code ┘   prepare_turn ◄────────┴─ curator (risk-based) ─► playbook (holdouts, scope, transfer) ─► evaluation
         implicit_feedback ◄── the owner's next message
```

No new store and no per-environment learner. Everything goes through `agent/learning` and the existing `LeanSession` hooks.

### Where experience lives

| Scope | When | Example |
|---|---|---|
| **Agent** (default) | Learned from that agent's work | Echo's lessons serve it in both Agents and Code |
| **Project** | The reflector marks `"scope": "project"` and the work had a project | "In this project, run the linter before the tests". Read only in that project |
| **Shared** (transfer) | A lesson is *proven* (established), trusted, general, and the task kind matches | Echo's "open two independent sources for research questions" offered to Chat, at most one per prompt, after Chat's own |
| **Never a lesson** | A fact about the user | "The user prefers short answers" is refused: it belongs in memory |

**Transfer is measured apart from home results:**
- Each use by another agent adds to `shared_uses`, `shared_wins` and `shared_losses`.
- After two losses at a low win rate, `transfer` switches off.
- The lesson keeps its standing with its own agent, so a strategy that works for Echo but not for a 4B model in Chat stops spreading without being retired.
- Lessons learned while reading outside content never transfer.

### Risk-based curation

Unchanged, and refused outright: credentials, links, markup, permissions, safety, approvals, weakening checks, injection patterns, file names.

| Lesson | Before | Now |
|---|---|---|
| From trusted, untampered work | Unproven, used | Same |
| Learned while reading outside content, generic | **Waited for owner** | Unproven, used, marked untrusted: never shared, retired after 2 losses with no win |
| Learned from outside content, names a site or says which sources to trust | Waited | Waits (MINJA-style steering) |
| From work that changed tests or EchoSpeak's files | Waited | Waits |
| About sending or posting things | Waited | Waits |
| A fact about the user | Kept as a lesson | Refused: memory, not behaviour |
| Unreviewed for 30 days | Waited forever | Set aside, never used |

### Feedback without buttons

`learning.implicit_feedback` runs at the start of each interactive owner request. If the message plainly says the last reply worked or was wrong, it applies that to the most recent request in the conversation (within 6 hours). Examples: "that worked", "it works now", "that's wrong", "it still doesn't work", "you misunderstood".

- It makes no model call.
- It never overrides the owner's own button.
- Hedged phrasing is ignored ("let me see if it works", "does it work?").

The 11.11.5 reclassification applies to it too: "you misunderstood" isn't blamed on lessons, and "it doesn't open in Excel" after passing checks is a verifier gap.

### Bounded

- Reflection is unchanged in cost: idle-only, 30 a day by default, using the agent's own model. It's now ordered by value:
  1. checks that missed something, and contradicted claims
  2. recoveries
  3. the owner's word
  4. checked successes
- Chat on a small model reads at most 2 lessons.
- Nothing in learning adds a model call to a conversation; the tests assert the call counts.

### What you see

- **Navigation:** Learning is gone from it.
- **Settings › Memory:** one switch, "Learn from experience", plus *Open learning diagnostics*.
- **Settings › Advanced › Learning:** the former page. It shows what was learned and whether it helps (holdouts), the rare lessons that need review, per-agent pause, and **Reset learning**, which leaves memory untouched.
- **Removed from the UI:** "reviews per day" and "lessons per task". The backend still honours existing values.

## 4. Verification

These checks ran on this branch. No paid models were used, and only tests for the changed components ran.

**New: `apps/backend/tests/test_autonomous_learning.py` (10 tests).** The real runtime with scripted models:
- **The whole lifecycle from ordinary use, with no approvals:**
  1. A checked task becomes an `agents` episode and queues its own reflection.
  2. The idle worker reflects, and the curator admits the lesson.
  3. The next similar task's prompt contains it, and its verified result credits the lesson.
  4. The evaluation counts it under `agents`.
- **Chat reads the lessons it's credited with.** This test fails without the fix: confirmed by temporarily reverting it.
- Risk-based curation: a generic web-learned lesson is admitted as untrusted, while trust-steering and site-naming ones wait.
- Untrusted lessons retire after 2 losses, and unreviewed ones are set aside after 30 days.
- Facts about the user are refused.
- **The next message as feedback:**
  - "That worked, thanks!" adds no model call, and the episode becomes a verified success.
  - The owner's button wins over it.
  - Hedged phrases are ignored.
- **Transfer:** offered once proven, counted apart from home results, switched off after it fails while the lesson stays established, never for untrusted lessons, and reported by the evaluation.
- Project lessons are read only in their project, and the reflector offers project scope only when there's a project.
- The review budget goes to a verifier-gap episode before plain work.
- Reset clears learning and keeps per-agent pauses.

**Re-run and passing** (existing tests for the changed learning, runtime and Chat prompt code):
- `test_learning`: one expectation deliberately updated, since generic lessons learned from web content are now admitted untrusted instead of waiting. The MINJA poisoning test is unchanged and passes.
- `test_verified_engineering`, `test_routing`, `test_swarm`
- `test_chat_mode`, `test_chat_paths`, `test_chat_lifecycle`
- `test_environments`, `test_lean_runtime`, `test_lean_group`, `test_execution_evidence`

**Web:**
- `LearningPage.test.tsx` and `environments.test.tsx`, with a new assertion that Learning isn't a navigation page
- `tsc --noEmit`
- The dev build in the browser preview:
  - Agents shows Group chats and Routines only
  - Settings › Memory shows the switch and the diagnostics button
  - Settings › Advanced › Learning renders the diagnostics with Reset learning
  - no console errors
  - `GET /lean/learning/evaluation` returns `by_environment` and `transfer`
- The reset HTTP call wasn't exercised in the browser, because the action was declined as destructive. The store reset is covered by the unit test.

## 5. Model-dependent, or needing your live test

- **Lesson quality depends on the model** that reflects (each agent's own). A 4B model writes weaker lessons; the curator and holdouts limit the damage but can't make them better.
- **Implicit feedback is phrase-based.** Unusual wording is missed, which is safe (no signal), and sarcasm would be misread, which is rare.
- **Transfer needs real volume** before switch-off decisions mean much (2 losses minimum). Watch `transfer` in the evaluation.

**Suggested live test:**
1. Ask Chat a research question with web sources, then say "that worked". Later, ask a similar question. In diagnostics, expect a Chat experience marked checked, possibly a new unproven lesson, and that lesson in later similar prompts.
2. In Code, fix a failing test twice in the same project. Expect a lesson, possibly project-scoped.
3. Say "that's wrong" after a reply. The experience shows "You said it didn't work", with your words as the note.
4. Turn learning off in Settings › Memory. No new experiences are recorded.

## 6. Additional findings

- **Chat's unread lessons** (fixed) inflated "uses" and could have promoted or retired lessons on results they never influenced.
- **Unreviewed lessons had no expiry**, and the review queue capped at 20 per agent, after which new lessons were silently refused.
- **The reflection queue was FIFO**, so a busy day spent the cap on routine successes.
- **Personal preferences could become behavioural lessons,** mixing memory and learning.

## 7. Limits and next steps

- Transferred lessons aren't held back at random. Their effect is judged against their home record, not a randomized control.
- Implicit feedback applies to the whole previous request, which in a group means every agent that spoke.
- The evaluation endpoint isn't charted in diagnostics yet; it's the place to watch once real data exists.
