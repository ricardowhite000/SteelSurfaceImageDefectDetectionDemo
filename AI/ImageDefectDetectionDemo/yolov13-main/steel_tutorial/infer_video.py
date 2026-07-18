from __future__ import annotations

from pathlib import Path
from typing import Any


def browser_video_path(output_dir: Path, source_path: Path) -> Path:
    """Return a browser-compatible WebM output path for a source video."""

    return output_dir / f"{source_path.stem}.webm"


def video_label_path(output_dir: Path, source_stem: str, frame_index: int) -> Path:
    """Return the YOLO label path for a zero-based video frame index."""

    return output_dir / "labels" / f"{source_stem}_{frame_index + 1}.txt"


def write_video_frame_labels(result: Any, label_path: Path) -> None:
    """Write one frame's detections using YOLO class/xywh/conf rows."""

    boxes = getattr(result, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return
    classes = boxes.cls.detach().cpu().tolist()
    confidence = boxes.conf.detach().cpu().tolist()
    xywhn = boxes.xywhn.detach().cpu().tolist()
    label_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        f"{int(class_id)} "
        + " ".join(f"{float(value):.6f}" for value in coords)
        + f" {float(score):.6f}"
        for class_id, coords, score in zip(classes, xywhn, confidence, strict=True)
    ]
    label_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
