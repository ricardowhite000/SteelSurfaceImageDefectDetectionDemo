# Steel Platform Resource Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build project-scoped file management, dual-mode folder import, virtual collections, and task-scoped review so the 225-item and 60-item review rounds never mix.

**Architecture:** Keep the existing FastAPI/SQLAlchemy/SQLite modular monolith, but move project, source, collection, import, and review rules behind domain ports and application services. A versioned Alembic migration backfills the existing steel project without changing asset IDs, annotation revisions, or review decisions; the vanilla browser UI becomes a module-separated file manager and review workspace.

**Tech Stack:** Python 3.11, Pydantic 2, SQLAlchemy 2, Alembic, FastAPI, Typer, Pillow, vanilla HTML/CSS/ES modules, pytest, Hypothesis, SQLite WAL, local content-addressed artifact storage.

## Global Constraints

- Do not modify Ultralytics/YOLOv13 core code, PyTorch, CUDA, model weights, or training algorithms.
- Existing source images, candidate labels, human annotation revisions, and model artifacts remain read-only and hash-identical.
- Service startup checks the Alembic version but never runs migrations automatically.
- All business queries explicitly receive `project_id`; review item operations also receive `round_id`.
- Project creation, import commit, task creation, collection membership changes, and review decisions require `Idempotency-Key`.
- Managed imports use content-addressed storage; external imports store a root locator plus relative paths and SHA256.
- The local directory picker is enabled only on loopback hosts and has a manual-path fallback.
- The existing `steel-defects-v1` class order is exactly `Cr, In, Pa, PS, RS, Sc`.
- Old `/api/v1/review/queues` returns HTTP 410 with code `scope_required`.
- Run all migration tests against copies; back up the real database and configuration before the real upgrade.

---

### Task 1: Domain Types and Workspace Rules

**Files:**
- Create: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/domain/workspace.py`
- Modify: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/domain/ports.py`
- Create: `AI/图像异常检测demo/yolov13-main/steel_platform/tests/test_workspace_domain.py`

**Interfaces:**
- Produces: `SourceMode`, `SourceStatus`, `ImportStatus`, `ClassSchema`, `Project`, `DataSource`, `Collection`, `ImportSession`, `ImportEntry`, `normalize_relative_path()`.
- Produces ports: `ProjectRepository`, `DataSourceRepository`, `CollectionRepository`, `ImportRepository`, `ReviewTaskRepository`, `DirectoryPicker`, and typed `UnitOfWork` properties.

- [ ] **Step 1: Write failing domain tests**

```python
def test_class_schema_is_ordered_and_immutable() -> None:
    schema = ClassSchema("schema-1", "steel-defects-v1", ("Cr", "In", "Pa", "PS", "RS", "Sc"))
    assert schema.class_name(3) == "PS"
    with pytest.raises(FrozenInstanceError):
        schema.names = ("other",)


@pytest.mark.parametrize("value", ["../secret.bmp", "/absolute.bmp", "C:/absolute.bmp", "a/../../b.bmp", ""])
def test_relative_path_rejects_escape(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_relative_path(value)


def test_collection_cannot_parent_itself() -> None:
    with pytest.raises(ValueError):
        Collection(id="c1", project_id="p1", name="bad", parent_id="c1", revision=0)
```

- [ ] **Step 2: Verify the tests fail**

Run: `conda run -n steel-review python -m pytest tests/test_workspace_domain.py -v`

Expected: collection fails with `ModuleNotFoundError: steel_platform.domain.workspace`.

- [ ] **Step 3: Implement the domain types and typed ports**

```python
class SourceMode(StrEnum):
    MANAGED = "managed"
    EXTERNAL = "external"


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
        if not self.names or len(set(self.names)) != len(self.names):
            raise ValueError("class names must be non-empty and unique")

    def class_name(self, class_id: int) -> str:
        return self.names[class_id]


def normalize_relative_path(value: str) -> str:
    candidate = PurePosixPath(value.replace("\\", "/"))
    if not value or candidate.is_absolute() or ":" in candidate.parts[0] or ".." in candidate.parts:
        raise ValueError("path must be a non-empty relative path")
    return candidate.as_posix()
```

Define repository methods with project-scoped signatures, for example:

```python
class ReviewTaskRepository(Protocol):
    def get_round(self, project_id: str, round_id: str) -> ReviewTask | None: ...
    def list_items(self, project_id: str, round_id: str, filters: ReviewFilters) -> Sequence[Any]: ...
```

- [ ] **Step 4: Run domain and architecture tests**

Run: `conda run -n steel-review python -m pytest tests/test_workspace_domain.py tests/test_architecture_and_maintenance.py -v`

