from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session
import yaml

from steel_platform import __version__

from steel_platform.application.errors import ApplicationError, NotFoundError
from steel_platform.infrastructure.artifacts import ArtifactRef, LocalArtifactStore
from steel_platform.infrastructure.config import PlatformSettings
from steel_platform.infrastructure.database import make_engine
from steel_platform.infrastructure.models import (
    AnnotationRevisionModel,
    AssetModel,
    DatasetMemberModel,
    DatasetVersionModel,
    DomainEventModel,
    ExperimentRunModel,
    JobModel,
    MetricSnapshotModel,
    ModelVersionModel,
    InferenceRunModel,
    CandidatePredictionModel,
    OutboxEventModel,
    ProjectModel,
    ReviewItemModel,
    ReviewRoundModel,
    SourceRootModel,
    new_id,
    utc_now,
)
from steel_platform.infrastructure.yolo import parse_yolo_text


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _project(session: Session) -> ProjectModel:
    project = session.scalar(select(ProjectModel).limit(1))
    if project is None:
        raise NotFoundError("项目尚未初始化")
    return project


def _revision_bytes(store: LocalArtifactStore, revision: AnnotationRevisionModel) -> bytes:
    ref = ArtifactRef(revision.storage_key, revision.sha256, 0, "text/yolo")
    return store.resolve(ref).read_bytes()


def _seed_entries(settings: PlatformSettings) -> list[tuple[Path, Path, str, int]]:
    entries: list[tuple[Path, Path, str, int]] = []
    for split in ("train", "val"):
        image_dir = settings.seed_dataset / "images" / split
        label_dir = settings.seed_dataset / "labels" / split
        if not image_dir.is_dir() or not label_dir.is_dir():
            raise ApplicationError("seed_dataset_invalid", f"种子数据集缺少 {split} 目录", status_code=422)
        for image in sorted(path for path in image_dir.iterdir() if path.is_file()):
            label = label_dir / f"{image.stem}.txt"
            if not label.is_file():
                raise ApplicationError("seed_dataset_invalid", f"种子图片缺少标签：{image.name}", status_code=422)
            boxes = parse_yolo_text(label.read_text(encoding="utf-8-sig"), source=label)
            if not boxes or len({box.class_id for box in boxes}) != 1:
                raise ApplicationError("seed_dataset_invalid", f"种子标签为空或包含多类别：{label.name}", status_code=422)
            prefix = image.stem.split("_")[0]
            if prefix not in settings.classes or settings.classes.index(prefix) != boxes[0].class_id:
                raise ApplicationError("seed_dataset_invalid", f"种子文件名前缀与标签类别不一致：{image.name}", status_code=422)
            entries.append((image, label, split, boxes[0].class_id))
    return entries


