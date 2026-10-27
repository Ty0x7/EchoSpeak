GitHub integration for EchoSpeak.

## When to use

When the user asks about:
- "List my open issues"
- "Show me the PRs on my repo"
- "Create an issue for bug X"
- "What's the status of PR #42?"
- "Comment on issue #7"
- "Fix issue #12 and open a PR"
- "How many open issues do we have?"

## Tool reference

### github_list_issues
List issues from a repository. Filters by state (open/closed/all), labels, and assignee. Uses the default repo unless specified.

### github_get_issue
Get details of a specific issue by number — title, body, labels, assignee, comments.

### github_create_issue
Create a new issue with title, body, and optional labels. This is an action tool — requires confirmation.

### github_list_prs
List pull requests from a repository. Filter by state (open/closed/all).

### github_get_pr
Get details of a specific PR by number — title, body, diff stats, review status, merge status.

### github_comment_issue
Add a comment to an issue or PR by number. This is an action tool — requires confirmation.

### github_create_pull_request
Open a pull request from a branch you already pushed (`head`) into `base` (the repo's default branch if empty). Opens as a draft unless asked otherwise. The owner approves every pull request on its own card; an earlier approval never covers the next one. It never merges: merging stays the owner's decision on GitHub. For the whole issue → fix → pull request procedure, follow the `github-issue-to-pr` Agent Skill.

## Requirements

Set `ALLOW_GITHUB=true` and `GITHUB_TOKEN` (personal access token with repo scope). Optionally set `GITHUB_DEFAULT_REPO` (e.g. "owner/repo").

## Output style

Use clean markdown. Show issue/PR numbers as `#N`. Keep descriptions concise. Group by labels when listing many items.
