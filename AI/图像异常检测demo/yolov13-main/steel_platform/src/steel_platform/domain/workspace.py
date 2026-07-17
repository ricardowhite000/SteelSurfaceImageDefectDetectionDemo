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


class ImportEntryStatus(StrEnum):
    PLANNED = "planned"
    VERIFIED = "verified"
    FAILED = "failed"


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
    manifest_sha256: str | None = None

    @property
    def locator(self) -> str:
        return self.root_path


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
    status: ImportEntryStatus
    revision: int
    size_bytes: int = 0
    media_type: str = "application/octet-stream"
    expected_sha256: str | None = None
    actual_sha256: str | None = None
    storage_key: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "relative_path", normalize_relative_path(self.relative_path))


@dataclass(frozen=True, slots=True)
class Asset:
    id: str
    project_id: str
    data_source_id: str
    relative_path: str
    sha256: str
    size_bytes: int
    media_type: str
    storage_key: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "relative_path", normalize_relative_path(self.relative_path))


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    relative_path: str
    size_bytes: int
    media_type: str
    sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "relative_path", normalize_relative_path(self.relative_path))
        if self.size_bytes < 0:
            raise ValueError("size_bytes must not be negative")
        if len(self.sha256) != 64 or any(character not in "0123456789abcdef" for character in self.sha256):
            raise ValueError("sha256 must be a lowercase hexadecimal digest")


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


class IdempotencyReservationConflict(RuntimeError):
    def __init__(self, key: str) -> None:
        super().__init__(f"idempotency key {key!r} was concurrently reserved")
        self.key = key


class ConcurrentAllocationError(RuntimeError):
    pass


def normalize_relative_path(value: str) -> str:
    candidate = PurePosixPath(value.replace("\\", "/"))
    if not value or candidate.is_absolute() or ":" in candidate.parts[0] or ".." in candidate.parts:
        raise ValueError("path must be a non-empty relative path")
    return candidate.as_posix()
