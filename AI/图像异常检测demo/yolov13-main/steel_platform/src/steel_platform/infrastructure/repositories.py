from __future__ import annotations

from collections.abc import Sequence
from typing import Any, overload

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from steel_platform.domain.workspace import (
    Collection,
    DataSource,
    ImportEntry,
    ImportSession,
    ImportStatus,
    Project,
    SourceMode,
    SourceStatus,
)
from steel_platform.infrastructure.models import (
    ClassSchemaModel,
    CollectionModel,
    ImportEntryModel,
    ImportSessionModel,
    ProjectModel,
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


def _source(model: SourceRootModel) -> DataSource:
    return DataSource(
        id=model.id,
        project_id=model.project_id,
        name=model.name,
        mode=SourceMode(model.mode),
        root_path=model.path,
        status=SourceStatus(model.status),
        revision=model.revision,
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
        status=ImportStatus(model.status),
        revision=model.revision,
    )


def _assert_project(project_id: str, entity_project_id: str) -> None:
    if project_id != entity_project_id:
        raise ValueError("entity project_id does not match repository project_id")


class SqlProjectRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, project_id: str) -> Project | None:
        model = self._session.scalar(select(ProjectModel).where(ProjectModel.id == project_id))
        return _project(model) if model is not None else None

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

    def add_project(self, name: str, schema_name: str, class_names: tuple[str, ...]) -> Project:
        project = ProjectModel(name=name)
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
                .where(SourceRootModel.project_id == project_id)
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
                revision=entity.revision,
            )
        )


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
            select(CollectionModel).where(CollectionModel.project_id == project_id).order_by(CollectionModel.name)
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
                size_bytes=0,
                media_type="application/octet-stream",
                status=entry.status.value,
                revision=entry.revision,
            )
        )


class SqlReviewTaskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_round(self, project_id: str, round_id: str) -> ReviewRoundModel | None:
        return self._session.scalar(
            select(ReviewRoundModel).where(
                ReviewRoundModel.project_id == project_id,
                ReviewRoundModel.id == round_id,
            )
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
        return list(self._session.scalars(statement.order_by(ReviewItemModel.rank)))
