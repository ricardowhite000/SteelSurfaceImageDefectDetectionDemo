from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any

from steel_platform.application.errors import (
    ApplicationError,
    NotFoundError,
    RevisionConflictError,
)
from steel_platform.domain.annotations import AnnotationBox, AnnotationDecision, ReviewState
from steel_platform.domain.ports import AnnotationCodec, ArtifactStore, UnitOfWork
from steel_platform.domain.workspace import (
    ConcurrentAllocationError,
    IdempotencyRecord,
    IdempotencyReservationConflict,
)


@dataclass(frozen=True, slots=True)
class ReviewDecisionCommand:
    expected_revision: int
    action: str
    boxes: tuple[AnnotationBox, ...]
    note: str = ""


@dataclass(frozen=True, slots=True)
class DecisionResult:
    item_id: str
    state: str
    revision: int
    annotation_revision_id: str | None
    replacement_item_id: str | None
    next_pending_item_id: str | None
    progress: dict[str, int]
    round_completed: bool

    def as_response(self) -> dict[str, object]:
        return {
            "item_id": self.item_id,
            "state": self.state,
            "revision": self.revision,
            "annotation_revision_id": self.annotation_revision_id,
            "replacement_item_id": self.replacement_item_id,
            "next_pending_item_id": self.next_pending_item_id,
            "progress": dict(self.progress),
            "round_completed": self.round_completed,
        }

    @classmethod
    def from_response(cls, response: dict[str, object]) -> DecisionResult:
        return cls(
            item_id=str(response["item_id"]),
            state=str(response["state"]),
            revision=int(response["revision"]),
            annotation_revision_id=_optional_string(response.get("annotation_revision_id")),
            replacement_item_id=_optional_string(response.get("replacement_item_id")),
            next_pending_item_id=_optional_string(response.get("next_pending_item_id")),
            progress={str(key): int(value) for key, value in dict(response["progress"]).items()},
            round_completed=bool(response["round_completed"]),
        )


