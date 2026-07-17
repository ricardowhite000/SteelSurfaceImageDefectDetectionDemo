from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from steel_platform.infrastructure.config import PlatformSettings
from steel_platform.interfaces.api import create_app
from steel_platform.interfaces.cli import _resolve_config_path, app


def test_cli_exposes_platform_workflow() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("db", "project", "review", "dataset", "jobs", "runs", "inference", "backup", "artifacts", "serve"):
        assert command in result.stdout


def test_browser_shell_is_packaged(tmp_path: Path) -> None:
    settings = PlatformSettings(
        project_name="ui-test",
        database_url=f"sqlite:///{(tmp_path / 'db.sqlite').as_posix()}",
        artifact_root=tmp_path / "artifacts",
        source_images=tmp_path / "images",
        candidate_labels=tmp_path / "labels",
        review_csv=tmp_path / "review.csv",
        seed_manifest=tmp_path / "seed.csv",
        seed_dataset=tmp_path / "seed",
        classes=("Cr", "In", "Pa", "PS", "RS", "Sc"),
    )
    response = TestClient(create_app(settings)).get("/")
    assert response.status_code == 200
    assert "钢材表面异常视觉系统" in response.text
    assert "reviewCanvas" in response.text
    assert "/static/app.js" in response.text


def test_config_path_accepts_common_package_name_typo(tmp_path: Path, monkeypatch) -> None:
    actual = tmp_path / "steel_platform" / "config" / "platform.local.yaml"
    actual.parent.mkdir(parents=True)
    actual.write_text("project_name: test\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    resolved = _resolve_config_path(Path("steel-platform/config/platform.local.yaml"))

    assert resolved == actual.resolve()
