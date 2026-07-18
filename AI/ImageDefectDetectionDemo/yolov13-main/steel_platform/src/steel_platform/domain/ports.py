from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, BinaryIO, Protocol, Sequence

from steel_platform.domain.annotations import AnnotationBox
from steel_platform.domain.workbench import WorkbenchJobSpec

from steel_platform.domain.workspace import (
    Asset,
    ClassSchema,
    Collection,
    DataSource,
    ExplorerResource,
    ResourceItem,
    AnnotationRevision,
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
    def get_class_schema(self, project_id: str, schema_id: str) -> ClassSchema | None: ...
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
    def update_status(
        self,
        project_id: str,
        data_source_id: str,
        *,
        status: str,
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
    def list_rounds(self, project_id: str) -> Sequence[ReviewTask]: ...
    def get_round(self, project_id: str, round_id: str) -> ReviewTask | None: ...
    def list_items(
        self,
        project_id: str,
        round_id: str,
        filters: ReviewFilters | None = None,
    ) -> Sequence[Any]: ...
    def get_item(self, project_id: str, round_id: str, item_id: str) -> Any | None: ...
    def get_draft(self, project_id: str, round_id: str, item_id: str) -> Any | None: ...
    def get_annotation(self, project_id: str, revision_id: str) -> Any | None: ...
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
    ) -> str: ...
    def upsert_draft(
        self,
        project_id: str,
        round_id: str,
        item_id: str,
        *,
        boxes: Sequence[dict[str, int | float]],
        note: str,
    ) -> None: ...
    def delete_draft(self, project_id: str, round_id: str, item_id: str) -> None: ...
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
    ) -> Any | None: ...
    def add_replacement(self, project_id: str, round_id: str, item_id: str) -> str | None: ...
    def progress(self, project_id: str, round_id: str) -> dict[str, int]: ...
    def next_pending_item_id(self, project_id: str, round_id: str) -> str | None: ...
    def set_round_completed(self, project_id: str, round_id: str, completed: bool) -> None: ...
    def add_review_event(
        self,
        project_id: str,
        round_id: str,
        item_id: str,
        *,
        state: str,
        revision: int,
    ) -> None: ...
    def create_from_collection(self, project_id: str, collection_id: str, sample_size: int) -> str: ...


class ExplorerRepository(Protocol):
    def list_resources(self, project_id: str) -> Sequence[ExplorerResource]: ...
    def asset_exists(self, project_id: str, asset_id: str) -> bool: ...


class ResourceBrowserRepository(Protocol):
    def get_resource(
        self, project_id: str, resource_type: str, resource_id: str
    ) -> ExplorerResource | None: ...
    def list_items(
        self, project_id: str, resource_type: str, resource_id: str
    ) -> Sequence[ResourceItem]: ...
    def get_item(
        self, project_id: str, resource_type: str, resource_id: str, asset_id: str
    ) -> ResourceItem | None: ...
    def list_revisions(self, project_id: str, asset_id: str) -> Sequence[AnnotationRevision]: ...


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
    def open_verified(
        self,
        locator: str,
        relative_path: str,
        *,
        expected_sha256: str,
        expected_size_bytes: int,
    ) -> BinaryIO: ...


class UnitOfWork(AbstractContextManager, Protocol):
    repository: Repository
    projects: ProjectRepository
    data_sources: DataSourceRepository
    collections: CollectionRepository
    imports: ImportRepository
    assets: AssetRepository
    review_tasks: ReviewTaskRepository
    explorer: ExplorerRepository
    resources: ResourceBrowserRepository
    idempotency: IdempotencyRepository
    def commit(self) -> None: ...
    def flush(self) -> None: ...
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


class AnnotationCodec(Protocol):
    def encode(self, boxes: Sequence[AnnotationBox]) -> bytes: ...
    def decode(self, content: bytes) -> tuple[AnnotationBox, ...]: ...


class JobExecutor(Protocol):
    def prepare(self, spec: JobSpec) -> str: ...


class PredictorAdapter(Protocol):
    def predict(self, source_ids: Sequence[str], *, model_id: str, batch: int = 1) -> str: ...


class EventPublisher(Protocol):
    def publish(self, event_type: str, payload: dict[str, Any]) -> None: ...


class Telemetry(Protocol):
    def event(self, name: str, attributes: dict[str, Any]) -> None: ...
    def metric(self, name: str, value: float, attributes: dict[str, Any]) -> None: ...


class WorkbenchGateway(Protocol):
    def options(self, project_id: str) -> dict[str, object]: ...
    def list_jobs(self, project_id: str) -> Sequence[dict[str, object]]: ...
    def get_job(self, project_id: str, job_id: str) -> dict[str, object]: ...
    def create_job(self, project_id: str, name: str, spec: WorkbenchJobSpec) -> dict[str, object]: ...
    def update_job(
        self,
        project_id: str,
        job_id: str,
        *,
        expected_revision: int,
        name: str,
        spec: WorkbenchJobSpec,
    ) -> dict[str, object]: ...
    def prepare_job(
        self, project_id: str, job_id: str, *, expected_revision: int, idempotency_key: str
    ) -> dict[str, object]: ...
    def launch_terminal(
        self, project_id: str, job_id: str, *, expected_revision: int, idempotency_key: str
    ) -> dict[str, object]: ...
    def read_log(self, project_id: str, job_id: str, *, after: int) -> dict[str, object]: ...
    def results(self, project_id: str, job_id: str) -> dict[str, object]: ...
    def ingest_job(
        self, project_id: str, job_id: str, *, expected_revision: int, idempotency_key: str
    ) -> dict[str, object]: ...
    def list_models(self, project_id: str) -> Sequence[dict[str, object]]: ...
    def import_model(
        self,
        project_id: str,
        *,
        name: str,
        weights_asset_id: str,
        model_format: str,
        purpose: str,
        class_names: Sequence[str] | None,
        source_note: str,
    ) -> dict[str, object]: ...
    def stage_model_file(
        self, project_id: str, *, filename: str, stream: BinaryIO
    ) -> dict[str, object]: ...
    def stage_inference_file(
        self, project_id: str, *, filename: str, media_type: str, stream: BinaryIO
    ) -> dict[str, object]: ...
    def cancel_job(
        self, project_id: str, job_id: str, *, expected_revision: int, idempotency_key: str
    ) -> dict[str, object]: ...
