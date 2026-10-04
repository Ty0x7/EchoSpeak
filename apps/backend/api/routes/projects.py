"""Projects (attached folders) and the skill workspace."""

import asyncio
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from loguru import logger

from config import config, read_runtime_override_payload, write_runtime_override_payload
from agent.state import get_state_store
from api.deps import _apply_thread_scope, get_agent, get_existing_agent

router = APIRouter()


# === Project Management Endpoints ===

class ProjectResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = ""
    created_at: str
    updated_at: str
    memory_type: str = "project"
    context_prompt: Optional[str] = ""
    tags: List[str] = []
    metadata: Dict[str, Any] = Field(default_factory=dict)
    workspace_root: str = ""
    trust_state: str = "untrusted"
    git_root: str = ""
    git_metadata: Dict[str, Any] = Field(default_factory=dict)
    instructions: str = ""
    verified_facts: List[Dict[str, Any]] = Field(default_factory=list)
    archived: bool = False
    preferred_model_profile: Optional[Dict[str, Any]] = None


class ProjectListResponse(BaseModel):
    items: List[ProjectResponse]
    count: int


class ProjectCreateRequest(BaseModel):
    name: str
    description: Optional[str] = ""
    context_prompt: Optional[str] = ""
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None
    workspace_root: str = ""
    trust_state: str = "untrusted"


class ProjectAttachFolderRequest(BaseModel):
    path: str
    name: str = ""
    trust_state: str = "trusted"
    session_id: str = ""


class ProjectUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    context_prompt: Optional[str] = None
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None


@router.get("/projects", response_model=ProjectListResponse)
async def list_projects():
    """List all projects."""
    from agent.projects import get_project_manager
    manager = get_project_manager()
    projects = manager.list_projects()
    return ProjectListResponse(
        items=[ProjectResponse(**p.model_dump()) for p in projects],
        count=len(projects),
    )


@router.get("/projects/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str):
    """Get a project by ID."""
    from agent.projects import get_project_manager
    manager = get_project_manager()
    project = manager.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectResponse(**project.model_dump())


@router.post("/projects", response_model=ProjectResponse)
async def create_project(request: ProjectCreateRequest):
    """Create a new project."""
    from agent.projects import get_project_manager
    manager = get_project_manager()
    project = manager.create_project(
        name=request.name,
        description=request.description,
        context_prompt=request.context_prompt,
        tags=request.tags,
        metadata=request.metadata,
        workspace_root=request.workspace_root,
        trust_state=request.trust_state,
    )
    return ProjectResponse(**project.model_dump())


@router.post("/projects/attach-folder", response_model=ProjectResponse)
async def attach_project_folder(request: ProjectAttachFolderRequest):
    from agent.projects import get_project_manager
    try:
        project = get_project_manager().attach_folder(request.path, name=request.name, trust_state=request.trust_state)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if request.session_id:
        agent = get_agent(request.session_id)
        with agent._request_lock:
            _apply_thread_scope(agent, request.session_id)
            if not agent.activate_project(project.id):
                raise HTTPException(status_code=409, detail="Folder attached but Project activation failed")
    return ProjectResponse(**project.model_dump())


@router.post("/projects/pick-folder")
async def pick_project_folder():
    """Open the native Windows folder dialog for the local desktop deployment."""
    def choose() -> str:
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            try:
                return str(filedialog.askdirectory(title="Add EchoSpeak Project folder", mustexist=True) or "")
            finally:
                root.destroy()
        except Exception as exc:
            raise RuntimeError(f"Native folder picker is unavailable: {exc}") from exc
    try:
        path = await asyncio.to_thread(choose)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"path": path, "cancelled": not bool(path)}


@router.put("/projects/{project_id}", response_model=ProjectResponse)
async def update_project(project_id: str, request: ProjectUpdateRequest):
    """Update an existing project."""
    from agent.projects import get_project_manager
    manager = get_project_manager()
    project = manager.update_project(
        project_id=project_id,
        name=request.name,
        description=request.description,
        context_prompt=request.context_prompt,
        tags=request.tags,
        metadata=request.metadata,
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectResponse(**project.model_dump())


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    """Delete a project and clear live agent scope for every detached Session."""
    from agent.projects import get_project_manager
    manager = get_project_manager()
    success = manager.delete_project(project_id)
    if not success:
        raise HTTPException(status_code=404, detail="Project not found")
    store = get_state_store()
    # Snapshot sessions before detach so we can clear live agent memory.
    affected = [
        st.thread_id
        for st in store.list_thread_states()
        if str(st.active_project_id or "") == str(project_id)
    ]
    detached_sessions = store.detach_project(project_id)
    for tid in affected:
        try:
            agent = get_existing_agent(tid) or get_agent(tid)
            with agent._request_lock:
                if hasattr(agent, "_clear_session_project_scope"):
                    agent._clear_session_project_scope(
                        thread_id=tid,
                        reason="Project deleted",
                    )
                else:
                    agent.activate_project(None)
        except Exception:
            pass
    return {"ok": True, "deleted": project_id, "detached_sessions": detached_sessions}


@router.post("/projects/{project_id}/activate")
async def activate_project(project_id: str, thread_id: Optional[str] = Query(default=None)):
    """Activate a project, injecting its context into the agent's system prompt."""
    agent = get_agent(thread_id)
    with agent._request_lock:
        _apply_thread_scope(agent, thread_id)
        success = agent.activate_project(project_id)
    if not success:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"ok": True, "activated": project_id, "thread_state": get_state_store().get_thread_state(thread_id).model_dump()}