def publish_dataset(settings: PlatformSettings, *, round_number: int) -> str:
    engine = make_engine(settings.database_url)
    store = LocalArtifactStore(settings.artifact_root)
    name = f"steel-dataset-v2-round-{round_number}"
    with Session(engine) as session:
        existing = session.scalar(select(DatasetVersionModel).where(DatasetVersionModel.name == name))
        if existing is not None:
            return existing.id
        project = _project(session)
        review_round = session.scalar(
            select(ReviewRoundModel).where(
                ReviewRoundModel.project_id == project.id,
                ReviewRoundModel.number == round_number,
                ReviewRoundModel.kind == "training",
            )
        )
        if review_round is None:
            raise NotFoundError(f"复核轮次 {round_number} 不存在")
        items = session.scalars(
            select(ReviewItemModel).where(ReviewItemModel.round_id == review_round.id).order_by(ReviewItemModel.rank)
        ).all()
        valid = [item for item in items if item.state in {"accepted", "corrected"}]
        quotas = {(class_id, split): 0 for class_id in range(len(settings.classes)) for split in ("train", "val")}
        for item in valid:
            quotas[(item.expected_class_id, item.split_role)] += 1
            if item.current_revision_id is None:
                raise ApplicationError("dataset_not_ready", f"复核条目缺少人工确认标签：{item.filename}", status_code=422)
        expected = {
            (class_id, "val"): settings.validation_per_class
            for class_id in range(len(settings.classes))
        } | {
            (class_id, "train"): settings.per_class - settings.validation_per_class
            for class_id in range(len(settings.classes))
        }
        if any(quotas[key] < count for key, count in expected.items()):
            missing = {f"{settings.classes[key[0]]}/{key[1]}": count - quotas[key] for key, count in expected.items() if quotas[key] < count}
            raise ApplicationError("dataset_not_ready", "有效复核数量尚未达到发布配额", status_code=422, details=missing)

        # Only quota-bearing items enter the immutable version; replacements can
        # leave additional reviewed records without silently changing the split.
        selected: list[ReviewItemModel] = []
        used = {(class_id, split): 0 for class_id, split in expected}
        for item in valid:
            key = (item.expected_class_id, item.split_role)
            if used[key] < expected[key]:
                selected.append(item)
                used[key] += 1
        seed_entries = _seed_entries(settings)
        dataset_id = new_id()
        final_dir = settings.artifact_root / "materialized" / "datasets" / dataset_id
        temporary_dir = final_dir.with_name(f".{dataset_id}.tmp")
        if temporary_dir.exists():
            raise ApplicationError("temporary_asset_exists", f"发现上次中断留下的临时目录：{temporary_dir}", status_code=409)
        for split in ("train", "val"):
            (temporary_dir / "images" / split).mkdir(parents=True, exist_ok=True)
            (temporary_dir / "labels" / split).mkdir(parents=True, exist_ok=True)

        members: list[dict[str, Any]] = []
        source_roots = {root.kind: root for root in session.scalars(select(SourceRootModel).where(SourceRootModel.project_id == project.id))}
        image_root = source_roots.get("images")
        if image_root is None:
            raise ApplicationError("source_missing", "原图来源未登记", status_code=422)
        for item in selected:
            asset = session.get(AssetModel, item.image_asset_id)
            revision = session.get(AnnotationRevisionModel, item.current_revision_id)
            if asset is None or revision is None or asset.relative_path is None:
                raise ApplicationError("dataset_not_ready", f"资产或标签版本缺失：{item.filename}", status_code=422)
            source = Path(image_root.path) / asset.relative_path
            if not source.is_file() or _sha256(source) != asset.sha256:
                raise ApplicationError("source_hash_mismatch", f"原图已变化或丢失：{item.filename}", status_code=409)
            target_image = temporary_dir / "images" / item.split_role / item.filename
            target_label = temporary_dir / "labels" / item.split_role / f"{Path(item.filename).stem}.txt"
            if target_image.exists():
                raise ApplicationError("duplicate_filename", f"数据集中存在重复文件名：{item.filename}", status_code=422)
            shutil.copy2(source, target_image)
            target_label.write_bytes(_revision_bytes(store, revision))
            members.append({"asset": asset, "revision": revision, "split": item.split_role, "source_round": round_number})

        seed_root = source_roots.get("seed_dataset")
        if seed_root is None:
            seed_root = SourceRootModel(project_id=project.id, kind="seed_dataset", path=str(settings.seed_dataset), read_only=True)
            session.add(seed_root)
            session.flush()
        for image, label, split, class_id in seed_entries:
            relative = image.relative_to(settings.seed_dataset).as_posix()
            asset = session.scalar(
                select(AssetModel).where(AssetModel.project_id == project.id, AssetModel.kind == "image", AssetModel.relative_path == relative)
            )
            if asset is None:
                asset = AssetModel(
                    project_id=project.id,
                    source_root_id=seed_root.id,
                    kind="image",
                    relative_path=relative,
                    sha256=_sha256(image),
                    size_bytes=image.stat().st_size,
                    media_type=f"image/{image.suffix.lower().lstrip('.')}",
                )
                session.add(asset)
                session.flush()
            label_bytes = label.read_bytes()
            label_ref = store.put_bytes(label_bytes, media_type="text/yolo")
            revision = session.scalar(
                select(AnnotationRevisionModel).where(
                    AnnotationRevisionModel.image_asset_id == asset.id,
                    AnnotationRevisionModel.sha256 == label_ref.sha256,
                )
            )
            if revision is None:
                revision = AnnotationRevisionModel(
                    project_id=project.id,
                    image_asset_id=asset.id,
                    origin="human_seed",
                    decision="accepted",
                    storage_key=label_ref.storage_key,
                    sha256=label_ref.sha256,
                    box_count=len(parse_yolo_text(label_bytes.decode("utf-8-sig"), source=label)),
                )
                session.add(revision)
                session.flush()
            target_image = temporary_dir / "images" / split / image.name
            target_label = temporary_dir / "labels" / split / label.name
            if target_image.exists():
                raise ApplicationError("duplicate_filename", f"数据集中存在重复文件名：{image.name}", status_code=422)
            shutil.copy2(image, target_image)
            shutil.copy2(label, target_label)
            members.append({"asset": asset, "revision": revision, "split": split, "source_round": 0})

        counts = {split: sum(member["split"] == split for member in members) for split in ("train", "val")}
        hash_splits: dict[str, set[str]] = {}
        for member in members:
            hash_splits.setdefault(member["asset"].sha256, set()).add(member["split"])
        leaked_hashes = [sha256 for sha256, splits in hash_splits.items() if len(splits) > 1]
        if leaked_hashes:
            raise ApplicationError(
                "split_leakage",
                "检测到相同图像内容同时进入训练集和验证集",
                status_code=422,
                details={"sha256": leaked_hashes[:20]},
            )
        manifest = {
            "schema_version": "steel-defects-v1",
            "dataset_id": dataset_id,
            "name": name,
            "parent_dataset_id": None,
            "class_schema": {str(index): value for index, value in enumerate(settings.classes)},
            "counts": counts,
            "created_at": utc_now().isoformat(),
            "code_version": __version__,
            "members": [
                {
                    "image_asset_id": member["asset"].id,
                    "image_sha256": member["asset"].sha256,
                    "annotation_revision_id": member["revision"].id,
                    "annotation_sha256": member["revision"].sha256,
                    "split": member["split"],
                    "source_round": member["source_round"],
                }
                for member in members
            ],
        }
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        (temporary_dir / "manifest.json").write_bytes(manifest_bytes)
        (temporary_dir / "data.yaml").write_text(
            yaml.safe_dump(
                {"train": "images/train", "val": "images/val", "names": {i: name for i, name in enumerate(settings.classes)}},
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary_dir, final_dir)
        manifest_ref = store.put_bytes(manifest_bytes, media_type="application/json")
        dataset = DatasetVersionModel(
            id=dataset_id,
            project_id=project.id,
            name=name,
            schema_version="steel-defects-v1",
            manifest_key=manifest_ref.storage_key,
            sha256=manifest_ref.sha256,
        )
        session.add(dataset)
        session.flush()
        for member in members:
            session.add(
                DatasetMemberModel(
                    dataset_version_id=dataset.id,
                    image_asset_id=member["asset"].id,
                    annotation_revision_id=member["revision"].id,
                    split=member["split"],
                )
            )
        event = DomainEventModel(project_id=project.id, event_type="dataset.version.published", payload_json={"dataset_id": dataset.id, "counts": counts})
        session.add(event); session.flush(); session.add(OutboxEventModel(domain_event_id=event.id))
        session.commit()
        return dataset.id


def _root(settings: PlatformSettings) -> Path:
    return settings.yolo_project_root or Path(__file__).resolve().parents[4]


def _command(args: list[str]) -> str:
    return subprocess.list2cmdline(args) + "\n"


def _materialize_file(source: Path, target: Path) -> None:
    """Create an atomic, suffix-preserving working copy of an immutable asset."""
    source_hash = _sha256(source)
    if target.is_file() and _sha256(target) == source_hash:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{new_id()}.tmp")
    try:
        with source.open("rb") as input_stream, temporary.open("wb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        if _sha256(temporary) != source_hash:
            raise ApplicationError(
                "artifact_hash_mismatch", "模型权重工作副本哈希不一致", status_code=500
            )
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _create_job(session: Session, store: LocalArtifactStore, project_id: str, kind: str, spec: dict[str, Any], args: list[str]) -> JobModel:
    job = JobModel(project_id=project_id, kind=kind, status="planned", spec_json=spec)
    session.add(job); session.flush()
    ref = store.put_bytes(_command(args).encode("utf-8"), media_type="text/x-powershell")
    job.command_key = ref.storage_key
    return job


def prepare_training_jobs(settings: PlatformSettings, *, dataset_id: str) -> list[str]:
    engine = make_engine(settings.database_url); store = LocalArtifactStore(settings.artifact_root)
    with Session(engine) as session:
        dataset = session.get(DatasetVersionModel, dataset_id)
        if dataset is None: raise NotFoundError("数据集版本不存在")
        existing = session.scalars(select(JobModel).where(JobModel.spec_json["dataset_id"].as_string() == dataset_id)).all()
        if existing: return [job.id for job in existing]
        project = _project(session); root = _root(settings); data = settings.artifact_root / "materialized" / "datasets" / dataset_id / "data.yaml"
        weights = settings.parent_weights or (root / "yolov13n.pt")
        runs_root = settings.artifact_root / "materialized" / "runs"
        common = [settings.yolo_python, str(root / "steel_tutorial" / "05_train.py"), "--data", str(data), "--weights", str(weights), "--batch", "4", "--imgsz", "640", "--device", settings.device, "--workers", "0", "--seed", str(settings.seed), "--project", str(runs_root)]
        smoke = _create_job(session, store, project.id, "train_smoke", {"dataset_id": dataset_id, "epochs": 1, "environment": "yolov13", "cwd": str(root)}, common + ["--name", f"v2_smoke_{dataset_id[:8]}", "--smoke"])
        formal = _create_job(session, store, project.id, "train_formal", {"dataset_id": dataset_id, "epochs": 100, "parent_weights": str(weights), "parent_weights_sha256": _sha256(weights) if weights.is_file() else None, "environment": "yolov13", "cwd": str(root)}, common + ["--name", f"v2_formal_{dataset_id[:8]}", "--epochs", "100"])
        formal_dir = runs_root / f"v2_formal_{dataset_id[:8]}"
        evaluate = _create_job(session, store, project.id, "evaluate", {"dataset_id": dataset_id, "training_job_id": formal.id, "environment": "yolov13", "cwd": str(root)}, [settings.yolo_python, "-m", "steel_tutorial.06_evaluate", "--data", str(data), "--weights", str(formal_dir / "weights" / "best.pt"), "--project", str(formal_dir / "evaluation"), "--name", "v2_fixed_val"])
        session.commit(); return [smoke.id, formal.id, evaluate.id]


def ingest_training_run(settings: PlatformSettings, *, job_id: str, run_dir: Path) -> str:
    engine = make_engine(settings.database_url); store = LocalArtifactStore(settings.artifact_root); run_dir = run_dir.resolve()
    metrics_path = run_dir / "metrics_summary.json"
    if not metrics_path.is_file():
        metrics_path = run_dir / "evaluation" / "v2_fixed_val" / "metrics_summary.json"
    required = {"best": run_dir / "weights" / "best.pt", "last": run_dir / "weights" / "last.pt", "results": run_dir / "results.csv", "metrics": metrics_path}
    missing = [str(path) for path in required.values() if not path.is_file()]
    if missing: raise ApplicationError("missing_outputs", "训练输出不完整", status_code=422, details=missing)
    with Session(engine) as session:
        job = session.get(JobModel, job_id)
        if job is None or job.kind != "train_formal": raise NotFoundError("正式训练任务不存在")
        prior_run = session.scalar(select(ExperimentRunModel).where(ExperimentRunModel.job_id == job_id))
        if prior_run is not None:
            model = session.scalar(select(ModelVersionModel).where(ModelVersionModel.experiment_run_id == prior_run.id)); return model.id
        project = _project(session); refs = {name: store.put_bytes(path.read_bytes(), media_type="application/octet-stream" if path.suffix == ".pt" else "text/plain") for name,path in required.items()}
        run = ExperimentRunModel(project_id=project.id, job_id=job.id, dataset_version_id=job.spec_json["dataset_id"], status="succeeded", run_path=str(run_dir)); session.add(run); session.flush()
        model_id = new_id(); metrics = json.loads(required["metrics"].read_text(encoding="utf-8"))
        manifest = {"schema_version":"steel-model-v1","model_id":model_id,"dataset_id":job.spec_json["dataset_id"],"parent_model_id":job.spec_json.get("parent_model_id"),"parent_weights_sha256":job.spec_json.get("parent_weights_sha256"),"weights":{"best":refs["best"].sha256,"last":refs["last"].sha256},"metrics":metrics,"code_version":__version__,"created_at":utc_now().isoformat()}
        manifest_ref = store.put_bytes(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"), media_type="application/json")
        model = ModelVersionModel(id=model_id, project_id=project.id, experiment_run_id=run.id, parent_id=job.spec_json.get("parent_model_id"), name=f"steel-v2-{model_id[:8]}", weights_key=refs["best"].storage_key, manifest_key=manifest_ref.storage_key); session.add(model)
        media_types={"best":"application/octet-stream","last":"application/octet-stream","results":"text/csv","metrics":"application/json"}
        for name,ref in refs.items():session.add(AssetModel(project_id=project.id,kind=f"training_{name}",storage_key=ref.storage_key,sha256=ref.sha256,size_bytes=ref.size_bytes,media_type=media_types[name]))
        session.add(AssetModel(project_id=project.id,kind="model_manifest",storage_key=manifest_ref.storage_key,sha256=manifest_ref.sha256,size_bytes=manifest_ref.size_bytes,media_type="application/json"))
        session.add(MetricSnapshotModel(project_id=project.id, subject_type="model", subject_id=model.id, metrics_json=metrics)); job.status="succeeded"
        event=DomainEventModel(project_id=project.id,event_type="model.version.registered",payload_json={"model_id":model.id,"dataset_id":job.spec_json["dataset_id"]});session.add(event);session.flush();session.add(OutboxEventModel(domain_event_id=event.id));session.commit();return model.id


def prepare_inference_job(settings: PlatformSettings, *, model_id: str) -> str:
    engine=make_engine(settings.database_url);store=LocalArtifactStore(settings.artifact_root)
    with Session(engine) as session:
        model=session.get(ModelVersionModel,model_id)
        if model is None: raise NotFoundError("模型版本不存在")
        existing=session.scalar(select(JobModel).where(JobModel.kind=="inference",JobModel.spec_json["model_id"].as_string()==model_id))
        if existing is not None and existing.status=="running":return existing.id
        project=_project(session);root=_root(settings);stored_weights=store.resolve(ArtifactRef(model.weights_key,"",0,"application/octet-stream"));output=settings.artifact_root/"materialized"/"inference"/model_id
        weights=output/"model.pt";_materialize_file(stored_weights,weights)
        reviewed_ids=set(session.scalars(select(ReviewItemModel.image_asset_id)).all())
        with settings.seed_manifest.open(newline="",encoding="utf-8-sig") as seed_stream:
            seed_names={row["filename"] for row in csv.DictReader(seed_stream)}
        source_root=session.scalar(select(SourceRootModel).where(SourceRootModel.project_id==project.id,SourceRootModel.kind=="images"))
        if source_root is None: raise ApplicationError("source_missing","原图来源未登记",status_code=422)
        assets=session.scalars(select(AssetModel).where(AssetModel.project_id==project.id,AssetModel.source_root_id==source_root.id,AssetModel.kind=="image").order_by(AssetModel.relative_path)).all()
        sources=[str((Path(source_root.path)/asset.relative_path).resolve()) for asset in assets if asset.id not in reviewed_ids and asset.relative_path and asset.relative_path not in seed_names]
        output.mkdir(parents=True,exist_ok=True);source_list=output/"sources.txt";temporary=source_list.with_suffix(".tmp");temporary.write_text("\n".join(sources)+("\n" if sources else ""),encoding="utf-8");os.replace(temporary,source_list)
        args=[settings.yolo_python,"-m","steel_platform.interfaces.inference_runner","--source-list",str(source_list),"--weights",str(weights),"--output",str(output),"--batch","1","--device",settings.device,"--classes",*settings.classes]
        spec={"model_id":model_id,"environment":"yolov13","cwd":str(root),"batch":1,"stream":True,"resumable":True,"source_count":len(sources),"output":str(output),"weights":str(weights)}
        if existing is not None:
            command_ref=store.put_bytes(_command(args).encode("utf-8"),media_type="text/x-powershell")
            existing.spec_json=spec;existing.command_key=command_ref.storage_key
            if existing.status in {"failed","cancelled"}:existing.status="planned"
            session.commit();return existing.id
        job=_create_job(session,store,project.id,"inference",spec,args);session.commit();return job.id


def ingest_inference_run(settings: PlatformSettings, *, job_id: str, prediction_dir: Path) -> str:
    engine=make_engine(settings.database_url);store=LocalArtifactStore(settings.artifact_root);prediction_dir=prediction_dir.resolve();review_csv=prediction_dir/"pseudo_review.csv"
    if not review_csv.is_file(): raise ApplicationError("missing_outputs","推理目录缺少pseudo_review.csv",status_code=422)
    with Session(engine) as session:
        job=session.get(JobModel,job_id)
        if job is None or job.kind!="inference": raise NotFoundError("推理任务不存在")
        existing=session.scalar(select(InferenceRunModel).where(InferenceRunModel.name==f"inference-{job_id}"))
        if existing is not None:return existing.id
        model=session.get(ModelVersionModel,job.spec_json["model_id"])
        if model is None:raise NotFoundError("推理任务引用的模型不存在")
        project=_project(session);source_root=session.scalar(select(SourceRootModel).where(SourceRootModel.project_id==project.id,SourceRootModel.kind=="images"))
        assets={asset.relative_path:asset for asset in session.scalars(select(AssetModel).where(AssetModel.source_root_id==source_root.id)).all()}
        v1={row.image_asset_id:row for row in session.scalars(select(CandidatePredictionModel).join(InferenceRunModel,CandidatePredictionModel.inference_run_id==InferenceRunModel.id).where(InferenceRunModel.name=="seed-v1-candidates")).all()}
        rows=list(csv.DictReader(review_csv.open(newline="",encoding="utf-8-sig")))
        files=[]
        for row in rows:
            asset=assets.get(row["filename"])
            if asset is None:raise ApplicationError("unknown_prediction_asset",f"推理结果引用未登记图片：{row['filename']}",status_code=422)
            label_path=prediction_dir/f"{Path(row['filename']).stem}.txt"
            files.append({"image_asset_id":asset.id,"image_sha256":asset.sha256,"filename":row["filename"],"label_sha256":_sha256(label_path) if label_path.is_file() else None})
        manifest={"schema_version":"steel-inference-v1","job_id":job_id,"model_id":model.id,"parent_model_id":model.parent_id,"source_count":job.spec_json.get("source_count"),"processed_count":len(rows),"batch":1,"stream":True,"code_version":__version__,"created_at":utc_now().isoformat(),"files":files}
        manifest_ref=store.put_bytes(json.dumps(manifest,ensure_ascii=False,indent=2,sort_keys=True).encode("utf-8"),media_type="application/json")
        run=InferenceRunModel(project_id=project.id,model_version_id=model.id,name=f"inference-{job_id}",status="succeeded",manifest_key=manifest_ref.storage_key);session.add(run);session.flush()
        for row in rows:
            filename=row["filename"];asset=assets.get(filename)
            if asset is None:raise ApplicationError("unknown_prediction_asset",f"推理结果引用未登记图片：{filename}",status_code=422)
            label=prediction_dir/f"{Path(filename).stem}.txt";revision=None;new_boxes=()
            if label.is_file():
                text=label.read_text(encoding="utf-8-sig");new_boxes=parse_yolo_text(text,source=label);ref=store.put_bytes(text.encode("utf-8"),media_type="text/yolo");revision=AnnotationRevisionModel(project_id=project.id,image_asset_id=asset.id,origin="machine",storage_key=ref.storage_key,sha256=ref.sha256,box_count=len(new_boxes));session.add(revision);session.flush()
            previous=v1.get(asset.id);old_boxes=()
            if previous and previous.annotation_revision_id:
                old_revision=session.get(AnnotationRevisionModel,previous.annotation_revision_id);old_boxes=parse_yolo_text(_revision_bytes(store,old_revision).decode("utf-8"),source=Path(previous.filename))
            expected=int(row["expected_class_id"]);predicted=row.get("predicted_class_ids","");box_count=int(row.get("box_count") or 0);confidence=float(row["min_confidence"]) if row.get("min_confidence") else None
            no_box=box_count==0;class_mismatch=bool(predicted) and str(expected) not in {value.strip() for value in predicted.split(";") if value.strip()};count_delta=abs(box_count-(previous.box_count if previous else 0));iou_delta=_box_difference(old_boxes,new_boxes)
            score=(100 if no_box else 0)+(80 if class_mismatch else 0)+(20*(1-(confidence if confidence is not None else 0)))+10*count_delta+20*iou_delta
            session.add(CandidatePredictionModel(project_id=project.id,inference_run_id=run.id,image_asset_id=asset.id,annotation_revision_id=revision.id if revision else None,filename=filename,expected_class_id=expected,predicted_class_ids=predicted,box_count=box_count,min_confidence=confidence,max_confidence=float(row["max_confidence"]) if row.get("max_confidence") else None,source_status=row.get("status") or ("no_box" if no_box else "ok"),diversity_hash=_image_dhash(Path(source_root.path)/filename),comparison_score=score,comparison_json={"no_box":no_box,"class_mismatch":class_mismatch,"box_count_delta":count_delta,"iou_difference":iou_delta}))
        job.status="succeeded";event=DomainEventModel(project_id=project.id,event_type="inference.run.completed",payload_json={"run_id":run.id,"model_id":model.id,"processed":len(rows)});session.add(event);session.flush();session.add(OutboxEventModel(domain_event_id=event.id));session.commit();return run.id


def create_audit_round(settings: PlatformSettings, inference_id: str, per_class: int) -> str:
    engine=make_engine(settings.database_url)
    with Session(engine) as session:
        run=session.get(InferenceRunModel,inference_id)
        if run is None:raise NotFoundError("推理运行不存在")
        existing=session.scalar(select(ReviewRoundModel).where(ReviewRoundModel.project_id==run.project_id,ReviewRoundModel.kind=="audit",ReviewRoundModel.number==2))
        if existing is not None:return existing.id
        candidates=session.scalars(select(CandidatePredictionModel).where(CandidatePredictionModel.inference_run_id==inference_id)).all();grouped={class_id:[] for class_id in range(len(settings.classes))}
        for candidate in candidates:grouped[candidate.expected_class_id].append(candidate)
        chosen=[]
        for class_id,group in grouped.items():
            if len(group)<per_class:raise ApplicationError("audit_quota_shortage",f"类别{class_id}只有{len(group)}张可抽查",status_code=422)
            ranked=sorted(group,key=lambda item:(-item.comparison_score,item.filename));priority=ranked[:max(1,int(per_class*.7))];remaining=[item for item in ranked if item not in priority]
            while len(priority)<per_class:
                anchors=[item.diversity_hash for item in priority];pick=max(remaining,key=lambda item:(min((item.diversity_hash^anchor).bit_count() for anchor in anchors),item.comparison_score,item.filename));remaining.remove(pick);priority.append(pick)
            chosen.extend(priority)
        round_model=ReviewRoundModel(project_id=run.project_id,number=2,kind="audit",per_class=per_class);session.add(round_model);session.flush()
        for rank,candidate in enumerate(chosen,1):session.add(ReviewItemModel(round_id=round_model.id,image_asset_id=candidate.image_asset_id,candidate_revision_id=candidate.annotation_revision_id,filename=candidate.filename,expected_class_id=candidate.expected_class_id,source_status=candidate.source_status,min_confidence=candidate.min_confidence,max_confidence=candidate.max_confidence,box_count=candidate.box_count,selection_reason="v1_v2_delta",split_role="audit",rank=rank))
        session.commit();return round_model.id


def _iou(left: Any, right: Any) -> float:
    la=(left.x_center-left.width/2,left.y_center-left.height/2,left.x_center+left.width/2,left.y_center+left.height/2);ra=(right.x_center-right.width/2,right.y_center-right.height/2,right.x_center+right.width/2,right.y_center+right.height/2);intersection=max(0,min(la[2],ra[2])-max(la[0],ra[0]))*max(0,min(la[3],ra[3])-max(la[1],ra[1]));union=left.width*left.height+right.width*right.height-intersection;return intersection/union if union else 0.0


def _box_difference(old_boxes: Any,new_boxes: Any)->float:
    if not old_boxes and not new_boxes:return 0.0
    if not old_boxes or not new_boxes:return 1.0
    return 1-sum(max(_iou(old,new) for new in new_boxes) for old in old_boxes)/len(old_boxes)


def _image_dhash(path:Path)->int:
    from PIL import Image
    with Image.open(path) as image:
        resized=image.convert("L").resize((9,8));pixels=list(resized.get_flattened_data() if hasattr(resized,"get_flattened_data") else resized.getdata())
    value=0
    for row in range(8):
        for column in range(8):value=(value<<1)|int(pixels[row*9+column]>pixels[row*9+column+1])
    return value&((1<<63)-1)


def run_manual_job(settings: PlatformSettings, *, job_id: str) -> str:
    engine=make_engine(settings.database_url);store=LocalArtifactStore(settings.artifact_root)
    with Session(engine) as session:
        job=session.get(JobModel,job_id)
        if job is None or job.command_key is None:raise NotFoundError("任务不存在或缺少命令")
        if job.status=="running":raise ApplicationError("job_already_running","任务已经处于运行状态",status_code=409)
        command_path=store.resolve(ArtifactRef(job.command_key,"",0,"text/x-powershell"));command=command_path.read_text(encoding="utf-8").strip();working_directory=job.spec_json.get("cwd");job.status="running";job.error_message=None;session.commit()
    try:
        completed=subprocess.run(command,cwd=working_directory,shell=True,check=False)
        status="succeeded" if completed.returncode==0 else "failed";error=None if completed.returncode==0 else f"进程退出码：{completed.returncode}"
    except KeyboardInterrupt:
        status="cancelled";error="用户中断"
    except Exception as exc:
        status="failed";error=str(exc)
    with Session(engine) as session:
        job=session.get(JobModel,job_id);job.status=status;job.error_message=error;project_id=job.project_id;event=DomainEventModel(project_id=project_id,event_type="job.status.changed",payload_json={"job_id":job_id,"status":status,"error":error});session.add(event);session.flush();session.add(OutboxEventModel(domain_event_id=event.id));session.commit()
    return status
