"""Agent Skills: the open SKILL.md format, loaded the way other agents load it.

A skill is a folder with a SKILL.md file: YAML frontmatter (`name`,
`description`) and Markdown instructions, plus any files those instructions
mention. Agents see only each approved skill's name and description in their
prompt; `read_skill` loads the instructions (or one of the skill's files) when a
task needs them. That keeps many skills cheap in context.

Trust (OWASP ASI04, supply chain): a skill is offered to agents only after the
owner approved it, and approval pins a SHA-256 of the whole folder. Any change,
however small, turns it back into "changed since you approved it" until the
owner looks again. Skills never run anything by themselves: a script inside a
skill runs only through the terminal, under the usual approvals.

Skills come from: importing a folder or pasting a SKILL.md (Settings › Skills),
or turning a proven ("established") learning lesson into a draft skill.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from agent.lean.toolbox import NativeTool

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NAME = 64
MAX_DESCRIPTION = 1024
MAX_FOLDER_BYTES = 2 * 1024 * 1024
MAX_FILES = 200
MAX_READ_CHARS = 60_000
TRUST_FILE = ".trust.json"

_lock = threading.Lock()


def skills_dir() -> Path:
    from config import DATA_DIR

    path = Path(DATA_DIR) / "agent-skills"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class AgentSkill:
    name: str
    description: str
    body: str
    folder: Path
    digest: str
    status: str  # approved | needs_review | changed | invalid
    problem: str = ""
    files: list[str] = field(default_factory=list)

    def public(self, *, with_body: bool = False) -> dict[str, Any]:
        data = {
            "name": self.name,
            "description": self.description,
            "status": self.status,
            "problem": self.problem,
            "files": list(self.files),
            "digest": self.digest[:12],
        }
        if with_body:
            data["body"] = self.body
        return data


# ── parsing ──────────────────────────────────────────────────────────

def parse_skill_md(text: str) -> tuple[dict[str, Any], str, str]:
    """(frontmatter, body, problem). An empty problem means it's a valid SKILL.md."""
    import yaml

    text = str(text or "").replace("\r\n", "\n").lstrip("﻿")
    match = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not match:
        return {}, text.strip(), "SKILL.md must start with frontmatter between --- lines (name and description)."
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        return {}, match.group(2).strip(), f"The frontmatter isn't valid YAML: {exc}"
    if not isinstance(meta, dict):
        return {}, match.group(2).strip(), "The frontmatter must be a set of key: value lines."
    body = match.group(2).strip()
    name = str(meta.get("name") or "").strip()
    description = " ".join(str(meta.get("description") or "").split())
    if not name or len(name) > MAX_NAME or not NAME_RE.match(name):
        return meta, body, "The name must be lowercase letters, digits and single hyphens (up to 64 characters)."
    if not description:
        return meta, body, "The description is missing. It tells agents when to use the skill."
    if len(description) > MAX_DESCRIPTION:
        return meta, body, "The description is longer than 1,024 characters."
    if not body:
        return meta, body, "The skill has no instructions under the frontmatter."
    return meta, body, ""


def _folder_files(folder: Path) -> list[Path]:
    files = []
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Skills can't contain links ({path.name}).")
        if path.is_file() and path.name != TRUST_FILE:
            files.append(path)
    if len(files) > MAX_FILES:
        raise ValueError(f"A skill can have at most {MAX_FILES} files.")
    if sum(p.stat().st_size for p in files) > MAX_FOLDER_BYTES:
        raise ValueError("A skill folder can be at most 2 MB.")
    return files


def folder_digest(folder: Path) -> str:
    """SHA-256 over every file's relative path and bytes, in a stable order."""
    sha = hashlib.sha256()
    for path in _folder_files(folder):
        sha.update(path.relative_to(folder).as_posix().encode("utf-8") + b"\0")
        sha.update(path.read_bytes() + b"\0")
    return sha.hexdigest()


# ── trust ────────────────────────────────────────────────────────────

