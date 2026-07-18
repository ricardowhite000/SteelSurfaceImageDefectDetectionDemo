from __future__ import annotations

import csv
import hashlib
import tempfile
import unittest
from pathlib import Path

from steel_tutorial.common import CLASS_NAMES, YoloBox, class_id_from_filename, parse_yolo_line, stable_pick
from steel_tutorial.dataset_tools import (
    audit_annotations,
    build_dataset,
    prepare_seed_workspace,
    verify_source_snapshot,
)


def write_fake_bmp(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"BM" + payload)


class CommonTests(unittest.TestCase):
    def test_class_mapping_uses_neu_prefix_order(self) -> None:
        self.assertEqual(CLASS_NAMES, ("Cr", "In", "Pa", "PS", "RS", "Sc"))
        self.assertEqual(class_id_from_filename("Cr_1.bmp"), 0)
        self.assertEqual(class_id_from_filename("PS_99.bmp"), 3)
        self.assertEqual(class_id_from_filename(Path("Sc_300.txt")), 5)
        with self.assertRaisesRegex(ValueError, "无法从文件名识别类别"):
            class_id_from_filename("unknown.bmp")

    def test_stable_pick_is_reproducible_and_order_independent(self) -> None:
        paths = [Path(f"In_{i}.bmp") for i in range(1, 21)]
        first = stable_pick(paths, count=5, seed=42, namespace="In")
        second = stable_pick(reversed(paths), count=5, seed=42, namespace="In")
        self.assertEqual(first, second)
        self.assertEqual(len(first), 5)

    def test_parse_yolo_line_accepts_normalized_box(self) -> None:
        box = parse_yolo_line("0 0.5 0.4 0.2 0.1", Path("Cr_1.txt"), 1)
        self.assertEqual(box, YoloBox(0, 0.5, 0.4, 0.2, 0.1))

    def test_parse_yolo_line_rejects_invalid_geometry(self) -> None:
        with self.assertRaisesRegex(ValueError, "宽高必须大于 0"):
            parse_yolo_line("0 0.5 0.5 0 0.2", Path("Cr_1.txt"), 1)
        with self.assertRaisesRegex(ValueError, "边界框超出图像范围"):
            parse_yolo_line("0 0.99 0.5 0.2 0.2", Path("Cr_1.txt"), 1)


class DatasetWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "source"
        self.front_images = self.root / "front" / "images"
        self.front_labels = self.root / "front" / "labels"
        for prefix in CLASS_NAMES:
            for index in range(1, 4):
                write_fake_bmp(self.source / f"{prefix}_{index}.bmp", f"{prefix}-{index}".encode())

        write_fake_bmp(self.front_images / "000000_Cr_1.bmp", b"front-copy")
        self.front_labels.mkdir(parents=True)
        self.front_labels.joinpath("000000_Cr_1.txt").write_text(
            "3 0.5 0.5 0.2 0.2\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_prepare_seed_reuses_front_cr_and_preserves_source(self) -> None:
        source_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.source.glob("*.bmp")}
        workspace = self.root / "workspace"

        manifest = prepare_seed_workspace(
            source_dir=self.source,
            front_images_dir=self.front_images,
            front_labels_dir=self.front_labels,
            workspace=workspace,
            per_class=2,
            seed=42,
        )

        self.assertEqual(len(manifest), 12)
        self.assertEqual(sum(row.prefix == "Cr" for row in manifest), 2)
        self.assertTrue((workspace / "annotation_work" / "Cr_1.bmp").exists())
        self.assertEqual(
            (workspace / "annotation_work" / "Cr_1.txt").read_text(encoding="utf-8"),
            "0 0.500000 0.500000 0.200000 0.200000\n",
        )
        self.assertEqual(
            source_hashes,
            {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.source.glob("*.bmp")},
        )

    def test_source_snapshot_detects_later_modification(self) -> None:
        workspace = self.root / "workspace"
        prepare_seed_workspace(
            source_dir=self.source,
            front_images_dir=self.front_images,
            front_labels_dir=self.front_labels,
            workspace=workspace,
            per_class=2,
            seed=42,
        )
        snapshot = workspace / "source_snapshot.json"
        self.assertTrue(verify_source_snapshot(snapshot)["ok"])

        self.source.joinpath("In_1.bmp").write_bytes(b"changed")
        verification = verify_source_snapshot(snapshot)
        self.assertFalse(verification["ok"])
        self.assertEqual(verification["changed"], ["In_1.bmp"])

    def test_audit_reports_missing_and_empty_labels(self) -> None:
        annotation_dir = self.root / "annotations"
        write_fake_bmp(annotation_dir / "Cr_1.bmp", b"1")
        write_fake_bmp(annotation_dir / "In_1.bmp", b"2")
        annotation_dir.joinpath("Cr_1.txt").write_text("", encoding="utf-8")

        report = audit_annotations(annotation_dir)

        self.assertFalse(report.ok)
        self.assertEqual(report.image_count, 2)
        self.assertTrue(any("空标签" in issue.message for issue in report.issues))
        self.assertTrue(any("缺少标签" in issue.message for issue in report.issues))

    def test_build_dataset_creates_balanced_split_and_rewrites_classes(self) -> None:
        annotation_dir = self.root / "annotations"
        for prefix in CLASS_NAMES:
            for index in range(1, 4):
                write_fake_bmp(annotation_dir / f"{prefix}_{index}.bmp", f"{prefix}-{index}".encode())
                annotation_dir.joinpath(f"{prefix}_{index}.txt").write_text(
                    "0 0.5 0.5 0.2 0.2\n", encoding="utf-8"
                )

        output = self.root / "dataset"
        rows = build_dataset(annotation_dir, output, train_per_class=2, seed=42)

        self.assertEqual(len(rows), 18)
        self.assertEqual(len(list((output / "images" / "train").glob("*.bmp"))), 12)
        self.assertEqual(len(list((output / "images" / "val").glob("*.bmp"))), 6)
        self.assertIn("train: images/train", (output / "data.yaml").read_text(encoding="utf-8"))
        for label_path in (output / "labels").rglob("*.txt"):
            expected = class_id_from_filename(label_path)
            for line in label_path.read_text(encoding="utf-8").splitlines():
                self.assertEqual(int(line.split()[0]), expected)

        with (output / "split_manifest.csv").open(newline="", encoding="utf-8-sig") as handle:
            csv_rows = list(csv.DictReader(handle))
        self.assertEqual(len(csv_rows), 18)


if __name__ == "__main__":
    unittest.main()
