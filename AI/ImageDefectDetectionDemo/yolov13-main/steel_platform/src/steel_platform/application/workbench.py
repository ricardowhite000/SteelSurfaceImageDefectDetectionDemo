from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import BinaryIO

from steel_platform.application.errors import ApplicationError
from steel_platform.domain.ports import WorkbenchGateway
from steel_platform.domain.workbench import (
    JobInputRef,
    JobKind,
    ModelFormat,
    ModelPurpose,
    WorkbenchJobSpec,
)


class WorkbenchService:
    def __init__(self, gateway: WorkbenchGateway, *, allowed_devices: tuple[str, ...]) -> None:
        self.gateway = gateway
        self.allowed_devices = tuple(dict.fromkeys((*allowed_devices, "cpu")))

    def options(self, project_id: str) -> dict[str, object]:
        options = self.gateway.options(project_id)
        return {
            **options,
            "allowed_devices": list(self.allowed_devices),
            "presets": {
                "train": ["smoke", "formal"],
                "evaluate": ["fixed_val"],
                "infer": ["visual", "pseudo_label", "video"],
            },
        }

    def list_jobs(self, project_id: str) -> Sequence[dict[str, object]]:
        return self.gateway.list_jobs(project_id)

    def get_job(self, project_id: str, job_id: str) -> dict[str, object]:
        return self.gateway.get_job(project_id, job_id)

    def create_job(
        self,
        project_id: str,
        *,
        name: str,
        kind: str,
        preset: str,
        input_refs: Sequence[Mapping[str, object]],
        parameters: Mapping[str, object],
    ) -> dict[str, object]:
        clean_name = name.strip()
        if not clean_name or len(clean_name) > 200:
            raise ApplicationError("invalid_job_spec", "任务名称不能为空且不能超过200个字符", status_code=422)
        try:
            spec = WorkbenchJobSpec.create(
                kind=JobKind(kind),
                preset=preset,
                input_refs=tuple(
                    JobInputRef(
                        role=str(row.get("role", "")),
                        ref_id=str(row.get("ref_id", "")),
                        ref_type=str(row.get("ref_type", "")),
                        sha256_snapshot=(
                            str(row["sha256_snapshot"])
                            if row.get("sha256_snapshot") is not None
                            else None
                        ),
                    )
                    for row in input_refs
                ),
                parameters=parameters,
                allowed_devices=self.allowed_devices,
            )
        except (ValueError, TypeError) as exc:
            raise ApplicationError("invalid_job_spec", str(exc), status_code=422) from exc
        return self.gateway.create_job(project_id, clean_name, spec)

    def update_job(
        self,
        project_id: str,
        job_id: str,
        *,
        expected_revision: int,
        name: str,
        parameters: Mapping[str, object],
    ) -> dict[str, object]:
        current = self.gateway.get_job(project_id, job_id)
        clean_name = name.strip()
        if not clean_name or len(clean_name) > 200:
            raise ApplicationError("invalid_job_spec", "任务名称不能为空且不能超过200个字符", status_code=422)
        try:
            spec = WorkbenchJobSpec.create(
                kind=JobKind(str(current["kind"])),
                preset=str(current["preset"]),
                input_refs=tuple(
                    JobInputRef(
                        role=str(row["role"]),
                        ref_type=str(row["ref_type"]),
                        ref_id=str(row["ref_id"]),
                        sha256_snapshot=(
                            str(row["sha256_snapshot"])
                            if row.get("sha256_snapshot") is not None
                            else None
                        ),
                    )
                    for row in current["input_refs"]
                ),
                parameters=parameters,
                allowed_devices=self.allowed_devices,
            )
        except (ValueError, TypeError) as exc:
            raise ApplicationError("invalid_job_spec", str(exc), status_code=422) from exc
        return self.gateway.update_job(
            project_id,
            job_id,
            expected_revision=expected_revision,
            name=clean_name,
            spec=spec,
        )

    def prepare_job(
        self,
        project_id: str,
        job_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> dict[str, object]:
        if not idempotency_key.strip():
            raise ApplicationError("idempotency_key_required", "缺少幂等键", status_code=422)
        return self.gateway.prepare_job(
            project_id,
            job_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
        )

    def launch_terminal(
        self,
        project_id: str,
        job_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> dict[str, object]:
        if not idempotency_key.strip():
            raise ApplicationError("idempotency_key_required", "缺少幂等键", status_code=422)
        return self.gateway.launch_terminal(
            project_id,
            job_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
        )

    def read_log(self, project_id: str, job_id: str, *, after: int) -> dict[str, object]:
        if after < 0:
            raise ApplicationError("invalid_log_offset", "日志偏移量不能为负数", status_code=422)
        return self.gateway.read_log(project_id, job_id, after=after)

    def results(self, project_id: str, job_id: str) -> dict[str, object]:
        return self.gateway.results(project_id, job_id)

    def ingest_job(
        self,
        project_id: str,
        job_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> dict[str, object]:
        if not idempotency_key.strip():
            raise ApplicationError("idempotency_key_required", "缺少幂等键", status_code=422)
        return self.gateway.ingest_job(
            project_id,
            job_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
        )

    def list_models(self, project_id: str) -> Sequence[dict[str, object]]:
        return self.gateway.list_models(project_id)

    def import_model(
        self,
        project_id: str,
        *,
        name: str,
        weights_asset_id: str,
        model_format: str,
        purpose: str,
        class_names: Sequence[str] | None,
        source_note: str,
    ) -> dict[str, object]:
        clean_name = name.strip()
        if not clean_name or len(clean_name) > 200:
            raise ApplicationError("invalid_model_import", "模型名称不能为空且不能超过200个字符", status_code=422)
        try:
            normalized_format = ModelFormat(model_format)
            normalized_purpose = ModelPurpose(purpose)
        except ValueError as exc:
            raise ApplicationError("invalid_model_import", str(exc), status_code=422) from exc
        return self.gateway.import_model(
            project_id,
            name=clean_name,
            weights_asset_id=weights_asset_id,
            model_format=normalized_format.value,
            purpose=normalized_purpose.value,
            class_names=class_names,
            source_note=source_note.strip(),
        )

    def stage_model_file(
        self, project_id: str, *, filename: str, stream: BinaryIO
    ) -> dict[str, object]:
        clean_name = filename.strip()
        if not clean_name or len(clean_name) > 255:
            raise ApplicationError("invalid_model_file", "模型文件名无效", status_code=422)
        if not clean_name.lower().endswith((".pt", ".onnx")):
            raise ApplicationError(
                "invalid_model_file", "只允许上传 .pt 或 .onnx 模型文件", status_code=422
            )
        return self.gateway.stage_model_file(
            project_id, filename=clean_name, stream=stream
        )

    def stage_inference_file(
        self,
        project_id: str,
        *,
        filename: str,
        media_type: str,
        stream: BinaryIO,
    ) -> dict[str, object]:
        clean_name = filename.strip()
        allowed = (".bmp", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".mp4", ".webm", ".avi", ".mov", ".mkv", ".wmv", ".m4v")
        if not clean_name or len(clean_name) > 255 or not clean_name.lower().endswith(allowed):
            raise ApplicationError(
                "invalid_inference_file", "只允许上传常见图片或视频文件", status_code=422
            )
        return self.gateway.stage_inference_file(
            project_id,
            filename=clean_name,
            media_type=media_type or "application/octet-stream",
            stream=stream,
        )

    def cancel_job(
        self,
        project_id: str,
        job_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> dict[str, object]:
        if not idempotency_key.strip():
            raise ApplicationError("idempotency_key_required", "缺少幂等键", status_code=422)
        return self.gateway.cancel_job(
            project_id,
            job_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
        )
