from __future__ import annotations

from dataclasses import asdict
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from steel_platform.application.errors import ApplicationError, NotFoundError, RevisionConflictError
from steel_platform.domain.annotations import AnnotationBox, AnnotationDecision, ReviewState
from steel_platform.infrastructure.artifacts import ArtifactRef, LocalArtifactStore
from steel_platform.infrastructure.config import PlatformSettings
from steel_platform.infrastructure.database import make_engine
from steel_platform.infrastructure.models import (
    AnnotationRevisionModel,
    AssetModel,
    CandidatePredictionModel,
    DatasetVersionModel,
    ExperimentRunModel,
    InferenceRunModel,
    MetricSnapshotModel,
    ModelVersionModel,
    JobModel,
    DomainEventModel,
    IdempotencyRecordModel,
    OutboxEventModel,
    ProjectModel,
    ReviewDraftModel,
    ReviewItemModel,
    ReviewRoundModel,
)
from steel_platform.infrastructure.yolo import parse_yolo_text, serialize_yolo


def _box_dict(box: AnnotationBox) -> dict[str, int | float]:
    return asdict(box)


class ReviewService:
    def __init__(self, settings: PlatformSettings) -> None:
        self.settings = settings
        self.engine = make_engine(settings.database_url)
        self.store = LocalArtifactStore(settings.artifact_root)

    def _boxes_for_revision(
        self, session: Session, revision_id: str | None, *, expected_class_id: int
    ) -> tuple[AnnotationBox, ...]:
        if revision_id is None:
            return ()
        revision = session.get(AnnotationRevisionModel, revision_id)
        if revision is None:
            raise NotFoundError("标签版本不存在")
        path = self.store.resolve(ArtifactRef(revision.storage_key, revision.sha256, 0, "text/yolo"))
        boxes = parse_yolo_text(path.read_text(encoding="utf-8"), source=path)
        if any(box.class_id != expected_class_id for box in boxes):
            raise ApplicationError("class_mismatch", "候选框类别与文件前缀类别不一致", status_code=422)
        return boxes

    def list_queue(
        self,
        *,
        state: str | None = None,
        class_id: int | None = None,
        source_status: str | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        with Session(self.engine) as session:
            query = select(ReviewItemModel, ReviewRoundModel.number).join(
                ReviewRoundModel, ReviewItemModel.round_id == ReviewRoundModel.id
            )
            if state:
                query = query.where(ReviewItemModel.state == state)
            if class_id is not None:
                query = query.where(ReviewItemModel.expected_class_id == class_id)
            if source_status:
                query = query.where(ReviewItemModel.source_status == source_status)
            if search:
                query = query.where(ReviewItemModel.filename.contains(search))
            rows = session.execute(query.order_by(ReviewRoundModel.number.desc(), ReviewItemModel.rank)).all()
            return {
                "items": [
                    {
                        "id": item.id,
                        "round": round_number,
                        "image_asset_id": item.image_asset_id,
                        "filename": item.filename,
                        "expected_class_id": item.expected_class_id,
                        "expected_class_name": self.settings.classes[item.expected_class_id],
                        "source_status": item.source_status,
                        "selection_reason": item.selection_reason,
                        "state": item.state,
                        "revision": item.revision,
                        "rank": item.rank,
                    }
                    for item, round_number in rows
                ]
            }

    def get_item(self, item_id: str) -> dict[str, Any]:
        with Session(self.engine) as session:
            item = session.get(ReviewItemModel, item_id)
            if item is None:
                raise NotFoundError("复核条目不存在")
            draft = session.get(ReviewDraftModel, item_id)
            if draft is not None and item.state == ReviewState.DOUBTFUL:
                boxes = tuple(AnnotationBox(**box) for box in draft.boxes_json)
            else:
                boxes = self._boxes_for_revision(
                    session, item.current_revision_id or item.candidate_revision_id, expected_class_id=item.expected_class_id
                )
            return {
                "id": item.id,
                "image_asset_id": item.image_asset_id,
                "filename": item.filename,
                "expected_class_id": item.expected_class_id,
                "expected_class_name": self.settings.classes[item.expected_class_id],
                "source_status": item.source_status,
                "selection_reason": item.selection_reason,
                "min_confidence": item.min_confidence,
                "max_confidence": item.max_confidence,
                "candidate_box_count": item.box_count,
                "state": item.state,
                "revision": item.revision,
                "note": draft.note if draft is not None and item.state == ReviewState.DOUBTFUL else item.note,
                "boxes": [_box_dict(box) for box in boxes],
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
        if not idempotency_key.strip():
            raise ApplicationError("validation_error", "必须提供Idempotency-Key", status_code=422)
        scope = f"review-decision:{item_id}"
        with Session(self.engine) as session:
            prior = session.get(IdempotencyRecordModel, idempotency_key)
            if prior is not None:
                if prior.scope != scope:
                    raise ApplicationError("idempotency_conflict", "幂等键已用于其他资源", status_code=409)
                return prior.response_json
            item = session.get(ReviewItemModel, item_id)
            if item is None:
                raise NotFoundError("复核条目不存在")
            if item.revision != expected_revision:
                raise RevisionConflictError(expected_revision, item.revision)
            try:
                boxes = tuple(AnnotationBox(**box) for box in boxes_data)
                state = ReviewState(action)
                decision = AnnotationDecision(state, boxes, note, expected_revision)
            except (TypeError, ValueError) as exc:
                raise ApplicationError("validation_error", str(exc), status_code=422) from exc
            if any(box.class_id != item.expected_class_id for box in decision.boxes):
                raise ApplicationError("class_mismatch", "一张图片只能保存文件前缀对应的缺陷类别", status_code=422)

            current_revision_id: str | None = None
            if state in {ReviewState.ACCEPTED, ReviewState.CORRECTED}:
                content = serialize_yolo(decision.boxes).encode("utf-8")
                ref = self.store.put_bytes(content, media_type="text/yolo")
                revision = AnnotationRevisionModel(
                    project_id=self._project_id(session),
                    image_asset_id=item.image_asset_id,
                    parent_id=item.current_revision_id or item.candidate_revision_id,
                    origin="human",
                    decision=state.value,
                    storage_key=ref.storage_key,
                    sha256=ref.sha256,
                    box_count=len(decision.boxes),
                )
                session.add(revision)
                session.flush()
                current_revision_id = revision.id
                old_draft = session.get(ReviewDraftModel, item_id)
                if old_draft is not None:
                    session.delete(old_draft)
            elif state == ReviewState.DOUBTFUL:
                draft = session.get(ReviewDraftModel, item_id)
                values = [_box_dict(box) for box in decision.boxes]
                if draft is None:
                    session.add(ReviewDraftModel(item_id=item_id, boxes_json=values, note=note))
                else:
                    draft.boxes_json = values
                    draft.note = note

            next_revision = expected_revision + 1
            updated = session.execute(
                update(ReviewItemModel)
                .where(ReviewItemModel.id == item_id, ReviewItemModel.revision == expected_revision)
                .values(
                    state=state.value,
                    note=note,
                    current_revision_id=current_revision_id,
                    revision=next_revision,
                )
            )
            if updated.rowcount != 1:
                session.rollback()
                actual = session.get(ReviewItemModel, item_id)
                raise RevisionConflictError(expected_revision, actual.revision if actual else expected_revision + 1)
            replacement_id = None
            if state in {ReviewState.DOUBTFUL, ReviewState.EXCLUDED}:
                replacement_id = self._add_replacement(session, item)
            response = {
                "id": item.id,
                "state": state.value,
                "revision": next_revision,
                "annotation_revision_id": current_revision_id,
                "replacement_item_id": replacement_id,
            }
            event = DomainEventModel(
                project_id=self._project_id(session),
                event_type="annotation.reviewed",
                payload_json={"item_id": item.id, "state": state.value, "revision": next_revision},
            )
            session.add(event)
            session.flush()
            session.add(OutboxEventModel(domain_event_id=event.id))
            session.add(IdempotencyRecordModel(key=idempotency_key, scope=scope, response_json=response))
            session.commit()
            return response

    def _add_replacement(self, session: Session, item: ReviewItemModel) -> str | None:
        review_round = session.get(ReviewRoundModel, item.round_id)
        if review_round is None or review_round.kind != "training":
            return None
        used_assets = set(
            session.scalars(
                select(ReviewItemModel.image_asset_id).where(ReviewItemModel.round_id == item.round_id)
            ).all()
        )
        candidates = session.scalars(
            select(CandidatePredictionModel).where(
                CandidatePredictionModel.project_id == review_round.project_id,
                CandidatePredictionModel.expected_class_id == item.expected_class_id,
            )
        ).all()
        available = [candidate for candidate in candidates if candidate.image_asset_id not in used_assets]
        if not available:
            return None

        def priority(candidate: CandidatePredictionModel) -> tuple[int, float, str]:
            is_risk = candidate.source_status == "no_box" or "class_mismatch" in candidate.source_status
            confidence = candidate.min_confidence if candidate.min_confidence is not None else -1.0
            return (0 if is_risk else 1, confidence, candidate.filename)

        candidate = min(available, key=priority)
        max_rank = session.scalar(
            select(func.max(ReviewItemModel.rank)).where(ReviewItemModel.round_id == item.round_id)
        ) or 0
        replacement = ReviewItemModel(
            round_id=item.round_id,
            image_asset_id=candidate.image_asset_id,
            candidate_revision_id=candidate.annotation_revision_id,
            filename=candidate.filename,
            expected_class_id=candidate.expected_class_id,
            source_status=candidate.source_status,
            min_confidence=candidate.min_confidence,
            max_confidence=candidate.max_confidence,
            box_count=candidate.box_count,
            selection_reason="replacement",
            split_role=item.split_role,
            rank=max_rank + 1,
        )
        session.add(replacement)
        session.flush()
        return replacement.id

    @staticmethod
    def _project_id(session: Session) -> str:
        project_id = session.scalar(select(ProjectModel.id).limit(1))
        if project_id is None:
            raise NotFoundError("项目尚未初始化")
        return project_id

    def overview(self) -> dict[str, Any]:
        with Session(self.engine) as session:
            image_count = session.scalar(
                select(func.count()).select_from(AssetModel).where(AssetModel.kind == "image")
            ) or 0
            label_count = session.scalar(select(func.count()).select_from(AnnotationRevisionModel)) or 0
            current_round = session.scalar(select(ReviewRoundModel).order_by(ReviewRoundModel.number.desc(),ReviewRoundModel.created_at.desc()).limit(1))
            round_filter = ReviewItemModel.round_id == current_round.id if current_round else False
            states = dict(session.execute(select(ReviewItemModel.state,func.count()).where(round_filter).group_by(ReviewItemModel.state)).all()) if current_round else {}
            target = current_round.per_class * len(self.settings.classes) if current_round else 0
            valid_completed = states.get("accepted",0)+states.get("corrected",0)
            class_rows = session.execute(select(ReviewItemModel.expected_class_id,ReviewItemModel.state,func.count()).where(round_filter).group_by(ReviewItemModel.expected_class_id,ReviewItemModel.state)).all() if current_round else []
            by_class={name:{"target":current_round.per_class if current_round else 0,"states":{}} for name in self.settings.classes}
            for class_id,state_name,count in class_rows:by_class[self.settings.classes[class_id]]["states"][state_name]=count
            by_risk=dict(session.execute(select(ReviewItemModel.source_status,func.count()).where(round_filter).group_by(ReviewItemModel.source_status)).all()) if current_round else {}
            datasets = session.scalars(select(DatasetVersionModel).order_by(DatasetVersionModel.created_at.desc()).limit(5)).all()
            jobs = session.scalars(select(JobModel).order_by(JobModel.created_at.desc()).limit(8)).all()
            experiments = session.scalars(select(ExperimentRunModel).order_by(ExperimentRunModel.created_at.desc()).limit(5)).all()
            models = session.scalars(select(ModelVersionModel).order_by(ModelVersionModel.created_at.desc()).limit(5)).all()
            inference = session.scalars(select(InferenceRunModel).order_by(InferenceRunModel.created_at.desc()).limit(5)).all()
            metrics = session.scalars(select(MetricSnapshotModel).order_by(MetricSnapshotModel.created_at.desc()).limit(5)).all()
            return {
                "assets": {"images": image_count, "annotation_revisions": label_count},
                "review": {"round":current_round.number if current_round else None,"kind":current_round.kind if current_round else None,"target":target,"total":target,"completed":valid_completed,"pending":max(0,target-valid_completed),"item_count":sum(states.values()),"by_class":by_class,"by_risk":by_risk,**{state.value: states.get(state.value, 0) for state in ReviewState if state != ReviewState.PENDING}},
                "datasets": [{"id":row.id,"name":row.name,"created_at":row.created_at.isoformat()} for row in datasets],
                "runs": [{"id":row.id,"kind":row.kind,"status":row.status,"created_at":row.created_at.isoformat()} for row in jobs],
                "experiments": [{"id":row.id,"job_id":row.job_id,"dataset_version_id":row.dataset_version_id,"status":row.status,"created_at":row.created_at.isoformat()} for row in experiments],
                "models": [{"id":row.id,"name":row.name,"created_at":row.created_at.isoformat()} for row in models],
                "inference": [{"id":row.id,"name":row.name,"status":row.status,"created_at":row.created_at.isoformat()} for row in inference],
                "metrics": [{"id":row.id,"subject_type":row.subject_type,"subject_id":row.subject_id,"values":row.metrics_json} for row in metrics],
            }

    def datasets(self) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            rows=session.scalars(select(DatasetVersionModel).order_by(DatasetVersionModel.created_at.desc())).all()
            return [{"id":row.id,"name":row.name,"schema_version":row.schema_version,"sha256":row.sha256,"created_at":row.created_at.isoformat()} for row in rows]

    def runs(self) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            rows=session.scalars(select(JobModel).order_by(JobModel.created_at.desc())).all()
            return [{"id":row.id,"kind":row.kind,"status":row.status,"spec":row.spec_json,"error_message":row.error_message,"created_at":row.created_at.isoformat()} for row in rows]

    def models(self) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            rows=session.scalars(select(ModelVersionModel).order_by(ModelVersionModel.created_at.desc())).all()
            return [{"id":row.id,"name":row.name,"parent_id":row.parent_id,"experiment_run_id":row.experiment_run_id,"created_at":row.created_at.isoformat()} for row in rows]
