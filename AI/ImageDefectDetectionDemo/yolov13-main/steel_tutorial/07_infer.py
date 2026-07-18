from __future__ import annotations

import argparse
import csv
from pathlib import Path

from .infer_video import browser_video_path, video_label_path, write_video_frame_labels
from .model_tools import DetectionRecord, detection_records

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VIDEO_SUFFIXES = {".avi", ".mp4", ".webm", ".mov", ".mkv", ".wmv", ".m4v"}


def main() -> int:
    parser = argparse.ArgumentParser(description="对单图、图片文件夹或视频执行YOLOv13钢板缺陷推理。")
    parser.add_argument("--source", required=True, help="图片、图片目录或视频路径")
    parser.add_argument(
        "--weights", type=Path, default=PROJECT_ROOT / "runs" / "steel_tutorial" / "train" / "seed_v1" / "weights" / "best.pt"
    )
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--device", default="0")
    parser.add_argument("--project", type=Path, default=PROJECT_ROOT / "runs" / "steel_tutorial" / "predict")
    parser.add_argument("--name", default="seed_v1")
    parser.add_argument("--save-crop", action="store_true")
    args = parser.parse_args()
    if not args.weights.is_file():
        parser.error(f"找不到模型权重：{args.weights}")

    from ultralytics import YOLO

    source_path = Path(args.source)
    is_video = source_path.is_file() and source_path.suffix.lower() in VIDEO_SUFFIXES
    fps = 0.0
    if is_video:
        import cv2

        capture = cv2.VideoCapture(str(source_path))
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        capture.release()

    output_dir = args.project / args.name
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "detections.csv"
    fieldnames = list(DetectionRecord.__dataclass_fields__)
    model = YOLO(str(args.weights))
    results = model.predict(
        source=args.source,
        conf=args.conf,
        imgsz=args.imgsz,
        device=args.device,
        stream=True,
        save=not is_video,
        save_txt=not is_video,
        save_conf=not is_video,
        save_crop=args.save_crop,
        project=str(args.project),
        name=args.name,
        exist_ok=True,
    )
    detection_count = 0
    video_writer = None
    video_path = browser_video_path(output_dir, source_path) if is_video else None
    try:
        with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for result_index, result in enumerate(results):
                frame_index = result_index if is_video else 0
                time_seconds = frame_index / fps if is_video and fps > 0 else 0.0
                rows = detection_records(result, frame_index, time_seconds)
                writer.writerows(row.to_dict() for row in rows)
                detection_count += len(rows)
                if is_video:
                    import cv2

                    annotated = result.plot()
                    if video_writer is None:
                        height, width = annotated.shape[:2]
                        video_writer = cv2.VideoWriter(
                            str(video_path),
                            cv2.VideoWriter_fourcc(*"VP80"),
                            fps if fps > 0 else 25.0,
                            (width, height),
                        )
                        if not video_writer.isOpened():
                            raise RuntimeError("无法创建 WebM 视频，请检查当前 OpenCV 的 VP8 编码支持")
                    video_writer.write(annotated)
                    write_video_frame_labels(
                        result,
                        video_label_path(output_dir, source_path.stem, frame_index),
                    )
    finally:
        if video_writer is not None:
            video_writer.release()
    print(f"推理完成，检测框数量：{detection_count}")
    print(f"结果目录：{output_dir.resolve()}")
    print(f"检测明细：{csv_path.resolve()}")
    if video_path is not None:
        print(f"浏览器视频：{video_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
