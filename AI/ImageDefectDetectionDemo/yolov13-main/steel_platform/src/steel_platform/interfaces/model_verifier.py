from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="验证YOLO模型可加载性并读取类别元数据")
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.weights.is_file():
        parser.error(f"模型权重不存在：{args.weights}")
    from ultralytics import YOLO

    model = YOLO(str(args.weights))
    raw_names = model.names
    if isinstance(raw_names, dict):
        names = [str(raw_names[index]) for index in sorted(raw_names)]
    else:
        names = [str(name) for name in raw_names]
    metadata = {
        "schema_version": "steel-model-metadata-v1",
        "loadable": True,
        "format": args.weights.suffix.lower().lstrip("."),
        "task": getattr(model, "task", None),
        "class_names": names,
        "class_count": len(names),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(metadata, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, args.output)
    print(f"模型可加载，类别数量：{len(names)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