Expected: all tests pass; domain modules contain no FastAPI, SQLAlchemy, Ultralytics, or concrete filesystem imports.

- [ ] **Step 5: Commit**

```powershell
git add -- AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/domain AI/图像异常检测demo/yolov13-main/steel_platform/tests/test_workspace_domain.py
git commit -m "feat: define project workspace domain"
```

### Task 2: Versioned Database Schema and Legacy Backfill

**Files:**
- Modify: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/infrastructure/models.py`
- Create: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/infrastructure/migrations/versions/0002_resource_scoping.py`
- Modify: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/infrastructure/database.py`
- Create: `AI/图像异常检测demo/yolov13-main/steel_platform/tests/test_resource_migration.py`

**Interfaces:**
- Produces ORM models: `ClassSchemaModel`, `CollectionModel`, `CollectionMemberModel`, `ImportSessionModel`, `ImportEntryModel`.
- Extends `ProjectModel`, `SourceRootModel`, `AssetModel`, `ReviewRoundModel` with fields defined in the design.
- Changes `upgrade_database(database_url: str, revision: str = "head") -> None`.

- [ ] **Step 1: Write failing fresh-schema and legacy-upgrade tests**

```python
def test_0002_backfills_legacy_project_without_changing_review_items(legacy_database) -> None:
    before = legacy_database.snapshot_counts()
    upgrade_database(legacy_database.url, "head")
    after = legacy_database.snapshot_counts()
    assert after.assets == before.assets
    assert after.annotation_revisions == before.annotation_revisions
    assert after.review_items == before.review_items
    assert after.review_states == before.review_states
    with Session(make_engine(legacy_database.url)) as session:
        schema = session.scalar(select(ClassSchemaModel))
        rounds = session.scalars(select(ReviewRoundModel).order_by(ReviewRoundModel.number)).all()
        assert schema.names_json == ["Cr", "In", "Pa", "PS", "RS", "Sc"]
        assert [row.name for row in rounds] == ["首轮主动学习", "第二轮质量抽查"]
```

Also assert that `source_roots` permits two sources with the same `kind` but different names.

- [ ] **Step 2: Verify migration tests fail**

Run: `conda run -n steel-review python -m pytest tests/test_resource_migration.py -v`

Expected: failure because revision `0002_resource_scoping` and new ORM models do not exist.

- [ ] **Step 3: Add explicit ORM fields and Alembic operations**

The migration creates the new tables, uses `batch_alter_table()` for SQLite constraint changes, and backfills deterministic legacy values:

```python
STEEL_CLASSES = '["Cr","In","Pa","PS","RS","Sc"]'

op.execute(
    sa.text("""
        INSERT INTO class_schemas (id, project_id, name, version, names_json, created_at)
        SELECT 'steel-defects-v1-' || id, id, 'steel-defects-v1', 1, :names, created_at
        FROM projects
    """).bindparams(names=STEEL_CLASSES)
)
op.execute("UPDATE review_rounds SET name = CASE WHEN kind='audit' THEN '第二轮质量抽查' ELSE '首轮主动学习' END")
op.execute("UPDATE source_roots SET name = kind, mode = 'external', status = 'available'")
```

Do not generate the migration through `Base.metadata.create_all`; enumerate tables, columns, indexes, foreign keys, and constraints explicitly.

- [ ] **Step 4: Run migration, schema, and downgrade-copy tests**

Run: `conda run -n steel-review python -m pytest tests/test_resource_migration.py tests/test_database_bootstrap.py -v`

Expected: all tests pass; downgrade is exercised only on a copied temporary database.

- [ ] **Step 5: Commit**

```powershell
git add -- AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/infrastructure AI/图像异常检测demo/yolov13-main/steel_platform/tests/test_resource_migration.py AI/图像异常检测demo/yolov13-main/steel_platform/tests/test_database_bootstrap.py
git commit -m "feat: add resource scoping migration"
```

### Task 3: SQLAlchemy Unit of Work and Repository Contracts

**Files:**
- Create: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/infrastructure/repositories.py`
- Create: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/infrastructure/uow.py`
- Create: `AI/图像异常检测demo/yolov13-main/steel_platform/tests/test_repository_contracts.py`

**Interfaces:**
- Consumes domain repository protocols from Task 1 and ORM models from Task 2.
- Produces `SqlAlchemyUnitOfWork(session_factory)`, with `.projects`, `.sources`, `.collections`, `.imports`, `.reviews` repositories.

- [ ] **Step 1: Write repository contract tests**

```python
def test_repository_never_returns_resource_from_another_project(uow_factory) -> None:
    with uow_factory() as uow:
        p1 = uow.projects.add_project("one", "one", ("Cr",))
        p2 = uow.projects.add_project("two", "two", ("Cr",))
        source = uow.sources.add(DataSource.new(p1.id, "images", SourceMode.EXTERNAL, "G:/one"))
        uow.commit()
    with uow_factory() as uow:
        assert uow.sources.get(p1.id, source.id) is not None
        assert uow.sources.get(p2.id, source.id) is None


