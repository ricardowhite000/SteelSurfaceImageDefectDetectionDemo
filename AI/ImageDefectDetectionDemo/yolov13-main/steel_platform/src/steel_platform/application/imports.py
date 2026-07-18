from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, BinaryIO
from uuid import uuid4

from steel_platform.application.errors import ApplicationError, NotFoundError
from steel_platform.application.projects import canonical_scope, require_idempotency_key, require_matching_scope
from steel_platform.domain.ports import ArtifactStore, FolderReader, UnitOfWork
from steel_platform.domain.workspace import (
    Asset,
    Collection,
    DataSource,
    IdempotencyRecord,
    IdempotencyReservationConflict,
    ImportEntry,
    ImportEntryStatus,
    ImportSession,
    ImportStatus,
    ManifestEntry,
    SourceMode,
    SourceContentChanged,
    SourceStatus,
    SourceUnavailable,
)

__all__ = [
    "DataSourceImportService",
    "ImportHashMismatch",
    "ImportResult",
    "ImportView",
    "ManifestEntry",
    "SourceManifestMismatch",
]


class ImportHashMismatch(ApplicationError):
    def __init__(self, relative_path: str) -> None:
        super().__init__(
            "import_hash_mismatch",
            f"Uploaded bytes do not match the manifest for {relative_path}",
            status_code=422,
        )


class SourceManifestMismatch(ApplicationError):
    def __init__(self) -> None:
        super().__init__(
            "source_manifest_mismatch",
            "The selected folder does not match the registered source manifest",
            status_code=409,
        )


@dataclass(frozen=True, slots=True)
class ImportView:
    session: ImportSession
    entries: tuple[ImportEntry, ...]

    @property
    def status(self) -> ImportStatus:
        return self.session.status


@dataclass(frozen=True, slots=True)
class ImportResult:
    import_id: str
    source_id: str
    collection_id: str
    asset_ids: tuple[str, ...]

    @property
    def id(self) -> str:
        return self.source_id


