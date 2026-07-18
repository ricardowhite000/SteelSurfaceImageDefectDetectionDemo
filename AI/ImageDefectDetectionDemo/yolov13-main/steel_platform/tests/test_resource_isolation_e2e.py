from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker
import yaml

from steel_platform.application.errors import NotFoundError
from steel_platform.application.imports import DataSourceImportService
from steel_platform.application.projects import CreateProjectCommand, ProjectCatalogService
from steel_platform.application.review import ReviewService
from steel_platform.application.review_queries import ReviewTaskQueryService
from steel_platform.infrastructure.artifacts import LocalArtifactStore
from steel_platform.infrastructure.config import load_settings
from steel_platform.infrastructure.database import make_engine, upgrade_database
from steel_platform.infrastructure.directory_picker import LocalFolderReader
from steel_platform.infrastructure.models import AssetModel, ReviewItemModel, ReviewRoundModel, SourceRootModel
from steel_platform.infrastructure.uow import SqlAlchemyUnitOfWork


STEEL_CLASSES = ("Cr", "In", "Pa", "PS", "RS", "Sc")


def _source_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _settings(tmp_path: Path):
    config_path = tmp_path / "platform.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "project_name": "p1",
                "database_url": "sqlite:///platform.db",
                "artifact_root": "artifacts",
                "source_images": "unused-images",
                "candidate_labels": "unused-labels",
                "review_csv": "unused-review.csv",
                "seed_manifest": "unused-seed.csv",
                "seed_dataset": "unused-seed-dataset",
                "classes": list(STEEL_CLASSES),
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    settings = load_settings(config_path)
    settings.artifact_root.mkdir()
    upgrade_database(settings.database_url)
    return settings


def _seed_project_rounds(engine, project_id: str, source_root: Path) -> tuple[str, str, str]:
    source_root.mkdir()
    source_file = source_root / "p1-0.bmp"
    source_file.write_bytes(b"BM-p1")
    factory = sessionmaker(bind=engine, class_=Session)
    with factory() as session:
        root = SourceRootModel(id="p1-source", project_id=project_id, name="p1 source", kind="image", path=str(source_root))
        first = ReviewRoundModel(id="p1-r1", project_id=project_id, number=1, kind="training", name="training", per_class=45, target_count=225)
        second = ReviewRoundModel(id="p1-r2", project_id=project_id, number=2, kind="audit", name="audit", per_class=10, target_count=60)
        session.add_all((root, first, second))
        session.flush()
        session.add_all(
            AssetModel(
                id=f"p1-asset-{index}", project_id=project_id, source_root_id=root.id,
                kind="image", relative_path=source_file.name if index == 0 else f"p1-{index}.bmp",
                sha256=sha256(source_file.read_bytes()).hexdigest() if index == 0 else f"{index:064x}",
                size_bytes=source_file.stat().st_size, media_type="image/bmp",
            )
            for index in range(285)
        )
        session.add_all(
            ReviewItemModel(
                id=f"p1-r1-item-{index}", round_id=first.id, image_asset_id=f"p1-asset-{index}",
                filename=f"p1-{index}.bmp", expected_class_id=index % len(STEEL_CLASSES),
                source_status="ok", box_count=0, selection_reason="seed", split_role="train", rank=index + 1,
            )
            for index in range(225)
        )
        session.add_all(
            ReviewItemModel(
                id=f"p1-r2-item-{index}", round_id=second.id, image_asset_id=f"p1-asset-{225 + index}",
                filename=f"p1-audit-{index}.bmp", expected_class_id=index % len(STEEL_CLASSES),
                source_status="ok", box_count=0, selection_reason="audit", split_role="audit", rank=index + 1,
            )
            for index in range(60)
        )
        session.commit()
    return "p1-r1", "p1-r2", "p1-asset-0"


def test_two_projects_and_two_rounds_are_isolated(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    engine = make_engine(settings.database_url)
    factory = sessionmaker(bind=engine, class_=Session)
    uow_factory = lambda: SqlAlchemyUnitOfWork(factory)
    catalog = ProjectCatalogService(uow_factory)
    p1 = catalog.create_project(CreateProjectCommand("p1", "p1", "steel", STEEL_CLASSES), "create-p1")
    p2 = catalog.create_project(CreateProjectCommand("p2", "p2", "steel", STEEL_CLASSES), "create-p2")
    round_one, round_two, p1_asset = _seed_project_rounds(engine, p1.id, tmp_path / "p1-source")

    managed_source = tmp_path / "managed-source"
    managed_source.mkdir()
    for index in range(6):
        (managed_source / f"p2-{index}.bmp").write_bytes(f"BM-p2-{index}".encode())
    before = _source_hashes(managed_source)
    imports = DataSourceImportService(uow_factory, LocalArtifactStore(settings.artifact_root), LocalFolderReader())
    imported = imports.import_managed(p2.id, "six managed images", managed_source, idempotency_key="p2-managed")

    queries = ReviewTaskQueryService(uow_factory, class_names=STEEL_CLASSES)
    assert queries.list_items(p1.id, round_one).total == 225
    assert queries.list_items(p1.id, round_two).total == 60
    assert ReviewService(settings).overview()["assets"]["images"] == 285
    assert ReviewService(settings.model_copy(update={"project_name": p2.name})).overview()["assets"]["images"] == 6
    with imports.open_asset(p2.id, imported.asset_ids[0]) as stream:
        assert stream.read().startswith(b"BM-p2")
    with pytest.raises(NotFoundError):
        imports.open_asset(p1.id, imported.asset_ids[0])
    with pytest.raises(NotFoundError):
        imports.open_asset(p2.id, p1_asset)
    assert _source_hashes(managed_source) == before
