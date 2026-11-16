# Learning that measurably helps: EchoSpeak against the reference systems

EchoSpeak 11.11.7 audits the autonomous learning from [11.11.6](autonomous-learning.md) and the [verification](verified-engineering.md) it rests on. It compares them with published systems and fixes the confirmed weaknesses. The aim is a harness that gets better from verified experience, not one that keeps producing memories. [Visual architecture](../../artifacts/research/autonomous-learning.html) (updated).

## 1. Comparison with the reference systems

Sources were read directly where available. Each row separates what's proven from what's experimental.

| System | What it does | Evidence | EchoSpeak already | Difference / taken |
|---|---|---|---|---|
| **ReasoningBank** ([Google Research](https://research.google/blog/reasoningbank-enabling-agents-to-learn-from-experience/), [paper](https://arxiv.org/abs/2509.25140)) | Distils strategies (title, description, content) from successful *and* failed trajectories. An LLM judge decides success. Memories are appended and retrieved before acting. MaTTS spends extra compute on more experience | +8.3% WebArena, +4.6% SWE-Bench-Verified with Gemini-2.5-Flash. Consolidation is "future work"; it relies on a self-judge | Lessons have title and text, "do" and "avoid" kinds, and a contrast with a similar task that went the other way | EchoSpeak judges from **execution evidence** (V-ladder, ledger), not a self-judge, so a weak local model can't certify its own success. MaTTS isn't adopted: it multiplies model calls |
| **ACE** ([paper](https://arxiv.org/abs/2510.04618)) | Playbook "bullets" with helpful/harmful counters. Reflector distils, curator makes small delta edits merged deterministically. Grow-and-refine dedupes by meaning | +10.6% agents, +8.6% finance. Without labels it can degrade (FiNER online 67.3 vs 70.7) when context is "polluted by spurious signals". Weak reflectors give noisy contexts | Reflector → code curator → playbook. Merge-by-meaning at admission. Win/loss counts | EchoSpeak's counts are **against random holdouts**, stronger than helpful/harmful tags. **Added:** deterministic consolidation of twins admitted apart. ACE's warning that "not every task needs rich context" → **added:** no lessons for small talk, and a size budget |
| **Reflexion** ([paper](https://arxiv.org/abs/2303.11366)) | Verbal feedback across attempts, no weight updates | Benchmark-proven when grounded in a signal | In-task: 11.11.5 ledger feedback on each check. Across tasks: lessons | Same principle, grounded in tests rather than self-critique |
| **AlphaEvolve** ([overview](https://docs.cloud.google.com/gemini/enterprise/docs/alphaevolve/developer-guide/overview)) | Evolutionary search against user-defined metrics, starting from correct code | Production use; needs a strong evaluator | 11.11.5's metric, direction, target, baseline and best result | Not adopted as a search engine; Echo hill-climbs inside one task |
| **Claude Code** ([power-user tips](https://support.claude.com/en/articles/14554000-claude-code-power-user-tips)) | Corrections become `CLAUDE.md` rules, at the user's or reviewer's request. Auto-memory per repo. Skills for repeated work | Production convention ("each correction makes every future session better") | Project instructions, approved skills ("Make it a skill" from a proven lesson), personal memory | EchoSpeak doesn't turn a correction straight into a permanent rule. Corrections are **feedback** that reclassifies; lessons must prove themselves first |
| **Letta** ([memory](https://docs.letta.com/configuration/memory)) | A git-backed memory filesystem; background "dreaming" subagents consolidate without interrupting work | Product feature | The background reflector runs only when idle, capped, with an audit log and rollback | Same separation from active work. Letta lets the agent edit memory freely; EchoSpeak keeps a code-side curator |
| **LangMem** ([GitHub](https://github.com/langchain-ai/langmem)) | Hot-path memory tools, plus a background manager that extracts and consolidates | Library | Background only, plus the in-task ledger | No hot-path memory writes for lessons: fewer injection paths |
| **Codex** ([customization](https://learn.chatgpt.com/docs/customization/overview)) | AGENTS.md (nearest wins), memories, skills loaded progressively | Production convention | The equivalent layers exist (project instructions, memory, skills) | Lessons are progressive too: only matching ones, within a budget |
| **SkillsBench** ([site](https://www.skillsbench.ai/)) | Skills × harness × model; curated skills raise resolution for most configurations | Most of 24 configurations improve; gains vary from +4 to +26 points | — | Gains depend on the model: the evaluation must be done per model (§4) |
| **MINJA** ([paper](https://arxiv.org/abs/2503.03704)) | Memory injection through ordinary queries | Attack shown | Vetting, an untrusted tool log | **Changed:** one web-reading run can no longer put a lesson into context (§2) |

**Proven patterns EchoSpeak relies on:** grading from execution evidence, holdout comparisons, code-side vetting, background consolidation separated from active work, and progressive loading.

**Experimental, kept bounded and measured:** automatic transfer between agents, implicit feedback, and semantic retrieval.

**Not adopted:**
- MaTTS: it multiplies calls.
- LLM-judged success: weak local models can't grade themselves.
- Agent-editable memory for lessons: an injection path.

## 2. Weaknesses confirmed, and what changed

| # | Weakness (brief §3) | Confirmed in code | Change | Test |
|---|---|---|---|---|
| 1 | **Untrusted web-derived lessons entering context.** 11.11.6 admitted a generic lesson from a single web-reading run | `curator.admit` → probation | Such lessons now wait for **confirmation**: a lesson taught by work in a *different conversation* is released (untrusted, never shared, retired after 2 losses). Risky ones (site names, trust steering, outward actions, tampering) wait for the owner. Unreleased ones expire after 30 days. Diagnostics show only the owner-gated ones as needing review | `test_web_learned_lessons_wait_for_confirmation…` |
| 2 | **Implicit feedback attribution** | A casual "thanks, that worked" made a verified success and a lesson win. It went to every agent in the request. "That's wrong" counted anywhere in the message | Implicit positive is recorded but isn't proof (`Episode.confirmed` is the button only). It goes to the lead agent, or nobody when it's ambiguous. Only the opening of the message counts. Implicit negative still counts (corrections are specific) | `test_the_next_message…`, `test_implicit_feedback_goes_to_the_lead…` |
| 3 | **Transfer without controls** | Shared lessons were never held back | Transferable lessons are held back at random (20%), counted in `shared_held_*`. Transfer stops when the other agent's tasks go clearly better without it, or, before there's enough control data, after repeated failures. The home record is untouched | `test_transfer_has_a_control_group…` |
| 4 | **Reflection queue overlooking older valuable experiences** | It sorted only the oldest `limit×6` rows; anything older than 7 days was skipped | A `priority` column (migrated in place) orders the queue in SQL. Valuable experiences (verifier gaps, false successes, recoveries, the owner's word) keep a 30-day window | `test_the_review_queue_reaches…`, `test_valuable_experiences_wait_longer…`, `test_an_existing_database…` |
| 5 | **Lessons credited without contributing** | Any lesson read was credited or blamed on the request's result, whatever kind of work it became | Only lessons for the kind of work the episode turned out to be are counted (`applies`). Chat's unread-lesson crediting was fixed in 11.11.6 | `test_a_lesson_is_only_credited_on_the_kind_of_work…` |
| 6 | **Excessive or irrelevant instructions** | Lessons went into small talk, with no size budget | No lessons for requests under 3 content words. Budget: 900 characters, or 420 for Chat on a small model. Semantic matching (when the local embedding model is installed) finds relevant lessons without shared words | `test_no_lessons_for_small_talk…` |
| 7 | **Can't compare learning on vs off** | Control mode recorded nothing | Control episodes are recorded and tagged, with no attribution or reflection. The evaluation reports `by_mode`, including lesson characters per task | `test_control_mode…`, `test_learning_on_and_control…` |
| 8 | **Unnecessary reflections; drifting duplicates** | Routine successes covered by a lesson that just won were reviewed again; twins admitted apart stayed apart | Covered routine successes are skipped. Hourly deterministic consolidation merges twins (the better-proven one keeps both histories) | `test_a_routine_success_already_covered…`, `test_twins_admitted_apart…` |

**Memory taxonomy, unchanged and confirmed separate:**

| Layer | Holds | Written by |
|---|---|---|
| Personal memory (`agent/memory.py`) | Facts and preferences about the user | The owner and Echo, with approval when outside content was read |
| Project knowledge | Project instructions, plus project-scoped lessons | The owner; lessons are also learned |
| Procedural skills (agent skills) | Reusable procedures | Owner-approved, including from a proven lesson |
| Behavioural lessons (`agent/learning`) | Strategies, proven against holdouts | The curator decides; never facts about the user (refused) |

## 3. Verification

All checks ran on this branch, with no paid models and only tests for the changed components.

- **New: `tests/test_learning_research.py` (10 tests),** listed in §2.
- **Updated on purpose** (behaviour the brief asked to change):
  - `test_learning`:
    - Untrusted lessons now wait for confirmation.
    - Control mode now records tagged episodes.
  - `test_autonomous_learning`:
    - The confirmation release path.
    - Implicit positive isn't proof.
    - Transfer tests pin the transfer holdout and set task kinds.
- **Re-run and passing (232):**
  - `test_learning_research`, `test_autonomous_learning`, `test_learning`
  - `test_verified_engineering`, `test_routing`, `test_swarm`
  - `test_chat_mode`, `test_chat_paths`, `test_chat_lifecycle`
  - `test_environments`, `test_lean_runtime`, `test_lean_group`, `test_execution_evidence`
- **Web:** `LearningPage.test.tsx` passes, and `tsc --noEmit` is clean.

## 4. Evaluation plan for the owner

The question is whether learning makes the *same model* more reliable on *new* tasks, at acceptable cost.

1. **Pick held-out tasks.** Choose 10–20 tasks per environment, unlike the ones that produced lessons:
   - **Code:** fix a failing test, add a small feature with a check.
   - **Agents:** a multi-step research or file task.
   - **Chat:** a sourced factual question, an explanation.

   Keep a written list so the same tasks run in each condition.
2. **Run two conditions per model** (one local, one API):
   - `learning_mode=on`
   - `learning_mode=control`: same-size unrelated notes, so a gain can't come from a longer prompt

   Use a fresh experience store, or note the date, for each condition.
3. **Read the results** from `GET /lean/learning/evaluation?days=…`, using `by_mode`, then `by_environment`:
   - Verified completion
   - False success: of the replies that claimed success, those the evidence or you contradicted
   - Recovery
   - Stall rate
   - Attempts and tokens per verified task, and seconds per task
   - Lesson characters per task: the cost
4. **Check lessons and transfer:**
   - `lesson_effects`: each lesson against its holdout. "Negative transfer" lessons should be retired.
   - `transfer`: each shared lesson elsewhere vs its control.
5. **Decide:**
   - **Learning is worth it:** `on` beats `control` on verified completion or false success, without more tokens per verified task.
   - **It isn't:** turn it off for that model and environment, or reduce the playbook size.
6. **Expect model differences** (SkillsBench): a 4B model may gain less, and needs the compact budget.

## 5. What remains model-dependent

- **Lesson quality** depends on each agent's own model reflecting. ACE reports weak reflectors produce noisy contexts; the holdouts are what catch it.
- **Semantic retrieval** only works when the local embedding model is installed; otherwise matching is by keywords.
- **Confirmation for web-learned lessons** slows Chat's learning on purpose. Whether enough lessons get confirmed in normal use is something to watch in diagnostics.
