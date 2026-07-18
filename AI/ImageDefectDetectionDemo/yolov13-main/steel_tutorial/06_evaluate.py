from __future__ import annotations

import argparse
from pathlib import Path

from .common import write_json
from .model_tools import metrics_to_summary, validation_image_count

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKSPACE = PROJECT_ROOT.parent / "data" / "tutorial_workspace" / "steel_seed_v1"


def main() -> int:
    parser = argparse.ArgumentParser(description="评估六类种子模型并输出 metrics_summary.json。")
    parser.add_argument("--data", type=Path, default=DEFAULT_WORKSPACE / "dataset" / "data.yaml")
    parser.add_argument(
        "--weights", type=Path, default=PROJECT_ROOT / "runs" / "steel_tutorial" / "train" / "seed_v1" / "weights" / "best.pt"
    )
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--device", default="0")
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--project", type=Path, default=PROJECT_ROOT / "runs" / "steel_tutorial" / "val")
    parser.add_argument("--name", default="seed_v1")
    args = parser.parse_args()
    if not args.data.is_file() or not args.weights.is_file():
        parser.error("找不到 data.yaml 或 best.pt，请先完成数据构建与训练。")

    from ultralytics import YOLO

    model = YOLO(str(args.weights))
    metrics = model.val(
        data=str(args.data.resolve()),
        split="val",
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        workers=args.workers,
        plots=True,
        project=str(args.project),
        name=args.name,
        exist_ok=True,
    )
    output = Path(metrics.save_dir) / "metrics_summary.json"
    validation_count = validation_image_count(args.data)
    summary = metrics_to_summary(metrics, model.names, validation_count=validation_count)
    write_json(output, summary)
    print(summary["warning"])
    print(f"指标摘要：{output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
