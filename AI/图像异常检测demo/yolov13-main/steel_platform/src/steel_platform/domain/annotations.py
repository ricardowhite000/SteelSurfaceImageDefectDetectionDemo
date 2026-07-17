from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ReviewState(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    CORRECTED = "corrected"
    DOUBTFUL = "doubtful"
    EXCLUDED = "excluded"


@dataclass(frozen=True, slots=True)
class AnnotationBox:
    class_id: int
    x_center: float
    y_center: float
    width: float
    height: float

    def __post_init__(self) -> None:
        if self.class_id < 0:
            raise ValueError("类别编号必须是非负整数")
        values = (self.x_center, self.y_center, self.width, self.height)
        if any(value < 0 or value > 1 for value in values):
            raise ValueError("坐标必须位于0到1之间")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("边界框宽高必须大于0")
        epsilon = 1e-9
        if (
            self.x_center - self.width / 2 < -epsilon
            or self.x_center + self.width / 2 > 1 + epsilon
            or self.y_center - self.height / 2 < -epsilon
            or self.y_center + self.height / 2 > 1 + epsilon
        ):
            raise ValueError("边界框超出图像范围")


@dataclass(frozen=True, slots=True)
class AnnotationDecision:
    action: ReviewState
    boxes: tuple[AnnotationBox, ...]
    note: str
    expected_revision: int

    def __post_init__(self) -> None:
        if self.expected_revision < 0:
            raise ValueError("expected_revision不能小于0")
        if self.action in {ReviewState.ACCEPTED, ReviewState.CORRECTED} and not self.boxes:
            raise ValueError("接受或修正必须包含至少一个有效框")
        if self.action == ReviewState.EXCLUDED and not self.note.strip():
            raise ValueError("排除图片必须填写原因")
        if self.action == ReviewState.PENDING:
            raise ValueError("pending不是有效的人工决策")

