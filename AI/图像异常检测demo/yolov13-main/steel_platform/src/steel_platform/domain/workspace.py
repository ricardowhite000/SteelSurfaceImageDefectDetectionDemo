from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath


class SourceMode(StrEnum):
    MANAGED = "managed"
    EXTERNAL = "external"


class SourceStatus(StrEnum):
    AVAILABLE = "available"
    MISSING = "missing"
    UNREADABLE = "unreadable"
    CHANGED = "changed"


class ImportStatus(StrEnum):
    PLANNED = "planned"
    SCANNING = "scanning"
    UPLOADING = "uploading"
    VALIDATING = "validating"
    READY = "ready"
    COMMITTING = "committing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ClassSchema:
    id: str
    name: str
    names: tuple[str, ...]

    def __post_init__(self) -> None:
        names = tuple(self.names)
        object.__setattr__(self, "names", names)
        if not names or len(set(names)) != len(names):
            raise ValueError("class names must be non-empty and unique")

    def class_name(self, class_id: int) -> str:
        return self.names[class_id]


@dataclass(frozen=True, slots=True)
class Project:
    id: str
    name: str
    class_schema_id: str
    revision: int


@dataclass(frozen=True, slots=True)
class DataSource:
    id: str
    project_id: str
    name: str
    mode: SourceMode
    root_path: str
    status: SourceStatus
    revision: int


@dataclass(frozen=True, slots=True)
class Collection:
    id: str
    project_id: str
    name: str
    parent_id: str | None
    revision: int

    def __post_init__(self) -> None:
        if self.parent_id == self.id:
            raise ValueError("a collection cannot be its own parent")


@dataclass(frozen=True, slots=True)
class ImportSession:
    id: str
    project_id: str
    data_source_id: str
    collection_id: str
    status: ImportStatus
    revision: int


@dataclass(frozen=True, slots=True)
class ImportEntry:
    id: str
    project_id: str
    import_session_id: str
    relative_path: str
    status: ImportStatus
    revision: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "relative_path", normalize_relative_path(self.relative_path))


@dataclass(frozen=True, slots=True)
class ExplorerResource:
    id: str
    type: str
    name: str
    count: int
    status: str


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    key: str
    scope: str
    response: dict[str, object]


@dataclass(frozen=True, slots=True)
class ReviewTaskItems:
    total: int
    asset_ids: tuple[str, ...]


def normalize_relative_path(value: str) -> str:
    candidate = PurePosixPath(value.replace("\\", "/"))
    if not value or candidate.is_absolute() or ":" in candidate.parts[0] or ".." in candidate.parts:
        raise ValueError("path must be a non-empty relative path")
    return candidate.as_posix()
