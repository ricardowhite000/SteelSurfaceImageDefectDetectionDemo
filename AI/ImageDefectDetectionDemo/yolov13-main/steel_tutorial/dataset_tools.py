from __future__ import annotations

import csv
import json
import re
import shutil
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path

from .common import (
    CLASS_NAMES,
    AuditIssue,
    AuditReport,
    SeedManifestRow,
    SplitManifestRow,
    class_id_from_filename,
    class_prefix_from_filename,
    ensure_empty_destination,
    image_files,
    parse_yolo_line,
    read_yolo_file,
    sha256_file,
    stable_pick,
    write_json,
    write_yolo_file,
)

_FRONT_NAME = re.compile(r"^\d+_(?P<original>(?:Cr|In|Pa|PS|RS|Sc)_\d+\.bmp)$", re.IGNORECASE)


def _write_csv(path: Path, rows: list[object]) -> None:
    if not rows:
        raise ValueError(f"不能写入空清单：{path}")
    fieldnames = list(asdict(rows[0]).keys())
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)


def _front_labels_by_original(front_images_dir: Path, front_labels_dir: Path) -> dict[str, Path]:
    labels: dict[str, Path] = {}
    if not front_images_dir.exists() or not front_labels_dir.exists():
        return labels
    for image_path in image_files(front_images_dir):
        match = _FRONT_NAME.match(image_path.name)
        if not match:
            continue
        label_path = front_labels_dir / f"{image_path.stem}.txt"
        if label_path.exists():
            labels[match.group("original")] = label_path
    return labels


def prepare_seed_workspace(
    source_dir: Path,
    front_images_dir: Path,
    front_labels_dir: Path,
    workspace: Path,
    per_class: int = 10,
    seed: int = 42,
) -> list[SeedManifestRow]:
    source_dir = source_dir.resolve()
    if not source_dir.is_dir():
        raise FileNotFoundError(f"未找到未标注图像目录：{source_dir}")

    grouped: dict[str, list[Path]] = {name: [] for name in CLASS_NAMES}
    source_images = image_files(source_dir)
    for path in source_images:
        grouped[class_prefix_from_filename(path)].append(path)
    for name in CLASS_NAMES:
        if len(grouped[name]) < per_class:
            raise ValueError(f"{name} 类只有 {len(grouped[name])} 张图，至少需要 {per_class} 张")

    ensure_empty_destination(workspace)
    annotation_dir = workspace / "annotation_work"
    annotation_dir.mkdir()
    front_labels = _front_labels_by_original(front_images_dir, front_labels_dir)
    selected: dict[str, list[Path]] = {}

    reusable_cr_names = sorted(name for name in front_labels if name.startswith("Cr_") and (source_dir / name).exists())
    reusable_cr = [source_dir / name for name in reusable_cr_names[:per_class]]
    cr_needed = per_class - len(reusable_cr)
    remaining_cr = [path for path in grouped["Cr"] if path.name not in reusable_cr_names]
    selected["Cr"] = reusable_cr + stable_pick(remaining_cr, cr_needed, seed, "Cr-fill")
    for prefix in CLASS_NAMES[1:]:
        selected[prefix] = stable_pick(grouped[prefix], per_class, seed, prefix)

    manifest: list[SeedManifestRow] = []
    for prefix in CLASS_NAMES:
        for source_path in selected[prefix]:
            target_image = annotation_dir / source_path.name
            shutil.copy2(source_path, target_image)
            reused = source_path.name in front_labels
            if reused:
                boxes = read_yolo_file(front_labels[source_path.name])
                write_yolo_file(annotation_dir / f"{source_path.stem}.txt", boxes, class_id=0)
            manifest.append(
                SeedManifestRow(
                    filename=source_path.name,
                    prefix=prefix,
                    class_id=class_id_from_filename(source_path),
                    source_path=str(source_path),
                    reused_front_label=reused,
                    sha256=sha256_file(source_path),
                )
            )

    (annotation_dir / "classes.txt").write_text("defect\n", encoding="utf-8")
    _write_csv(workspace / "seed_manifest.csv", manifest)
    write_json(
        workspace / "source_snapshot.json",
        {
            "source_dir": str(source_dir),
            "image_count": len(source_images),
            "counts": {name: len(grouped[name]) for name in CLASS_NAMES},
            "sha256": {path.name: sha256_file(path) for path in source_images},
        },
    )
    return manifest


