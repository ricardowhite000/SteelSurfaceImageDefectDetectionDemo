from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

CLASS_NAMES = ("Cr", "In", "Pa", "PS", "RS", "Sc")
CLASS_TO_ID = {name: index for index, name in enumerate(CLASS_NAMES)}
IMAGE_SUFFIXES = {".bmp", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}


@dataclass(frozen=True)
class YoloBox:
    class_id: int
    x_center: float
    y_center: float
    width: float
    height: float

    def to_line(self, class_id: int | None = None) -> str:
        target_class = self.class_id if class_id is None else class_id
        return (
            f"{target_class} {self.x_center:.6f} {self.y_center:.6f} "
            f"{self.width:.6f} {self.height:.6f}"
        )


@dataclass(frozen=True)
class AuditIssue:
    code: str
    path: str
    message: str
    line: int | None = None


@dataclass
class AuditReport:
    image_count: int
    label_count: int
    box_count: int
    images_per_class: dict[str, int]
    boxes_per_class: dict[str, int]
    issues: list[AuditIssue]

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "image_count": self.image_count,
            "label_count": self.label_count,
            "box_count": self.box_count,
            "images_per_class": self.images_per_class,
            "boxes_per_class": self.boxes_per_class,
            "issues": [asdict(issue) for issue in self.issues],
        }


@dataclass(frozen=True)
class SeedManifestRow:
    filename: str
    prefix: str
    class_id: int
    source_path: str
    reused_front_label: bool
    sha256: str


@dataclass(frozen=True)
class SplitManifestRow:
    filename: str
    prefix: str
    class_id: int
    split: str
    sha256: str


def class_prefix_from_filename(path: str | Path) -> str:
    stem = Path(path).stem
    prefix = stem.split("_", 1)[0]
    if prefix not in CLASS_TO_ID:
        raise ValueError(f"无法从文件名识别类别：{Path(path).name}")
    return prefix


def class_id_from_filename(path: str | Path) -> int:
    return CLASS_TO_ID[class_prefix_from_filename(path)]


def stable_pick(paths: Iterable[Path], count: int, seed: int, namespace: str) -> list[Path]:
    candidates = list(paths)
    if count < 0:
        raise ValueError("count 不能小于 0")
    if len(candidates) < count:
        raise ValueError(f"{namespace} 只有 {len(candidates)} 张图，无法选择 {count} 张")

    def score(path: Path) -> tuple[str, str]:
        digest = hashlib.sha256(f"{seed}:{namespace}:{path.name}".encode("utf-8")).hexdigest()
        return digest, path.name

    return sorted(candidates, key=score)[:count]


def parse_yolo_line(line: str, source: Path, line_number: int) -> YoloBox:
    parts = line.strip().split()
    location = f"{source}:{line_number}"
    if len(parts) != 5:
        raise ValueError(f"{location} 必须恰好包含 5 列，当前为 {len(parts)} 列")
    try:
        class_value = float(parts[0])
        values = [float(value) for value in parts[1:]]
    except ValueError as exc:
        raise ValueError(f"{location} 包含非数字内容") from exc
    if not class_value.is_integer() or class_value < 0:
        raise ValueError(f"{location} 类别编号必须是非负整数")
    x_center, y_center, width, height = values
    if width <= 0 or height <= 0:
        raise ValueError(f"{location} 宽高必须大于 0")
    if any(value < 0 or value > 1 for value in values):
        raise ValueError(f"{location} 坐标必须位于 0 到 1 之间")
    epsilon = 1e-9
    if (
        x_center - width / 2 < -epsilon
        or x_center + width / 2 > 1 + epsilon
        or y_center - height / 2 < -epsilon
        or y_center + height / 2 > 1 + epsilon
    ):
        raise ValueError(f"{location} 边界框超出图像范围")
    return YoloBox(int(class_value), x_center, y_center, width, height)


def read_yolo_file(path: Path) -> list[YoloBox]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    return [parse_yolo_line(line, path, index) for index, line in enumerate(lines, start=1) if line.strip()]


def image_files(directory: Path) -> list[Path]:
    return sorted(
        (path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES),
        key=lambda path: path.name,
    )


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_empty_destination(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise FileExistsError(f"目标目录不为空，为避免覆盖人工成果已停止：{path}")
    path.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_yolo_file(path: Path, boxes: Sequence[YoloBox], class_id: int | None = None) -> None:
    text = "".join(f"{box.to_line(class_id)}\n" for box in boxes)
    path.write_text(text, encoding="utf-8")