def test_uow_rolls_back_failed_collection_write(uow_factory) -> None:
    with pytest.raises(RuntimeError), uow_factory() as uow:
        uow.collections.add(Collection.new("p1", "temporary"))
        raise RuntimeError("abort")
    with uow_factory() as uow:
        assert uow.collections.list("p1") == []
```

- [ ] **Step 2: Verify tests fail**

Run: `conda run -n steel-review python -m pytest tests/test_repository_contracts.py -v`

Expected: import failure for `infrastructure.uow`.

- [ ] **Step 3: Implement scoped repositories and UoW**

```python
class SqlAlchemyUnitOfWork:
    def __enter__(self) -> "SqlAlchemyUnitOfWork":
        self.session = self._session_factory()
        self.projects = SqlProjectRepository(self.session)
        self.sources = SqlDataSourceRepository(self.session)
        self.collections = SqlCollectionRepository(self.session)
        self.imports = SqlImportRepository(self.session)
        self.reviews = SqlReviewTaskRepository(self.session)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            self.session.rollback()
        self.session.close()

    def commit(self) -> None:
        self.session.commit()
```

Every `get` and `list` query must include the caller's `project_id`; review item queries must join `ReviewRoundModel` and filter both project and round.

- [ ] **Step 4: Run contract and existing database tests**

Run: `conda run -n steel-review python -m pytest tests/test_repository_contracts.py tests/test_database_bootstrap.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add -- AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/infrastructure/repositories.py AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/infrastructure/uow.py AI/图像异常检测demo/yolov13-main/steel_platform/tests/test_repository_contracts.py
git commit -m "refactor: add scoped repositories and unit of work"
```

### Task 4: Project Catalog and Explorer Application Services

**Files:**
- Create: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/application/projects.py`
- Create: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/application/explorer.py`
- Create: `AI/图像异常检测demo/yolov13-main/steel_platform/tests/test_project_explorer.py`

**Interfaces:**
- Produces `ProjectCatalogService.list_projects()`, `create_project(command, idempotency_key)`.
- Produces `ExplorerService.tree(project_id)`, `create_collection()`, `rename_collection()`, `add_members()`, `remove_member()`.
- Tree result uses typed nodes `{id, type, name, count, status, children}` and projects existing sources, review rounds, datasets, models, and inference runs without polymorphic foreign keys.

- [ ] **Step 1: Write failing isolation and tree-projection tests**

```python
def test_explorer_contains_only_requested_project(service, seeded_two_projects) -> None:
    tree = service.tree(seeded_two_projects.first.id)
    ids = {node["id"] for group in tree["groups"] for node in group["children"]}
    assert seeded_two_projects.first_source.id in ids
    assert seeded_two_projects.second_source.id not in ids


def test_task_members_remain_frozen_when_collection_changes(review_service, collection) -> None:
    round_id = review_service.create_from_collection(collection.project_id, collection.id, sample_size=2)
    review_service.explorer.add_members(
        collection.project_id,
        collection.id,
        ["asset-later"],
        expected_revision=collection.revision,
    )
    assert review_service.list_items(collection.project_id, round_id).total == 2
```

- [ ] **Step 2: Verify tests fail**

Run: `conda run -n steel-review python -m pytest tests/test_project_explorer.py -v`

Expected: services do not exist.

- [ ] **Step 3: Implement project catalog and explorer projection**

```python
@dataclass(frozen=True, slots=True)
class CreateProjectCommand:
    name: str
    slug: str
    class_schema_name: str
    class_names: tuple[str, ...]


class ExplorerService:
    def tree(self, project_id: str) -> dict[str, object]:
        with self.uow_factory() as uow:
            project = uow.projects.require(project_id)
            return {
                "project": {"id": project.id, "name": project.name},
                "groups": self._groups(uow, project_id),
            }
