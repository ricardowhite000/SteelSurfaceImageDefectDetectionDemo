from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
import io
from pathlib import Path
import shutil

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from steel_platform.application.errors import ApplicationError, NotFoundError
from steel_platform.application.imports import (
    DataSourceImportService,
    ImportHashMismatch,
    ManifestEntry,
    SourceManifestMismatch,
)
from steel_platform.application.projects import CreateProjectCommand, ProjectCatalogService
from steel_platform.domain.workspace import ImportEntryStatus, ImportStatus, SourceMode, SourceStatus
from steel_platform.infrastructure.artifacts import LocalArtifactStore
from steel_platform.infrastructure.directory_picker import (
    LocalFolderReader,
    UnavailableDirectoryPicker,
    WindowsDirectoryPicker,
)
from steel_platform.infrastructure.models import AssetModel, Base, ImportEntryModel
from steel_platform.infrastructure.uow import SqlAlchemyUnitOfWork


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session)


@pytest.fixture
def uow_factory(session_factory: sessionmaker[Session]) -> Callable[[], SqlAlchemyUnitOfWork]:
    return lambda: SqlAlchemyUnitOfWork(session_factory)


@pytest.fixture
def project_ids(uow_factory: Callable[[], SqlAlchemyUnitOfWork]) -> tuple[str, str]:
    catalog = ProjectCatalogService(uow_factory)
    command = lambda slug: CreateProjectCommand(slug, slug, "steel", ("Cr",))
    first = catalog.create_project(command("p1"), "project-p1")
    second = catalog.create_project(command("p2"), "project-p2")
    return first.id, second.id


@pytest.fixture
def import_service(
    tmp_path: Path,
    uow_factory: Callable[[], SqlAlchemyUnitOfWork],
    project_ids: tuple[str, str],
) -> DataSourceImportService:
    return DataSourceImportService(
        uow_factory,
        LocalArtifactStore(tmp_path / "artifacts"),
        LocalFolderReader(),
    )


def _build_image_folder(root: Path, *, content: bytes = b"BM-one") -> Path:
    root.mkdir(parents=True)
    (root / "Cr_1.bmp").write_bytes(content)
    nested = root / "nested"
    nested.mkdir()
    (nested / "Cr_2.bmp").write_bytes(b"BM-two")
    return root


def test_managed_import_survives_source_removal(
    import_service: DataSourceImportService,
    tmp_path: Path,
    project_ids: tuple[str, str],
) -> None:
    source_dir = _build_image_folder(tmp_path / "managed-source")
    run = import_service.import_managed(
        project_ids[0],
        "managed-images",
        source_dir,
        idempotency_key="managed-commit",
    )

    shutil.rmtree(source_dir)

    with import_service.open_asset(project_ids[0], run.asset_ids[0]) as stream:
        assert stream.read().startswith(b"BM")


def test_external_scan_is_read_only_and_open_uses_registered_project_asset(
    import_service: DataSourceImportService,
    tmp_path: Path,
    project_ids: tuple[str, str],
) -> None:
    root = _build_image_folder(tmp_path / "external-source")
    before = {
        path.relative_to(root).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns, sha256(path.read_bytes()).hexdigest())
        for path in root.rglob("*")
        if path.is_file()
    }

    run = import_service.import_external(
        project_ids[0],
        "external-images",
        root,
        idempotency_key="external-commit",
    )

    after = {
        path.relative_to(root).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns, sha256(path.read_bytes()).hexdigest())
        for path in root.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert not any(path.name.startswith(".") for path in root.rglob("*"))
    with import_service.open_asset(project_ids[0], run.asset_ids[0]) as stream:
        assert stream.read().startswith(b"BM")
    with pytest.raises(NotFoundError):
        import_service.open_asset(project_ids[1], run.asset_ids[0])
    with pytest.raises(NotFoundError):
        import_service.open_asset(project_ids[0], "unregistered")


def test_manifest_traversal_is_rejected_before_artifact_or_source_access(
    uow_factory: Callable[[], SqlAlchemyUnitOfWork],
    project_ids: tuple[str, str],
) -> None:
    class NeverArtifacts:
        def put_stream(self, stream: object, *, media_type: str) -> object:
            raise AssertionError("artifact storage must not be touched")

        def open(self, storage_key: str) -> object:
            raise AssertionError("artifact storage must not be touched")

    class NeverFolder:
        def canonicalize(self, locator: str) -> str:
            raise AssertionError("source folder must not be touched")

        def scan(self, locator: str) -> tuple[()]:
            raise AssertionError("source folder must not be touched")

        def open_readonly(self, locator: str, relative_path: str) -> object:
            raise AssertionError("source folder must not be touched")

    service = DataSourceImportService(uow_factory, NeverArtifacts(), NeverFolder())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="relative path"):
        service.register_manifest(project_ids[0], "unknown", [{
            "relative_path": "../escape.bmp",
            "size_bytes": 2,
            "media_type": "image/bmp",
            "sha256": sha256(b"BM").hexdigest(),
        }])


