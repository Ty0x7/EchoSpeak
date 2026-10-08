"""Turn a chat that went wrong into a replay case (tests/replays/<name>.json).

    python scripts/replay_capture.py --chat <session id> --name search-broken-ai-news \
        --tools-any safe_web_fetch,ask_user --reply-excludes "I'm sorry"

It saves the user's message, the tool calls and their outputs, and what the
model said, plus the expectations you give. Edit the JSON afterwards to add
"fixed_turns" (a good run) and tidy any private details out of the outputs.
Set ECHOSPEAK_DATA_DIR if your chats live somewhere other than the default.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _split(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def main(argv: list[str] | None = None) -> int:
    from agent.lean import replays

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--chat", required=True, help="The chat's session id (from the app's URL or history)")
    parser.add_argument("--name", required=True, help="Case name, e.g. search-broken-ai-news")
    parser.add_argument("--turn", default="", help="A specific turn id (default: the latest)")
    parser.add_argument("--tools-any", default="", help="At least one of these tools should be called")
    parser.add_argument("--tools-none", default="", help="None of these tools should be called")
    parser.add_argument("--reply-excludes", action="append", default=[], help="A phrase the reply must not contain (repeatable)")
    parser.add_argument("--reply-includes-any", default="", help="The reply should mention one of these (comma-separated)")
    args = parser.parse_args(argv)

    expect = {key: value for key, value in {
        "tools_any": _split(args.tools_any),
        "tools_none": _split(args.tools_none),
        "reply_excludes": list(args.reply_excludes),
        "reply_includes_any": _split(args.reply_includes_any),
    }.items() if value}
    if not expect:
        print("Give at least one expectation (--tools-any, --tools-none, --reply-excludes, --reply-includes-any).")
        return 1
    case = replays.capture(args.chat, args.name, expect, turn_id=args.turn)
    path = replays.save_case(case)
    print(f"Saved {path}")
    print("Check the tool outputs for private details, and add \"fixed_turns\" if you can write a good run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