```

Collection writes use `expected_revision`; cross-project parent or member IDs raise `NotFoundError`.

- [ ] **Step 4: Run service and domain tests**

Run: `conda run -n steel-review python -m pytest tests/test_project_explorer.py tests/test_workspace_domain.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add -- AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/application/projects.py AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/application/explorer.py AI/图像异常检测demo/yolov13-main/steel_platform/tests/test_project_explorer.py
git commit -m "feat: add project catalog and explorer"
```

### Task 5: Managed and External Folder Imports

**Files:**
- Create: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/application/imports.py`
- Create: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/infrastructure/directory_picker.py`
- Modify: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/infrastructure/artifacts.py`
- Create: `AI/图像异常检测demo/yolov13-main/steel_platform/tests/test_import_workflow.py`

**Interfaces:**
- Produces `DataSourceImportService.start()`, `register_manifest()`, `upload_entry()`, `validate()`, `commit()`, `cancel()`, `rebind()`.
- Produces `WindowsDirectoryPicker.pick() -> Path | None` and `UnavailableDirectoryPicker`.
- Managed upload is idempotent by `(import_id, relative_path, sha256)` and resumes at file granularity.

- [ ] **Step 1: Write failing managed/external/rebind tests**

```python
def test_managed_import_survives_source_removal(import_service, tmp_path) -> None:
    source_dir = build_image_folder(tmp_path / "source")
    run = import_service.import_managed("p1", "managed-images", source_dir)
    shutil.rmtree(source_dir)
    content = import_service.open_asset("p1", run.asset_ids[0]).read()
    assert content.startswith(b"BM")


def test_external_rebind_is_atomic_on_hash_mismatch(import_service, tmp_path) -> None:
    old = build_image_folder(tmp_path / "old")
    source = import_service.import_external("p1", "external-images", old)
    moved = tmp_path / "moved"
    shutil.copytree(old, moved)
    (moved / "Cr_1.bmp").write_bytes(b"changed")
    with pytest.raises(SourceManifestMismatch):
        import_service.rebind("p1", source.id, moved)
    assert import_service.get_source("p1", source.id).locator == old.resolve().as_posix()
```

Add a traversal test that rejects manifest entries such as `../escape.bmp`.

- [ ] **Step 2: Verify tests fail**

Run: `conda run -n steel-review python -m pytest tests/test_import_workflow.py -v`

Expected: import service does not exist.

- [ ] **Step 3: Implement import state machine and atomic artifact write**

```python
def upload_entry(self, project_id: str, import_id: str, entry_id: str, stream: BinaryIO) -> ImportEntry:
    with self.uow_factory() as uow:
        session = uow.imports.require(project_id, import_id)
        entry = uow.imports.require_entry(session.id, entry_id)
        if entry.status == "verified":
            return entry
        ref = self.artifacts.put_stream(stream, media_type=entry.media_type)
        if entry.expected_sha256 and ref.sha256 != entry.expected_sha256:
            raise ImportHashMismatch(entry.relative_path)
        uow.imports.mark_uploaded(entry.id, ref)
        uow.commit()
        return uow.imports.require_entry(session.id, entry.id)
```

The artifact store writes a temporary sibling, flushes and `os.fsync()`s it, then calls `os.replace()` into the SHA256 path. External scanning opens files read-only and never writes beside the source.

- [ ] **Step 4: Run import, artifact, and source-protection tests**

Run: `conda run -n steel-review python -m pytest tests/test_import_workflow.py tests/test_storage_config.py tests/test_architecture_and_maintenance.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add -- AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/application/imports.py AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/infrastructure/directory_picker.py AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/infrastructure/artifacts.py AI/图像异常检测demo/yolov13-main/steel_platform/tests/test_import_workflow.py
git commit -m "feat: add resumable folder imports"
```

### Task 6: Task-Scoped Review Query and Decision Services

**Files:**
- Create: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/application/review_queries.py`
- Create: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/application/review_decisions.py`
- Modify: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/application/review.py`
- Modify: `AI/图像异常检测demo/yolov13-main/steel_platform/tests/test_review_api.py`
- Create: `AI/图像异常检测demo/yolov13-main/steel_platform/tests/test_review_scoping.py`

**Interfaces:**
- Produces `ReviewTaskQueryService.list_rounds(project_id)`, `get_round(project_id, round_id)`, `list_items(project_id, round_id, filters)`, `get_item(project_id, round_id, item_id)`.
- Produces `ReviewDecisionService.decide(project_id, round_id, item_id, command, idempotency_key)`.
- Decision result includes `next_pending_item_id`, `progress`, and `round_completed`.

- [ ] **Step 1: Write the 225/60 regression and cross-scope tests**

```python
def test_round_queues_never_mix(query_service, project_with_two_rounds) -> None:
    first = query_service.list_items(project_with_two_rounds.id, "round-1", ReviewFilters())
    second = query_service.list_items(project_with_two_rounds.id, "round-2", ReviewFilters())
    assert first.total == 225
    assert second.total == 60
    assert {item.round_id for item in first.items} == {"round-1"}
    assert {item.round_id for item in second.items} == {"round-2"}


