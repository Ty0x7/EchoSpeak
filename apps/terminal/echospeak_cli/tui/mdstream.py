"""Streaming markdown, committed block by block (after Codex ``markdown_stream.rs``, at block level).

Text accumulates per message. A block is *closed* by a blank line outside a code fence, or by the
closing fence itself; closed blocks go to scrollback as rendered markdown. The open remainder is
shown as plain text in the live tail until it closes or the message ends.
"""
from __future__ import annotations

import re

_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")


class MdStream:
    def __init__(self) -> None:
        self.buf = ""            # uncommitted text
        self.total = ""          # everything fed (for /copy and the transcript)

    def feed(self, text: str) -> list[str]:
        """Append text; return the blocks that just closed (possibly none)."""
        if not text:
            return []
        self.buf += text
        self.total += text
        return self._commit()

    def _commit(self) -> list[str]:
        # Only whole lines can close a block.
        end = self.buf.rfind("\n")
        if end < 0:
            return []
        lines = self.buf[: end + 1].split("\n")[:-1]
        fence: str = ""
        cut = -1                 # index (in lines) after which everything is committed
        prev_blank = True
        for i, line in enumerate(lines):
            m = _FENCE.match(line)
            if fence:
                if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not line.strip()[len(m.group(1)):].strip():
                    fence = ""
                    cut = i
                continue
            if m:
                fence = m.group(1)
                continue
            if not line.strip():
                if not prev_blank:
                    cut = i
                prev_blank = True
            else:
                prev_blank = False
        if cut < 0:
            return []
        committed_lines = lines[: cut + 1]
        consumed = sum(len(x) + 1 for x in committed_lines)
        block = "\n".join(committed_lines).strip("\n")
        self.buf = self.buf[consumed:]
        # Blank lines right after the cut belong to nothing; drop them so the tail starts clean.
        self.buf = self.buf.lstrip("\n")
        return [block] if block.strip() else []

    def tail(self) -> str:
        return self.buf

    def flush(self) -> list[str]:
        block = self.buf.strip("\n")
        self.buf = ""
        return [block] if block.strip() else []

    def in_fence(self) -> bool:
        fence = ""
        for line in self.buf.split("\n"):
            m = _FENCE.match(line)
            if fence:
                if m and m.group(1)[0] == fence[0]:
                    fence = ""
            elif m:
                fence = m.group(1)
        return bool(fence)
