from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .dataset_tools import build_dataset

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKSPACE = PROJECT_ROOT.parent / "data" / "tutorial_workspace" / "steel_seed_v1"


def main() -> int:
    parser = argparse.ArgumentParser(description="将通过审计的60张标注构建为48/12的YOLO数据集。")
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--train-per-class", type=int, default=8)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    annotation_dir = args.workspace / "annotation_work"
    output_dir = args.workspace / "dataset"
    try:
        rows = build_dataset(annotation_dir, output_dir, args.train_per_class, args.seed)
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        print(f"构建失败：{exc}", file=sys.stderr)
        return 1
    train_count = sum(row.split == "train" for row in rows)
    val_count = sum(row.split == "val" for row in rows)
    print(f"数据集构建完成：train={train_count}，val={val_count}")
    print(f"配置文件：{(output_dir / 'data.yaml').resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