def test_item_from_other_round_is_not_found(query_service, project_with_two_rounds) -> None:
    with pytest.raises(NotFoundError):
        query_service.get_item(project_with_two_rounds.id, "round-2", "round-1-item")
```

- [ ] **Step 2: Verify tests fail against the global ReviewService**

Run: `conda run -n steel-review python -m pytest tests/test_review_scoping.py -v`

Expected: scoped services are missing or the queue contains 285 items.

- [ ] **Step 3: Move review logic behind scoped services**

```python
@dataclass(frozen=True, slots=True)
class DecisionResult:
    item_id: str
    state: str
    revision: int
    annotation_revision_id: str | None
    replacement_item_id: str | None
    next_pending_item_id: str | None
    progress: dict[str, int]
    round_completed: bool
```

Retain `application.review.ReviewService` only as a compatibility import that delegates to the new services; delete `_project_id()` and all `select(ProjectModel.id).limit(1)` behavior.

- [ ] **Step 4: Run review, rounding repair, and replacement tests**

Run: `conda run -n steel-review python -m pytest tests/test_review_scoping.py tests/test_review_api.py -v`

Expected: all tests pass, including idempotency, 409 stale revision, doubtful drafts, exclusion reasons, and same-class replacement.

- [ ] **Step 5: Commit**

```powershell
git add -- AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/application/review.py AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/application/review_queries.py AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/application/review_decisions.py AI/图像异常检测demo/yolov13-main/steel_platform/tests/test_review_api.py AI/图像异常检测demo/yolov13-main/steel_platform/tests/test_review_scoping.py
git commit -m "fix: scope review queues to project and task"
```

### Task 7: Versioned FastAPI Routers and Compatibility Errors

**Files:**
- Create: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/interfaces/api_models.py`
- Create: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/interfaces/routes/projects.py`
- Create: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/interfaces/routes/imports.py`
- Create: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/interfaces/routes/review.py`
- Create: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/interfaces/routes/assets.py`
- Modify: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/interfaces/api.py`
- Create: `AI/图像异常检测demo/yolov13-main/steel_platform/tests/test_project_api.py`

**Interfaces:**
- Exposes the exact `/api/v1/projects/{project_id}/...` routes in the design specification.
- Old `/api/v1/review/queues` returns `{code:"scope_required", ...}` with status 410.
- Asset content route resolves managed assets through ArtifactStore and external assets through the registered source root.

- [ ] **Step 1: Write failing route and error-shape tests**

```python
def test_legacy_queue_requires_scope(client) -> None:
    response = client.get("/api/v1/review/queues")
    assert response.status_code == 410
    assert response.json()["code"] == "scope_required"
    assert response.json()["request_id"]


def test_scoped_queue_and_asset_reject_other_project(client, seeded_ids) -> None:
    queue = client.get(f"/api/v1/projects/{seeded_ids.p1}/review-rounds/{seeded_ids.round1}/items")
    assert queue.status_code == 200 and queue.json()["total"] == 225
    illegal = client.get(f"/api/v1/projects/{seeded_ids.p2}/assets/{seeded_ids.p1_asset}/content")
    assert illegal.status_code == 404
```

- [ ] **Step 2: Verify tests fail**

Run: `conda run -n steel-review python -m pytest tests/test_project_api.py -v`

Expected: new routes return 404 and legacy queue returns 200.

- [ ] **Step 3: Implement routers and dependency injection**

```python
router = APIRouter(prefix="/api/v1/projects/{project_id}/review-rounds", tags=["review"])

@router.get("/{round_id}/items")
def list_items(project_id: str, round_id: str, filters: Annotated[ReviewFiltersPayload, Query()]):
    return services.review_queries.list_items(project_id, round_id, filters.to_domain())

@legacy_router.get("/api/v1/review/queues")
def legacy_queue() -> None:
    raise ApplicationError("scope_required", "请先选择项目和复核任务", status_code=410)
