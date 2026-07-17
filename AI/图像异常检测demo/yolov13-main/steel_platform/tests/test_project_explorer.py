from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from steel_platform.application.errors import ApplicationError, NotFoundError, RevisionConflictError
from steel_platform.application.explorer import ExplorerService, ReviewTaskCreationService
from steel_platform.application.projects import CreateProjectCommand, ProjectCatalogService
from steel_platform.domain.workspace import DataSource, SourceMode, SourceStatus
from steel_platform.infrastructure.models import (
    AssetModel,
    Base,
    DatasetVersionModel,
    ExperimentRunModel,
    InferenceRunModel,
    JobModel,
    ModelVersionModel,
    ReviewItemModel,
    ReviewRoundModel,
)
from steel_platform.infrastructure.uow import SqlAlchemyUnitOfWork


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session)


@pytest.fixture
def uow_factory(session_factory: sessionmaker[Session]) -> Callable[[], SqlAlchemyUnitOfWork]:
    return lambda: SqlAlchemyUnitOfWork(session_factory)


def _command(slug: str, name: str | None = None) -> CreateProjectCommand:
    return CreateProjectCommand(
        name=name or slug,
        slug=slug,
        class_schema_name=f"{slug}-classes",
        class_names=("Cr", "In"),
    )


def test_project_creation_requires_idempotency_key_and_retry_returns_original(
    uow_factory: Callable[[], SqlAlchemyUnitOfWork],
) -> None:
    service = ProjectCatalogService(uow_factory)

    with pytest.raises(ApplicationError) as error:
        service.create_project(_command("project-one"), " ")
    assert error.value.code == "validation_error"

    first = service.create_project(_command("project-one"), "create-project-one")
    retried = service.create_project(_command("project-one", "ignored retry name"), "create-project-one")

    assert retried == first
    assert first.id == "project-one"
    assert service.list_projects() == [first]


def test_idempotency_key_cannot_be_reused_for_another_project(
    uow_factory: Callable[[], SqlAlchemyUnitOfWork],
) -> None:
    service = ProjectCatalogService(uow_factory)
    service.create_project(_command("project-one"), "shared-key")

    with pytest.raises(ApplicationError) as error:
        service.create_project(_command("project-two"), "shared-key")

    assert error.value.code == "idempotency_conflict"
    assert [project.id for project in service.list_projects()] == ["project-one"]


def test_explorer_projects_normalized_resources_for_only_requested_project(
    uow_factory: Callable[[], SqlAlchemyUnitOfWork],
    session_factory: sessionmaker[Session],
) -> None:
    catalog = ProjectCatalogService(uow_factory)
    first = catalog.create_project(_command("project-one"), "create-one")
    second = catalog.create_project(_command("project-two"), "create-two")
    with uow_factory() as uow:
        uow.sources.add(
            DataSource(
                id="source-one",
                project_id=first.id,
                name="first images",
                mode=SourceMode.EXTERNAL,
                root_path="G:/one",
                status=SourceStatus.AVAILABLE,
                revision=0,
            )
        )
        uow.sources.add(
            DataSource(
                id="source-two",
                project_id=second.id,
                name="second images",
                mode=SourceMode.EXTERNAL,
                root_path="G:/two",
                status=SourceStatus.MISSING,
                revision=0,
            )
        )
        uow.commit()

    with session_factory() as session:
        asset = AssetModel(
            id="asset-one",
            project_id=first.id,
            source_root_id="source-one",
            kind="image",
            relative_path="Cr_1.bmp",
            sha256="a" * 64,
            size_bytes=1,
            media_type="image/bmp",
        )
        dataset = DatasetVersionModel(
            id="dataset-one",
            project_id=first.id,
            name="dataset v1",
            manifest_key="sha256/manifest",
            sha256="b" * 64,
        )
        job = JobModel(id="job-one", project_id=first.id, kind="training", spec_json={})
        experiment = ExperimentRunModel(
            id="experiment-one",
            project_id=first.id,
            job_id=job.id,
            dataset_version_id=dataset.id,
            status="succeeded",
            run_path="runs/one",
        )
        session.add_all(
            [
                asset,
                ReviewRoundModel(
                    id="round-one",
                    project_id=first.id,
                    number=1,
                    name="review one",
                    status="active",
                    per_class=1,
                ),
                dataset,
                job,
                experiment,
                ModelVersionModel(
                    id="model-one",
                    project_id=first.id,
                    experiment_run_id=experiment.id,
                    name="model v1",
                    weights_key="sha256/weights",
                ),
                InferenceRunModel(
                    id="inference-one",
                    project_id=first.id,
                    name="inference one",
                    status="succeeded",
                ),
                ReviewRoundModel(
                    id="round-two",
                    project_id=second.id,
                    number=1,
                    name="review two",
                    status="active",
                    per_class=1,
                ),
            ]
        )
        session.commit()

    tree = ExplorerService(uow_factory).tree(first.id)
    nodes = [node for group in tree["groups"] for node in group["children"]]
    ids = {node["id"] for node in nodes}

    assert {"source-one", "round-one", "dataset-one", "model-one", "inference-one"} <= ids
    assert {"source-two", "round-two"}.isdisjoint(ids)
    assert tree["project"] == {"id": first.id, "name": first.name}
    for group in tree["groups"]:
        assert set(group) == {"id", "type", "name", "count", "status", "children"}
    for node in nodes:
        assert set(node) == {"id", "type", "name", "count", "status", "children"}


