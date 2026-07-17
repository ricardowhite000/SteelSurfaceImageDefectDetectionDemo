from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, BinaryIO, Protocol, Sequence

from steel_platform.domain.workspace import Collection, DataSource, ImportEntry, ImportSession, Project


@dataclass(frozen=True, slots=True)
class JobSpec:
    kind: str
    environment: str
    working_directory: str
    arguments: tuple[str, ...]
    input_asset_ids: tuple[str, ...]
    expected_outputs: tuple[str, ...]
    resource_hints: dict[str, Any]


class ReviewTask(Protocol):
    id: str
    project_id: str


class ReviewFilters(Protocol):
    pass


class Repository(Protocol):
    def get(self, entity_type: str, entity_id: str) -> Any | None: ...
    def add(self, entity: Any) -> None: ...


class ProjectRepository(Protocol):
    def get(self, project_id: str) -> Project | None: ...
    def list(self) -> Sequence[Project]: ...
    def add(self, project: Project) -> None: ...


class DataSourceRepository(Protocol):
    def get(self, project_id: str, data_source_id: str) -> DataSource | None: ...
    def list(self, project_id: str) -> Sequence[DataSource]: ...
    def add(self, project_id: str, data_source: DataSource) -> None: ...


class CollectionRepository(Protocol):
    def get(self, project_id: str, collection_id: str) -> Collection | None: ...
    def list(self, project_id: str, *, parent_id: str | None = None) -> Sequence[Collection]: ...
    def add(self, project_id: str, collection: Collection) -> None: ...


class ImportRepository(Protocol):
    def get_session(self, project_id: str, import_session_id: str) -> ImportSession | None: ...
    def list_sessions(self, project_id: str) -> Sequence[ImportSession]: ...
    def add_session(self, project_id: str, session: ImportSession) -> None: ...
    def list_entries(self, project_id: str, import_session_id: str) -> Sequence[ImportEntry]: ...
    def add_entry(self, project_id: str, entry: ImportEntry) -> None: ...


class ReviewTaskRepository(Protocol):
    def get_round(self, project_id: str, round_id: str) -> ReviewTask | None: ...
    def list_items(self, project_id: str, round_id: str, filters: ReviewFilters) -> Sequence[Any]: ...


class DirectoryPicker(Protocol):
    def pick_directory(self, *, title: str) -> str | None: ...


class UnitOfWork(AbstractContextManager, Protocol):
    repository: Repository
    projects: ProjectRepository
    data_sources: DataSourceRepository
    collections: CollectionRepository
    imports: ImportRepository
    review_tasks: ReviewTaskRepository
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class ArtifactStore(Protocol):
    def put_bytes(self, content: bytes, *, media_type: str) -> Any: ...
    def open(self, storage_key: str) -> BinaryIO: ...


class JobExecutor(Protocol):
    def prepare(self, spec: JobSpec) -> str: ...


class PredictorAdapter(Protocol):
    def predict(self, source_ids: Sequence[str], *, model_id: str, batch: int = 1) -> str: ...


class EventPublisher(Protocol):
    def publish(self, event_type: str, payload: dict[str, Any]) -> None: ...


class Telemetry(Protocol):
    def event(self, name: str, attributes: dict[str, Any]) -> None: ...
    def metric(self, name: str, value: float, attributes: dict[str, Any]) -> None: ...
