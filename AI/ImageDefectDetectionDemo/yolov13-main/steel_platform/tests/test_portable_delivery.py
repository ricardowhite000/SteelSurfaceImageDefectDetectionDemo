from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session
from typer.testing import CliRunner

from steel_platform.infrastructure.artifacts import LocalArtifactStore
from steel_platform.infrastructure.database import make_engine, upgrade_database
from steel_platform.infrastructure.config import load_settings
from steel_platform.infrastructure.models import AssetModel, ProjectModel, SourceRootModel
from steel_platform.infrastructure.runtime_profiles import RuntimeProfileStore
from steel_platform.interfaces.cli import app


def test_workspace_root_derives_machine_local_state_without_legacy_data_paths(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "配置 目录"
    config_dir.mkdir()
    workspace = tmp_path / "组员 工作区"
    (config_dir / "project.yaml").write_text(
        "project_name: portable-demo\nclasses: [Cr, In, Pa, PS, RS, Sc]\n",
        encoding="utf-8",
    )
    (config_dir / "machine.yaml").write_text(
        f'workspace_root: "{workspace.as_posix()}"\n'
        "host: 127.0.0.1\n"
        "port: 8765\n"
        "device: cpu\n",
        encoding="utf-8",
    )
    config = config_dir / "platform.yaml"
    config.write_text(
        "project_config: project.yaml\nmachine_config: machine.yaml\n",
        encoding="utf-8",
    )

    settings = load_settings(config)

    assert settings.workspace_root == workspace.resolve()
    assert settings.database_path == (workspace / "state/platform.db").resolve()
    assert settings.artifact_root == (workspace / "artifacts").resolve()
    assert settings.source_images is None
    assert settings.candidate_labels is None
    assert settings.review_csv is None
    assert settings.seed_manifest is None
    assert settings.seed_dataset is None


def test_runtime_registry_migrates_v1_and_upserts_by_logical_name(tmp_path: Path) -> None:
    path = tmp_path / "machine" / "runtime-profiles.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profiles": [
                    {
                        "id": "legacy-id",
                        "name": "本机YOLO",
                        "python_executable": "D:/env/python.exe",
                        "project_root": "D:/repo/yolov13-main",
                        "devices": ["0", "cpu"],
                        "created_at": "2026-07-20T00:00:00+00:00",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store = RuntimeProfileStore(path)

    migrated = store.list()
    updated = store.upsert(
        name="本机YOLO",
        python_executable="E:/env/python.exe",
        project_root="E:/repo/yolov13-main",
        devices=["cpu"],
        backend="cpu",
    )

    assert migrated[0]["backend"] == "cuda"
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 2
    assert updated["id"] == "legacy-id"
    assert updated["backend"] == "cpu"
    assert updated["devices"] == ["cpu"]
    assert store.list() == [updated]


def test_delivery_scripts_expose_cpu_cuda_and_workspace_inputs() -> None:
    root = Path(__file__).resolve().parents[1]
    bootstrap = (root / "scripts/bootstrap.ps1").read_text(encoding="utf-8-sig")
    configure = (root / "scripts/configure.ps1").read_text(encoding="utf-8-sig")
    doctor = (root / "scripts/doctor.ps1").read_text(encoding="utf-8-sig")
    start = (root / "scripts/start.ps1").read_text(encoding="utf-8-sig")

    assert "ValidateSet(\"cpu\", \"cuda\")" in bootstrap
    assert "yolo-runtime-cpu" in bootstrap
    assert "yolo-runtime-cuda" in bootstrap
    assert "steel_platform" in bootstrap
    assert "locks.sha256.json" in bootstrap
    assert "Get-FileHash" in bootstrap
    assert "Invoke-Native" in bootstrap
    assert "$LASTEXITCODE" in bootstrap
    assert "WorkspaceRoot" in configure
    assert "DemoPackage" in configure
    assert "workspace_root:" in configure
    assert "Resolve-CondaPython" in configure
    assert "Invoke-Native" in configure
    assert all("conda run" not in script for script in (bootstrap, configure, doctor, start))


def test_model_workbench_exposes_cpu_presets_and_runtime_recommendation() -> None:
    root = Path(__file__).resolve().parents[1]
    html = (root / "src/steel_platform/interfaces/static/index.html").read_text(encoding="utf-8")
    javascript = (root / "src/steel_platform/interfaces/static/js/model-workbench.js").read_text(encoding="utf-8")
    locale = (root / "src/steel_platform/interfaces/static/js/locale-zh.js").read_text(encoding="utf-8")

    assert 'value="smoke_cpu"' in html
    assert 'value="infer_cpu"' in html
    assert "applyRuntimeRecommendation" in javascript
    assert 'profile?.backend === "cpu"' in javascript
    assert 'smoke_cpu: "CPU冒烟训练"' in locale
    assert 'infer_cpu: "CPU常规推理"' in locale


def test_project_check_accepts_managed_demo_without_legacy_machine_paths(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "团队成员 工作区"
    config = tmp_path / "platform.portable.yaml"
    config.write_text(
        f'project_name: portable-demo\nworkspace_root: "{workspace.as_posix()}"\n'
        "classes: [Cr, In, Pa, PS, RS, Sc]\n"
        "device: cpu\n",
        encoding="utf-8",
    )
    settings = load_settings(config)
    upgrade_database(settings.database_url)
    store = LocalArtifactStore(settings.artifact_root)
    image = store.put_bytes(b"portable-image", media_type="image/bmp")
    with Session(make_engine(settings.database_url)) as session:
        project = ProjectModel(name="portable-demo", schema_version="steel-defects-v1")
        session.add(project)
        session.flush()
        source = SourceRootModel(
            project_id=project.id,
            name="标准Demo数据",
            kind="demo_package",
            mode="managed",
            path="package://steel-platform-demo-1.0.0/images",
            status="available",
            read_only=True,
        )
        session.add(source)
        session.flush()
        session.add(
            AssetModel(
                project_id=project.id,
                source_root_id=source.id,
                kind="image",
                relative_path="Cr_1.bmp",
                storage_key=image.storage_key,
                sha256=image.sha256,
                size_bytes=image.size_bytes,
                media_type=image.media_type,
            )
        )
        session.commit()

    result = CliRunner().invoke(
        app, ["project", "check", "--config", str(config)]
    )

    assert result.exit_code == 0, result.output
    assert "managed" in result.output
    assert "登记原图：1" in result.output
    assert "哈希异常：0" in result.output
