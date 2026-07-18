from __future__ import annotations

import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKSPACE = PROJECT_ROOT.parent / "data" / "tutorial_workspace" / "steel_seed_v1"


def main() -> int:
    parser = argparse.ArgumentParser(description="使用 yolov13n.pt 对六类钢板种子集进行迁移学习。")
    parser.add_argument("--data", type=Path, default=DEFAULT_WORKSPACE / "dataset" / "data.yaml")
    parser.add_argument("--weights", type=Path, default=PROJECT_ROOT / "yolov13n.pt")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--patience", type=int, default=20)
    parser.add_argument("--device", default="0")
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--project", type=Path, default=PROJECT_ROOT / "runs" / "steel_tutorial" / "train")
    parser.add_argument("--name", default="seed_v1")
    parser.add_argument("--smoke", action="store_true", help="只训练1轮，用于验证完整管线")
    parser.add_argument("--amp", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    if not args.data.is_file():
        parser.error(f"找不到数据配置：{args.data}")
    if not args.weights.is_file():
        parser.error(f"找不到预训练权重：{args.weights}")

    from ultralytics import YOLO

    epochs = 1 if args.smoke else args.epochs
    name = "seed_smoke" if args.smoke and args.name == "seed_v1" else args.name
    model = YOLO(str(args.weights))
    model.train(
        data=str(args.data.resolve()),
        epochs=epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        patience=args.patience,
        device=args.device,
        workers=args.workers,
        amp=args.amp,
        seed=args.seed,
        deterministic=True,
        cache=False,
        optimizer="auto",
        close_mosaic=min(10, max(0, epochs - 1)),
        project=str(args.project),
        name=name,
        exist_ok=True,
    )
    print(f"训练结束，结果位于：{args.project.resolve() / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
