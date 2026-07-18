from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CliHelpTests(unittest.TestCase):
    def test_pseudo_label_uses_manifest_backed_small_batches(self) -> None:
        script = Path(__file__).resolve().parents[1] / "08_pseudo_label.py"
        source = script.read_text(encoding="utf-8")

        self.assertNotIn("source=[str(path) for path in remaining]", source)
        self.assertIn("source=str(source_manifest)", source)
        self.assertIn("batch=args.batch", source)

        completed = subprocess.run(
            [sys.executable, "-m", "steel_tutorial.08_pseudo_label", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--batch", completed.stdout)
        self.assertIn("--overwrite", completed.stdout)

    def test_all_tutorial_commands_offer_help_without_importing_torch(self) -> None:
        modules = [
            "01_check_environment",
            "02_prepare_seed",
            "03_audit_labels",
            "04_build_dataset",
            "05_train",
            "06_evaluate",
            "07_infer",
            "08_pseudo_label",
        ]
        for module in modules:
            with self.subTest(module=module):
                completed = subprocess.run(
                    [sys.executable, "-m", f"steel_tutorial.{module}", "--help"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertIn("usage:", completed.stdout.lower())

    def test_build_command_reports_audit_failure_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            annotation_dir = workspace / "annotation_work"
            annotation_dir.mkdir()
            annotation_dir.joinpath("Cr_1.bmp").write_bytes(b"BMfake")
            annotation_dir.joinpath("Cr_1.txt").write_text("", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "steel_tutorial.04_build_dataset",
                    "--workspace",
                    str(workspace),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("标签审计未通过", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)


if __name__ == "__main__":
    unittest.main()
