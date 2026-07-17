from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, BinaryIO, Protocol, Sequence


@dataclass(frozen=True, slots=True)
class JobSpec:
    kind: str
    environment: str
    working_directory: str
    arguments: tuple[str, ...]
    input_asset_ids: tuple[str, ...]
    expected_outputs: tuple[str, ...]
    resource_hints: dict[str, Any]


class Repository(Protocol):
    def get(self, entity_type: str, entity_id: str) -> Any | None: ...
    def add(self, entity: Any) -> None: ...


class UnitOfWork(AbstractContextManager, Protocol):
    repository: Repository
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
