from __future__ import annotations

from pathlib import Path
import sys

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from steel_platform.infrastructure.database import make_engine
from steel_platform.infrastructure.models import (
    AssetModel,
    CandidatePredictionModel,
    ExperimentRunModel,
    InferenceRunModel,
    JobLineageRefModel,
    JobModel,
    ModelVersionModel,
    ProjectModel,
    SourceRootModel,
)
from steel_platform.infrastructure.workbench_executor import (
    RecordingTerminalLauncher,
    execute_job,
)
from test_review_api import _prepared_workspace
from test_workbench_api import _workbench_workspace


def test_job_worker_runs_argument_array_and_persists_utf8_log(tmp_path: Path) -> None:
    settings, project_id, _ = _prepared_workspace(tmp_path)
    workspace_key = "workbench/jobs/worker-test"
    workspace = settings.artifact_root / workspace_key
    workspace.mkdir(parents=True)
    with Session(make_engine(settings.database_url)) as session:
        job = JobModel(
            id="worker-test",
            project_id=project_id,
            name="执行器测试",
            kind="train",
            preset="smoke",
            status="ready",
            workspace_key=workspace_key,
            log_key=f"{workspace_key}/job.log",
            spec_json={
                "parameters": {"device": "cpu", "epochs": 1},
                "runtime": {
                    "arguments": [sys.executable, "-c", "print('中文日志 ok')"],
                    "cwd": str(tmp_path),
                    "output_dir": str(workspace / "output"),
                    "expected_outputs": [],
                },
            },
        )
        session.add(job)
        session.commit()

    assert execute_job(settings, "worker-test") == "succeeded"

    with Session(make_engine(settings.database_url)) as session:
        completed = session.get(JobModel, "worker-test")
        assert completed.status == "succeeded"
        assert completed.exit_code == 0
        assert completed.started_at is not None
        assert completed.finished_at is not None
    assert "中文日志 ok" in (workspace / "job.log").read_text(encoding="utf-8")


def test_recording_terminal_launcher_receives_wrapper_without_executing_it(tmp_path: Path) -> None:
    launcher = RecordingTerminalLauncher()
    wrapper = tmp_path / "launch.ps1"
    wrapper.write_text("Read-Host '确认'", encoding="utf-8")

    launcher.launch(wrapper, working_directory=tmp_path)

    assert launcher.calls == [(wrapper.resolve(), tmp_path.resolve())]


def test_successful_worker_registers_immutable_output_manifest(tmp_path: Path) -> None:
    settings, project_id, _ = _prepared_workspace(tmp_path)
    workspace_key = "workbench/jobs/output-test"
    workspace = settings.artifact_root / workspace_key
    output = workspace / "output"
    output.mkdir(parents=True)
    script = "from pathlib import Path; Path(r'{}').write_text('epoch,metric\\n1,0.5\\n', encoding='utf-8')".format(
        str(output / "results.csv").replace("\\", "\\\\")
    )
    with Session(make_engine(settings.database_url)) as session:
        session.add(
            JobModel(
                id="output-test",
                project_id=project_id,
                name="输出登记测试",
                kind="train",
                preset="smoke",
                status="ready",
                workspace_key=workspace_key,
                log_key=f"{workspace_key}/job.log",
                spec_json={
                    "parameters": {"device": "cpu", "epochs": 1},
                    "runtime": {
                        "arguments": [sys.executable, "-c", script],
                        "cwd": str(tmp_path),
                        "output_dir": str(output),
                        "expected_outputs": ["results.csv"],
                    },
                },
            )
        )
        session.commit()

    assert execute_job(settings, "output-test") == "succeeded"

    with Session(make_engine(settings.database_url)) as session:
        job = session.get(JobModel, "output-test")
        assert job.result_manifest_key is not None
        assert session.scalar(
            select(func.count()).select_from(JobLineageRefModel).where(
                JobLineageRefModel.job_id == job.id,
                JobLineageRefModel.direction == "output",
            )
        ) == 1
        output_asset = session.scalar(select(AssetModel).where(AssetModel.kind == "job_output"))
        assert output_asset is not None
        assert output_asset.sha256


