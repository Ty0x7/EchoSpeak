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

Each episode also gets a cause (classify_cause): why it turned out the way it did.
"effective" is a checked success. The losses are failed_check, false_success
(said done, evidence disagrees), action_failed and stalled. The rest are
blockers that aren't the strategy's doing: environment, authorization,
tool_failure, information_gap and model_error. Those never count for or against
a lesson (store.NOT_ATTRIBUTABLE). The owner's feedback can reclassify an episode
(feedback_cause): "misunderstood", or "verifier_gap" when the checks passed but
the owner says it didn't work.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

from agent.lean.evidence import NOT_RUN as NOT_RUN_OUTCOMES
from agent.lean.job import READ_ONLY_TOOLS, Job
from agent.lean.policy import redact_secrets
from agent.learning.store import Episode

BOOKKEEPING_TOOLS = {"complete_task", "assign_tasks", "delegate_to_agent", "project_workspace", "save_project_image", "save_project_report", "set_objective"}  # Preserve historical classification.

# Results the loop wrote itself because the tool never ran (refused, denied,
# repeated, bad arguments). They say nothing about the tool or the task.
# Timelines now carry each call's outcome (agent/lean/evidence.py); these phrases
# grade records saved before that, and refusals a tool reports in its own words.
_NOT_RUN = (
    "blocked by echospeak's safety policy", "you already called", "the user denied permission",
    "the user did not respond to the approval", "the user stopped this request",
    "cancelled by the user before", "has more than 64 kib of arguments", "no result.",
    # Refused by a setting or held for the owner: the owner's call, not the agent's failure.
    "blocked by the user's privacy setting", "not run:",
)
_NOT_RUN_ANYWHERE = ("is not available right now", "is in blocked apps")


def not_run(output: str) -> bool:
    low = str(output or "").strip().lower()
    return low.startswith(_NOT_RUN) or any(m in low[:300] for m in _NOT_RUN_ANYWHERE) \
        or "needs the user's approval, which can only be given" in low \
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


def grade(goal: str, tools: list[dict[str, Any]], *, outcome: str, claim_failed: bool,
          verification: Optional[dict[str, Any]] = None) -> tuple[int, list[str], list[dict[str, Any]]]:
    """(level, reasons, tampering) for one agent's work. See the module docstring for the ladder."""
    level, reasons, tamper = _grade(goal, tools, outcome=outcome, claim_failed=claim_failed)
    measured = verification or {}
    if measured.get("status") == "failing" and level > 1:
        level = 1
        reasons.append(f"the last check failed ({str(measured.get('last') or '')[:120]})")
    if measured.get("tamper_after_fail") and level > 1:
        level = 1
        reasons.append("tests were changed after they failed, so passing them doesn't count")
    return level, reasons, tamper


def _grade(goal: str, tools: list[dict[str, Any]], *, outcome: str, claim_failed: bool) -> tuple[int, list[str], list[dict[str, Any]]]:
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
        # The loop records each call's outcome (agent/lean/evidence.py); older timelines only have the text.
        outcome = str(item.get("outcome") or "")
        row = {
            "name": str(item.get("name") or ""),
            "label": str(item.get("label") or "")[:200],
            "target": redact_secrets(str(item.get("target") or ""))[:300],
            # A screen action that changed nothing visible ran, but didn't work.
            "ok": outcome == "ok" if outcome else item.get("status") == "done",
            "output": redact_secrets(" ".join(output.split()))[:240],
            "not_run": outcome in NOT_RUN_OUTCOMES if outcome else not_run(output),
        }
        if outcome:
            row["outcome"] = outcome
        rows.append(row)
    return rows[:80]


def _first_sentence(text: str, limit: int = 200) -> str:
    body = " ".join(str(text or "").split())
    match = re.match(r"(.+?[.!?])(\s|$)", body)
    return (match.group(1) if match else body)[:limit]


_ERROR_KINDS: list[tuple[str, re.Pattern[str]]] = [
    ("context", re.compile(r"(?i)context (length|window)|n_ctx|too many tokens|maximum context")),
    ("rate_limit", re.compile(r"(?i)\b429\b|rate limit")),
    ("config", re.compile(r"(?i)\b(401|403|404)\b|api key|no api key|not found|select an api model|permission|"
                          r"billing|quota|credits|private mode|offline mode")),
    ("outage", re.compile(r"(?i)\b(500|502|503|504|529)\b|overloaded|unavailable|timed? ?out|timeout|connect|"
                          r"refused|unreachable|reset by peer|server disconnected|network")),
]


