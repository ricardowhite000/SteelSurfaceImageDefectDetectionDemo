from __future__ import annotations

import unittest
from pathlib import Path

from steel_tutorial.common import parse_yolo_line
from steel_tutorial.model_tools import detection_records, metrics_to_summary, pseudo_label_rows, validation_image_count


class FakeTensor:
    def __init__(self, values):
        self.values = values

    def cpu(self):
        return self

    def tolist(self):
        return self.values


class FakeBoxes:
    def __init__(self):
        self.cls = FakeTensor([1.0, 3.0])
        self.conf = FakeTensor([0.91, 0.31])
        self.xyxy = FakeTensor([[-2.0, 5.0, 205.0, 199.5], [10.0, 20.0, 30.0, 40.0]])
        self.xywhn = FakeTensor([[0.5, 0.5, 1.0, 0.9], [0.1, 0.2, 0.1, 0.1]])

    def __len__(self):
        return len(self.cls.values)


class FakeResult:
    path = "In_1.bmp"
    orig_shape = (200, 200)
    names = {0: "Cr", 1: "In", 2: "Pa", 3: "PS", 4: "RS", 5: "Sc"}
    boxes = FakeBoxes()


class FakeMetricBox:
    mp = 0.7
    mr = 0.8
    map50 = 0.75
    map = 0.55
    p = [0.6, 0.8]
    r = [0.7, 0.9]
    ap50 = [0.65, 0.85]
    maps = [0.45, 0.55, 0.55, 0.65, 0.55, 0.55]
    ap_class_index = [0, 3]


class FakeMetrics:
    box = FakeMetricBox()
    speed = {"preprocess": 1.0, "inference": 2.0, "postprocess": 0.5}


class ModelToolTests(unittest.TestCase):
    def test_detection_records_clamp_pixel_coordinates(self) -> None:
        rows = detection_records(FakeResult(), frame_index=4, time_seconds=0.2)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].source_file, "In_1.bmp")
        self.assertEqual(rows[0].class_name, "In")
        self.assertEqual((rows[0].x1, rows[0].y1, rows[0].x2, rows[0].y2), (0.0, 5.0, 200.0, 199.5))

    def test_metrics_summary_preserves_sparse_class_indices(self) -> None:
        summary = metrics_to_summary(FakeMetrics(), FakeResult.names, validation_count=72)
        self.assertEqual(summary["overall"]["mAP50"], 0.75)
        self.assertEqual([row["class_id"] for row in summary["per_class"]], [0, 3])
        self.assertEqual(summary["per_class"][1]["class_name"], "PS")
        self.assertEqual(summary["per_class"][1]["mAP50-95"], 0.65)
        self.assertEqual(summary["validation_image_count"], 72)
        self.assertIn("72", summary["warning"])

    def test_validation_count_is_read_from_dataset_yaml(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            val = root / "images" / "val"
            val.mkdir(parents=True)
            for index in range(5):
                (val / f"Cr_{index}.bmp").write_bytes(b"image")
            data = root / "data.yaml"
            data.write_text("path: .\nval: images/val\n", encoding="utf-8")
            self.assertEqual(validation_image_count(data), 5)

    def test_pseudo_labels_use_expected_filename_class_and_flag_mismatch(self) -> None:
        labels, review = pseudo_label_rows(FakeResult(), review_conf=0.4)
        self.assertTrue(all(line.startswith("1 ") for line in labels))
        self.assertEqual(labels[0], "1 0.500000 0.511250 1.000000 0.972500")
        for line_number, line in enumerate(labels, start=1):
            parse_yolo_line(line, Path(FakeResult.path), line_number)
        self.assertEqual(review["expected_class_id"], 1)
        self.assertEqual(review["status"], "class_mismatch;low_confidence")


if __name__ == "__main__":
    unittest.main()