def test_formal_training_output_registers_experiment_and_child_model(tmp_path: Path) -> None:
    settings, project_id, dataset_id, parent_model_id = _workbench_workspace(tmp_path)
    from steel_platform.infrastructure.artifacts import LocalArtifactStore
    from steel_platform.infrastructure.workbench import SqlWorkbenchGateway

    gateway = SqlWorkbenchGateway(settings, LocalArtifactStore(settings.artifact_root))
    from steel_platform.domain.workbench import JobInputRef, JobKind, WorkbenchJobSpec

    created = gateway.create_job(
        project_id,
        "正式训练登记测试",
        WorkbenchJobSpec.create(
            kind=JobKind.TRAIN,
            preset="formal",
            input_refs=(
                JobInputRef("dataset", dataset_id, "dataset"),
                JobInputRef("model", parent_model_id, "model"),
            ),
            parameters={"epochs": 2, "device": "cpu"},
            allowed_devices=("cpu",),
        ),
    )
    prepared = gateway.prepare_job(
        project_id,
        created["id"],
        expected_revision=0,
        idempotency_key="prepare-formal-registration",
    )
    with Session(make_engine(settings.database_url)) as session:
        job = session.get(JobModel, prepared["id"])
        output = Path(job.spec_json["runtime"]["output_dir"])
        script = (
            "from pathlib import Path; "
            f"p=Path(r'{str(output).replace(chr(92), chr(92) * 2)}'); "
            "(p/'weights').mkdir(parents=True, exist_ok=True); "
            "(p/'weights'/'best.pt').write_bytes(b'best'); "
            "(p/'weights'/'last.pt').write_bytes(b'last'); "
            "(p/'results.csv').write_text('epoch,metric\\n1,0.5\\n', encoding='utf-8')"
        )
        job.spec_json = {
            **job.spec_json,
            "runtime": {
                **job.spec_json["runtime"],
                "arguments": [sys.executable, "-c", script],
            },
            "parameters": {**job.spec_json["parameters"], "device": "cpu"},
        }
        session.commit()

    assert execute_job(settings, prepared["id"]) == "succeeded"

    with Session(make_engine(settings.database_url)) as session:
        experiment = session.query(ExperimentRunModel).filter_by(job_id=prepared["id"]).one()
        child = session.query(ModelVersionModel).filter_by(experiment_run_id=experiment.id).one()
        assert experiment.dataset_version_id == dataset_id
        assert child.parent_id == parent_model_id
        assert child.purpose == "detector"
        assert child.verification_status == "ready"
        assert child.class_schema_json == list(settings.classes)