class ReviewDecisionService:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        *,
        artifact_store: ArtifactStore,
        annotation_codec: AnnotationCodec,
        class_names: Sequence[str] = (),
    ) -> None:
        self._uow_factory = uow_factory
        self._artifact_store = artifact_store
        self._annotation_codec = annotation_codec
        self._class_names = tuple(class_names)

    def decide(
        self,
        project_id: str,
        round_id: str,
        item_id: str,
        command: ReviewDecisionCommand,
        idempotency_key: str,
    ) -> DecisionResult:
        key = idempotency_key.strip()
        if not key:
            raise ApplicationError(
                "validation_error",
                "必须提供Idempotency-Key",
                status_code=422,
            )
        scope = _canonical_scope(project_id, round_id, item_id, command)
        replay = self._load_replay(key, scope)
        if replay is not None:
            return replay
        try:
            with self._uow_factory() as uow:
                prior = uow.idempotency.get(key)
                if prior is not None:
                    return self._validate_replay(prior, scope)
                uow.idempotency.reserve(IdempotencyRecord(key=key, scope=scope, response={}))
                review_round = uow.review_tasks.get_round(project_id, round_id)
                item = uow.review_tasks.get_item(project_id, round_id, item_id)
                if review_round is None or item is None:
                    raise NotFoundError("复核条目不存在")
                if item.revision != command.expected_revision:
                    raise RevisionConflictError(command.expected_revision, item.revision)
                class_names = self._resolve_class_names(uow, project_id, review_round)
                if item.expected_class_id < 0 or item.expected_class_id >= len(class_names):
                    raise ApplicationError(
                        "class_mismatch",
                        "复核条目的类别编号不属于任务类别模式",
                        status_code=422,
                    )
                project = uow.projects.get(project_id)
                policy = project.annotation_policy if project is not None else None
                single_class_locked = (policy or {}).get("mode") == "single_class_locked"
                allow_empty_labels = bool((policy or {}).get("allow_empty_labels", False))
                decision = _validate_command(command, allow_empty_labels=allow_empty_labels)
                if single_class_locked and any(
                    box.class_id != item.expected_class_id for box in decision.boxes
                ):
                    raise ApplicationError(
                        "class_mismatch",
                        "一张图片只能保存文件前缀对应的缺陷类别",
                        status_code=422,
                    )
                if (
                    not decision.boxes
                    and decision.action in {ReviewState.ACCEPTED, ReviewState.CORRECTED}
                    and not allow_empty_labels
                ):
                    raise ApplicationError(
                        "empty_label_not_allowed",
                        "当前项目不允许将空标签作为有效标注保存",
                        status_code=422,
                    )

                annotation_revision_id: str | None = None
                current_revision_id = item.current_revision_id
                if decision.action in {ReviewState.ACCEPTED, ReviewState.CORRECTED}:
                    content = self._annotation_codec.encode(decision.boxes)
                    ref = self._artifact_store.put_bytes(content, media_type="text/yolo")
                    annotation_revision_id = uow.review_tasks.add_annotation_revision(
                        project_id,
                        round_id,
                        item_id,
                        parent_id=item.current_revision_id or item.candidate_revision_id,
                        decision=decision.action.value,
                        storage_key=ref.storage_key,
                        sha256=ref.sha256,
                        box_count=len(decision.boxes),
                    )
                    current_revision_id = annotation_revision_id
                    uow.review_tasks.delete_draft(project_id, round_id, item_id)
                elif decision.action == ReviewState.DOUBTFUL:
                    uow.review_tasks.upsert_draft(
                        project_id,
                        round_id,
                        item_id,
                        boxes=tuple(asdict(box) for box in decision.boxes),
                        note=decision.note,
                    )

                updated = uow.review_tasks.update_item_decision(
                    project_id,
                    round_id,
                    item_id,
                    expected_revision=command.expected_revision,
                    state=decision.action.value,
                    note=decision.note,
                    current_revision_id=current_revision_id,
                )
                if updated is None:
                    actual = uow.review_tasks.get_item(project_id, round_id, item_id)
                    raise RevisionConflictError(
                        command.expected_revision,
                        actual.revision if actual is not None else command.expected_revision + 1,
                    )

                replacement_item_id = None
                if decision.action in {ReviewState.DOUBTFUL, ReviewState.EXCLUDED}:
                    replacement_item_id = uow.review_tasks.add_replacement(
                        project_id,
                        round_id,
                        item_id,
                    )
                uow.review_tasks.add_review_event(
                    project_id,
                    round_id,
                    item_id,
                    state=decision.action.value,
                    revision=updated.revision,
                )
                progress = uow.review_tasks.progress(project_id, round_id)
                next_pending_item_id = uow.review_tasks.next_pending_item_id(project_id, round_id)
                round_completed = next_pending_item_id is None
                uow.review_tasks.set_round_completed(project_id, round_id, round_completed)
                result = DecisionResult(
                    item_id=item_id,
                    state=decision.action.value,
                    revision=updated.revision,
                    annotation_revision_id=annotation_revision_id,
                    replacement_item_id=replacement_item_id,
                    next_pending_item_id=next_pending_item_id,
                    progress=progress,
                    round_completed=round_completed,
                )
                uow.idempotency.set_response(key, result.as_response())
                uow.commit()
                return result
        except IdempotencyReservationConflict:
            replay = self._load_replay(key, scope)
            if replay is None:
                raise _retryable_concurrency_error()
            return replay
        except ConcurrentAllocationError as exc:
            replay = self._load_replay(key, scope)
            if replay is not None:
                return replay
            raise _retryable_concurrency_error() from exc

    def _resolve_class_names(
        self,
        uow: UnitOfWork,
        project_id: str,
        review_round: Any,
    ) -> tuple[str, ...]:
        schema_id = review_round.class_schema_id
        if schema_id is None:
            project = uow.projects.get(project_id)
            schema_id = project.class_schema_id if project is not None else None
        if schema_id:
            schema = uow.projects.get_class_schema(project_id, schema_id)
            if schema is None:
                raise NotFoundError("复核任务的类别模式不存在")
            return tuple(schema.names)
        if self._class_names:
            return self._class_names
        raise NotFoundError("项目尚未配置类别模式")

    def _load_replay(self, key: str, scope: str) -> DecisionResult | None:
        with self._uow_factory() as uow:
            prior = uow.idempotency.get(key)
            if prior is None:
                return None
            return self._validate_replay(prior, scope)

    @staticmethod
    def _validate_replay(record: IdempotencyRecord, scope: str) -> DecisionResult:
        if record.scope != scope:
            raise ApplicationError(
                "idempotency_conflict",
                "幂等键已用于不同的请求负载",
                status_code=409,
            )
        if not record.response:
            raise ApplicationError(
                "idempotency_conflict",
                "幂等请求尚未完成",
                status_code=409,
            )
        return DecisionResult.from_response(record.response)


def _validate_command(
    command: ReviewDecisionCommand, *, allow_empty_labels: bool = False
) -> AnnotationDecision:
    try:
        return AnnotationDecision(
            ReviewState(command.action),
            tuple(command.boxes),
            command.note,
            command.expected_revision,
            allow_empty_labels,
        )
    except (TypeError, ValueError) as exc:
        raise ApplicationError("validation_error", str(exc), status_code=422) from exc


def _canonical_scope(
    project_id: str,
    round_id: str,
    item_id: str,
    command: ReviewDecisionCommand,
) -> str:
    payload = {
        "project_id": project_id,
        "round_id": round_id,
        "item_id": item_id,
        "expected_revision": command.expected_revision,
        "action": command.action,
        "boxes": [asdict(box) for box in command.boxes],
        "note": command.note,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"review-decision:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _optional_string(value: Any) -> str | None:
    return None if value is None else str(value)


def _retryable_concurrency_error() -> ApplicationError:
    return ApplicationError(
        "concurrency_conflict",
        "复核决策与另一个请求并发，请重试",
        status_code=409,
        details={"retryable": True},
    )
