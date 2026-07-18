from __future__ import annotations

from pathlib import Path

import pytest

from steel_platform.domain.annotations import AnnotationBox, AnnotationDecision, ReviewState
from steel_platform.infrastructure.yolo import parse_yolo_text, serialize_yolo


def test_annotation_box_rejects_out_of_bounds_geometry() -> None:
    with pytest.raises(ValueError, match="超出图像范围"):
        AnnotationBox(class_id=0, x_center=0.95, y_center=0.5, width=0.2, height=0.2)


def test_yolo_codec_round_trips_multiple_boxes() -> None:
    boxes = (
        AnnotationBox(class_id=2, x_center=0.25, y_center=0.5, width=0.2, height=0.3),
        AnnotationBox(class_id=2, x_center=0.75, y_center=0.2, width=0.1, height=0.1),
    )

    encoded = serialize_yolo(boxes)
    decoded = parse_yolo_text(encoded, source=Path("Pa_1.txt"))

    assert decoded == boxes


def test_yolo_codec_round_trips_full_image_box() -> None:
    boxes = (AnnotationBox(class_id=0, x_center=0.5, y_center=0.5, width=1.0, height=1.0),)

    encoded = serialize_yolo(boxes)
    decoded = parse_yolo_text(encoded, source=Path("Cr_full.txt"))

    assert encoded == "0 0.500000 0.500000 1.000000 1.000000\n"
    assert decoded == boxes


def test_review_decision_requires_note_for_exclusion() -> None:
    with pytest.raises(ValueError, match="必须填写原因"):
        AnnotationDecision(
            action=ReviewState.EXCLUDED,
            boxes=(),
            note="",
            expected_revision=0,
        )


def test_accept_and_correct_require_at_least_one_box() -> None:
    for action in (ReviewState.ACCEPTED, ReviewState.CORRECTED):
        with pytest.raises(ValueError, match="至少一个有效框"):
            AnnotationDecision(action=action, boxes=(), note="", expected_revision=0)
