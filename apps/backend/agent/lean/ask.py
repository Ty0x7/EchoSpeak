"""ask_user: the agent asks the person a short question with a few choices.

It reuses the approval pause (agent/lean/approvals.py): the run waits until the
person picks an option or types an answer in the app, and the answer comes back
as the tool's result. Meant for when the agent is blocked (a tool keeps failing,
setup is missing) or when a choice is genuinely the user's. Not for things the
agent can decide or look up itself.
"""

from __future__ import annotations

from typing import Any

from agent.lean.toolbox import NativeTool

ASK_USER = "ask_user"

ASK_USER_DESCRIPTION = (
    "Ask the user a short question with 2-4 concrete choices and wait for their answer. "
    "Use it when you are blocked (a tool keeps failing, something isn't set up) or when the choice is "
    "genuinely theirs (which option, whether to go ahead). Don't use it for things you can decide or look up. "
    "Each option is a short action, e.g. 'Read a news site directly', 'Set up a search key', 'Skip it'."
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
