from __future__ import annotations

import argparse
import csv
import locale
from pathlib import Path
import tempfile

from .common import CLASS_NAMES, image_files
from .model_tools import pseudo_label_rows

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT_ROOT.parent / "data"
DEFAULT_WORKSPACE = DATA_ROOT / "tutorial_workspace" / "steel_seed_v1"


def main() -> int:
    parser = argparse.ArgumentParser(description="用种子模型为未入选图像生成待复核候选框。")
    parser.add_argument("--source", type=Path, default=DATA_ROOT / "unmarked" / "NEU surface defect database")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_WORKSPACE / "seed_manifest.csv")
    parser.add_argument(
        "--weights", type=Path, default=PROJECT_ROOT / "runs" / "steel_tutorial" / "train" / "seed_v1" / "weights" / "best.pt"
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_WORKSPACE / "pseudo_labels")
    parser.add_argument("--conf", type=float, default=0.20)
    parser.add_argument("--review-conf", type=float, default=0.40)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=1, help="每次送入GPU的图片数；8GB显存建议保持1。")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖本目录已有的机器候选标签；人工复核开始后不要使用。",
    )
    parser.add_argument("--device", default="0")
    args = parser.parse_args()
    if not args.source.is_dir() or not args.manifest.is_file() or not args.weights.is_file():
        parser.error("缺少未标注图像、seed_manifest.csv 或 best.pt。")
    if args.batch < 1:
        parser.error("--batch 必须大于等于1。")

    with args.manifest.open(newline="", encoding="utf-8-sig") as handle:
        seed_names = {row["filename"] for row in csv.DictReader(handle)}
    remaining = [path for path in image_files(args.source) if path.name not in seed_names]
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "classes.txt").write_text("\n".join(CLASS_NAMES) + "\n", encoding="utf-8")

    from ultralytics import YOLO

    model = YOLO(str(args.weights))
    review_rows = []
    written = 0
    with tempfile.TemporaryDirectory(prefix="pseudo_source_", dir=args.output) as temporary:
        source_manifest = Path(temporary) / "sources.txt"
        source_manifest.write_text(
            "\n".join(str(path.resolve()) for path in remaining) + "\n",
            encoding=locale.getpreferredencoding(False),
        )
        results = model.predict(
            source=str(source_manifest),
            batch=args.batch,
            conf=args.conf,
            imgsz=args.imgsz,
            device=args.device,
            stream=True,
            save=False,
            verbose=False,
        )
        for processed, result in enumerate(results, start=1):
            labels, review = pseudo_label_rows(result, args.review_conf)
            target = args.output / f"{Path(result.path).stem}.txt"
            if target.exists() and not args.overwrite:
                review["status"] = "existing_label_not_overwritten"
            elif labels:
                target.write_text("\n".join(labels) + "\n", encoding="utf-8")
                written += 1
            elif target.exists():
                target.unlink()
            review_rows.append(review)
            if processed % 100 == 0 or processed == len(remaining):
                print(f"辅助标注进度：{processed}/{len(remaining)}")

    review_path = args.output / "pseudo_review.csv"
    fieldnames = [
        "filename", "expected_class_id", "predicted_class_ids", "box_count", "min_confidence", "max_confidence", "status"
    ]
    with review_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(review_rows)
    print(f"候选图像：{len(remaining)}，已写候选标签：{written}")
    print(f"人工复核清单：{review_path.resolve()}")
    print("注意：候选标签不能直接当真值，必须逐张检查、移动和补框。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
