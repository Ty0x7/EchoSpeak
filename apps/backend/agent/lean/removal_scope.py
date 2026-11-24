"""Keep a removal within what the user asked to remove.

An ambiguous request ("delete the notes file" with notes-draft.md and notes-final.md present) was resolved by
deleting every candidate (qwen3.5:9b, `rm notes-draft.md notes-final.md`, #170). The approval card shows the
command, but nothing compared its targets with the request, and autonomous mode has no card at all.

What authorizes removing a file that existed when the request began:

- **The user's own words in this request.** The request is split into clauses. A clause asks for removal when it
  has a removal verb that isn't negated ("delete", "remove", "clean up"…). Its object is the text after the verb,
  up to the next clause or the next unrelated verb. A target is *named* when its name (or stem) is in a removal
  clause. A file mentioned in a keep, except or negated clause is *protected* and is never removed. Earlier turns
  and the assistant's own suggestions never authorize anything.
- **The user's selection on a question card in this request** (``ask_user``). The recorded answer names the targets.
  "Both"/"All" selects every file the card listed.
- **Collective intent.** A removal clause whose object has a quantifier, a number, a plural kind of file or a
  wildcard allows several unnamed targets. Each must still match the object's describing words ("all the .pyc
  files" covers a.pyc and b.pyc, not notes.md).
- **One unambiguous unnamed target.** A singular removal clause ("delete the log") allows one unnamed file only when
  it is the only file in its folder that matches the clause's describing words. "The notes file" with two notes
  files is ambiguous.

Files the agent created during this request are its own. Ownership comes from a record of every path and inode
that existed when the request began, not from modification times. A user's file the agent edited, rewrote or moved
is still the user's. A directory counts as the agent's only if everything inside it is.

Not checked from the arguments, so allowed only under collective intent: removals that don't list their targets
(`find -delete`, `git clean`, `xargs rm`, `rsync --delete`, inline-script deletions). The project folder and
its parents are never removed this way. Overwrites and scripts run from files are outside this check. Approvals,
the sandbox and the floor of always-asked commands still apply to everything allowed here.
"""
from __future__ import annotations

import glob
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

REMOVAL_TOOLS = frozenset({"file_delete"})
COMMAND_TOOLS = frozenset({"terminal", "terminal_run"})

_DIRECT = re.compile(r"(?:^|[\s;&|(])(?:sudo\s+)?(?:rm|rmdir|unlink|shred)(?=\s)", re.I)
_INDIRECT = re.compile(
    r"(?ix)"
    r"\bfind\b[^;&|]*\s-(?:delete\b|exec(?:dir)?\s+(?:rm|unlink|shred)\b)"
    r"|\bgit\s+(?:clean|rm)\b"
    r"|\bxargs\s+(?:-\S+\s+)*(?:rm|unlink|shred)\b"
    r"|\brsync\b[^;&|]*--delete"
    r"|\bos\.(?:remove|unlink|rmdir|removedirs)\s*\(|\bshutil\.rmtree\s*\(|\.unlink\s*\(|\.rmdir\s*\("
    r"|\bfs\.(?:rm|rmSync|unlink|unlinkSync|rmdir|rmdirSync)\b|\bRemove-Item\b|\bdel\s+/[sq]"
)
_SEGMENT_SPLIT = re.compile(
    r"\.(?=\s|$)|[;!?\n,]+|(?=\b(?:but|except|other than|apart from|without|then)\b)|"
    r"\band\s+(?=(?:keep|leave|don'?t|do not|never|fix|run|update|add|write|create|make|check|test|commit|push|"
    r"tell|show|build|rename|move|copy|install|open|read|summari[sz]e|explain|save|send|deploy)\b)",
    re.I,
)
_REMOVE_VERB = re.compile(
    r"\b(?:delete|deleting|remove|removing|erase|wipe|purge|trash|discard|rm|unlink|destroy|"
    r"get\s+rid\s+of|clean(?:\s+up|\s+out)?|clear(?:\s+out)?)\b",
    re.I,
)
_OTHER_START = re.compile(
    r"^\s*(?:then\s+)?(?:fix|run|update|add|write|create|make|check|test|commit|push|tell|show|build|rename|move|"
    r"copy|install|open|read|summari[sz]e|explain|save|send|deploy|list)\b", re.I)
