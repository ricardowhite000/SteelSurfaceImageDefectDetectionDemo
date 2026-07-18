from __future__ import annotations

import argparse
from pathlib import Path

from .dataset_tools import prepare_seed_workspace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_ROOT = PROJECT_ROOT.parent
DATA_ROOT = DEMO_ROOT / "data"
DEFAULT_WORKSPACE = DATA_ROOT / "tutorial_workspace" / "steel_seed_v1"


def main() -> int:
    parser = argparse.ArgumentParser(description="从 NEU 数据中准备每类 10 张的标注种子集。")
    parser.add_argument("--source", type=Path, default=DATA_ROOT / "unmarked" / "NEU surface defect database")
    parser.add_argument("--front-images", type=Path, default=DATA_ROOT / "marked" / "front" / "images")
    parser.add_argument("--front-labels", type=Path, default=DATA_ROOT / "marked" / "front" / "labels")
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--per-class", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    manifest = prepare_seed_workspace(
        args.source, args.front_images, args.front_labels, args.workspace, args.per_class, args.seed
    )
    print(f"种子集已准备：{len(manifest)} 张")
    print(f"LabelImg 打开目录：{(args.workspace / 'annotation_work').resolve()}")
    print("下一步：逐张画框并保存，然后运行 03_audit_labels。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
