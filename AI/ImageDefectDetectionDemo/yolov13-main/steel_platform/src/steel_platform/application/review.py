from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from steel_platform.application.errors import ApplicationError, NotFoundError
from steel_platform.application.review_decisions import (
    ReviewDecisionCommand,
    ReviewDecisionService,
)
from steel_platform.application.review_queries import ReviewFilters, ReviewTaskQueryService
from steel_platform.domain.annotations import AnnotationBox, ReviewState
from steel_platform.infrastructure.artifacts import LocalArtifactStore
from steel_platform.infrastructure.config import PlatformSettings
from steel_platform.infrastructure.database import make_engine
from steel_platform.infrastructure.models import (
    AnnotationRevisionModel,
    AssetModel,
    DatasetVersionModel,
    ExperimentRunModel,
    InferenceRunModel,
    JobModel,
    MetricSnapshotModel,
    ModelVersionModel,
    ProjectModel,
    ReviewItemModel,
    ReviewRoundModel,
)
from steel_platform.infrastructure.uow import SqlAlchemyUnitOfWork
from steel_platform.infrastructure.yolo import YoloAnnotationCodec


class ReviewService:
    """Compatibility façade for the original single-project API.

    New callers should pass project and round IDs directly to
    :class:`ReviewTaskQueryService` and :class:`ReviewDecisionService`.
    """

    def __init__(self, settings: PlatformSettings) -> None:
        self.settings = settings
        self.engine = make_engine(settings.database_url)
        factory = sessionmaker(bind=self.engine, class_=Session)
        uow_factory: Callable[[], SqlAlchemyUnitOfWork] = lambda: SqlAlchemyUnitOfWork(factory)
        store = LocalArtifactStore(settings.artifact_root)
        codec = YoloAnnotationCodec()
        self.queries = ReviewTaskQueryService(
            uow_factory,
            class_names=settings.classes,
            artifact_store=store,
            annotation_codec=codec,
        )
        self.decisions = ReviewDecisionService(
            uow_factory,
            artifact_store=store,
            annotation_codec=codec,
            class_names=settings.classes,
        )

    def _configured_project(self, session: Session) -> ProjectModel:
        project = session.scalar(
            select(ProjectModel).where(ProjectModel.name == self.settings.project_name)
        )
        if project is None:
            raise NotFoundError("配置的项目尚未初始化")
        return project

    def _configured_scope(self) -> tuple[str, str]:
        with Session(self.engine) as session:
            project = self._configured_project(session)
            review_round = session.scalars(
                select(ReviewRoundModel)
                .where(ReviewRoundModel.project_id == project.id)
                .order_by(ReviewRoundModel.number.desc(), ReviewRoundModel.created_at.desc())
            ).first()
            if review_round is None:
                raise NotFoundError("配置的项目没有复核任务")
            return project.id, review_round.id

    def list_queue(
        self,
        *,
        state: str | None = None,
        class_id: int | None = None,
        source_status: str | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        project_id, round_id = self._configured_scope()
        page = self.queries.list_items(
            project_id,
            round_id,
            ReviewFilters(state, class_id, source_status, search),
        )
        return {
            "items": [
                {
                    "id": item.id,
                    "round": item.round_number,
                    "image_asset_id": item.image_asset_id,
                    "filename": item.filename,
                    "expected_class_id": item.expected_class_id,
                    "expected_class_name": item.expected_class_name,
                    "source_status": item.source_status,
                    "selection_reason": item.selection_reason,
                    "state": item.state,
                    "revision": item.revision,
                    "rank": item.rank,
                }
                for item in page.items
            ]
        }

    def get_item(self, item_id: str) -> dict[str, Any]:
        project_id, round_id = self._configured_scope()
        item = self.queries.get_item(project_id, round_id, item_id)
        return {
            "id": item.id,
            "image_asset_id": item.image_asset_id,
            "filename": item.filename,
            "expected_class_id": item.expected_class_id,
            "expected_class_name": item.expected_class_name,
            "source_status": item.source_status,
            "selection_reason": item.selection_reason,
            "min_confidence": item.min_confidence,
            "max_confidence": item.max_confidence,
            "candidate_box_count": item.candidate_box_count,
            "state": item.state,
            "revision": item.revision,
            "note": item.note,
            "boxes": [asdict(box) for box in item.boxes],
        }

    def decide(
        self,
        item_id: str,
        *,
        idempotency_key: str,
        expected_revision: int,
        action: str,
        boxes_data: list[dict[str, Any]],
        note: str,
    ) -> dict[str, Any]:
        project_id, round_id = self._configured_scope()
        try:
            boxes = tuple(AnnotationBox(**box) for box in boxes_data)
        except (TypeError, ValueError) as exc:
            raise ApplicationError("validation_error", str(exc), status_code=422) from exc
        result = self.decisions.decide(
            project_id,
            round_id,
            item_id,
            ReviewDecisionCommand(expected_revision, action, boxes, note),
            idempotency_key,
        )
        response = result.as_response()
        response["id"] = response.pop("item_id")
        return response

    def overview(self) -> dict[str, Any]:
        with Session(self.engine) as session:
            project = self._configured_project(session)
            image_count = session.scalar(
                select(func.count()).select_from(AssetModel).where(
                    AssetModel.project_id == project.id,
                    AssetModel.kind == "image",
                )
            ) or 0
            label_count = session.scalar(
                select(func.count()).select_from(AnnotationRevisionModel).where(
                    AnnotationRevisionModel.project_id == project.id
                )
            ) or 0
            current_round = session.scalars(
                select(ReviewRoundModel)
                .where(ReviewRoundModel.project_id == project.id)
                .order_by(ReviewRoundModel.number.desc(), ReviewRoundModel.created_at.desc())
            ).first()
            states: dict[str, int] = {}
            class_rows: list[tuple[int, str, int]] = []
            by_risk: dict[str, int] = {}
            if current_round is not None:
                states = dict(
                    session.execute(
                        select(ReviewItemModel.state, func.count())
                        .where(ReviewItemModel.round_id == current_round.id)
                        .group_by(ReviewItemModel.state)
                    ).all()
                )
                class_rows = list(
                    session.execute(
                        select(ReviewItemModel.expected_class_id, ReviewItemModel.state, func.count())
                        .where(ReviewItemModel.round_id == current_round.id)
                        .group_by(ReviewItemModel.expected_class_id, ReviewItemModel.state)
                    ).all()
                )
                by_risk = dict(
                    session.execute(
                        select(ReviewItemModel.source_status, func.count())
                        .where(ReviewItemModel.round_id == current_round.id)
                        .group_by(ReviewItemModel.source_status)
                    ).all()
                )
            target = current_round.per_class * len(self.settings.classes) if current_round else 0
            valid_completed = states.get("accepted", 0) + states.get("corrected", 0)
            by_class = {
                name: {"target": current_round.per_class if current_round else 0, "states": {}}
                for name in self.settings.classes
            }
            for class_id, state_name, count in class_rows:
                by_class[self.settings.classes[class_id]]["states"][state_name] = count

            datasets = session.scalars(
                select(DatasetVersionModel)
                .where(DatasetVersionModel.project_id == project.id)
                .order_by(DatasetVersionModel.created_at.desc())
            ).all()[:5]
            jobs = session.scalars(
                select(JobModel).where(JobModel.project_id == project.id).order_by(JobModel.created_at.desc())
            ).all()[:8]
            experiments = session.scalars(
                select(ExperimentRunModel)
                .where(ExperimentRunModel.project_id == project.id)
                .order_by(ExperimentRunModel.created_at.desc())
            ).all()[:5]
            models = session.scalars(
                select(ModelVersionModel)
                .where(ModelVersionModel.project_id == project.id)
                .order_by(ModelVersionModel.created_at.desc())
            ).all()[:5]
            inference = session.scalars(
                select(InferenceRunModel)
                .where(InferenceRunModel.project_id == project.id)
                .order_by(InferenceRunModel.created_at.desc())
            ).all()[:5]
            metrics = session.scalars(
                select(MetricSnapshotModel)
                .where(MetricSnapshotModel.project_id == project.id)
                .order_by(MetricSnapshotModel.created_at.desc())
            ).all()[:5]
            return {
                "assets": {"images": image_count, "annotation_revisions": label_count},
                "review": {
                    "round": current_round.number if current_round else None,
                    "kind": current_round.kind if current_round else None,
                    "target": target,
                    "total": target,
                    "completed": valid_completed,
                    "pending": max(0, target - valid_completed),
                    "item_count": sum(states.values()),
                    "by_class": by_class,
                    "by_risk": by_risk,
                    **{
                        state.value: states.get(state.value, 0)
                        for state in ReviewState
                        if state != ReviewState.PENDING
                    },
                },
                "datasets": [
                    {"id": row.id, "name": row.name, "created_at": row.created_at.isoformat()}
                    for row in datasets
                ],
                "runs": [
                    {
                        "id": row.id,
                        "kind": row.kind,
                        "status": row.status,
                        "created_at": row.created_at.isoformat(),
                    }
                    for row in jobs
                ],
                "experiments": [
                    {
                        "id": row.id,
                        "job_id": row.job_id,
                        "dataset_version_id": row.dataset_version_id,
                        "status": row.status,
                        "created_at": row.created_at.isoformat(),
                    }
                    for row in experiments
                ],
                "models": [
                    {"id": row.id, "name": row.name, "created_at": row.created_at.isoformat()}
                    for row in models
                ],
                "inference": [
                    {
                        "id": row.id,
                        "name": row.name,
                        "status": row.status,
                        "created_at": row.created_at.isoformat(),
                    }
                    for row in inference
                ],
                "metrics": [
                    {
                        "id": row.id,
                        "subject_type": row.subject_type,
                        "subject_id": row.subject_id,
                        "values": row.metrics_json,
                    }
                    for row in metrics
                ],
            }

    def datasets(self) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            project = self._configured_project(session)
            rows = session.scalars(
                select(DatasetVersionModel)
                .where(DatasetVersionModel.project_id == project.id)
                .order_by(DatasetVersionModel.created_at.desc())
            ).all()
            return [
                {
                    "id": row.id,
                    "name": row.name,
                    "schema_version": row.schema_version,
                    "sha256": row.sha256,
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ]

    def runs(self) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            project = self._configured_project(session)
            rows = session.scalars(
                select(JobModel).where(JobModel.project_id == project.id).order_by(JobModel.created_at.desc())
            ).all()
            return [
                {
                    "id": row.id,
                    "kind": row.kind,
                    "status": row.status,
                    "spec": row.spec_json,
                    "error_message": row.error_message,
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ]

    def models(self) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            project = self._configured_project(session)
            rows = session.scalars(
                select(ModelVersionModel)
                .where(ModelVersionModel.project_id == project.id)
                .order_by(ModelVersionModel.created_at.desc())
            ).all()
            return [
                {
                    "id": row.id,
                    "name": row.name,
                    "parent_id": row.parent_id,
                    "experiment_run_id": row.experiment_run_id,
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ]
