from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from sqlalchemy import select
from sqlalchemy.orm import Session
import yaml

from steel_platform.infrastructure.config import PlatformSettings
from steel_platform.infrastructure.database import make_engine
from steel_platform.infrastructure.artifacts import ArtifactRef, LocalArtifactStore
from steel_platform.infrastructure.models import AnnotationRevisionModel, AssetModel, CandidatePredictionModel, DatasetVersionModel, DomainEventModel, InferenceRunModel, JobModel, ModelVersionModel, OutboxEventModel, ReviewItemModel, ReviewRoundModel, SourceRootModel
from steel_platform.infrastructure.yolo import parse_yolo_text, repair_yolo_rounding_text


def _digest(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open("rb") as stream:
        while block:=stream.read(1024*1024):digest.update(block)
    return digest.hexdigest()


def _referenced(settings: PlatformSettings) -> dict[str, str | None]:
    with Session(make_engine(settings.database_url)) as session:
        references: dict[str,str|None]={}
        for row in session.scalars(select(AssetModel).where(AssetModel.storage_key.is_not(None))):references[row.storage_key]=row.sha256
        for row in session.scalars(select(AnnotationRevisionModel)):references[row.storage_key]=row.sha256
        for row in session.scalars(select(DatasetVersionModel)):references[row.manifest_key]=row.sha256
        for row in session.scalars(select(ModelVersionModel)):
            references[row.weights_key]=None
            if row.manifest_key:references[row.manifest_key]=None
        for row in session.scalars(select(InferenceRunModel)):
            if row.manifest_key:references[row.manifest_key]=None
        for row in session.scalars(select(JobModel)):
            if row.command_key:references[row.command_key]=None
        return references


def verify_artifacts(settings: PlatformSettings) -> dict[str, object]:
    invalid=[];references=_referenced(settings);root=settings.artifact_root.resolve()
    for key,expected_hash in references.items():
        path=(root/Path(key)).resolve()
        if root not in path.parents or not path.is_file() or (expected_hash is not None and _digest(path)!=expected_hash):invalid.append(key)
    return {"checked":len(references),"invalid":len(invalid),"invalid_keys":invalid}


def find_orphan_artifacts(settings: PlatformSettings) -> list[str]:
    referenced=set(_referenced(settings));content_root=settings.artifact_root/"sha256"
    orphans = [] if not content_root.is_dir() else [path.relative_to(settings.artifact_root).as_posix() for path in content_root.rglob("*") if path.is_file() and path.relative_to(settings.artifact_root).as_posix() not in referenced]
    datasets_root=settings.artifact_root/"materialized"/"datasets"
    if datasets_root.is_dir():
        with Session(make_engine(settings.database_url)) as session:known=set(session.scalars(select(DatasetVersionModel.id)).all())
        orphans.extend(path.relative_to(settings.artifact_root).as_posix() for path in datasets_root.iterdir() if path.is_dir() and not path.name.startswith(".") and path.name not in known)
    return sorted(orphans)


def create_backup(settings: PlatformSettings) -> Path:
    stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ");target=settings.artifact_root/"backups"/stamp;target.mkdir(parents=True,exist_ok=False)
    source=sqlite3.connect(settings.database_path);destination=sqlite3.connect(target/"platform.db")
    try:source.backup(destination)
    finally:destination.close();source.close()
    config={key:(value.as_posix() if isinstance(value,Path) else list(value) if isinstance(value,tuple) else value) for key,value in settings.model_dump().items()}
    (target/"settings.snapshot.yaml").write_text(yaml.safe_dump(config,allow_unicode=True,sort_keys=True),encoding="utf-8")
    report=verify_artifacts(settings);manifest={"schema_version":"steel-backup-v1","created_at":datetime.now(timezone.utc).isoformat(),"database_sha256":_digest(target/"platform.db"),"artifact_verification":report}
    (target/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2,sort_keys=True),encoding="utf-8");return target


