from pathlib import Path

from fastapi import APIRouter, Request

from steel_platform.interfaces.api_models import RuntimeProfilePayload, SourceBindingPayload


router = APIRouter(tags=["portability"])


@router.get("/api/v1/runtime-profiles")
def runtime_profiles(request: Request) -> list[dict[str, object]]:
    return request.app.state.services.runtime_profiles.list()


@router.post("/api/v1/runtime-profiles", status_code=201)
def add_runtime_profile(payload: RuntimeProfilePayload, request: Request) -> dict[str, object]:
    return request.app.state.services.runtime_profiles.add(**payload.model_dump())


@router.post("/api/v1/runtime-profiles/{profile_id}/check")
def check_runtime_profile(profile_id: str, request: Request) -> dict[str, object]:
    return request.app.state.services.runtime_profiles.check(profile_id)


@router.get("/api/v1/projects/{project_id}/source-bindings")
def source_bindings(project_id: str, request: Request) -> list[dict[str, object]]:
    return request.app.state.services.source_bindings.list(project_id)


@router.put("/api/v1/projects/{project_id}/source-bindings/{source_id}")
def update_source_binding(project_id: str, source_id: str, payload: SourceBindingPayload, request: Request) -> dict[str, object]:
    return request.app.state.services.source_bindings.bind(project_id, source_id, Path(payload.locator))
