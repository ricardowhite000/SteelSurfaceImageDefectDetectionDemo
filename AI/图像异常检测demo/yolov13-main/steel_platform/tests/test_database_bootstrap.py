from __future__ import annotations

import csv
from pathlib import Path

from PIL import Image
from sqlalchemy import create_engine, inspect, select, func
from sqlalchemy.orm import Session
import yaml

from steel_platform.application.bootstrap import bootstrap_project, create_review_round
from steel_platform.infrastructure.config import load_settings
from steel_platform.infrastructure.database import upgrade_database
from steel_platform.infrastructure.models import AssetModel, CandidatePredictionModel, ReviewItemModel


REQUIRED_TABLES = {
    "projects",
    "source_roots",
    "assets",
    "annotation_revisions",
    "review_rounds",
    "review_items",
    "review_drafts",
    "candidate_predictions",
    "dataset_versions",
    "dataset_members",
    "jobs",
    "experiment_runs",
    "model_versions",
    "inference_runs",
    "metric_snapshots",
    "domain_events",
    "outbox_events",
    "idempotency_records",
}


def _workspace(tmp_path: Path) -> Path:
    images = tmp_path / "images"
    labels = tmp_path / "labels"
    seed_dataset = tmp_path / "seed_dataset"
    images.mkdir()
    labels.mkdir()
    seed_dataset.mkdir()
    rows: list[dict[str, object]] = []
    classes = ("Cr", "In", "Pa", "PS", "RS", "Sc")
    for class_id, prefix in enumerate(classes):
        for index in range(4):
            filename = f"{prefix}_{index + 1}.bmp"
            Image.new("L", (32, 32), color=20 + class_id * 20 + index).save(images / filename)
            (labels / f"{prefix}_{index + 1}.txt").write_text(
                f"{class_id} 0.500000 0.500000 0.250000 0.250000\n",
                encoding="utf-8",
            )
            rows.append(
                {
                    "filename": filename,
                    "expected_class_id": class_id,
                    "predicted_class_ids": str(class_id),
                    "box_count": 1,
                    "min_confidence": 0.2 + index / 10,
                    "max_confidence": 0.8,
                    "status": ("no_box", "class_mismatch", "low_confidence", "review")[index],
                }
            )
    with (labels / "pseudo_review.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (tmp_path / "seed_manifest.csv").write_text("filename\n", encoding="utf-8")
    config = {
        "project_name": "test-project",
        "database_url": "sqlite:///workspace/platform.db",
        "artifact_root": "workspace/artifacts",
        "source_images": "images",
        "candidate_labels": "labels",
        "review_csv": "labels/pseudo_review.csv",
        "seed_manifest": "seed_manifest.csv",
        "seed_dataset": "seed_dataset",
        "classes": list(classes),
        "per_class": 2,
        "validation_per_class": 1,
        "seed": 42,
    }
    config_path = tmp_path / "platform.yaml"
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    return config_path


def test_upgrade_database_creates_versioned_platform_schema(tmp_path: Path) -> None:
    settings = load_settings(_workspace(tmp_path))

    upgrade_database(settings.database_url)

    tables = set(inspect(create_engine(settings.database_url)).get_table_names())
    assert REQUIRED_TABLES <= tables
    assert "alembic_version" in tables


def test_bootstrap_registers_assets_predictions_and_balanced_round(tmp_path: Path) -> None:
    settings = load_settings(_workspace(tmp_path))
    upgrade_database(settings.database_url)

    project_id = bootstrap_project(settings)
    round_id = create_review_round(settings, project_id=project_id, round_number=1)

    with Session(create_engine(settings.database_url)) as session:
        asset_count = session.scalar(select(func.count()).select_from(AssetModel))
        prediction_count = session.scalar(select(func.count()).select_from(CandidatePredictionModel))
        items = session.scalars(
            select(ReviewItemModel).where(ReviewItemModel.round_id == round_id).order_by(ReviewItemModel.rank)
        ).all()

    assert asset_count == 24
    assert prediction_count == 24
    assert len(items) == 12
    assert {item.expected_class_id for item in items} == set(range(6))
    assert all(sum(other.expected_class_id == item.expected_class_id for other in items) == 2 for item in items)
    assert all(item.id != item.filename for item in items)


def test_bootstrap_is_idempotent(tmp_path: Path) -> None:
    settings = load_settings(_workspace(tmp_path))
    upgrade_database(settings.database_url)

    first = bootstrap_project(settings)
    second = bootstrap_project(settings)

    assert first == second
    with Session(create_engine(settings.database_url)) as session:
        assert session.scalar(select(func.count()).select_from(AssetModel)) == 24

