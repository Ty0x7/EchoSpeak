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
- **The user's selection on a question card in this request** (``ask_user``), when the question is about removing
  something. The recorded answer names the targets by their full names. "Both"/"All" selects exactly the files that
  question presented, and nothing else. An answer to any other question selects nothing.
- **Collective intent.** A removal clause whose object has a quantifier, a number, a plural kind of file or a
  wildcard allows several unnamed targets. Each must still match the object's describing words ("all the .pyc
  files" covers a.pyc and b.pyc, not notes.md).
- **One unambiguous unnamed target.** A singular removal clause ("delete the log") allows one unnamed file only when
  it is the only file in its folder that matches the clause's describing words. "The notes file" with two notes
  files is ambiguous.

Files the agent created during this request are its own. Ownership comes from a record of every path and inode
that existed when the request began, not from modification times. A user's file the agent edited, rewrote or moved
is still the user's. A directory counts as the agent's only if everything inside it is.

Removals that don't list their targets are resolved where a read-only listing reproduces them exactly: a single
`find` with selecting tests and exactly one action (`-delete`, or `-exec rm|unlink [flags] {}`) is listed with that
action replaced *in place* by `-print0`, since precedence makes its position matter; `git clean` with one `-f` and
without `-X` is listed with `git clean -n`. A recursive removal (`rm -r`, `-exec rm -r`, `git clean -d`) is checked
file by file inside each folder unless the user named the folder. A listing that reports any error, and anything the
shell would expand first ($VAR, ~, braces, escapes other than find's own), isn't resolved. The listed targets then go
through every check above. Anything else (`xargs rm`, `rsync --delete`, inline-script deletions,
`sh -c '…rm…'`, `eval`, `cd … && rm`, variables, other `find` actions or options, `git clean -ff`/`-X`) runs only if
the user's own words, or their card answer, contain that exact command. The project folder and its parents are
never removed this way.

Limits: overwriting or truncating a file, and scripts run from files (`python cleanup.py`), are outside this check.
A resolved listing reflects the moment of the check; the listing runs read-only `find`, or `git` with the
repository's program-running settings overridden and global configuration ignored (so it can list more than the
real command, never less), on the host with a 5-second limit. The command itself still runs in the sandbox. Clause parsing is heuristic and English-only; when it misreads,
the agent asks. Approvals, the sandbox and the floor of always-asked commands still apply to everything allowed
here.
"""
from __future__ import annotations

import glob
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

REMOVAL_TOOLS = frozenset({"file_delete"})
COMMAND_TOOLS = frozenset({"terminal", "terminal_run"})

# A removal command anywhere in the text, including inside a quoted script (`sh -c 'rm "$0"'`).
_DIRECT = re.compile(r"(?<![\w./-])(?:rm|rmdir|unlink|shred)(?=[\s'\"]|$)", re.I)
_INDIRECT = re.compile(
    r"(?ix)"
    r"\bfind\b[^;&|]*\s-(?:delete\b|exec(?:dir)?\s+(?:rm|unlink|shred)\b)"
    r"|\bgit\s+clean\b"
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
    named_text: str = ""  # text that may name targets: this request's removal clauses
    protected_text: str = ""  # keep / except / negated clauses
    selected_text: str = ""  # the user's selections: they authorize exact file names (and exact commands) only
    request_text: str = ""  # the request as the user wrote it


def intent(request: str, selections: Iterable[tuple[str, list[str], str]] = (), previous_request: str = "") -> Intent:
    """Read the request's removal clauses, and the user's card selections ``(question, options, answer)``.

    A reply without a removal clause, after the user's own previous message asked for a removal, is a selection:
    it names targets by their exact names and nothing else ("notes-draft.md" yes; "yes" or "both" no). The
    assistant's messages never count.
    """
    result = Intent(request_text=request or "")
    if previous_request and not _REMOVE_VERB.search(request or "") and intent(previous_request).removal:
        result.removal = True
        result.selected_text = request or ""
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
        # Only an answer to a question about removing something selects what to remove. "Both"/"All" selects
        # exactly the files that question presented, by their full names, and nothing else.
        if not answer or not _REMOVE_VERB.search(question):
            continue
        result.removal = True
        if _SELECT_ALL.match(answer):
            presented = [o for o in options if not _SELECT_ALL.match(o)]
            result.selected_text += "\n" + "\n".join([question, *presented])
        else:
            result.selected_text += "\n" + answer
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


def _contains_command(text: str, command: str) -> bool:
    """The text contains this exact command (whitespace aside)."""
    squash = lambda s: " ".join((s or "").split()).strip("`'\" ")  # noqa: E731
    return bool(command.strip()) and squash(command) in squash(text)


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
    targets: list[Path] = field(default_factory=list)  # existing paths the call would remove
    indirect: bool = False  # removes things its arguments don't list, and they couldn't be resolved
    project: bool = False  # names the project folder or one of its parents
    resolved_by: str = ""  # how an indirect removal's targets were listed ("find", "git clean -n")
    command: str = ""
    attributes: frozenset[str] = frozenset()  # what every resolved target is by construction ("untracked")
    recursive: bool = False  # removes folders with everything inside them (rm -r, find -exec rm -r)


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
    found = Removal(indirect=indirect, command=command)
    if indirect:
        # Resolve what it would remove where that can be done by listing alone; otherwise it stays unresolved.
        listed = _list_indirect(command, root, cwd)
        if listed is not None:
            listed.command = command
            return listed
        return found
    if direct:
        try:
            tokens = shlex.split(command, comments=True)
        except ValueError:
            tokens = command.split()
        words: list[str] = []
        take = False
        remover = ""
        for token in tokens:
            if token in {";", "&&", "||", "|", "&"}:
                take = False
                continue
            word = token.rstrip(";&|")
            base = os.path.basename(word)
            if base in {"rm", "rmdir", "unlink", "shred"}:
                take = True
                remover = base
            elif word != "sudo" and take and word and not word.startswith("-"):
                words.append(word)
            elif take and remover == "rm" and _RECURSIVE_FLAG.fullmatch(word):
                found.recursive = True
            if word != token:
                take = False
        resolved = _resolve(words, root, cwd)
        found.targets, found.project = resolved.targets, resolved.project
        found.recursive = found.recursive and any(p.is_dir() and not p.is_symlink() for p in found.targets)
        if not any(os.path.basename(tok.rstrip(";&|")) in {"rm", "rmdir", "unlink", "shred"} for tok in tokens):
            if not _INTERPRETER.search(command):
                return None  # only text mentioning a removal (`grep "rm -rf" notes.md`): nothing is removed
            # The removal is inside a script a shell will run (`sh -c 'rm …'`, `eval`): its operands aren't listable.
            found.indirect = True
        # Operands it can't name (a variable, a substitution), or a changed folder (`cd x && rm y`), remove
        # things the arguments don't identify here.
        if any(("$" in w or "`" in w) for w in words) or re.search(r"(?:^|[\s;&|(])(?:cd|pushd)\s", command):
            found.indirect = True
    return found


_RM_FLAG = re.compile(r"-[frRvd]+|--(?:force|recursive|verbose|dir)")
_RECURSIVE_FLAG = re.compile(r"-[a-zA-Z]*[rR][a-zA-Z]*|--recursive")

# Something that runs a quoted string as commands.
_INTERPRETER = re.compile(r"(?<![\w./-])(?:sh|bash|zsh|dash|ksh|fish|eval|xargs|su|doas|sudo|nohup|watch|timeout|env)"
                          r"(?![\w-])")

# `find` tests that only select (no actions, no command execution), with how many values each takes.
_FIND_TESTS = {"-name": 1, "-iname": 1, "-path": 1, "-ipath": 1, "-wholename": 1, "-regex": 1, "-iregex": 1,
               "-type": 1, "-maxdepth": 1, "-mindepth": 1, "-size": 1, "-mtime": 1, "-mmin": 1, "-atime": 1,
               "-amin": 1, "-ctime": 1, "-cmin": 1, "-newer": 1, "-empty": 0, "-not": 0, "!": 0, "-a": 0,
               "-and": 0, "-o": 0, "-or": 0, "(": 0, ")": 0, "-depth": 0, "-xdev": 0, "-mount": 0}
_LIST_TIMEOUT = 5


def _list_indirect(command: str, root: Path, cwd: Path) -> Removal | None:
    """The targets of a single `find … -delete` / `find … -exec rm {} +|;` or `git clean` command, listed by
    running its read-only equivalent (`find … -print0`, `git clean -n`). None when that can't be done safely."""
    # One command only: no other commands, pipes, redirections or substitutions, apart from the terminator
    # of `find -exec … {} \;` or `{} +` at the very end.
    body = re.sub(r"""\s(?:\\;|';'|";"|\+)\s*$""", "", command)
    if re.search(r"[;&|<>`\n]|\$\(", body):
        return None
    # Anything the shell would expand or rewrite before the command runs ($VAR, ${...}, ~, {a,b}, backslash
    # escapes other than find's own grouping and terminator) isn't reproduced by a listing that runs without a
    # shell, so it isn't resolved. `{}` is find's placeholder.
    if re.search(r"[$~]|\\(?![();])|\{(?!\})|(?<!\{)\}", body):
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    try:
        cwd.resolve().relative_to(root)
    except ValueError:
        return None
    if tokens[:1] == ["find"]:
        return _list_find(tokens, root, cwd)
    if tokens[:2] == ["git", "clean"]:
        return _list_git_clean(tokens, root, cwd)
    return None