@router.post("/projects/deactivate")
async def deactivate_project(thread_id: Optional[str] = Query(default=None)):
    """Deactivate the current project."""
    agent = get_agent(thread_id)
    with agent._request_lock:
        _apply_thread_scope(agent, thread_id)
        agent.activate_project(None)
    return {"ok": True, "deactivated": True, "thread_state": get_state_store().get_thread_state(thread_id).model_dump()}


class WorkspaceResponse(BaseModel):
    root: str = Field(description="Absolute path of the current FILE_TOOL_ROOT")
    display_name: str = Field(description="Short display name (last directory component)")
    files: List[Dict[str, Any]] = Field(default_factory=list, description="File listing of the root directory")
    writable: bool = Field(default=False, description="Whether file_write is enabled")
    terminal: bool = Field(default=False, description="Whether terminal_run is enabled")


class WorkspaceChangeRequest(BaseModel):
    root: str = Field(..., description="New FILE_TOOL_ROOT path (absolute)")


def _build_file_tree(root_path: Path, max_depth: int = 3, max_items: int = 200) -> List[Dict[str, Any]]:
    """Build a recursive file tree from root_path, limited by depth and item count."""
    items: List[Dict[str, Any]] = []
    count = 0

    def _walk(current: Path, depth: int, rel: str):
        nonlocal count
        if depth > max_depth or count >= max_items:
            return
        try:
            entries = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            return
        for entry in entries:
            if count >= max_items:
                return
            if entry.name.startswith(".") and entry.name not in (".env", ".env.example"):
                continue
            name = entry.name
            rel_path = f"{rel}/{name}" if rel else name
            is_dir = entry.is_dir()
            node: Dict[str, Any] = {
                "name": name,
                "path": rel_path,
                "type": "directory" if is_dir else "file",
            }
            if not is_dir:
                try:
                    node["size"] = entry.stat().st_size
                except Exception:
                    node["size"] = 0
            count += 1
            if is_dir and depth < max_depth:
                children: List[Dict[str, Any]] = []
                old_count = count
                _walk_into(entry, depth + 1, rel_path, children)
                node["children"] = children
                node["item_count"] = count - old_count
            elif is_dir:
                node["children"] = []
                try:
                    node["item_count"] = sum(1 for _ in entry.iterdir())
                except Exception:
                    node["item_count"] = 0
            items.append(node)

    def _walk_into(current: Path, depth: int, rel: str, target: List[Dict[str, Any]]):
        nonlocal count
        if depth > max_depth or count >= max_items:
            return
        try:
            entries = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            return
        for entry in entries:
            if count >= max_items:
                return
            if entry.name.startswith(".") and entry.name not in (".env", ".env.example"):
                continue
            name = entry.name
            rel_path = f"{rel}/{name}" if rel else name
            is_dir = entry.is_dir()
            node: Dict[str, Any] = {
                "name": name,
                "path": rel_path,
                "type": "directory" if is_dir else "file",
            }
            if not is_dir:
                try:
                    node["size"] = entry.stat().st_size
                except Exception:
                    node["size"] = 0
            count += 1
            if is_dir and depth < max_depth:
                children: List[Dict[str, Any]] = []
                old_count = count
                _walk_into(entry, depth + 1, rel_path, children)
                node["children"] = children
                node["item_count"] = count - old_count
            elif is_dir:
                node["children"] = []
                try:
                    node["item_count"] = sum(1 for _ in entry.iterdir())
                except Exception:
                    node["item_count"] = 0
            target.append(node)

    _walk(root_path, 0, "")
    return items


@router.get("/workspace", response_model=WorkspaceResponse)
async def get_workspace():
    """Return the current workspace root, file tree, and permission flags."""
    try:
        from agent.tools import _file_tool_root
        root = _file_tool_root()
        files = _build_file_tree(root, max_depth=2, max_items=150)
        return WorkspaceResponse(
            root=str(root),
            display_name=root.name or str(root),
            files=files,
            writable=bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_file_write", False)),
            terminal=bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_terminal_commands", False)),
        )
    except Exception as e:
        logger.error(f"Workspace error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/workspace", response_model=WorkspaceResponse)
async def set_workspace(request: WorkspaceChangeRequest):
    """Change the FILE_TOOL_ROOT at runtime."""
    try:
        new_root = Path(request.root).expanduser().resolve()
        if not new_root.exists():
            raise HTTPException(status_code=400, detail=f"Path does not exist: {new_root}")
        if not new_root.is_dir():
            raise HTTPException(status_code=400, detail=f"Path is not a directory: {new_root}")
        config.file_tool_root = str(new_root)
        try:
            overrides = read_runtime_override_payload(include_secrets=True, migrate_legacy=True)
            if not isinstance(overrides, dict):
                overrides = {}
            overrides["file_tool_root"] = str(new_root)
            write_runtime_override_payload(overrides)
        except Exception as persist_error:
            logger.warning(f"Failed to persist FILE_TOOL_ROOT override: {persist_error}")
        logger.info(f"FILE_TOOL_ROOT changed to: {new_root}")
        from agent.tools import _file_tool_root
        root = _file_tool_root()
        files = _build_file_tree(root, max_depth=2, max_items=150)
        return WorkspaceResponse(
            root=str(root),
            display_name=root.name or str(root),
            files=files,
            writable=bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_file_write", False)),
            terminal=bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_terminal_commands", False)),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Workspace change error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
