from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import os
import shutil
import sqlite3
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session
import yaml

from steel_platform.infrastructure.config import PlatformSettings
from steel_platform.infrastructure.database import make_engine
from steel_platform.infrastructure.artifacts import ArtifactRef, LocalArtifactStore
from steel_platform.infrastructure.models import AnnotationRevisionCheckModel, AnnotationRevisionModel, AssetModel, CandidatePredictionModel, DatasetVersionModel, DomainEventModel, InferenceRunModel, JobModel, ModelVersionModel, OutboxEventModel, ReviewItemModel, ReviewRoundModel, SourceRootModel, utc_now
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


def _fsync(path: Path) -> None:
    with path.open("rb+") as stream:
        stream.flush()
        os.fsync(stream.fileno())


def _validate_sqlite_backup(path: Path) -> None:
    database = sqlite3.connect(path)
    try:
        status = database.execute("PRAGMA integrity_check").fetchone()
    finally:
        database.close()
    if status != ("ok",):
        raise RuntimeError(f"backup integrity check failed: {status}")


def create_backup(settings: PlatformSettings, *, verify_artifact_references: bool = True) -> Path:
    """Atomically publish a verified SQLite snapshot without ever replacing the source."""
    source_path = settings.database_path
    if not source_path.is_file():
        raise FileNotFoundError(f"database does not exist: {source_path}")
    backups = settings.artifact_root / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = backups / f"{stamp}-{uuid4().hex}"
    temporary = backups / f".{target.name}.tmp"
    temporary.mkdir()
    try:
        database_path = temporary / "platform.db"
        source = sqlite3.connect(source_path)
        destination = sqlite3.connect(database_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        _fsync(database_path)
        _validate_sqlite_backup(database_path)
        config = {key: (value.as_posix() if isinstance(value, Path) else list(value) if isinstance(value, tuple) else value) for key, value in settings.model_dump().items()}
        config_path = temporary / "settings.snapshot.yaml"
        config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=True), encoding="utf-8")
        _fsync(config_path)
        report = verify_artifacts(settings) if verify_artifact_references else {"checked": 0, "invalid": 0, "status": "deferred_until_schema_upgrade"}
        manifest = {"schema_version": "steel-backup-v1", "created_at": datetime.now(timezone.utc).isoformat(), "database_sha256": _digest(database_path), "artifact_verification": report}
        manifest_path = temporary / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        _fsync(manifest_path)
        temporary.rename(target)
        return target
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def snapshot_database_counts(database_path: Path) -> dict[str, object]:
    """Capture legacy records whose identity and review decisions must survive a migration."""
    if not database_path.is_file():
        raise FileNotFoundError(f"database does not exist: {database_path}")
    with sqlite3.connect(database_path) as database:
        tables = {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        tracked = ("projects", "source_roots", "assets", "annotation_revisions", "review_rounds", "review_items")
        counts = {table: database.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tracked if table in tables}
        identifiers = {table: tuple(row[0] for row in database.execute(f"SELECT id FROM {table} ORDER BY id")) for table in tracked if table in tables}
        review_states: tuple[tuple[Any, ...], ...] = ()
        if "review_items" in tables:
            review_states = tuple(database.execute("SELECT round_id, state, count(*) FROM review_items GROUP BY round_id, state ORDER BY round_id, state"))
    return {"counts": counts, "identifiers": identifiers, "review_states": review_states}


def verify_upgrade_counts(database_path: Path, before: dict[str, object] | None) -> None:
    """Fail loudly if an Alembic upgrade changed legacy data identities or decisions."""
    if before is not None and snapshot_database_counts(database_path) != before:
        raise RuntimeError("database migration did not preserve legacy primary IDs, row counts, or review states")


def verify_external_sources(settings: PlatformSettings) -> dict[str, object]:
    invalid: list[str] = []
    checked_images = 0
    checked_source_assets = 0
    checked_candidate_labels = 0
    by_source: list[dict[str, object]] = []
    with Session(make_engine(settings.database_url)) as session:
        roots = session.scalars(
            select(SourceRootModel)
            .where(SourceRootModel.mode == "external")
            .order_by(SourceRootModel.kind, SourceRootModel.id)
        ).all()
        if not roots:
            return {
                "sources": 0,
                "source_assets": 0,
                "images": 0,
                "candidate_labels": 0,
                "invalid": 1,
                "invalid_paths": ["source_roots"],
                "by_source": [],
            }
        for root in roots:
            source_invalid = 0
            source_checked = 0
            assets = session.scalars(
                select(AssetModel)
                .where(AssetModel.source_root_id == root.id)
                .order_by(AssetModel.relative_path, AssetModel.id)
            ).all()
            for asset in assets:
                path = Path(root.path) / (asset.relative_path or "")
                source_checked += 1
                checked_source_assets += 1
                if root.kind == "images" and asset.kind == "image":
                    checked_images += 1
                if not path.is_file() or _digest(path) != asset.sha256:
                    invalid.append(str(path))
                    source_invalid += 1
            by_source.append(
                {
                    "id": root.id,
                    "kind": root.kind,
                    "name": root.name,
                    "path": root.path,
                    "checked": source_checked,
                    "invalid": source_invalid,
                }
            )
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
    return {
        "sources": len(by_source),
        "source_assets": checked_source_assets,
        "images": checked_images,
        "candidate_labels": checked_candidate_labels,
        "invalid": len(invalid),
        "invalid_paths": invalid,
        "by_source": by_source,
    }


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


def audit_annotation_revisions(settings: PlatformSettings) -> dict[str, object]:
    """Audit every immutable annotation revision without changing its content."""
    store = LocalArtifactStore(settings.artifact_root)
    report: dict[str, object] = {
        "scanned": 0,
        "valid": 0,
        "repairable": 0,
        "invalid": 0,
        "problems": [],
    }
    problems: list[dict[str, str]] = report["problems"]  # type: ignore[assignment]
    with Session(make_engine(settings.database_url)) as session:
        revisions = session.scalars(
            select(AnnotationRevisionModel).order_by(
                AnnotationRevisionModel.project_id,
                AnnotationRevisionModel.created_at,
                AnnotationRevisionModel.id,
            )
        ).all()
        for revision in revisions:
            report["scanned"] = int(report["scanned"]) + 1
            try:
                with store.open(revision.storage_key) as stream:
                    text = stream.read().decode("utf-8-sig")
                parse_yolo_text(text, source=Path(f"<{revision.id}>"))
                status, code, message = "valid", None, None
                report["valid"] = int(report["valid"]) + 1
            except (OSError, UnicodeError, ValueError) as strict_error:
                try:
                    repair_yolo_rounding_text(text, source=Path(f"<{revision.id}>"))
                    status, code, message = "repairable", "rounding_overflow", str(strict_error)
                    report["repairable"] = int(report["repairable"]) + 1
                except (UnboundLocalError, OSError, UnicodeError, ValueError) as repair_error:
                    status, code, message = "invalid", "invalid_annotation", f"{strict_error}; {repair_error}"
                    report["invalid"] = int(report["invalid"]) + 1
                problems.append(
                    {"revision_id": revision.id, "status": status, "message": message or ""}
                )
            check = session.get(AnnotationRevisionCheckModel, revision.id)
            if check is None:
                check = AnnotationRevisionCheckModel(revision_id=revision.id, status=status)
                session.add(check)
            check.status = status
            check.error_code = code
            check.message = message
            check.checked_at = utc_now()
        session.commit()
    return report


def repair_annotation_rounding(
    settings: PlatformSettings,
    *,
    apply: bool = False,
    create_backup_first: bool = True,
) -> dict[str, object]:
    """Create canonical child revisions for all safely repairable labels."""
    if apply and create_backup_first:
        create_backup(settings)
    audit_annotation_revisions(settings)
    store = LocalArtifactStore(settings.artifact_root)
    report: dict[str, object] = {
        "repairable": 0,
        "repaired": 0,
        "already_repaired": 0,
        "unresolved": [],
    }
    with Session(make_engine(settings.database_url)) as session:
        checks = session.scalars(
            select(AnnotationRevisionCheckModel).where(
                AnnotationRevisionCheckModel.status.in_(("repairable", "repaired"))
            )
        ).all()
        for check in checks:
            revision = session.get(AnnotationRevisionModel, check.revision_id)
            if revision is None:
                continue
            existing = session.scalar(
                select(AnnotationRevisionModel).where(
                    AnnotationRevisionModel.parent_id == revision.id,
                    AnnotationRevisionModel.origin == "system",
                    AnnotationRevisionModel.decision == "normalized_rounding",
                )
            )
            if existing is not None:
                check.status = "repaired"
                check.repaired_by_revision_id = existing.id
                report["already_repaired"] = int(report["already_repaired"]) + 1
                continue
            report["repairable"] = int(report["repairable"]) + 1
            if not apply:
                continue
            try:
                with store.open(revision.storage_key) as stream:
                    text = stream.read().decode("utf-8-sig")
                repaired_text, boxes = repair_yolo_rounding_text(
                    text, source=Path(f"<{revision.id}>")
                )
            except (OSError, UnicodeError, ValueError) as exc:
                report["unresolved"].append(
                    {"revision_id": revision.id, "message": str(exc)}
                )
                continue
            ref = store.put_bytes(repaired_text.encode("utf-8"), media_type="text/yolo")
            child = AnnotationRevisionModel(
                project_id=revision.project_id,
                image_asset_id=revision.image_asset_id,
                parent_id=revision.id,
                origin="system",
                decision="normalized_rounding",
                storage_key=ref.storage_key,
                sha256=ref.sha256,
                box_count=len(boxes),
                created_by="system-maintenance",
            )
            session.add(child)
            session.flush()
            check.status = "repaired"
            check.repaired_by_revision_id = child.id
            check.checked_at = utc_now()
            session.add(
                DomainEventModel(
                    project_id=revision.project_id,
                    event_type="annotation.rounding_repaired",
                    payload_json={
                        "old_revision_id": revision.id,
                        "new_revision_id": child.id,
                    },
                )
            )
            report["repaired"] = int(report["repaired"]) + 1
        if apply:
            session.flush()
            events = session.scalars(
                select(DomainEventModel).where(
                    DomainEventModel.event_type == "annotation.rounding_repaired",
                    ~select(OutboxEventModel.id)
                    .where(OutboxEventModel.domain_event_id == DomainEventModel.id)
                    .exists(),
                )
            ).all()
            session.add_all(OutboxEventModel(domain_event_id=event.id) for event in events)
            session.commit()
        else:
            session.rollback()
    return report
