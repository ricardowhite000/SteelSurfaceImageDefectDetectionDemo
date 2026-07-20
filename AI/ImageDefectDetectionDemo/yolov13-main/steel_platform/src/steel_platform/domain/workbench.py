from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class JobKind(str, Enum):
    TRAIN = "train"
    EVALUATE = "evaluate"
    INFER = "infer"
    VERIFY_MODEL = "verify_model"


class JobStatus(str, Enum):
    DRAFT = "draft"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class ModelPurpose(str, Enum):
    BASE_WEIGHT = "base_weight"
    DETECTOR = "detector"


class ModelFormat(str, Enum):
    PYTORCH = "pt"
    ONNX = "onnx"


@dataclass(frozen=True, slots=True)
class JobInputRef:
    role: str
    ref_id: str
    ref_type: str
    sha256_snapshot: str | None = None


_DEFAULTS: dict[tuple[JobKind, str], dict[str, object]] = {
    (JobKind.TRAIN, "smoke"): {
        "epochs": 1,
        "imgsz": 640,
        "batch": 4,
        "patience": 20,
        "workers": 0,
        "amp": True,
        "seed": 42,
    },
    (JobKind.TRAIN, "smoke_cpu"): {
        "epochs": 1,
        "imgsz": 320,
        "batch": 1,
        "patience": 0,
        "workers": 0,
        "amp": False,
        "seed": 42,
        "device": "cpu",
        "timeout_seconds": 1800,
    },
    (JobKind.TRAIN, "formal"): {
        "epochs": 100,
        "imgsz": 640,
        "batch": 4,
        "patience": 20,
        "workers": 0,
        "amp": True,
        "seed": 42,
    },
    (JobKind.EVALUATE, "fixed_val"): {
        "imgsz": 640,
        "batch": 4,
        "workers": 0,
    },
    (JobKind.INFER, "visual"): {
        "conf": 0.25,
        "imgsz": 640,
        "batch": 1,
        "stream": True,
        "save_crop": False,
    },
    (JobKind.INFER, "infer_cpu"): {
        "conf": 0.25,
        "imgsz": 320,
        "batch": 1,
        "stream": True,
        "save_crop": False,
        "device": "cpu",
    },
    (JobKind.INFER, "pseudo_label"): {
        "conf": 0.20,
        "review_conf": 0.40,
        "imgsz": 640,
        "batch": 1,
        "stream": True,
        "save_crop": False,
    },
    (JobKind.INFER, "video"): {
        "conf": 0.25,
        "imgsz": 640,
        "batch": 1,
        "stream": True,
        "save_crop": False,
    },
    (JobKind.VERIFY_MODEL, "metadata"): {},
}

_ALLOWED: dict[JobKind, set[str]] = {
    JobKind.TRAIN: {"epochs", "imgsz", "batch", "patience", "workers", "amp", "seed", "device"},
    JobKind.EVALUATE: {"imgsz", "batch", "workers", "device"},
    JobKind.INFER: {"conf", "review_conf", "imgsz", "batch", "stream", "save_crop", "device"},
    JobKind.VERIFY_MODEL: {"device"},
}


@dataclass(frozen=True, slots=True)
class WorkbenchJobSpec:
    kind: JobKind
    preset: str
    input_refs: tuple[JobInputRef, ...]
    parameters: Mapping[str, object]
    runtime_profile_id: str | None = None

    @classmethod
    def create(
        cls,
        *,
        kind: JobKind,
        preset: str,
        input_refs: tuple[JobInputRef, ...],
        parameters: Mapping[str, object],
        allowed_devices: tuple[str, ...],
        runtime_profile_id: str | None = None,
    ) -> "WorkbenchJobSpec":
        defaults = _DEFAULTS.get((kind, preset))
        if defaults is None:
            raise ValueError(f"未知任务预设：{kind.value}/{preset}")
        unknown = set(parameters) - _ALLOWED[kind]
        if unknown:
            raise ValueError(f"未知参数：{', '.join(sorted(unknown))}")
        normalized = {**defaults, **parameters}
        normalized.setdefault("device", allowed_devices[0] if allowed_devices else "cpu")
        _validate_inputs(kind, input_refs)
        _validate_parameters(kind, normalized, allowed_devices)
        normalized_profile_id = runtime_profile_id.strip() if runtime_profile_id else None
        return cls(
            kind,
            preset,
            input_refs,
            MappingProxyType(normalized),
            normalized_profile_id,
        )


def _validate_inputs(kind: JobKind, refs: tuple[JobInputRef, ...]) -> None:
    roles = {ref.role for ref in refs}
    required = {
        JobKind.TRAIN: {"dataset", "model"},
        JobKind.EVALUATE: {"dataset", "model"},
        JobKind.INFER: {"model", "source"},
        JobKind.VERIFY_MODEL: {"model_asset"},
    }[kind]
    missing = required - roles
    if missing:
        raise ValueError(f"缺少任务输入：{', '.join(sorted(missing))}")
    if any(not ref.ref_id.strip() or not ref.ref_type.strip() for ref in refs):
        raise ValueError("任务输入引用不能为空")


def _bounded_integer(values: Mapping[str, object], name: str, minimum: int, maximum: int) -> int:
    value = values.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError(f"{name} 必须在 {minimum} 到 {maximum} 之间")
    return value


def _validate_parameters(
    kind: JobKind, values: Mapping[str, object], allowed_devices: tuple[str, ...]
) -> None:
    imgsz = _bounded_integer(values, "imgsz", 320, 1536) if "imgsz" in values else None
    if imgsz is not None and imgsz % 32:
        raise ValueError("imgsz 必须是32的倍数")
    if kind is JobKind.TRAIN:
        _bounded_integer(values, "epochs", 1, 500)
        _bounded_integer(values, "batch", 1, 64)
        _bounded_integer(values, "patience", 0, 200)
        _bounded_integer(values, "workers", 0, 8)
        _bounded_integer(values, "seed", 0, 2_147_483_647)
        if not isinstance(values.get("amp"), bool):
            raise ValueError("amp 必须为布尔值")
    elif kind is JobKind.EVALUATE:
        _bounded_integer(values, "batch", 1, 64)
        _bounded_integer(values, "workers", 0, 8)
    elif kind is JobKind.INFER:
        if values.get("batch") != 1:
            raise ValueError("推理 batch 固定为1")
        if values.get("stream") is not True:
            raise ValueError("推理必须使用流式处理")
        for name in ("conf", "review_conf"):
            if name in values:
                value = values[name]
                if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0.01 <= float(value) <= 0.99:
                    raise ValueError(f"{name} 必须在0.01到0.99之间")
    if values.get("device") not in allowed_devices:
        raise ValueError(f"device 不在允许列表中：{values.get('device')}")


_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.DRAFT: {JobStatus.READY, JobStatus.CANCELLED},
    JobStatus.READY: {JobStatus.RUNNING, JobStatus.CANCELLED},
    JobStatus.RUNNING: {
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
        JobStatus.INTERRUPTED,
    },
    JobStatus.FAILED: {JobStatus.READY},
    JobStatus.CANCELLED: {JobStatus.READY},
    JobStatus.INTERRUPTED: {JobStatus.READY},
    JobStatus.SUCCEEDED: set(),
}


def validate_transition(current: JobStatus, target: JobStatus) -> None:
    if target not in _TRANSITIONS[current]:
        raise ValueError(f"不允许的任务状态转换：{current.value} -> {target.value}")
