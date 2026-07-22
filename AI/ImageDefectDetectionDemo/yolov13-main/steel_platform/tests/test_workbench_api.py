from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from steel_platform.infrastructure.artifacts import LocalArtifactStore
from steel_platform.infrastructure.database import make_engine
from steel_platform.infrastructure.models import (
    AssetModel,
    ClassSchemaModel,
    DatasetVersionModel,
    ModelVersionModel,
    ProjectModel,
    new_id,
)
from steel_platform.infrastructure.runtime_profiles import RuntimeProfileStore
from steel_platform.infrastructure.workbench_executor import RecordingTerminalLauncher
from steel_platform.interfaces.api import create_app
from test_review_api import _prepared_workspace


def _workbench_workspace(tmp_path: Path):
    settings, project_id, _ = _prepared_workspace(tmp_path)
    store = LocalArtifactStore(settings.artifact_root)
    dataset_id = new_id()
    dataset_root = settings.artifact_root / "materialized" / "datasets" / dataset_id
    dataset_root.mkdir(parents=True)
    (dataset_root / "data.yaml").write_text(
        "train: images/train\nval: images/val\nnames: [Cr, In, Pa, PS, RS, Sc]\n",
        encoding="utf-8",
    )
    dataset_manifest = store.put_bytes(b'{"schema_version":"steel-defects-v1"}', media_type="application/json")
    weights = store.put_bytes(b"fake-pytorch-weights", media_type="application/octet-stream")
    model_id = new_id()
    with Session(make_engine(settings.database_url)) as session:
        session.add(
            DatasetVersionModel(
                id=dataset_id,
                project_id=project_id,
                name="training-dataset",
                schema_version="steel-defects-v1",
                manifest_key=dataset_manifest.storage_key,
                sha256=dataset_manifest.sha256,
            )
        )
        session.add(
            ModelVersionModel(
                id=model_id,
                project_id=project_id,
                name="parent-model",
                format="pt",
                purpose="detector",
                verification_status="ready",
                evaluation_status="not_evaluated",
                class_schema_json=["Cr", "In", "Pa", "PS", "RS", "Sc"],
                weights_sha256=weights.sha256,
                weights_key=weights.storage_key,
            )
        )
        session.commit()
    return settings, project_id, dataset_id, model_id


def test_runtime_profile_devices_override_machine_legacy_device(tmp_path: Path) -> None:
    settings, project_id, dataset_id, model_id = _workbench_workspace(tmp_path)
    settings = settings.model_copy(update={"device": "cpu"})
    registry = RuntimeProfileStore(
        settings.artifact_root / "machine" / "runtime-profiles.json"
    )
    cuda_profile = registry.add(
        name="团队NVIDIA运行环境",
        python_executable=str(tmp_path / "python.exe"),
        project_root=str(tmp_path),
        devices=["0"],
        backend="cuda",
    )
    client = TestClient(create_app(settings, terminal_launcher=RecordingTerminalLauncher()))

    created = client.post(
        f"/api/v1/projects/{project_id}/jobs",
        json={
            "name": "使用独立CUDA档案的冒烟训练",
            "kind": "train",
            "preset": "smoke",
            "runtime_profile_id": cuda_profile["id"],
            "input_refs": [
                {"role": "dataset", "ref_type": "dataset", "ref_id": dataset_id},
                {"role": "model", "ref_type": "model", "ref_id": model_id},
            ],
            "parameters": {"device": "0"},
        },
    )

    assert created.status_code == 201, created.text
    assert created.json()["runtime_profile_id"] == cuda_profile["id"]
    assert created.json()["parameters"]["device"] == "0"