def _list_find(tokens: list[str], root: Path, cwd: Path) -> Removal | None:
    """List with the same expression, the one action replaced in place by `-print0`.

    Precedence makes the action's position matter (`find . -delete -name x` removes everything), so the listing
    keeps it. Exactly one action is allowed; global options (-L, -H…), other actions and unknown tests aren't.
    """
    paths, rest = [], tokens[1:]
    while rest and not rest[0].startswith("-") and rest[0] not in {"!", "(", ")"}:
        paths.append(rest.pop(0))
    if any(glob.has_magic(path) for path in paths):
        return None
    listing: list[str] = []
    actions = 0
    recursive = False
    i = 0
    while i < len(rest):
        token = rest[i]
        if token == "-delete":
            actions += 1
            listing.append("-print0")
            i += 1
            continue
        if token in {"-exec", "-execdir"}:
            end = next((j for j in range(i + 1, len(rest)) if rest[j] in {";", "+"}), None)
            body = rest[i + 1:end] if end is not None else []
            flags = body[1:-1]
            if not body or os.path.basename(body[0]) not in {"rm", "unlink"} or body[-1:] != ["{}"] \
                    or any(not _RM_FLAG.fullmatch(f) for f in flags):
                return None
            recursive = recursive or any(_RECURSIVE_FLAG.fullmatch(f) for f in flags)
            actions += 1
            listing.append("-print0")
            i = end + 1
            continue
        if token not in _FIND_TESTS:
            return None
        count = _FIND_TESTS[token]
        listing.extend(rest[i:i + 1 + count])
        i += 1 + count
    if actions != 1:
        return None
    for path in paths or ["."]:
        target = Path(path) if os.path.isabs(path) else cwd / path
        try:
            target.resolve().relative_to(root)
        except ValueError:
            return None
    try:
        listed = subprocess.run(["find", *(paths or ["."]), *listing], cwd=cwd, capture_output=True,
                                timeout=_LIST_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if listed.returncode or listed.stderr.strip():
        return None  # an incomplete listing (unreadable folders, errors) isn't a listing
    names = [n for n in listed.stdout.decode(errors="surrogateescape").split("\0") if n]
    found = _resolve(names, root, cwd)
    found.resolved_by, found.recursive = "find", recursive
    return found


def _list_git_clean(tokens: list[str], root: Path, cwd: Path) -> Removal | None:
    flags, paths, after = [], [], False
    forces = sum(t.count("f") for t in tokens[2:] if t.startswith("-") and not t.startswith("--")) + \
        sum(t == "--force" for t in tokens[2:])
    if forces != 1 or any(t.startswith("-") and not t.startswith("--") and "X" in t for t in tokens[2:]):
        # -ff also removes nested repositories, which `git clean -n` only reports as skipped; -X depends on the
        # user's global ignore files, which the isolated dry run doesn't read. Neither is listed faithfully.
        return None
    for token in tokens[2:]:
        if token == "--":
            after = True
        elif not after and token.startswith("-"):
            if token in {"-i", "--interactive", "-e"} or token.startswith("--exclude"):
                return None
            letters = token[1:] if not token.startswith("--") else ""
            if token in {"-f", "--force", "-ff", "-q", "--quiet"} or (letters and set(letters) <= {"f", "q"}):
                if "d" in letters or "x" in letters or "X" in letters:
                    return None
                continue
            if letters and set(letters) <= {"f", "d", "x", "X", "q"}:
                flags.extend(f"-{c}" for c in letters if c in "dxX")
            elif token in {"-d", "-x", "-X"}:
                flags.append(token)
            else:
                return None
        else:
            paths.append(token)
    try:
        # Runs on the host, so nothing in the repository's own configuration may run a program: command-line
        # settings override `core.fsmonitor` and hooks, and global/system configuration is ignored.
        listed = subprocess.run(["git", "-c", "core.fsmonitor=", "-c", "core.hooksPath=/dev/null",
                                 "-c", "core.untrackedCache=false", "clean", "-n", *flags, "--", *paths],
                                cwd=cwd, capture_output=True, text=True, timeout=_LIST_TIMEOUT,
                                env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                                     "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"})
    except (OSError, subprocess.TimeoutExpired):
        return None
    if listed.returncode or listed.stderr.strip():
        return None
    names = [line[len("Would remove "):] for line in listed.stdout.splitlines() if line.startswith("Would remove ")]
    found = _resolve(names, root, cwd)
    found.resolved_by, found.attributes = "git clean -n", frozenset({"untracked"})
    found.recursive = "-d" in flags
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


