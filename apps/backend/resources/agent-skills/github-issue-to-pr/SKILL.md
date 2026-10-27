---
name: github-issue-to-pr
description: Fix a GitHub issue in the attached project and open a pull request for the owner to review. Reads the issue, works on a new branch, runs the project's tests, commits, pushes and opens the pull request, each consequential step on the owner's approval. Never merges. Use when asked to fix, resolve or work on a GitHub issue and send the change back as a PR.
---

# GitHub issue → fix → pull request

The result is a pull request the owner reviews. It is never a merge, a release, or a change to
the default branch. Pushing and opening the pull request publish under the owner's GitHub
account, so each one waits for the owner's approval card.

## Before you change anything

1. **Read the issue.** `github_get_issue` with its number (and `repo` when it isn't the default).
   Restate in one line what "fixed" means. If the issue is vague, `ask_user` instead of guessing.
2. **Check the project.** It must be the attached project (`project_status`). Run
   `git status --porcelain` and `git branch --show-current` in `terminal`.
   - Uncommitted changes that aren't yours: stop and tell the owner. Don't stash, reset or
     commit their work.
   - Note the current branch so you can say where you started.
3. **Make a branch** from the up-to-date default branch: `git switch -c fix/issue-<N>-<short-slug>`.
   Never work on `main`/`master` directly.

## The fix

4. Find the cause with `file_search`, `file_find` and `file_read`. Fix it at the source with
   `file_edit`: the smallest change that resolves the issue. Don't weaken, skip or delete tests
   to make them pass.
5. **Test.** Run the project's own tests (look for them in the README, `package.json`,
   `pyproject.toml`, `Makefile`). Add or update a test that fails without your fix when the
   project has tests. If tests fail for reasons unrelated to your change, say so. Never claim
   they passed.
6. **Commit** only the files you changed: `git add <paths>` (never `git add -A` in a checkout
   with other changes), then `git commit -m "<what changed> (fixes #<N>)"`.

## Publishing (owner approval)

7. **Push** the branch: `git push -u origin <branch>`. Never force-push, and never push to the
   default branch.
8. **Open the pull request** with `github_create_pull_request`:
   - `head` is your branch.
   - `base` is empty for the default branch.
   - Leave `draft` as is unless the owner asked for a ready PR.
   - The body says what changed, why, how you tested it (the exact commands and results), and
     ends with `Fixes #<N>`.
9. **Report:**
   - the PR link
   - the branch
   - the tests you ran and their results
   - anything you weren't able to verify

   Tell the owner the PR is waiting for their review.

## Never

- Merge, close, approve or auto-merge a pull request, or enable auto-merge.
- Push to the default branch, force-push, delete branches, or rewrite history.
- Publish releases or packages, or change repository settings.
- Proceed when an approval is denied. Report what was done locally and stop.
- Drive Codex or Claude Code sessions behind the owner's back. Echo Code sessions are the
  owner's to start.
