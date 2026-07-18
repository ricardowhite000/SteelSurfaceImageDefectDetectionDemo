from __future__ import annotations

import csv
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session
import yaml

from steel_platform.application.bootstrap import bootstrap_project, create_review_round
from steel_platform.domain.annotations import AnnotationBox
from steel_platform.infrastructure.config import load_settings
from steel_platform.infrastructure.database import make_engine, upgrade_database
from steel_platform.infrastructure.directory_picker import LocalFolderReader
from steel_platform.infrastructure.artifacts import ArtifactRef, LocalArtifactStore
from steel_platform.infrastructure.models import AssetModel, AnnotationRevisionModel, DomainEventModel, ReviewItemModel
from steel_platform.infrastructure.yolo import parse_yolo_text
from steel_platform.interfaces.api import create_app


def _prepared_workspace(tmp_path: Path):
    images = tmp_path / "images"
    labels = tmp_path / "labels"
    images.mkdir()
    labels.mkdir()
    rows = []
    classes = ("Cr", "In", "Pa", "PS", "RS", "Sc")
    for class_id, prefix in enumerate(classes):
        for index in range(2):
            filename = f"{prefix}_{index}.bmp"
            Image.new("RGB", (32, 24), (class_id * 30, index * 20, 80)).save(images / filename)
            (labels / f"{prefix}_{index}.txt").write_text(
                f"{class_id} 0.5 0.5 0.25 0.25\n", encoding="utf-8"
            )
            rows.append(
                {
                    "filename": filename,
                    "expected_class_id": class_id,
                    "predicted_class_ids": str(class_id),
                    "box_count": 1,
                    "min_confidence": 0.3 + index * 0.1,
                    "max_confidence": 0.5 + index * 0.1,
                    "status": "low_confidence" if index == 0 else "ok",
                }
            )
    review_csv = tmp_path / "pseudo_review.csv"
    with review_csv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    (tmp_path / "seed_manifest.csv").write_text("filename,split\n", encoding="utf-8")
    (tmp_path / "seed_dataset").mkdir()
    config = {
        "project_name": "api-test",
        "database_url": "sqlite:///platform.db",
        "artifact_root": "artifacts",
        "source_images": "images",
        "candidate_labels": "labels",
        "review_csv": "pseudo_review.csv",
        "seed_manifest": "seed_manifest.csv",
        "seed_dataset": "seed_dataset",
        "classes": list(classes),
        "per_class": 1,
        "validation_per_class": 0,
    }
    path = tmp_path / "platform.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    settings = load_settings(path)
    upgrade_database(settings.database_url)
    project_id = bootstrap_project(settings)
    round_id = create_review_round(settings, project_id=project_id, round_number=1)
    return settings, project_id, round_id