class DataSourceImportService:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        artifacts: ArtifactStore,
        folders: FolderReader,
    ) -> None:
        self.uow_factory = uow_factory
        self.artifacts = artifacts
        self.folders = folders

    def start(
        self,
        project_id: str,
        name: str,
        mode: SourceMode,
        locator: object | None = None,
    ) -> ImportSession:
        normalized_name = name.strip()
        if not normalized_name:
            raise ApplicationError("validation_error", "Import name is required", status_code=422)
        if mode is SourceMode.EXTERNAL and locator is None:
            raise ApplicationError("validation_error", "External imports require a source locator", status_code=422)
        source_id = str(uuid4())
        collection_id = str(uuid4())
        session = ImportSession(
            id=str(uuid4()),
            project_id=project_id,
            data_source_id=source_id,
            collection_id=collection_id,
            status=ImportStatus.PLANNED,
            revision=0,
        )
        with self.uow_factory() as uow:
            if uow.projects.get(project_id) is None:
                raise NotFoundError("Project was not found")
            root_path = (
                self.folders.canonicalize(str(locator))
                if mode is SourceMode.EXTERNAL and locator is not None
                else f"managed://{uuid4()}"
            )
            uow.sources.add(
                project_id,
                DataSource(
                    id=source_id,
                    project_id=project_id,
                    name=normalized_name,
                    mode=mode,
                    root_path=root_path,
                    status=SourceStatus.IMPORTING,
                    revision=0,
                ),
            )
            uow.collections.add(
                project_id,
                Collection(collection_id, project_id, normalized_name, None, 0),
            )
            # No ORM relationships model these foreign-key dependencies.
            # Persist the staged parents before adding their import session.
            uow.flush()
            uow.imports.add_session(project_id, session)
            uow.commit()
        return session

    def register_manifest(
        self,
        project_id: str,
        import_id: str,
        entries: Sequence[ManifestEntry | Mapping[str, Any]],
    ) -> tuple[ImportEntry, ...]:
        normalized = tuple(_manifest_entry(entry) for entry in entries)
        paths = [entry.relative_path for entry in normalized]
        if not normalized:
            raise ApplicationError("validation_error", "Import manifest must not be empty", status_code=422)
        if len(paths) != len(set(paths)):
            raise ApplicationError("validation_error", "Import manifest paths must be unique", status_code=422)
        with self.uow_factory() as uow:
            session = self._require_session(uow, project_id, import_id)
            self._require_state(session, (ImportStatus.PLANNED, ImportStatus.SCANNING, ImportStatus.UPLOADING))
            source = self._require_source(uow, project_id, session.data_source_id)
            authoritative = normalized
            if source.mode is SourceMode.EXTERNAL:
                authoritative = self._scan_external(source)
                if _manifest_signature(normalized) != _manifest_signature(authoritative):
                    raise SourceManifestMismatch()
            registered: list[ImportEntry] = []
            for candidate in authoritative:
                current = uow.imports.find_entry(project_id, import_id, candidate.relative_path)
                if current is not None:
                    if (
                        current.size_bytes != candidate.size_bytes
                        or current.media_type != candidate.media_type
                        or current.expected_sha256 != candidate.sha256
                    ):
                        raise ApplicationError(
                            "manifest_conflict",
                            f"Manifest path {candidate.relative_path} was already registered with different metadata",
                            status_code=409,
                        )
                    registered.append(current)
                    continue
                external = source.mode is SourceMode.EXTERNAL
                entry = ImportEntry(
                    id=str(uuid4()),
                    project_id=project_id,
                    import_session_id=import_id,
                    relative_path=candidate.relative_path,
                    status=ImportEntryStatus.VERIFIED if external else ImportEntryStatus.PLANNED,
                    revision=0,
                    size_bytes=candidate.size_bytes,
                    media_type=candidate.media_type,
                    expected_sha256=candidate.sha256,
                    actual_sha256=candidate.sha256 if external else None,
                )
                uow.imports.add_entry(project_id, entry)
                registered.append(entry)
            target = ImportStatus.VALIDATING if source.mode is SourceMode.EXTERNAL else ImportStatus.UPLOADING
            transitioned = uow.imports.transition_session(
                project_id,
                import_id,
                allowed=(session.status.value,),
                target=target.value,
            )
            if transitioned is None:
                raise self._invalid_state(session, (session.status,))
            uow.commit()
            return tuple(registered)

    def upload_entry(
        self,
        project_id: str,
        import_id: str,
        entry_id: str,
        stream: BinaryIO,
    ) -> ImportEntry:
        with self.uow_factory() as uow:
            session = self._require_session(uow, project_id, import_id)
            self._require_state(session, (ImportStatus.UPLOADING,))
            entry = uow.imports.get_entry(project_id, import_id, entry_id)
            if entry is None:
                raise NotFoundError("Import entry was not found")
            if entry.status is ImportEntryStatus.VERIFIED:
                return entry
            try:
                artifact = self.artifacts.put_stream(
                    stream,
                    media_type=entry.media_type,
                    expected_sha256=entry.expected_sha256,
                )
            except ValueError as exc:
                raise ImportHashMismatch(entry.relative_path) from exc
            if artifact.sha256 != entry.expected_sha256 or artifact.size_bytes != entry.size_bytes:
                raise ImportHashMismatch(entry.relative_path)
            verified = uow.imports.mark_verified(
                project_id,
                import_id,
                entry.id,
                actual_sha256=artifact.sha256,
                storage_key=artifact.storage_key,
            )
            if verified is None:
                raise NotFoundError("Import entry was not found")
            uow.commit()
            return verified

    def validate(self, project_id: str, import_id: str) -> ImportSession:
        with self.uow_factory() as uow:
            session = self._require_session(uow, project_id, import_id)
            allowed = (ImportStatus.UPLOADING, ImportStatus.VALIDATING)
            self._require_state(session, allowed)
            entries = tuple(uow.imports.list_entries(project_id, import_id))
            if not entries or any(entry.status is not ImportEntryStatus.VERIFIED for entry in entries):
                raise ApplicationError(
                    "import_not_verified",
                    "Every manifest entry must be verified before validation",
                    status_code=409,
                )
            source = self._require_source(uow, project_id, session.data_source_id)
            if source.mode is SourceMode.EXTERNAL:
                scanned = self._scan_external(source)
                if _manifest_signature(entries) != _manifest_signature(scanned):
                    raise SourceManifestMismatch()
            ready = uow.imports.transition_session(
                project_id,
                import_id,
                allowed=tuple(status.value for status in allowed),
                target=ImportStatus.READY.value,
            )
            if ready is None:
                raise self._invalid_state(session, allowed)
            uow.commit()
            return ready

    def commit(self, project_id: str, import_id: str, *, idempotency_key: str) -> ImportResult:
        key = require_idempotency_key(idempotency_key)
        scope = canonical_scope("import-commit", {"project_id": project_id, "import_id": import_id})
        try:
            with self.uow_factory() as uow:
                prior = uow.idempotency.get(key)
                if prior is not None:
                    return self._replay(uow, prior, scope)
                session = self._require_session(uow, project_id, import_id)
                self._require_state(session, (ImportStatus.READY,))
                entries = tuple(uow.imports.list_entries(project_id, import_id))
                if not entries or any(entry.status is not ImportEntryStatus.VERIFIED for entry in entries):
                    raise ApplicationError("import_not_verified", "Import is not verified", status_code=409)
                source = self._require_source(uow, project_id, session.data_source_id)
                if source.mode is SourceMode.EXTERNAL:
                    scanned = self._scan_external(source)
                    if _manifest_signature(entries) != _manifest_signature(scanned):
                        raise SourceManifestMismatch()
                if uow.collections.get(project_id, session.collection_id) is None:
                    raise NotFoundError("Import collection was not found")
                uow.idempotency.reserve(IdempotencyRecord(key, scope, {}))
                committing = uow.imports.transition_session(
                    project_id,
                    import_id,
                    allowed=(ImportStatus.READY.value,),
                    target=ImportStatus.COMMITTING.value,
                )
                if committing is None:
                    raise self._invalid_state(session, (ImportStatus.READY,))
                asset_ids: list[str] = []
                for entry in entries:
                    if entry.actual_sha256 is None:
                        raise ApplicationError("import_not_verified", "Import is not verified", status_code=409)
                    asset = Asset(
                        id=str(uuid4()),
                        project_id=project_id,
                        data_source_id=source.id,
                        relative_path=entry.relative_path,
                        sha256=entry.actual_sha256,
                        size_bytes=entry.size_bytes,
                        media_type=entry.media_type,
                        storage_key=entry.storage_key,
                    )
                    uow.assets.add(project_id, asset)
                    asset_ids.append(asset.id)
                uow.collections.add_members(project_id, session.collection_id, asset_ids)
                manifest_sha256 = _manifest_sha256(entries)
                updated_source = uow.sources.update_binding(
                    project_id,
                    source.id,
                    root_path=source.root_path,
                    status=SourceStatus.AVAILABLE.value,
                    manifest_sha256=manifest_sha256,
                    expected_revision=source.revision,
                )
                if updated_source is None:
                    raise ApplicationError("concurrency_conflict", "Source changed during commit", status_code=409)
                succeeded = uow.imports.transition_session(
                    project_id,
                    import_id,
                    allowed=(ImportStatus.COMMITTING.value,),
                    target=ImportStatus.SUCCEEDED.value,
                )
                if succeeded is None:
                    raise ApplicationError("concurrency_conflict", "Import changed during commit", status_code=409)
                response: dict[str, object] = {
                    "project_id": project_id,
                    "import_id": import_id,
                    "source_id": source.id,
                    "collection_id": session.collection_id,
                    "asset_ids": asset_ids,
                }
                uow.idempotency.set_response(key, response)
                uow.commit()
                return ImportResult(import_id, source.id, session.collection_id, tuple(asset_ids))
        except IdempotencyReservationConflict:
            return self._replay_committed(key, scope)

    def cancel(self, project_id: str, import_id: str) -> ImportSession:
        with self.uow_factory() as uow:
            session = self._require_session(uow, project_id, import_id)
            if session.status is ImportStatus.CANCELLED:
                return session
            allowed = (
                ImportStatus.PLANNED,
                ImportStatus.SCANNING,
                ImportStatus.UPLOADING,
                ImportStatus.VALIDATING,
                ImportStatus.READY,
                ImportStatus.FAILED,
            )
            self._require_state(session, allowed)
            cancelled = uow.imports.transition_session(
                project_id,
                import_id,
                allowed=tuple(status.value for status in allowed),
                target=ImportStatus.CANCELLED.value,
            )
            if cancelled is None:
                raise self._invalid_state(session, allowed)
            uow.commit()
            return cancelled

    def rebind(self, project_id: str, source_id: str, locator: object) -> DataSource:
        with self.uow_factory() as uow:
            source = self._require_source(uow, project_id, source_id)
            if source.mode is not SourceMode.EXTERNAL:
                raise ApplicationError("invalid_source_mode", "Managed sources cannot be rebound", status_code=409)
            expected_revision = source.revision
        root_path = self.folders.canonicalize(str(locator))
        first_candidate = tuple(self.folders.scan(root_path))
        second_candidate = tuple(self.folders.scan(root_path))
        with self.uow_factory() as uow:
            current = self._require_source(uow, project_id, source_id)
            if current.revision != expected_revision:
                raise ApplicationError("concurrency_conflict", "Source changed during rebind", status_code=409)
            assets = tuple(uow.assets.list_by_source(project_id, source_id))
            expected = tuple(
                sorted((asset.relative_path, asset.size_bytes, asset.sha256) for asset in assets)
            )
            expected_paths = {relative_path for relative_path, _, _ in expected}
            first_registered = tuple(
                entry for entry in first_candidate if entry.relative_path in expected_paths
            )
            second_registered = tuple(
                entry for entry in second_candidate if entry.relative_path in expected_paths
            )
            first_actual = tuple(
                sorted((entry.relative_path, entry.size_bytes, entry.sha256) for entry in first_registered)
            )
            second_actual = tuple(
                sorted((entry.relative_path, entry.size_bytes, entry.sha256) for entry in second_registered)
            )
            if not expected or first_actual != expected or second_actual != expected or first_actual != second_actual:
                raise SourceManifestMismatch()
            rebound = uow.sources.update_binding(
                project_id,
                source_id,
                root_path=root_path,
                status=SourceStatus.AVAILABLE.value,
                manifest_sha256=_manifest_sha256(second_registered),
                expected_revision=expected_revision,
            )
            if rebound is None:
                raise ApplicationError("concurrency_conflict", "Source changed during rebind", status_code=409)
            uow.commit()
            return rebound

    def get_import(self, project_id: str, import_id: str) -> ImportView:
        with self.uow_factory() as uow:
            session = self._require_session(uow, project_id, import_id)
            return ImportView(session, tuple(uow.imports.list_entries(project_id, import_id)))

    def get_source(self, project_id: str, source_id: str) -> DataSource:
        with self.uow_factory() as uow:
            return self._require_source(uow, project_id, source_id)

    def get_asset(self, project_id: str, asset_id: str) -> Asset:
        try:
            with self.uow_factory() as uow:
                asset = uow.assets.get(project_id, asset_id)
                if asset is None:
                    raise NotFoundError("Asset was not found")
                return asset
        except ValueError as exc:
            raise ApplicationError("source_offline", "Registered asset path is invalid", status_code=409) from exc

    def open_asset(self, project_id: str, asset_id: str) -> BinaryIO:
        try:
            with self.uow_factory() as uow:
                asset = uow.assets.get(project_id, asset_id)
                if asset is None:
                    raise NotFoundError("Asset was not found")
                source = (
                    self._require_source(uow, project_id, asset.data_source_id)
                    if asset.data_source_id is not None
                    else None
                )
        except ValueError as exc:
            raise ApplicationError("source_offline", "Registered asset path is invalid", status_code=409) from exc
        if source is None or source.mode is SourceMode.MANAGED:
            if asset.storage_key is None:
                raise ApplicationError("artifact_missing", "Managed asset has no storage key", status_code=500)
            return self.artifacts.open(asset.storage_key)
        try:
            return self.folders.open_verified(
                source.root_path,
                asset.relative_path,
                expected_sha256=asset.sha256,
                expected_size_bytes=asset.size_bytes,
            )
        except SourceContentChanged as exc:
            self._mark_source_status(project_id, source, SourceStatus.CHANGED)
            raise ApplicationError("source_changed", "External source content has changed", status_code=409) from exc
        except (SourceUnavailable, FileNotFoundError, OSError, ValueError) as exc:
            self._mark_source_status(project_id, source, SourceStatus.MISSING)
            raise ApplicationError("source_offline", "External source is unavailable", status_code=409) from exc

    def import_managed(
        self,
        project_id: str,
        name: str,
        locator: object,
        *,
        idempotency_key: str,
    ) -> ImportResult:
        self._require_registered_project(project_id)
        session = self.start(project_id, name, SourceMode.MANAGED)
        root = self.folders.canonicalize(str(locator))
        manifest = tuple(self.folders.scan(root))
        entries = self.register_manifest(project_id, session.id, manifest)
        for entry in entries:
            with self.folders.open_readonly(root, entry.relative_path) as stream:
                self.upload_entry(project_id, session.id, entry.id, stream)
        self.validate(project_id, session.id)
        return self.commit(project_id, session.id, idempotency_key=idempotency_key)

    def import_external(
        self,
        project_id: str,
        name: str,
        locator: object,
        *,
        idempotency_key: str,
    ) -> ImportResult:
        self._require_registered_project(project_id)
        session = self.start(project_id, name, SourceMode.EXTERNAL, locator)
        root = self.get_source(project_id, session.data_source_id).root_path
        manifest = tuple(self.folders.scan(root))
        self.register_manifest(project_id, session.id, manifest)
        self.validate(project_id, session.id)
        return self.commit(project_id, session.id, idempotency_key=idempotency_key)

    def _require_registered_project(self, project_id: str) -> None:
        with self.uow_factory() as uow:
            if uow.projects.get(project_id) is None:
                raise NotFoundError("Project was not found")

    def _scan_external(self, source: DataSource) -> tuple[ManifestEntry, ...]:
        try:
            return tuple(self.folders.scan(source.root_path))
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise ApplicationError("source_offline", "External source is unavailable", status_code=409) from exc

    def _mark_source_status(
        self,
        project_id: str,
        observed: DataSource,
        status: SourceStatus,
    ) -> None:
        with self.uow_factory() as uow:
            current = uow.sources.get(project_id, observed.id)
            if current is None or current.status is status:
                return
            changed = uow.sources.update_status(
                project_id,
                current.id,
                status=status.value,
                expected_revision=current.revision,
            )
            if changed is not None:
                uow.commit()

    @staticmethod
    def _require_session(uow: UnitOfWork, project_id: str, import_id: str) -> ImportSession:
        session = uow.imports.get_session(project_id, import_id)
        if session is None:
            raise NotFoundError("Import session was not found")
        return session

    @staticmethod
    def _require_source(uow: UnitOfWork, project_id: str, source_id: str) -> DataSource:
        source = uow.sources.get(project_id, source_id)
        if source is None:
            raise NotFoundError("Data source was not found")
        return source

    @classmethod
    def _require_state(cls, session: ImportSession, allowed: Sequence[ImportStatus]) -> None:
        if session.status not in allowed:
            raise cls._invalid_state(session, allowed)

    @staticmethod
    def _invalid_state(session: ImportSession, allowed: Sequence[ImportStatus]) -> ApplicationError:
        return ApplicationError(
            "invalid_import_state",
            f"Import is {session.status.value}; expected one of {', '.join(status.value for status in allowed)}",
            status_code=409,
            details={"current": session.status.value, "allowed": [status.value for status in allowed]},
        )

    @staticmethod
    def _replay(uow: UnitOfWork, record: IdempotencyRecord, scope: str) -> ImportResult:
        require_matching_scope(record, scope)
        response = record.response
        project_id = response.get("project_id")
        import_id = response.get("import_id")
        source_id = response.get("source_id")
        collection_id = response.get("collection_id")
        asset_ids = response.get("asset_ids")
        if not (
            isinstance(project_id, str)
            and isinstance(import_id, str)
            and isinstance(source_id, str)
            and isinstance(collection_id, str)
        ):
            raise NotFoundError("Idempotent import result no longer exists")
        if not isinstance(asset_ids, list) or not all(isinstance(asset_id, str) for asset_id in asset_ids):
            raise NotFoundError("Idempotent import result no longer exists")
        if (
            uow.imports.get_session(project_id, import_id) is None
            or uow.sources.get(project_id, source_id) is None
            or uow.collections.get(project_id, collection_id) is None
            or any(uow.assets.get(project_id, asset_id) is None for asset_id in asset_ids)
        ):
            raise NotFoundError("Idempotent import result no longer exists")
        return ImportResult(import_id, source_id, collection_id, tuple(asset_ids))

    def _replay_committed(self, key: str, scope: str) -> ImportResult:
        for _ in range(3):
            with self.uow_factory() as uow:
                record = uow.idempotency.get(key)
                if record is not None:
                    return self._replay(uow, record, scope)
        raise ApplicationError(
            "concurrency_conflict",
            "Concurrent idempotency reservation did not become visible",
            status_code=409,
        )


def _manifest_entry(value: ManifestEntry | Mapping[str, Any]) -> ManifestEntry:
    if isinstance(value, ManifestEntry):
        return value
    return ManifestEntry(
        relative_path=str(value["relative_path"]),
        size_bytes=int(value["size_bytes"]),
        media_type=str(value["media_type"]),
        sha256=str(value["sha256"]),
    )


def _manifest_sha256(entries: Sequence[ImportEntry | ManifestEntry]) -> str:
    payload = [
        {
            "relative_path": entry.relative_path,
            "size_bytes": entry.size_bytes,
            "sha256": entry.actual_sha256 if isinstance(entry, ImportEntry) else entry.sha256,
        }
        for entry in sorted(entries, key=lambda item: item.relative_path)
    ]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return sha256(encoded).hexdigest()


def _manifest_signature(
    entries: Sequence[ImportEntry | ManifestEntry],
) -> tuple[tuple[str, int, str | None], ...]:
    return tuple(
        sorted(
            (
                entry.relative_path,
                entry.size_bytes,
                entry.actual_sha256 if isinstance(entry, ImportEntry) else entry.sha256,
            )
            for entry in entries
        )
    )