_NEGATION = re.compile(r"\b(?:don'?t|do\s+not|never|not|no\s+need\s+to|shouldn'?t|without)\b", re.I)
_PROTECT_START = re.compile(
    r"^\s*(?:but\s+)?(?:keep|leave|preserve|retain|spare|except|other than|apart from|without|not|don'?t|do not|never)\b",
    re.I)
_QUANTIFIER = re.compile(
    r"\b(?:all|every|each|both|any|these|those|everything|entire|whole|multiple|several|many|"
    r"two|three|four|five|six|seven|eight|nine|ten|[2-9]|\d{2,})\b|[*?]", re.I)
_PLURAL_KIND = re.compile(
    r"\b(?:files|folders|directories|dirs|documents|copies|duplicates|backups|logs|artifacts|caches|outputs|"
    r"images|screenshots|scripts|drafts|temporaries|temp\s+files)\b", re.I)
_SELECT_ALL = re.compile(r"^\s*(?:both|all|all of them|everything|both of them|all of these)\s*[.!]?\s*$", re.I)
_FILLER = frozenset("""a an the this that these those my our your their its it them one ones file files folder
folders directory directories dir dirs project repo repository workspace here there from in of on at to for with
and or please also just only all every each both any everything entire whole multiple several many some old
item items thing things document documents""".split())
_WORD = re.compile(r"[a-z0-9]+")


# ── what the request asks for ──────────────────────────────────────────

@dataclass
class Intent:
    removal: bool = False  # a non-negated removal clause exists
    collective: bool = False  # a removal clause's object covers several things
    objects: list[str] = field(default_factory=list)  # the removal clauses' objects
    named_text: str = ""  # text that may name targets: removal clauses and the user's card selections
    protected_text: str = ""  # keep / except / negated clauses
    selected_all: bool = False  # the user picked "both"/"all" on a card
    exact_only: bool = False  # a typed selection: only exact file names count


def intent(request: str, selections: Iterable[tuple[str, list[str], str]] = (), previous_request: str = "") -> Intent:
    """Read the request's removal clauses, and the user's card selections ``(question, options, answer)``.

    A reply without a removal clause, after the user's own previous message asked for a removal, is a selection:
    it names targets by their exact names and nothing else ("notes-draft.md" yes; "yes" or "both" no). The
    assistant's messages never count.
    """
    result = Intent()
    if previous_request and not _REMOVE_VERB.search(request or "") and intent(previous_request).removal:
        result.removal = True
        result.named_text = request or ""
        result.exact_only = True
    current = None  # the kind of the previous clause, inherited by a verbless continuation ("…, and the logs")
    for raw in _SEGMENT_SPLIT.split(request or ""):
        segment = raw.strip()
        if not segment:
            continue
        verb = _REMOVE_VERB.search(segment)
        if _PROTECT_START.match(segment) or (verb and _NEGATION.search(segment[:verb.start()])):
            current = "protect"
        elif verb:
            current = "remove"
        elif _OTHER_START.match(segment):
            current = None
        if current == "protect":
            result.protected_text += "\n" + segment
        elif current == "remove":
            obj = segment[verb.end():] if verb else segment
            result.removal = True
            result.objects.append(obj)
            result.named_text += "\n" + obj
            if _QUANTIFIER.search(obj) or _PLURAL_KIND.search(obj):
                result.collective = True
    for question, options, answer in selections:
        if not answer:
            continue
        result.removal = True
        if _SELECT_ALL.match(answer):
            result.selected_all = True
            result.named_text += "\n" + "\n".join([question, *options])
        else:
            result.named_text += "\n" + answer
    return result


# Kinds of file people name by type ("the markdown files") and the extensions they mean.
_KIND_EXTENSIONS = {
    "markdown": {"md", "markdown"}, "python": {"py"}, "javascript": {"js", "mjs", "cjs"}, "typescript": {"ts", "tsx"},
    "text": {"txt"}, "yaml": {"yaml", "yml"}, "image": {"png", "jpg", "jpeg", "gif", "webp", "svg"},
    "picture": {"png", "jpg", "jpeg", "gif", "webp"}, "spreadsheet": {"csv", "xlsx", "ods"}, "temp": {"tmp"},
    "temporary": {"tmp"}, "backup": {"bak"}, "pdf": {"pdf"}, "json": {"json"}, "csv": {"csv"}, "log": {"log"},
}


