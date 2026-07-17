from __future__ import annotations

import csv
import json
from pathlib import Path

from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from steel_platform.application.review import ReviewService
from steel_platform.application.workflows import (
    create_audit_round,
    ingest_inference_run,
    ingest_training_run,
    prepare_inference_job,
    prepare_training_jobs,
    publish_dataset,
)
from steel_platform.infrastructure.database import make_engine
from steel_platform.application.maintenance import verify_external_sources
from steel_platform.infrastructure.models import DatasetMemberModel, DatasetVersionModel, InferenceRunModel, JobModel, ModelVersionModel, ReviewItemModel
from test_review_api import _prepared_workspace


def _add_seed_dataset(settings) -> None:
    rows = []
    for class_id, prefix in enumerate(settings.classes):
        for split in ("train", "val"):
            image_dir = settings.seed_dataset / "images" / split
            label_dir = settings.seed_dataset / "labels" / split
            image_dir.mkdir(parents=True, exist_ok=True)
            label_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{prefix}_seed_{split}.bmp"
            Image.new("RGB", (32, 24), (class_id * 25, 60 if split == "train" else 61, 90)).save(image_dir / filename)
            (label_dir / f"{Path(filename).stem}.txt").write_text(
                f"{class_id} 0.5 0.5 0.2 0.2\n", encoding="utf-8"
            )
            rows.append({"filename": filename, "class_id": class_id, "split": split})
    with settings.seed_manifest.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def _accept_all(settings) -> None:
    service = ReviewService(settings)
    for index, item in enumerate(service.list_queue()["items"]):
        detail = service.get_item(item["id"])
        service.decide(
            item["id"],
            idempotency_key=f"accept-{index}",
            expected_revision=detail["revision"],
            action="accepted",
            boxes_data=detail["boxes"],
            note="test",
        )


def test_publish_prepare_and_ingest_real_vertical_slice(tmp_path: Path) -> None:
    settings, _ = _prepared_workspace(tmp_path)
    _add_seed_dataset(settings)
    _accept_all(settings)

    dataset_id = publish_dataset(settings, round_number=1)
    dataset_dir = settings.artifact_root / "materialized" / "datasets" / dataset_id
    manifest = json.loads((dataset_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "steel-defects-v1"
    assert manifest["counts"] == {"train": 12, "val": 6}
    assert len(list((dataset_dir / "images" / "train").iterdir())) == 12
    assert len(list((dataset_dir / "labels" / "val").iterdir())) == 6

    job_ids = prepare_training_jobs(settings, dataset_id=dataset_id)
    assert len(job_ids) == 3
    engine = make_engine(settings.database_url)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(DatasetMemberModel)) == 18
        jobs = session.scalars(select(JobModel).order_by(JobModel.created_at)).all()
        assert [job.kind for job in jobs] == ["train_smoke", "train_formal", "evaluate"]
        command = (settings.artifact_root / jobs[0].command_key).read_text(encoding="utf-8")
        assert "05_train.py" in command and "--smoke" in command

    run_dir = tmp_path / "fake_train"
    (run_dir / "weights").mkdir(parents=True)
    (run_dir / "weights" / "best.pt").write_bytes(b"best-model")
    (run_dir / "weights" / "last.pt").write_bytes(b"last-model")
    (run_dir / "results.csv").write_text("epoch,metrics/mAP50(B)\n0,0.42\n", encoding="utf-8")
    (run_dir / "metrics_summary.json").write_text(json.dumps({"map50": 0.42}), encoding="utf-8")
    model_id = ingest_training_run(settings, job_id=job_ids[1], run_dir=run_dir)
    with Session(engine) as session:
        model = session.get(ModelVersionModel, model_id)
        assert model is not None
    inference_job_id = prepare_inference_job(settings, model_id=model_id)
    with Session(engine) as session:
        inference_job = session.get(JobModel, inference_job_id)
        assert inference_job is not None
        command = (settings.artifact_root / inference_job.command_key).read_text(encoding="utf-8")
        assert "--batch 1" in command and "stream" not in command.lower()
        prediction_dir = Path(inference_job.spec_json["output"])
        materialized_weights = Path(inference_job.spec_json["weights"])
        assert materialized_weights.suffix == ".pt"
        assert materialized_weights.read_bytes() == b"best-model"
    sources = [Path(line) for line in (prediction_dir / "sources.txt").read_text(encoding="utf-8").splitlines()]
    assert len(sources) == 6
    rows = []
    for source in sources:
        class_id = settings.classes.index(source.stem.split("_")[0])
        (prediction_dir / f"{source.stem}.txt").write_text(
            f"{class_id} 0.5 0.5 0.2 0.2\n", encoding="utf-8"
        )
        rows.append({"filename": source.name, "expected_class_id": class_id, "predicted_class_ids": str(class_id), "box_count": 1, "min_confidence": .6, "max_confidence": .7, "status": "ok"})
    with (prediction_dir / "pseudo_review.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys()); writer.writeheader(); writer.writerows(rows)
    inference_id = ingest_inference_run(settings, job_id=inference_job_id, prediction_dir=prediction_dir)
    source_report = verify_external_sources(settings)
    assert source_report["candidate_labels"] == 12
    assert source_report["invalid"] == 0
    audit_id = create_audit_round(settings, inference_id, 1)
    with Session(engine) as session:
        assert session.get(InferenceRunModel, inference_id) is not None
        assert session.scalar(select(func.count()).select_from(ReviewItemModel).where(ReviewItemModel.round_id == audit_id)) == 6


def test_dataset_publish_is_idempotent(tmp_path: Path) -> None:
    settings, _ = _prepared_workspace(tmp_path)
    _add_seed_dataset(settings)
    _accept_all(settings)
    first = publish_dataset(settings, round_number=1)
    second = publish_dataset(settings, round_number=1)
    assert first == second
    with Session(make_engine(settings.database_url)) as session:
        assert session.scalar(select(func.count()).select_from(DatasetVersionModel)) == 1