```

`create_app()` constructs shared engine/session factory/store once, wires services, includes routers, and retains health/error/static handling.

- [ ] **Step 4: Run all API tests**

Run: `conda run -n steel-review python -m pytest tests/test_project_api.py tests/test_review_api.py tests/test_cli_and_ui.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add -- AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/interfaces AI/图像异常检测demo/yolov13-main/steel_platform/tests/test_project_api.py AI/图像异常检测demo/yolov13-main/steel_platform/tests/test_review_api.py AI/图像异常检测demo/yolov13-main/steel_platform/tests/test_cli_and_ui.py
git commit -m "feat: expose project-scoped platform api"
```

### Task 8: Backup-Aware CLI Migration and Project Commands

**Files:**
- Modify: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/application/maintenance.py`
- Modify: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/interfaces/cli.py`
- Create: `AI/图像异常检测demo/yolov13-main/steel_platform/tests/test_cli_resource_commands.py`

**Interfaces:**
- `steel-platform db upgrade` creates a backup when an existing SQLite database is below head, then upgrades and verifies preserved counts.
- Adds `project list --json`, `review round-list --project {project_id} --json`, `source verify`, `source rebind`, and `import status` commands.
- Changes review export to require `--project {project_id} --round-id {round_id}`; no CLI workflow selects the first project implicitly.
- Keeps `serve` startup read-only with respect to schema.

- [ ] **Step 1: Write failing CLI backup and command tests**

```python
def test_db_upgrade_backs_up_existing_database_before_0002(runner, legacy_config) -> None:
    result = runner.invoke(app, ["db", "upgrade", "--config", str(legacy_config)])
    assert result.exit_code == 0
    assert "备份" in result.stdout
    backups = list((legacy_config.parent / "workspace/artifacts/backups").glob("*/platform.db"))
    assert len(backups) == 1


def test_serve_refuses_outdated_database(runner, legacy_config) -> None:
    result = runner.invoke(app, ["project", "check", "--config", str(legacy_config)])
    assert result.exit_code == 2
    assert "steel-platform db upgrade" in result.stdout + (result.stderr or "")
```

- [ ] **Step 2: Verify tests fail**

Run: `conda run -n steel-review python -m pytest tests/test_cli_resource_commands.py -v`

Expected: backup and new commands are absent.

- [ ] **Step 3: Implement backup-aware upgrade and scoped commands**

```python
@db_app.command("upgrade")
def db_upgrade(config: Path = typer.Option(Path("platform.yaml"), "--config", "-c")) -> None:
    settings = _config(config)
    current, head = database_version(settings.database_url)
    backup = create_backup(settings) if current and current != head else None
    before = snapshot_database_counts(settings.database_url) if current else None
    upgrade_database(settings.database_url)
    verify_upgrade_counts(settings.database_url, before)
    typer.echo(f"数据库已升级；备份={backup}" if backup else "数据库已升级")
```

The count snapshot includes primary IDs and grouped review states, not only row totals.

- [ ] **Step 4: Run CLI and backup tests**

Run: `conda run -n steel-review python -m pytest tests/test_cli_resource_commands.py tests/test_cli_and_ui.py tests/test_architecture_and_maintenance.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add -- AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/application/maintenance.py AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/interfaces/cli.py AI/图像异常检测demo/yolov13-main/steel_platform/tests/test_cli_resource_commands.py
git commit -m "feat: add backup-aware resource migration cli"
```

### Task 9: Module-Separated Browser Shell and File Manager

**Files:**
- Modify: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/interfaces/static/index.html`
- Modify: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/interfaces/static/styles.css`
- Remove: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/interfaces/static/qa-fixes.css`
- Create: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/interfaces/static/js/api.js`
- Create: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/interfaces/static/js/state.js`
- Create: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/interfaces/static/js/file-manager.js`
- Create: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/interfaces/static/js/import-wizard.js`
- Create: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/interfaces/static/js/main.js`
- Modify: `AI/图像异常检测demo/yolov13-main/steel_platform/pyproject.toml`
- Modify: `AI/图像异常检测demo/yolov13-main/steel_platform/tests/test_cli_and_ui.py`

**Interfaces:**
- Produces persistent top navigation and project selector.
- File manager renders two columns: resource tree and current content. Details open in a drawer; import uses a modal wizard.
- Persists `project_id`, selected node, and import session in URL/query state, not local absolute paths.

- [ ] **Step 1: Write failing packaged-UI structure tests**

```python
def test_browser_shell_has_module_separated_file_manager(client) -> None:
    html = client.get("/").text
    assert 'id="projectSelector"' in html
    assert 'id="resourceTree"' in html
    assert 'id="assetTable"' in html
    assert 'id="assetDrawer"' in html
    assert 'id="importWizard"' in html
    assert '/static/js/main.js' in html
    assert '/static/app.js' not in html
```

- [ ] **Step 2: Verify the UI test fails**

Run: `conda run -n steel-review python -m pytest tests/test_cli_and_ui.py::test_browser_shell_has_module_separated_file_manager -v`

Expected: assertions fail because the current monolithic page is still packaged.

- [ ] **Step 3: Implement the app shell and file manager modules**

```javascript
export const state = {
  projectId: new URLSearchParams(location.search).get("project"),
  node: new URLSearchParams(location.search).get("node"),
  dirty: false,
};