def describing_words(text: str) -> set[str]:
    words = {w for w in _WORD.findall(text.lower()) if w not in _FILLER and not w.isdigit()}
    words |= {w[:-1] for w in words if len(w) > 3 and w.endswith("s")}
    return words | {ext for w in list(words) for ext in _KIND_EXTENSIONS.get(w, ())}


def _tokens(path: Path) -> set[str]:
    return set(_WORD.findall(path.name.lower()))


def mentioned(path: Path, text: str) -> bool:
    """The text mentions this file or folder: its name, or its stem as a whole word."""
    lowered = (text or "").lower()
    name = path.name.lower()
    if name and re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w-])", lowered):
        return True
    stem = path.stem.lower()
    return len(stem) >= 3 and re.search(rf"(?<![\w.-]){re.escape(stem)}(?![\w.-])", lowered) is not None


def exact_named(path: Path, text: str) -> bool:
    """The text names this file by its full name."""
    return re.search(rf"(?<![\w.-]){re.escape(path.name.lower())}(?![\w-])", (text or "").lower()) is not None


# ── what existed when the request began ────────────────────────────────

_SNAPSHOT_LIMIT = 50_000
_UNSCANNED = frozenset({".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".cache"})


@dataclass
class Provenance:
    """Every path and inode present under the project when the request began."""

    root: Path | None = None
    paths: set[str] = field(default_factory=set)
    inodes: set[tuple[int, int]] = field(default_factory=set)
    complete: bool = False

    @classmethod
    def snapshot(cls, root: Path | None) -> "Provenance":
        record = cls(root=root.resolve() if root else None)
        if record.root is None or not record.root.is_dir():
            return record
        count = 0
        for current, dirs, files in os.walk(record.root):
            dirs[:] = [d for d in dirs if d not in _UNSCANNED]
            for name in [*dirs, *files]:
                path = os.path.join(current, name)
                try:
                    st = os.lstat(path)
                except OSError:
                    continue
                record.paths.add(path)
                record.inodes.add((st.st_dev, st.st_ino))
                count += 1
                if count >= _SNAPSHOT_LIMIT:
                    return record
        record.complete = True
        return record

    def created(self, path: Path) -> bool:
        """The agent made this during the request: it is new by path and inode, inside the recorded project.

        A directory is the agent's only if everything inside it is too. Anything the snapshot couldn't cover
        (outside the project, a skipped folder, a project larger than the limit) belongs to the user.
        """
        if not self.complete or self.root is None:
            return False
        try:
            path.relative_to(self.root)
        except ValueError:
            return False
        if any(part in _UNSCANNED for part in path.relative_to(self.root).parts):
            return False
        if not self._new(path):
            return False
        if path.is_dir() and not path.is_symlink():
            for count, inner in enumerate(path.rglob("*")):
                if count >= 2000 or not self._new(inner):
                    return False
        return True

    def _new(self, path: Path) -> bool:
        try:
            st = os.lstat(path)
        except OSError:
            return False
        return str(path) not in self.paths and (st.st_dev, st.st_ino) not in self.inodes


# ── what a call would remove ───────────────────────────────────────────

@dataclass
class Removal:
    targets: list[Path] = field(default_factory=list)  # existing paths the call names
    indirect: bool = False  # removes things its arguments don't list
    project: bool = False  # names the project folder or one of its parents


def removal(name: str, args: dict[str, Any], root: Path) -> Removal | None:
    """What this call would remove, or None when it removes nothing."""
    root = root.resolve()
    if name in REMOVAL_TOOLS:
        return _resolve([str(args.get("path") or args.get("file_path") or "")], root, root)
    if name not in COMMAND_TOOLS:
        return None
    command = str(args.get("command") or args.get("cmd") or "")
    direct, indirect = bool(_DIRECT.search(command)), bool(_INDIRECT.search(command))
    if not (direct or indirect):
        return None
    cwd = Path(str(args.get("cwd") or root)).expanduser()
    cwd = cwd if cwd.is_absolute() else root / cwd
    found = Removal(indirect=indirect)
    if direct:
        try:
            tokens = shlex.split(command, comments=True)
        except ValueError:
            tokens = command.split()
        words: list[str] = []
        take = False
        for token in tokens:
            if token in {";", "&&", "||", "|", "&"}:
                take = False
                continue
            word = token.rstrip(";&|")
            base = os.path.basename(word)
            if base in {"rm", "rmdir", "unlink", "shred"}:
                take = True
            elif word != "sudo" and take and word and not word.startswith("-"):
                words.append(word)
            if word != token:
                take = False
        resolved = _resolve(words, root, cwd)
        found.targets, found.project = resolved.targets, resolved.project
        # Operands it can't name (a variable, a substitution), or a changed folder (`cd x && rm y`), remove
        # things the arguments don't identify here.
        if any(("$" in w or "`" in w) for w in words) or re.search(r"(?:^|[\s;&|(])(?:cd|pushd)\s", command):
            found.indirect = True
    return found


def _resolve(words: list[str], root: Path, cwd: Path) -> Removal:
    found = Removal()
    seen: set[Path] = set()
    protected = {root, *root.parents}
    for word in words:
        if not word.strip():
            continue
        pattern = os.path.expanduser(word)
        pattern = pattern if os.path.isabs(pattern) else str(cwd / pattern)
        matches = glob.glob(pattern, include_hidden=False) if glob.has_magic(pattern) else [pattern]
        for match in matches:
            path = Path(os.path.normpath(match))
            if path in protected or path.resolve() in protected:
                found.project = True
                continue
            if (path.exists() or path.is_symlink()) and path not in seen:
                seen.add(path)
                found.targets.append(path)
    return found


# ── the decision ───────────────────────────────────────────────────────

def _ambiguous(target: Path, words: set[str], provenance: Provenance) -> list[Path]:
    """Other pre-existing files beside ``target`` that the same describing words fit."""
    try:
        siblings = [p for p in target.parent.iterdir() if p != target and not provenance.created(p)]
    except OSError:
        return []
    if not words:
        return siblings
    return [p for p in siblings if _tokens(p) & words]


def problem(call: Removal, request: Intent, provenance: Provenance, already: set[Path],
            root: Path | None = None) -> tuple[str, set[Path]]:
    """Why this removal isn't run (or ""), and the user's unnamed targets it uses up when it goes ahead."""

    def show(paths: Iterable[Path]) -> str:
        out = []
        for path in sorted(paths):
            try:
                out.append(str(path.relative_to(root)) if root else path.name)
            except ValueError:
                out.append(str(path))
        return ", ".join(out)

    ask = " Ask the user which ones they mean, and remove only what they choose."
    if call.project:
        return ("Not run: this would remove the project folder or a folder containing it. That is never done as "
                "part of a request. Tell the user the exact command if they want to run it themselves."), set()
    users = [t for t in call.targets if not provenance.created(t)]
    kept = [t for t in users if mentioned(t, request.protected_text)]
    if kept:
        return (f"Not run: the user asked to keep {show(kept)}. Don't remove it in any other way."), set()
    if call.indirect:
        if request.collective or request.selected_all:
            return "", set()
        return ("Not run: this command removes files its arguments don't list, so it can't be checked against "
                "what the user asked for. Remove the specific files they asked about with `rm <file>`, or ask "
                "them to confirm the files." + ask), set()
    unnamed = [t for t in users if not (request.selected_all or (exact_named(t, request.named_text)
                                                                 if request.exact_only else
                                                                 mentioned(t, request.named_text)))]
    if not unnamed:
        return "", set()
    if not request.removal:
        return (f"Not run: this would remove {show(unnamed)}, which existed before this request, and the user "
                "didn't ask to remove anything." + ask), set()
    words = set().union(*(describing_words(o) for o in request.objects)) if request.objects else set()
    if request.collective:
        off = [t for t in unnamed if words and not (_tokens(t) & words or t.suffix.lstrip(".").lower() in words)]
        if off:
            return (f"Not run: {show(off)} doesn't match what the user asked to remove." + ask), set()
        return "", set(unnamed)
    total = already | set(unnamed)
    if len(total) > 1:
        return (f"Not run: this request asked to remove one item without naming it, and this would remove more "
                f"than one existing file or folder it didn't name ({show(total)})." + ask +
                " Don't remove them one at a time instead."), set()
    target = unnamed[0]
    if target.is_dir() and not target.is_symlink() and any(target.iterdir()):
        return (f"Not run: {show([target])} is a folder with the user's files in it, and the user didn't name it."
                + ask), set()
    if words and not (_tokens(target) & words):
        return (f"Not run: {show([target])} doesn't match what the user asked to remove." + ask), set()
    others = _ambiguous(target, words, provenance)
    if others:
        return (f"Not run: the request could mean more than one file ({show([target, *others])})." + ask), set()
    return "", {target}
