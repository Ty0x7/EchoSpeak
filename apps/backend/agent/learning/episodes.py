"""Episodes: what each agent actually did on a finished request, and how sure we are it worked.

Built in code from the run's own records (the tool timeline, stop reasons,
the job outcome), never from what the agent says about itself. Each episode
gets a level on the verification ladder:

    V0 claimed      the reply says it worked; nothing ran, or a claim had no tool behind it
    V1 observed     tools ran and succeeded
    V2 checked      a check succeeded after the last change (read it back, ran it, a test),
                    or for research, a source page was actually opened
    V3 corroborated two different kinds of check after the last change, or two
                    independent sources opened
    V4 confirmed    the owner pressed "Worked" (applied later, see feedback.py)

Tampering is watched too. Editing a test, a CI workflow, an eval script or
EchoSpeak's own settings files makes later "checks" worthless (the Verification
Horizon and Darwin Gödel Machine papers both show agents doing exactly this), so
such episodes are capped and anything learned from them waits for the owner.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

from agent.lean.job import READ_ONLY_TOOLS, Job
from agent.lean.policy import redact_secrets
from agent.learning.store import Episode

BOOKKEEPING_TOOLS = {"complete_task", "assign_tasks", "delegate_to_agent"}

# Results the loop wrote itself because the tool never ran (refused, denied,
# repeated, bad arguments). They say nothing about the tool or the task.
_NOT_RUN = (
    "blocked by echospeak's safety policy", "you already called", "the user denied permission",
    "the user did not respond to the approval", "the user stopped this request",
    "cancelled by the user before", "has more than 64 kib of arguments", "no result.",
)


def not_run(output: str) -> bool:
    low = str(output or "").strip().lower()
    return low.startswith(_NOT_RUN) or "needs the user's approval, which can only be given" in low \
        or (low.startswith("error:") and " again with a json object matching its parameters" in low)


# ── task kinds ──────────────────────────────────────────────────────────

_KIND_TOOLS: list[tuple[str, set[str]]] = [
    ("coding", {"terminal_run", "terminal", "process_start", "process_output", "process_stop", "self_edit",
                "self_rollback", "self_git_status", "self_grep", "self_read", "self_list"}),
    ("comms", {"email_send", "email_reply", "email_read_inbox", "email_search", "email_get_thread",
               "discord_send_channel", "discord_web_send", "discord_read_channel", "telegram_send",
               "whatsapp_send", "slack_send", "twitter_post", "tweet_post"}),
    ("media", {"create_media", "creation_status"}),
    ("files", {"file_write", "file_edit", "file_delete", "file_move", "file_copy", "file_mkdir", "file_read",
               "file_list", "file_find", "file_search"}),
    ("artifact", {"create_artifact", "update_artifact"}),
    ("desktop", {"desktop_click", "desktop_type_text", "desktop_send_hotkey", "desktop_list_windows",
                 "take_screenshot", "analyze_screen", "vision_qa", "open_application"}),
    ("research", {"web_search", "safe_web_fetch", "browse_task", "youtube_transcript", "research_notebook",
                  "document_search"}),
    ("live_data", {"weather_live", "sports_live", "stock_history", "product_search", "video_search", "image_search"}),
    ("memory", {"memory_save", "memory_search", "soul_update", "chat_search"}),
]
TASK_KINDS = [kind for kind, _ in _KIND_TOOLS] + ["chat"]
_CODE_FILE = re.compile(r"(?i)\.(py|js|jsx|ts|tsx|mjs|cjs|rs|go|java|kt|cs|cpp|cc|c|h|hpp|rb|php|swift|sh|ps1|sql|html|css)$")

_KIND_WORDS: list[tuple[str, re.Pattern[str]]] = [
    ("coding", re.compile(r"(?i)\b(code|script|function|bug|debug|compile|build|refactor|test|python|javascript|"
                          r"typescript|react|api|repo|git|npm|pip|terminal|command|program)\b"
                          r"|\b[\w-]+\.(py|js|jsx|ts|tsx|mjs|rs|go|java|kt|cs|cpp|rb|php|swift|sh|ps1|sql|html|css)\b")),
    ("files", re.compile(r"(?i)\b(file|folder|directory|rename|move|copy|delete|txt|csv|markdown)\b"
                         r"|\b[\w-]+\.(md|txt|csv|json|yaml|yml|toml|ini|log|pdf|docx?|xlsx?)\b")),
    ("comms", re.compile(r"(?i)\b(email|e-mail|discord|telegram|message (him|her|them)|reply to|send (a|an) (message|note))\b")),
    ("media", re.compile(r"(?i)\b(image|picture|video|draw|generate (a|an) (image|video|picture))\b")),
    ("live_data", re.compile(r"(?i)\b(weather|forecast|score|game|stock|price|shares)\b")),
    ("research", re.compile(r"(?i)\b(search|research|look up|find out|news|latest|sources?|compare|who is|what is)\b")),
    ("memory", re.compile(r"(?i)\b(remember|forget|my preferences?|soul|about me)\b")),
]


def guess_kind(goal: str) -> str:
    """Task kind from the request alone, before anything has run."""
    for kind, pattern in _KIND_WORDS:
        if pattern.search(goal or ""):
            return kind
    return "chat"


def classify(goal: str, tools: list[dict[str, Any]]) -> str:
    """Task kind from what actually ran; the request's words when nothing did."""
    ran = [t for t in tools if not t.get("not_run")]
    names = {t["name"] for t in ran}
    if any(t["name"] in {"file_write", "file_edit"} and _CODE_FILE.search(str(t.get("target") or "")) for t in ran):
        return "coding"
    for kind, members in _KIND_TOOLS:
        if names & members:
            return kind
    return guess_kind(goal)