def test_external_scan_requires_a_registered_project_before_source_access(
    tmp_path: Path,
    uow_factory: Callable[[], SqlAlchemyUnitOfWork],
) -> None:
    root = _build_image_folder(tmp_path / "must-not-scan")

    class NeverFolder(LocalFolderReader):
        def canonicalize(self, locator: str) -> str:
            raise AssertionError("source folder must not be touched for an unknown project")

    service = DataSourceImportService(
        uow_factory,
        LocalArtifactStore(tmp_path / "unused-artifacts"),
        NeverFolder(),
    )

    with pytest.raises(NotFoundError):
        service.import_external("unknown", "external", root, idempotency_key="unknown-project")


def test_external_scan_occurs_after_source_and_session_registration(
    tmp_path: Path,
    uow_factory: Callable[[], SqlAlchemyUnitOfWork],
    project_ids: tuple[str, str],
) -> None:
    root = _build_image_folder(tmp_path / "registered-before-scan")

    class RegisteredFolder(LocalFolderReader):
        def scan(self, locator: str) -> tuple[ManifestEntry, ...]:
            with uow_factory() as uow:
                assert len(uow.sources.list(project_ids[0])) == 1
                assert len(uow.imports.list_sessions(project_ids[0])) == 1
            return super().scan(locator)

    service = DataSourceImportService(
        uow_factory,
        LocalArtifactStore(tmp_path / "registered-artifacts"),
        RegisteredFolder(),
    )

    service.import_external(project_ids[0], "external", root, idempotency_key="registered-before-scan")


def test_managed_upload_resumes_without_duplicate_verified_write(
    tmp_path: Path,
    uow_factory: Callable[[], SqlAlchemyUnitOfWork],
    project_ids: tuple[str, str],
) -> None:
    class CountingArtifacts:
        def __init__(self) -> None:
            self.delegate = LocalArtifactStore(tmp_path / "counted-artifacts")
            self.puts = 0

        def put_stream(
            self,
            stream: object,
            *,
            media_type: str,
            expected_sha256: str | None = None,
        ) -> object:
            self.puts += 1
            return self.delegate.put_stream(  # type: ignore[arg-type]
                stream,
                media_type=media_type,
                expected_sha256=expected_sha256,
            )

        def open(self, storage_key: str) -> object:
            return self.delegate.open(storage_key)

    artifacts = CountingArtifacts()
    service = DataSourceImportService(uow_factory, artifacts, LocalFolderReader())  # type: ignore[arg-type]
    session = service.start(project_ids[0], "managed", SourceMode.MANAGED)
    content = b"BM-resumable"
    manifest = [ManifestEntry("one.bmp", len(content), "image/bmp", sha256(content).hexdigest())]
    entry = service.register_manifest(project_ids[0], session.id, manifest)[0]

    verified = service.upload_entry(project_ids[0], session.id, entry.id, io.BytesIO(content))
    replayed = service.upload_entry(project_ids[0], session.id, entry.id, io.BytesIO(content))
    repeated_manifest = service.register_manifest(project_ids[0], session.id, manifest)

    assert verified.status is ImportEntryStatus.VERIFIED
    assert replayed == verified
    assert repeated_manifest == (verified,)
    assert artifacts.puts == 1


def test_upload_hash_mismatch_does_not_verify_entry(
    import_service: DataSourceImportService,
    project_ids: tuple[str, str],
) -> None:
    session = import_service.start(project_ids[0], "managed", SourceMode.MANAGED)
    entry = import_service.register_manifest(
        project_ids[0],
        session.id,
        [ManifestEntry("one.bmp", 2, "image/bmp", sha256(b"BM").hexdigest())],
    )[0]

    with pytest.raises(ImportHashMismatch):
        import_service.upload_entry(project_ids[0], session.id, entry.id, io.BytesIO(b"wrong"))

    current = import_service.get_import(project_ids[0], session.id)
    assert current.status is ImportStatus.UPLOADING
    assert current.entries[0].status is ImportEntryStatus.PLANNED


def test_commit_requires_key_binds_payload_and_replays_atomically(
    import_service: DataSourceImportService,
    project_ids: tuple[str, str],
    session_factory: sessionmaker[Session],
) -> None:
    first = import_service.start(project_ids[0], "first", SourceMode.MANAGED)
    content = b"BM-first"
    entry = import_service.register_manifest(
        project_ids[0], first.id,
        [ManifestEntry("first.bmp", len(content), "image/bmp", sha256(content).hexdigest())],
    )[0]
    import_service.upload_entry(project_ids[0], first.id, entry.id, io.BytesIO(content))
    import_service.validate(project_ids[0], first.id)

    with pytest.raises(ApplicationError) as missing:
        import_service.commit(project_ids[0], first.id, idempotency_key=" ")
    assert missing.value.code == "validation_error"

    committed = import_service.commit(project_ids[0], first.id, idempotency_key="commit-key")
    replayed = import_service.commit(project_ids[0], first.id, idempotency_key="commit-key")
    assert replayed == committed

    second = import_service.start(project_ids[0], "second", SourceMode.MANAGED)
    second_entry = import_service.register_manifest(
        project_ids[0], second.id,
        [ManifestEntry("second.bmp", len(content), "image/bmp", sha256(content).hexdigest())],
    )[0]
    import_service.upload_entry(project_ids[0], second.id, second_entry.id, io.BytesIO(content))
    import_service.validate(project_ids[0], second.id)
    with pytest.raises(ApplicationError) as conflict:
        import_service.commit(project_ids[0], second.id, idempotency_key="commit-key")
    assert conflict.value.code == "idempotency_conflict"

    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AssetModel)) == 1


