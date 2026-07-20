from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from steel_platform.application.errors import ApplicationError, NotFoundError, RevisionConflictError
from steel_platform.infrastructure.artifacts import LocalArtifactStore
from steel_platform.infrastructure.models import (
    AnnotationActionModel,
    AnnotationRevisionModel,
    AssetModel,
    CandidatePredictionModel,
    ClassSchemaModel,
    CollectionMemberModel,
    CollectionModel,
    DomainEventModel,
    IdempotencyRecordModel,
    InferenceRunModel,
    OutboxEventModel,
    ProjectModel,
    ReviewItemModel,
    ReviewRoundModel,
    SourceRootModel,
)


@dataclass(frozen=True, slots=True)
class WorkOrderPreview:
    matched: int
    selected: int
    by_class: dict[str, int]
    by_risk: dict[str, int]
    sample_asset_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SelectionRow:
    image_asset_id: str
    annotation_revision_id: str | None
    filename: str
    expected_class_id: int
    source_status: str
    min_confidence: float | None
    max_confidence: float | None
    box_count: int
    comparison_score: float = 0.0
    id: str = ""


class AnnotationWorkOrderService:
    """Application service for immutable annotation work-order allocation."""

    def __init__(self, factory: sessionmaker[Session], store: LocalArtifactStore) -> None:
        self._factory = factory
        self._store = store

    def options(self, project_id: str) -> dict[str, Any]:
        """Return project-scoped values used by the work-order creator."""
        with self._factory() as session:
            project, class_names = self._project_schema(session, project_id)
            runs = list(
                session.scalars(
                    select(InferenceRunModel)
                    .where(InferenceRunModel.project_id == project_id)
                    .order_by(InferenceRunModel.created_at.desc(), InferenceRunModel.id)
                )
            )
            sources = list(session.scalars(select(SourceRootModel).where(SourceRootModel.project_id == project_id).order_by(SourceRootModel.name)))
            collections = list(session.scalars(select(CollectionModel).where(CollectionModel.project_id == project_id).order_by(CollectionModel.name)))
            return {
                "annotation_policy": project.annotation_policy_json or {},
                "classes": [
                    {"id": class_id, "name": name}
                    for class_id, name in enumerate(class_names)
                ],
                "inference_runs": [
                    {
                        "id": row.id,
                        "name": row.name or f"推理运行 {row.id[:8]}",
                        "status": row.status,
                        "model_version_id": row.model_version_id,
                        "created_at": row.created_at.isoformat(),
                    }
                    for row in runs
                ],
                "sources": [{"id": row.id, "name": row.name, "status": row.status} for row in sources],
                "collections": [{"id": row.id, "name": row.name} for row in collections],
            }

    def preview(self, project_id: str, spec: dict[str, Any]) -> WorkOrderPreview:
        with self._factory() as session:
            project, class_names = self._project_schema(session, project_id)
            rows = self._select_candidates(session, project, spec)
            selected = self._apply_limit(rows, spec.get("filters") or {})
            return WorkOrderPreview(
                matched=len(rows),
                selected=len(selected),
                by_class=dict(
                    sorted(
                        Counter(
                            class_names[row.expected_class_id]
                            if 0 <= row.expected_class_id < len(class_names)
                            else str(row.expected_class_id)
                            for row in selected
                        ).items()
                    )
                ),
                by_risk=dict(sorted(Counter(row.source_status for row in selected).items())),
                sample_asset_ids=tuple(row.image_asset_id for row in selected[:12]),
            )

    def create(
        self, project_id: str, spec: dict[str, Any], *, idempotency_key: str
    ) -> dict[str, Any]:
        key = idempotency_key.strip()
        if not key:
            raise ApplicationError("validation_error", "必须提供Idempotency-Key", status_code=422)
        scope = self._scope("annotation-work-order-create", project_id, spec)
        with self._factory.begin() as session:
            replay = session.get(IdempotencyRecordModel, key)
            if replay is not None:
                return self._replay(replay, scope)
            project, _ = self._project_schema(session, project_id)
            self._validate_spec(session, project, spec)
            number = (
                session.scalar(
                    select(func.max(ReviewRoundModel.number)).where(
                        ReviewRoundModel.project_id == project_id
                    )
                )
                or 0
            ) + 1
            work_order = ReviewRoundModel(
                project_id=project_id,
                number=number,
                kind="annotation",
                name=str(spec["name"]).strip(),
                description=str(spec.get("description") or ""),
                task_type=str(spec["task_type"]),
                source_type=str(spec["source_type"]),
                source_id=str(spec["source_id"]),
                selection_spec_json=spec,
                class_schema_id=project.class_schema_id,
                target_count=0,
                per_class=0,
                status="draft",
            )
            session.add(work_order)
            session.flush()
            session.add(
                AnnotationActionModel(
                    project_id=project_id,
                    work_order_id=work_order.id,
                    action="created",
                    to_state="draft",
                )
            )
            self._emit(
                session,
                project_id,
                "annotation.work_order.created",
                {"work_order_id": work_order.id, "name": work_order.name, "status": work_order.status},
            )
            response = self._view(work_order)
            session.add(IdempotencyRecordModel(key=key, scope=scope, response_json=response))
            return response

    def freeze(
        self,
        project_id: str,
        work_order_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        key = idempotency_key.strip()
        with self._factory.begin() as session:
            work_order = self._work_order(session, project_id, work_order_id)
            scope = self._scope(
                "annotation-work-order-freeze",
                project_id,
                {"work_order_id": work_order_id, "expected_revision": expected_revision},
            )
            replay = session.get(IdempotencyRecordModel, key)
            if replay is not None:
                return self._replay(replay, scope)
            if work_order.revision != expected_revision:
                raise RevisionConflictError(expected_revision, work_order.revision)
            if work_order.status != "draft":
                raise ApplicationError("work_order_not_draft", "只有草稿工单可以冻结", status_code=409)
            project, _ = self._project_schema(session, project_id)
            rows = self._apply_limit(
                self._select_candidates(session, project, work_order.selection_spec_json),
                work_order.selection_spec_json.get("filters") or {},
            )
            if not rows:
                raise ApplicationError("empty_work_order", "筛选条件没有匹配图片", status_code=422)
            manifest = {
                "schema_version": "annotation-work-order-v1",
                "project_id": project_id,
                "work_order_id": work_order.id,
                "source_type": work_order.source_type,
                "source_id": work_order.source_id,
                "selection_spec": work_order.selection_spec_json,
                "items": [
                    {
                        "asset_id": row.image_asset_id,
                        "candidate_revision_id": row.annotation_revision_id,
                        "expected_class_id": row.expected_class_id,
                    }
                    for row in rows
                ],
            }
            payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
            ref = self._store.put_bytes(payload, media_type="application/json")
            for rank, row in enumerate(rows, start=1):
                session.add(
                    ReviewItemModel(
                        round_id=work_order.id,
                        image_asset_id=row.image_asset_id,
                        candidate_revision_id=row.annotation_revision_id,
                        filename=row.filename,
                        expected_class_id=row.expected_class_id,
                        source_status=row.source_status,
                        min_confidence=row.min_confidence,
                        max_confidence=row.max_confidence,
                        box_count=row.box_count,
                        selection_reason="condition_filter",
                        split_role="review",
                        rank=rank,
                    )
                )
            work_order.manifest_key = ref.storage_key
            work_order.manifest_sha256 = ref.sha256
            work_order.target_count = len(rows)
            work_order.per_class = 0
            work_order.status = "active"
            work_order.revision += 1
            session.add(
                AnnotationActionModel(
                    project_id=project_id,
                    work_order_id=work_order.id,
                    action="frozen",
                    from_state="draft",
                    to_state="active",
                )
            )
            self._emit(
                session,
                project_id,
                "annotation.work_order.created",
                {"work_order_id": work_order.id, "name": work_order.name, "status": work_order.status},
            )
            response = self._view(work_order)
            session.add(IdempotencyRecordModel(key=key, scope=scope, response_json=response))
            return response

    def create_amendment(
        self,
        project_id: str,
        parent_id: str,
        *,
        name: str,
        item_ids: list[str],
        idempotency_key: str,
    ) -> dict[str, Any]:
        key = idempotency_key.strip()
        payload = {"parent_id": parent_id, "name": name, "item_ids": sorted(set(item_ids))}
        scope = self._scope("annotation-amendment-create", project_id, payload)
        with self._factory.begin() as session:
            replay = session.get(IdempotencyRecordModel, key)
            if replay is not None:
                return self._replay(replay, scope)
            parent = self._work_order(session, project_id, parent_id)
            if parent.status not in {"completed", "archived"}:
                raise ApplicationError(
                    "work_order_not_completed", "只能从已完成或归档工单创建修订", status_code=409
                )
            rows = list(
                session.scalars(
                    select(ReviewItemModel).where(
                        ReviewItemModel.round_id == parent_id,
                        ReviewItemModel.id.in_(payload["item_ids"]),
                    )
                )
            )
            if len(rows) != len(payload["item_ids"]):
                raise NotFoundError("部分复核条目不存在或不属于原工单")
            number = (
                session.scalar(
                    select(func.max(ReviewRoundModel.number)).where(
                        ReviewRoundModel.project_id == project_id
                    )
                )
                or 0
            ) + 1
            work_order = ReviewRoundModel(
                project_id=project_id,
                number=number,
                kind="annotation",
                name=name.strip(),
                task_type="amendment",
                parent_work_order_id=parent.id,
                source_type="work_order",
                source_id=parent.id,
                selection_spec_json=payload,
                class_schema_id=parent.class_schema_id,
                target_count=len(rows),
                per_class=0,
                status="active",
            )
            session.add(work_order)
            session.flush()
            for rank, row in enumerate(sorted(rows, key=lambda item: item.rank), start=1):
                session.add(
                    ReviewItemModel(
                        round_id=work_order.id,
                        image_asset_id=row.image_asset_id,
                        candidate_revision_id=row.current_revision_id or row.candidate_revision_id,
                        filename=row.filename,
                        expected_class_id=row.expected_class_id,
                        source_status="amendment",
                        min_confidence=row.min_confidence,
                        max_confidence=row.max_confidence,
                        box_count=row.box_count,
                        selection_reason="amendment",
                        split_role="review",
                        rank=rank,
                    )
                )
            session.add(
                AnnotationActionModel(
                    project_id=project_id,
                    work_order_id=work_order.id,
                    action="amendment_created",
                    to_state="active",
                    note=f"父工单：{parent.id}",
                )
            )
            self._emit(
                session,
                project_id,
                "annotation.work_order.created",
                {"work_order_id": work_order.id, "name": work_order.name, "status": work_order.status, "parent_work_order_id": parent.id},
            )
            response = self._view(work_order)
            session.add(IdempotencyRecordModel(key=key, scope=scope, response_json=response))
            return response

    def list(self, project_id: str) -> list[dict[str, Any]]:
        with self._factory() as session:
            self._project_schema(session, project_id)
            return [
                self._view(row)
                for row in session.scalars(
                    select(ReviewRoundModel)
                    .where(ReviewRoundModel.project_id == project_id)
                    .order_by(ReviewRoundModel.created_at.desc(), ReviewRoundModel.id)
                )
            ]

    def get(self, project_id: str, work_order_id: str) -> dict[str, Any]:
        with self._factory() as session:
            return self._view(self._work_order(session, project_id, work_order_id))

    def history(self, project_id: str, work_order_id: str) -> list[dict[str, Any]]:
        with self._factory() as session:
            self._work_order(session, project_id, work_order_id)
            return [
                {
                    "id": row.id,
                    "item_id": row.item_id,
                    "actor": row.actor,
                    "action": row.action,
                    "from_state": row.from_state,
                    "to_state": row.to_state,
                    "annotation_revision_id": row.annotation_revision_id,
                    "request_id": row.request_id,
                    "note": row.note,
                    "created_at": row.created_at.isoformat(),
                }
                for row in session.scalars(
                    select(AnnotationActionModel)
                    .where(AnnotationActionModel.work_order_id == work_order_id)
                    .order_by(AnnotationActionModel.created_at, AnnotationActionModel.id)
                )
            ]

    def _select_candidates(
        self, session: Session, project: ProjectModel, spec: dict[str, Any]
    ) -> list[CandidatePredictionModel | SelectionRow]:
        self._validate_spec(session, project, spec)
        if spec["task_type"] == "manual_annotation":
            return self._select_manual_assets(session, project, spec)
        if spec["source_type"] != "inference":
            raise ApplicationError(
                "unsupported_work_order_source",
                "当前版本的条件预览仅支持已登记推理运行",
                status_code=422,
            )
        filters = spec.get("filters") or {}
        statement = (
            select(CandidatePredictionModel)
            .join(InferenceRunModel, InferenceRunModel.id == CandidatePredictionModel.inference_run_id)
            .where(
                CandidatePredictionModel.project_id == project.id,
                InferenceRunModel.project_id == project.id,
                InferenceRunModel.id == spec["source_id"],
            )
        )
        class_ids = filters.get("class_ids") or []
        if class_ids:
            statement = statement.where(CandidatePredictionModel.expected_class_id.in_(class_ids))
        rows = list(session.scalars(statement))
        risks = set(filters.get("risk_statuses") or [])
        if risks:
            rows = [row for row in rows if any(risk in row.source_status.split(";") for risk in risks)]
        if not filters.get("include_no_box", False):
            rows = [row for row in rows if row.source_status != "no_box" and row.box_count > 0]
        threshold = filters.get("max_min_confidence")
        if threshold is not None:
            rows = [
                row
                for row in rows
                if row.min_confidence is not None and row.min_confidence <= float(threshold)
            ]
        box_min = filters.get("box_count_min")
        box_max = filters.get("box_count_max")
        if box_min is not None:
            rows = [row for row in rows if row.box_count >= int(box_min)]
        if box_max is not None:
            rows = [row for row in rows if row.box_count <= int(box_max)]
        difference_min = filters.get("comparison_score_min")
        if difference_min is not None:
            rows = [row for row in rows if row.comparison_score >= float(difference_min)]
        if filters.get("exclude_reviewed", True):
            reviewed = set(
                session.scalars(
                    select(ReviewItemModel.image_asset_id)
                    .join(ReviewRoundModel, ReviewRoundModel.id == ReviewItemModel.round_id)
                    .where(
                        ReviewRoundModel.project_id == project.id,
                        ReviewItemModel.state.in_(("accepted", "corrected")),
                    )
                )
            )
            rows = [row for row in rows if row.image_asset_id not in reviewed]
        return sorted(
            rows,
            key=lambda row: (
                0 if "no_box" in row.source_status else 1,
                row.min_confidence if row.min_confidence is not None else -1.0,
                -row.comparison_score,
                row.filename.casefold(),
                row.id,
            ),
        )

    def _select_manual_assets(
        self, session: Session, project: ProjectModel, spec: dict[str, Any]
    ) -> list[SelectionRow]:
        _, class_names = self._project_schema(session, project.id)
        statement = select(AssetModel).where(
            AssetModel.project_id == project.id,
            AssetModel.kind == "image",
        )
        if spec["source_type"] == "source":
            statement = statement.where(AssetModel.source_root_id == spec["source_id"])
        else:
            statement = statement.join(
                CollectionMemberModel, CollectionMemberModel.asset_id == AssetModel.id
            ).where(CollectionMemberModel.collection_id == spec["source_id"])
        annotated = set(
            session.scalars(
                select(AnnotationRevisionModel.image_asset_id).where(
                    AnnotationRevisionModel.project_id == project.id
                )
            )
        )
        rows = []
        for asset in session.scalars(statement.order_by(AssetModel.relative_path, AssetModel.id)):
            if asset.id in annotated:
                continue
            filename = Path(asset.relative_path or asset.storage_key or asset.id).name
            prefix = filename.split("_", 1)[0]
            expected = class_names.index(prefix) if prefix in class_names else 0
            rows.append(
                SelectionRow(
                    image_asset_id=asset.id,
                    annotation_revision_id=None,
                    filename=filename,
                    expected_class_id=expected,
                    source_status="unannotated",
                    min_confidence=None,
                    max_confidence=None,
                    box_count=0,
                    id=asset.id,
                )
            )
        return rows

    @staticmethod
    def _apply_limit(
        rows: list[CandidatePredictionModel | SelectionRow], filters: dict[str, Any]
    ) -> list[CandidatePredictionModel | SelectionRow]:
        per_class_limit = filters.get("per_class_limit")
        if per_class_limit:
            counts: Counter[int] = Counter()
            limited = []
            for row in rows:
                if counts[row.expected_class_id] < int(per_class_limit):
                    limited.append(row)
                    counts[row.expected_class_id] += 1
            rows = limited
        total_limit = int(filters.get("total_limit") or len(rows))
        return rows[: max(0, total_limit)]

    @staticmethod
    def _validate_spec(session: Session, project: ProjectModel, spec: dict[str, Any]) -> None:
        if not str(spec.get("name") or "").strip():
            raise ApplicationError("validation_error", "工单名称不能为空", status_code=422)
        task_type = spec.get("task_type")
        if task_type not in {"manual_annotation", "inference_review"}:
            raise ApplicationError("validation_error", "不支持的工单类型", status_code=422)
        source_type, source_id = spec.get("source_type"), spec.get("source_id")
        if not source_id:
            raise ApplicationError("validation_error", "必须选择数据来源", status_code=422)
        if task_type == "inference_review":
            if source_type != "inference":
                raise ApplicationError("validation_error", "复核工单必须选择一次推理运行", status_code=422)
            exists = session.scalar(select(InferenceRunModel.id).where(InferenceRunModel.project_id == project.id, InferenceRunModel.id == source_id))
        elif source_type == "source":
            exists = session.scalar(select(SourceRootModel.id).where(SourceRootModel.project_id == project.id, SourceRootModel.id == source_id))
        elif source_type == "collection":
            exists = session.scalar(select(CollectionModel.id).where(CollectionModel.project_id == project.id, CollectionModel.id == source_id))
        else:
            raise ApplicationError("validation_error", "初始标注仅支持数据源或集合", status_code=422)
        if exists is None:
            raise NotFoundError("所选数据来源不存在或不属于当前项目")

    @staticmethod
    def _project_schema(session: Session, project_id: str) -> tuple[ProjectModel, tuple[str, ...]]:
        project = session.get(ProjectModel, project_id)
        if project is None:
            raise NotFoundError("项目不存在")
        schema = session.get(ClassSchemaModel, project.class_schema_id) if project.class_schema_id else None
        return project, tuple(schema.names_json) if schema is not None else ()

    @staticmethod
    def _work_order(session: Session, project_id: str, work_order_id: str) -> ReviewRoundModel:
        row = session.scalar(
            select(ReviewRoundModel).where(
                ReviewRoundModel.project_id == project_id,
                ReviewRoundModel.id == work_order_id,
            )
        )
        if row is None:
            raise NotFoundError("标注工单不存在")
        return row

    @staticmethod
    def _view(row: ReviewRoundModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "project_id": row.project_id,
            "number": row.number,
            "name": row.name,
            "description": row.description,
            "task_type": row.task_type,
            "parent_work_order_id": row.parent_work_order_id,
            "source_type": row.source_type,
            "source_id": row.source_id,
            "status": row.status,
            "target_count": row.target_count,
            "manifest_sha256": row.manifest_sha256,
            "revision": row.revision,
            "created_at": row.created_at.isoformat(),
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            "archived_at": row.archived_at.isoformat() if row.archived_at else None,
        }

    @staticmethod
    def _scope(operation: str, project_id: str, payload: dict[str, Any]) -> str:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return f"{operation}:{project_id}:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"

    @staticmethod
    def _replay(record: IdempotencyRecordModel, scope: str) -> dict[str, Any]:
        if record.scope != scope:
            raise ApplicationError(
                "idempotency_conflict", "幂等键已用于不同请求", status_code=409
            )
        return dict(record.response_json)

    @staticmethod
    def _emit(session: Session, project_id: str, event_type: str, payload: dict[str, Any]) -> None:
        event = DomainEventModel(
            project_id=project_id,
            event_type=event_type,
            payload_json={"project_id": project_id, **payload},
        )
        session.add(event)
        session.flush()
        session.add(OutboxEventModel(domain_event_id=event.id))