_EXPAND_LIMIT = 2000


def covered(call: Removal) -> list[Path]:
    """Everything the call would remove: its targets, and for a recursive removal everything inside its folders."""
    out = list(call.targets)
    if call.recursive:
        for folder in call.targets:
            if folder.is_dir() and not folder.is_symlink():
                out.extend(folder.rglob("*"))
    return out


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
    inside: list[Path] = []
    if call.recursive:
        # A recursive removal takes everything inside a folder: each of the user's files in it is checked, unless
        # the user named the folder itself. An incomplete or oversized listing isn't checked; it isn't run.
        for folder in [t for t in users if t.is_dir() and not t.is_symlink()]:
            try:
                contents = []
                for count, entry in enumerate(folder.rglob("*")):
                    if count >= _EXPAND_LIMIT:
                        return (f"Not run: {show([folder])} holds more than {_EXPAND_LIMIT} entries, too many to "
                                "check against the request." + ask), set()
                    contents.append(entry)
            except OSError:
                return f"Not run: the contents of {show([folder])} couldn't be listed completely." + ask, set()
            named_folder = mentioned(folder, request.named_text) or exact_named(folder, request.selected_text)
            for entry in contents:
                if provenance.created(entry):
                    continue
                if named_folder:
                    if mentioned(entry, request.protected_text):
                        inside.append(entry)  # still checked against the keep clauses below
                    continue
                inside.append(entry)
    users = users + inside
    kept = [t for t in users if mentioned(t, request.protected_text)]
    if kept:
        return (f"Not run: the user asked to keep {show(kept)}. Don't remove it in any other way."), set()
    if call.indirect:
        # Targets that couldn't be listed: only the exact command, in the user's words or chosen on a card.
        if _contains_command(request.request_text, call.command) or _contains_command(request.selected_text,
                                                                                      call.command):
            return "", set()
        return ("Not run: this command removes files its arguments don't list, and they couldn't be listed "
                "safely, so it can't be checked against what the user asked for. Remove the specific files with "
                "`rm <file>`, or show the user this exact command and run it only if they approve it in their "
                "own words." + ask), set()
    unnamed = [t for t in users if not (mentioned(t, request.named_text) or exact_named(t, request.selected_text))]
    if not unnamed:
        return "", set()
    if not request.removal:
        return (f"Not run: this would remove {show(unnamed)}, which existed before this request, and the user "
                "didn't ask to remove anything." + ask), set()
    words = set().union(*(describing_words(o) for o in request.objects)) if request.objects else set()
    if request.collective:
        off = [t for t in unnamed if words and not (_tokens(t) & words or t.suffix.lstrip(".").lower() in words
                                                    or words & call.attributes)]
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