# ── checks and tampering ────────────────────────────────────────────────

_TEST_CMD = re.compile(r"(?i)\b(pytest|py\.test|unittest|nose2|npm\s+(run\s+)?test|yarn\s+test|pnpm\s+(run\s+)?test|"
                       r"vitest|jest|mocha|cargo\s+test|go\s+test|dotnet\s+test|mvn\s+test|gradlew?\s+test|phpunit|rspec|ctest)\b")
_MUTATING_CMD = re.compile(r"(?i)(\s>{1,2}\s?\S|\b(rm|del|erase|rmdir|rd|mv|move|cp|copy|xcopy|robocopy|mkdir|md|touch|"
                           r"sed\s+-i|pip3?\s+(install|uninstall)|npm\s+(i|install|uninstall|ci)|yarn\s+add|"
                           r"git\s+(commit|checkout|reset|restore|push|merge|rebase|apply|stash|rm|mv)|"
                           r"set-content|add-content|out-file|new-item|remove-item|move-item|copy-item|rename-item)\b)")
_RUN_CMD = re.compile(r"(?i)^\s*(python3?|py|node|deno|bun|ruby|php|go\s+(run|build|vet)|cargo\s+(run|build|check)|"
                      r"dotnet\s+(run|build)|tsc|mypy|ruff|eslint|flake8|pylint|npm\s+run\s+(build|lint|typecheck|check)|"
                      r"make|\./\S+|bash\s+\S+\.sh|sh\s+\S+\.sh)\b")
_READ_CMD = re.compile(r"(?i)^\s*(ls|dir|cat|type|head|tail|more|wc|stat|tree|find|grep|rg|"
                       r"git\s+(status|diff|log|show)|get-content|get-childitem|gc|gci|test\s+-[ef])\b")

# Files whose change makes a later "check" meaningless.
_CHECK_FILES = re.compile(
    r"(?i)(^|[\\/])(tests?|__tests__|spec)[\\/]|(^|[\\/])(test_[^\\/]*|[^\\/]*_test)\.py$|\.(test|spec)\.[cm]?[jt]sx?$"
    r"|(^|[\\/])conftest\.py$|(^|[\\/])\.github[\\/]workflows[\\/]|(^|[\\/])scripts[\\/]eval_[^\\/]*$"
    r"|(^|[\\/])(pytest\.ini|tox\.ini|jest\.config\.[cm]?[jt]s|vitest\.config\.[cm]?[jt]s|ruff\.toml|mypy\.ini)$"
)
_CHECK_FILES_IN_TEXT = re.compile(
    r"(?i)(\btests?[\\/]|\btest_\w+\.py\b|\w_test\.py\b|\.(test|spec)\.[cm]?[jt]sx?\b|\bconftest\.py\b|\.github[\\/]workflows"
    r"|\bpytest\.ini\b|\bjest\.config|\bvitest\.config|\bscripts[\\/]eval_)"
)
# EchoSpeak's own controls: its data folder (settings, credentials, agents, the
# audit log, learning itself) and its code. Names only EchoSpeak uses count
# anywhere; a project's own settings.json or .env is the user's business.
_ECHOSPEAK_NAMES = re.compile(r"(?i)(experience\.db|tool-audit\.jsonl|settings\.secrets\.json)")
SELF_EDIT_TOOLS = {"self_edit", "self_rollback"}
_ASKS_FOR_TESTS = re.compile(r"(?i)\b(tests?|testing|spec|unit tests?|pytest|jest|vitest|ci|workflow|lint(er|ing)?)\b")


