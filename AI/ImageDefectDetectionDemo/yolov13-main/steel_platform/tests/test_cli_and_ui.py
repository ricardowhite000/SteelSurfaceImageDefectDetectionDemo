from pathlib import Path
import re

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
    assert "钢材视觉平台" in response.text
    assert 'id="projectSelector"' in response.text
    assert "/static/js/main.js" in response.text


def test_browser_shell_has_module_separated_file_manager(tmp_path: Path) -> None:
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
    html = TestClient(create_app(settings)).get("/").text

    assert 'id="projectSelector"' in html
    assert 'id="resourceTree"' in html
    assert 'id="resourceContent"' in html
    assert 'id="assetDetailView"' in html
    assert 'id="importWizard"' in html
    assert "/static/js/main.js" in html
    assert "/static/app.js" not in html


def test_browser_shell_versions_entry_module_and_disables_stale_static_cache(tmp_path: Path) -> None:
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
    client = TestClient(create_app(settings))

    shell = client.get("/")
    entry_module = re.search(r'/static/js/main\.js\?v=([0-9a-f]{12})', shell.text)

    assert shell.status_code == 200
    assert entry_module is not None
    assert "__STATIC_VERSION__" not in shell.text
    assert shell.headers["cache-control"] == "no-store"
    assert client.get(entry_module.group(0)).headers["cache-control"] == "no-store"


def test_every_frontend_module_import_uses_the_same_content_version(tmp_path: Path) -> None:
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
    client = TestClient(create_app(settings))
    shell = client.get("/")
    match = re.search(r'/static/js/main\.js\?v=([0-9a-f]{12})', shell.text)
    assert match is not None
    version = match.group(1)

    pending = [f"/static/js/main.js?v={version}"]
    visited: set[str] = set()
    while pending:
        module_url = pending.pop()
        if module_url in visited:
            continue
        visited.add(module_url)
        response = client.get(module_url)
        assert response.status_code == 200
        assert "__STATIC_VERSION__" not in response.text
        for relative_import in re.findall(r'from\s+["\'](\./[^"\']+)["\']', response.text):
            assert relative_import.endswith(f"?v={version}")
            parent = module_url.split("?", 1)[0].rsplit("/", 1)[0]
            pending.append(f"{parent}/{relative_import[2:]}")

    assert len(visited) == 13


def test_favicon_request_is_harmless(tmp_path: Path) -> None:
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

    response = TestClient(create_app(settings)).get("/favicon.ico")

    assert response.status_code == 204


def test_primary_navigation_targets_real_project_resources(tmp_path: Path) -> None:
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
    client = TestClient(create_app(settings))

    html = client.get("/").text
    main_js = client.get("/static/js/main.js").text

    assert 'href="#data" data-node-target="sources"' in html
    assert 'href="#annotation"' in html
    assert 'href="#model"' in html
    assert 'href="#monitoring"' in html
    assert "activatePrimaryNavigation" in main_js


def test_model_workbench_exposes_manual_yolo_workflow(tmp_path: Path) -> None:
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
    client = TestClient(create_app(settings))

    html = client.get("/").text
    source = client.get("/static/js/model-workbench.js").text

    for element_id in (
        "modelWorkbench",
        "workbenchNewTraining",
        "workbenchNewInference",
        "workbenchJobCenter",
        "workbenchModelLibrary",
        "workbenchCommandPreview",
        "workbenchLog",
        "workbenchResults",
        "trainingRuntimeProfile",
        "inferenceRuntimeProfile",
    ):
        assert f'id="{element_id}"' in html
    assert "模型工作台" in html
    assert "/workbench/options" in source
    assert "/terminal-launch" in source
    assert "/ingest" in source
    assert "navigator.clipboard.writeText" in source
    assert "setInterval" in source
    assert "function escapeHtml" in source
    assert "renderLossChart" in source
    assert "file.download_url" in source
    assert 'download="${escapeHtml(file.download_name)}"' in source
    assert 'video/webm' in source
    assert "当前格式不能在浏览器内播放" in source
    assert "runtime_profile_id" in source
    assert "options.runtime_profiles" in source


def test_windows_delivery_scripts_use_utf8_bom_for_powershell_51() -> None:
    scripts = Path(__file__).parents[1] / "scripts"
    powershell_files = sorted(scripts.glob("*.ps1"))
    assert powershell_files
    for script in powershell_files:
        assert script.read_bytes().startswith(b"\xef\xbb\xbf"), script.name


def test_browser_folder_import_cannot_create_unresolvable_external_source(tmp_path: Path) -> None:
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
    client = TestClient(create_app(settings))

    html = client.get("/").text
    wizard_js = client.get("/static/js/import-wizard.js").text

    assert 'value="external" disabled' in html
    assert "外部模式需要服务端可访问的路径" in html
    assert 'mode: "managed"' in wizard_js
    assert "browser-selection" not in wizard_js


def test_import_wizard_rejects_invalid_selection_and_cleans_up_partial_session(tmp_path: Path) -> None:
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

    source = TestClient(create_app(settings)).get("/static/js/import-wizard.js").text

    assert "function selectionError" in source
    assert "counts.images === 0" in source
    assert "counts.errors" in source
    assert "counts.duplicates" in source
    assert '`/imports/${session.id}/cancel`' in source
    assert "wizard.committed" in source
    assert "result.asset_ids?.length" in source


def test_frontend_startup_and_navigation_failures_replace_loading_state(tmp_path: Path) -> None:
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

    client = TestClient(create_app(settings))
    shell = client.get("/").text
    source = client.get("/static/js/main.js").text

    assert "function showWorkspaceError" in source
    assert "项目加载失败" in source
    assert "selector.disabled = true" in source
    assert '$("openImport").disabled = true' in source
    assert "async function renderRoute" in source
    assert "window.__steelPlatformStarted = true" in source
    assert "钢材视觉平台前端未能启动" in shell
    assert "unhandledrejection" in shell


def test_config_path_accepts_common_package_name_typo(tmp_path: Path, monkeypatch) -> None:
    actual = tmp_path / "steel_platform" / "config" / "platform.local.yaml"
    actual.parent.mkdir(parents=True)
    actual.write_text("project_name: test\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    resolved = _resolve_config_path(Path("steel-platform/config/platform.local.yaml"))

    assert resolved == actual.resolve()
