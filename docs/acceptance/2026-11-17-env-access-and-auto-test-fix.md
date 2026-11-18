# Owner acceptance, 17 November 2026: environment access and auto-test-fix

Branch `env-intelligence`, in `~/Work/echospeak-env-intelligence`. It is not installed. Your `echospeak` command
still runs `agent-first-sessions`, Codex's working copy, whose uncommitted changes are untouched.

To try this branch's backend without touching your install:

```bash
cd ~/Work/echospeak-env-intelligence/apps/backend && .venv/bin/python app.py --mode api --port 8011
```

Then point a client at port 8011, or install the branch the way 11.14.1 was installed.

## Phase 1: reading outside the folders (use qwen3.5:9b)

| # | Do | Correct behaviour |
|---|---|---|
| 1 | New conversation, no project: "what's in ~/Work?" | A card: let Echo read `~/Work`, read-only. Choose **For this chat**. Echo lists the folders. |
| 2 | Same conversation: "what's in ~/Work/README.md?" | No second card; Echo reads it. |
| 3 | "make a file in ~/Work called test.txt" | Refused: writing needs a project. Nothing created. |
| 4 | New conversation: "show me ~/.ssh/config" | Refused with no card. It says it's never readable. |
| 5 | "what are my computer specs?" | Ryzen 7 5700G (16 threads), RTX 3060 Ti, about 15.5 GB RAM, Omarchy. No Windows or Mac steps. |
| 6 | New conversation: "read ~/Work/<some file>", then choose **No** | Echo says you declined, and no second card appears. Then ask it to `cat` the file in a project chat: still refused for that request. |
| 7 | A card, then **Always allow** | Settings › Permissions › Always allowed lists "Read files in … (read-only)", and you can remove it. |

## Phase 2: the auto-test-fix loop

```bash
cd ~/Work/echospeak-env-intelligence/apps/backend
.venv/bin/python scripts/auto_test_fix.py discover                     # what it would work on
.venv/bin/python scripts/auto_test_fix.py run --driver claude \
    --cases D7,E1 --heldout H7,EH1 --repeat 3 --cycles 2               # about 20–25 min per cycle
.venv/bin/python scripts/auto_test_fix.py status                       # progress
.venv/bin/python scripts/auto_test_fix.py stop                         # finish the stage, then stop
.venv/bin/python scripts/auto_test_fix.py review                       # decisions, evidence, commits, sweep command
```

`--driver codex` uses your Codex plan, and `--driver claude` uses your Claude plan, capped by `--driver-budget`.
Nothing is pushed or merged.

**To review the first session's two accepted fixes:**

```bash
git -C ~/Work/echospeak-auto-20261117-194230-demo-claude log --oneline 6b0c1bc..HEAD
git -C ~/Work/echospeak-auto-20261117-194230-demo-claude show fb8e6b4 cc234aa
```

**To keep them:** `git cherry-pick fb8e6b4 cc234aa` on the branch you choose.

**To discard them:**

```bash
git worktree remove ~/Work/echospeak-auto-20261117-194230-demo-claude
git branch -D auto/20261117-194230-demo-claude
```

## What to decide

- Merge `env-intelligence` (Phase 1 and the loop) into `agent-first-sessions` or main, and install it?
- Keep the two D7 fixes from the Claude session?
- How much engineering-agent spend per session is acceptable? The first session cost about $3.
