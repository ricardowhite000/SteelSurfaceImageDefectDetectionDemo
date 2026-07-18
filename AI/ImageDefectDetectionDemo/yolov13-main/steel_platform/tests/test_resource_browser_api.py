from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session, sessionmaker
import yaml

from steel_platform.application.projects import CreateProjectCommand, ProjectCatalogService
from steel_platform.infrastructure.artifacts import LocalArtifactStore
from steel_platform.infrastructure.config import load_settings
from steel_platform.infrastructure.database import make_engine, upgrade_database
from steel_platform.infrastructure.models import (
    AnnotationRevisionModel,
    AssetModel,
    CandidatePredictionModel,
    DatasetMemberModel,
    DatasetVersionModel,
    InferenceRunModel,
    ReviewItemModel,
    ReviewRoundModel,
    SourceRootModel,
)
from steel_platform.infrastructure.uow import SqlAlchemyUnitOfWork
from steel_platform.interfaces.api import create_app


def _context(tmp_path: Path):
    config = {
        "project_name": "resource-test",
        "database_url": "sqlite:///platform.db",
        "artifact_root": "artifacts",
        "source_images": "images",
        "candidate_labels": "labels",
        "review_csv": "review.csv",
        "seed_manifest": "seed.csv",
        "seed_dataset": "seed",
        "classes": ["Cr", "In", "Pa", "PS", "RS", "Sc"],
    }
    config_path = tmp_path / "platform.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    settings = load_settings(config_path)
    settings.artifact_root.mkdir(exist_ok=True)
    upgrade_database(settings.database_url)
    engine = make_engine(settings.database_url)
    factory = sessionmaker(bind=engine)
    catalog = ProjectCatalogService(lambda: SqlAlchemyUnitOfWork(factory))
    project = catalog.create_project(
        CreateProjectCommand("资源测试", "resource-test", "steel-defects", tuple(config["classes"])),
        "create-resource-test",
    )
    other = catalog.create_project(
        CreateProjectCommand("其他项目", "other-project", "steel-defects", tuple(config["classes"])),
        "create-other-project",
    )
    image_root = tmp_path / "images"
    image_root.mkdir()
    store = LocalArtifactStore(settings.artifact_root)
    with Session(engine) as session:
        source = SourceRootModel(
            id="source-images", project_id=project.id, name="钢板原图", kind="images",
            mode="external", status="available", path=str(image_root), read_only=True,
        )
        session.add(source)
        session.flush()
        assets = []
        for index, name in enumerate(("Cr_1.bmp", "Cr_2.bmp", "Sc_1.bmp"), start=1):
            path = image_root / name
            Image.new("RGB", (48, 32), (index * 40, 60, 90)).save(path)
            content = path.read_bytes()
            assets.append(
                AssetModel(
                    id=f"asset-{index}", project_id=project.id, source_root_id=source.id,
                    kind="image", relative_path=name, sha256=hashlib.sha256(content).hexdigest(),
                    size_bytes=len(content), media_type="image/bmp",
                )
            )
        session.add_all(assets)
        session.flush()
        machine_ref = store.put_bytes(b"0 0.5 0.5 0.4 0.4\n", media_type="text/yolo")
        human_ref = store.put_bytes(b"0 0.45 0.45 0.2 0.2\n", media_type="text/yolo")
        machine = AnnotationRevisionModel(
            id="revision-machine", project_id=project.id, image_asset_id="asset-1",
            origin="machine", storage_key=machine_ref.storage_key, sha256=machine_ref.sha256,
            box_count=1,
        )
        review_round = ReviewRoundModel(
            id="round-1", project_id=project.id, number=1, kind="training",
            name="首轮复核", target_count=3, status="active", per_class=1,
            class_schema_id=project.class_schema_id,
        )
        session.add(machine)
        session.flush()
        human = AnnotationRevisionModel(
            id="revision-human", project_id=project.id, image_asset_id="asset-1",
            parent_id=machine.id, origin="human", decision="corrected",
            storage_key=human_ref.storage_key, sha256=human_ref.sha256, box_count=1,
        )
        session.add_all([human, review_round])
        session.flush()
        session.add_all([
            ReviewItemModel(
                id="item-1", round_id=review_round.id, image_asset_id="asset-1",
                candidate_revision_id=machine.id, current_revision_id=human.id,
                filename="Cr_1.bmp", expected_class_id=0, source_status="low_confidence",
                selection_reason="risk_priority", split_role="train", state="corrected",
                note="已修正裂纹框", revision=1, rank=1, box_count=1,
            ),
            ReviewItemModel(
                id="item-2", round_id=review_round.id, image_asset_id="asset-2",
                filename="Cr_2.bmp", expected_class_id=0, source_status="ok",
                selection_reason="diversity", split_role="val", state="pending", rank=2,
            ),
            ReviewItemModel(
                id="item-3", round_id=review_round.id, image_asset_id="asset-3",
                filename="Sc_1.bmp", expected_class_id=5, source_status="no_box",
                selection_reason="risk_priority", split_role="train", state="excluded",
                note="未确认到有效划痕", revision=1, rank=3,
            ),
        ])
        dataset = DatasetVersionModel(
            id="dataset-1", project_id=project.id, name="steel-dataset-test",
            schema_version="steel-defects-v1", manifest_key="manifests/dataset.json",
            sha256="d" * 64,
        )
        inference = InferenceRunModel(
            id="inference-1", project_id=project.id, name="inference-long-name", status="succeeded",
        )
        session.add_all([dataset, inference])
        session.flush()
        session.add(DatasetMemberModel(
            dataset_version_id=dataset.id, image_asset_id="asset-1",
            annotation_revision_id=human.id, split="train",
        ))
        session.add(CandidatePredictionModel(
            inference_run_id=inference.id, project_id=project.id, image_asset_id="asset-1",
            annotation_revision_id=machine.id, filename="Cr_1.bmp", expected_class_id=0,
            predicted_class_ids="0", box_count=1, min_confidence=.25, max_confidence=.8,
            source_status="low_confidence", diversity_hash=1,
        ))
        session.commit()
    return settings, project.id, other.id