def test_workbench_options_and_training_job_are_project_scoped(tmp_path: Path) -> None:
    settings, project_id, dataset_id, model_id = _workbench_workspace(tmp_path)
    launcher = RecordingTerminalLauncher()
    client = TestClient(create_app(settings, terminal_launcher=launcher))

    options = client.get(f"/api/v1/projects/{project_id}/workbench/options")
    assert options.status_code == 200
    assert [row["id"] for row in options.json()["datasets"]] == [dataset_id]
    assert [row["id"] for row in options.json()["models"]] == [model_id]

    created = client.post(
        f"/api/v1/projects/{project_id}/jobs",
        json={
            "name": "第一次冒烟训练",
            "kind": "train",
            "preset": "smoke",
            "input_refs": [
                {"role": "dataset", "ref_type": "dataset", "ref_id": dataset_id},
                {"role": "model", "ref_type": "model", "ref_id": model_id},
            ],
            "parameters": {"device": "0"},
        },
    )
    assert created.status_code == 201, created.text
    job = created.json()
    assert job["status"] == "draft"
    assert job["parameters"]["epochs"] == 1
    assert job["command"] is None

    prepared = client.post(
        f"/api/v1/projects/{project_id}/jobs/{job['id']}/prepare",
        json={"expected_revision": 0},
        headers={"Idempotency-Key": "prepare-training-once"},
    )
    assert prepared.status_code == 200, prepared.text
    assert prepared.json()["status"] == "ready"
    assert "05_train.py" in prepared.json()["command"]
    assert "model.pt" in prepared.json()["command"]
    materialized = (
        settings.artifact_root / "workbench" / "jobs" / job["id"] / "inputs" / "model.pt"
    )
    assert materialized.read_bytes() == b"fake-pytorch-weights"
    assert prepared.json()["revision"] == 1

    launch_url = f"/api/v1/projects/{project_id}/jobs/{job['id']}/terminal-launch"
    rejected_origin = client.post(
        launch_url,
        json={"expected_revision": 1},
        headers={"Idempotency-Key": "forged-origin", "Origin": "https://example.invalid"},
    )
    assert rejected_origin.status_code == 403
    assert not launcher.calls
    launched = client.post(
        launch_url,
        json={"expected_revision": 1},
        headers={"Idempotency-Key": "launch-training-once"},
    )
    repeated = client.post(
        launch_url,
        json={"expected_revision": 1},
        headers={"Idempotency-Key": "launch-training-once"},
    )
    assert launched.status_code == repeated.status_code == 200
    assert len(launcher.calls) == 1
    wrapper = launcher.calls[0][0]
    wrapper_text = wrapper.read_text(encoding="utf-8-sig")
    assert "Read-Host" in wrapper_text
    assert "job_worker" in wrapper_text

    log = client.get(f"/api/v1/projects/{project_id}/jobs/{job['id']}/log?after=0")
    assert log.status_code == 200
    assert log.json() == {"content": "", "next_offset": 0}
    results = client.get(f"/api/v1/projects/{project_id}/jobs/{job['id']}/results")
    assert results.status_code == 200
    assert results.json()["files"] == []

    missing_project = client.get(f"/api/v1/projects/not-this-project/jobs/{job['id']}")
    assert missing_project.status_code == 404


def test_training_job_uses_selected_machine_runtime_profile(tmp_path: Path) -> None:
    settings, project_id, dataset_id, model_id = _workbench_workspace(tmp_path)
    custom_root = tmp_path / "可迁移 YOLO 项目"
    custom_root.mkdir()
    custom_python = tmp_path / "环境" / "python.exe"
    custom_python.parent.mkdir()
    custom_python.write_bytes(b"")
    profile = RuntimeProfileStore(
        settings.artifact_root / "machine" / "runtime-profiles.json"
    ).add(
        name="组员电脑 YOLO",
        python_executable=str(custom_python),
        project_root=str(custom_root),
        devices=["cpu"],
    )
    client = TestClient(create_app(settings, terminal_launcher=RecordingTerminalLauncher()))

    options = client.get(f"/api/v1/projects/{project_id}/workbench/options")
    assert options.status_code == 200
    assert options.json()["runtime_profiles"] == [profile]

    created = client.post(
        f"/api/v1/projects/{project_id}/jobs",
        json={
            "name": "可迁移环境冒烟训练",
            "kind": "train",
            "preset": "smoke",
            "runtime_profile_id": profile["id"],
            "input_refs": [
                {"role": "dataset", "ref_type": "dataset", "ref_id": dataset_id},
                {"role": "model", "ref_type": "model", "ref_id": model_id},
            ],
            "parameters": {"device": "cpu"},
        },
    )
    assert created.status_code == 201, created.text
    job = created.json()
    assert job["runtime_profile_id"] == profile["id"]

    prepared = client.post(
        f"/api/v1/projects/{project_id}/jobs/{job['id']}/prepare",
        json={"expected_revision": job["revision"]},
        headers={"Idempotency-Key": "prepare-portable-runtime"},
    )
    assert prepared.status_code == 200, prepared.text
    runtime = prepared.json()["runtime"]
    assert runtime["arguments"][0] == str(custom_python)
    assert runtime["cwd"] == str(custom_root)
    assert prepared.json()["runtime_profile_id"] == profile["id"]