def test_review_api_is_idempotent_and_rejects_stale_revision(tmp_path: Path) -> None:
    settings, project_id, round_id = _prepared_workspace(tmp_path)
    client = TestClient(create_app(settings))

    queue = client.get(f"/api/v1/projects/{project_id}/review-rounds/{round_id}/items").json()
    item_id = queue["items"][0]["id"]
    detail = client.get(f"/api/v1/projects/{project_id}/review-rounds/{round_id}/items/{item_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["revision"] == 0
    assert body["boxes"][0]["class_id"] == body["expected_class_id"]

    payload = {
        "expected_revision": 0,
        "decision": "accepted",
        "boxes": body["boxes"],
        "note": "候选框正确",
    }
    headers = {"Idempotency-Key": "accept-first"}
    url = f"/api/v1/projects/{project_id}/review-rounds/{round_id}/items/{item_id}/decision"
    first = client.put(url, json=payload, headers=headers)
    second = client.put(url, json=payload, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["revision"] == 1

    stale = client.put(
        url,
        json=payload,
        headers={"Idempotency-Key": "stale-attempt"},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "revision_conflict"
    assert stale.json()["request_id"]

    engine = make_engine(settings.database_url)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(AnnotationRevisionModel)) == 13
        event = session.scalar(select(DomainEventModel).where(DomainEventModel.project_id == project_id))
        assert event is not None and event.event_type == "annotation.reviewed"


def _assert_error_shape(response) -> None:
    body = response.json()
    assert {"code", "message", "details", "request_id"} <= body.keys()
    assert body["request_id"]


def test_asset_route_uses_registered_id_and_rejects_missing_or_escaping_asset(tmp_path: Path) -> None:
    settings, project_id, round_id = _prepared_workspace(tmp_path)
    client = TestClient(create_app(settings))
    queue = client.get(f"/api/v1/projects/{project_id}/review-rounds/{round_id}/items").json()
    item = queue["items"][0]

    asset = client.get(f"/api/v1/projects/{project_id}/assets/{item['image_asset_id']}/content")
    assert asset.status_code == 200
    assert asset.headers["content-type"].startswith("image/")
    missing = client.get(f"/api/v1/projects/{project_id}/assets/not-a-registered-asset/content")
    assert missing.status_code == 404
    _assert_error_shape(missing)

    sentinel = tmp_path / "outside.bmp"
    sentinel_bytes = b"TASK7-OUTSIDE-SENTINEL-DO-NOT-SERVE"
    sentinel.write_bytes(sentinel_bytes)
    with Session(make_engine(settings.database_url)) as session:
        registered = session.get(AssetModel, item["image_asset_id"])
        assert registered is not None
        registered.relative_path = "../outside.bmp"
        session.commit()
    escaping = client.get(f"/api/v1/projects/{project_id}/assets/{item['image_asset_id']}/content")
    assert escaping.status_code == 409
    _assert_error_shape(escaping)
    assert escaping.json()["code"] == "source_offline"
    assert sentinel_bytes.decode("ascii") not in escaping.text


def test_external_reader_rejects_registered_path_escaping_root(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "registered-root"
    root.mkdir()
    sentinel = tmp_path / "outside.bmp"
    sentinel.write_bytes(b"TASK7-OUTSIDE-SENTINEL-DO-NOT-SERVE")

    import pytest

    calls = 0
    original_open = Path.open

    def spy_open(path: Path, *args, **kwargs):
        nonlocal calls
        calls += 1
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", spy_open)
    with pytest.raises(ValueError):
        LocalFolderReader().open_verified(
            root.as_posix(),
            "../outside.bmp",
            expected_sha256="0" * 64,
            expected_size_bytes=0,
        )
    assert calls == 0



def test_exclusion_requires_a_reason_and_doubtful_saves_draft(tmp_path: Path) -> None:
    settings, project_id, round_id = _prepared_workspace(tmp_path)
    client = TestClient(create_app(settings))
    base = f"/api/v1/projects/{project_id}/review-rounds/{round_id}/items"
    item_id = client.get(base).json()["items"][0]["id"]
    boxes = client.get(f"{base}/{item_id}").json()["boxes"]

    invalid = client.put(
        f"{base}/{item_id}/decision",
        json={"expected_revision": 0, "decision": "excluded", "boxes": [], "note": ""},
        headers={"Idempotency-Key": "exclude-invalid"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "validation_error"

    saved = client.put(
        f"{base}/{item_id}/decision",
        json={"expected_revision": 0, "decision": "doubtful", "boxes": boxes, "note": "纹理不清"},
        headers={"Idempotency-Key": "doubtful-first"},
    )
    assert saved.status_code == 200
    assert saved.json()["state"] == "doubtful"
    with Session(make_engine(settings.database_url)) as session:
        item = session.get(ReviewItemModel, item_id)
        assert item is not None and item.current_revision_id is None


def test_invalid_decision_automatically_adds_same_class_replacement(tmp_path: Path) -> None:
    settings, project_id, round_id = _prepared_workspace(tmp_path)
    client = TestClient(create_app(settings))
    base = f"/api/v1/projects/{project_id}/review-rounds/{round_id}/items"
    original = client.get(f"{base}?class_id=0").json()["items"]
    assert len(original) == 1
    response = client.put(
        f"{base}/{original[0]['id']}/decision",
        json={"expected_revision": 0, "decision": "excluded", "boxes": [], "note": "无可确认裂纹"},
        headers={"Idempotency-Key": "replace-cr"},
    )
    assert response.status_code == 200
    updated = client.get(f"{base}?class_id=0").json()["items"]
    assert len(updated) == 2
    assert sum(item["state"] == "pending" for item in updated) == 1
    assert updated[-1]["selection_reason"] == "replacement"


def test_rounding_repair_is_dry_run_versioned_and_idempotent(tmp_path: Path) -> None:
    from steel_platform.application.maintenance import repair_review_rounding

    settings, _, _ = _prepared_workspace(tmp_path)
    engine = make_engine(settings.database_url)
    store = LocalArtifactStore(settings.artifact_root)
    invalid_ref = store.put_bytes(
        b"0 0.500001 0.500000 1.000000 1.000000\n", media_type="text/yolo"
    )
    with Session(engine) as session:
        item = session.scalar(select(ReviewItemModel).where(ReviewItemModel.expected_class_id == 0))
        assert item is not None
        invalid_revision = AnnotationRevisionModel(
            project_id=session.get(AnnotationRevisionModel, item.candidate_revision_id).project_id,
            image_asset_id=item.image_asset_id,
            parent_id=item.candidate_revision_id,
            origin="human",
            decision="corrected",
            storage_key=invalid_ref.storage_key,
            sha256=invalid_ref.sha256,
            box_count=1,
        )
        session.add(invalid_revision)
        session.flush()
        invalid_revision_id = invalid_revision.id
        item.current_revision_id = invalid_revision_id
        item.state = "corrected"
        item.revision = 1
        item_id = item.id
        session.commit()

    preview = repair_review_rounding(settings, round_number=1, apply=False)
    assert preview["invalid"] == preview["repairable"] == 1
    assert preview["repaired"] == 0
    with Session(engine) as session:
        assert session.get(ReviewItemModel, item_id).current_revision_id == invalid_revision_id

    applied = repair_review_rounding(settings, round_number=1, apply=True)
    assert applied["repaired"] == 1
    with Session(engine) as session:
        item = session.get(ReviewItemModel, item_id)
        assert item is not None and item.current_revision_id != invalid_revision_id
        assert item.revision == 2
        repaired = session.get(AnnotationRevisionModel, item.current_revision_id)
        assert repaired is not None and repaired.parent_id == invalid_revision_id
        assert repaired.origin == "system_repair"
        path = store.resolve(ArtifactRef(repaired.storage_key, repaired.sha256, 0, "text/yolo"))
        boxes = parse_yolo_text(path.read_text(encoding="utf-8"), source=path)
        assert boxes == (AnnotationBox(0, 0.5, 0.5, 1.0, 1.0),)

    repeated = repair_review_rounding(settings, round_number=1, apply=True)
    assert repeated["invalid"] == repeated["repaired"] == 0
