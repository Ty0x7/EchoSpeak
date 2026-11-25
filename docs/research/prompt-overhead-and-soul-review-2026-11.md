# Prompt overhead and the SOUL review (25 November 2026)

Campaign round 2, priorities 2 (token optimization) and 3 (SOUL review). This note records what was measured,
what was removed and what was deliberately kept.

## The overhead, measured

Source: recorded model requests from a Code turn (qwen3.5:9b, 16K, PC1 baseline):

- system prompt: ~16.6K characters (~4.2K tokens);
- 21 tool schemas: ~12.8K characters (~3.2K tokens);
- in all, ~7.3K tokens before any work.

The largest pieces:

| Piece | Tokens (≈ characters ÷ 4) |
|---|---|
| "How you work" | ~1,200 |
| SOUL.md, all sections | ~1,200 |
| terminal tool schema | ~560 |
| Environment | ~410 |
| "What you can use right now" | ~400 |
| Code workflow | ~400 |

## What this change removes (~320 tokens per Code-turn model call)

1. **Rules for tools the model can't see this turn.**
   - The tool-specific rules in "How you work" were filtered by every loadable tool, deferred ones included, so a
     Code turn carried the rules for `research_notebook`, screenshots and `propose_skill` while offering none of
     them.
   - Usage rules now follow the tools offered this turn.
   - The two safety rules (computer control; looking vs doing) still follow every loadable tool, because
     `find_tools` can load a deferred tool part-way through a turn.
2. **The terminal description, sent twice.** The Environment line repeated `terminal.describe()`, which the
   terminal tool's own description already carries. It is now included only while the terminal is deferred.

On the test fixture, the Code prompt went from 12,329 to 11,042 characters. Chat's prompt is built separately and is
unchanged.

## What is deliberately kept

- **"What you can use right now"**: the capability report. The owner's VPN conversation ("I don't have terminal
  commands") shows that capability awareness is the weak spot. Trimming this would trade tokens for exactly the
  behavior the owner asked to improve.
- **The computer-control boundary text** in Environment: a security boundary.
- **SOUL.md, unchanged.** See the review below.

The larger lever is the window itself: a 32K window (#193) roughly triples the working room left after the overhead.

## SOUL review against the owner's sessions (23–24 November)

| SOUL rule | Evidence | Decision |
|---|---|---|
| "I'm an agent… real tools… I use them"; "I try the tool before saying I can't" | GTA_CLONE ran as Chat with no file tools; artifacts stood in for the work. | The cause was routing (#184) and the missing offer (#196). With `use_project_folder` the rule points at the right action. Kept. |
| "End with a short offer… not on every message" | The GTA replies asked "Should I proceed?" before acting. | Asking before acting is covered by the harness rule "only ask when…". An offer after finishing is fine (FA1 on main). Kept. |
| "Don't use emoji" | The GTA replies used 🎮 and ✅. | Model non-compliance, not wording. Kept. |
| "Never claim success…", "approvals pause…", "read the error, try another approach" | No failure traced to them. They duplicate "How you work" (~120 tokens). | The owner's identity file is not trimmed for tokens. Kept. |

**Conclusion:** no SOUL edit is justified by the evidence. Every measured failure traced to routing or harness checks
(#184, #186, #189, #196).