def audit_annotations(annotation_dir: Path) -> AuditReport:
    if not annotation_dir.is_dir():
        raise FileNotFoundError(f"未找到标注工作目录：{annotation_dir}")
    images = image_files(annotation_dir)
    issues: list[AuditIssue] = []
    image_counts: Counter[str] = Counter()
    box_counts: Counter[str] = Counter()
    label_count = 0
    box_count = 0

    for image_path in images:
        try:
            prefix = class_prefix_from_filename(image_path)
        except ValueError as exc:
            issues.append(AuditIssue("unknown_class", str(image_path), str(exc)))
            continue
        image_counts[prefix] += 1
        label_path = image_path.with_suffix(".txt")
        if not label_path.exists():
            issues.append(AuditIssue("missing_label", str(label_path), f"缺少标签：{label_path.name}"))
            continue
        label_count += 1
        lines = label_path.read_text(encoding="utf-8-sig").splitlines()
        nonempty = [(index, line) for index, line in enumerate(lines, start=1) if line.strip()]
        if not nonempty:
            issues.append(AuditIssue("empty_label", str(label_path), f"空标签：{label_path.name}"))
            continue
        for line_number, line in nonempty:
            try:
                box = parse_yolo_line(line, label_path, line_number)
                if box.class_id != 0:
                    raise ValueError("标注阶段只允许临时类别 0（defect）")
            except ValueError as exc:
                issues.append(AuditIssue("invalid_label", str(label_path), str(exc), line_number))
                continue
            box_count += 1
            box_counts[prefix] += 1

    image_stems = {path.stem for path in images}
    for label_path in sorted(annotation_dir.glob("*.txt")):
        if label_path.name == "classes.txt":
            continue
        if label_path.stem not in image_stems:
            issues.append(AuditIssue("orphan_label", str(label_path), f"标签没有对应图片：{label_path.name}"))

    return AuditReport(
        image_count=len(images),
        label_count=label_count,
        box_count=box_count,
        images_per_class={name: image_counts[name] for name in CLASS_NAMES},
        boxes_per_class={name: box_counts[name] for name in CLASS_NAMES},
        issues=issues,
    )


def verify_source_snapshot(snapshot_path: Path) -> dict:
    if not snapshot_path.is_file():
        raise FileNotFoundError(f"未找到原始数据快照：{snapshot_path}")
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    source_dir = Path(snapshot["source_dir"])
    if not source_dir.is_dir():
        raise FileNotFoundError(f"快照中的原始数据目录不存在：{source_dir}")
    expected: dict[str, str] = snapshot["sha256"]
    current_paths = {path.name: path for path in image_files(source_dir)}
    expected_names = set(expected)
    current_names = set(current_paths)
    missing = sorted(expected_names - current_names)
    extra = sorted(current_names - expected_names)
    changed = sorted(
        name for name in expected_names & current_names if sha256_file(current_paths[name]) != expected[name]
    )
    return {
        "ok": not missing and not extra and not changed,
        "source_dir": str(source_dir),
        "expected_count": len(expected),
        "current_count": len(current_paths),
        "missing": missing,
        "extra": extra,
        "changed": changed,
    }


def build_dataset(
    annotation_dir: Path,
    output_dir: Path,
    train_per_class: int = 8,
    seed: int = 42,
) -> list[SplitManifestRow]:
    report = audit_annotations(annotation_dir)
    if not report.ok:
        first = report.issues[0]
        raise ValueError(f"标签审计未通过，共 {len(report.issues)} 个问题；首个问题：{first.message}")

    grouped: dict[str, list[Path]] = defaultdict(list)
    for image_path in image_files(annotation_dir):
        grouped[class_prefix_from_filename(image_path)].append(image_path)
    for prefix in CLASS_NAMES:
        count = len(grouped[prefix])
        if count <= train_per_class:
            raise ValueError(f"{prefix} 类共有 {count} 张，训练取 {train_per_class} 张后没有验证图")

    ensure_empty_destination(output_dir)
    for split in ("train", "val"):
        (output_dir / "images" / split).mkdir(parents=True)
        (output_dir / "labels" / split).mkdir(parents=True)

    rows: list[SplitManifestRow] = []
    for prefix in CLASS_NAMES:
        ordered = stable_pick(grouped[prefix], len(grouped[prefix]), seed, f"split-{prefix}")
        assignments = [(path, "train" if index < train_per_class else "val") for index, path in enumerate(ordered)]
        target_class = class_id_from_filename(ordered[0])
        for image_path, split in assignments:
            shutil.copy2(image_path, output_dir / "images" / split / image_path.name)
            boxes = read_yolo_file(image_path.with_suffix(".txt"))
            write_yolo_file(output_dir / "labels" / split / f"{image_path.stem}.txt", boxes, target_class)
            rows.append(
                SplitManifestRow(
                    filename=image_path.name,
                    prefix=prefix,
                    class_id=target_class,
                    split=split,
                    sha256=sha256_file(image_path),
                )
            )

    yaml_lines = ["train: images/train", "val: images/val", "", "names:"]
    yaml_lines.extend(f"  {index}: {name}" for index, name in enumerate(CLASS_NAMES))
    (output_dir / "data.yaml").write_text("\n".join(yaml_lines) + "\n", encoding="utf-8")
    _write_csv(output_dir / "split_manifest.csv", rows)
    write_json(output_dir / "audit_report.json", report.to_dict())
    return rows
