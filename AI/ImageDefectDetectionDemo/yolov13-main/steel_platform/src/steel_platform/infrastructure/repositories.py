from __future__ import annotations

from collections.abc import Sequence
import sqlite3
from typing import Any, overload

from sqlalchemy import Select, and_, delete, func, literal, select, update
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError, OperationalError

from steel_platform.domain.workspace import (
    AnnotationRevision,
    Asset,
    ClassSchema,
    Collection,
    ConcurrentAllocationError,
    DataSource,
    ExplorerResource,
    IdempotencyRecord,
    IdempotencyReservationConflict,
    ImportEntry,
    ImportEntryStatus,
    ImportSession,
    ImportStatus,
    Project,
    ResourceItem,
    SourceMode,
    SourceStatus,
)
from steel_platform.infrastructure.models import (
    AnnotationRevisionModel,
    AssetModel,
    CandidatePredictionModel,
    ClassSchemaModel,
    CollectionMemberModel,
    CollectionModel,
    DatasetMemberModel,
    DatasetVersionModel,
    DomainEventModel,
    IdempotencyRecordModel,
    ImportEntryModel,
    ImportSessionModel,
    InferenceRunModel,
    ModelVersionModel,
    ProjectModel,
    OutboxEventModel,
    ReviewDraftModel,
    ReviewItemModel,
    ReviewRoundModel,
    SourceRootModel,
)


def _project(model: ProjectModel) -> Project:
    return Project(
        id=model.id,
        name=model.name,
        class_schema_id=model.class_schema_id or "",
        revision=model.revision,
    )


def _class_schema(model: ClassSchemaModel) -> ClassSchema:
    return ClassSchema(id=model.id, name=model.name, names=model.names_json)


def _source(model: SourceRootModel) -> DataSource:
    return DataSource(
        id=model.id,
        project_id=model.project_id,
        name=model.name,
        mode=SourceMode(model.mode),
        root_path=model.path,
        status=SourceStatus(model.status),
        revision=model.revision,
        manifest_sha256=model.manifest_sha256,
    )


def _collection(model: CollectionModel) -> Collection:
    return Collection(
        id=model.id,
        project_id=model.project_id,
        name=model.name,
        parent_id=model.parent_id,
        revision=model.revision,
    )


def _import_session(model: ImportSessionModel) -> ImportSession:
    return ImportSession(
        id=model.id,
        project_id=model.project_id,
        data_source_id=model.data_source_id,
        collection_id=model.collection_id,
        status=ImportStatus(model.status),
        revision=model.revision,
    )


def _import_entry(model: ImportEntryModel) -> ImportEntry:
    return ImportEntry(
        id=model.id,
        project_id=model.project_id,
        import_session_id=model.import_session_id,
        relative_path=model.relative_path,
        status=ImportEntryStatus(model.status),
        revision=model.revision,
        size_bytes=model.size_bytes,
        media_type=model.media_type,
        expected_sha256=model.expected_sha256,
        actual_sha256=model.actual_sha256,
        storage_key=model.storage_key,
    )


def _asset(model: AssetModel) -> Asset:
    if model.relative_path is None:
        raise ValueError("registered asset is missing its relative path")
    return Asset(
        id=model.id,
        project_id=model.project_id,
        data_source_id=model.source_root_id,
        relative_path=model.relative_path,
        sha256=model.sha256,
        size_bytes=model.size_bytes,
        media_type=model.media_type,
        storage_key=model.storage_key,
    )


def _assert_project(project_id: str, entity_project_id: str) -> None:
    if project_id != entity_project_id:
        raise ValueError("entity project_id does not match repository project_id")


def _dbapi_sqlstate(error: IntegrityError | OperationalError) -> str | None:
    original = error.orig
    value = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    return value if isinstance(value, str) else None


def _is_retryable_concurrency_error(error: OperationalError) -> bool:
    if _dbapi_sqlstate(error) in {"40001", "40P01", "55P03"}:
        return True
    original = error.orig
    if not isinstance(original, sqlite3.OperationalError):
        return False
    code = getattr(original, "sqlite_errorcode", None)
    if isinstance(code, int):
        return code & 0xFF in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
    return str(original).strip().lower() in {"database is locked", "table is locked"}


def _is_unique_constraint_error(error: IntegrityError) -> bool:
    if _dbapi_sqlstate(error) == "23505":
        return True
    original = error.orig
    if not isinstance(original, sqlite3.IntegrityError):
        return False
    code = getattr(original, "sqlite_errorcode", None)
    return code in {
        sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY,
        sqlite3.SQLITE_CONSTRAINT_UNIQUE,
    }


class SqlProjectRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, project_id: str) -> Project | None:
        model = self._session.scalar(select(ProjectModel).where(ProjectModel.id == project_id))
        return _project(model) if model is not None else None

    def get_class_schema(self, project_id: str, schema_id: str) -> ClassSchema | None:
        model = self._session.scalar(
            select(ClassSchemaModel).where(
                ClassSchemaModel.project_id == project_id,
                ClassSchemaModel.id == schema_id,
            )
        )
        return _class_schema(model) if model is not None else None

    def list(self) -> Sequence[Project]:
        return [_project(model) for model in self._session.scalars(select(ProjectModel).order_by(ProjectModel.name))]

    def add(self, project: Project) -> None:
        self._session.add(
            ProjectModel(
                id=project.id,
                name=project.name,
                class_schema_id=project.class_schema_id or None,
                revision=project.revision,
            )
        )

    def add_project(
        self,
        name: str,
        schema_name: str,
        class_names: tuple[str, ...],
        *,
        project_id: str | None = None,
    ) -> Project:
        project = ProjectModel(id=project_id, name=name) if project_id is not None else ProjectModel(name=name)
        self._session.add(project)
        self._session.flush()
        schema = ClassSchemaModel(project_id=project.id, name=schema_name, version=1, names_json=class_names)
        self._session.add(schema)
        self._session.flush()
        project.class_schema_id = schema.id
        self._session.flush()
        return _project(project)


class SqlDataSourceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, project_id: str, data_source_id: str) -> DataSource | None:
        model = self._session.scalar(
            select(SourceRootModel).where(
                SourceRootModel.project_id == project_id,
                SourceRootModel.id == data_source_id,
            )
        )
        return _source(model) if model is not None else None

    def list(self, project_id: str) -> Sequence[DataSource]:
        return [
            _source(model)
            for model in self._session.scalars(
                select(SourceRootModel)
                .where(
                    SourceRootModel.project_id == project_id,
                    SourceRootModel.status != SourceStatus.IMPORTING.value,
                )
                .order_by(SourceRootModel.name)
            )
        ]

    @overload
    def add(self, data_source: DataSource) -> None: ...

    @overload
    def add(self, project_id: str, data_source: DataSource) -> None: ...

    def add(self, project_id: str | DataSource, data_source: DataSource | None = None) -> None:
        entity = project_id if isinstance(project_id, DataSource) else data_source
        if entity is None:
            raise TypeError("data_source is required")
        if isinstance(project_id, str):
            _assert_project(project_id, entity.project_id)
        self._session.add(
            SourceRootModel(
                id=entity.id,
                project_id=entity.project_id,
                name=entity.name,
                kind=entity.name,
                mode=entity.mode.value,
                path=entity.root_path,
                status=entity.status.value,
                manifest_sha256=entity.manifest_sha256,
                revision=entity.revision,
            )
        )

    def update_binding(
        self,
        project_id: str,
        data_source_id: str,
        *,
        root_path: str,
        status: str,
        manifest_sha256: str,
        expected_revision: int,
    ) -> DataSource | None:
        result = self._session.execute(
            update(SourceRootModel)
            .where(
                SourceRootModel.project_id == project_id,
                SourceRootModel.id == data_source_id,
                SourceRootModel.revision == expected_revision,
            )
            .values(
                path=root_path,
                status=status,
                manifest_sha256=manifest_sha256,
                last_verified_at=func.now(),
                revision=expected_revision + 1,
            )
        )
        if result.rowcount != 1:
            return None
        return self.get(project_id, data_source_id)

    def update_status(
        self,
        project_id: str,
        data_source_id: str,
        *,
        status: str,
        expected_revision: int,
    ) -> DataSource | None:
        result = self._session.execute(
            update(SourceRootModel)
            .where(
                SourceRootModel.project_id == project_id,
                SourceRootModel.id == data_source_id,
                SourceRootModel.revision == expected_revision,
            )
            .values(status=status, revision=expected_revision + 1)
        )
        if result.rowcount != 1:
            return None
        return self.get(project_id, data_source_id)


class SqlCollectionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, project_id: str, collection_id: str) -> Collection | None:
        model = self._session.scalar(
            select(CollectionModel).where(
                CollectionModel.project_id == project_id,
                CollectionModel.id == collection_id,
            )
        )
        return _collection(model) if model is not None else None

    def list(self, project_id: str, *, parent_id: str | None = None) -> Sequence[Collection]:
        statement: Select[tuple[CollectionModel]] = (
            select(CollectionModel)
            .where(
                CollectionModel.project_id == project_id,
                ~select(ImportSessionModel.id)
                .where(
                    ImportSessionModel.collection_id == CollectionModel.id,
                    ImportSessionModel.status != ImportStatus.SUCCEEDED.value,
                )
                .exists(),
            )
            .order_by(CollectionModel.name)
        )
        if parent_id is not None:
            statement = statement.where(CollectionModel.parent_id == parent_id)
        return [_collection(model) for model in self._session.scalars(statement)]

    @overload
    def add(self, collection: Collection) -> None: ...

    @overload
    def add(self, project_id: str, collection: Collection) -> None: ...

    def add(self, project_id: str | Collection, collection: Collection | None = None) -> None:
        entity = project_id if isinstance(project_id, Collection) else collection
        if entity is None:
            raise TypeError("collection is required")
        if isinstance(project_id, str):
            _assert_project(project_id, entity.project_id)
        self._session.add(
            CollectionModel(
                id=entity.id,
                project_id=entity.project_id,
                name=entity.name,
                parent_id=entity.parent_id,
                revision=entity.revision,
            )
        )

    def rename(
        self,
        project_id: str,
        collection_id: str,
        name: str,
        expected_revision: int,
    ) -> Collection | None:
        result = self._session.execute(
            update(CollectionModel)
            .where(
                CollectionModel.project_id == project_id,
                CollectionModel.id == collection_id,
                CollectionModel.revision == expected_revision,
            )
            .values(name=name, revision=expected_revision + 1)
        )
        if result.rowcount != 1:
            return None
        return self.get(project_id, collection_id)

    def bump_revision(
        self,
        project_id: str,
        collection_id: str,
        expected_revision: int,
    ) -> Collection | None:
        result = self._session.execute(
            update(CollectionModel)
            .where(
                CollectionModel.project_id == project_id,
                CollectionModel.id == collection_id,
                CollectionModel.revision == expected_revision,
            )
            .values(revision=expected_revision + 1)
        )
        if result.rowcount != 1:
            return None
        return self.get(project_id, collection_id)

    def list_members(self, project_id: str, collection_id: str) -> Sequence[str]:
        return tuple(
            self._session.scalars(
                select(CollectionMemberModel.asset_id)
                .join(CollectionModel, CollectionMemberModel.collection_id == CollectionModel.id)
                .join(AssetModel, CollectionMemberModel.asset_id == AssetModel.id)
                .where(
                    CollectionModel.project_id == project_id,
                    CollectionModel.id == collection_id,
                    AssetModel.project_id == project_id,
                )
                .order_by(CollectionMemberModel.asset_id)
            )
        )

    def add_members(self, project_id: str, collection_id: str, asset_ids: Sequence[str]) -> None:
        existing = set(self.list_members(project_id, collection_id))
        self._session.add_all(
            CollectionMemberModel(collection_id=collection_id, asset_id=asset_id)
            for asset_id in asset_ids
            if asset_id not in existing
        )

    def remove_member(self, project_id: str, collection_id: str, asset_id: str) -> None:
        self._session.execute(
            delete(CollectionMemberModel).where(
                CollectionMemberModel.collection_id == collection_id,
                CollectionMemberModel.asset_id == asset_id,
                CollectionMemberModel.collection_id.in_(
                    select(CollectionModel.id).where(CollectionModel.project_id == project_id)
                ),
            )
        )


class SqlImportRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_session(self, project_id: str, import_session_id: str) -> ImportSession | None:
        model = self._session.scalar(
            select(ImportSessionModel).where(
                ImportSessionModel.project_id == project_id,
                ImportSessionModel.id == import_session_id,
            )
        )
        return _import_session(model) if model is not None else None

    def list_sessions(self, project_id: str) -> Sequence[ImportSession]:
        return [
            _import_session(model)
            for model in self._session.scalars(
                select(ImportSessionModel)
                .where(ImportSessionModel.project_id == project_id)
                .order_by(ImportSessionModel.created_at)
            )
        ]

    def add_session(self, project_id: str, session: ImportSession) -> None:
        _assert_project(project_id, session.project_id)
        self._session.add(
            ImportSessionModel(
                id=session.id,
                project_id=session.project_id,
                data_source_id=session.data_source_id,
                collection_id=session.collection_id,
                status=session.status.value,
                revision=session.revision,
            )
        )

    def list_entries(self, project_id: str, import_session_id: str) -> Sequence[ImportEntry]:
        return [
            _import_entry(model)
            for model in self._session.scalars(
                select(ImportEntryModel).where(
                    ImportEntryModel.project_id == project_id,
                    ImportEntryModel.import_session_id == import_session_id,
                )
            )
        ]

    def add_entry(self, project_id: str, entry: ImportEntry) -> None:
        _assert_project(project_id, entry.project_id)
        self._session.add(
            ImportEntryModel(
                id=entry.id,
                project_id=entry.project_id,
                import_session_id=entry.import_session_id,
                relative_path=entry.relative_path,
                size_bytes=entry.size_bytes,
                media_type=entry.media_type,
                expected_sha256=entry.expected_sha256,
                actual_sha256=entry.actual_sha256,
                storage_key=entry.storage_key,
                status=entry.status.value,
                revision=entry.revision,
            )
        )

    def get_entry(self, project_id: str, import_session_id: str, entry_id: str) -> ImportEntry | None:
        model = self._session.scalar(
            select(ImportEntryModel).where(
                ImportEntryModel.project_id == project_id,
                ImportEntryModel.import_session_id == import_session_id,
                ImportEntryModel.id == entry_id,
            )
        )
        return _import_entry(model) if model is not None else None

    def find_entry(
        self,
        project_id: str,
        import_session_id: str,
        relative_path: str,
    ) -> ImportEntry | None:
        model = self._session.scalar(
            select(ImportEntryModel).where(
                ImportEntryModel.project_id == project_id,
                ImportEntryModel.import_session_id == import_session_id,
                ImportEntryModel.relative_path == relative_path,
            )
        )
        return _import_entry(model) if model is not None else None

    def mark_verified(
        self,
        project_id: str,
        import_session_id: str,
        entry_id: str,
        *,
        actual_sha256: str,
        storage_key: str | None,
    ) -> ImportEntry | None:
        result = self._session.execute(
            update(ImportEntryModel)
            .where(
                ImportEntryModel.project_id == project_id,
                ImportEntryModel.import_session_id == import_session_id,
                ImportEntryModel.id == entry_id,
                ImportEntryModel.status != ImportEntryStatus.VERIFIED.value,
            )
            .values(
                actual_sha256=actual_sha256,
                storage_key=storage_key,
                status=ImportEntryStatus.VERIFIED.value,
                revision=ImportEntryModel.revision + 1,
            )
        )
        if result.rowcount != 1:
            return self.get_entry(project_id, import_session_id, entry_id)
        return self.get_entry(project_id, import_session_id, entry_id)

    def transition_session(
        self,
        project_id: str,
        import_session_id: str,
        *,
        allowed: Sequence[str],
        target: str,
    ) -> ImportSession | None:
        result = self._session.execute(
            update(ImportSessionModel)
            .where(
                ImportSessionModel.project_id == project_id,
                ImportSessionModel.id == import_session_id,
                ImportSessionModel.status.in_(allowed),
            )
            .values(status=target, revision=ImportSessionModel.revision + 1)
        )
        if result.rowcount != 1:
            return None
        return self.get_session(project_id, import_session_id)


class SqlAssetRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, project_id: str, asset_id: str) -> Asset | None:
        model = self._session.scalar(
            select(AssetModel).where(AssetModel.project_id == project_id, AssetModel.id == asset_id)
        )
        return _asset(model) if model is not None else None

    def list_by_source(self, project_id: str, data_source_id: str) -> Sequence[Asset]:
        return tuple(
            _asset(model)
            for model in self._session.scalars(
                select(AssetModel)
                .where(
                    AssetModel.project_id == project_id,
                    AssetModel.source_root_id == data_source_id,
                )
                .order_by(AssetModel.relative_path)
            )
        )

    def add(self, project_id: str, asset: Asset) -> None:
        _assert_project(project_id, asset.project_id)
        self._session.add(
            AssetModel(
                id=asset.id,
                project_id=asset.project_id,
                source_root_id=asset.data_source_id,
                kind="image",
                relative_path=asset.relative_path,
                storage_key=asset.storage_key,
                sha256=asset.sha256,
                size_bytes=asset.size_bytes,
                media_type=asset.media_type,
            )
        )


class SqlReviewTaskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_rounds(self, project_id: str) -> Sequence[ReviewRoundModel]:
        return list(
            self._session.scalars(
                select(ReviewRoundModel)
                .where(ReviewRoundModel.project_id == project_id)
                .order_by(ReviewRoundModel.number, ReviewRoundModel.created_at)
            )
        )

    def get_round(self, project_id: str, round_id: str) -> ReviewRoundModel | None:
        return self._session.scalar(
            select(ReviewRoundModel).where(
                ReviewRoundModel.project_id == project_id,
                ReviewRoundModel.id == round_id,
            )
        )

    @staticmethod
    def _scoped_round_ids(project_id: str, round_id: str) -> Select[tuple[str]]:
        return select(ReviewRoundModel.id).where(
            ReviewRoundModel.project_id == project_id,
            ReviewRoundModel.id == round_id,
        )

    def list_items(self, project_id: str, round_id: str, filters: Any = None) -> Sequence[ReviewItemModel]:
        statement = select(ReviewItemModel).join(
            ReviewRoundModel,
            ReviewItemModel.round_id == ReviewRoundModel.id,
        ).where(
            ReviewRoundModel.project_id == project_id,
            ReviewRoundModel.id == round_id,
        )
        if state := getattr(filters, "state", None):
            statement = statement.where(ReviewItemModel.state == state)
        if (class_id := getattr(filters, "class_id", None)) is not None:
            statement = statement.where(ReviewItemModel.expected_class_id == class_id)
        if source_status := getattr(filters, "source_status", None):
            statement = statement.where(ReviewItemModel.source_status == source_status)
        if search := getattr(filters, "search", None):
            statement = statement.where(ReviewItemModel.filename.contains(search))
        return list(self._session.scalars(statement.order_by(ReviewItemModel.rank)))

    def get_item(self, project_id: str, round_id: str, item_id: str) -> ReviewItemModel | None:
        return self._session.scalar(
            select(ReviewItemModel)
            .join(ReviewRoundModel, ReviewItemModel.round_id == ReviewRoundModel.id)
            .where(
                ReviewRoundModel.project_id == project_id,
                ReviewRoundModel.id == round_id,
                ReviewItemModel.id == item_id,
            )
        )

    def get_draft(self, project_id: str, round_id: str, item_id: str) -> Any | None:
        return self._session.scalar(
            select(ReviewDraftModel)
            .join(ReviewItemModel, ReviewDraftModel.item_id == ReviewItemModel.id)
            .join(ReviewRoundModel, ReviewItemModel.round_id == ReviewRoundModel.id)
            .where(
                ReviewRoundModel.project_id == project_id,
                ReviewRoundModel.id == round_id,
                ReviewItemModel.id == item_id,
            )
        )

    def get_annotation(self, project_id: str, revision_id: str) -> Any | None:
        return self._session.scalar(
            select(AnnotationRevisionModel).where(
                AnnotationRevisionModel.project_id == project_id,
                AnnotationRevisionModel.id == revision_id,
            )
        )

    def add_annotation_revision(
        self,
        project_id: str,
        round_id: str,
        item_id: str,
        *,
        parent_id: str | None,
        decision: str,
        storage_key: str,
        sha256: str,
        box_count: int,
    ) -> str:
        item = self.get_item(project_id, round_id, item_id)
        if item is None:
            raise ValueError("review item does not belong to project and round")
        revision = AnnotationRevisionModel(
            project_id=project_id,
            image_asset_id=item.image_asset_id,
            parent_id=parent_id,
            origin="human",
            decision=decision,
            storage_key=storage_key,
            sha256=sha256,
            box_count=box_count,
        )
        self._session.add(revision)
        self._session.flush()
        return revision.id

    def upsert_draft(
        self,
        project_id: str,
        round_id: str,
        item_id: str,
        *,
        boxes: Sequence[dict[str, int | float]],
        note: str,
    ) -> None:
        if self.get_item(project_id, round_id, item_id) is None:
            raise ValueError("review item does not belong to project and round")
        draft = self._session.get(ReviewDraftModel, item_id)
        values = list(boxes)
        if draft is None:
            self._session.add(ReviewDraftModel(item_id=item_id, boxes_json=values, note=note))
        else:
            draft.boxes_json = values
            draft.note = note
        self._session.flush()

    def delete_draft(self, project_id: str, round_id: str, item_id: str) -> None:
        draft = self.get_draft(project_id, round_id, item_id)
        if draft is not None:
            self._session.delete(draft)

    def update_item_decision(
        self,
        project_id: str,
        round_id: str,
        item_id: str,
        *,
        expected_revision: int,
        state: str,
        note: str,
        current_revision_id: str | None,
    ) -> ReviewItemModel | None:
        result = self._session.execute(
            update(ReviewItemModel)
            .where(
                ReviewItemModel.id == item_id,
                ReviewItemModel.round_id == round_id,
                ReviewItemModel.revision == expected_revision,
                ReviewItemModel.round_id.in_(self._scoped_round_ids(project_id, round_id)),
            )
            .values(
                state=state,
                note=note,
                current_revision_id=current_revision_id,
                revision=expected_revision + 1,
            )
        )
        if result.rowcount != 1:
            return None
        return self.get_item(project_id, round_id, item_id)

    def add_replacement(self, project_id: str, round_id: str, item_id: str) -> str | None:
        review_round = self._session.scalar(
            select(ReviewRoundModel)
            .where(
                ReviewRoundModel.project_id == project_id,
                ReviewRoundModel.id == round_id,
            )
            .with_for_update()
        )
        if review_round is None:
            return None
        # Re-read the item only after the round lock so quota, membership, and
        # candidate selection all observe one serialized round state.
        item = self.get_item(project_id, round_id, item_id)
        if item is None or review_round.kind != "training":
            return None
        class_quota = self._session.scalar(
            select(func.count())
            .select_from(ReviewItemModel)
            .where(
                ReviewItemModel.round_id == round_id,
                ReviewItemModel.round_id.in_(self._scoped_round_ids(project_id, round_id)),
                ReviewItemModel.expected_class_id == item.expected_class_id,
                ReviewItemModel.selection_reason != "replacement",
            )
        ) or 0
        active_count = self._session.scalar(
            select(func.count())
            .select_from(ReviewItemModel)
            .where(
                ReviewItemModel.round_id == round_id,
                ReviewItemModel.round_id.in_(self._scoped_round_ids(project_id, round_id)),
                ReviewItemModel.expected_class_id == item.expected_class_id,
                ReviewItemModel.state.in_(("pending", "accepted", "corrected")),
            )
        ) or 0
        if active_count >= class_quota:
            return None
        used_assets = select(ReviewItemModel.image_asset_id).where(
            ReviewItemModel.round_id == round_id,
            ReviewItemModel.round_id.in_(self._scoped_round_ids(project_id, round_id)),
        )
        candidates = list(
            self._session.scalars(
                select(CandidatePredictionModel)
                .join(AssetModel, CandidatePredictionModel.image_asset_id == AssetModel.id)
                .where(
                    CandidatePredictionModel.project_id == project_id,
                    AssetModel.project_id == project_id,
                    CandidatePredictionModel.expected_class_id == item.expected_class_id,
                    ~CandidatePredictionModel.image_asset_id.in_(used_assets),
                )
            )
        )
        if not candidates:
            return None

        def priority(candidate: CandidatePredictionModel) -> tuple[int, float, str]:
            risky = candidate.source_status == "no_box" or "class_mismatch" in candidate.source_status
            confidence = candidate.min_confidence if candidate.min_confidence is not None else -1.0
            return (0 if risky else 1, confidence, candidate.filename)

        candidate = min(candidates, key=priority)
        max_rank = self._session.scalar(
            select(func.max(ReviewItemModel.rank)).where(
                ReviewItemModel.round_id == round_id,
                ReviewItemModel.round_id.in_(self._scoped_round_ids(project_id, round_id)),
            )
        ) or 0
        replacement = ReviewItemModel(
            round_id=round_id,
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
        try:
            with self._session.begin_nested():
                self._session.add(replacement)
                self._session.flush()
        except IntegrityError as exc:
            if not _is_unique_constraint_error(exc):
                raise
            raise ConcurrentAllocationError("review replacement") from exc
        except OperationalError as exc:
            if not _is_retryable_concurrency_error(exc):
                raise
            raise ConcurrentAllocationError("review replacement") from exc
        return replacement.id

    def progress(self, project_id: str, round_id: str) -> dict[str, int]:
        if self.get_round(project_id, round_id) is None:
            return {}
        counts = dict(
            self._session.execute(
                select(ReviewItemModel.state, func.count())
                .where(
                    ReviewItemModel.round_id == round_id,
                    ReviewItemModel.round_id.in_(self._scoped_round_ids(project_id, round_id)),
                )
                .group_by(ReviewItemModel.state)
            ).all()
        )
        counts["total"] = sum(counts.values())
        counts["completed"] = counts.get("accepted", 0) + counts.get("corrected", 0)
        for state in ("pending", "accepted", "corrected", "doubtful", "excluded"):
            counts.setdefault(state, 0)
        return counts

    def next_pending_item_id(self, project_id: str, round_id: str) -> str | None:
        if self.get_round(project_id, round_id) is None:
            return None
        return self._session.scalar(
            select(ReviewItemModel.id)
            .where(
                ReviewItemModel.round_id == round_id,
                ReviewItemModel.round_id.in_(self._scoped_round_ids(project_id, round_id)),
                ReviewItemModel.state == "pending",
            )
            .order_by(ReviewItemModel.rank)
            .limit(1)
        )

    def set_round_completed(self, project_id: str, round_id: str, completed: bool) -> None:
        self._session.execute(
            update(ReviewRoundModel)
            .where(ReviewRoundModel.project_id == project_id, ReviewRoundModel.id == round_id)
            .values(status="completed" if completed else "active", completed_at=func.now() if completed else None)
        )

    def add_review_event(
        self,
        project_id: str,
        round_id: str,
        item_id: str,
        *,
        state: str,
        revision: int,
    ) -> None:
        if self.get_item(project_id, round_id, item_id) is None:
            raise ValueError("review item does not belong to project and round")
        event = DomainEventModel(
            project_id=project_id,
            event_type="annotation.reviewed",
            payload_json={
                "round_id": round_id,
                "item_id": item_id,
                "state": state,
                "revision": revision,
            },
        )
        self._session.add(event)
        self._session.flush()
        self._session.add(OutboxEventModel(domain_event_id=event.id))

    def create_from_collection(self, project_id: str, collection_id: str, sample_size: int) -> str:
        assets = list(
            self._session.scalars(
                select(AssetModel)
                .join(CollectionMemberModel, CollectionMemberModel.asset_id == AssetModel.id)
                .join(CollectionModel, CollectionModel.id == CollectionMemberModel.collection_id)
                .where(
                    CollectionModel.project_id == project_id,
                    CollectionModel.id == collection_id,
                    AssetModel.project_id == project_id,
                )
                .order_by(AssetModel.id)
                .limit(sample_size)
            )
        )
        number = (
            self._session.scalar(
                select(func.max(ReviewRoundModel.number)).where(ReviewRoundModel.project_id == project_id)
            )
            or 0
        ) + 1
        project = self._session.scalar(select(ProjectModel).where(ProjectModel.id == project_id))
        review_round = ReviewRoundModel(
            project_id=project_id,
            number=number,
            name=f"collection review {number}",
            source_collection_id=collection_id,
            class_schema_id=project.class_schema_id if project is not None else None,
            target_count=len(assets),
            per_class=sample_size,
        )
        self._session.add(review_round)
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise ConcurrentAllocationError("review round number") from exc
        self._session.add_all(
            ReviewItemModel(
                round_id=review_round.id,
                image_asset_id=asset.id,
                filename=asset.relative_path or asset.id,
                expected_class_id=0,
                source_status="collection",
                selection_reason="collection",
                split_role="review",
                rank=rank,
            )
            for rank, asset in enumerate(assets, start=1)
        )
        return review_round.id


class SqlExplorerRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def asset_exists(self, project_id: str, asset_id: str) -> bool:
        return self._session.scalar(
            select(func.count()).select_from(AssetModel).where(
                AssetModel.project_id == project_id,
                AssetModel.id == asset_id,
            )
        ) == 1

    def list_resources(self, project_id: str) -> Sequence[ExplorerResource]:
        resources: list[ExplorerResource] = []
        source_rows = self._session.execute(
            select(SourceRootModel, func.count(AssetModel.id))
            .outerjoin(
                AssetModel,
                and_(
                    AssetModel.source_root_id == SourceRootModel.id,
                    AssetModel.project_id == project_id,
                ),
            )
            .where(SourceRootModel.project_id == project_id)
            .where(SourceRootModel.status != SourceStatus.IMPORTING.value)
            .group_by(SourceRootModel.id)
            .order_by(SourceRootModel.name)
        )
        resources.extend(
            ExplorerResource(row.id, "source", row.name, count, row.status)
            for row, count in source_rows
        )
        round_rows = self._session.execute(
            select(ReviewRoundModel, func.count(ReviewItemModel.id))
            .outerjoin(ReviewItemModel, ReviewItemModel.round_id == ReviewRoundModel.id)
            .where(ReviewRoundModel.project_id == project_id)
            .group_by(ReviewRoundModel.id)
            .order_by(ReviewRoundModel.number)
        )
        resources.extend(
            ExplorerResource(row.id, "review_round", row.name, count, row.status)
            for row, count in round_rows
        )
        dataset_rows = self._session.execute(
            select(DatasetVersionModel, func.count(DatasetMemberModel.id))
            .outerjoin(DatasetMemberModel, DatasetMemberModel.dataset_version_id == DatasetVersionModel.id)
            .where(DatasetVersionModel.project_id == project_id)
            .group_by(DatasetVersionModel.id)
            .order_by(DatasetVersionModel.name)
        )
        resources.extend(
            ExplorerResource(row.id, "dataset", row.name, count, "ready")
            for row, count in dataset_rows
        )
        resources.extend(
            ExplorerResource(row.id, "model", row.name, 0, "ready")
            for row in self._session.scalars(
                select(ModelVersionModel)
                .where(ModelVersionModel.project_id == project_id)
                .order_by(ModelVersionModel.name)
            )
        )
        inference_rows = self._session.execute(
            select(InferenceRunModel, func.count(CandidatePredictionModel.id))
            .outerjoin(
                CandidatePredictionModel,
                and_(
                    CandidatePredictionModel.inference_run_id == InferenceRunModel.id,
                    CandidatePredictionModel.project_id == project_id,
                ),
            )
            .where(InferenceRunModel.project_id == project_id)
            .group_by(InferenceRunModel.id)
            .order_by(InferenceRunModel.name)
        )
        resources.extend(
            ExplorerResource(row.id, "inference", row.name, count, row.status)
            for row, count in inference_rows
        )
        return resources


class SqlResourceBrowserRepository:
    """Project-scoped read model for file-like resource browsing.

    The application service performs filtering, sorting and paging so all
    resource kinds share one deterministic contract.  A project currently has
    at most a few thousand assets, making this intentionally simple read model
    appropriate for the local demo while keeping the application independent
    from SQLAlchemy.
    """

    _KINDS = {"source", "collection", "review_round", "dataset", "model", "inference"}

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_resource(
        self, project_id: str, resource_type: str, resource_id: str
    ) -> ExplorerResource | None:
        if resource_type not in self._KINDS:
            return None
        if resource_type == "collection":
            row = self._session.scalar(
                select(CollectionModel).where(
                    CollectionModel.project_id == project_id,
                    CollectionModel.id == resource_id,
                )
            )
            if row is None:
                return None
            count = self._session.scalar(
                select(func.count()).select_from(CollectionMemberModel).where(
                    CollectionMemberModel.collection_id == row.id
                )
            ) or 0
            return ExplorerResource(row.id, "collection", row.name, count, "available")
        return next(
            (
                row
                for row in SqlExplorerRepository(self._session).list_resources(project_id)
                if row.type == resource_type and row.id == resource_id
            ),
            None,
        )

    @staticmethod
    def _item(
        asset: AssetModel,
        *,
        status: str,
        source_name: str | None,
        context_revision_id: str | None = None,
    ) -> ResourceItem:
        return ResourceItem(
            id=asset.id,
            asset_id=asset.id,
            name=asset.relative_path or asset.storage_key or asset.id,
            item_type=asset.kind,
            media_type=asset.media_type,
            size_bytes=asset.size_bytes,
            created_at=asset.created_at,
            status=status,
            source_name=source_name,
            context_revision_id=context_revision_id,
        )

    def list_items(
        self, project_id: str, resource_type: str, resource_id: str
    ) -> Sequence[ResourceItem]:
        if self.get_resource(project_id, resource_type, resource_id) is None:
            return ()
        if resource_type == "model":
            model = self._session.scalar(
                select(ModelVersionModel).where(
                    ModelVersionModel.project_id == project_id,
                    ModelVersionModel.id == resource_id,
                )
            )
            if model is None:
                return ()
            keys = (("weights", model.weights_key), ("manifest", model.manifest_key))
            return tuple(
                ResourceItem(
                    id=f"{model.id}:{kind}", asset_id=None,
                    name=key.rsplit("/", 1)[-1], item_type=kind,
                    media_type="application/octet-stream" if kind == "weights" else "application/json",
                    size_bytes=0, created_at=model.created_at, status="ready",
                )
                for kind, key in keys if key
            )

        source_name = SourceRootModel.name
        if resource_type == "source":
            statement = (
                select(AssetModel, source_name, literal(None).label("revision_id"), SourceRootModel.status)
                .join(SourceRootModel, SourceRootModel.id == AssetModel.source_root_id)
                .where(
                    AssetModel.project_id == project_id,
                    SourceRootModel.project_id == project_id,
                    SourceRootModel.id == resource_id,
                )
            )
        elif resource_type == "collection":
            statement = (
                select(AssetModel, source_name, literal(None).label("revision_id"), literal("ready"))
                .join(CollectionMemberModel, CollectionMemberModel.asset_id == AssetModel.id)
                .join(CollectionModel, CollectionModel.id == CollectionMemberModel.collection_id)
                .outerjoin(SourceRootModel, SourceRootModel.id == AssetModel.source_root_id)
                .where(
                    AssetModel.project_id == project_id,
                    CollectionModel.project_id == project_id,
                    CollectionModel.id == resource_id,
                )
            )
        elif resource_type == "review_round":
            statement = (
                select(
                    AssetModel, source_name,
                    func.coalesce(ReviewItemModel.current_revision_id, ReviewItemModel.candidate_revision_id),
                    ReviewItemModel.state,
                )
                .join(ReviewItemModel, ReviewItemModel.image_asset_id == AssetModel.id)
                .join(ReviewRoundModel, ReviewRoundModel.id == ReviewItemModel.round_id)
                .outerjoin(SourceRootModel, SourceRootModel.id == AssetModel.source_root_id)
                .where(
                    AssetModel.project_id == project_id,
                    ReviewRoundModel.project_id == project_id,
                    ReviewRoundModel.id == resource_id,
                )
            )
        elif resource_type == "dataset":
            statement = (
                select(AssetModel, source_name, DatasetMemberModel.annotation_revision_id, DatasetMemberModel.split)
                .join(DatasetMemberModel, DatasetMemberModel.image_asset_id == AssetModel.id)
                .join(DatasetVersionModel, DatasetVersionModel.id == DatasetMemberModel.dataset_version_id)
                .outerjoin(SourceRootModel, SourceRootModel.id == AssetModel.source_root_id)
                .where(
                    AssetModel.project_id == project_id,
                    DatasetVersionModel.project_id == project_id,
                    DatasetVersionModel.id == resource_id,
                )
            )
        else:
            statement = (
                select(AssetModel, source_name, CandidatePredictionModel.annotation_revision_id, CandidatePredictionModel.source_status)
                .join(CandidatePredictionModel, CandidatePredictionModel.image_asset_id == AssetModel.id)
                .join(InferenceRunModel, InferenceRunModel.id == CandidatePredictionModel.inference_run_id)
                .outerjoin(SourceRootModel, SourceRootModel.id == AssetModel.source_root_id)
                .where(
                    AssetModel.project_id == project_id,
                    CandidatePredictionModel.project_id == project_id,
                    InferenceRunModel.project_id == project_id,
                    InferenceRunModel.id == resource_id,
                )
            )
        return tuple(
            self._item(
                asset,
                status=str(status or "ready"),
                source_name=str(name) if name is not None else None,
                context_revision_id=str(revision_id) if revision_id is not None else None,
            )
            for asset, name, revision_id, status in self._session.execute(statement)
        )

    def get_item(
        self, project_id: str, resource_type: str, resource_id: str, asset_id: str
    ) -> ResourceItem | None:
        return next(
            (item for item in self.list_items(project_id, resource_type, resource_id) if item.asset_id == asset_id),
            None,
        )

    def list_revisions(self, project_id: str, asset_id: str) -> Sequence[AnnotationRevision]:
        return tuple(
            AnnotationRevision(
                id=row.id, asset_id=row.image_asset_id, parent_id=row.parent_id,
                origin=row.origin, decision=row.decision, storage_key=row.storage_key,
                sha256=row.sha256, box_count=row.box_count, created_at=row.created_at,
            )
            for row in self._session.scalars(
                select(AnnotationRevisionModel).where(
                    AnnotationRevisionModel.project_id == project_id,
                    AnnotationRevisionModel.image_asset_id == asset_id,
                ).order_by(AnnotationRevisionModel.created_at.desc(), AnnotationRevisionModel.id)
            )
        )


class SqlIdempotencyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, key: str) -> IdempotencyRecord | None:
        model = self._session.get(IdempotencyRecordModel, key)
        if model is None:
            return None
        return IdempotencyRecord(model.key, model.scope, dict(model.response_json))

    def reserve(self, record: IdempotencyRecord) -> None:
        self._session.add(
            IdempotencyRecordModel(
                key=record.key,
                scope=record.scope,
                response_json=record.response,
            )
        )
        try:
            self._session.flush()
        except IntegrityError as exc:
            if not _is_unique_constraint_error(exc):
                raise
            raise IdempotencyReservationConflict(record.key) from exc
        except OperationalError as exc:
            if not _is_retryable_concurrency_error(exc):
                raise
            raise IdempotencyReservationConflict(record.key) from exc

    def set_response(self, key: str, response: dict[str, object]) -> None:
        model = self._session.get(IdempotencyRecordModel, key)
        if model is None:
            raise RuntimeError("idempotency key must be reserved before setting its response")
        model.response_json = response
        self._session.flush()