export async function loadExplorer(projectId) {
  const tree = await api(`/api/v1/projects/${projectId}/explorer`);
  renderResourceTree(tree.groups);
  renderCurrentNode(tree, state.node);
}
```

The import wizard has five explicit steps: select folder, scan preview, choose `managed` or `external`, validate, commit. It displays counts for images, labels, unsupported files, duplicates, and errors before commit. The details drawer is closed by default and never consumes layout width.

- [ ] **Step 4: Run packaging tests and static syntax checks**

Run: `conda run -n steel-review python -m pytest tests/test_cli_and_ui.py -v`

Run: `node --check src/steel_platform/interfaces/static/js/main.js`

Expected: pytest passes and Node exits 0 without syntax errors.

- [ ] **Step 5: Commit**

```powershell
git add -- AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/interfaces/static AI/图像异常检测demo/yolov13-main/steel_platform/pyproject.toml AI/图像异常检测demo/yolov13-main/steel_platform/tests/test_cli_and_ui.py
git commit -m "feat: add project file manager ui"
```

### Task 10: Dedicated Task-Scoped Review Workspace

**Files:**
- Create: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/interfaces/static/js/review-workspace.js`
- Modify: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/interfaces/static/index.html`
- Modify: `AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/interfaces/static/styles.css`
- Create: `AI/图像异常检测demo/yolov13-main/steel_platform/tests/test_review_ui_contract.py`

**Interfaces:**
- Review route state requires `project` and `round` query parameters.
- Queue load calls only `/projects/{project_id}/review-rounds/{round_id}/items`.
- Save uses the scoped decision route and trusts returned `next_pending_item_id`/`round_completed`.
- Preserves A/S/D/X/R/Delete/Ctrl+Z/Ctrl+Y/Q shortcuts and dirty-state warning.

- [ ] **Step 1: Write failing review UI contract tests**

```python
def test_review_javascript_never_calls_global_queue() -> None:
    source = REVIEW_JS.read_text(encoding="utf-8")
    assert "/api/v1/review/queues" not in source
    assert "review-rounds/${state.roundId}/items" in source
    assert "round_completed" in source
    for key in ("KeyA", "KeyS", "KeyD", "KeyX", "KeyR", "Delete", "KeyQ"):
        assert key in source