def _trust() -> dict[str, str]:
    path = skills_dir() / TRUST_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_trust(trust: dict[str, str]) -> None:
    path = skills_dir() / TRUST_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(trust, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def load(folder: Path, trust: Optional[dict[str, str]] = None) -> AgentSkill:
    trust = _trust() if trust is None else trust
    name = folder.name
    try:
        files = _folder_files(folder)
        digest = folder_digest(folder)
    except (OSError, ValueError) as exc:
        return AgentSkill(name, "", "", folder, "", "invalid", str(exc))
    skill_md = folder / "SKILL.md"
    if not skill_md.is_file():
        return AgentSkill(name, "", "", folder, digest, "invalid", "The folder has no SKILL.md.")
    meta, body, problem = parse_skill_md(skill_md.read_text(encoding="utf-8", errors="replace"))
    description = " ".join(str(meta.get("description") or "").split())
    if not problem and str(meta.get("name")) != name:
        problem = f"The name in SKILL.md ({meta.get('name')}) doesn't match its folder ({name})."
    rel = [p.relative_to(folder).as_posix() for p in files if p.name != "SKILL.md"]
    if problem:
        return AgentSkill(name, description, body, folder, digest, "invalid", problem, rel)
    pinned = trust.get(name, "")
    status = "approved" if pinned == digest else "changed" if pinned else "needs_review"
    return AgentSkill(name, description, body, folder, digest, status, "", rel)


def list_skills() -> list[AgentSkill]:
    trust = _trust()
    return [load(path, trust) for path in sorted(skills_dir().iterdir()) if path.is_dir() and not path.name.startswith(".")]


def get(name: str) -> Optional[AgentSkill]:
    if not NAME_RE.match(str(name or "")):
        return None
    folder = skills_dir() / name
    return load(folder) if folder.is_dir() else None


def approved() -> list[AgentSkill]:
    return [skill for skill in list_skills() if skill.status == "approved"]


def approve(name: str, digest: str = "") -> AgentSkill:
    """Pin the skill as it is now. `digest` (from the review screen) must still match."""
    with _lock:
        skill = get(name)
        if skill is None:
            raise KeyError(name)
        if skill.status == "invalid":
            raise ValueError(skill.problem)
        if digest and not skill.digest.startswith(digest):
            raise ValueError("The skill changed while you were reviewing it. Review it again.")
        trust = _trust()
        trust[name] = skill.digest
        _save_trust(trust)
        return load(skill.folder, trust)


def remove(name: str) -> bool:
    with _lock:
        skill = get(name)
        if skill is None:
            return False
        shutil.rmtree(skill.folder)
        trust = _trust()
        if trust.pop(name, None) is not None:
            _save_trust(trust)
        return True


# ── adding skills ────────────────────────────────────────────────────

def _install(name: str, writer) -> AgentSkill:
    """Write a skill into a staging folder, then swap it in. It always needs review."""
    with _lock:
        root = skills_dir()
        staging = root / f".incoming-{name}"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir()
        try:
            writer(staging)
            folder_digest(staging)  # size, file count and no links
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        target = root / name
        if target.exists():
            shutil.rmtree(target)
        staging.replace(target)
    return load(target)


def import_text(skill_md: str) -> AgentSkill:
    meta, _body, problem = parse_skill_md(skill_md)
    if problem:
        raise ValueError(problem)
    name = str(meta["name"])
    return _install(name, lambda folder: (folder / "SKILL.md").write_text(skill_md.replace("\r\n", "\n"), encoding="utf-8"))


def import_folder(source: str) -> AgentSkill:
    src = Path(str(source or "")).expanduser()
    if not src.is_dir():
        raise ValueError("That folder doesn't exist.")
    if not (src / "SKILL.md").is_file():
        raise ValueError("That folder has no SKILL.md.")
    meta, _body, problem = parse_skill_md((src / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
    if problem:
        raise ValueError(problem)
    for path in src.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Skills can't contain links ({path.name}).")

    def copy(folder: Path) -> None:
        for path in src.rglob("*"):
            if path.is_file() and path.name != TRUST_FILE:
                dest = folder / path.relative_to(src)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, dest)

    return _install(str(meta["name"]), copy)


def skill_from_lesson(lesson: Any, name: str = "") -> AgentSkill:
    """Draft a skill from a proven lesson. It still needs the owner's approval."""
    if str(getattr(lesson, "status", "")) != "established":
        raise ValueError("Only established lessons (proven by results) can become skills.")
    title = " ".join(str(getattr(lesson, "title", "") or "").split())
    text = str(getattr(lesson, "text", "") or "").strip()
    slug = name or re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:MAX_NAME].strip("-") or "lesson"
    if not NAME_RE.match(slug):
        raise ValueError("Pick a name with lowercase letters, digits and hyphens.")
    task_kind = str(getattr(lesson, "task_kind", "") or "").strip()
    avoid = str(getattr(lesson, "kind", "do")) == "avoid"
    description = (f"{'What to avoid' if avoid else 'How to approach'} {task_kind or 'tasks like this'}: {title}")[:MAX_DESCRIPTION]
    skill_md = (
        f"---\nname: {slug}\ndescription: {json.dumps(description)}\n---\n\n"
        f"# {title}\n\n{text}\n\n"
        f"_Made from a lesson EchoSpeak learned and proved on {getattr(lesson, 'wins', 0)} tasks._\n"
    )
    return import_text(skill_md)


# ── what agents see ──────────────────────────────────────────────────

def prompt_section(skills: Optional[list[AgentSkill]] = None) -> str:
    skills = approved() if skills is None else skills
    if not skills:
        return ""
    lines = [
        "## Skills",
        "Approved instructions for specific kinds of tasks. When a task matches one, call read_skill with its name "
        "first and follow it. Files a skill mentions can be read with read_skill(name, file).",
    ]
    lines += [f"- {skill.name}: {skill.description}" for skill in skills[:40]]
    return "\n".join(lines)


def read_skill(args: dict[str, Any]) -> str:
    name = str(args.get("name") or "").strip()
    skill = get(name)
    if skill is None or skill.status != "approved":
        names = ", ".join(s.name for s in approved()) or "none"
        return f"Error: no approved skill named '{name}'. Approved skills: {names}."
    wanted = str(args.get("file") or "").strip().replace("\\", "/")
    if not wanted:
        return f"# Skill: {skill.name}\n\n{skill.body[:MAX_READ_CHARS]}"
    target = (skill.folder / wanted).resolve()
    if skill.folder.resolve() not in target.parents or not target.is_file() or target.name == TRUST_FILE:
        return f"Error: '{wanted}' isn't a file in the {skill.name} skill. Files: {', '.join(skill.files) or 'none'}."
    data = target.read_bytes()
    if b"\0" in data[:4096]:
        return f"Error: '{wanted}' is a binary file; read_skill only returns text."
    return data.decode("utf-8", errors="replace")[:MAX_READ_CHARS]


def read_skill_tool() -> NativeTool:
    return NativeTool(
        name="read_skill",
        description=("Load an approved skill's instructions (or one of its files) before doing a task it covers. "
                     "Skills are listed in the system prompt under 'Skills'."),
        parameters={"type": "object", "properties": {
            "name": {"type": "string", "description": "The skill's name, exactly as listed."},
            "file": {"type": "string", "description": "Optional: a file inside the skill, e.g. reference.md."},
        }, "required": ["name"]},
        func=read_skill,
        parallel_safe=True,
        always=True,
    )