def test_resource_items_are_project_scoped_searchable_and_paginated(tmp_path: Path) -> None:
    settings, project_id, other_id = _context(tmp_path)
    client = TestClient(create_app(settings), raise_server_exceptions=False)
    base = f"/api/v1/projects/{project_id}/resources/source/source-images/items"

    first = client.get(f"{base}?page=1&page_size=2&sort=name&order=asc")
    assert first.status_code == 200
    assert first.json()["pagination"] == {"page": 1, "page_size": 2, "total": 3, "pages": 2}
    assert [row["name"] for row in first.json()["items"]] == ["Cr_1.bmp", "Cr_2.bmp"]
    assert all(row["is_image"] for row in first.json()["items"])

    searched = client.get(f"{base}?q=Sc_1")
    assert searched.status_code == 200
    assert [row["asset_id"] for row in searched.json()["items"]] == ["asset-3"]
    assert client.get(
        f"/api/v1/projects/{other_id}/resources/source/source-images/items"
    ).status_code == 404


def test_thumbnail_is_cached_and_rejects_invalid_size(tmp_path: Path) -> None:
    settings, project_id, other_id = _context(tmp_path)
    client = TestClient(create_app(settings), raise_server_exceptions=False)
    url = f"/api/v1/projects/{project_id}/assets/asset-1/thumbnail?size=320"

    first = client.get(url)
    second = client.get(url)
    assert first.status_code == second.status_code == 200
    assert first.content == second.content
    assert first.headers["content-type"].startswith("image/jpeg")
    assert first.headers["etag"] == second.headers["etag"]
    assert "immutable" in first.headers["cache-control"]
    smaller = client.get(
        f"/api/v1/projects/{project_id}/assets/asset-1/thumbnail?size=160"
    )
    assert smaller.status_code == 200
    assert smaller.headers["etag"] != first.headers["etag"]
    assert client.get(
        f"/api/v1/projects/{project_id}/assets/asset-1/thumbnail?size=80"
    ).status_code == 422
    assert client.get(
        f"/api/v1/projects/{other_id}/assets/asset-1/thumbnail?size=320"
    ).status_code == 404


