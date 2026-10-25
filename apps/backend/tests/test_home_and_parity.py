"""~/.echospeak like ~/.claude and ~/.codex, drafted and discovered skills, and this PC's terminal
saying what the sandbox says (11.10.3)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent import home
from agent.lean import agent_skills, terminal
from agent.lean.approvals import dangerous_command

SKILL = "---\nname: {name}\ndescription: How to do {name}.\n---\n\n# Steps\n\n1. Do it.\n"


@pytest.fixture
def fresh(monkeypatch, tmp_path):
    """An empty ~/.echospeak, home folder and skills store for each test."""
    monkeypatch.setenv("ECHOSPEAK_HOME", str(tmp_path / ".echospeak"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(agent_skills, "skills_dir", lambda: _mk(tmp_path / "installed"))
    return tmp_path


@pytest.fixture
def workspace(monkeypatch, tmp_path):
    """tmp_path as the only folder commands may run in, whatever earlier tests left configured."""
    from agent.tools import _tool_execution_context
    from config import config

    monkeypatch.setattr(config, "file_tool_root", str(tmp_path), raising=False)
    monkeypatch.setattr(config, "file_tool_extra_roots", [], raising=False)
    token = _tool_execution_context.set({})  # no project scope left over from an earlier test
    yield tmp_path
    _tool_execution_context.reset(token)


def _mk(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


# ── ~/.echospeak ───────────────────────────────────────────────────────
def test_ensure_makes_the_folder_once_and_keeps_what_you_wrote(fresh):
    folder = home.ensure()
    assert (folder / "skills").is_dir() and (folder / "AGENTS.md").read_text(encoding="utf-8") == home.TEMPLATE
    (folder / "AGENTS.md").write_text("Use pnpm.", encoding="utf-8")
    home.ensure()
    assert (folder / "AGENTS.md").read_text(encoding="utf-8") == "Use pnpm."


def test_the_untouched_template_adds_nothing_to_the_prompt(fresh):
    home.ensure()
    assert home.instructions_section() == ""


def test_your_agents_md_and_the_projects_reach_the_prompt(fresh):
    home.ensure()
    (home.home_dir() / "AGENTS.md").write_text("<!-- note to self -->\nKeep replies short.", encoding="utf-8")
    project = _mk(fresh / "proj")
    (project / "CLAUDE.md").write_text("Run tests with pytest -q.", encoding="utf-8")
    section = home.instructions_section(str(project))
    assert "Keep replies short." in section and "note to self" not in section
    assert "Run tests with pytest -q." in section and "this project's, written by whoever made the project" in section
    (project / "AGENTS.md").write_text("Use the Makefile.", encoding="utf-8")
    section = home.instructions_section(str(project))
    assert "Use the Makefile." in section and "pytest -q" not in section  # AGENTS.md first, like Codex


def test_instructions_stop_at_32_kib(fresh):
    _mk(home.home_dir())
    (home.home_dir() / "AGENTS.md").write_text("x" * 40_000, encoding="utf-8")
    project = _mk(fresh / "proj")
    (project / "AGENTS.md").write_text("never seen", encoding="utf-8")
    files = home.instruction_files(str(project))
    assert len(files) == 1 and len(files[0][1]) == home.MAX_INSTRUCTION_BYTES


def test_the_prompt_places_them_after_skills():
    from agent.lean.personas import AgentPersona
    from agent.lean.prompt import build_system_prompt

    prompt = build_system_prompt(persona=AgentPersona(id="echo", name="Echo"), soul_text="", skills="## Skills\n- a: b",
                                 instructions="## Instructions from AGENTS.md files\nUse pnpm.")
    assert prompt.index("## Skills") < prompt.index("Use pnpm.")


# ── skills: drafted by an agent, found in other agents' folders ─────────
def test_a_proposed_skill_waits_for_review(fresh):
    out = agent_skills.propose({"name": "minecraft-new-world", "description": "Make a world.",
                                "instructions": "1. Click Singleplayer.\n2. Click Create New World."})
    assert "draft skill" in out and "Settings › Skills" in out
    skill = agent_skills.get("minecraft-new-world")
    assert skill.status == "needs_review" and "Click Singleplayer" in skill.body
    assert agent_skills.prompt_section() == ""  # agents don't see it until the owner approves


def test_a_proposed_skill_never_replaces_an_installed_one(fresh):
    agent_skills.import_text(SKILL.format(name="weekly-report"))
    agent_skills.approve("weekly-report")
    out = agent_skills.propose({"name": "weekly-report", "description": "x", "instructions": "y"})
    assert out.startswith("Error") and agent_skills.get("weekly-report").status == "approved"
    assert agent_skills.propose({"name": "Bad Name", "description": "x", "instructions": "y"}).startswith("Error")
    assert agent_skills.propose({"name": "ok-name", "description": "", "instructions": "y"}).startswith("Error")


def test_skills_in_other_agents_folders_are_offered_until_added(fresh):
    for folder, name in [(fresh / ".agents" / "skills" / "deploy", "deploy"),
                         (fresh / ".claude" / "skills" / "review", "review"),
                         (fresh / ".echospeak" / "skills" / "notes", "notes")]:
        _mk(folder)
        (folder / "SKILL.md").write_text(SKILL.format(name=name), encoding="utf-8")
    offered = {item["name"]: item for item in agent_skills.available()}
    assert {"deploy", "review", "notes", "desktop-apps-and-games"} <= set(offered)
    assert offered["desktop-apps-and-games"]["source"] == "Comes with EchoSpeak"
    agent_skills.import_folder(offered["deploy"]["path"])
    assert "deploy" not in {item["name"] for item in agent_skills.available()}
    assert agent_skills.get("deploy").status == "needs_review"


# ── this PC's terminal says what the sandbox says ───────────────────────
def test_this_pc_says_what_is_installed_and_how_powershell_differs(monkeypatch):
    monkeypatch.setattr(terminal, "_HOST_FOUND", {"git": "", "py": "", "python": "may be the Microsoft Store shortcut",
                                                  "_checked": ""})
    monkeypatch.setattr(terminal, "git_bash", lambda: r"C:\Program Files\Git\bin\bash.exe")
    monkeypatch.setattr(terminal, "execution_mode", lambda: "host")
    monkeypatch.setattr(terminal.os, "name", "nt")
    monkeypatch.setattr(terminal.shutil, "which", lambda name: None)
    term = terminal.Terminal()
    term.mode = "host"
    text = term.describe()
    assert "Windows PowerShell 5.1" in text and "there is no &&" in text
    assert "Found on this PC: git, py, python (may be the Microsoft Store shortcut)" in text and "Run Python with py." in text
    assert "Not found:" in text and "node" in text.split("Not found:")[1]
    assert "grep x f -> Select-String" in text and 'shell="bash"' in text
    schema = term.tools()[0].parameters["properties"]
    assert schema["shell"]["enum"] == ["powershell", "bash"]


def test_failure_hints_turn_bash_habits_into_powershell(monkeypatch):
    monkeypatch.setattr(terminal.os, "name", "nt")
    monkeypatch.setattr(terminal, "git_bash", lambda: "bash.exe")
    hint = terminal.failure_hint(1, "grep : The term 'grep' is not recognized as the name of a cmdlet", "host", "grep x f")
    assert "in PowerShell use Select-String" in hint and 'shell="bash"' in hint
    hint = terminal.failure_hint(1, "Get-ChildItem : A parameter cannot be found that matches parameter name 'la'.", "host", "ls -la")
    assert "not bash flags" in hint
    assert "nothing matched" in terminal.failure_hint(1, "\n", "docker", "grep -r TODO .")
    assert "nothing matched" in terminal.failure_hint(1, "", "host", "git diff --quiet")
    # In Git Bash or the sandbox, grep is grep.
    assert "PowerShell" not in terminal.failure_hint(127, "bash: grep: command not found", "host", "grep x", shell="bash")


def test_bash_runs_on_this_pc_only_when_git_bash_is_there(monkeypatch, workspace):
    tmp_path = workspace
    monkeypatch.setattr(terminal.os, "name", "nt")
    monkeypatch.setattr(terminal, "git_bash", lambda: "")
    term = terminal.Terminal(str(tmp_path))
    term.mode = "host"
    out = term.run({"command": "ls -la", "shell": "bash", "cwd": str(tmp_path)})
    assert out.startswith("Error: not run. Git Bash isn't installed")
    assert terminal.Terminal.shell_for({"shell": "bash"}, "host") == ""
    monkeypatch.setattr(terminal, "git_bash", lambda: "bash.exe")
    assert terminal.Terminal.shell_for({"shell": "bash"}, "host") == "bash"
    assert terminal.Terminal.shell_for({"shell": "bash"}, "docker") == ""


@pytest.mark.skipif(os.name != "nt" or not terminal.git_bash(), reason="needs Git for Windows")
def test_git_bash_really_runs_in_the_requested_folder(workspace):
    tmp_path = workspace
    term = terminal.Terminal(str(tmp_path))
    term.mode = "host"
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    out = term.run({"command": "ls && cat a.txt | grep -c hello", "shell": "bash", "cwd": str(tmp_path)})
    assert out.startswith("[ok") and "a.txt" in out and "\n1" in out


def test_bash_deleters_ask_on_this_pc_too():
    assert dangerous_command("find . -name '*.log' -delete")
    assert dangerous_command("shred -u secrets.txt") and dangerous_command("truncate -s 0 app.log")
    assert not dangerous_command("find . -name '*.log'")