def test_job_rejects_missing_runtime_profile(tmp_path: Path) -> None:
    settings, project_id, dataset_id, model_id = _workbench_workspace(tmp_path)
    client = TestClient(create_app(settings, terminal_launcher=RecordingTerminalLauncher()))

    response = client.post(
        f"/api/v1/projects/{project_id}/jobs",
        json={
            "name": "不存在的运行环境",
            "kind": "train",
            "preset": "smoke",
            "runtime_profile_id": "missing-runtime-profile",
            "input_refs": [
                {"role": "dataset", "ref_type": "dataset", "ref_id": dataset_id},
                {"role": "model", "ref_type": "model", "ref_id": model_id},
            ],
            "parameters": {"device": "0"},
        },
    )
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_inference_job_uses_registered_model_and_source_with_batch_one(tmp_path: Path) -> None:
    settings, project_id, _, model_id = _workbench_workspace(tmp_path)
    client = TestClient(create_app(settings, terminal_launcher=RecordingTerminalLauncher()))
    options = client.get(f"/api/v1/projects/{project_id}/workbench/options").json()
    source_id = next(row["id"] for row in options["sources"] if row["kind"] == "images")

    created = client.post(
        f"/api/v1/projects/{project_id}/jobs",
        json={
            "name": "钢板图片推理",
            "kind": "infer",
            "preset": "visual",
            "input_refs": [
                {"role": "model", "ref_type": "model", "ref_id": model_id},
                {"role": "source", "ref_type": "source", "ref_id": source_id},
            ],
            "parameters": {"conf": 0.3, "device": "0"},
        },
    )
    assert created.status_code == 201, created.text
    job = created.json()
    assert job["parameters"]["batch"] == 1
    assert job["parameters"]["stream"] is True

    prepared = client.post(
        f"/api/v1/projects/{project_id}/jobs/{job['id']}/prepare",
        json={"expected_revision": 0},
        headers={"Idempotency-Key": "prepare-inference"},
    )
    assert prepared.status_code == 200, prepared.text
    assert "steel_tutorial.07_infer" in prepared.json()["command"]
    assert "--conf 0.3" in prepared.json()["command"]
    workspace = settings.artifact_root / "workbench" / "jobs" / job["id"]
    assert (workspace / "source-map.json").is_file()
    assert any((workspace / "inputs" / "source").iterdir())

    rejected_shell = client.post(
        f"/api/v1/projects/{project_id}/jobs",
        json={
            "name": "非法任务",
            "kind": "infer",
            "preset": "visual",
            "input_refs": [],
            "parameters": {},
            "command": "whoami",
        },
    )
    assert rejected_shell.status_code == 422


def test_inference_model_schema_uses_project_classes_not_global_settings(tmp_path: Path) -> None:
    settings, project_id, _, model_id = _workbench_workspace(tmp_path)
    with Session(make_engine(settings.database_url)) as session:
        project = session.get(ProjectModel, project_id)
        assert project is not None
        schema = ClassSchemaModel(
            id=new_id(), project_id=project_id, name="generic-defects", version=1,
            names_json=("scratch", "pit"),
        )
        session.add(schema)
        session.flush()
        project.class_schema_id = schema.id
        model = session.get(ModelVersionModel, model_id)
        assert model is not None
        model.class_schema_json = ["scratch", "pit"]
        session.commit()

    client = TestClient(create_app(settings, terminal_launcher=RecordingTerminalLauncher()))
    options = client.get(f"/api/v1/projects/{project_id}/workbench/options").json()
    source_id = next(row["id"] for row in options["sources"] if row["kind"] == "images")
    created = client.post(
        f"/api/v1/projects/{project_id}/jobs",
        json={
            "name": "通用项目推理",
            "kind": "infer",
            "preset": "visual",
            "input_refs": [
                {"role": "model", "ref_type": "model", "ref_id": model_id},
                {"role": "source", "ref_type": "source", "ref_id": source_id},
            ],
            "parameters": {"conf": 0.3, "device": "0"},
        },
    )

    assert created.status_code == 201, created.text


