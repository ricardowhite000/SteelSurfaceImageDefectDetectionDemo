from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from .common import class_id_from_filename


@dataclass(frozen=True)
class DetectionRecord:
    source_file: str
    frame_index: int
    time_seconds: float
    class_id: int
    class_name: str
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _as_list(value: Any) -> list:
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "tolist"):
        value = value.tolist()
    return list(value)


def _class_name(names: dict | list | tuple, class_id: int) -> str:
    return str(names[class_id] if not isinstance(names, dict) else names.get(class_id, class_id))


def _clipped_normalized_xywh(coordinates: list, width: int, height: int) -> tuple[float, float, float, float] | None:
    x1, y1, x2, y2 = (float(value) for value in coordinates)
    x1 = max(0.0, min(float(width), x1))
    y1 = max(0.0, min(float(height), y1))
    x2 = max(0.0, min(float(width), x2))
    y2 = max(0.0, min(float(height), y2))
    if x2 <= x1 or y2 <= y1:
        return None

    x_center = round((x1 + x2) / (2 * width), 6)
    y_center = round((y1 + y2) / (2 * height), 6)
    box_width = round((x2 - x1) / width, 6)
    box_height = round((y2 - y1) / height, 6)
    box_width = round(min(box_width, 2 * x_center, 2 * (1 - x_center)), 6)
    box_height = round(min(box_height, 2 * y_center, 2 * (1 - y_center)), 6)
    if box_width <= 0 or box_height <= 0:
        return None
    return x_center, y_center, box_width, box_height


def detection_records(result: Any, frame_index: int, time_seconds: float) -> list[DetectionRecord]:
    boxes = getattr(result, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return []
    height, width = result.orig_shape[:2]
    class_ids = _as_list(boxes.cls)
    confidences = _as_list(boxes.conf)
    coordinates = _as_list(boxes.xyxy)
    records: list[DetectionRecord] = []
    for raw_class, confidence, coords in zip(class_ids, confidences, coordinates):
        class_id = int(raw_class)
        x1, y1, x2, y2 = (float(value) for value in coords)
        records.append(
            DetectionRecord(
                source_file=str(result.path),
                frame_index=frame_index,
                time_seconds=round(float(time_seconds), 6),
                class_id=class_id,
                class_name=_class_name(result.names, class_id),
                confidence=float(confidence),
                x1=max(0.0, min(float(width), x1)),
                y1=max(0.0, min(float(height), y1)),
                x2=max(0.0, min(float(width), x2)),
                y2=max(0.0, min(float(height), y2)),
            )
        )
    return records


def validation_image_count(data_yaml: Path) -> int:
    data = yaml.safe_load(data_yaml.read_text(encoding="utf-8"))
    dataset_root = Path(data.get("path", "."))
    if not dataset_root.is_absolute():
        dataset_root = (data_yaml.parent / dataset_root).resolve()
    sources = data.get("val", [])
    if isinstance(sources, (str, Path)):
        sources = [sources]
    suffixes = {".bmp", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}
    images: set[Path] = set()
    for raw_source in sources:
        source = Path(raw_source)
        if not source.is_absolute():
            source = dataset_root / source
        if source.is_dir():
            images.update(path.resolve() for path in source.rglob("*") if path.is_file() and path.suffix.lower() in suffixes)
        elif source.is_file() and source.suffix.lower() == ".txt":
            for line in source.read_text(encoding="utf-8-sig").splitlines():
                if not line.strip():
                    continue
                image = Path(line.strip())
                if not image.is_absolute():
                    image = dataset_root / image
                if image.suffix.lower() in suffixes:
                    images.add(image.resolve())
        elif source.is_file() and source.suffix.lower() in suffixes:
            images.add(source.resolve())
    return len(images)


def metrics_to_summary(
    metrics: Any,
    names: dict | list | tuple,
    *,
    validation_count: int | None = None,
) -> dict[str, Any]:
    box = metrics.box
    class_indices = [int(value) for value in _as_list(box.ap_class_index)]
    precision = _as_list(box.p)
    recall = _as_list(box.r)
    ap50 = _as_list(box.ap50)
    maps = _as_list(box.maps)
    per_class = []
    for position, class_id in enumerate(class_indices):
        per_class.append(
            {
                "class_id": class_id,
                "class_name": _class_name(names, class_id),
                "precision": float(precision[position]),
                "recall": float(recall[position]),
                "mAP50": float(ap50[position]),
                "mAP50-95": float(maps[class_id]),
            }
        )
    warning = (
        f"验证集共有 {validation_count} 张图；阶段性指标仅用于同一固定验证集上的流程检查和模型比较，不代表生产性能。"
        if validation_count is not None
        else "阶段性指标仅用于流程检查和模型比较，不代表生产性能。"
    )
    return {
        "warning": warning,
        "validation_image_count": validation_count,
        "overall": {
            "precision": float(box.mp),
            "recall": float(box.mr),
            "mAP50": float(box.map50),
            "mAP50-95": float(box.map),
        },
        "per_class": per_class,
        "speed_ms": {key: float(value) for key, value in getattr(metrics, "speed", {}).items()},
    }


def pseudo_label_rows(result: Any, review_conf: float) -> tuple[list[str], dict[str, Any]]:
    expected_class = class_id_from_filename(Path(result.path).name)
    boxes = getattr(result, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return [], {
            "filename": Path(result.path).name,
            "expected_class_id": expected_class,
            "predicted_class_ids": "",
            "box_count": 0,
            "min_confidence": "",
            "max_confidence": "",
            "status": "no_box",
        }

    predicted_classes = [int(value) for value in _as_list(boxes.cls)]
    confidences = [float(value) for value in _as_list(boxes.conf)]
    height, width = result.orig_shape[:2]
    labels = []
    invalid_box_count = 0
    for coordinates in _as_list(boxes.xyxy):
        normalized = _clipped_normalized_xywh(coordinates, width, height)
        if normalized is None:
            invalid_box_count += 1
            continue
        labels.append(f"{expected_class} " + " ".join(f"{value:.6f}" for value in normalized))
    flags: list[str] = []
    if any(class_id != expected_class for class_id in predicted_classes):
        flags.append("class_mismatch")
    if any(confidence < review_conf for confidence in confidences):
        flags.append("low_confidence")
    if invalid_box_count:
        flags.append("invalid_box_removed")
    return labels, {
        "filename": Path(result.path).name,
        "expected_class_id": expected_class,
        "predicted_class_ids": ";".join(str(value) for value in predicted_classes),
        "box_count": len(labels),
        "min_confidence": min(confidences),
        "max_confidence": max(confidences),
        "status": ";".join(flags) if flags else "review",
    }
