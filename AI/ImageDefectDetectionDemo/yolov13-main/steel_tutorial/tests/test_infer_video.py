from pathlib import Path
from importlib import import_module

from steel_tutorial import infer_video


def test_browser_video_path_uses_webm_container(tmp_path: Path) -> None:
    source = tmp_path / "line sample.avi"

    result = infer_video.browser_video_path(tmp_path / "output", source)

    assert result == tmp_path / "output" / "line sample.webm"


def test_yolo_label_path_is_one_based_and_stable(tmp_path: Path) -> None:
    result = infer_video.video_label_path(tmp_path, "line sample", 0)

    assert result == tmp_path / "labels" / "line sample_1.txt"


def test_webm_input_is_treated_as_video() -> None:
    infer_cli = import_module("steel_tutorial.07_infer")

    assert ".webm" in infer_cli.VIDEO_SUFFIXES
