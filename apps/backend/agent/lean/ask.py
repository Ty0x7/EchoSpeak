"""ask_user: the agent asks the person a short question with a few choices.

It reuses the approval pause (agent/lean/approvals.py): the run waits until the
person picks an option or types an answer in the app, and the answer comes back
as the tool's result. Meant for when the agent is blocked (a tool keeps failing,
setup is missing) or when a choice is genuinely the user's. Not for things the
agent can decide or look up itself.
"""

from __future__ import annotations

import re
from typing import Any

from agent.lean.toolbox import NativeTool

ASK_USER = "ask_user"

ASK_USER_DESCRIPTION = (
    "Ask the user a short question with 2-4 concrete choices and wait for their answer. "
    "Use it when you are blocked (a tool keeps failing, something isn't set up) or when the choice is "
    "genuinely theirs (which option, whether to go ahead). Don't use it for things you can decide or look up. "
    "Each option is a short action, e.g. 'Read a news site directly', 'Set up a search key', 'Skip it'. "
    "A question is not a permission: an answer never turns on a tool or a setting that is off."
)

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "question": {"type": "string", "description": "One short question, in plain words."},
        "options": {
            "type": "array",
            "items": {"type": "string"},
            "description": "2-4 short choices, each an action the user can pick.",
        },
        "allow_other": {
            "type": "boolean",
            "description": "Also let the user type their own answer (default true).",
        },
    },
    "required": ["question", "options"],
}

MAX_QUESTION = 300
MAX_OPTION = 80


def parse(args: dict[str, Any]) -> tuple[str, list[str], bool, str]:
    """(question, options, allow_other, problem). A non-empty problem means don't ask."""
    question = " ".join(str(args.get("question") or "").split())[:MAX_QUESTION]
    raw = args.get("options")
    if isinstance(raw, str):
        raw = [part for part in raw.replace("|", "\n").splitlines()]
    options: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        text = " ".join(str(item or "").split())[:MAX_OPTION]
        if text and text.lower() not in {o.lower() for o in options}:
            options.append(text)
    allow_other = args.get("allow_other")
    allow_other = True if allow_other is None else bool(allow_other)
    if not question:
        return "", [], allow_other, "Error: ask_user needs a 'question'."
    if len(options) < 2:
        return question, options, allow_other, "Error: ask_user needs 2-4 'options' the user can pick from."
    return question, options[:4], allow_other, ""


_ITEM = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+(.+?)\s*$")
_ASKS = re.compile(r"(?i)\b(would you|should i|do you want|shall i|which (?:one|option|would)|want me to|prefer|how would you like)\b")
_MARKUP = re.compile(r"\*\*|__|`")