def test_model_verification_result_promotes_a_loadable_base_weight(tmp_path: Path) -> None:
    settings, project_id, _, _ = _workbench_workspace(tmp_path)
    from steel_platform.infrastructure.artifacts import LocalArtifactStore
    from steel_platform.infrastructure.workbench import SqlWorkbenchGateway

    store = LocalArtifactStore(settings.artifact_root)
    weights = store.put_bytes(b"pending-model", media_type="application/octet-stream")
    with Session(make_engine(settings.database_url)) as session:
        asset = AssetModel(
            project_id=project_id,
            kind="model_file",
            relative_path="imports/pending.pt",
            storage_key=weights.storage_key,
            sha256=weights.sha256,
            size_bytes=weights.size_bytes,
            media_type=weights.media_type,
        )
        session.add(asset)
        session.commit()
        asset_id = asset.id
    gateway = SqlWorkbenchGateway(settings, store)
    imported = gateway.import_model(
        project_id,
        name="待校验基础权重",
        weights_asset_id=asset_id,
        model_format="pt",
        purpose="base_weight",
        class_names=None,
        source_note="测试",
    )
    job_id = imported["verification_job_id"]
    prepared = gateway.prepare_job(
        project_id, job_id, expected_revision=0, idempotency_key="prepare-verifier"
    )
    with Session(make_engine(settings.database_url)) as session:
        job = session.get(JobModel, job_id)
        output = Path(job.spec_json["runtime"]["output_dir"])
        metadata = '{"loadable": true, "class_names": ["one", "two"]}'
        script = (
            "from pathlib import Path; "
            f"p=Path(r'{str(output).replace(chr(92), chr(92) * 2)}'); p.mkdir(parents=True, exist_ok=True); "
            f"(p/'metadata.json').write_text({metadata!r}, encoding='utf-8')"
        )
        job.spec_json = {
            **job.spec_json,
            "runtime": {**job.spec_json["runtime"], "arguments": [sys.executable, "-c", script]},
            "parameters": {**job.spec_json["parameters"], "device": "cpu"},
        }
        session.commit()

    assert execute_job(settings, prepared["id"]) == "succeeded"
    with Session(make_engine(settings.database_url)) as session:
        model = session.get(ModelVersionModel, imported["model"]["id"])
        assert model.verification_status == "ready"
        assert model.class_schema_json == ["one", "two"]


def test_inference_ingestion_registers_inference_run(tmp_path: Path) -> None:
    settings, project_id, _, model_id = _workbench_workspace(tmp_path)
    engine = make_engine(settings.database_url)
    with Session(engine) as session:
        image_asset = session.scalar(
            select(AssetModel).where(
                AssetModel.project_id == project_id, AssetModel.kind == "image"
            )
        )
        assert image_asset is not None
        job = JobModel(
            id="infer-registration",
            project_id=project_id,
            name="单图推理",
            kind="infer",
            preset="visual",
            status="succeeded",
            workspace_key="workbench/jobs/infer-registration",
            spec_json={
                "runtime": {
                    "output_dir": str(
                        settings.artifact_root / "workbench/jobs/infer-registration/output"
                    )
                }
            },
        )
        session.add(job)
        session.flush()
        session.add(
            JobLineageRefModel(
                job_id=job.id,
                direction="input",
                role="model",
                ref_type="model",
                ref_id=model_id,
                sha256_snapshot="b" * 64,
            )
        )
        session.add(
            JobLineageRefModel(
                job_id=job.id,
                direction="input",
                role="source",
                ref_type="asset",
                ref_id=image_asset.id,
                sha256_snapshot=image_asset.sha256,
            )
        )
        image_asset_id = image_asset.id
        image_name = Path(image_asset.relative_path).name
        session.commit()
    output = settings.artifact_root / "workbench/jobs/infer-registration/output"
    output.mkdir(parents=True)
    (output / "detections.csv").write_text(
        "source_file,frame_index,time_seconds,class_id,class_name,confidence,x1,y1,x2,y2\n"
        f"{image_name},0,0,0,Cr,0.8,1,1,10,10\n",
        encoding="utf-8",
    )
    (output / "labels").mkdir()
    (output / "labels" / f"{Path(image_name).stem}.txt").write_text(
        "0 0.5 0.5 0.2 0.2 0.8\n", encoding="utf-8"
    )

    from steel_platform.infrastructure.workbench_results import ingest_job_outputs

    ingest_job_outputs(settings, "infer-registration")

    with Session(engine) as session:
        run = session.scalar(
            select(InferenceRunModel).where(
                InferenceRunModel.name == "workbench-infer-registration"
            )
        )
        assert run is not None
        assert run.project_id == project_id
        assert run.model_version_id == model_id
        assert run.manifest_key
        prediction = session.scalar(
            select(CandidatePredictionModel).where(
                CandidatePredictionModel.inference_run_id == run.id
            )
        )
        assert prediction is not None
        assert prediction.image_asset_id == image_asset_id
        assert prediction.box_count == 1
        assert prediction.annotation_revision_id
