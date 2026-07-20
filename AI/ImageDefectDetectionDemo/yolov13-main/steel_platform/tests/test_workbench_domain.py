from __future__ import annotations

import pytest

from steel_platform.domain.workbench import (
    JobInputRef,
    JobKind,
    JobStatus,
    WorkbenchJobSpec,
    validate_transition,
)


def test_training_presets_are_normalized_without_accepting_raw_commands() -> None:
    spec = WorkbenchJobSpec.create(
        kind=JobKind.TRAIN,
        preset="smoke",
        input_refs=(
            JobInputRef("dataset", "dataset-1", "dataset"),
            JobInputRef("model", "model-1", "model"),
        ),
        parameters={"imgsz": 640, "batch": 4, "device": "0"},
        allowed_devices=("0", "cpu"),
    )

    assert spec.parameters["epochs"] == 1
    assert spec.parameters["workers"] == 0
    assert "command" not in spec.parameters
    with pytest.raises(ValueError, match="未知参数"):
        WorkbenchJobSpec.create(
            kind=JobKind.TRAIN,
            preset="formal",
            input_refs=spec.input_refs,
            parameters={"command": "Remove-Item -Recurse C:/"},
            allowed_devices=("0",),
        )


@pytest.mark.parametrize(
    ("parameters", "message"),
    [
        ({"imgsz": 641}, "32"),
        ({"epochs": 501}, "epochs"),
        ({"batch": 65}, "batch"),
        ({"device": "cuda:99"}, "device"),
    ],
)
def test_training_parameter_limits_are_enforced(parameters: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        WorkbenchJobSpec.create(
            kind=JobKind.TRAIN,
            preset="formal",
            input_refs=(
                JobInputRef("dataset", "dataset-1", "dataset"),
                JobInputRef("model", "model-1", "model"),
            ),
            parameters=parameters,
            allowed_devices=("0", "cpu"),
        )


def test_inference_is_always_streamed_with_batch_one() -> None:
    spec = WorkbenchJobSpec.create(
        kind=JobKind.INFER,
        preset="visual",
        input_refs=(
            JobInputRef("model", "model-1", "model"),
            JobInputRef("source", "source-1", "source"),
        ),
        parameters={"conf": 0.25, "imgsz": 640, "device": "0"},
        allowed_devices=("0",),
    )
    assert spec.parameters["batch"] == 1
    assert spec.parameters["stream"] is True
    with pytest.raises(ValueError, match="batch"):
        WorkbenchJobSpec.create(
            kind=JobKind.INFER,
            preset="visual",
            input_refs=spec.input_refs,
            parameters={"batch": 2},
            allowed_devices=("0",),
        )


def test_cpu_presets_disable_amp_and_reduce_image_size() -> None:
    train = WorkbenchJobSpec.create(
        kind=JobKind.TRAIN,
        preset="smoke_cpu",
        input_refs=(
            JobInputRef("dataset", "dataset-1", "dataset"),
            JobInputRef("model", "model-1", "model"),
        ),
        parameters={},
        allowed_devices=("cpu",),
    )
    infer = WorkbenchJobSpec.create(
        kind=JobKind.INFER,
        preset="infer_cpu",
        input_refs=(
            JobInputRef("model", "model-1", "model"),
            JobInputRef("source", "source-1", "source"),
        ),
        parameters={},
        allowed_devices=("cpu",),
    )

    assert train.parameters == {
        "epochs": 1,
        "imgsz": 320,
        "batch": 1,
        "patience": 0,
        "workers": 0,
        "amp": False,
        "seed": 42,
        "device": "cpu",
        "timeout_seconds": 1800,
    }
    assert infer.parameters["imgsz"] == 320
    assert infer.parameters["batch"] == 1
    assert infer.parameters["stream"] is True
    assert infer.parameters["device"] == "cpu"


def test_training_requires_a_registered_parent_model() -> None:
    with pytest.raises(ValueError, match="model"):
        WorkbenchJobSpec.create(
            kind=JobKind.TRAIN,
            preset="formal",
            input_refs=(JobInputRef("dataset", "dataset-1", "dataset"),),
            parameters={},
            allowed_devices=("0",),
        )


def test_job_state_machine_allows_retry_but_not_rewriting_success() -> None:
    validate_transition(JobStatus.DRAFT, JobStatus.READY)
    validate_transition(JobStatus.READY, JobStatus.RUNNING)
    validate_transition(JobStatus.RUNNING, JobStatus.FAILED)
    validate_transition(JobStatus.FAILED, JobStatus.READY)
    with pytest.raises(ValueError, match="状态转换"):
        validate_transition(JobStatus.SUCCEEDED, JobStatus.RUNNING)
