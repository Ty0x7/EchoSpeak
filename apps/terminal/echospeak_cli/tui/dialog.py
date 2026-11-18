"""Dialogs that replace the composer: approvals, questions, confirmations and pickers (spec §3.7).

The model here is pure: options, selection, the 300 ms input guard and key handling return an
``Action``; the app turns actions into backend calls. Nothing ever defaults to allow: an approval
opens with "Yes, allow once" highlighted only as a cursor position, and Enter during the guard
window does nothing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..render import one_line

GUARD_S = 0.3


@dataclass
class Option:
    label: str
    value: Any
    key: str = ""             # shortcut letter shown as "(y)"
    hint: str = ""            # dim text after the label
    text_entry: bool = False  # choosing it opens an inline text field
    style: str = ""           # "" | dim | attention


@dataclass
class Action:
    kind: str                 # choose | text | cancel | none
    value: Any = None
    text: str = ""


def approval_options(evt: dict, agent: str = "Echo") -> list[Option]:
    """Exactly the decisions ``POST /lean/approvals/{id}`` accepts for this item (api/routes/lean.py).

    1 allow once · 2 always (only if grantable) · 3 persist (only if persistable with a scope label)
    · 4 deny · 5 deny after telling the agent what to do instead. Recovered approvals: 1, 4, 5.
    """
    opts = [Option("Yes, allow once", "allow", "y")]
    recovered = bool(evt.get("recovered"))
    if not recovered and evt.get("grantable", True):
        opts.append(Option(f"Yes, and allow {_scope_noun(evt)} for this chat", "always", "c"))
    if not recovered and evt.get("persistable") and str(evt.get("scope_label") or "").strip():
        opts.append(Option(f"Yes, always allow: {one_line(evt.get('scope_label'), 80)}", "persist", "a"))
    opts.append(Option("No", "deny", "n"))
    opts.append(Option(f"No, and tell {agent} what to do instead", "steer_deny", "esc", text_entry=True))
    return opts


def _scope_noun(evt: dict) -> str:
    tool = str(evt.get("tool") or "")
    if tool.startswith("read:"):
        # A read-only folder grant (backend agent/lean/file_access.py): the card names the folder it opens.
        return f"reading {one_line(tool[5:], 60)}"
    if tool in {"terminal", "terminal_run", "process_start"}:
        return "terminal commands"
    if tool.startswith("file_"):
        return "file changes"
    return "this action"


def question_options(evt: dict) -> list[Option]:
    opts = [Option(one_line(o, 200), str(o)) for o in (evt.get("options") or [])]
    if evt.get("allow_other") or not opts:
        opts.append(Option("Other (type your answer)" if opts else "Type your answer", None, text_entry=True))
    return opts


@dataclass
class Dialog:
    title: str
    options: list[Option]
    body: list[tuple[str, str]] = field(default_factory=list)   # (style class, text) lines
    footer: str = ""
    selected: int = 0              # -1: nothing highlighted, Enter does nothing
    opened_at: float = 0.0
    guard: float = GUARD_S
    text_mode: bool = False        # the inline text field is open
    text_prompt: str = ""
    attention: bool = True
    kind: str = "approval"         # approval | question | confirm | picker
    payload: Any = None
    on_result: Optional[Callable[[Action], Any]] = None
    escape_value: Any = "__cancel__"   # what a bare Esc means (approvals: open the steer field)
    full_text: str = ""            # ctrl+g shows this in the pager
    filter_text: str = ""          # pickers: typed filter
    filterable: bool = False
    change: Any = None             # approvals for file tools: the proposed diffview.Change
    diff_top: int = 0              # first diff row shown (pgup/pgdn)

    def scroll_diff(self, delta: int, total: int, page: int) -> None:
        """PgUp/PgDn in the diff; never moves the option cursor and is never a decision."""
        self.diff_top = max(0, min(max(0, total - page), self.diff_top + delta))

    def visible_options(self) -> list[tuple[int, Option]]:
        if not self.filter_text:
            return list(enumerate(self.options))
        f = self.filter_text.lower()
        hits = [(i, o) for i, o in enumerate(self.options) if f in (o.label + " " + o.hint).lower()]
        return hits

    def guarded(self, now: float) -> bool:
        return now - self.opened_at < self.guard

    def move(self, delta: int) -> None:
        vis = self.visible_options()
        if not vis:
            return
        idxs = [i for i, _ in vis]
        if self.selected not in idxs:
            # Nothing highlighted yet (approvals open that way): the first arrow lands on an end.
            self.selected = idxs[0] if delta > 0 else idxs[-1]
            return
        pos = idxs.index(self.selected)
        self.selected = idxs[(pos + delta) % len(idxs)]

    def choose(self, index: int) -> Action:
        if not (0 <= index < len(self.options)):
            return Action("none")
        self.selected = index
        opt = self.options[index]
        if opt.text_entry:
            self.text_mode = True
            return Action("none")
        return Action("choose", opt.value)

    def key(self, key: str, now: float) -> Action:
        """Keys outside the text field. Digits pick directly, letters are shortcuts, Esc cancels."""
        if self.text_mode:
            return Action("none")
        if key in {"escape"}:
            if self.escape_value == "__steer__":
                for i, o in enumerate(self.options):
                    if o.text_entry:
                        self.selected = i
                        self.text_mode = True
                        return Action("none")
            return Action("cancel", self.escape_value)
        if key in {"up", "c-p"}:
            self.move(-1)
            return Action("none")
        if key in {"down", "c-n"}:
            self.move(1)
            return Action("none")
        if self.guarded(now):
            return Action("none")   # typed ahead of the dialog: never acted on
        if key == "enter":
            vis = [i for i, _ in self.visible_options()]
            if self.selected not in vis:
                return Action("none")
            return self.choose(self.selected)
        if self.filterable:
            return Action("none")
        if key.isdigit() and key != "0":
            n = int(key) - 1
            return self.choose(n) if n < len(self.options) else Action("none")
        for i, o in enumerate(self.options):
            if o.key and o.key == key.lower() and len(key) == 1:
                return self.choose(i)
        return Action("none")

    def submit_text(self, text: str) -> Action:
        """Enter in the inline field. Empty text on 'tell Echo' is a plain deny."""
        opt = self.options[self.selected] if 0 <= self.selected < len(self.options) else None
        self.text_mode = False
        if opt is None:
            return Action("none")
        if opt.value == "steer_deny":
            return Action("choose", "deny" if not text.strip() else "steer_deny", text.strip())
        if not text.strip():
            return Action("none")
        return Action("text", opt.value, text.strip())

    def cancel_text(self) -> Action:
        """Esc inside the field: for approvals a second Esc is a plain deny."""
        self.text_mode = False
        opt = self.options[self.selected] if 0 <= self.selected < len(self.options) else None
        if opt is not None and opt.value == "steer_deny":
            return Action("choose", "deny")
        return Action("none")
