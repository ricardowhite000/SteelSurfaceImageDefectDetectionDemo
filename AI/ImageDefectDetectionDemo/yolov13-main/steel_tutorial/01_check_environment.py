from __future__ import annotations

import argparse
import platform
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 YOLOv13、PyTorch、CUDA 和预训练权重。")
    parser.add_argument("--weights", type=Path, default=PROJECT_ROOT / "yolov13n.pt", help="预训练权重路径")
    args = parser.parse_args()

    print(f"Python: {sys.version.split()[0]}")
    print(f"解释器: {sys.executable}")
    print(f"系统: {platform.platform()}")
    try:
        import torch
        import ultralytics
    except ImportError as exc:
        print(f"环境检查失败：{exc}", file=sys.stderr)
        print("请在 PyCharm 中切换到已经配置好的 yolov13 解释器后重试。", file=sys.stderr)
        return 1

    print(f"PyTorch: {torch.__version__}")
    print(f"Ultralytics: {ultralytics.__version__}")
    print(f"CUDA available: {torch.cuda.is_available()}")
    if not torch.cuda.is_available():
        print("环境检查失败：当前解释器无法使用 CUDA。", file=sys.stderr)
        return 1
    print(f"GPU: {torch.cuda.get_device_name(0)}")
    if not args.weights.is_file():
        print(f"环境检查失败：找不到权重 {args.weights}", file=sys.stderr)
        return 1
    print(f"权重: {args.weights.resolve()}")
    print("环境检查通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