def classify_error(text: str) -> str:
    """Why a model call failed: outage | config | rate_limit | context | other.

    None of these are the model's doing at the task, so they never count as a loss.
    "context" is the one that does say something about fit: the task outgrew the model's window.
    """
    for kind, pattern in _ERROR_KINDS:
        if pattern.search(text or ""):
            return kind
    return "other" if text else ""


_TERMINAL_TOOLS = {"terminal", "terminal_run", "process_output", "process_start"}
_LOOKUP_TOOLS = {"file_read", "file_find", "file_search", "file_list", "self_read", "self_grep", "web_search",
                 "safe_web_fetch", "browse_task", "memory_search", "chat_search"}
_MISSED = re.compile(r"(?i)^(error: )?(no (matches|results|files?)\b|nothing (found|matches)|file not found|not found\b|no such file)")
_TOOL_BROKE = re.compile(r"(?i)(timed? ?out|internal (server )?error|unexpected error|service unavailable|\b50[0234]\b|"
                         r"\b429\b|rate limit|crashed)")
# Only explicit words for a misunderstood request: "wrong file" alone is just as likely a strategy that failed,
# and excusing it would shield a bad lesson from retirement.
_MISUNDERSTOOD = re.compile(r"(?i)(misunderst|misread|misinterpret|not what i (asked|meant|wanted)|that'?s not what i|"
                            r"\bi (meant|wanted|asked for)\b)")
_ENVIRONMENT_NOTE = re.compile(r"(?i)(my (machine|computer|pc|setup|environment|network|internet|connection)|"
                               r"(was|were|is) (down|offline)|(not|wasn'?t|isn'?t) installed|missing (dependency|package|module)|no internet)")


def _missing_info(ran: list[dict[str, Any]], run: int = 3) -> bool:
    tail = ran[-run:]
    return len(tail) == run and all(t["name"] in _LOOKUP_TOOLS and _MISSED.search(str(t.get("output") or "")) for t in tail)


def classify_cause(*, outcome: str, stops: set[str], tools: list[dict[str, Any]], verification: dict[str, Any],
                   text: str, level: int) -> str:
    """Why an episode turned out the way it did, from evidence. See the module docstring for the causes."""
    from agent.lean.verification import DONE_CLAIM, ENVIRONMENT_ERROR

    status, bottleneck = str(verification.get("status") or ""), str(verification.get("bottleneck") or "")
    if outcome == "error":
        return "model_error"
    if stops & {"unverified_claim", "promise_unfulfilled"}:
        return "false_success"
    if status == "blocked":
        return "authorization" if bottleneck == "authorization" else "environment"
    claims_done = bool(DONE_CLAIM.search(text or ""))
    if status == "failing":
        return "false_success" if claims_done else "stalled" if outcome == "stopped" else "failed_check"
    if status == "unverified" and verification.get("tamper_after_fail"):
        return "false_success" if claims_done else "unverified"
    if "refusals" in stops:
        return "authorization"
    ran = [t for t in tools if not t.get("not_run")]
    if outcome == "stopped":
        if bottleneck == "environment":
            return "environment"
        return "information_gap" if bottleneck == "information" or _missing_info(ran) else "stalled"
    if outcome == "failure":
        last = ran[-1] if ran else {}
        output = str(last.get("output") or "")
        if bottleneck == "environment" or ENVIRONMENT_ERROR.search(output):
            return "environment"
        if last.get("name") not in _TERMINAL_TOOLS and _TOOL_BROKE.search(output):
            return "tool_failure"  # the tool broke; the agent's command didn't fail
        return "information_gap" if _missing_info(ran) else "action_failed"
    if outcome == "success":
        if status in {"verified", "checks_pass"} or (level >= 2 and status != "partial"):
            return "effective"
        return "partial" if status == "partial" else "unverified"
    return ""


