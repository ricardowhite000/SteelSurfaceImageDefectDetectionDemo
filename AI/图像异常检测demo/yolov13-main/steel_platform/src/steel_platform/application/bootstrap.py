from __future__ import annotations

import csv
import hashlib
from pathlib import Path

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from steel_platform.application.sampling import CandidateSample, select_balanced_round
from steel_platform.infrastructure.artifacts import LocalArtifactStore
from steel_platform.infrastructure.config import PlatformSettings
from steel_platform.infrastructure.database import make_engine
from steel_platform.infrastructure.models import (
    AnnotationRevisionModel,
    AssetModel,
    CandidatePredictionModel,
    InferenceRunModel,
    ProjectModel,
    ReviewItemModel,
    ReviewRoundModel,
    SourceRootModel,
)
from steel_platform.infrastructure.yolo import parse_yolo_text


IMAGE_SUFFIXES = {".bmp", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _dhash(path: Path) -> int:
    with Image.open(path) as image:
        resized = image.convert("L").resize((9, 8))
        pixels = list(resized.get_flattened_data() if hasattr(resized, "get_flattened_data") else resized.getdata())
    value = 0
    for row in range(8):
        for column in range(8):
            left = pixels[row * 9 + column]
            right = pixels[row * 9 + column + 1]
            value = (value << 1) | int(left > right)
    # SQLite INTEGER is signed 64-bit. Keeping 63 deterministic bits retains
    # ample diversity information without risking an overflow on insertion.
    return value & ((1 << 63) - 1)


def _optional_float(value: str | None) -> float | None:
    return float(value) if value not in {None, ""} else None


def bootstrap_project(settings: PlatformSettings) -> str:
    if not settings.source_images.is_dir() or not settings.review_csv.is_file():
        raise FileNotFoundError("缺少原图目录或pseudo_review.csv")
    engine = make_engine(settings.database_url)
    store = LocalArtifactStore(settings.artifact_root)
    with Session(engine) as session:
        existing = session.scalar(select(ProjectModel).where(ProjectModel.name == settings.project_name))
        if existing is not None:
            return existing.id
        project = ProjectModel(name=settings.project_name)
        session.add(project)
        session.flush()
        root = SourceRootModel(project_id=project.id, kind="images", path=str(settings.source_images), read_only=True)
        session.add(root)
        session.flush()

        image_assets: dict[str, AssetModel] = {}
        for path in sorted(settings.source_images.iterdir(), key=lambda item: item.name):
            if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            asset = AssetModel(
                project_id=project.id,
                source_root_id=root.id,
                kind="image",
                relative_path=path.name,
                sha256=_sha256(path),
                size_bytes=path.stat().st_size,
                media_type=f"image/{path.suffix.lower().lstrip('.')}",
            )
            session.add(asset)
            image_assets[path.name] = asset
        session.flush()

        inference = InferenceRunModel(project_id=project.id, name="seed-v1-candidates", status="succeeded")
        session.add(inference)
        session.flush()
        with settings.review_csv.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                filename = row["filename"]
                image_asset = image_assets.get(filename)
                if image_asset is None:
                    raise ValueError(f"复核清单引用了不存在的图片：{filename}")
                label_path = settings.candidate_labels / f"{Path(filename).stem}.txt"
                annotation: AnnotationRevisionModel | None = None
                if label_path.is_file():
                    text = label_path.read_text(encoding="utf-8-sig")
                    boxes = parse_yolo_text(text, source=label_path)
                    ref = store.put_bytes(text.encode("utf-8"), media_type="text/yolo")
                    annotation = AnnotationRevisionModel(
                        project_id=project.id,
                        image_asset_id=image_asset.id,
                        origin="machine",
                        storage_key=ref.storage_key,
                        sha256=ref.sha256,
                        box_count=len(boxes),
                    )
                    session.add(annotation)
                    session.flush()
                session.add(
                    CandidatePredictionModel(
                        project_id=project.id,
                        inference_run_id=inference.id,
                        image_asset_id=image_asset.id,
                        annotation_revision_id=annotation.id if annotation else None,
                        filename=filename,
                        expected_class_id=int(row["expected_class_id"]),
                        predicted_class_ids=row.get("predicted_class_ids", ""),
                        box_count=int(row.get("box_count") or 0),
                        min_confidence=_optional_float(row.get("min_confidence")),
                        max_confidence=_optional_float(row.get("max_confidence")),
                        source_status=row["status"],
                        diversity_hash=_dhash(settings.source_images / filename),
                    )
                )
        session.commit()
        return project.id


def create_review_round(settings: PlatformSettings, *, project_id: str, round_number: int) -> str:
    engine = make_engine(settings.database_url)
    with Session(engine) as session:
        existing = session.scalar(
            select(ReviewRoundModel).where(
                ReviewRoundModel.project_id == project_id,
                ReviewRoundModel.number == round_number,
                ReviewRoundModel.kind == "training",
            )
        )
        if existing is not None:
            return existing.id
        predictions = session.scalars(
            select(CandidatePredictionModel).where(CandidatePredictionModel.project_id == project_id)
        ).all()
        candidates = [
            CandidateSample(
                filename=item.filename,
                class_id=item.expected_class_id,
                status=item.source_status,
                min_confidence=item.min_confidence,
                box_count=item.box_count,
                diversity_hash=item.diversity_hash,
            )
            for item in predictions
        ]
        risk_quota = min(18, int(settings.per_class * 0.6))
        uncertainty_quota = min(6, int(settings.per_class * 0.2))
        selected = select_balanced_round(
            candidates,
            per_class=settings.per_class,
            risk_quota=risk_quota,
            uncertainty_quota=uncertainty_quota,
            seed=settings.seed,
        )
        predictions_by_name = {item.filename: item for item in predictions}
        round_model = ReviewRoundModel(
            project_id=project_id,
            number=round_number,
            kind="training",
            per_class=settings.per_class,
        )
        session.add(round_model)
        session.flush()
        class_positions: dict[int, int] = {}
        for rank, item in enumerate(selected, start=1):
            prediction = predictions_by_name[item.filename]
            position = class_positions.get(item.class_id, 0)
            class_positions[item.class_id] = position + 1
            split = "val" if position < settings.validation_per_class else "train"
            session.add(
                ReviewItemModel(
                    round_id=round_model.id,
                    image_asset_id=prediction.image_asset_id,
                    candidate_revision_id=prediction.annotation_revision_id,
                    filename=item.filename,
                    expected_class_id=item.class_id,
                    source_status=item.status,
                    min_confidence=prediction.min_confidence,
                    max_confidence=prediction.max_confidence,
                    box_count=item.box_count,
                    selection_reason=item.selection_reason,
                    split_role=split,
                    rank=rank,
                )
            )
        session.commit()
        return round_model.id
