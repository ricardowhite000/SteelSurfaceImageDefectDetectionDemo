from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, BinaryIO, Protocol, Sequence

from steel_platform.domain.workspace import (
    Asset,
    Collection,
    DataSource,
    ExplorerResource,
    IdempotencyRecord,
    ImportEntry,
    ImportSession,
    ManifestEntry,
    Project,
)


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
    def add_project(
        self,
        name: str,
        schema_name: str,
        class_names: tuple[str, ...],
        *,
        project_id: str | None = None,
    ) -> Project: ...


class DataSourceRepository(Protocol):
    def get(self, project_id: str, data_source_id: str) -> DataSource | None: ...
    def list(self, project_id: str) -> Sequence[DataSource]: ...
    def add(self, project_id: str, data_source: DataSource) -> None: ...
    def update_binding(
        self,
        project_id: str,
        data_source_id: str,
        *,
        root_path: str,
        status: str,
        manifest_sha256: str,
        expected_revision: int,
    ) -> DataSource | None: ...


class CollectionRepository(Protocol):
    def get(self, project_id: str, collection_id: str) -> Collection | None: ...
    def list(self, project_id: str, *, parent_id: str | None = None) -> Sequence[Collection]: ...
    def add(self, project_id: str, collection: Collection) -> None: ...
    def rename(
        self,
        project_id: str,
        collection_id: str,
        name: str,
        expected_revision: int,
    ) -> Collection | None: ...
    def bump_revision(
        self,
        project_id: str,
        collection_id: str,
        expected_revision: int,
    ) -> Collection | None: ...
    def list_members(self, project_id: str, collection_id: str) -> Sequence[str]: ...
    def add_members(self, project_id: str, collection_id: str, asset_ids: Sequence[str]) -> None: ...
    def remove_member(self, project_id: str, collection_id: str, asset_id: str) -> None: ...


class ImportRepository(Protocol):
    def get_session(self, project_id: str, import_session_id: str) -> ImportSession | None: ...
    def list_sessions(self, project_id: str) -> Sequence[ImportSession]: ...
    def add_session(self, project_id: str, session: ImportSession) -> None: ...
    def list_entries(self, project_id: str, import_session_id: str) -> Sequence[ImportEntry]: ...
    def add_entry(self, project_id: str, entry: ImportEntry) -> None: ...
    def get_entry(self, project_id: str, import_session_id: str, entry_id: str) -> ImportEntry | None: ...
    def find_entry(
        self,
        project_id: str,
        import_session_id: str,
        relative_path: str,
    ) -> ImportEntry | None: ...
    def mark_verified(
        self,
        project_id: str,
        import_session_id: str,
        entry_id: str,
        *,
        actual_sha256: str,
        storage_key: str | None,
    ) -> ImportEntry | None: ...
    def transition_session(
        self,
        project_id: str,
        import_session_id: str,
        *,
        allowed: Sequence[str],
        target: str,
    ) -> ImportSession | None: ...


class AssetRepository(Protocol):
    def get(self, project_id: str, asset_id: str) -> Asset | None: ...
    def list_by_source(self, project_id: str, data_source_id: str) -> Sequence[Asset]: ...
    def add(self, project_id: str, asset: Asset) -> None: ...


class ReviewTaskRepository(Protocol):
    def get_round(self, project_id: str, round_id: str) -> ReviewTask | None: ...
    def list_items(
        self,
        project_id: str,
        round_id: str,
        filters: ReviewFilters | None = None,
    ) -> Sequence[Any]: ...
    def create_from_collection(self, project_id: str, collection_id: str, sample_size: int) -> str: ...


class ExplorerRepository(Protocol):
    def list_resources(self, project_id: str) -> Sequence[ExplorerResource]: ...
    def asset_exists(self, project_id: str, asset_id: str) -> bool: ...


class IdempotencyRepository(Protocol):
    def get(self, key: str) -> IdempotencyRecord | None: ...
    def reserve(self, record: IdempotencyRecord) -> None: ...
    def set_response(self, key: str, response: dict[str, object]) -> None: ...


class DirectoryPicker(Protocol):
    def pick_directory(self, *, title: str) -> str | None: ...


class FolderReader(Protocol):
    def canonicalize(self, locator: str) -> str: ...
    def scan(self, locator: str) -> Sequence[ManifestEntry]: ...
    def open_readonly(self, locator: str, relative_path: str) -> BinaryIO: ...


class UnitOfWork(AbstractContextManager, Protocol):
    repository: Repository
    projects: ProjectRepository
    data_sources: DataSourceRepository
    collections: CollectionRepository
    imports: ImportRepository
    assets: AssetRepository
    review_tasks: ReviewTaskRepository
    explorer: ExplorerRepository
    idempotency: IdempotencyRepository
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class ArtifactStore(Protocol):
    def put_bytes(self, content: bytes, *, media_type: str) -> Any: ...
    def put_stream(
        self,
        stream: BinaryIO,
        *,
        media_type: str,
        expected_sha256: str | None = None,
    ) -> Any: ...
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
