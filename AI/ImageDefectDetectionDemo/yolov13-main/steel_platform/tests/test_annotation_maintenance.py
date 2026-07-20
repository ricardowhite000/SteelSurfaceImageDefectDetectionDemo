from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from steel_platform.application.maintenance import audit_annotation_revisions, repair_annotation_rounding
from steel_platform.infrastructure.artifacts import LocalArtifactStore
from steel_platform.infrastructure.database import make_engine
from steel_platform.infrastructure.models import AnnotationRevisionCheckModel, AnnotationRevisionModel
from test_resource_browser_api import _context


def test_project_wide_annotation_repair_creates_immutable_child_and_is_idempotent(
    tmp_path: Path,
) -> None:
    settings, project_id, _ = _context(tmp_path)
    store = LocalArtifactStore(settings.artifact_root)
    ref = store.put_bytes(
        b"0 0.96497 0.506167 0.0700603 0.0381834\n", media_type="text/yolo"
    )
    with Session(make_engine(settings.database_url)) as session:
        session.add(
            AnnotationRevisionModel(
                id="rounding-parent",
                project_id=project_id,
                image_asset_id="asset-1",
                origin="machine",
                storage_key=ref.storage_key,
                sha256=ref.sha256,
                box_count=1,
            )
        )
        session.commit()

    audit = audit_annotation_revisions(settings)
    assert audit["repairable"] == 1
    first = repair_annotation_rounding(settings, apply=True, create_backup_first=False)
    second = repair_annotation_rounding(settings, apply=True, create_backup_first=False)
    assert first["repaired"] == 1
    assert second["repaired"] == 0

    with Session(make_engine(settings.database_url)) as session:
        child = session.scalar(
            select(AnnotationRevisionModel).where(
                AnnotationRevisionModel.parent_id == "rounding-parent",
                AnnotationRevisionModel.decision == "normalized_rounding",
            )
        )
        assert child is not None
        assert child.origin == "system"
        check = session.get(AnnotationRevisionCheckModel, "rounding-parent")
        assert check.status == "repaired"
        assert check.repaired_by_revision_id == child.id
        assert session.scalar(
            select(func.count()).select_from(AnnotationRevisionModel).where(
                AnnotationRevisionModel.parent_id == "rounding-parent",
                AnnotationRevisionModel.decision == "normalized_rounding",
            )
        ) == 1

