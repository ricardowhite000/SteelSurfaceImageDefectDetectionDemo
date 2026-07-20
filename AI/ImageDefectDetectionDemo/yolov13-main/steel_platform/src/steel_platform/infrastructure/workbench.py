from __future__ import annotations

from pathlib import Path, PurePosixPath
import csv
import hashlib
from io import StringIO
import json
import os
import shutil
import subprocess
import sys
from typing import Any
from typing import BinaryIO
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from steel_platform.application.errors import ApplicationError, NotFoundError, RevisionConflictError
from steel_platform.domain.workbench import JobInputRef, JobKind, WorkbenchJobSpec
from steel_platform.infrastructure.artifacts import ArtifactRef, LocalArtifactStore
from steel_platform.infrastructure.config import PlatformSettings
from steel_platform.infrastructure.database import make_engine
from steel_platform.infrastructure.models import (
    AssetModel,
    DatasetVersionModel,
    JobLineageRefModel,
    JobModel,
    ModelVersionModel,
    ProjectModel,
    SourceRootModel,
    utc_now,
)
from steel_platform.infrastructure.runtime_profiles import RuntimeProfileStore
from steel_platform.infrastructure.workbench_executor import TerminalLauncher, WindowsPowerShellLauncher


class SqlWorkbenchGateway:
    def __init__(
        self,
        settings: PlatformSettings,
        artifact_store: LocalArtifactStore,
        terminal_launcher: TerminalLauncher | None = None,
        runtime_profiles: RuntimeProfileStore | None = None,
    ) -> None:
        self.settings = settings
        self.store = artifact_store
        self.engine = make_engine(settings.database_url)
        self.terminal_launcher = terminal_launcher or WindowsPowerShellLauncher()
        self.runtime_profiles = runtime_profiles or RuntimeProfileStore(
            settings.artifact_root / "machine" / "runtime-profiles.json"
        )

    def options(self, project_id: str) -> dict[str, object]:
        with Session(self.engine) as session:
            self._require_project(session, project_id)
            datasets = session.scalars(
                select(DatasetVersionModel)
                .where(DatasetVersionModel.project_id == project_id)
                .order_by(DatasetVersionModel.created_at.desc(), DatasetVersionModel.id)
            ).all()
            models = session.scalars(
                select(ModelVersionModel)
                .where(ModelVersionModel.project_id == project_id)
                .order_by(ModelVersionModel.created_at.desc(), ModelVersionModel.id)
            ).all()
            sources = session.scalars(
                select(SourceRootModel)
                .where(SourceRootModel.project_id == project_id)
                .order_by(SourceRootModel.kind, SourceRootModel.name, SourceRootModel.id)
            ).all()
            return {
                "runtime_profiles": self.runtime_profiles.list(),
                "datasets": [
                    {
                        "id": row.id,
                        "name": row.name,
                        "schema_version": row.schema_version,
                        "sha256": row.sha256,
                    }
                    for row in datasets
                ],
                "models": [self._model_view(row) for row in models],
                "sources": [
                    {
                        "id": row.id,
                        "name": row.name,
                        "kind": row.kind,
                        "status": row.status,
                        "manifest_sha256": row.manifest_sha256,
                    }
                    for row in sources
                ],
            }

    def list_jobs(self, project_id: str) -> list[dict[str, object]]:
        with Session(self.engine) as session:
            self._require_project(session, project_id)
            rows = session.scalars(
                select(JobModel)
                .where(JobModel.project_id == project_id)
                .order_by(JobModel.created_at.desc(), JobModel.id)
            ).all()
            return [self._job_view(session, row) for row in rows]

    def list_models(self, project_id: str) -> list[dict[str, object]]:
        with Session(self.engine) as session:
            self._require_project(session, project_id)
            rows = session.scalars(
                select(ModelVersionModel)
                .where(ModelVersionModel.project_id == project_id)
                .order_by(ModelVersionModel.created_at.desc(), ModelVersionModel.id)
            ).all()
            return [self._model_view(row) for row in rows]

    def stage_model_file(
        self, project_id: str, *, filename: str, stream: BinaryIO
    ) -> dict[str, object]:
        safe_name = Path(filename).name
        if safe_name != filename or safe_name in {".", ".."}:
            raise ApplicationError("invalid_model_file", "模型文件名包含非法路径", status_code=422)
        suffix = Path(safe_name).suffix.lower()
        if suffix not in {".pt", ".onnx"}:
            raise ApplicationError("invalid_model_file", "只允许 .pt 或 .onnx 文件", status_code=422)
        with Session(self.engine) as session:
            self._require_project(session, project_id)
            ref = self.store.put_stream(stream, media_type="application/octet-stream")
            asset = AssetModel(
                project_id=project_id,
                kind="model_file",
                relative_path=f"model-imports/{uuid4().hex}-{safe_name}",
                storage_key=ref.storage_key,
                sha256=ref.sha256,
                size_bytes=ref.size_bytes,
                media_type=ref.media_type,
            )
            session.add(asset)
            session.commit()
            return {
                "asset_id": asset.id,
                "name": safe_name,
                "sha256": asset.sha256,
                "size_bytes": asset.size_bytes,
                "format": suffix.lstrip("."),
            }

    def stage_inference_file(
        self,
        project_id: str,
        *,
        filename: str,
        media_type: str,
        stream: BinaryIO,
    ) -> dict[str, object]:
        safe_name = Path(filename).name
        allowed = {".bmp", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".mp4", ".webm", ".avi", ".mov", ".mkv", ".wmv", ".m4v"}
        if safe_name != filename or Path(safe_name).suffix.lower() not in allowed:
            raise ApplicationError("invalid_inference_file", "推理文件名或格式无效", status_code=422)
        with Session(self.engine) as session:
            self._require_project(session, project_id)
            ref = self.store.put_stream(stream, media_type=media_type)
            asset = AssetModel(
                project_id=project_id,
                kind="inference_source",
                relative_path=f"inference-inputs/{uuid4().hex}-{safe_name}",
                storage_key=ref.storage_key,
                sha256=ref.sha256,
                size_bytes=ref.size_bytes,
                media_type=ref.media_type,
            )
            session.add(asset)
            session.commit()
            return {
                "asset_id": asset.id,
                "name": safe_name,
                "sha256": asset.sha256,
                "size_bytes": asset.size_bytes,
                "media_type": asset.media_type,
            }

    def import_model(
        self,
        project_id: str,
        *,
        name: str,
        weights_asset_id: str,
        model_format: str,
        purpose: str,
        class_names: list[str] | tuple[str, ...] | None,
        source_note: str,
    ) -> dict[str, object]:
        with Session(self.engine) as session:
            self._require_project(session, project_id)
            asset = session.scalar(
                select(AssetModel).where(
                    AssetModel.id == weights_asset_id,
                    AssetModel.project_id == project_id,
                )
            )
            if asset is None:
                raise NotFoundError("模型文件资产不存在或不属于当前项目")
            suffix = Path(asset.relative_path or "").suffix.lower().lstrip(".")
            if suffix and suffix != model_format:
                raise ApplicationError("invalid_model_import", "模型格式与文件扩展名不一致", status_code=422)
            if asset.storage_key:
                source_ref = ArtifactRef(
                    asset.storage_key, asset.sha256, asset.size_bytes, asset.media_type
                )
                if not self.store.verify(source_ref):
                    raise ApplicationError("artifact_hash_mismatch", "模型文件哈希不一致", status_code=409)
                weights_ref = source_ref
            elif asset.source_root_id and asset.relative_path:
                source_root = session.get(SourceRootModel, asset.source_root_id)
                source_path = Path(source_root.path) / asset.relative_path if source_root else None
                if source_path is None or not source_path.is_file():
                    raise ApplicationError("source_offline", "模型文件来源当前不可用", status_code=422)
                with source_path.open("rb") as stream:
                    weights_ref = self.store.put_stream(
                        stream,
                        media_type="application/octet-stream",
                        expected_sha256=asset.sha256,
                    )
            else:
                raise ApplicationError("artifact_missing", "模型文件没有可读取内容", status_code=422)
            model = ModelVersionModel(
                project_id=project_id,
                source_asset_id=asset.id,
                name=name,
                format=model_format,
                purpose=purpose,
                verification_status="pending" if model_format == "pt" else "archived",
                evaluation_status="not_evaluated",
                class_schema_json=list(class_names) if class_names else None,
                weights_sha256=weights_ref.sha256,
                source_note=source_note or None,
                weights_key=weights_ref.storage_key,
            )
            session.add(model)
            session.flush()
            verification_job_id: str | None = None
            if model_format == "pt":
                job = JobModel(
                    project_id=project_id,
                    name=f"校验模型：{name}",
                    kind=JobKind.VERIFY_MODEL.value,
                    preset="metadata",
                    status="draft",
                    spec_json={
                        "kind": JobKind.VERIFY_MODEL.value,
                        "preset": "metadata",
                        "target_model_id": model.id,
                        "parameters": {"device": self.settings.device},
                        "input_refs": [
                            {"role": "model_asset", "ref_type": "asset", "ref_id": asset.id}
                        ],
                    },
                )
                session.add(job)
                session.flush()
                session.add(
                    JobLineageRefModel(
                        job_id=job.id,
                        direction="input",
                        role="model_asset",
                        ref_type="asset",
                        ref_id=asset.id,
                        sha256_snapshot=asset.sha256,
                    )
                )
                verification_job_id = job.id
            session.commit()
            return {
                "model": self._model_view(model),
                "verification_job_id": verification_job_id,
            }

    def get_job(self, project_id: str, job_id: str) -> dict[str, object]:
        with Session(self.engine) as session:
            return self._job_view(session, self._require_job(session, project_id, job_id))

    def create_job(
        self, project_id: str, name: str, spec: WorkbenchJobSpec
    ) -> dict[str, object]:
        with Session(self.engine) as session:
            self._require_project(session, project_id)
            self._validate_runtime_profile(
                spec.runtime_profile_id, spec.parameters.get("device")
            )
            verified_refs = [self._verify_ref(session, project_id, ref) for ref in spec.input_refs]
            if spec.kind in {JobKind.INFER, JobKind.EVALUATE}:
                model_ref = next(ref for ref in spec.input_refs if ref.role == "model")
                detector = self._require_model(session, project_id, model_ref.ref_id)
                if detector.purpose != "detector":
                    raise ApplicationError("model_purpose_mismatch", "基础权重不能直接用于推理", status_code=422)
                if detector.class_schema_json != list(self.settings.classes):
                    raise ApplicationError("model_schema_mismatch", "模型类别顺序与当前项目不一致", status_code=422)
            job = JobModel(
                project_id=project_id,
                name=name,
                kind=spec.kind.value,
                preset=spec.preset,
                status="draft",
                spec_json={
                    "kind": spec.kind.value,
                    "preset": spec.preset,
                    "parameters": dict(spec.parameters),
                    "runtime_profile_id": spec.runtime_profile_id,
                    "input_refs": [
                        {"role": ref.role, "ref_type": ref.ref_type, "ref_id": ref.ref_id}
                        for ref, _ in verified_refs
                    ],
                },
            )
            session.add(job)
            session.flush()
            for ref, sha256_snapshot in verified_refs:
                session.add(
                    JobLineageRefModel(
                        job_id=job.id,
                        direction="input",
                        role=ref.role,
                        ref_type=ref.ref_type,
                        ref_id=ref.ref_id,
                        sha256_snapshot=sha256_snapshot,
                    )
                )
            session.commit()
            session.refresh(job)
            return self._job_view(session, job)

    def update_job(
        self,
        project_id: str,
        job_id: str,
        *,
        expected_revision: int,
        name: str,
        spec: WorkbenchJobSpec,
    ) -> dict[str, object]:
        with Session(self.engine) as session:
            job = self._require_job(session, project_id, job_id)
            if job.revision != expected_revision:
                raise RevisionConflictError(expected_revision, job.revision)
            if job.status != "draft":
                raise ApplicationError("job_not_editable", "只有草稿任务可以修改参数", status_code=409)
            for ref in spec.input_refs:
                self._verify_ref(session, project_id, ref)
            self._validate_runtime_profile(spec.runtime_profile_id, spec.parameters.get("device"))
            job.name = name
            job.spec_json = {
                "kind": spec.kind.value,
                "preset": spec.preset,
                "parameters": dict(spec.parameters),
                "runtime_profile_id": spec.runtime_profile_id,
                "input_refs": [
                    {"role": ref.role, "ref_type": ref.ref_type, "ref_id": ref.ref_id}
                    for ref in spec.input_refs
                ],
            }
            job.revision += 1
            session.commit()
            session.refresh(job)
            return self._job_view(session, job)

    def prepare_job(
        self,
        project_id: str,
        job_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> dict[str, object]:
        with Session(self.engine) as session:
            job = self._require_job(session, project_id, job_id)
            progress = dict(job.progress_json or {})
            if job.status == "ready" and progress.get("prepare_idempotency_key") == idempotency_key:
                return self._job_view(session, job)
            if job.revision != expected_revision:
                raise RevisionConflictError(expected_revision, job.revision)
            if job.status != "draft":
                raise ApplicationError("job_not_editable", "只有草稿任务可以生成命令", status_code=409)
            arguments, cwd, output_dir, expected_outputs = self._build_command(session, job)
            workspace = self.settings.artifact_root / "workbench" / "jobs" / job.id
            workspace.mkdir(parents=True, exist_ok=True)
            command = subprocess.list2cmdline(arguments)
            command_ref = self.store.put_bytes(
                (command + "\n").encode("utf-8"), media_type="text/x-powershell"
            )
            relative_workspace = workspace.relative_to(self.settings.artifact_root).as_posix()
            runtime = {
                "arguments": arguments,
                "cwd": str(cwd),
                "output_dir": str(output_dir),
                "expected_outputs": expected_outputs,
            }
            job.spec_json = {**job.spec_json, "runtime": runtime}
            job.command_key = command_ref.storage_key
            job.workspace_key = relative_workspace
            job.log_key = f"{relative_workspace}/job.log"
            job.status = "ready"
            job.revision += 1
            job.progress_json = {
                **progress,
                "current": 0,
                "total": int(job.spec_json["parameters"].get("epochs", 1)),
                "prepare_idempotency_key": idempotency_key,
            }
            session.commit()
            session.refresh(job)
            return self._job_view(session, job)

    def launch_terminal(
        self,
        project_id: str,
        job_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> dict[str, object]:
        with Session(self.engine) as session:
            job = self._require_job(session, project_id, job_id)
            progress = dict(job.progress_json or {})
            if progress.get("terminal_launch_idempotency_key") == idempotency_key:
                return self._job_view(session, job)
            if job.revision != expected_revision:
                raise RevisionConflictError(expected_revision, job.revision)
            if job.status != "ready" or not job.workspace_key:
                raise ApplicationError("job_not_ready", "只有就绪任务可以打开执行终端", status_code=409)
            workspace = (self.settings.artifact_root / Path(job.workspace_key)).resolve()
            artifact_root = self.settings.artifact_root.resolve()
            if artifact_root not in workspace.parents:
                raise ApplicationError("illegal_workspace_key", "任务工作目录越界", status_code=500)
            workspace.mkdir(parents=True, exist_ok=True)
            settings_path = workspace / "worker-settings.json"
            wrapper_path = workspace / "launch.ps1"
            self._atomic_write(
                settings_path,
                json.dumps(self.settings.model_dump(mode="json"), ensure_ascii=False, indent=2),
            )
            worker_arguments = [
                sys.executable,
                "-m",
                "steel_platform.interfaces.job_worker",
                "--settings",
                str(settings_path),
                "--job",
                job.id,
            ]
            display_command = subprocess.list2cmdline(worker_arguments)
            wrapper = (
                "$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()\n"
                f"Write-Host '任务：{self._ps_quote(job.name)}' -ForegroundColor Cyan\n"
                f"Write-Host 'YOLO命令：{self._ps_quote(self._job_view(session, job)['command'] or '')}'\n"
                "$answer = Read-Host '检查页面中的参数后，直接按 Enter 开始；输入 C 取消'\n"
                "if ($answer -match '^[Cc]$') { Write-Host '已取消，任务保持就绪状态。'; exit 0 }\n"
                f"Write-Host '执行器：{self._ps_quote(display_command)}' -ForegroundColor DarkGray\n"
                f"& '{self._ps_quote(sys.executable)}' '-m' 'steel_platform.interfaces.job_worker' "
                f"'--settings' '{self._ps_quote(str(settings_path))}' '--job' '{self._ps_quote(job.id)}'\n"
                "$code = $LASTEXITCODE\n"
                "Write-Host \"任务进程已结束，退出码：$code\"\n"
            )
            self._atomic_write(wrapper_path, wrapper, encoding="utf-8-sig")
            progress["terminal_launch_idempotency_key"] = idempotency_key
            progress["terminal_launched_at"] = job.created_at.isoformat()
            job.progress_json = progress
            job.revision += 1
            session.commit()
            session.refresh(job)
            try:
                self.terminal_launcher.launch(wrapper_path, working_directory=workspace)
            except Exception:
                with Session(self.engine) as rollback_session:
                    current = self._require_job(rollback_session, project_id, job_id)
                    restored = dict(current.progress_json or {})
                    restored.pop("terminal_launch_idempotency_key", None)
                    restored.pop("terminal_launched_at", None)
                    current.progress_json = restored
                    current.revision += 1
                    rollback_session.commit()
                raise
            return self._job_view(session, job)

    def read_log(self, project_id: str, job_id: str, *, after: int) -> dict[str, object]:
        with Session(self.engine) as session:
            job = self._require_job(session, project_id, job_id)
            if not job.log_key:
                return {"content": "", "next_offset": 0}
            log_path = (self.settings.artifact_root / Path(job.log_key)).resolve()
            root = self.settings.artifact_root.resolve()
            if root not in log_path.parents:
                raise ApplicationError("illegal_workspace_key", "任务日志路径越界", status_code=500)
            if not log_path.is_file():
                return {"content": "", "next_offset": 0}
            size = log_path.stat().st_size
            offset = min(after, size)
            with log_path.open("rb") as stream:
                stream.seek(offset)
                content = stream.read(64 * 1024)
            return {
                "content": content.decode("utf-8", errors="replace"),
                "next_offset": offset + len(content),
            }

    def results(self, project_id: str, job_id: str) -> dict[str, object]:
        with Session(self.engine) as session:
            job = self._require_job(session, project_id, job_id)
            if not job.result_manifest_key:
                return {"job_id": job.id, "status": job.status, "files": [], "series": []}
            manifest = json.loads(self.store.open(job.result_manifest_key).read().decode("utf-8"))
            series: list[dict[str, object]] = []
            for item in manifest.get("files", []):
                content_url = f"/api/v1/projects/{project_id}/assets/{item['asset_id']}/content"
                relative_path = str(item.get("relative_path") or item["asset_id"]).replace("\\", "/")
                item["content_url"] = content_url
                item["download_url"] = f"{content_url}?download=1"
                item["download_name"] = PurePosixPath(relative_path).name
                if item.get("relative_path") == "results.csv":
                    asset = session.get(AssetModel, item["asset_id"])
                    if asset is not None and asset.storage_key:
                        text = self.store.open(asset.storage_key).read().decode("utf-8-sig", errors="replace")
                        for row in csv.DictReader(StringIO(text)):
                            normalized: dict[str, object] = {}
                            for key, value in row.items():
                                try:
                                    normalized[key.strip()] = float(value) if value not in (None, "") else None
                                except ValueError:
                                    normalized[key.strip()] = value
                            series.append(normalized)
            return {**manifest, "status": job.status, "series": series}

    def ingest_job(
        self,
        project_id: str,
        job_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> dict[str, object]:
        from steel_platform.infrastructure.workbench_results import ingest_job_outputs

        with Session(self.engine) as session:
            job = self._require_job(session, project_id, job_id)
            progress = dict(job.progress_json or {})
            if progress.get("ingest_idempotency_key") == idempotency_key:
                return self._job_view(session, job)
            if job.revision != expected_revision:
                raise RevisionConflictError(expected_revision, job.revision)
            if job.status == "running":
                raise ApplicationError(
                    "job_still_running", "任务仍在运行，不能人工导入结果", status_code=409
                )
            if not job.workspace_key or not job.spec_json.get("runtime"):
                raise ApplicationError("job_not_ready", "任务尚未生成工作目录", status_code=409)
        ingest_job_outputs(self.settings, job_id)
        with Session(self.engine) as session:
            job = self._require_job(session, project_id, job_id)
            progress = dict(job.progress_json or {})
            progress["ingest_idempotency_key"] = idempotency_key
            job.progress_json = progress
            job.status = "succeeded"
            job.exit_code = 0
            job.finished_at = job.finished_at or utc_now()
            job.revision += 1
            session.commit()
            session.refresh(job)
            return self._job_view(session, job)

    def cancel_job(
        self,
        project_id: str,
        job_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> dict[str, object]:
        with Session(self.engine) as session:
            job = self._require_job(session, project_id, job_id)
            progress = dict(job.progress_json or {})
            if progress.get("cancel_idempotency_key") == idempotency_key:
                return self._job_view(session, job)
            if job.revision != expected_revision:
                raise RevisionConflictError(expected_revision, job.revision)
            if job.status in {"draft", "ready"}:
                job.status = "cancelled"
                job.finished_at = utc_now()
                job.error_message = "用户取消"
            elif job.status == "running":
                job.cancel_requested_at = utc_now()
            else:
                raise ApplicationError("job_not_cancellable", "当前任务状态不能取消", status_code=409)
            progress["cancel_idempotency_key"] = idempotency_key
            job.progress_json = progress
            job.revision += 1
            session.commit()
            session.refresh(job)
            return self._job_view(session, job)

    def _build_command(
        self, session: Session, job: JobModel
    ) -> tuple[list[str], Path, Path, list[str]]:
        refs = {row["role"]: row for row in job.spec_json["input_refs"]}
        parameters = job.spec_json["parameters"]
        python_executable, root = self._execution_environment(job)
        workspace = self.settings.artifact_root / "workbench" / "jobs" / job.id
        output = workspace / "output"
        if job.kind == JobKind.TRAIN.value:
            dataset = self._require_dataset(session, job.project_id, refs["dataset"]["ref_id"])
            model = self._require_model(session, job.project_id, refs["model"]["ref_id"])
            data_yaml = self.settings.artifact_root / "materialized" / "datasets" / dataset.id / "data.yaml"
            if not data_yaml.is_file():
                raise ApplicationError("dataset_materialization_missing", "数据集缺少data.yaml", status_code=422)
            weights = self._materialize_weights(model, workspace)
            arguments = [
                python_executable,
                str(root / "steel_tutorial" / "05_train.py"),
                "--data", str(data_yaml),
                "--weights", str(weights),
                "--epochs", str(parameters["epochs"]),
                "--batch", str(parameters["batch"]),
                "--imgsz", str(parameters["imgsz"]),
                "--patience", str(parameters["patience"]),
                "--device", str(parameters["device"]),
                "--workers", str(parameters["workers"]),
                "--seed", str(parameters["seed"]),
                "--project", str(workspace),
                "--name", "output",
                "--amp" if parameters["amp"] else "--no-amp",
            ]
            if job.preset == "smoke":
                arguments.append("--smoke")
            return arguments, root, output, ["weights/best.pt", "weights/last.pt", "results.csv"]
        if job.kind == JobKind.EVALUATE.value:
            dataset = self._require_dataset(session, job.project_id, refs["dataset"]["ref_id"])
            model = self._require_model(session, job.project_id, refs["model"]["ref_id"])
            data_yaml = self.settings.artifact_root / "materialized" / "datasets" / dataset.id / "data.yaml"
            if not data_yaml.is_file():
                raise ApplicationError("dataset_materialization_missing", "数据集缺少data.yaml", status_code=422)
            weights = self._materialize_weights(model, workspace)
            arguments = [
                python_executable,
                "-m", "steel_tutorial.06_evaluate",
                "--data", str(data_yaml),
                "--weights", str(weights),
                "--imgsz", str(parameters["imgsz"]),
                "--batch", str(parameters["batch"]),
                "--device", str(parameters["device"]),
                "--workers", str(parameters["workers"]),
                "--project", str(workspace),
                "--name", "output",
            ]
            return arguments, root, output, ["metrics_summary.json"]
        if job.kind == JobKind.INFER.value:
            model = self._require_model(session, job.project_id, refs["model"]["ref_id"])
            weights = self._materialize_weights(model, workspace)
            source_ref = refs["source"]
            if source_ref["ref_type"] == "source":
                source = session.scalar(
                    select(SourceRootModel).where(
                        SourceRootModel.id == source_ref["ref_id"],
                        SourceRootModel.project_id == job.project_id,
                    )
                )
                if source is None or source.status != "available":
                    raise ApplicationError("source_offline", "推理数据源当前不可用", status_code=422)
                source_assets = session.scalars(
                    select(AssetModel)
                    .where(
                        AssetModel.project_id == job.project_id,
                        AssetModel.source_root_id == source.id,
                        AssetModel.kind == "image",
                    )
                    .order_by(AssetModel.relative_path, AssetModel.id)
                ).all()
                if not source_assets:
                    raise ApplicationError("empty_inference_source", "推理数据源没有已登记图片", status_code=422)
                source_path = self._materialize_source_assets(source, source_assets, workspace)
            elif source_ref["ref_type"] == "asset":
                asset = session.scalar(
                    select(AssetModel).where(
                        AssetModel.id == source_ref["ref_id"],
                        AssetModel.project_id == job.project_id,
                    )
                )
                if asset is None:
                    raise NotFoundError("推理资产不存在")
                if asset.storage_key:
                    source_path = self._materialize_asset(asset, workspace)
                elif asset.source_root_id and asset.relative_path:
                    source_root = session.get(SourceRootModel, asset.source_root_id)
                    if source_root is None:
                        raise ApplicationError("source_offline", "推理资产来源不存在", status_code=422)
                    source_path = Path(source_root.path) / asset.relative_path
                else:
                    raise ApplicationError("source_offline", "推理资产没有可读取内容", status_code=422)
            else:
                raise ApplicationError("invalid_job_spec", "推理来源必须是数据源或资产", status_code=422)
            if job.preset == "pseudo_label":
                if source_ref["ref_type"] != "source":
                    raise ApplicationError("invalid_job_spec", "辅助标注预设需要图片数据源", status_code=422)
                assets = source_assets
                source_list = workspace / "sources.txt"
                self._atomic_write(
                    source_list,
                    "\n".join(str(path.resolve()) for path in sorted(source_path.iterdir()) if path.is_file())
                    + ("\n" if assets else ""),
                )
                arguments = [
                    python_executable,
                    "-m", "steel_platform.interfaces.inference_runner",
                    "--source-list", str(source_list),
                    "--weights", str(weights),
                    "--output", str(output),
                    "--batch", "1",
                    "--device", str(parameters["device"]),
                    "--conf", str(parameters["conf"]),
                    "--review-conf", str(parameters["review_conf"]),
                    "--imgsz", str(parameters["imgsz"]),
                    "--classes", *self.settings.classes,
                ]
                job.spec_json = {
                    **job.spec_json,
                    "parameters": {**parameters, "source_count": len(assets)},
                }
                return arguments, root, output, ["pseudo_review.csv"]
            arguments = [
                python_executable,
                "-m", "steel_tutorial.07_infer",
                "--source", str(source_path),
                "--weights", str(weights),
                "--conf", str(parameters["conf"]),
                "--imgsz", str(parameters["imgsz"]),
                "--device", str(parameters["device"]),
                "--project", str(workspace),
                "--name", "output",
            ]
            if parameters.get("save_crop"):
                arguments.append("--save-crop")
            return arguments, root, output, ["detections.csv"]
        if job.kind == JobKind.VERIFY_MODEL.value:
            target_model_id = job.spec_json.get("target_model_id")
            model = self._require_model(session, job.project_id, str(target_model_id))
            weights = self._materialize_weights(model, workspace)
            arguments = [
                python_executable,
                "-m", "steel_platform.interfaces.model_verifier",
                "--weights", str(weights),
                "--output", str(output / "metadata.json"),
            ]
            return arguments, root, output, ["metadata.json"]
        raise ApplicationError("unsupported_job_kind", f"尚未支持任务类型：{job.kind}", status_code=422)

    def _verify_ref(
        self, session: Session, project_id: str, ref: JobInputRef
    ) -> tuple[JobInputRef, str | None]:
        if ref.ref_type == "dataset":
            row = self._require_dataset(session, project_id, ref.ref_id)
            return ref, row.sha256
        if ref.ref_type == "model":
            row = self._require_model(session, project_id, ref.ref_id)
            if row.verification_status != "ready":
                raise ApplicationError("model_not_ready", "模型尚未通过验证", status_code=422)
            return ref, row.weights_sha256
        if ref.ref_type == "source":
            row = session.scalar(
                select(SourceRootModel).where(
                    SourceRootModel.id == ref.ref_id,
                    SourceRootModel.project_id == project_id,
                )
            )
            if row is None:
                raise NotFoundError("数据源不存在或不属于当前项目")
            return ref, row.manifest_sha256
        if ref.ref_type == "asset":
            row = session.scalar(
                select(AssetModel).where(AssetModel.id == ref.ref_id, AssetModel.project_id == project_id)
            )
            if row is None:
                raise NotFoundError("资产不存在或不属于当前项目")
            return ref, row.sha256
        raise ApplicationError("invalid_job_spec", f"不支持的输入引用：{ref.ref_type}", status_code=422)

    @staticmethod
    def _require_project(session: Session, project_id: str) -> ProjectModel:
        row = session.get(ProjectModel, project_id)
        if row is None:
            raise NotFoundError("项目不存在")
        return row

    @staticmethod
    def _require_dataset(session: Session, project_id: str, dataset_id: str) -> DatasetVersionModel:
        row = session.scalar(
            select(DatasetVersionModel).where(
                DatasetVersionModel.id == dataset_id,
                DatasetVersionModel.project_id == project_id,
            )
        )
        if row is None:
            raise NotFoundError("数据集不存在或不属于当前项目")
        return row

    @staticmethod
    def _require_model(session: Session, project_id: str, model_id: str) -> ModelVersionModel:
        row = session.scalar(
            select(ModelVersionModel).where(
                ModelVersionModel.id == model_id,
                ModelVersionModel.project_id == project_id,
            )
        )
        if row is None:
            raise NotFoundError("模型不存在或不属于当前项目")
        return row

    @staticmethod
    def _require_job(session: Session, project_id: str, job_id: str) -> JobModel:
        row = session.scalar(
            select(JobModel).where(JobModel.id == job_id, JobModel.project_id == project_id)
        )
        if row is None:
            raise NotFoundError("任务不存在或不属于当前项目")
        return row

    def _job_view(self, session: Session, row: JobModel) -> dict[str, object]:
        command = None
        if row.command_key:
            command = self.store.resolve(
                ArtifactRef(row.command_key, "", 0, "text/x-powershell")
            ).read_text(encoding="utf-8").strip()
        inputs = session.scalars(
            select(JobLineageRefModel)
            .where(JobLineageRefModel.job_id == row.id, JobLineageRefModel.direction == "input")
            .order_by(JobLineageRefModel.role, JobLineageRefModel.id)
        ).all()
        return {
            "id": row.id,
            "project_id": row.project_id,
            "name": row.name,
            "kind": row.kind,
            "preset": row.preset,
            "runtime_profile_id": row.spec_json.get("runtime_profile_id"),
            "status": row.status,
            "revision": row.revision,
            "parameters": row.spec_json.get("parameters", {}),
            "input_refs": [
                {
                    "role": item.role,
                    "ref_type": item.ref_type,
                    "ref_id": item.ref_id,
                    "sha256_snapshot": item.sha256_snapshot,
                }
                for item in inputs
            ],
            "command": command,
            "progress": row.progress_json or {},
            "runtime": row.spec_json.get("runtime"),
            "workspace_key": row.workspace_key,
            "log_key": row.log_key,
            "result_manifest_key": row.result_manifest_key,
            "error_message": row.error_message,
            "exit_code": row.exit_code,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "created_at": row.created_at.isoformat(),
        }

    def _validate_runtime_profile(
        self, profile_id: str | None, device: object
    ) -> dict[str, object] | None:
        if not profile_id:
            return None
        profile = self.runtime_profiles.get(profile_id)
        devices = {str(item) for item in profile.get("devices") or []}
        if str(device) not in devices:
            raise ApplicationError(
                "runtime_device_mismatch",
                f"运行环境不支持设备：{device}",
                status_code=422,
            )
        return profile

    def _execution_environment(self, job: JobModel) -> tuple[str, Path]:
        profile_id = job.spec_json.get("runtime_profile_id")
        if not profile_id:
            return (
                self.settings.yolo_python,
                self.settings.yolo_project_root or Path(__file__).resolve().parents[4],
            )
        profile = self._validate_runtime_profile(
            str(profile_id), job.spec_json.get("parameters", {}).get("device")
        )
        assert profile is not None
        executable = Path(str(profile["python_executable"]))
        project_root = Path(str(profile["project_root"]))
        if not executable.is_file() or not project_root.is_dir():
            raise ApplicationError(
                "runtime_unavailable",
                "所选运行环境的Python解释器或YOLO项目目录不可用，请先执行运行环境检查",
                status_code=422,
            )
        return str(executable), project_root

    def _materialize_weights(self, model: ModelVersionModel, workspace: Path) -> Path:
        suffix = ".pt" if model.format == "pt" else f".{model.format}"
        target = workspace / "inputs" / f"model{suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        expected = model.weights_sha256
        if not expected or len(expected) != 64:
            raise ApplicationError(
                "model_hash_missing", "模型权重缺少可验证的 SHA256", status_code=422
            )
        if target.is_file():
            with target.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() == expected:
                    return target
            raise ApplicationError(
                "model_input_conflict", "任务输入目录中存在哈希不一致的模型文件", status_code=409
            )
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        digest = hashlib.sha256()
        try:
            with self.store.open(model.weights_key) as source, temporary.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
                    digest.update(chunk)
                output.flush()
                os.fsync(output.fileno())
            if digest.hexdigest() != expected:
                raise ApplicationError(
                    "artifact_hash_mismatch", "模型权重资产哈希不一致", status_code=409
                )
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target

    def _materialize_asset(self, asset: AssetModel, workspace: Path) -> Path:
        filename = Path(asset.relative_path or asset.id).name
        target = workspace / "inputs" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            with target.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() == asset.sha256:
                    return target
            raise ApplicationError(
                "source_input_conflict", "任务输入目录中存在哈希不一致的推理文件", status_code=409
            )
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        digest = hashlib.sha256()
        try:
            with self.store.open(asset.storage_key) as source, temporary.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
                    digest.update(chunk)
                output.flush()
                os.fsync(output.fileno())
            if digest.hexdigest() != asset.sha256:
                raise ApplicationError(
                    "artifact_hash_mismatch", "推理输入资产哈希不一致", status_code=409
                )
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target

    def _materialize_source_assets(
        self,
        source: SourceRootModel,
        assets: list[AssetModel],
        workspace: Path,
    ) -> Path:
        target_root = workspace / "inputs" / "source"
        target_root.mkdir(parents=True, exist_ok=True)
        counts: dict[str, int] = {}
        for asset in assets:
            basename = Path(asset.relative_path or asset.id).name
            counts[basename] = counts.get(basename, 0) + 1
        mapping: dict[str, str] = {}
        for asset in assets:
            basename = Path(asset.relative_path or asset.id).name
            if counts[basename] > 1:
                path = Path(basename)
                basename = f"{path.stem}-{asset.id[:8]}{path.suffix}"
            target = target_root / basename
            mapping[basename] = asset.id
            if target.is_file():
                with target.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() == asset.sha256:
                        continue
                raise ApplicationError(
                    "source_input_conflict", "任务数据源视图存在哈希冲突", status_code=409
                )
            if asset.storage_key:
                original = self.store.resolve(
                    ArtifactRef(asset.storage_key, asset.sha256, asset.size_bytes, asset.media_type)
                )
            else:
                original = Path(source.path) / Path(asset.relative_path or "")
            if not original.is_file():
                raise ApplicationError("source_offline", f"推理源文件不可用：{asset.relative_path}", status_code=422)
            with original.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != asset.sha256:
                raise ApplicationError("source_hash_mismatch", f"推理源文件哈希变化：{asset.relative_path}", status_code=409)
            temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
            try:
                shutil.copyfile(original, temporary)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        self._atomic_write(
            workspace / "source-map.json",
            json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True),
        )
        return target_root

    @staticmethod
    def _model_view(row: ModelVersionModel) -> dict[str, object]:
        return {
            "id": row.id,
            "name": row.name,
            "format": row.format,
            "purpose": row.purpose,
            "verification_status": row.verification_status,
            "evaluation_status": row.evaluation_status,
            "class_schema": row.class_schema_json,
            "weights_sha256": row.weights_sha256,
            "parent_id": row.parent_id,
            "created_at": row.created_at.isoformat(),
        }

    @staticmethod
    def _ps_quote(value: str) -> str:
        return value.replace("'", "''")

    @staticmethod
    def _atomic_write(path: Path, content: str, *, encoding: str = "utf-8") -> None:
        temporary = path.with_name(f".{path.name}.tmp")
        with temporary.open("w", encoding=encoding, newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