def command_kind(command: str) -> str:
    """test | run | read | change, for one terminal command."""
    text = str(command or "")
    if _TEST_CMD.search(text):
        return "test"
    if _MUTATING_CMD.search(text):
        return "change"
    last = re.split(r"&&|\|\||;|\|", text)[-1]
    if _RUN_CMD.search(last):
        return "run"
    if _READ_CMD.search(last):
        return "read"
    return "change"


def _check_kind(tool: dict[str, Any]) -> str:
    """The kind of check a successful call is, or ''."""
    name, output = tool["name"], str(tool.get("output") or "")
    if name in {"terminal_run", "terminal"}:
        kind = command_kind(str(tool.get("target") or ""))
        if kind in {"test", "run"}:
            # Finished with exit code 0; "still running in the background" checks nothing yet.
            return kind if output.lower().startswith("[ok") else ""
        return "read_back" if kind == "read" else ""
    if name == "process_output":
        return "run" if "finished (ok)" in output.lower() else ""
    if name in {"file_read", "file_list", "file_find", "file_search", "self_read", "self_grep", "self_git_status"}:
        return "read_back"
    if name in {"memory_save", "soul_update"} and re.search(r"(?i)\bverified\b", output):
        return "read_back"  # these tools read the change back from disk themselves
    return ""


def _is_change(tool: dict[str, Any]) -> bool:
    name = tool["name"]
    if name in {"terminal_run", "terminal"}:
        return command_kind(str(tool.get("target") or "")) == "change"
    if name in {"process_start", "process_output", "process_stop", "creation_status"}:
        return False
    return name not in READ_ONLY_TOOLS and name not in BOOKKEEPING_TOOLS


def _echospeak_file(target: str) -> bool:
    if _ECHOSPEAK_NAMES.search(target):
        return True
    from config import BASE_DIR, DATA_DIR

    norm = target.replace("\\", "/").lower()
    return any(str(root).replace("\\", "/").lower() in norm for root in (DATA_DIR, BASE_DIR))


