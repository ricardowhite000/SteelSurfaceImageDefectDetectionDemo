from __future__ import annotations

import ast
import hashlib
from pathlib import Path
import sqlite3

from hypothesis import given, strategies as st
from sqlalchemy import select
from sqlalchemy.orm import Session

from steel_platform.application.maintenance import create_backup, find_orphan_artifacts, verify_artifacts, verify_external_sources
from steel_platform.domain.annotations import AnnotationBox
from steel_platform.infrastructure.database import database_version
from steel_platform.infrastructure.database import make_engine
from steel_platform.infrastructure.models import AssetModel, ProjectModel, SourceRootModel
from steel_platform.infrastructure.yolo import parse_yolo_text, serialize_yolo
from test_review_api import _prepared_workspace


def test_domain_layer_has_no_framework_or_adapter_imports() -> None:
    domain = Path(__file__).parents[1] / "src" / "steel_platform" / "domain"
    forbidden = {"fastapi", "sqlalchemy", "ultralytics", "alembic", "PIL"}
    for source in domain.glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import): imports.extend(alias.name.split(".")[0] for alias in node.names)
            if isinstance(node, ast.ImportFrom) and node.module: imports.append(node.module.split(".")[0])
        assert forbidden.isdisjoint(imports), f"{source.name} imports {forbidden.intersection(imports)}"


@given(
    class_id=st.integers(min_value=0, max_value=5),
    left=st.floats(min_value=0, max_value=.7, allow_nan=False, allow_infinity=False),
    top=st.floats(min_value=0, max_value=.7, allow_nan=False, allow_infinity=False),
    width=st.floats(min_value=.01, max_value=.29, allow_nan=False, allow_infinity=False),
    height=st.floats(min_value=.01, max_value=.29, allow_nan=False, allow_infinity=False),
)
def test_legal_yolo_boxes_survive_serialization(class_id, left, top, width, height) -> None:
    box = AnnotationBox(class_id, left + width / 2, top + height / 2, width, height)
    decoded = parse_yolo_text(serialize_yolo([box]), source=Path("property.txt"))[0]
    assert decoded.class_id == class_id
    assert abs(decoded.x_center - box.x_center) <= 1.6e-6
    assert abs(decoded.y_center - box.y_center) <= 1.6e-6


def test_backup_is_consistent_and_gc_only_reports_orphans(tmp_path: Path) -> None:
    settings, _, _ = _prepared_workspace(tmp_path)
    current, head = database_version(settings.database_url)
    assert current == head
    assert verify_artifacts(settings)["invalid"] == 0
    source_report = verify_external_sources(settings)
    assert source_report["images"] == 12 and source_report["invalid"] == 0, source_report
    orphan = settings.artifact_root / "sha256" / "ff" / ("f" * 64)
    orphan.parent.mkdir(parents=True, exist_ok=True); orphan.write_bytes(b"orphan")
    assert orphan.relative_to(settings.artifact_root).as_posix() in find_orphan_artifacts(settings)

    backup = create_backup(settings)
    assert (backup / "manifest.json").is_file()
    restored = sqlite3.connect(backup / "platform.db")
    try:
        assert restored.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 12
    finally:
        restored.close()
    assert orphan.is_file(), "GC检查不得删除文件"


def test_external_source_verification_covers_every_registered_source_root(tmp_path: Path) -> None:
    settings, _, _ = _prepared_workspace(tmp_path)
    seed_root = tmp_path / "registered-seed-dataset"
    seed_image = seed_root / "images" / "train" / "Cr_1.bmp"
    seed_image.parent.mkdir(parents=True)
    seed_image.write_bytes(b"BM-seed")
    with Session(make_engine(settings.database_url)) as session:
        project_id = session.scalar(select(ProjectModel.id))
        source = SourceRootModel(
            project_id=project_id,
            kind="seed_dataset",
            path=str(seed_root),
            read_only=True,
        )
        session.add(source)
        session.flush()
        session.add(
            AssetModel(
                project_id=project_id,
                source_root_id=source.id,
                kind="image",
                relative_path="images/train/Cr_1.bmp",
                sha256=hashlib.sha256(seed_image.read_bytes()).hexdigest(),
                size_bytes=seed_image.stat().st_size,
                media_type="image/bmp",
            )
        )
        session.commit()

    valid = verify_external_sources(settings)
    assert valid["sources"] == 2
    assert valid["source_assets"] == 13
    assert {row["kind"]: row["invalid"] for row in valid["by_source"]} == {
        "images": 0,
        "seed_dataset": 0,
    }

    seed_image.unlink()
    invalid = verify_external_sources(settings)
    assert invalid["invalid"] == 1
    assert {row["kind"]: row["invalid"] for row in invalid["by_source"]}[
        "seed_dataset"
    ] == 1
