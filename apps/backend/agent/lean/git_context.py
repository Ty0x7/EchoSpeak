"""Git awareness for the lean loop, the way Codex and Claude Code work with repos.

- ``summary(root)``: one line about the repo the agent is in (branch, upstream,
  ahead/behind, changed files), read at the start of a turn so the agent knows
  where it stands before it touches anything.
- ``wants_rules(goal, summary)``: whether this turn needs the git working rules
  (the folder is a repo, or the request is about git or GitHub), so other chats
  don't spend tokens on them.
- ``RULES``: how to branch, commit, push and use GitHub safely.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

RULES = """## Git and GitHub
- Look before you change anything: `git status -sb`, then `git log --oneline -5` or `git diff` as needed.
- Work on a branch, not the default branch: `git switch -c <short-topic>` unless the user says otherwise.
- Stage the files you changed by name (`git add path …`), check `git diff --staged`, and never commit secrets, `.env` files or build output.
- Commit messages: an imperative summary under 72 characters, then a line on why if it isn't obvious. Pass it with `-m`.
- Push, open or merge pull requests, and create releases only when the user asks. First push: `git push -u origin <branch>`.
- Never force-push, rewrite pushed history, skip hooks (`--no-verify`) or discard changes you didn't make (`reset --hard`, `checkout --`, `clean`, `restore`) unless the user asks for exactly that.
- Pull with `git pull --ff-only`. On a conflict, list the conflicted files, resolve them, and say what you chose.
- Use the GitHub CLI for GitHub: `gh pr create --title … --body …`, `gh pr view`, `gh pr checks`, `gh run view --log-failed` for failing CI, `gh issue list` / `gh issue view`. If `gh` isn't signed in, ask the user to run `gh auth login`; never ask for a token.
- Finish with what happened: branch, commit hash, and the PR or run URL."""

_GIT_WORDS = re.compile(
    r"(?i)\b(git|github|gh|commits?|branch(?:es)?|pull requests?|prs?|rebase|repo|repositor(?:y|ies)|clone|stash|"
    r"merge conflicts?|push (?:it|this|that|to|the branch)|ci checks?|github actions)\b"
)


def _git(root: str, *args: str) -> str:
    try:
        done = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, timeout=3,
                              encoding="utf-8", errors="replace",
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def summary(root: str) -> str:
    """'git: branch feature/x → origin/feature/x (ahead 2) · 3 changed files', or '' outside a repo."""
    if not root or not Path(root).is_dir():
        return ""
    status = _git(root, "status", "--porcelain=v1", "--branch")
    if not status:
        return ""
    lines = status.splitlines()
    head = lines[0][3:] if lines and lines[0].startswith("## ") else ""
    changed = [line for line in lines[1:] if line.strip()]
    branch, _, rest = head.partition("...")
    track = ""
    if rest:
        upstream, _, counts = rest.partition(" ")
        track = f" → {upstream}"
        if counts:
            track += " " + counts.strip()
    elif " [" in branch:
        branch = branch.split(" [")[0]
    branch = branch.replace("No commits yet on ", "").strip() or "(unknown)"
    if branch.startswith("HEAD (no branch)"):
        branch = "detached HEAD"
    conflicts = sum(1 for line in changed if line[:2] in {"UU", "AA", "DD", "AU", "UA", "DU", "UD"})
    parts = [f"git: branch {branch}{track}"]
    parts.append(f"{len(changed)} changed file{'' if len(changed) == 1 else 's'}" if changed else "clean")
    if conflicts:
        parts.append(f"{conflicts} with merge conflicts")
    return " · ".join(parts)


def wants_rules(goal: str, repo_summary: str) -> bool:
    return bool(repo_summary) or bool(_GIT_WORDS.search(goal or ""))


def failure_hint(text: str) -> str:
    """One line for the git and gh failures agents most often stall on."""
    low = (text or "").lower()
    if "not a git repository" in low:
        return "Hint: this folder isn't a git repository. Check the path, or run git init only if the user wants one."
    if "non-fast-forward" in low or "updates were rejected" in low or "fetch first" in low:
        return "Hint: the remote has commits you don't. Run git pull --ff-only (or ask before rebasing), then push again. Never force-push unasked."
    if "could not read username" in low or "authentication failed" in low or "gh auth login" in low or "not logged into" in low:
        return "Hint: not signed in to GitHub. Ask the user to run gh auth login (or set up credentials); never ask for a token."
    if "conflict" in low and ("merge" in low or "rebase" in low or "automatic merge failed" in low):
        return "Hint: merge conflict. List them with git diff --name-only --diff-filter=U, resolve each file, git add it, then continue."
    if "has no upstream branch" in low:
        return "Hint: first push of this branch: git push -u origin <branch>."
    if "please tell me who you are" in low:
        return "Hint: git has no author set. Ask the user for their name and email (git config user.name / user.email)."
    if "detached head" in low:
        return "Hint: you're not on a branch. Create one before committing: git switch -c <name>."
    return ""