def verify_external_sources(settings: PlatformSettings) -> dict[str, int | list[str]]:
    invalid: list[str] = []
    checked_images = 0
    checked_candidate_labels = 0
    with Session(make_engine(settings.database_url)) as session:
        root = session.scalar(select(SourceRootModel).where(SourceRootModel.kind == "images"))
        if root is None:
            return {"images": 0, "candidate_labels": 0, "invalid": 1, "invalid_paths": ["source_root:images"]}
        for asset in session.scalars(select(AssetModel).where(AssetModel.source_root_id == root.id, AssetModel.kind == "image")):
            path = Path(root.path) / (asset.relative_path or "")
            checked_images += 1
            if not path.is_file() or _digest(path) != asset.sha256:
                invalid.append(str(path))
        predictions = session.scalars(
            select(CandidatePredictionModel)
            .join(
                InferenceRunModel,
                CandidatePredictionModel.inference_run_id == InferenceRunModel.id,
            )
            .where(InferenceRunModel.name == "seed-v1-candidates")
        ).all()
        for prediction in predictions:
            if prediction.annotation_revision_id is None:
                continue
            revision = session.get(AnnotationRevisionModel, prediction.annotation_revision_id)
            path = settings.candidate_labels / f"{Path(prediction.filename).stem}.txt"
            checked_candidate_labels += 1
            normalized_hash = (
                hashlib.sha256(path.read_text(encoding="utf-8-sig").encode("utf-8")).hexdigest()
                if path.is_file()
                else None
            )
            if revision is None or normalized_hash != revision.sha256:
                invalid.append(str(path))
    return {"images": checked_images, "candidate_labels": checked_candidate_labels, "invalid": len(invalid), "invalid_paths": invalid}


def repair_review_rounding(
    settings: PlatformSettings, *, round_number: int, apply: bool = False
) -> dict[str, object]:
    """Version tiny legacy YOLO rounding fixes without overwriting old labels."""
    store = LocalArtifactStore(settings.artifact_root)
    report: dict[str, object] = {
        "scanned": 0,
        "invalid": 0,
        "repairable": 0,
        "repaired": 0,
        "unresolved": [],
    }
    unresolved: list[dict[str, str]] = report["unresolved"]  # type: ignore[assignment]
    with Session(make_engine(settings.database_url)) as session:
        items = session.scalars(
            select(ReviewItemModel)
            .join(ReviewRoundModel, ReviewItemModel.round_id == ReviewRoundModel.id)
            .where(
                ReviewRoundModel.number == round_number,
                ReviewItemModel.current_revision_id.is_not(None),
            )
            .order_by(ReviewItemModel.rank, ReviewItemModel.filename)
        ).all()
        for item in items:
            report["scanned"] = int(report["scanned"]) + 1
            revision = session.get(AnnotationRevisionModel, item.current_revision_id)
            if revision is None:
                unresolved.append({"filename": item.filename, "reason": "当前标签版本不存在"})
                continue
            path = store.resolve(ArtifactRef(revision.storage_key, revision.sha256, 0, "text/yolo"))
            try:
                text = path.read_text(encoding="utf-8")
                parse_yolo_text(text, source=path)
                continue
            except (OSError, UnicodeError, ValueError) as strict_error:
                report["invalid"] = int(report["invalid"]) + 1
            try:
                repaired_text, boxes = repair_yolo_rounding_text(text, source=path)
                if any(box.class_id != item.expected_class_id for box in boxes):
                    raise ValueError("修复后的类别与文件前缀不一致")
            except (UnboundLocalError, ValueError) as repair_error:
                unresolved.append(
                    {
                        "filename": item.filename,
                        "reason": f"{strict_error}；{repair_error}",
                    }
                )
                continue
            report["repairable"] = int(report["repairable"]) + 1
            if not apply:
                continue
            ref = store.put_bytes(repaired_text.encode("utf-8"), media_type="text/yolo")
            repaired_revision = AnnotationRevisionModel(
                project_id=revision.project_id,
                image_asset_id=revision.image_asset_id,
                parent_id=revision.id,
                origin="system_repair",
                decision=revision.decision,
                storage_key=ref.storage_key,
                sha256=ref.sha256,
                box_count=len(boxes),
            )
            session.add(repaired_revision)
            session.flush()
            item.current_revision_id = repaired_revision.id
            item.revision += 1
            event = DomainEventModel(
                project_id=revision.project_id,
                event_type="annotation.rounding_repaired",
                payload_json={
                    "item_id": item.id,
                    "old_revision_id": revision.id,
                    "new_revision_id": repaired_revision.id,
                },
            )
            session.add(event)
            session.flush()
            session.add(OutboxEventModel(domain_event_id=event.id))
            report["repaired"] = int(report["repaired"]) + 1
        if apply:
            session.commit()
        else:
            session.rollback()
    return report