def tampering(goal: str, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Changes to checks or to EchoSpeak's own controls, each marked expected or not."""
    asks_for_tests = bool(_ASKS_FOR_TESTS.search(goal or ""))
    found: list[dict[str, Any]] = []
    for tool in tools:
        if tool.get("not_run") or not _is_change(tool):
            continue
        target = str(tool.get("target") or "")
        is_command = tool["name"] in {"terminal_run", "terminal"}
        if tool["name"] in SELF_EDIT_TOOLS:
            found.append({"tool": tool["name"], "target": target[:200], "what": "EchoSpeak's own code", "expected": False})
        elif _echospeak_file(target):
            found.append({"tool": tool["name"], "target": target[:200], "what": "EchoSpeak's settings or records", "expected": False})
        elif (_CHECK_FILES_IN_TEXT if is_command else _CHECK_FILES).search(target):
            found.append({"tool": tool["name"], "target": target[:200], "what": "tests or checks", "expected": asks_for_tests})
    return found


def _domain(url: str) -> str:
    try:
        host = urlparse(url if "://" in url else f"https://{url}").hostname or ""
    except ValueError:
        return ""
    return host.lower().removeprefix("www.")


def grade(goal: str, tools: list[dict[str, Any]], *, outcome: str, claim_failed: bool) -> tuple[int, list[str], list[dict[str, Any]]]:
    """(level, reasons, tampering) for one agent's work. See the module docstring for the ladder."""
    ran = [t for t in tools if not t.get("not_run")]
    worked = [t for t in ran if t.get("ok")]
    tamper = tampering(goal, ran)
    if claim_failed:
        return 0, ["the reply claimed something no tool call did"], tamper
    if not worked:
        return 0, ["nothing ran, so there is nothing to check" if not ran else "no tool call succeeded"], tamper
    level, reasons = 1, [f"{len(worked)} tool call(s) succeeded"]
    changes = [i for i, t in enumerate(ran) if t.get("ok") and _is_change(t)]
    if changes:
        after = ran[changes[-1] + 1:]
        kinds = {k for k in (_check_kind(t) for t in after if t.get("ok")) if k}
        # A change that verifies itself (memory and soul writes read back from disk).
        own = _check_kind(ran[changes[-1]])
        if own:
            kinds.add(own)
        if kinds:
            level = 3 if len(kinds) >= 2 else 2
            reasons.append("checked after the last change: " + ", ".join(sorted(kinds)))
        else:
            reasons.append("nothing was checked after the last change")
    else:
        sources = {_domain(str(t.get("target") or "")) for t in worked if t["name"] in {"safe_web_fetch", "browse_task"}}
        sources.discard("")
        if len(sources) >= 2:
            level = 3
            reasons.append(f"read {len(sources)} independent sources")
        elif sources:
            level = 2
            reasons.append("read a source page, not only search snippets")
    unexpected = [t for t in tamper if not t["expected"]]
    if unexpected:
        level = min(level, 1)
        reasons.append("changed " + unexpected[0]["what"] + f" ({unexpected[0]['target'][:80]}), so later checks don't count")
    elif tamper:
        level = min(level, 2)
        reasons.append("wrote its own tests, so passing them is weaker evidence")
    if outcome != "success":
        reasons.append(f"outcome: {outcome}")
    return level, reasons, tamper


# ── building episodes from a finished request ───────────────────────────

def tool_rows(timeline: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in timeline:
        if item.get("kind") != "tool" or str(item.get("name") or "") in BOOKKEEPING_TOOLS:
            continue
        output = str(item.get("output") or "")
        rows.append({
            "name": str(item.get("name") or ""),
            "label": str(item.get("label") or "")[:200],
            "target": redact_secrets(str(item.get("target") or ""))[:300],
            "ok": item.get("status") == "done",
            "output": redact_secrets(" ".join(output.split()))[:240],
            "not_run": not_run(output),
        })
    return rows[:80]


def _first_sentence(text: str, limit: int = 200) -> str:
    body = " ".join(str(text or "").split())
    match = re.match(r"(.+?[.!?])(\s|$)", body)
    return (match.group(1) if match else body)[:limit]


def build_episodes(
    *,
    goal: str,
    results: list[Any],
    job: Optional[Job],
    taint: list[str],
    session_id: str,
    execution_id: str,
    source: str,
    names: dict[str, str],
    endpoints: dict[str, tuple[str, str]],
    lessons_used: dict[str, list[str]],
    team: bool,
) -> list[Episode]:
    """One episode per agent that spoke in this request (TurnResults in order)."""
    goal = redact_secrets(" ".join(str(goal or "").split()))[:2000]
    job_outcome = (job.outcome if job is not None else None) or {}
    episodes: list[Episode] = []
    for agent_id in dict.fromkeys(r.agent_id for r in results):
        mine = [r for r in results if r.agent_id == agent_id]
        name = names.get(agent_id, agent_id)
        tools = tool_rows(item for r in mine for item in (r.timeline or []))
        ran = [t for t in tools if not t["not_run"]]
        stops = {r.stop_reason for r in mine if r.stop_reason}
        errors = [r.error for r in mine if not r.success and r.error and r.error != "cancelled"]
        claim_failed = bool(stops & {"unverified_claim", "promise_unfulfilled"})
        owned_open = bool(job is not None and any(
            s.owner == name and s.status in {"open", "failed"} for s in job.subtasks))
        completed = next((r.completed for r in reversed(mine) if r.completed), "")
        text = next((r.text for r in reversed(mine) if r.text), "")
        if errors:
            # The model call itself failed (server down, bad key, overflow): not the agent's
            # doing, so it counts neither for nor against it, and nothing is learned from it.
            outcome, summary = "error", f"the model request failed: {errors[0][:200]}"
        elif claim_failed:
            outcome = "failure"
            summary = ("said it would act but didn't" if "promise_unfulfilled" in stops
                       else "claimed work that no tool call did")
        elif "max_steps" in stops:
            outcome, summary = "stopped", "ran out of steps"
        elif ran and not ran[-1]["ok"]:
            outcome, summary = "failure", f"its last action failed: {ran[-1]['label'][:160]}"
        elif job_outcome.get("status") == "stopped" and owned_open:
            outcome, summary = "stopped", str(job_outcome.get("reason") or "the team stopped")[:300]
        elif any(t["ok"] for t in ran):
            outcome = "success"
            summary = completed or (str(job_outcome.get("summary") or "") if team else "") or _first_sentence(text)
        else:
            outcome, summary = "answered", _first_sentence(text)
        level, reasons, tamper = grade(goal, tools, outcome=outcome, claim_failed=claim_failed)
        provider, model = endpoints.get(agent_id, ("", ""))
        episodes.append(Episode(
            agent_id=agent_id,
            agent_name=name,
            goal=goal,
            outcome=outcome,
            session_id=session_id,
            execution_id=execution_id,
            task_kind=classify(goal, tools),
            level=level,
            reasons=reasons,
            trusted=not taint,
            summary=redact_secrets(summary)[:400],
            stop_reason=",".join(sorted(stops)),
            source=source,
            provider=provider,
            model=model,
            team=team,
            tools=tools,
            taint=sorted(set(taint)),
            tamper=tamper,
            lessons_used=list(dict.fromkeys(lessons_used.get(agent_id, []))),
        ))
    return episodes