def menu_in_text(text: str) -> dict[str, Any] | None:
    """A choice question written as prose at the end of a reply: {"question", "options"} for ask_user, or None.

    "Would you like me to: 1. Create config.json and run it again 2. … 3. …?" ends the run, where the
    question card would have let the run go on with the user's pick (ministral-3:8b, all three runs of a
    recover-from-an-error task in the five-model evaluation). Only a trailing list of 2-4 short items
    under a question counts; lists inside an answer don't.
    """
    lines = [line for line in str(text or "").rstrip().splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()
    end = len(lines)
    if lines and not _ITEM.match(lines[-1]) and lines[-1].strip().endswith("?"):
        end -= 1  # a closing question after the list
    start = end
    while start > 0 and (_ITEM.match(lines[start - 1]) or not lines[start - 1].strip()):
        start -= 1
    items = [_ITEM.match(line).group(1) for line in lines[start:end] if _ITEM.match(line)]
    if not 2 <= len(items) <= 4 or any(len(item) > 160 for item in items):
        return None
    # The question must introduce the list ("Would you like me to:"): a list of what was done followed by
    # "Want me to run the tests?" is an answer, not a menu.
    question = next((line.strip() for line in reversed(lines[:start]) if line.strip()), "")
    if not question.endswith(("?", ":")) or not (_ASKS.search(question) or question.endswith("?")):
        return None
    options = [_MARKUP.sub("", item).rstrip("?.").strip()[:MAX_OPTION] for item in items]
    question = _MARKUP.sub("", question).rstrip(":").strip()
    if re.search(r"(?i)\b(me|i)\s+to$", question):
        question = "Which of these should I do?"
    elif not question.endswith("?"):
        question += "?"
    return {"question": question[:MAX_QUESTION], "options": options}


# An option that asks the user to hand over a permission or switch something on.
_GRANT = re.compile(
    r"(?i)\b(give|grant|allow|enable|turn(?:ing)? on|switch(?:ing)? on|unlock|approve|authori[sz]e)\b[^.?!]{0,60}"
    r"\b(permissions?|access|file (?:creation|writing|writes?|edits?)|creat(?:e|ing) files|writ(?:e|ing) files|"
    r"terminal|commands?|desktop control|capabilit(?:y|ies)|tools?|switch(?:es)?|settings?)\b"
    r"|\b(permissions?)\b[^.?!]{0,30}\b(to|for)\b[^.?!]{0,40}\b(creat|writ|edit|run|execut|control|sav|delet|build)")


def asks_for_permission(question: str, options: list[str]) -> str:
    """The option that asks the user to grant a permission, or "".

    A question card collects a choice. It cannot turn on a switch in Settings › Permissions, and it is
    not an approval of an action: those come from the approval card EchoSpeak shows when a tool that
    needs one is called. Glados offered "Give me permission to create files and I'll build it right
    now", the owner picked it, and nothing changed (conversation audit, 16 November 2026).
    """
    for text in options:
        if _GRANT.search(text or ""):
            return text
    return ""


def prose_action_confirmation(goal: str, reply: str) -> bool:
    """A narrow case: one unconditionally requested file removal, followed by a prose confirmation."""
    request = re.fullmatch(r"(?:please\s+)?(?:delete|remove)\s+[`\"']?([\w./-]+\.[\w-]+)[`\"']?"
                           r"(?: (?:from|in) the (?:project|workspace))?[.!]?", goal.strip(), re.I)
    if not request:
        return False
    target = re.escape(request[1].rstrip("."))
    return bool(re.search(r"(?:would you like me to|do you want me to|should I)\s+(?:proceed with\s+)?"
                          r"(?:delet(?:e|ing)|remov(?:e|ing))\s+[`\"']?(?:it|" + target + r")[`\"']?[:, ]*"
                          r"(?:or would you prefer to keep it)?\?", reply, re.I))


# Consequential actions, by family: a confirmation counts only when the owner's request used the same family.
_ACTION_FAMILIES = {
    "delete": r"delet\w*|remov\w*|eras\w*|wip(?:e|es|ed|ing)\b|clear(?:s|ed|ing)?\b",
    "overwrite": r"overwrit\w*|replac\w*",
    "send": r"send\w*|sent\b|e-?mail\w*|messag\w*",
    "install": r"(?:un)?install\w*",
    "run": r"run(?:s|ning)?\b|execut\w*",
    "publish": r"publish\w*|push\w*|deploy\w*|post(?:s|ed|ing)?\b|releas\w*",
    "move": r"mov(?:e|es|ed|ing)\b|renam\w*",
}
_CONFIRM_FRAME = re.compile(
    r"(?i)^\W*(?:please\s+)?(?:confirm\b|are\s+you\s+sure|proceed\b|go\s+ahead\b|do\s+you\s+want(?:\s+me)?\s+to\b|"
    r"would\s+you\s+like\s+me\s+to\b|should\s+i\b|shall\s+i\b|can\s+i\b|may\s+i\b|"
    r"is\s+it\s+(?:ok|okay|alright|fine)\s+(?:to|if)\b|ok(?:ay)?\s+to\b)")
_YES = re.compile(r"(?i)^\W*(?:yes|yeah|yep|y|ok(?:ay)?|sure|proceed|go\s+ahead|confirm|do\s+it|delete|send|install)\b")
_NO = re.compile(r"(?i)^\W*(?:no|nope|n|cancel|don'?t|do\s+not|stop|keep|abort|not\s+now)\b")
_BEYOND = re.compile(r"(?i)\b(?:also|too|as\s+well|additionally|in\s+addition)\b")
_FILLER = {"the", "a", "an", "my", "your", "this", "that", "these", "those", "all", "of", "for", "me", "now", "go",
           "ahead", "and", "with", "from", "in", "to", "on", "project", "folder", "file", "files"}


def _stem(word: str) -> str:
    word = word.casefold().strip("`\"'.,:;!?()[]")
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def repeats_action_request(question: str, options: list[str], goal: str) -> bool:
    """A yes/no confirmation of an action the owner already asked for belongs on the tool's approval card (#179).

    Refused only when the options are a yes/no pair, the question is framed as a confirmation, it names a
    consequential action whose family the owner's request also uses, and its object is one the request named
    (file names included). A question about something beyond the request ("also delete the logs?") or a real
    choice ("Which file…?") is still asked.
    """
    choices = [str(o or "").strip() for o in options]
    if not (len(choices) <= 3 and any(_YES.match(o) for o in choices) and any(_NO.match(o) for o in choices)):
        return False
    q = str(question or "").strip()
    if not (_CONFIRM_FRAME.match(q) or re.search(r"(?i)\bconfirm\b", q)) or _BEYOND.search(q):
        return False
    wanted = str(goal or "").casefold()
    family = next((name for name, pattern in _ACTION_FAMILIES.items()
                   if re.search(rf"(?i)\b(?:{pattern})", q) and re.search(rf"(?i)\b(?:{pattern})", wanted)), "")
    if not family:
        return False
    goal_words = {_stem(w) for w in re.findall(r"[\w./-]+", wanted)}
    if any(name.casefold() not in wanted for name in re.findall(r"[\w-]+\.[\w]{1,6}\b", q)):
        return False   # a file the owner didn't name
    verb = re.search(rf"(?i)\b(?:{_ACTION_FAMILIES[family]})", q)
    following = [w for w in re.findall(r"[\w./-]+", q[verb.end():]) if _stem(w) not in _FILLER]
    obj = _stem(following[0]) if following else "it"
    return obj in {"it", "them", "that", "this"} or obj in goal_words


def permission_refusal(off: str) -> str:
    state = (f"Off right now: {off}." if off else
             "Nothing you need is switched off: an action that needs approval asks by itself when you call its tool.")
    return ("Not shown. A question card can't grant a permission or switch a capability on, and choosing an option "
            f"changes no setting. {state} Only the owner can change a switch: Settings › Permissions in the desktop app, "
            "or /permissions in the terminal. In your reply, say plainly what is off and where to turn it on, then "
            "offer only what you can do as things are. Don't ask for permission in a question again.")


def answer_caveat(answer: str) -> str:
    """Appended to an answer that reads like a grant ("yes, you have my permission")."""
    if _GRANT.search(answer or "") or re.search(r"(?i)\b(you have|i give|i grant)\b[^.]{0,20}\bpermission", answer or ""):
        return ("\n(EchoSpeak: this answer is a choice, not a permission. No switch changed; tools that were off "
                "are still off, and actions that need approval still ask.)")
    return ""


def ask_user_tool() -> NativeTool:
    # The loop answers this call itself (it needs the chat's pause); this body
    # only runs if something calls the toolbox directly.
    return NativeTool(
        name=ASK_USER,
        description=ASK_USER_DESCRIPTION,
        parameters=PARAMETERS,
        func=lambda _args: "Error: ask_user only works inside a chat in the EchoSpeak app.",
        always=True,
    )