```

- [ ] **Step 2: Verify the test fails**

Run: `conda run -n steel-review python -m pytest tests/test_review_ui_contract.py -v`

Expected: `review-workspace.js` is missing.

- [ ] **Step 3: Move canvas/editor behavior into the scoped workspace**

```javascript
async function saveDecision(decision) {
  const result = await api(itemUrl(state.current.id) + "/decision", {
    method: "PUT",
    headers: {"Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID()},
    body: JSON.stringify({expected_revision: state.current.revision, decision, boxes: state.boxes, note: note.value}),
  });
  setDirty(false);
  if (result.round_completed) return renderCompletion(result.progress);
  return selectItem(result.next_pending_item_id);
}
```

Completion view shows counts, accepted/corrected/doubtful/excluded rates, report link, and “返回任务列表”; it does not leave a blank canvas.

- [ ] **Step 4: Run UI contract and review API tests**

Run: `conda run -n steel-review python -m pytest tests/test_review_ui_contract.py tests/test_review_api.py tests/test_project_api.py -v`

Run: `node --check src/steel_platform/interfaces/static/js/review-workspace.js`

Expected: all checks pass.

- [ ] **Step 5: Commit**

```powershell
git add -- AI/图像异常检测demo/yolov13-main/steel_platform/src/steel_platform/interfaces/static AI/图像异常检测demo/yolov13-main/steel_platform/tests/test_review_ui_contract.py
git commit -m "feat: add task scoped review workspace"
```

### Task 11: End-to-End Regression, Real Workspace Upgrade, and Tutorial

**Files:**
- Modify: `AI/图像异常检测demo/yolov13-main/steel_platform/tests/test_vertical_workflow.py`
- Create: `AI/图像异常检测demo/yolov13-main/steel_platform/tests/test_resource_isolation_e2e.py`
- Modify: `AI/图像异常检测demo/yolov13-main/steel_platform/README.md`
- Create: `AI/图像异常检测demo/yolov13-main/steel_platform/config/platform.example.yaml`

**Interfaces:**
- Adds a full temporary two-project test and live verification commands.
- Documents background, goal, architecture, import modes, migration, daily workflow, recovery, and expansion path.

- [ ] **Step 1: Write the full two-project acceptance test**

```python
def test_two_projects_and_two_rounds_are_isolated(platform_fixture) -> None:
    p1 = platform_fixture.create_project("steel-a", classes=STEEL_CLASSES)
    p2 = platform_fixture.create_project("steel-b", classes=STEEL_CLASSES)
    platform_fixture.seed_review_round(p1, count=225, round_id="r1")
    platform_fixture.seed_review_round(p1, count=60, round_id="r2")
    platform_fixture.import_images(p2, count=6, mode="managed")
    assert platform_fixture.queue(p1, "r1").total == 225
    assert platform_fixture.queue(p1, "r2").total == 60
    assert platform_fixture.overview(p2).assets.images == 6
    assert platform_fixture.overview(p1).assets.images != 6
```

Also snapshot and compare source hashes before and after the full workflow.

- [ ] **Step 2: Run the complete automated suite before touching the real workspace**

Run: `conda run -n steel-review python -m pytest -q`

Expected: all tests pass with no failures or errors.

- [ ] **Step 3: Exercise migration on a copy of the real SQLite database**

Run from `AI/图像异常检测demo/yolov13-main/steel_platform`:

```powershell
steel-platform backup create --config config/platform.local.yaml
$dbPath = conda run -n steel-review python -c "from pathlib import Path; from steel_platform.infrastructure.config import load_settings; print(load_settings(Path('config/platform.local.yaml')).database_path)"
$copyPath = Join-Path $env:TEMP "steel-platform-migration-test.db"
Copy-Item -LiteralPath $dbPath.Trim() -Destination $copyPath -Force
$env:STEEL_PLATFORM_DATABASE_URL = "sqlite:///" + $copyPath.Replace('\\','/')
steel-platform db upgrade --config config/platform.local.yaml
steel-platform project check --config config/platform.local.yaml
Remove-Item Env:STEEL_PLATFORM_DATABASE_URL
```

Expected: backup manifest verifies, copied database reaches `0002_resource_scoping`, project check passes, and exported counts show225/60 separate rounds with unchanged IDs and states.

- [ ] **Step 4: Upgrade the real workspace and verify protected-source hashes**

Stop the local server, then run:

```powershell
steel-platform db upgrade --config config/platform.local.yaml
steel-platform project check --config config/platform.local.yaml
steel-platform artifacts verify --config config/platform.local.yaml
$projectId = (steel-platform project list --config config/platform.local.yaml --json | ConvertFrom-Json)[0].id
$rounds = steel-platform review round-list --project $projectId --config config/platform.local.yaml --json | ConvertFrom-Json
$round1 = ($rounds | Where-Object { $_.number -eq 1 -and $_.kind -eq 'training' }).id
$round2 = ($rounds | Where-Object { $_.number -eq 2 -and $_.kind -eq 'audit' }).id
steel-platform review export-progress --project $projectId --round-id $round1 --config config/platform.local.yaml --output artifacts/round-1-after.csv
steel-platform review export-progress --project $projectId --round-id $round2 --config config/platform.local.yaml --output artifacts/round-2-after.csv
```

Expected: database is at head, artifact verification reports zero invalid entries, round exports contain225 and60 rows respectively, and the protected-source manifest is unchanged.

- [ ] **Step 5: Update the Chinese tutorial and run browser QA**

README order: background knowledge → current goal → architecture → create/switch project → managed import → external mount/rebind → create collection → enter a scoped review task → completion report → migration recovery → future training/inference/monitoring expansion.

Start the server and verify in the built-in browser at desktop and narrow viewport:

1. Project selector changes all counts and resource tree nodes.
2. File manager imports a temporary six-image managed folder and displays it under only the chosen project.
3. Round 1 queue reports225; round 2 reports60; no visible page reports285 as a current queue.
4. Existing canvas drawing, moving, resizing, deletion, undo/redo and shortcuts work.
5. A completed task renders the summary instead of a blank image.
6. Browser console has no errors and `/health/ready` returns200.

- [ ] **Step 6: Run final verification and commit**

Run:

```powershell
conda run -n steel-review python -m pytest -q
git diff --check
git status --short
```

Expected: full suite passes; diff check is clean; only intended source, tests, docs, migration, and example-configuration changes are present. `config/platform.local.yaml` remains ignored and unmodified.

```powershell
git add -- AI/图像异常检测demo/yolov13-main/steel_platform docs/superpowers/plans/2026-07-17-steel-platform-resource-isolation.md
git commit -m "feat: isolate platform projects and review tasks"
```
