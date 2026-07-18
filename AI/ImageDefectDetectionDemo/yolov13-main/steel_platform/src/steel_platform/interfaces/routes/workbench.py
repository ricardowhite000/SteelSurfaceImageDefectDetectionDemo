from __future__ import annotations

from typing import Annotated
from tempfile import SpooledTemporaryFile

from fastapi import APIRouter, Header, Query, Request, status

from steel_platform.application.errors import ApplicationError
from steel_platform.interfaces.api_models import (
    JobCreatePayload,
    JobPreparePayload,
    JobUpdatePayload,
    ModelImportPayload,
)


router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["workbench"])


@router.get("/workbench/options")
def workbench_options(project_id: str, request: Request) -> dict[str, object]:
    return request.app.state.services.workbench.options(project_id)


@router.get("/jobs")
def list_jobs(project_id: str, request: Request) -> list[dict[str, object]]:
    return list(request.app.state.services.workbench.list_jobs(project_id))


@router.post("/jobs", status_code=status.HTTP_201_CREATED)
def create_job(
    project_id: str, payload: JobCreatePayload, request: Request
) -> dict[str, object]:
    return request.app.state.services.workbench.create_job(
        project_id,
        name=payload.name,
        kind=payload.kind,
        preset=payload.preset,
        input_refs=[row.model_dump() for row in payload.input_refs],
        parameters=payload.parameters,
    )


@router.get("/jobs/{job_id}")
def get_job(project_id: str, job_id: str, request: Request) -> dict[str, object]:
    return request.app.state.services.workbench.get_job(project_id, job_id)


@router.put("/jobs/{job_id}")
def update_job(
    project_id: str,
    job_id: str,
    payload: JobUpdatePayload,
    request: Request,
) -> dict[str, object]:
    return request.app.state.services.workbench.update_job(
        project_id,
        job_id,
        expected_revision=payload.expected_revision,
        name=payload.name,
        parameters=payload.parameters,
    )


@router.post("/jobs/{job_id}/prepare")
def prepare_job(
    project_id: str,
    job_id: str,
    payload: JobPreparePayload,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    return request.app.state.services.workbench.prepare_job(
        project_id,
        job_id,
        expected_revision=payload.expected_revision,
        idempotency_key=idempotency_key,
    )


@router.post("/jobs/{job_id}/terminal-launch")
def launch_terminal(
    project_id: str,
    job_id: str,
    payload: JobPreparePayload,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    origin = request.headers.get("Origin")
    if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
        raise ApplicationError("origin_not_allowed", "终端只能由当前本机平台页面打开", status_code=403)
    return request.app.state.services.workbench.launch_terminal(
        project_id,
        job_id,
        expected_revision=payload.expected_revision,
        idempotency_key=idempotency_key,
    )


@router.get("/jobs/{job_id}/log")
def job_log(
    project_id: str,
    job_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
) -> dict[str, object]:
    return request.app.state.services.workbench.read_log(
        project_id, job_id, after=after
    )


@router.get("/jobs/{job_id}/results")
def job_results(project_id: str, job_id: str, request: Request) -> dict[str, object]:
    return request.app.state.services.workbench.results(project_id, job_id)


@router.post("/jobs/{job_id}/ingest")
def ingest_job(
    project_id: str,
    job_id: str,
    payload: JobPreparePayload,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    return request.app.state.services.workbench.ingest_job(
        project_id,
        job_id,
        expected_revision=payload.expected_revision,
        idempotency_key=idempotency_key,
    )


@router.post("/jobs/{job_id}/cancel")
def cancel_job(
    project_id: str,
    job_id: str,
    payload: JobPreparePayload,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    return request.app.state.services.workbench.cancel_job(
        project_id,
        job_id,
        expected_revision=payload.expected_revision,
        idempotency_key=idempotency_key,
    )


@router.get("/models")
def list_models(project_id: str, request: Request) -> list[dict[str, object]]:
    return list(request.app.state.services.workbench.list_models(project_id))


@router.post("/model-files", status_code=status.HTTP_201_CREATED)
async def upload_model_file(
    project_id: str,
    request: Request,
    filename: Annotated[str, Header(alias="X-Filename")],
) -> dict[str, object]:
    maximum = 1024 * 1024 * 1024
    size = 0
    with SpooledTemporaryFile(max_size=16 * 1024 * 1024, mode="w+b") as stream:
        async for chunk in request.stream():
            size += len(chunk)
            if size > maximum:
                raise ApplicationError(
                    "model_file_too_large", "模型文件不能超过 1 GiB", status_code=413
                )
            stream.write(chunk)
        if size == 0:
            raise ApplicationError("invalid_model_file", "模型文件不能为空", status_code=422)
        stream.seek(0)
        return request.app.state.services.workbench.stage_model_file(
            project_id, filename=filename, stream=stream
        )


@router.post("/inference-files", status_code=status.HTTP_201_CREATED)
async def upload_inference_file(
    project_id: str,
    request: Request,
    filename: Annotated[str, Header(alias="X-Filename")],
) -> dict[str, object]:
    maximum = 4 * 1024 * 1024 * 1024
    size = 0
    with SpooledTemporaryFile(max_size=32 * 1024 * 1024, mode="w+b") as stream:
        async for chunk in request.stream():
            size += len(chunk)
            if size > maximum:
                raise ApplicationError(
                    "inference_file_too_large", "推理文件不能超过 4 GiB", status_code=413
                )
            stream.write(chunk)
        if size == 0:
            raise ApplicationError("invalid_inference_file", "推理文件不能为空", status_code=422)
        stream.seek(0)
        return request.app.state.services.workbench.stage_inference_file(
            project_id,
            filename=filename,
            media_type=request.headers.get("Content-Type", "application/octet-stream"),
            stream=stream,
        )


@router.post("/model-imports", status_code=status.HTTP_201_CREATED)
def import_model(
    project_id: str, payload: ModelImportPayload, request: Request
) -> dict[str, object]:
    return request.app.state.services.workbench.import_model(
        project_id,
        name=payload.name,
        weights_asset_id=payload.weights_asset_id,
        model_format=payload.format,
        purpose=payload.purpose,
        class_names=payload.class_names,
        source_note=payload.source_note,
    )
