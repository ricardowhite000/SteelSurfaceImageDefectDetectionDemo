from __future__ import annotations

import argparse
from pathlib import Path

from .common import read_yolo_file, stable_pick, write_json
from .dataset_tools import audit_annotations, verify_source_snapshot

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKSPACE = PROJECT_ROOT.parent / "data" / "tutorial_workspace" / "steel_seed_v1"


def main() -> int:
    parser = argparse.ArgumentParser(description="审计 LabelImg 生成的 YOLO 标签并绘制抽查预览。")
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--preview-count", type=int, default=12)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    annotation_dir = args.workspace / "annotation_work"
    report = audit_annotations(annotation_dir)
    source_integrity = verify_source_snapshot(args.workspace / "source_snapshot.json")
    report_path = args.workspace / "reports" / "audit_report.json"
    report_data = report.to_dict()
    report_data["source_integrity"] = source_integrity
    write_json(report_path, report_data)

    if args.preview_count > 0:
        try:
            from PIL import Image, ImageDraw

            images = [path for path in annotation_dir.iterdir() if path.suffix.lower() == ".bmp"]
            chosen = stable_pick(images, min(args.preview_count, len(images)), args.seed, "preview")
            preview_dir = args.workspace / "reports" / "label_preview"
            preview_dir.mkdir(parents=True, exist_ok=True)
            for image_path in chosen:
                label_path = image_path.with_suffix(".txt")
                if not label_path.exists() or not label_path.read_text(encoding="utf-8-sig").strip():
                    continue
                image = Image.open(image_path).convert("RGB")
                draw = ImageDraw.Draw(image)
                for box in read_yolo_file(label_path):
                    width, height = image.size
                    x1 = (box.x_center - box.width / 2) * width
                    y1 = (box.y_center - box.height / 2) * height
                    x2 = (box.x_center + box.width / 2) * width
                    y2 = (box.y_center + box.height / 2) * height
                    draw.rectangle((x1, y1, x2, y2), outline="red", width=2)
                image.save(preview_dir / f"{image_path.stem}.jpg", quality=95)
        except ImportError:
            print("提示：当前环境没有 Pillow，已跳过标签预览；标签数值审计不受影响。")

    print(f"图片: {report.image_count}，标签: {report.label_count}，框: {report.box_count}")
    print(f"原始数据完整性: {'通过' if source_integrity['ok'] else '失败'}")
    print(f"审计报告: {report_path.resolve()}")
    if not source_integrity["ok"]:
        print("原始数据快照不一致，已停止。请检查 missing/extra/changed 列表。")
        return 1
    if not report.ok:
        print(f"审计未通过，共 {len(report.issues)} 个问题：")
        for issue in report.issues[:20]:
            print(f"- {issue.message}")
        return 1
    print("标签审计通过，可以构建训练数据集。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
