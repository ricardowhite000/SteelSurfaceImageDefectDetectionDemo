from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from steel_platform.infrastructure.database import make_engine
from steel_platform.infrastructure.models import ReviewRoundModel
from steel_platform.interfaces.api import create_app
from test_resource_browser_api import _context


def test_inference_filter_preview_and_freeze_create_reproducible_work_order(
    tmp_path: Path,
) -> None:
    settings, project_id, _ = _context(tmp_path)
    client = TestClient(create_app(settings), raise_server_exceptions=False)
    request = {
        "name": "低置信度裂纹专项复核",
        "task_type": "inference_review",
        "source_type": "inference",
        "source_id": "inference-1",
        "filters": {
            "class_ids": [0],
            "risk_statuses": ["low_confidence"],
            "max_min_confidence": 0.30,
            "include_no_box": False,
            "exclude_reviewed": False,
            "total_limit": 10,
            "seed": 42,
        },
    }

    preview = client.post(
        f"/api/v1/projects/{project_id}/annotation-work-orders/preview",
        json=request,
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["matched"] == 1
    assert preview.json()["selected"] == 1
    assert preview.json()["by_class"] == {"Cr": 1}
    assert preview.json()["sample_asset_ids"] == ["asset-1"]

    created = client.post(
        f"/api/v1/projects/{project_id}/annotation-work-orders",
        headers={"Idempotency-Key": "create-low-confidence-cr"},
        json=request,
    )
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "draft"
    work_order_id = created.json()["id"]

    frozen = client.post(
        f"/api/v1/projects/{project_id}/annotation-work-orders/{work_order_id}/freeze",
        headers={"Idempotency-Key": "freeze-low-confidence-cr"},
        json={"expected_revision": 0},
    )
    assert frozen.status_code == 200, frozen.text
    assert frozen.json()["status"] == "active"
    assert frozen.json()["target_count"] == 1
    assert frozen.json()["manifest_sha256"]

    items = client.get(
        f"/api/v1/projects/{project_id}/annotation-work-orders/{work_order_id}/items"
    )
    assert items.status_code == 200
    assert [item["image_asset_id"] for item in items.json()["items"]] == ["asset-1"]


def test_completed_work_order_is_browsable_and_amendment_creates_new_lineage(
    tmp_path: Path,
) -> None:
    settings, project_id, _ = _context(tmp_path)
    with Session(make_engine(settings.database_url)) as session:
        original = session.get(ReviewRoundModel, "round-1")
        original.status = "completed"
        session.commit()
    client = TestClient(create_app(settings), raise_server_exceptions=False)

    completed = client.get(
        f"/api/v1/projects/{project_id}/annotation-work-orders/round-1"
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"

    amendment = client.post(
        f"/api/v1/projects/{project_id}/annotation-work-orders/round-1/amendments",
        headers={"Idempotency-Key": "amend-round-1-item-1"},
        json={"name": "裂纹框二次修订", "item_ids": ["item-1"]},
    )
    assert amendment.status_code == 201, amendment.text
    body = amendment.json()
    assert body["task_type"] == "amendment"
    assert body["parent_work_order_id"] == "round-1"
    assert body["status"] == "active"
    assert body["target_count"] == 1


def test_manual_annotation_work_order_selects_unannotated_source_images(
    tmp_path: Path,
) -> None:
    settings, project_id, _ = _context(tmp_path)
    client = TestClient(create_app(settings), raise_server_exceptions=False)
    request = {
        "name": "原图初始标注",
        "task_type": "manual_annotation",
        "source_type": "source",
        "source_id": "source-images",
        "filters": {"total_limit": 10, "seed": 42},
    }

    preview = client.post(
        f"/api/v1/projects/{project_id}/annotation-work-orders/preview", json=request
    )

    assert preview.status_code == 200, preview.text
    assert preview.json()["matched"] == 2
    assert preview.json()["by_class"] == {"Cr": 1, "Sc": 1}
