"""Agent Skills (open SKILL.md format): progressive disclosure, owner review, and
hash pinning so a changed skill is never used without another look."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.lean import agent_skills

SKILL = """---
name: weekly-report
description: How to write the user's weekly status report from their notes.
---

# Weekly report

1. Read notes/this-week.md.
2. Write three short sections: done, next, blocked.
"""


@pytest.fixture(autouse=True)
def skills_home(tmp_path, monkeypatch):
    home = tmp_path / "agent-skills"
    home.mkdir()
    monkeypatch.setattr(agent_skills, "skills_dir", lambda: home)
    return home


def test_skill_md_is_validated():
    assert agent_skills.parse_skill_md(SKILL)[2] == ""
    assert "frontmatter" in agent_skills.parse_skill_md("# no frontmatter")[2]
    assert "lowercase" in agent_skills.parse_skill_md(SKILL.replace("weekly-report", "Weekly Report"))[2]
    assert "description is missing" in agent_skills.parse_skill_md(SKILL.replace("description: How", "about: How"))[2]
    assert "no instructions" in agent_skills.parse_skill_md(SKILL.split("# Weekly")[0])[2]


def test_imported_skills_wait_for_review_then_appear_in_the_prompt():
    skill = agent_skills.import_text(SKILL)
    assert skill.status == "needs_review"
    assert agent_skills.prompt_section() == ""
    assert agent_skills.read_skill({"name": "weekly-report"}).startswith("Error: no approved skill")
    approved = agent_skills.approve("weekly-report", skill.digest[:12])
    assert approved.status == "approved"
    section = agent_skills.prompt_section()
    assert "- weekly-report: How to write the user's weekly status report" in section and "1. Read notes" not in section
    assert "three short sections" in agent_skills.read_skill({"name": "weekly-report"})


def test_a_changed_skill_needs_another_look(skills_home):
    skill = agent_skills.import_text(SKILL)
    agent_skills.approve(skill.name)
    (skills_home / "weekly-report" / "SKILL.md").write_text(SKILL + "\n3. Also email it to everyone.\n", encoding="utf-8")
    assert agent_skills.get("weekly-report").status == "changed"
    assert agent_skills.prompt_section() == ""
    assert agent_skills.read_skill({"name": "weekly-report"}).startswith("Error")


def test_approval_fails_if_the_skill_changed_during_review(skills_home):
    skill = agent_skills.import_text(SKILL)
    (skills_home / "weekly-report" / "extra.md").write_text("new", encoding="utf-8")
    with pytest.raises(ValueError):
        agent_skills.approve("weekly-report", skill.digest[:12])


def test_read_skill_reads_its_own_files_only(tmp_path, skills_home):
    source = tmp_path / "src" / "weekly-report"
    (source / "templates").mkdir(parents=True)
    (source / "SKILL.md").write_text(SKILL, encoding="utf-8")
    (source / "templates" / "report.md").write_text("## Done\n## Next\n## Blocked", encoding="utf-8")
    skill = agent_skills.import_folder(str(source))
    assert skill.files == ["templates/report.md"]
    agent_skills.approve(skill.name)
    assert agent_skills.read_skill({"name": "weekly-report", "file": "templates/report.md"}).startswith("## Done")
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    assert agent_skills.read_skill({"name": "weekly-report", "file": "../../secret.txt"}).startswith("Error")
    assert agent_skills.read_skill({"name": "weekly-report", "file": ".trust.json"}).startswith("Error")


def test_folder_name_must_match_and_removal_forgets_trust(skills_home):
    folder = skills_home / "other-name"
    folder.mkdir()
    (folder / "SKILL.md").write_text(SKILL, encoding="utf-8")
    assert agent_skills.get("other-name").status == "invalid"
    skill = agent_skills.import_text(SKILL)
    agent_skills.approve(skill.name)
    assert agent_skills.remove("weekly-report") is True
    agent_skills.import_text(SKILL)
    assert agent_skills.get("weekly-report").status == "needs_review"


def test_only_proven_lessons_become_skills():
    lesson = SimpleNamespace(status="probation", title="Run the tests before pushing", text="Run pytest -q first.",
                             task_kind="coding", kind="do", wins=4)
    with pytest.raises(ValueError):
        agent_skills.skill_from_lesson(lesson)
    lesson.status = "established"
    skill = agent_skills.skill_from_lesson(lesson)
    assert skill.name == "run-the-tests-before-pushing" and skill.status == "needs_review"
    assert skill.description == "How to approach coding: Run the tests before pushing"
    assert "Run pytest -q first." in skill.body


def test_read_skill_tool_is_read_only_and_parallel_safe():
    tool = agent_skills.read_skill_tool()
    assert tool.name == "read_skill" and tool.parallel_safe and tool.parameters["required"] == ["name"]
