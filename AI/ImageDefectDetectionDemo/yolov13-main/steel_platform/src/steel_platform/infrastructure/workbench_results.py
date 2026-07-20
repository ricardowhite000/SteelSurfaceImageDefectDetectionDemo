from __future__ import annotations

import json
import mimetypes
import csv
from pathlib import Path
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from steel_platform import __version__
from steel_platform.application.errors import ApplicationError, NotFoundError
from steel_platform.infrastructure.artifacts import LocalArtifactStore
from steel_platform.infrastructure.config import PlatformSettings
from steel_platform.infrastructure.database import make_engine
from steel_platform.infrastructure.models import (
    AssetModel,
    AnnotationRevisionModel,
    CandidatePredictionModel,
    DomainEventModel,
    ExperimentRunModel,
    InferenceRunModel,
    JobLineageRefModel,
    JobModel,
    MetricSnapshotModel,
    ModelVersionModel,
    OutboxEventModel,
    SourceRootModel,
    utc_now,
)
from steel_platform.infrastructure.yolo import (
    parse_yolo_text,
    repair_yolo_rounding_text,
    serialize_yolo,
)


def _media_type(path: Path) -> str:
    if path.suffix.lower() in {".pt", ".onnx"}:
        return "application/octet-stream"
    if path.suffix.lower() == ".csv":
        return "text/csv"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def ingest_job_outputs(settings: PlatformSettings, job_id: str) -> dict[str, object]:
    store = LocalArtifactStore(settings.artifact_root)
    engine = make_engine(settings.database_url)
    with Session(engine) as session:
        job = session.get(JobModel, job_id)
        if job is None:
            raise NotFoundError("任务不存在")
        if job.result_manifest_key:
            return json.loads(store.open(job.result_manifest_key).read().decode("utf-8"))
        runtime = job.spec_json.get("runtime") or {}
        output_dir = Path(str(runtime.get("output_dir", ""))).resolve()
        workspace = (settings.artifact_root / Path(job.workspace_key or "")).resolve()
        if settings.artifact_root.resolve() not in workspace.parents or workspace not in output_dir.parents:
            raise ApplicationError("illegal_output_path", "任务输出目录越界", status_code=500)
        if not output_dir.is_dir():
            raise ApplicationError("missing_outputs", "任务输出目录不存在", status_code=422)
        files = sorted(
            path
            for path in output_dir.rglob("*")
            if path.is_file() and not path.name.startswith(".") and not path.name.endswith(".tmp")
        )
        manifest_files: list[dict[str, object]] = []
        for path in files:
            relative = path.relative_to(output_dir).as_posix()
            registered_path = f"workbench/jobs/{job.id}/output/{relative}"
            asset = session.scalar(
                select(AssetModel).where(
                    AssetModel.project_id == job.project_id,
                    AssetModel.kind == "job_output",
                    AssetModel.relative_path == registered_path,
                )
            )
            if asset is None:
                with path.open("rb") as stream:
                    ref = store.put_stream(stream, media_type=_media_type(path))
                asset = AssetModel(
                    project_id=job.project_id,
                    kind="job_output",
                    relative_path=registered_path,
                    storage_key=ref.storage_key,
                    sha256=ref.sha256,
                    size_bytes=ref.size_bytes,
                    media_type=ref.media_type,
                )
                session.add(asset)
                session.flush()
                session.add(
                    JobLineageRefModel(
                        job_id=job.id,
                        direction="output",
                        role="artifact",
                        ref_type="asset",
                        ref_id=asset.id,
                        sha256_snapshot=asset.sha256,
                    )
                )
            manifest_files.append(
                {
                    "asset_id": asset.id,
                    "relative_path": relative,
                    "sha256": asset.sha256,
                    "size_bytes": asset.size_bytes,
                    "media_type": asset.media_type,
                }
            )
        manifest = {
            "schema_version": "steel-job-result-v1",
            "job_id": job.id,
            "project_id": job.project_id,
            "kind": job.kind,
            "preset": job.preset,
            "code_version": __version__,
            "created_at": utc_now().isoformat(),
            "files": manifest_files,
        }
        manifest_ref = store.put_bytes(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"),
            media_type="application/json",
        )
        job.result_manifest_key = manifest_ref.storage_key
        if job.kind == "verify_model":
            model = session.get(ModelVersionModel, job.spec_json.get("target_model_id"))
            metadata_path = output_dir / "metadata.json"
            if model is None or not metadata_path.is_file():
                raise ApplicationError("missing_outputs", "模型校验结果不完整", status_code=422)
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            names = [str(name) for name in metadata.get("class_names", [])]
            if model.class_schema_json is None:
                model.class_schema_json = names
            loadable = metadata.get("loadable") is True
            schema_valid = model.purpose == "base_weight" or names == list(settings.classes)
            model.verification_status = "ready" if loadable and schema_valid else "rejected"
            event = DomainEventModel(
                project_id=job.project_id,
                event_type="model.version.verified",
                payload_json={
                    "model_id": model.id,
                    "job_id": job.id,
                    "status": model.verification_status,
                    "class_names": names,
                },
            )
            session.add(event)
            session.flush()
            session.add(OutboxEventModel(domain_event_id=event.id))
        if job.kind == "evaluate":
            metrics_path = output_dir / "metrics_summary.json"
            model_input = session.scalar(
                select(JobLineageRefModel).where(
                    JobLineageRefModel.job_id == job.id,
                    JobLineageRefModel.direction == "input",
                    JobLineageRefModel.role == "model",
                )
            )
            model = session.get(ModelVersionModel, model_input.ref_id) if model_input else None
            if model is None or not metrics_path.is_file():
                raise ApplicationError("missing_outputs", "评估指标或模型血缘缺失", status_code=422)
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            session.add(
                MetricSnapshotModel(
                    project_id=job.project_id,
                    subject_type="model",
                    subject_id=model.id,
                    metrics_json=metrics,
                )
            )
            model.evaluation_status = "evaluated"
            event = DomainEventModel(
                project_id=job.project_id,
                event_type="metric.snapshot.recorded",
                payload_json={"model_id": model.id, "job_id": job.id},
            )
            session.add(event)
            session.flush()
            session.add(OutboxEventModel(domain_event_id=event.id))
        if job.kind == "infer":
            model_input = session.scalar(
                select(JobLineageRefModel).where(
                    JobLineageRefModel.job_id == job.id,
                    JobLineageRefModel.direction == "input",
                    JobLineageRefModel.role == "model",
                )
            )
            inference_run = session.scalar(
                select(InferenceRunModel).where(
                    InferenceRunModel.project_id == job.project_id,
                    InferenceRunModel.name == f"workbench-{job.id}",
                )
            )
            if inference_run is None:
                inference_run = InferenceRunModel(
                    project_id=job.project_id,
                    model_version_id=model_input.ref_id if model_input else None,
                    name=f"workbench-{job.id}",
                    status="succeeded",
                    manifest_key=manifest_ref.storage_key,
                )
                session.add(inference_run)
                session.flush()
                event = DomainEventModel(
                    project_id=job.project_id,
                    event_type="inference.run.completed",
                    payload_json={"inference_run_id": inference_run.id, "job_id": job.id},
                )
                session.add(event)
                session.flush()
                session.add(OutboxEventModel(domain_event_id=event.id))
            _register_inference_predictions(
                session,
                store,
                settings,
                job,
                inference_run,
                output_dir,
            )
        if job.kind == "train" and job.preset == "formal":
            experiment = session.scalar(
                select(ExperimentRunModel).where(ExperimentRunModel.job_id == job.id)
            )
            if experiment is None:
                inputs = session.scalars(
                    select(JobLineageRefModel).where(
                        JobLineageRefModel.job_id == job.id,
                        JobLineageRefModel.direction == "input",
                    )
                ).all()
                by_role = {item.role: item for item in inputs}
                dataset_ref = by_role.get("dataset")
                parent_ref = by_role.get("model")
                best_file = next(
                    (item for item in manifest_files if item["relative_path"] == "weights/best.pt"),
                    None,
                )
                if dataset_ref is None or parent_ref is None or best_file is None:
                    raise ApplicationError(
                        "missing_outputs", "正式训练缺少数据血缘或best.pt", status_code=422
                    )
                best_asset = session.get(AssetModel, best_file["asset_id"])
                if best_asset is None or not best_asset.storage_key:
                    raise ApplicationError("artifact_missing", "best.pt登记失败", status_code=500)
                experiment = ExperimentRunModel(
                    project_id=job.project_id,
                    job_id=job.id,
                    dataset_version_id=dataset_ref.ref_id,
                    status="succeeded",
                    run_path=str(output_dir),
                )
                session.add(experiment)
                session.flush()
                model = ModelVersionModel(
                    project_id=job.project_id,
                    experiment_run_id=experiment.id,
                    parent_id=parent_ref.ref_id,
                    name=job.name,
                    format="pt",
                    purpose="detector",
                    verification_status="ready",
                    evaluation_status="not_evaluated",
                    class_schema_json=list(settings.classes),
                    weights_sha256=best_asset.sha256,
                    weights_key=best_asset.storage_key,
                    manifest_key=manifest_ref.storage_key,
                    source_note="由模型工作台正式训练任务生成",
                )
                session.add(model)
                session.flush()
                event = DomainEventModel(
                    project_id=job.project_id,
                    event_type="model.version.registered",
                    payload_json={
                        "model_id": model.id,
                        "job_id": job.id,
                        "dataset_id": dataset_ref.ref_id,
                    },
                )
                session.add(event)
                session.flush()
                session.add(OutboxEventModel(domain_event_id=event.id))
        session.commit()
        return manifest