def test_collection_edits_are_project_scoped_and_revision_checked(
    uow_factory: Callable[[], SqlAlchemyUnitOfWork],
    session_factory: sessionmaker[Session],
) -> None:
    catalog = ProjectCatalogService(uow_factory)
    first = catalog.create_project(_command("project-one"), "create-one")
    second = catalog.create_project(_command("project-two"), "create-two")
    explorer = ExplorerService(uow_factory)
    parent = explorer.create_collection(first.id, "parent")
    foreign_parent = explorer.create_collection(second.id, "foreign")

    with pytest.raises(NotFoundError):
        explorer.create_collection(first.id, "bad child", parent_id=foreign_parent.id)

    renamed = explorer.rename_collection(first.id, parent.id, "renamed", expected_revision=0)
    assert renamed.revision == 1
    with pytest.raises(RevisionConflictError):
        explorer.rename_collection(first.id, parent.id, "stale", expected_revision=0)

    with session_factory() as session:
        session.add_all(
            [
                AssetModel(
                    id="asset-one",
                    project_id=first.id,
                    kind="image",
                    relative_path="one.bmp",
                    sha256="1" * 64,
                    size_bytes=1,
                    media_type="image/bmp",
                ),
                AssetModel(
                    id="asset-two",
                    project_id=first.id,
                    kind="image",
                    relative_path="two.bmp",
                    sha256="2" * 64,
                    size_bytes=1,
                    media_type="image/bmp",
                ),
                AssetModel(
                    id="foreign-asset",
                    project_id=second.id,
                    kind="image",
                    relative_path="foreign.bmp",
                    sha256="3" * 64,
                    size_bytes=1,
                    media_type="image/bmp",
                ),
            ]
        )
        session.commit()

    with pytest.raises(NotFoundError):
        explorer.add_members(first.id, parent.id, ["foreign-asset"], expected_revision=renamed.revision)

    changed = explorer.add_members(
        first.id,
        parent.id,
        ["asset-one", "asset-two"],
        expected_revision=renamed.revision,
    )
    assert changed.revision == 2
    changed = explorer.remove_member(first.id, parent.id, "asset-two", expected_revision=changed.revision)
    assert changed.revision == 3
    assert explorer.list_members(first.id, parent.id) == ("asset-one",)


def test_task_members_remain_frozen_when_collection_changes(
    uow_factory: Callable[[], SqlAlchemyUnitOfWork],
    session_factory: sessionmaker[Session],
) -> None:
    project = ProjectCatalogService(uow_factory).create_project(_command("project-one"), "create-one")
    explorer = ExplorerService(uow_factory)
    collection = explorer.create_collection(project.id, "review candidates")
    with session_factory() as session:
        session.add_all(
            [
                AssetModel(
                    id=f"asset-{index}",
                    project_id=project.id,
                    kind="image",
                    relative_path=f"Cr_{index}.bmp",
                    sha256=str(index) * 64,
                    size_bytes=1,
                    media_type="image/bmp",
                )
                for index in range(1, 4)
            ]
        )
        session.commit()
    collection = explorer.add_members(
        project.id,
        collection.id,
        ["asset-1", "asset-2"],
        expected_revision=collection.revision,
    )
    review = ReviewTaskCreationService(uow_factory, explorer=explorer)

    round_id = review.create_from_collection(project.id, collection.id, sample_size=2)
    explorer.add_members(project.id, collection.id, ["asset-3"], expected_revision=collection.revision)

    items = review.list_items(project.id, round_id)
    assert items.total == 2
    assert items.asset_ids == ("asset-1", "asset-2")


def test_new_application_services_do_not_import_framework_or_storage_implementations() -> None:
    application = Path(__file__).parents[1] / "src" / "steel_platform" / "application"
    forbidden = {"fastapi", "sqlalchemy", "steel_platform.infrastructure"}
    for filename in ("projects.py", "explorer.py"):
        tree = ast.parse((application / filename).read_text(encoding="utf-8"))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        assert not any(name == blocked or name.startswith(f"{blocked}.") for name in imports for blocked in forbidden)


def test_review_snapshot_is_stored_as_review_items(
    uow_factory: Callable[[], SqlAlchemyUnitOfWork],
    session_factory: sessionmaker[Session],
) -> None:
    project = ProjectCatalogService(uow_factory).create_project(_command("project-one"), "create-one")
    explorer = ExplorerService(uow_factory)
    collection = explorer.create_collection(project.id, "snapshot")
    with session_factory() as session:
        session.add(
            AssetModel(
                id="asset-one",
                project_id=project.id,
                kind="image",
                relative_path="Cr_1.bmp",
                sha256="a" * 64,
                size_bytes=1,
                media_type="image/bmp",
            )
        )
        session.commit()
    collection = explorer.add_members(
        project.id, collection.id, ["asset-one"], expected_revision=collection.revision
    )

    ReviewTaskCreationService(uow_factory, explorer=explorer).create_from_collection(
        project.id, collection.id, sample_size=1
    )

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ReviewRoundModel)) == 1
        assert session.scalar(select(func.count()).select_from(ReviewItemModel)) == 1
