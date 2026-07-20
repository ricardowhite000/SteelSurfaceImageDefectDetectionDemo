import json
from pathlib import Path

from steel_platform.infrastructure.runtime_profiles import RuntimeProfileStore


def test_runtime_profiles_are_machine_local_and_replaceable(tmp_path: Path) -> None:
    store = RuntimeProfileStore(tmp_path / "machine" / "runtime-profiles.json")

    created = store.add(
        name="本机YOLO",
        python_executable=str(Path("D:/conda/envs/yolo/python.exe")),
        project_root=str(Path("D:/project/yolov13-main")),
        devices=["0", "cpu"],
    )

    assert created["name"] == "本机YOLO"
    assert store.list() == [created]
    assert json.loads(store.path.read_text(encoding="utf-8"))["profiles"][0][
        "python_executable"
    ] == created["python_executable"]


def test_runtime_check_reports_missing_python_without_running_it(tmp_path: Path) -> None:
    store = RuntimeProfileStore(tmp_path / "runtime-profiles.json")
    profile = store.add(
        name="无效环境",
        python_executable=str(tmp_path / "missing-python.exe"),
        project_root=str(tmp_path),
        devices=["cpu"],
    )

    result = store.check(profile["id"])

    assert result["available"] is False
    assert result["checks"]["python"] is False
