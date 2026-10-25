"""Owner-facing Echo Code foundation; protected by the normal API auth middleware."""
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agent import echo_code

router = APIRouter(prefix="/echo-code", tags=["echo code"])


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except echo_code.RuntimeUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "Project unavailable") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)[:300]) from exc


@router.get("/runtimes")
def runtimes():
    return echo_code.runtime_catalog()


@router.get("/projects/{project_id}")
def workspace(project_id: str):
    return _call(echo_code.workspace, project_id)


class StartSession(BaseModel):
    runtime_id: Literal["echo", "codex", "claude_code", "acp"]
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


@router.post("/projects/{project_id}/sessions")
def create_session(project_id: str, request: StartSession):
    return _call(echo_code.create_session, project_id, runtime_id=request.runtime_id,
                 idempotency_key=request.idempotency_key)
