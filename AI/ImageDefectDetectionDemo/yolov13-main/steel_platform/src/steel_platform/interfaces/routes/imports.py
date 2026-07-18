from __future__ import annotations

from dataclasses import asdict
from io import BytesIO
from typing import Annotated

from fastapi import APIRouter, Body, Header, Request

from steel_platform.interfaces.api_models import ImportStartPayload, ManifestEntryPayload

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["imports"])


@router.post("/imports")
def start_import(project_id: str, payload: ImportStartPayload, request: Request) -> dict[str, object]:
    return asdict(request.app.state.services.imports.start(project_id, payload.name, payload.mode, payload.locator))


@router.get("/imports/{import_id}")
def get_import(project_id: str, import_id: str, request: Request) -> dict[str, object]:
    view = request.app.state.services.imports.get_import(project_id, import_id)
    return {"session": asdict(view.session), "entries": [asdict(entry) for entry in view.entries]}


@router.put("/imports/{import_id}/manifest")
def register_manifest(project_id: str, import_id: str, entries: list[ManifestEntryPayload], request: Request) -> list[dict[str, object]]:
    return [asdict(entry) for entry in request.app.state.services.imports.register_manifest(project_id, import_id, [entry.to_domain() for entry in entries])]


@router.put("/imports/{import_id}/entries/{entry_id}/content")
def upload_entry(project_id: str, import_id: str, entry_id: str, request: Request, content: Annotated[bytes, Body()]) -> dict[str, object]:
    return asdict(request.app.state.services.imports.upload_entry(project_id, import_id, entry_id, BytesIO(content)))


@router.post("/imports/{import_id}/validate")
def validate_import(project_id: str, import_id: str, request: Request) -> dict[str, object]:
    return asdict(request.app.state.services.imports.validate(project_id, import_id))


@router.post("/imports/{import_id}/commit")
def commit_import(project_id: str, import_id: str, request: Request, idempotency_key: Annotated[str, Header(alias="Idempotency-Key")]) -> dict[str, object]:
    return asdict(request.app.state.services.imports.commit(project_id, import_id, idempotency_key=idempotency_key))


@router.post("/imports/{import_id}/cancel")
def cancel_import(project_id: str, import_id: str, request: Request) -> dict[str, object]:
    return asdict(request.app.state.services.imports.cancel(project_id, import_id))