def test_external_pt_model_import_creates_a_verification_job(tmp_path: Path) -> None:
    settings, project_id, _, _ = _workbench_workspace(tmp_path)
    store = LocalArtifactStore(settings.artifact_root)
    weights = store.put_bytes(b"external-model", media_type="application/octet-stream")
    with Session(make_engine(settings.database_url)) as session:
        asset = AssetModel(
            project_id=project_id,
            kind="model_file",
            relative_path="imports/external-model.pt",
            storage_key=weights.storage_key,
            sha256=weights.sha256,
            size_bytes=weights.size_bytes,
            media_type=weights.media_type,
        )
        session.add(asset)
        session.commit()
        asset_id = asset.id

    client = TestClient(create_app(settings, terminal_launcher=RecordingTerminalLauncher()))
    imported = client.post(
        f"/api/v1/projects/{project_id}/model-imports",
        json={
            "name": "外部基础权重",
            "weights_asset_id": asset_id,
            "format": "pt",
            "purpose": "base_weight",
            "class_names": None,
            "source_note": "由合作方提供",
        },
    )
    assert imported.status_code == 201, imported.text
    body = imported.json()
    assert body["model"]["verification_status"] == "pending"
    assert body["model"]["weights_sha256"] == weights.sha256
    assert body["verification_job_id"]
    verification = client.get(
        f"/api/v1/projects/{project_id}/jobs/{body['verification_job_id']}"
    )
    assert verification.status_code == 200
    assert verification.json()["kind"] == "verify_model"
    prepared = client.post(
        f"/api/v1/projects/{project_id}/jobs/{body['verification_job_id']}/prepare",
        json={"expected_revision": 0},
        headers={"Idempotency-Key": "prepare-model-verification"},
    )
    assert prepared.status_code == 200, prepared.text
    assert "model_verifier" in prepared.json()["command"]


def test_browser_can_register_only_supported_model_files(tmp_path: Path) -> None:
    settings, project_id, _, _ = _workbench_workspace(tmp_path)
    client = TestClient(create_app(settings, terminal_launcher=RecordingTerminalLauncher()))

    uploaded = client.post(
        f"/api/v1/projects/{project_id}/model-files",
        content=b"external-pt-weights",
        headers={"X-Filename": "steel detector.pt", "Content-Type": "application/octet-stream"},
    )
    assert uploaded.status_code == 201, uploaded.text
    asset = uploaded.json()
    assert asset["name"] == "steel detector.pt"
    assert asset["sha256"]
    imported = client.post(
        f"/api/v1/projects/{project_id}/model-imports",
        json={
            "name": "浏览器导入权重",
            "weights_asset_id": asset["asset_id"],
            "format": "pt",
            "purpose": "base_weight",
            "class_names": None,
            "source_note": "人工上传",
        },
    )
    assert imported.status_code == 201
    rejected = client.post(
        f"/api/v1/projects/{project_id}/model-files",
        content=b"not-a-model",
        headers={"X-Filename": "unsafe.exe", "Content-Type": "application/octet-stream"},
    )
    assert rejected.status_code == 422


def test_browser_can_stage_an_inference_image_or_video(tmp_path: Path) -> None:
    settings, project_id, _, model_id = _workbench_workspace(tmp_path)
    client = TestClient(create_app(settings, terminal_launcher=RecordingTerminalLauncher()))

    uploaded = client.post(
        f"/api/v1/projects/{project_id}/inference-files",
        content=b"short-demo-video",
        headers={"X-Filename": "line sample.mp4", "Content-Type": "video/mp4"},
    )
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["asset_id"]
    assert uploaded.json()["media_type"] == "video/mp4"
    job = client.post(
        f"/api/v1/projects/{project_id}/jobs",
        json={
            "name": "视频流式推理",
            "kind": "infer",
            "preset": "video",
            "input_refs": [
                {"role": "model", "ref_type": "model", "ref_id": model_id},
                {"role": "source", "ref_type": "asset", "ref_id": uploaded.json()["asset_id"]},
            ],
            "parameters": {},
        },
    ).json()
    prepared = client.post(
        f"/api/v1/projects/{project_id}/jobs/{job['id']}/prepare",
        json={"expected_revision": job["revision"]},
        headers={"Idempotency-Key": "prepare-video-upload"},
    )
    assert prepared.status_code == 200, prepared.text
    assert ".mp4" in prepared.json()["command"]
    webm = client.post(
        f"/api/v1/projects/{project_id}/inference-files",
        content=b"browser-video",
        headers={"X-Filename": "browser result.webm", "Content-Type": "video/webm"},
    )
    assert webm.status_code == 201, webm.text
    assert webm.json()["media_type"] == "video/webm"
    rejected = client.post(
        f"/api/v1/projects/{project_id}/inference-files",
        content=b"bad",
        headers={"X-Filename": "notes.txt", "Content-Type": "text/plain"},
    )
    assert rejected.status_code == 422


