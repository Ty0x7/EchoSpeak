from pathlib import Path

import pytest
from pydantic import ValidationError


def _connection_record(*, connection_id: str, project_id: str = "project-a", global_access: bool = False):
    from agent.connections import (
        ConnectionAuthentication,
        ConnectionCapability,
        ConnectionCapabilityKind,
        ConnectionHealth,
        ConnectionKind,
        ConnectionRecord,
        ConnectionScope,
    )

    return ConnectionRecord(
        id=connection_id,
        kind=ConnectionKind.MCP_SERVER,
        display_name=f"Fixture {connection_id}",
        provider="fixture",
        source_ref=f"settings:mcp_servers:{connection_id}",
        health=ConnectionHealth.HEALTHY,
        authentication=ConnectionAuthentication.CONFIGURED,
        scope=ConnectionScope(
            allow_global=global_access,
            project_ids=[] if global_access else [project_id],
            session_ids=["session-a"] if not global_access else [],
            network_hosts=["fixture.example"],
            permissions=["calendar.read"],
        ),
        capabilities=[
            ConnectionCapability(
                id="calendar.read",
                kind=ConnectionCapabilityKind.RESOURCE,
                name="Read calendar",
                resource_types=["calendar_event"],
                permissions=["calendar.read"],
            ),
            ConnectionCapability(
                id="calendar.write",
                kind=ConnectionCapabilityKind.TOOL,
                name="Write calendar",
                tool_names=["calendar_event_create"],
                requires_approval=True,
                permissions=["calendar.write"],
            ),
        ],
        provenance={"owner": "MCPManager"},
    )


def test_connection_registry_projects_secret_free_scoped_capability_refs(tmp_path: Path) -> None:
    from agent.connections import (
        ConnectionCapability,
        ConnectionCapabilityKind,
        ConnectionRecord,
        ConnectionReference,
        ConnectionRegistry,
        ConnectionRegistryError,
        ConnectionScopeError,
    )

    path = tmp_path / "connections" / "registry.json"
    registry = ConnectionRegistry(path)
    scoped = registry.register(_connection_record(connection_id="calendar-a"))
    registry.register(_connection_record(connection_id="global-provider", global_access=True))

    visible = registry.list(project_id="project-a", session_id="session-a")
    assert {item.id for item in visible} == {"calendar-a", "global-provider"}
    assert {item.id for item in registry.list(project_id="project-b", session_id="session-a")} == {
        "global-provider"
    }
    assert registry.get("calendar-a", project_id="project-b", session_id="session-a") is None

    resolved = registry.resolve_references(
        [ConnectionReference(connection_id="calendar-a", capability_ids=["calendar.read"])],
        project_id="project-a",
        session_id="session-a",
    )
    assert [capability["id"] for capability in resolved[0].capabilities] == ["calendar.read"]
    with pytest.raises(ConnectionScopeError):
        registry.resolve_references(
            [{"connection_id": "calendar-a", "capability_ids": ["calendar.read"]}],
            project_id="project-b",
            session_id="session-a",
        )
    with pytest.raises(ConnectionRegistryError, match="explicit capability"):
        registry.resolve_references(
            [{"connection_id": "calendar-a", "capability_ids": []}],
            project_id="project-a",
            session_id="session-a",
        )

    updated = registry.update(
        scoped.id,
        expected_revision=scoped.revision,
        errors=["provider rejected token=super-secret Bearer abc.def.ghi"],
        last_checked_at=100.0,
    )
    projection = registry.list(project_id="project-a", session_id="session-a")[0]
    assert "super-secret" not in updated.errors[0]
    assert "abc.def.ghi" not in updated.errors[0]
    assert "super-secret" not in projection.errors[0]
    assert "abc.def.ghi" not in projection.errors[0]
    assert "[REDACTED]" in projection.errors[0]

    with pytest.raises(ValidationError, match="secret field"):
        ConnectionRecord(
            **{
                **_connection_record(connection_id="secret-fixture").model_dump(),
                "metadata": {"api_key": "must-not-be-stored"},
            }
        )
    with pytest.raises(ValidationError, match="unrestricted shell"):
        ConnectionCapability(
            id="unsafe",
            kind=ConnectionCapabilityKind.TOOL,
            name="Unsafe",
            tool_names=["terminal_run"],
        )

    reloaded = ConnectionRegistry(path)
    assert reloaded.get("calendar-a", project_id="project-a", session_id="session-a") is not None


def test_connection_registry_corruption_fails_closed_and_preserves_authority(tmp_path: Path) -> None:
    from agent.connections import ConnectionRegistry, ConnectionStateError

    path = tmp_path / "connections" / "registry.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"schema_version": 99, "connections": {}}', encoding="utf-8")
    with pytest.raises(ConnectionStateError, match="not overwritten"):
        ConnectionRegistry(path)
    assert '"schema_version": 99' in path.read_text(encoding="utf-8")
    assert list((path.parent / "corrupt-state").rglob("RECOVERY.txt"))
