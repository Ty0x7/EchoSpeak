"""Audit repository state. Only explicitly selected, merged local branches can be retired."""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "Ty0x7/EchoSpeak"


def run(*args, check=True):
    return subprocess.run(args, cwd=ROOT, check=check, text=True, capture_output=True)


def git(*args):
    return run("git", *args).stdout.strip()


def json_command(*args):
    result = run(*args, check=False)
    if result.returncode:
        return {"unavailable": True, "exit": result.returncode}
    return json.loads(result.stdout)


def ancestor(ref, base):
    return run("git", "merge-base", "--is-ancestor", ref, base, check=False).returncode == 0


def audit():
    base = git("rev-parse", "refs/remotes/origin/main")
    remote = git("ls-remote", "--heads", "--tags", "--refs", "origin")
    remote_refs = {ref: oid for oid, ref in (line.split() for line in remote.splitlines())}
    if remote_refs.get("refs/heads/main") != base:
        raise SystemExit("Fetch origin/main before auditing.")
    worktrees = set(line.removeprefix("branch ") for line in git("worktree", "list", "--porcelain").splitlines()
                    if line.startswith("branch "))
    prs = json_command("gh", "pr", "list", "-R", REPO, "--limit", "100", "--json", "number,headRefName")
    active = {p["headRefName"] for p in prs} if isinstance(prs, list) else None
    refs = {}
    for line in git("for-each-ref", "--format=%(refname) %(objectname)", "refs/heads", "refs/tags").splitlines():
        ref, oid = line.split()
        refs[ref] = oid
    branches = []
    for side, inventory in (("local", refs), ("remote", remote_refs)):
        for ref, oid in sorted(inventory.items()):
            if not ref.startswith("refs/heads/"):
                continue
            name = ref.removeprefix("refs/heads/")
            reasons = []
            if name == "main":
                reasons.append("default branch")
            if active is None:
                reasons.append("open PR inventory unavailable")
            elif name in active:
                reasons.append("open pull request")
            if ref in worktrees:
                reasons.append("checked out in a local worktree")
            if not ancestor(oid, base):
                reasons.append("tip is not proven reachable from fetched origin/main")
            branches.append({"location": side, "name": name, "oid": oid, "reasons": reasons,
                             "proposal": "retain" if reasons else "review merged branch for retirement"})

    releases = json_command("gh", "release", "list", "-R", REPO, "--limit", "100",
                            "--json", "tagName,isLatest,isDraft,isPrerelease")
    tag_sources = sorted({ref for ref in (*refs, *remote_refs) if ref.startswith("refs/tags/")})
    tags = [{"name": ref.removeprefix("refs/tags/"), "local": refs.get(ref), "remote": remote_refs.get(ref),
             "proposal": "retain pending dependency review"} for ref in tag_sources]
    workflows = json_command("gh", "workflow", "list", "-R", REPO, "--all", "--json", "name,path,state")
    pages = json_command("gh", "api", f"repos/{REPO}/pages", "--jq", "{status,html_url,build_type,source}")
    deployments = json_command("gh", "api", f"repos/{REPO}/deployments?per_page=100",
                               "--jq", "map({id, environment, sha, created_at})")
    untracked = git("ls-files", "--others", "--exclude-standard")
    ignored = git("ls-files", "--others", "--ignored", "--exclude-standard", "--directory")
    version = run("python3", "scripts/check-version.py", check=False)
    return {"recorded_at": datetime.now(timezone.utc).isoformat(), "base": base, "branches": branches,
            "tags": tags, "releases": releases, "pages": pages, "workflows": workflows,
            "recent_deployments": deployments, "version_check": {"exit": version.returncode,
            "output": (version.stdout or version.stderr).strip()},
            "review_untracked": untracked.splitlines(), "review_ignored_roots": ignored.splitlines()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="Write the dry-run report as JSON")
    parser.add_argument("--retire-local", action="append", default=[], metavar="BRANCH")
    parser.add_argument("--execute", action="store_true", help="Allow selected local branch retirement")
    args = parser.parse_args()
    data = audit()
    if args.execute:
        if not args.report or not args.retire_local:
            parser.error("--execute requires --report and explicit --retire-local branches")
        candidates = {entry["name"]: entry for entry in data["branches"] if entry["location"] == "local" and not entry["reasons"]}
        if missing := set(args.retire_local) - candidates.keys():
            parser.error(f"Branches outside the merged and unclaimed set: {', '.join(sorted(missing))}")
        refs = [f"refs/heads/{name}" for name in set(args.retire_local)]
        bundle = args.report.resolve().with_suffix(".bundle")
        if bundle.exists() or args.report.exists():
            parser.error("Choose a new report path to preserve prior evidence")
        git("bundle", "create", str(bundle), *refs)
        git("bundle", "verify", str(bundle))
        heads = git("bundle", "list-heads", str(bundle)).splitlines()
        for name in args.retire_local:
            if f"{candidates[name]['oid']} refs/heads/{name}" not in heads:
                raise SystemExit("Archived ref changed. Audit again before retirement.")
        data["backup"] = str(bundle)
        data["restore"] = f"git fetch {bundle} refs/heads/BRANCH:refs/heads/BRANCH"
        data["retired_local"] = []
        args.report.write_text(json.dumps(data, indent=2) + "\n")
        for name in sorted(set(args.retire_local)):
            if git("rev-parse", "refs/heads/" + name) != candidates[name]["oid"]:
                raise SystemExit(f"{name} moved since audit")
            git("branch", "-d", "--", name)
            data["retired_local"].append(name)
            args.report.write_text(json.dumps(data, indent=2) + "\n")
    elif args.report:
        args.report.write_text(json.dumps(data, indent=2) + "\n")
    else:
        print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
