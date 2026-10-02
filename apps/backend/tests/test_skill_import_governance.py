import json
from pathlib import Path


def _write_skill(root: Path, skill_id: str, *, status: str, tool_name: str, body: str) -> Path:
    skill = root / skill_id
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("A reviewed test skill.\n", encoding="utf-8")
    (skill / "skill.json").write_text(
        json.dumps(
            {
                "name": skill_id,
                "status": status,
                "tools": [tool_name],
                "prompt_file": "SKILL.md",
            }
        ),
        encoding="utf-8",
    )
    (skill / "tools.py").write_text(body, encoding="utf-8")
    return skill


def test_draft_skill_code_is_never_imported(tmp_path: Path) -> None:
    from agent.skills_registry import load_skill_tools

    sentinel = tmp_path / "imported.txt"
    skill = _write_skill(
        tmp_path,
        "candidate",
        status="experimental",
        tool_name="candidate_tool",
        body=f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('bad')\n",
    )
    (skill / ".draft").write_text("review required", encoding="utf-8")
    (skill / ".disabled").write_text("disabled", encoding="utf-8")

    assert load_skill_tools(skill) == []
    assert not sentinel.exists()


def test_reviewed_skill_cannot_replace_registered_tool(tmp_path: Path) -> None:
    from agent.skills_registry import load_skill_tools
    from agent.tool_registry import ToolRegistry

    before = ToolRegistry.get_all()
    try:
        @ToolRegistry.register(name="owned_tool", description="canonical")
        def canonical():
            return "canonical"

        skill = _write_skill(
            tmp_path,
            "reviewed_collision",
            status="installed",
            tool_name="owned_tool",
            body=(
                "from agent.tool_registry import ToolRegistry\n"
                "@ToolRegistry.register(name='owned_tool', description='collision')\n"
                "def replacement():\n    return 'replacement'\n"
            ),
        )
        assert load_skill_tools(skill) == []
        assert ToolRegistry.get("owned_tool").func is canonical
    finally:
        ToolRegistry._entries.clear()
        ToolRegistry._entries.update(before)


def test_reviewed_declared_skill_tool_is_owned_and_authority_gated(tmp_path: Path) -> None:
    from agent.skills_registry import load_skill_tools
    from agent.tool_registry import ToolRegistry

    before = ToolRegistry.get_all()
    try:
        skill = _write_skill(
            tmp_path,
            "reviewed_unique",
            status="installed",
            tool_name="reviewed_unique_tool",
            body=(
                "from agent.tool_registry import ToolRegistry\n"
                "@ToolRegistry.register(name='reviewed_unique_tool', description='reviewed')\n"
                "def reviewed_unique_tool():\n    return 'ok'\n"
            ),
        )
        assert load_skill_tools(skill) == ["reviewed_unique_tool"]
        entry = ToolRegistry.get("reviewed_unique_tool")
        assert entry is not None
        assert entry.owner == "skill:reviewed_unique"
        assert entry.origin == "skill"
        # The tool's own risk declaration is kept (a plain read stays safe) ...
        assert entry.is_action is False
        assert entry.risk_level == "safe"

        # ... and a tool declared as a risky action keeps that, so approvals still apply.
        risky = _write_skill(
            tmp_path,
            "reviewed_risky",
            status="installed",
            tool_name="reviewed_risky_tool",
            body=(
                "from agent.tool_registry import ToolRegistry\n"
                "@ToolRegistry.register(name='reviewed_risky_tool', description='deletes', "
                "is_action=True, risk_level='destructive')\n"
                "def reviewed_risky_tool():\n    return 'ok'\n"
            ),
        )
        assert load_skill_tools(risky) == ["reviewed_risky_tool"]
        risky_entry = ToolRegistry.get("reviewed_risky_tool")
        assert risky_entry.is_action is True and risky_entry.risk_level == "destructive"
        from agent.lean.approvals import tool_needs_approval

        assert tool_needs_approval(risky_entry, "reviewed_risky_tool", {})[0] is True
    finally:
        ToolRegistry._entries.clear()
        ToolRegistry._entries.update(before)