def test_thumbnail_rejects_non_image_and_corrupt_image(tmp_path: Path) -> None:
    settings, project_id, _ = _context(tmp_path)
    image_root = tmp_path / "images"
    corrupt = image_root / "broken.bmp"
    text_file = image_root / "notes.txt"
    corrupt.write_bytes(b"not-an-image")
    text_file.write_text("metadata", encoding="utf-8")
    engine = make_engine(settings.database_url)
    with Session(engine) as session:
        session.add_all([
            AssetModel(
                id="asset-corrupt", project_id=project_id, source_root_id="source-images",
                kind="image", relative_path=corrupt.name,
                sha256=hashlib.sha256(corrupt.read_bytes()).hexdigest(),
                size_bytes=corrupt.stat().st_size, media_type="image/bmp",
            ),
            AssetModel(
                id="asset-text", project_id=project_id, source_root_id="source-images",
                kind="artifact", relative_path=text_file.name,
                sha256=hashlib.sha256(text_file.read_bytes()).hexdigest(),
                size_bytes=text_file.stat().st_size, media_type="text/plain",
            ),
        ])
        session.commit()
    client = TestClient(create_app(settings), raise_server_exceptions=False)

    corrupt_response = client.get(
        f"/api/v1/projects/{project_id}/assets/asset-corrupt/thumbnail?size=320"
    )
    text_response = client.get(
        f"/api/v1/projects/{project_id}/assets/asset-text/thumbnail?size=320"
    )
    assert corrupt_response.status_code == 422
    assert corrupt_response.json()["code"] == "invalid_image"
    assert text_response.status_code == 422
    assert text_response.json()["code"] == "not_image"


def test_asset_detail_selects_overlay_from_resource_context(tmp_path: Path) -> None:
    settings, project_id, _ = _context(tmp_path)
    client = TestClient(create_app(settings), raise_server_exceptions=False)
    template = f"/api/v1/projects/{project_id}/resources/{{kind}}/{{resource}}/assets/asset-1"

    review = client.get(template.format(kind="review_round", resource="round-1"))
    dataset = client.get(template.format(kind="dataset", resource="dataset-1"))
    inference = client.get(template.format(kind="inference", resource="inference-1"))
    assert review.status_code == dataset.status_code == inference.status_code == 200
    assert review.json()["selected_overlay_id"] == "revision-human"
    assert dataset.json()["selected_overlay_id"] == "revision-human"
    assert inference.json()["selected_overlay_id"] == "revision-machine"
    assert {item["id"] for item in review.json()["overlays"]} == {
        "revision-human", "revision-machine"
    }


def test_review_report_reconciles_decisions_classes_and_problem_notes(tmp_path: Path) -> None:
    settings, project_id, _ = _context(tmp_path)
    client = TestClient(create_app(settings), raise_server_exceptions=False)
    response = client.get(
        f"/api/v1/projects/{project_id}/review-rounds/round-1/report"
    )

    assert response.status_code == 200
    report = response.json()
    assert report["summary"]["total"] == 3
    assert report["summary"]["corrected"] == 1
    assert report["summary"]["excluded"] == 1
    assert report["summary"]["pending"] == 1
    assert report["summary"]["completion_rate"] == 66.67
    assert report["by_class"]["Cr"]["total"] == 2
    assert report["by_class"]["Sc"]["excluded"] == 1
    assert report["problems"] == [{
        "item_id": "item-3", "asset_id": "asset-3", "filename": "Sc_1.bmp",
        "class_id": 5, "class_name": "Sc", "state": "excluded",
        "note": "未确认到有效划痕", "revision": 1,
    }]
