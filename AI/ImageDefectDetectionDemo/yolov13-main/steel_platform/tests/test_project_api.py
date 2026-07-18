from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
import yaml

from steel_platform.application.projects import CreateProjectCommand, ProjectCatalogService
from steel_platform.infrastructure.config import load_settings
from steel_platform.infrastructure.database import make_engine, upgrade_database
from steel_platform.infrastructure.artifacts import LocalArtifactStore
from steel_platform.infrastructure.models import AssetModel, ReviewItemModel, ReviewRoundModel, SourceRootModel
from steel_platform.infrastructure.uow import SqlAlchemyUnitOfWork
from steel_platform.interfaces.api import create_app


@dataclass(frozen=True, slots=True)
class SeededIds:
    p1: str
    p2: str
    round1: str
    p1_asset: str


def _assert_error_shape(response) -> None:
    body = response.json()
    assert {"code", "message", "details", "request_id"} <= body.keys()
    assert isinstance(body["code"], str) and body["code"]
    assert isinstance(body["message"], str) and body["message"]
    assert body["request_id"]


def _settings(tmp_path: Path):
    config = {
        "project_name": "api-test",
        "database_url": "sqlite:///platform.db",
        "artifact_root": "artifacts",
        "source_images": "images",
        "candidate_labels": "labels",
        "review_csv": "pseudo_review.csv",
        "seed_manifest": "seed_manifest.csv",
        "seed_dataset": "seed_dataset",
        "classes": ["Cr", "In", "Pa", "PS", "RS", "Sc"],
    }
    path = tmp_path / "platform.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    settings = load_settings(path)
    settings.artifact_root.mkdir(exist_ok=True)
    upgrade_database(settings.database_url)
    return settings


def _seed(settings, tmp_path: Path) -> SeededIds:
    engine = make_engine(settings.database_url)
    # The API contract needs two independently-addressable projects; direct SQL
    # fixtures keep the router tests focused on authorization and route shape.
    factory = __import__("sqlalchemy").orm.sessionmaker(bind=engine)
    catalog = ProjectCatalogService(lambda: SqlAlchemyUnitOfWork(factory))
    p1 = catalog.create_project(CreateProjectCommand("one", "project-one", "steel", ("Cr",)), "create-one")
    p2 = catalog.create_project(CreateProjectCommand("two", "project-two", "steel", ("Cr",)), "create-two")
    images = tmp_path / "images"
    images.mkdir(exist_ok=True)
    image = images / "one.bmp"
    image.write_bytes(b"BM")
    with Session(engine) as session:
        source = SourceRootModel(id="source-one", project_id=p1.id, name="images", kind="image", path=str(images))
        session.add(source)
        session.flush()
        asset = AssetModel(
            id="asset-one",
            project_id=p1.id,
            source_root_id=source.id,
            kind="image",
            relative_path=image.name,
            sha256="a" * 64,
            size_bytes=2,
            media_type="image/bmp",
        )
        review_round = ReviewRoundModel(
            id="round-one", project_id=p1.id, number=1, name="round one", status="active", per_class=225,
        )
        session.add_all([asset, review_round])
        session.flush()
        session.add_all(
            AssetModel(
                id=f"asset-{index}", project_id=p1.id, source_root_id=source.id,
                kind="image", relative_path=f"item-{index}.bmp", sha256=f"{index:064x}",
                size_bytes=2, media_type="image/bmp",
            )
            for index in range(1, 225)
        )
        session.flush()
        session.add_all(
            ReviewItemModel(
                id=f"item-{index}", round_id=review_round.id,
                image_asset_id="asset-one" if index == 0 else f"asset-{index}",
                filename=image.name, expected_class_id=0, source_status="ok", box_count=0,
                selection_reason="seed", split_role="train", state="pending", rank=index,
            )
            for index in range(225)
        )
        session.commit()
    return SeededIds(p1.id, p2.id, "round-one", "asset-one")