# The owner's next message, read as feedback on the reply before it. Unambiguous phrases only: a missed
# signal costs nothing, a misread one would credit or blame the wrong strategy.
_SAID_WRONG = re.compile(
    r"(?i)\b((that|this|it)('?s| is| was) (wrong|incorrect|not (right|correct|true|what i (asked|meant|wanted))))\b|"
    r"\b(that|it|this) (didn'?t|doesn'?t|did not|does not) work\b|\bstill (doesn'?t work|broken|failing|fails|wrong)\b|"
    r"\bnot what i (asked|meant|wanted)\b|\byou misunderstood\b|\byou got (it|that) wrong\b|\bthat'?s not (it|true|right)\b")
_SAID_WORKED = re.compile(
    r"(?i)\b(that|it|this) (worked|works( now| perfectly| great)?)\b|\bworks now\b|\b(that|this|it) fixed it\b|"
    r"\bperfect,? (thanks|thank you)\b|\bexactly what i (wanted|needed)\b")
_HEDGED = re.compile(r"(?i)\b(if|whether|once|until|hope|make sure|check that|see if|unless)\b[^.!?]{0,30}$")


def implicit_signal(message: str) -> int:
    """+1 / -1 when the owner's message plainly says the last reply worked or was wrong; 0 otherwise."""
    text = " ".join(str(message or "").split())[:240]
    if not text:
        return 0
    wrong = _SAID_WRONG.search(text)
    if wrong and not _HEDGED.search(text[:wrong.start()]):
        return -1
    worked = _SAID_WORKED.search(text)
    if worked and "?" not in text[:worked.end() + 2] and not _HEDGED.search(text[:worked.start()]):
        return 1
    return 0


def feedback_cause(episode: Episode, value: int, note: str = "") -> str:
    """The owner's Worked / Didn't work, as a cause. Reclassifies; never becomes an instruction by itself."""
    if value > 0:
        return "confirmed"
    if value == 0:
        return ""
    if _MISUNDERSTOOD.search(note or ""):
        return "misunderstood"  # about this request, not the strategy: no lesson loses for it
    if _ENVIRONMENT_NOTE.search(note or ""):
        return "environment"
    if str((episode.verification or {}).get("status") or "") in {"verified", "checks_pass"} or episode.level >= 2:
        return "verifier_gap"  # the checks passed and it still didn't work: they missed something
    return "owner_failed"


def _duration(results: list[Any]) -> float:
    stamps = [float(item.get("at") or 0) for r in results for item in (r.timeline or []) if item.get("at")]
    ends = [float(item.get("ended_at") or 0) for r in results for item in (r.timeline or []) if item.get("ended_at")]
    stamps = [s for s in stamps + ends if s > 0]
    return round(max(stamps) - min(stamps), 2) if len(stamps) >= 2 else 0.0


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
    lead: str = "",
    routed: Optional[dict[str, str]] = None,
    lessons_withheld: Optional[dict[str, list[str]]] = None,
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
        elif "refusals" in stops:
            # Handed back after repeated refusals: the owner's decision, so neither a win nor a loss.
            outcome, summary = "answered", "stopped after its actions were refused or blocked"
        elif ran and not ran[-1]["ok"]:
            outcome, summary = "failure", f"its last action failed: {ran[-1]['label'][:160]}"
        elif job_outcome.get("status") == "stopped" and owned_open:
            outcome, summary = "stopped", str(job_outcome.get("reason") or "the team stopped")[:300]
        elif any(t["ok"] for t in ran):
            outcome = "success"
            summary = completed or (str(job_outcome.get("summary") or "") if team else "") or _first_sentence(text)
        else:
            outcome, summary = "answered", _first_sentence(text)
        measured = next((dict(r.verification) for r in reversed(mine) if getattr(r, "verification", None)), {})
        level, reasons, tamper = grade(goal, tools, outcome=outcome, claim_failed=claim_failed, verification=measured)
        cause = classify_cause(outcome=outcome, stops=stops, tools=tools, verification=measured, text=text, level=level)
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
            lessons_withheld=[i for i in dict.fromkeys((lessons_withheld or {}).get(agent_id, []))
                              if i not in lessons_used.get(agent_id, [])],
            error_kind=classify_error(errors[0]) if errors else "",
            duration_s=_duration(mine),
            tokens=sum(int((r.usage or {}).get("fresh", (r.usage or {}).get("total", 0)) or 0) for r in mine),
            role="solo" if not team else ("lead" if agent_id == lead else "worker"),
            routed=(routed or {}).get(agent_id, "user"),
            verification={k: v for k, v in measured.items() if k != "ledger"},
            cause=cause,
        ))
    return episodes
