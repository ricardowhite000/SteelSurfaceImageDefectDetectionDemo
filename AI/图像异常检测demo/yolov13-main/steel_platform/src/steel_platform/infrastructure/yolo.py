from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable

from steel_platform.domain.annotations import AnnotationBox


_YOLO_SCALE = 1_000_000


def _quantize_axis(center: float, size: float) -> tuple[float, float]:
    """Quantize one YOLO axis without moving a box outside the image."""
    size_units = max(1, min(_YOLO_SCALE, round(size * _YOLO_SCALE)))
    center_units = round(center * _YOLO_SCALE)
    minimum_center = (size_units + 1) // 2
    maximum_center = (2 * _YOLO_SCALE - size_units) // 2
    center_units = max(minimum_center, min(maximum_center, center_units))
    return center_units / _YOLO_SCALE, size_units / _YOLO_SCALE


def parse_yolo_text(text: str, *, source: Path) -> tuple[AnnotationBox, ...]:
    boxes: list[AnnotationBox] = []
    for line_number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        parts = raw.split()
        if len(parts) != 5:
            raise ValueError(f"{source}:{line_number} 必须恰好包含5列")
        try:
            class_value = float(parts[0])
            coordinates = tuple(float(value) for value in parts[1:])
        except ValueError as exc:
            raise ValueError(f"{source}:{line_number} 包含非数字内容") from exc
        if not class_value.is_integer():
            raise ValueError(f"{source}:{line_number} 类别编号必须是整数")
        boxes.append(AnnotationBox(int(class_value), *coordinates))
    return tuple(boxes)


def serialize_yolo(boxes: Iterable[AnnotationBox]) -> str:
    lines = []
    for box in boxes:
        x_center, width = _quantize_axis(box.x_center, box.width)
        y_center, height = _quantize_axis(box.y_center, box.height)
        lines.append(
            f"{box.class_id} {x_center:.6f} {y_center:.6f} {width:.6f} {height:.6f}\n"
        )
    return "".join(lines)


def repair_yolo_rounding_text(
    text: str, *, source: Path, tolerance: float = 1.1e-6
) -> tuple[str, tuple[AnnotationBox, ...]]:
    """Repair only tiny six-decimal boundary overflows from an older serializer.

    Strict parsing remains strict. Values farther than ``tolerance`` outside the
    image are rejected so this helper cannot conceal genuinely bad annotations.
    """
    repaired: list[AnnotationBox] = []
    for line_number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        parts = raw.split()
        if len(parts) != 5:
            raise ValueError(f"{source}:{line_number} 必须恰好包含5列")
        try:
            class_value = float(parts[0])
            x_center, y_center, width, height = (float(value) for value in parts[1:])
        except ValueError as exc:
            raise ValueError(f"{source}:{line_number} 包含非数字内容") from exc
        values = (class_value, x_center, y_center, width, height)
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"{source}:{line_number} 包含非有限数字")
        if not class_value.is_integer() or class_value < 0:
            raise ValueError(f"{source}:{line_number} 类别编号必须是非负整数")
        if width <= 0 or height <= 0:
            raise ValueError(f"{source}:{line_number} 边界框宽高必须大于0")
        extents = (
            x_center - width / 2,
            x_center + width / 2,
            y_center - height / 2,
            y_center + height / 2,
        )
        if (
            x_center < -tolerance
            or x_center > 1 + tolerance
            or y_center < -tolerance
            or y_center > 1 + tolerance
            or width > 1 + tolerance
            or height > 1 + tolerance
            or min(extents[0], extents[2]) < -tolerance
            or max(extents[1], extents[3]) > 1 + tolerance
        ):
            raise ValueError(f"{source}:{line_number} 不是可安全修复的舍入误差")
        safe_x, safe_width = _quantize_axis(x_center, width)
        safe_y, safe_height = _quantize_axis(y_center, height)
        repaired.append(
            AnnotationBox(int(class_value), safe_x, safe_y, safe_width, safe_height)
        )
    encoded = serialize_yolo(repaired)
    parsed = parse_yolo_text(encoded, source=source)
    return encoded, parsed