def test_legacy_queue_requires_scope(client: TestClient) -> None:
    response = client.get("/api/v1/review/queues")
    assert response.status_code == 410
    _assert_error_shape(response)
    assert response.json()["code"] == "scope_required"


def test_scoped_queue_and_asset_reject_other_project(client: TestClient, seeded_ids: SeededIds) -> None:
    queue = client.get(
        f"/api/v1/projects/{seeded_ids.p1}/review-rounds/{seeded_ids.round1}/items"
    )
    assert queue.status_code == 200 and queue.json()["total"] == 225
    illegal = client.get(f"/api/v1/projects/{seeded_ids.p2}/assets/{seeded_ids.p1_asset}/content")
    assert illegal.status_code == 404
    _assert_error_shape(illegal)


def test_storage_backed_job_output_can_be_streamed(api_context, tmp_path: Path) -> None:
    client, seeded_ids = api_context
    settings = load_settings(tmp_path / "platform.yaml")
    stored = LocalArtifactStore(settings.artifact_root).put_bytes(b"video-result", media_type="video/webm")
    with Session(make_engine(settings.database_url)) as session:
        session.add(
            AssetModel(
                id="job-video-output",
                project_id=seeded_ids.p1,
                source_root_id=None,
                kind="job_output",
                relative_path="workbench/jobs/demo/output/result.webm",
                storage_key=stored.storage_key,
                sha256=stored.sha256,
                size_bytes=stored.size_bytes,
                media_type=stored.media_type,
            )
        )
        session.commit()

    response = client.get(
        f"/api/v1/projects/{seeded_ids.p1}/assets/job-video-output/content"
    )

    assert response.status_code == 200
    assert response.content == b"video-result"
    assert response.headers["content-type"].startswith("video/webm")


def test_storage_backed_job_output_download_uses_registered_basename(api_context, tmp_path: Path) -> None:
    client, seeded_ids = api_context
    settings = load_settings(tmp_path / "platform.yaml")
    stored = LocalArtifactStore(settings.artifact_root).put_bytes(
        b"training-results", media_type="text/csv"
    )
    with Session(make_engine(settings.database_url)) as session:
        session.add(
            AssetModel(
                id="job-results-download",
                project_id=seeded_ids.p1,
                source_root_id=None,
                kind="job_output",
                relative_path="workbench/jobs/demo/output/训练结果.csv",
                storage_key=stored.storage_key,
                sha256=stored.sha256,
                size_bytes=stored.size_bytes,
                media_type=stored.media_type,
            )
        )
        session.commit()

    inline = client.get(
        f"/api/v1/projects/{seeded_ids.p1}/assets/job-results-download/content"
    )
    download = client.get(
        f"/api/v1/projects/{seeded_ids.p1}/assets/job-results-download/content?download=1"
    )

    assert inline.status_code == 200
    assert inline.headers["content-disposition"].startswith("inline;")
    assert download.status_code == 200
    assert download.content == b"training-results"
    disposition = download.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "filename*=UTF-8''%E8%AE%AD%E7%BB%83%E7%BB%93%E6%9E%9C.csv" in disposition
    assert "workbench" not in disposition
    assert download.headers["content-length"] == str(len(b"training-results"))


def test_unknown_path_and_wrong_method_use_error_envelope(client: TestClient) -> None:
    unknown = client.get("/api/v1/does-not-exist")
    assert unknown.status_code == 404
    _assert_error_shape(unknown)

    wrong_method = client.post("/health/live")
    assert wrong_method.status_code == 405
    _assert_error_shape(wrong_method)


import pytest


@pytest.fixture
def api_context(tmp_path: Path) -> tuple[TestClient, SeededIds]:
    settings = _settings(tmp_path)
    return TestClient(create_app(settings), raise_server_exceptions=False), _seed(settings, tmp_path)


@pytest.fixture
def client(api_context: tuple[TestClient, SeededIds]) -> TestClient:
    return api_context[0]


@pytest.fixture
def seeded_ids(api_context: tuple[TestClient, SeededIds]) -> SeededIds:
    return api_context[1]
