from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Header, Request

from steel_platform.interfaces.api_models import CreateProjectPayload

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


@router.get("")
def list_projects(request: Request) -> list[dict[str, object]]:
    return [asdict(project) for project in request.app.state.services.projects.list_projects()]


@router.post("")
def create_project(
    payload: CreateProjectPayload,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    return asdict(request.app.state.services.projects.create_project(payload.to_domain(), idempotency_key))


@router.get("/{project_id}/explorer")
def explorer(project_id: str, request: Request) -> dict[str, object]:
    return request.app.state.services.explorer.tree(project_id)
