from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from steel_platform.application.errors import ApplicationError, NotFoundError
from steel_platform.domain.annotations import AnnotationBox, ReviewState
from steel_platform.domain.ports import AnnotationCodec, ArtifactStore, UnitOfWork


@dataclass(frozen=True, slots=True)
class ReviewFilters:
    state: str | None = None
    class_id: int | None = None
    source_status: str | None = None
    search: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewRoundView:
    id: str
    project_id: str
    number: int
    kind: str
    name: str
    description: str
    target_count: int
    status: str
    per_class: int
    class_schema_id: str | None
    class_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReviewItemView:
    id: str
    round_id: str
    round_number: int
    image_asset_id: str
    filename: str
    expected_class_id: int
    expected_class_name: str
    source_status: str
    selection_reason: str
    state: str
    revision: int
    rank: int


@dataclass(frozen=True, slots=True)
class ReviewItemPage:
    items: tuple[ReviewItemView, ...]
    total: int


@dataclass(frozen=True, slots=True)
class ReviewItemDetail:
    id: str
    round_id: str
    image_asset_id: str
    filename: str
    expected_class_id: int
    expected_class_name: str
    class_names: tuple[str, ...]
    annotation_mode: str
    source_status: str
    selection_reason: str
    min_confidence: float | None
    max_confidence: float | None
    candidate_box_count: int
    state: str
    revision: int
    note: str
    boxes: tuple[AnnotationBox, ...]


class ReviewTaskQueryService:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        *,
        class_names: Sequence[str],
        artifact_store: ArtifactStore | None = None,
        annotation_codec: AnnotationCodec | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._class_names = tuple(class_names)
        self._artifact_store = artifact_store
        self._annotation_codec = annotation_codec

    def list_rounds(self, project_id: str) -> tuple[ReviewRoundView, ...]:
        with self._uow_factory() as uow:
            return tuple(
                self._round_view(uow, project_id, row)
                for row in uow.review_tasks.list_rounds(project_id)
            )

    def get_round(self, project_id: str, round_id: str) -> ReviewRoundView:
        with self._uow_factory() as uow:
            row = uow.review_tasks.get_round(project_id, round_id)
            if row is None:
                raise NotFoundError("复核任务不存在")
            return self._round_view(uow, project_id, row)

    def list_items(
        self,
        project_id: str,
        round_id: str,
        filters: ReviewFilters | None = None,
    ) -> ReviewItemPage:
        filters = filters or ReviewFilters()
        with self._uow_factory() as uow:
            review_round = uow.review_tasks.get_round(project_id, round_id)
            if review_round is None:
                raise NotFoundError("复核任务不存在")
            class_names = self._resolve_class_names(uow, project_id, review_round)
            rows = uow.review_tasks.list_items(project_id, round_id, filters)
            items = tuple(self._item_view(row, review_round.number, class_names) for row in rows)
            return ReviewItemPage(items=items, total=len(items))

    def get_item(self, project_id: str, round_id: str, item_id: str) -> ReviewItemDetail:
        with self._uow_factory() as uow:
            review_round = uow.review_tasks.get_round(project_id, round_id)
            if review_round is None:
                raise NotFoundError("复核任务不存在")
            item = uow.review_tasks.get_item(project_id, round_id, item_id)
            if item is None:
                raise NotFoundError("复核条目不存在")
            class_names = self._resolve_class_names(uow, project_id, review_round)
            project = uow.projects.get(project_id)
            annotation_policy = project.annotation_policy if project is not None else {}
            annotation_mode = (annotation_policy or {}).get("mode", "multi_class")
            enforce_expected_class = (
                annotation_mode == "single_class_locked"
            )
            draft = uow.review_tasks.get_draft(project_id, round_id, item_id)
            if draft is not None and item.state == ReviewState.DOUBTFUL.value:
                boxes = tuple(AnnotationBox(**box) for box in draft.boxes_json)
                note = draft.note
            else:
                boxes = self._read_boxes(
                    uow,
                    project_id,
                    item.current_revision_id or item.candidate_revision_id,
                    item.expected_class_id,
                    enforce_expected_class=enforce_expected_class,
                )
                note = item.note
            return ReviewItemDetail(
                id=item.id,
                round_id=item.round_id,
                image_asset_id=item.image_asset_id,
                filename=item.filename,
                expected_class_id=item.expected_class_id,
                expected_class_name=self._class_name(class_names, item.expected_class_id),
                class_names=class_names,
                annotation_mode=annotation_mode,
                source_status=item.source_status,
                selection_reason=item.selection_reason,
                min_confidence=item.min_confidence,
                max_confidence=item.max_confidence,
                candidate_box_count=item.box_count,
                state=item.state,
                revision=item.revision,
                note=note,
                boxes=boxes,
            )

    def _read_boxes(
        self,
        uow: UnitOfWork,
        project_id: str,
        revision_id: str | None,
        expected_class_id: int,
        *,
        enforce_expected_class: bool,
    ) -> tuple[AnnotationBox, ...]:
        if revision_id is None:
            return ()
        revision = uow.review_tasks.get_annotation(project_id, revision_id)
        if revision is None:
            raise NotFoundError("标签版本不存在")
        if self._artifact_store is None or self._annotation_codec is None:
            raise RuntimeError("artifact_store and annotation_codec are required to read annotations")
        with self._artifact_store.open(revision.storage_key) as stream:
            boxes = self._annotation_codec.decode(stream.read())
        if enforce_expected_class and any(box.class_id != expected_class_id for box in boxes):
            raise ApplicationError("class_mismatch", "候选框类别与文件前缀类别不一致", status_code=422)
        return boxes

    def _item_view(
        self,
        item: Any,
        round_number: int,
        class_names: tuple[str, ...],
    ) -> ReviewItemView:
        return ReviewItemView(
            id=item.id,
            round_id=item.round_id,
            round_number=round_number,
            image_asset_id=item.image_asset_id,
            filename=item.filename,
            expected_class_id=item.expected_class_id,
            expected_class_name=self._class_name(class_names, item.expected_class_id),
            source_status=item.source_status,
            selection_reason=item.selection_reason,
            state=item.state,
            revision=item.revision,
            rank=item.rank,
        )

    def _round_view(self, uow: UnitOfWork, project_id: str, row: Any) -> ReviewRoundView:
        class_names = self._resolve_class_names(uow, project_id, row)
        return ReviewRoundView(
            id=row.id,
            project_id=row.project_id,
            number=row.number,
            kind=row.kind,
            name=row.name,
            description=row.description,
            target_count=row.target_count,
            status=row.status,
            per_class=row.per_class,
            class_schema_id=row.class_schema_id,
            class_names=class_names,
        )

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

    @staticmethod
    def _class_name(class_names: tuple[str, ...], class_id: int) -> str:
        if class_id < 0:
            raise ApplicationError("class_mismatch", "复核条目的类别编号无效", status_code=422)
        try:
            return class_names[class_id]
        except IndexError as exc:
            raise ApplicationError(
                "class_mismatch",
                "复核条目的类别编号不属于任务类别模式",
                status_code=422,
            ) from exc