def _register_inference_predictions(
    session: Session,
    store: LocalArtifactStore,
    settings: PlatformSettings,
    job: JobModel,
    inference_run: InferenceRunModel,
    output_dir: Path,
) -> None:
    if session.scalar(
        select(CandidatePredictionModel.id).where(
            CandidatePredictionModel.inference_run_id == inference_run.id
        )
    ):
        return
    source_ref = session.scalar(
        select(JobLineageRefModel).where(
            JobLineageRefModel.job_id == job.id,
            JobLineageRefModel.direction == "input",
            JobLineageRefModel.role == "source",
        )
    )
    if source_ref is None:
        return
    if source_ref.ref_type == "asset":
        asset = session.get(AssetModel, source_ref.ref_id)
        assets = [asset] if asset is not None and asset.kind == "image" else []
    elif source_ref.ref_type == "source":
        assets = list(
            session.scalars(
                select(AssetModel)
                .where(
                    AssetModel.project_id == job.project_id,
                    AssetModel.source_root_id == source_ref.ref_id,
                    AssetModel.kind == "image",
                )
                .order_by(AssetModel.relative_path, AssetModel.id)
            )
        )
    else:
        assets = []
    if not assets:
        return
    rows_by_name: dict[str, list[dict[str, str]]] = defaultdict(list)
    csv_path = output_dir / "detections.csv"
    if csv_path.is_file():
        with csv_path.open(newline="", encoding="utf-8-sig") as stream:
            for row in csv.DictReader(stream):
                rows_by_name[Path(row.get("source_file", "")).name].append(row)
    source_map_path = settings.artifact_root / Path(job.workspace_key or "") / "source-map.json"
    source_name_by_asset: dict[str, str] = {}
    if source_map_path.is_file():
        stored_map = json.loads(source_map_path.read_text(encoding="utf-8"))
        source_name_by_asset = {str(asset_id): str(name) for name, asset_id in stored_map.items()}
    for asset in assets:
        source_name = source_name_by_asset.get(asset.id, Path(asset.relative_path or "").name)
        rows = rows_by_name.get(source_name, [])
        predicted_ids = sorted(
            {int(row["class_id"]) for row in rows if row.get("class_id", "").isdigit()}
        )
        confidences = [float(row["confidence"]) for row in rows if row.get("confidence")]
        prefix = Path(asset.relative_path or "").stem.split("_", 1)[0]
        expected_class_id = (
            list(settings.classes).index(prefix) if prefix in settings.classes else 0
        )
        statuses: list[str] = []
        if not rows:
            statuses.append("no_box")
        if predicted_ids and any(value != expected_class_id for value in predicted_ids):
            statuses.append("class_mismatch")
        if confidences and max(confidences) < 0.40:
            statuses.append("low_confidence")
        revision_id: str | None = None
        label_path = output_dir / "labels" / f"{Path(source_name).stem}.txt"
        if label_path.is_file():
            normalized_lines = []
            for raw in label_path.read_text(encoding="utf-8", errors="replace").splitlines():
                parts = raw.split()
                if len(parts) >= 5:
                    normalized_lines.append(" ".join(parts[:5]))
            if normalized_lines:
                source_text = "\n".join(normalized_lines) + "\n"
                try:
                    boxes = parse_yolo_text(source_text, source=label_path)
                except ValueError:
                    _, boxes = repair_yolo_rounding_text(source_text, source=label_path)
                canonical_text = serialize_yolo(boxes)
                label_ref = store.put_bytes(
                    canonical_text.encode("utf-8"),
                    media_type="text/yolo",
                )
                revision = AnnotationRevisionModel(
                    project_id=job.project_id,
                    image_asset_id=asset.id,
                    origin="machine",
                    decision=None,
                    storage_key=label_ref.storage_key,
                    sha256=label_ref.sha256,
                    box_count=len(boxes),
                )
                session.add(revision)
                session.flush()
                revision_id = revision.id
        session.add(
            CandidatePredictionModel(
                project_id=job.project_id,
                inference_run_id=inference_run.id,
                image_asset_id=asset.id,
                annotation_revision_id=revision_id,
                filename=asset.relative_path or asset.id,
                expected_class_id=expected_class_id,
                predicted_class_ids=",".join(str(value) for value in predicted_ids),
                box_count=len(rows),
                min_confidence=min(confidences) if confidences else None,
                max_confidence=max(confidences) if confidences else None,
                source_status=";".join(statuses) or "ok",
                diversity_hash=0,
                comparison_json={"workbench_job_id": job.id},
            )
        )