def test_external_rebind_is_atomic_on_hash_mismatch_and_updates_once_on_match(
    import_service: DataSourceImportService,
    tmp_path: Path,
    project_ids: tuple[str, str],
) -> None:
    old = _build_image_folder(tmp_path / "old")
    run = import_service.import_external(
        project_ids[0], "external", old, idempotency_key="external-rebind-source"
    )
    original = import_service.get_source(project_ids[0], run.source_id)
    moved = tmp_path / "moved"
    shutil.copytree(old, moved)
    (moved / "Cr_1.bmp").write_bytes(b"changed")

    with pytest.raises(SourceManifestMismatch):
        import_service.rebind(project_ids[0], run.source_id, moved)

    unchanged = import_service.get_source(project_ids[0], run.source_id)
    assert unchanged.root_path == original.root_path
    assert unchanged.status == original.status
    assert unchanged.manifest_sha256 == original.manifest_sha256
    assert unchanged.revision == original.revision

    shutil.rmtree(moved)
    shutil.copytree(old, moved)
    rebound = import_service.rebind(project_ids[0], run.source_id, moved)
    assert rebound.root_path == moved.resolve().as_posix()
    assert rebound.status is SourceStatus.AVAILABLE
    assert rebound.manifest_sha256 == original.manifest_sha256
    assert rebound.revision == original.revision + 1
    with pytest.raises(NotFoundError):
        import_service.rebind(project_ids[1], run.source_id, moved)


def test_cancel_and_invalid_transitions_are_deterministic(
    import_service: DataSourceImportService,
    project_ids: tuple[str, str],
) -> None:
    session = import_service.start(project_ids[0], "cancelled", SourceMode.MANAGED)
    cancelled = import_service.cancel(project_ids[0], session.id)
    assert cancelled.status is ImportStatus.CANCELLED
    assert import_service.cancel(project_ids[0], session.id) == cancelled

    with pytest.raises(ApplicationError) as invalid:
        import_service.validate(project_ids[0], session.id)
    assert invalid.value.code == "invalid_import_state"
    assert invalid.value.details == {"current": "cancelled", "allowed": ["uploading", "validating"]}


def test_directory_picker_adapters_are_inert_without_explicit_windows_callback() -> None:
    calls: list[str] = []
    picker = WindowsDirectoryPicker(lambda title: calls.append(title) or Path("C:/chosen"))
    assert picker.pick_directory(title="Choose") == "C:/chosen"
    assert calls == ["Choose"]
    assert UnavailableDirectoryPicker().pick_directory(title="Never GUI") is None


def test_local_artifact_store_uses_atomic_stream_write_and_byte_idempotency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import steel_platform.infrastructure.artifacts as artifact_module

    calls: list[str] = []
    real_fsync = artifact_module.os.fsync
    real_replace = artifact_module.os.replace
    monkeypatch.setattr(artifact_module.os, "fsync", lambda fd: calls.append("fsync") or real_fsync(fd))
    monkeypatch.setattr(
        artifact_module.os,
        "replace",
        lambda source, target: calls.append("replace") or real_replace(source, target),
    )
    store = LocalArtifactStore(tmp_path / "atomic-artifacts")

    first = store.put_stream(io.BytesIO(b"streamed"), media_type="application/octet-stream")
    second = store.put_stream(io.BytesIO(b"streamed"), media_type="application/octet-stream")

    assert first == second
    assert calls.count("replace") == 1
    assert calls.count("fsync") == 2
    assert not list(store.resolve(first).parent.glob(".artifact-*"))
    with store.open(first.storage_key) as stream:
        assert stream.read() == b"streamed"


def test_repository_persists_verified_manifest_metadata(
    import_service: DataSourceImportService,
    project_ids: tuple[str, str],
    session_factory: sessionmaker[Session],
) -> None:
    session = import_service.start(project_ids[0], "metadata", SourceMode.MANAGED)
    content = b"BM-metadata"
    entry = import_service.register_manifest(
        project_ids[0], session.id,
        [ManifestEntry("metadata.bmp", len(content), "image/bmp", sha256(content).hexdigest())],
    )[0]
    import_service.upload_entry(project_ids[0], session.id, entry.id, io.BytesIO(content))

    with session_factory() as db:
        row = db.get(ImportEntryModel, entry.id)
        assert row is not None
        assert row.size_bytes == len(content)
        assert row.media_type == "image/bmp"
        assert row.expected_sha256 == sha256(content).hexdigest()
        assert row.actual_sha256 == row.expected_sha256
        assert row.storage_key is not None
        assert row.status == "verified"