def test_evaluation_job_uses_registered_dataset_and_detector(tmp_path: Path) -> None:
    settings, project_id, dataset_id, model_id = _workbench_workspace(tmp_path)
    client = TestClient(create_app(settings, terminal_launcher=RecordingTerminalLauncher()))
    created = client.post(
        f"/api/v1/projects/{project_id}/jobs",
        json={
            "name": "固定验证集评估",
            "kind": "evaluate",
            "preset": "fixed_val",
            "input_refs": [
                {"role": "dataset", "ref_type": "dataset", "ref_id": dataset_id},
                {"role": "model", "ref_type": "model", "ref_id": model_id},
            ],
            "parameters": {"device": "0"},
        },
    )
    assert created.status_code == 201, created.text
    prepared = client.post(
        f"/api/v1/projects/{project_id}/jobs/{created.json()['id']}/prepare",
        json={"expected_revision": 0},
        headers={"Idempotency-Key": "prepare-evaluation"},
    )
    assert prepared.status_code == 200, prepared.text
    assert "steel_tutorial.06_evaluate" in prepared.json()["command"]


def test_draft_job_update_uses_optimistic_revision_and_rejects_stale_write(tmp_path: Path) -> None:
    settings, project_id, dataset_id, model_id = _workbench_workspace(tmp_path)
    client = TestClient(create_app(settings, terminal_launcher=RecordingTerminalLauncher()))
    created = client.post(
        f"/api/v1/projects/{project_id}/jobs",
        json={
            "name": "待调整训练",
            "kind": "train",
            "preset": "formal",
            "input_refs": [
                {"role": "dataset", "ref_type": "dataset", "ref_id": dataset_id},
                {"role": "model", "ref_type": "model", "ref_id": model_id},
            ],
            "parameters": {},
        },
    ).json()
    url = f"/api/v1/projects/{project_id}/jobs/{created['id']}"
    updated = client.put(
        url,
        json={"expected_revision": 0, "name": "二十轮训练", "parameters": {"epochs": 20}},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["revision"] == 1
    assert updated.json()["parameters"]["epochs"] == 20

    stale = client.put(
        url,
        json={"expected_revision": 0, "name": "覆盖任务", "parameters": {"epochs": 30}},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "revision_conflict"

    cancelled = client.post(
        f"{url}/cancel",
        json={"expected_revision": 1},
        headers={"Idempotency-Key": "cancel-draft-once"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_manual_ingest_registers_existing_outputs_once(tmp_path: Path) -> None:
    settings, project_id, dataset_id, model_id = _workbench_workspace(tmp_path)
    client = TestClient(create_app(settings, terminal_launcher=RecordingTerminalLauncher()))
    created = client.post(
        f"/api/v1/projects/{project_id}/jobs",
        json={
            "name": "人工导入训练结果",
            "kind": "train",
            "preset": "smoke",
            "input_refs": [
                {"role": "dataset", "ref_type": "dataset", "ref_id": dataset_id},
                {"role": "model", "ref_type": "model", "ref_id": model_id},
            ],
            "parameters": {},
        },
    ).json()
    prepared = client.post(
        f"/api/v1/projects/{project_id}/jobs/{created['id']}/prepare",
        headers={"Idempotency-Key": "prepare-manual-ingest"},
        json={"expected_revision": created["revision"]},
    ).json()
    output_dir = Path(prepared["runtime"]["output_dir"])
    (output_dir / "weights").mkdir(parents=True, exist_ok=True)
    (output_dir / "weights" / "best.pt").write_bytes(b"best")
    (output_dir / "weights" / "last.pt").write_bytes(b"last")
    (output_dir / "results.csv").write_text(
        "epoch,metric\n1,0.5\n", encoding="utf-8"
    )

    first = client.post(
        f"/api/v1/projects/{project_id}/jobs/{created['id']}/ingest",
        headers={"Idempotency-Key": "ingest-manual"},
        json={"expected_revision": prepared["revision"]},
    )
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "succeeded"
    repeated = client.post(
        f"/api/v1/projects/{project_id}/jobs/{created['id']}/ingest",
        headers={"Idempotency-Key": "ingest-manual"},
        json={"expected_revision": prepared["revision"]},
    )
    assert repeated.status_code == 200
    assert repeated.json()["result_manifest_key"] == first.json()["result_manifest_key"]

    results = client.get(
        f"/api/v1/projects/{project_id}/jobs/{created['id']}/results"
    )
    assert results.status_code == 200
    files = {item["relative_path"]: item for item in results.json()["files"]}
    csv_result = files["results.csv"]
    assert csv_result["download_name"] == "results.csv"
    assert csv_result["content_url"].endswith(
        f"/assets/{csv_result['asset_id']}/content"
    )
    assert csv_result["download_url"].endswith(
        f"/assets/{csv_result['asset_id']}/content?download=1"
    )
