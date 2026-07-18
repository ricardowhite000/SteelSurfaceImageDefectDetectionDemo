from __future__ import annotations

import csv
from pathlib import Path
import sys
from types import ModuleType

from steel_platform.interfaces.inference_runner import main


def test_inference_runner_submits_exactly_one_path_per_predict_call(
    tmp_path: Path, monkeypatch
) -> None:
    sources = [tmp_path / f"Cr_{index}.bmp" for index in range(3)]
    source_list = tmp_path / "sources.txt"
    source_list.write_text("\n".join(str(path) for path in sources) + "\n", encoding="utf-8")
    weights = tmp_path / "model.pt"
    weights.write_bytes(b"fake")
    output = tmp_path / "output"
    calls: list[str] = []

    class Result:
        def __init__(self, path: str) -> None:
            self.path = path

    class FakeModel:
        def predict(self, *, source, batch, **_kwargs):
            assert batch == 1
            assert isinstance(source, str)
            calls.append(source)
            return [Result(source)]

    ultralytics = ModuleType("ultralytics")
    ultralytics.YOLO = lambda _weights: FakeModel()
    model_tools = ModuleType("steel_tutorial.model_tools")
    model_tools.pseudo_label_rows = lambda result, _threshold: (
        ["0 0.500000 0.500000 0.200000 0.200000"],
        {
            "filename": Path(result.path).name,
            "expected_class_id": 0,
            "predicted_class_ids": "0",
            "box_count": 1,
            "min_confidence": 0.8,
            "max_confidence": 0.8,
            "status": "ok",
        },
    )
    monkeypatch.setitem(sys.modules, "ultralytics", ultralytics)
    monkeypatch.setitem(sys.modules, "steel_tutorial.model_tools", model_tools)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "inference_runner",
            "--source-list",
            str(source_list),
            "--weights",
            str(weights),
            "--output",
            str(output),
            "--classes",
            "Cr",
            "In",
            "Pa",
            "PS",
            "RS",
            "Sc",
        ],
    )

    assert main() == 0

    assert calls == [str(path) for path in sources]
    with (output / "pseudo_review.csv").open(newline="", encoding="utf-8-sig") as stream:
        assert len(list(csv.DictReader(stream))) == 3
