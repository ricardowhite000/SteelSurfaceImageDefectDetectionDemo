from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from steel_platform.application.errors import NotFoundError
from steel_platform.application.review_decisions import ReviewDecisionCommand, ReviewDecisionService
from steel_platform.application.review_queries import ReviewFilters, ReviewTaskQueryService
from steel_platform.domain.annotations import AnnotationBox
from steel_platform.infrastructure.artifacts import LocalArtifactStore
from steel_platform.infrastructure.models import (
    AnnotationRevisionModel,
    AssetModel,
    Base,
    CandidatePredictionModel,
    ClassSchemaModel,
    DomainEventModel,
    InferenceRunModel,
    ProjectModel,
    ReviewDraftModel,
    ReviewItemModel,
    ReviewRoundModel,
)
from steel_platform.infrastructure.repositories import (
    SqlIdempotencyRepository,
    SqlReviewTaskRepository,
)
from steel_platform.infrastructure.uow import SqlAlchemyUnitOfWork
from steel_platform.domain.workspace import (
    ConcurrentAllocationError,
    IdempotencyRecord,
    IdempotencyReservationConflict,
)


@pytest.fixture
def project_with_two_rounds() -> SimpleNamespace:
    return SimpleNamespace(id="project-one", first_round_id="round-1", second_round_id="round-2")


@pytest.fixture
def query_service(
    project_with_two_rounds: SimpleNamespace,
) -> ReviewTaskQueryService:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, class_=Session)
    with session_factory() as session:
        session.add(ProjectModel(id=project_with_two_rounds.id, name="one"))
        session.add_all(
            (
                ReviewRoundModel(
                    id=project_with_two_rounds.first_round_id,
                    project_id=project_with_two_rounds.id,
                    number=1,
                    kind="training",
                    per_class=45,
                    target_count=225,
                ),
                ReviewRoundModel(
                    id=project_with_two_rounds.second_round_id,
                    project_id=project_with_two_rounds.id,
                    number=2,
                    kind="audit",
                    per_class=10,
                    target_count=60,
                ),
            )
        )
        session.add_all(
            ReviewItemModel(
                id=f"round-1-item-{index}",
                round_id=project_with_two_rounds.first_round_id,
                image_asset_id=f"asset-1-{index}",
                filename=f"first-{index}.bmp",
                expected_class_id=index % 6,
                source_status="ok",
                selection_reason="sampled",
                split_role="train",
                rank=index + 1,
            )
            for index in range(225)
        )
        session.add_all(
            ReviewItemModel(
                id=f"round-2-item-{index}",
                round_id=project_with_two_rounds.second_round_id,
                image_asset_id=f"asset-2-{index}",
                filename=f"second-{index}.bmp",
                expected_class_id=index % 6,
                source_status="ok",
                selection_reason="sampled",
                split_role="audit",
                rank=index + 1,
            )
            for index in range(60)
        )
        session.commit()
    uow_factory: Callable[[], SqlAlchemyUnitOfWork] = lambda: SqlAlchemyUnitOfWork(session_factory)
    return ReviewTaskQueryService(uow_factory, class_names=("Cr", "In", "Pa", "PS", "RS", "Sc"))


def test_round_queues_never_mix(
    query_service: ReviewTaskQueryService,
    project_with_two_rounds: SimpleNamespace,
) -> None:
    first = query_service.list_items(
        project_with_two_rounds.id,
        project_with_two_rounds.first_round_id,
        ReviewFilters(),
    )
    second = query_service.list_items(
        project_with_two_rounds.id,
        project_with_two_rounds.second_round_id,
        ReviewFilters(),
    )

    assert first.total == 225
    assert second.total == 60
    assert {item.round_id for item in first.items} == {project_with_two_rounds.first_round_id}
    assert {item.round_id for item in second.items} == {project_with_two_rounds.second_round_id}


def test_item_from_other_round_is_not_found(
    query_service: ReviewTaskQueryService,
    project_with_two_rounds: SimpleNamespace,
) -> None:
    with pytest.raises(NotFoundError):
        query_service.get_item(
            project_with_two_rounds.id,
            project_with_two_rounds.second_round_id,
            "round-1-item-0",
        )


def test_round_and_item_from_another_project_are_not_found(
    query_service: ReviewTaskQueryService,
    project_with_two_rounds: SimpleNamespace,
) -> None:
    with pytest.raises(NotFoundError):
        query_service.get_round("project-two", project_with_two_rounds.first_round_id)
    with pytest.raises(NotFoundError):
        query_service.get_item(
            "project-two",
            project_with_two_rounds.first_round_id,
            "round-1-item-0",
        )


def test_item_names_use_each_round_schema_snapshot_not_global_names() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, class_=Session)
    with session_factory() as session:
        session.add_all(
            (
                ProjectModel(id="schema-project-a", name="schema-a", class_schema_id="schema-a-v1"),
                ProjectModel(id="schema-project-b", name="schema-b", class_schema_id="schema-b-v2"),
                ClassSchemaModel(
                    id="schema-a-v1",
                    project_id="schema-project-a",
                    name="defects",
                    version=1,
                    names_json=("Cr", "In"),
                ),
                ClassSchemaModel(
                    id="schema-b-v1",
                    project_id="schema-project-b",
                    name="defects",
                    version=1,
                    names_json=("In", "Cr"),
                ),
                ClassSchemaModel(
                    id="schema-b-v2",
                    project_id="schema-project-b",
                    name="defects",
                    version=2,
                    names_json=("Cr", "In", "Pa"),
                ),
            )
        )
        session.add_all(
            (
                ReviewRoundModel(
                    id="schema-round-a",
                    project_id="schema-project-a",
                    class_schema_id="schema-a-v1",
                    number=1,
                    kind="training",
                    per_class=1,
                ),
                ReviewRoundModel(
                    id="schema-round-b",
                    project_id="schema-project-b",
                    class_schema_id="schema-b-v1",
                    number=1,
                    kind="training",
                    per_class=1,
                ),
            )
        )
        session.add_all(
            (
                ReviewItemModel(
                    id="schema-item-a",
                    round_id="schema-round-a",
                    image_asset_id="schema-asset-a",
                    filename="Cr.bmp",
                    expected_class_id=0,
                    source_status="ok",
                    selection_reason="sampled",
                    split_role="train",
                    rank=1,
                ),
                ReviewItemModel(
                    id="schema-item-b",
                    round_id="schema-round-b",
                    image_asset_id="schema-asset-b",
                    filename="In.bmp",
                    expected_class_id=0,
                    source_status="ok",
                    selection_reason="sampled",
                    split_role="train",
                    rank=1,
                ),
            )
        )
        session.commit()

    uow_factory: Callable[[], SqlAlchemyUnitOfWork] = lambda: SqlAlchemyUnitOfWork(session_factory)
    service = ReviewTaskQueryService(
        uow_factory,
        class_names=("global-zero", "global-one", "global-two"),
    )

    first = service.list_items("schema-project-a", "schema-round-a").items[0]
    second = service.list_items("schema-project-b", "schema-round-b").items[0]
    second_round = service.get_round("schema-project-b", "schema-round-b")

    assert first.expected_class_name == "Cr"
    assert second.expected_class_name == "In"
    assert second_round.class_names == ("In", "Cr")


class _TrackingAnnotationCodec:
    def __init__(self) -> None:
        self.encoded = 0

    def encode(self, boxes: Sequence[AnnotationBox]) -> bytes:
        self.encoded += 1
        return json.dumps([asdict(box) for box in boxes], sort_keys=True).encode("utf-8")

    def decode(self, content: bytes) -> tuple[AnnotationBox, ...]:
        return tuple(AnnotationBox(**box) for box in json.loads(content.decode("utf-8")))


def test_accepted_decision_validates_class_id_against_round_schema_snapshot(tmp_path: Path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, class_=Session)
    codec = _TrackingAnnotationCodec()
    with session_factory() as session:
        session.add(
            ProjectModel(
                id="decision-schema-project",
                name="decision-schema",
                class_schema_id="decision-schema-v2",
            )
        )
        session.add_all(
            (
                ClassSchemaModel(
                    id="decision-schema-v1",
                    project_id="decision-schema-project",
                    name="defects",
                    version=1,
                    names_json=("In", "Cr"),
                ),
                ClassSchemaModel(
                    id="decision-schema-v2",
                    project_id="decision-schema-project",
                    name="defects",
                    version=2,
                    names_json=("Cr", "In", "Pa"),
                ),
                ReviewRoundModel(
                    id="decision-schema-round",
                    project_id="decision-schema-project",
                    class_schema_id="decision-schema-v1",
                    number=1,
                    kind="audit",
                    per_class=1,
                ),
                ReviewItemModel(
                    id="decision-schema-valid",
                    round_id="decision-schema-round",
                    image_asset_id="decision-schema-asset-valid",
                    filename="In.bmp",
                    expected_class_id=0,
                    source_status="ok",
                    selection_reason="sampled",
                    split_role="audit",
                    rank=1,
                ),
                ReviewItemModel(
                    id="decision-schema-invalid",
                    round_id="decision-schema-round",
                    image_asset_id="decision-schema-asset-invalid",
                    filename="Pa.bmp",
                    expected_class_id=2,
                    source_status="ok",
                    selection_reason="sampled",
                    split_role="audit",
                    rank=2,
                ),
            )
        )
        session.commit()
    uow_factory: Callable[[], SqlAlchemyUnitOfWork] = lambda: SqlAlchemyUnitOfWork(session_factory)
    service = ReviewDecisionService(
        uow_factory,
        artifact_store=LocalArtifactStore(tmp_path / "schema-artifacts"),
        annotation_codec=codec,
        class_names=("global-zero", "global-one", "global-two"),
    )

    accepted = service.decide(
        "decision-schema-project",
        "decision-schema-round",
        "decision-schema-valid",
        _command("accepted", boxes=(AnnotationBox(0, 0.5, 0.5, 0.25, 0.25),)),
        "schema-valid",
    )
    assert accepted.state == "accepted"
    assert codec.encoded == 1

    with pytest.raises(Exception) as invalid:
        service.decide(
            "decision-schema-project",
            "decision-schema-round",
            "decision-schema-invalid",
            _command("accepted", boxes=(AnnotationBox(2, 0.5, 0.5, 0.25, 0.25),)),
            "schema-invalid",
        )
    assert getattr(invalid.value, "code", None) == "class_mismatch"
    assert codec.encoded == 1


class _JsonAnnotationCodec:
    def encode(self, boxes: Sequence[AnnotationBox]) -> bytes:
        return json.dumps([asdict(box) for box in boxes], sort_keys=True).encode("utf-8")

    def decode(self, content: bytes) -> tuple[AnnotationBox, ...]:
        return tuple(AnnotationBox(**box) for box in json.loads(content.decode("utf-8")))


@dataclass(frozen=True)
class _DecisionContext:
    service: ReviewDecisionService
    session_factory: sessionmaker[Session]
    project_id: str
    round_id: str
    other_round_id: str
    item_id: str
    store: LocalArtifactStore


@pytest.fixture
def decision_context(tmp_path: Path) -> _DecisionContext:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, class_=Session)
    store = LocalArtifactStore(tmp_path / "artifacts")
    codec = _JsonAnnotationCodec()
    candidate_ref = store.put_bytes(
        codec.encode((AnnotationBox(0, 0.5, 0.5, 0.25, 0.25),)),
        media_type="text/yolo",
    )
    with session_factory() as session:
        session.add(ProjectModel(id="decision-project", name="decision"))
        session.add_all(
            (
                ReviewRoundModel(
                    id="decision-round",
                    project_id="decision-project",
                    number=1,
                    kind="training",
                    per_class=1,
                    target_count=1,
                ),
                ReviewRoundModel(
                    id="other-round",
                    project_id="decision-project",
                    number=2,
                    kind="audit",
                    per_class=1,
                    target_count=1,
                ),
            )
        )
        session.add(
            InferenceRunModel(
                id="inference",
                project_id="decision-project",
                name="candidates",
                status="succeeded",
            )
        )
        for index in range(3):
            session.add(
                AssetModel(
                    id=f"decision-asset-{index}",
                    project_id="decision-project",
                    kind="image",
                    relative_path=f"Cr_{index}.bmp",
                    sha256=str(index) * 64,
                    size_bytes=1,
                    media_type="image/bmp",
                )
            )
        session.flush()
        for index in range(3):
            revision = AnnotationRevisionModel(
                id=f"machine-revision-{index}",
                project_id="decision-project",
                image_asset_id=f"decision-asset-{index}",
                origin="machine",
                storage_key=candidate_ref.storage_key,
                sha256=candidate_ref.sha256,
                box_count=1,
            )
            session.add(revision)
            session.add(
                CandidatePredictionModel(
                    id=f"candidate-{index}",
                    project_id="decision-project",
                    inference_run_id="inference",
                    image_asset_id=f"decision-asset-{index}",
                    annotation_revision_id=revision.id,
                    filename=f"Cr_{index}.bmp",
                    expected_class_id=0,
                    predicted_class_ids="0",
                    box_count=1,
                    min_confidence=0.2 + index / 10,
                    max_confidence=0.5,
                    source_status="low_confidence",
                    diversity_hash=index,
                )
            )
        session.add(
            ReviewItemModel(
                id="decision-item",
                round_id="decision-round",
                image_asset_id="decision-asset-0",
                candidate_revision_id="machine-revision-0",
                filename="Cr_0.bmp",
                expected_class_id=0,
                source_status="low_confidence",
                selection_reason="risk",
                split_role="train",
                rank=1,
            )
        )
        session.add(
            ReviewItemModel(
                id="other-item",
                round_id="other-round",
                image_asset_id="decision-asset-2",
                candidate_revision_id="machine-revision-2",
                filename="Cr_2.bmp",
                expected_class_id=0,
                source_status="low_confidence",
                selection_reason="audit",
                split_role="audit",
                rank=1,
            )
        )
        session.commit()
    uow_factory: Callable[[], SqlAlchemyUnitOfWork] = lambda: SqlAlchemyUnitOfWork(session_factory)
    return _DecisionContext(
        service=ReviewDecisionService(
            uow_factory,
            artifact_store=store,
            annotation_codec=codec,
            class_names=("Cr", "In", "Pa", "PS", "RS", "Sc"),
        ),
        session_factory=session_factory,
        project_id="decision-project",
        round_id="decision-round",
        other_round_id="other-round",
        item_id="decision-item",
        store=store,
    )


def _command(
    action: str,
    *,
    expected_revision: int = 0,
    boxes: tuple[AnnotationBox, ...] = (AnnotationBox(0, 0.5, 0.5, 0.25, 0.25),),
    note: str = "reviewed",
) -> ReviewDecisionCommand:
    return ReviewDecisionCommand(
        expected_revision=expected_revision,
        action=action,
        boxes=boxes,
        note=note,
    )


def test_decision_is_payload_bound_idempotent_and_task_local(
    decision_context: _DecisionContext,
) -> None:
    first = decision_context.service.decide(
        decision_context.project_id,
        decision_context.round_id,
        decision_context.item_id,
        _command("accepted"),
        "accept-once",
    )
    replay = decision_context.service.decide(
        decision_context.project_id,
        decision_context.round_id,
        decision_context.item_id,
        _command("accepted"),
        "accept-once",
    )

    assert replay == first
    assert first.next_pending_item_id is None
    assert first.progress["accepted"] == 1
    assert first.progress["pending"] == 0
    assert first.round_completed is True
    with decision_context.session_factory() as session:
        human_revisions = session.scalar(
            select(func.count())
            .select_from(AnnotationRevisionModel)
            .where(AnnotationRevisionModel.origin == "human")
        )
        events = session.scalar(select(func.count()).select_from(DomainEventModel))
        assert human_revisions == events == 1

    with pytest.raises(Exception) as conflict:
        decision_context.service.decide(
            decision_context.project_id,
            decision_context.round_id,
            decision_context.item_id,
            _command("accepted", note="different payload"),
            "accept-once",
        )
    assert getattr(conflict.value, "code", None) == "idempotency_conflict"


def test_decision_rejects_empty_key_stale_revision_and_cross_scope_item(
    decision_context: _DecisionContext,
) -> None:
    with pytest.raises(Exception) as empty:
        decision_context.service.decide(
            decision_context.project_id,
            decision_context.round_id,
            decision_context.item_id,
            _command("accepted"),
            "   ",
        )
    assert getattr(empty.value, "status_code", None) == 422

    with pytest.raises(NotFoundError):
        decision_context.service.decide(
            decision_context.project_id,
            decision_context.other_round_id,
            decision_context.item_id,
            _command("accepted"),
            "wrong-round",
        )
    with pytest.raises(NotFoundError):
        decision_context.service.decide(
            "another-project",
            decision_context.round_id,
            decision_context.item_id,
            _command("accepted"),
            "wrong-project",
        )

    decision_context.service.decide(
        decision_context.project_id,
        decision_context.round_id,
        decision_context.item_id,
        _command("accepted"),
        "accepted",
    )
    with pytest.raises(Exception) as stale:
        decision_context.service.decide(
            decision_context.project_id,
            decision_context.round_id,
            decision_context.item_id,
            _command("corrected", expected_revision=0),
            "stale",
        )
    assert getattr(stale.value, "code", None) == "revision_conflict"


def test_doubtful_saves_draft_and_adds_one_same_task_replacement(
    decision_context: _DecisionContext,
) -> None:
    result = decision_context.service.decide(
        decision_context.project_id,
        decision_context.round_id,
        decision_context.item_id,
        _command("doubtful", note="unclear"),
        "doubtful",
    )

    assert result.annotation_revision_id is None
    assert result.replacement_item_id == result.next_pending_item_id
    assert result.round_completed is False
    with decision_context.session_factory() as session:
        draft = session.get(ReviewDraftModel, decision_context.item_id)
        replacement = session.get(ReviewItemModel, result.replacement_item_id)
        assert draft is not None and draft.note == "unclear"
        assert replacement is not None
        assert replacement.round_id == decision_context.round_id
        assert replacement.expected_class_id == 0
        assert session.scalar(
            select(func.count())
            .select_from(ReviewItemModel)
            .where(ReviewItemModel.round_id == decision_context.round_id)
        ) == 2


def test_excluded_requires_reason_creates_no_label_and_replacement_replays(
    decision_context: _DecisionContext,
) -> None:
    with pytest.raises(Exception) as invalid:
        decision_context.service.decide(
            decision_context.project_id,
            decision_context.round_id,
            decision_context.item_id,
            _command("excluded", boxes=(), note=""),
            "exclude-invalid",
        )
    assert getattr(invalid.value, "status_code", None) == 422

    before = None
    with decision_context.session_factory() as session:
        before = session.scalar(select(func.count()).select_from(AnnotationRevisionModel))
    result = decision_context.service.decide(
        decision_context.project_id,
        decision_context.round_id,
        decision_context.item_id,
        _command("excluded", boxes=(), note="not a defect"),
        "exclude",
    )
    replay = decision_context.service.decide(
        decision_context.project_id,
        decision_context.round_id,
        decision_context.item_id,
        _command("excluded", boxes=(), note="not a defect"),
        "exclude",
    )

    assert replay == result
    assert result.annotation_revision_id is None
    with decision_context.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AnnotationRevisionModel)) == before
        assert session.scalar(
            select(func.count())
            .select_from(ReviewItemModel)
            .where(ReviewItemModel.round_id == decision_context.round_id)
        ) == 2


def test_replacement_locks_the_project_scoped_round_before_reading_quota() -> None:
    statements: list[object] = []

    class _CapturingSession:
        def scalar(self, statement: object) -> None:
            statements.append(statement)
            return None

    repository = SqlReviewTaskRepository(_CapturingSession())  # type: ignore[arg-type]

    assert repository.add_replacement("locked-project", "locked-round", "locked-item") is None
    assert len(statements) == 1
    sql = str(
        statements[0].compile(  # type: ignore[union-attr]
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "FOR UPDATE" in sql
    assert "review_rounds.project_id = 'locked-project'" in sql
    assert "review_rounds.id = 'locked-round'" in sql


def test_sqlite_database_lock_is_translated_at_idempotency_reservation() -> None:
    class _LockedSession:
        def add(self, _model: object) -> None:
            pass

        def flush(self) -> None:
            raise OperationalError(
                "INSERT INTO idempotency_records",
                {},
                sqlite3.OperationalError("database is locked"),
            )

    repository = SqlIdempotencyRepository(_LockedSession())  # type: ignore[arg-type]

    with pytest.raises(IdempotencyReservationConflict):
        repository.reserve(IdempotencyRecord("locked-key", "locked-scope", {}))


def _sqlite_error_code(error: BaseException, code: int) -> BaseException:
    error.sqlite_errorcode = code  # type: ignore[attr-defined]
    return error


@pytest.mark.parametrize("code", (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED))
def test_sqlite_busy_and_locked_codes_translate_at_idempotency_reservation(code: int) -> None:
    error = OperationalError(
        "INSERT INTO idempotency_records",
        {},
        _sqlite_error_code(sqlite3.OperationalError("busy"), code),
    )

    class _BusySession:
        def add(self, _model: object) -> None:
            pass

        def flush(self) -> None:
            raise error

    with pytest.raises(IdempotencyReservationConflict):
        SqlIdempotencyRepository(_BusySession()).reserve(  # type: ignore[arg-type]
            IdempotencyRecord("busy-key", "busy-scope", {})
        )


def test_missing_table_operational_error_is_not_misclassified_as_concurrency() -> None:
    error = OperationalError(
        "INSERT INTO idempotency_records",
        {},
        sqlite3.OperationalError("no such table: idempotency_records"),
    )

    class _BrokenSession:
        def add(self, _model: object) -> None:
            pass

        def flush(self) -> None:
            raise error

    with pytest.raises(OperationalError) as raised:
        SqlIdempotencyRepository(_BrokenSession()).reserve(  # type: ignore[arg-type]
            IdempotencyRecord("broken-key", "broken-scope", {})
        )
    assert raised.value is error


@pytest.mark.parametrize(
    "code",
    (sqlite3.SQLITE_CONSTRAINT_NOTNULL, sqlite3.SQLITE_CONSTRAINT_FOREIGNKEY),
)
def test_non_unique_integrity_error_is_not_misclassified_as_idempotency(code: int) -> None:
    error = IntegrityError(
        "INSERT INTO idempotency_records",
        {},
        _sqlite_error_code(sqlite3.IntegrityError("constraint failed"), code),
    )

    class _InvalidSession:
        def add(self, _model: object) -> None:
            pass

        def flush(self) -> None:
            raise error

    with pytest.raises(IntegrityError) as raised:
        SqlIdempotencyRepository(_InvalidSession()).reserve(  # type: ignore[arg-type]
            IdempotencyRecord("invalid-key", "invalid-scope", {})
        )
    assert raised.value is error


def _replacement_repository_with_flush_error(
    error: IntegrityError | OperationalError,
) -> tuple[SqlReviewTaskRepository, dict[str, bool]]:
    locked_round = SimpleNamespace(kind="training")
    item = SimpleNamespace(
        id="collision-item",
        expected_class_id=0,
        split_role="train",
    )
    candidate = SimpleNamespace(
        image_asset_id="collision-asset",
        annotation_revision_id=None,
        filename="Cr_collision.bmp",
        expected_class_id=0,
        source_status="low_confidence",
        min_confidence=0.1,
        max_confidence=0.2,
        box_count=1,
    )
    scalar_values = iter((locked_round, item, 1, 0, 1))
    state = {"nested_exited": False}

    class _Nested:
        def __enter__(self) -> None:
            return None

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            state["nested_exited"] = True

    class _CollisionSession:
        def scalar(self, _statement: object) -> object:
            return next(scalar_values)

        def scalars(self, _statement: object) -> tuple[object, ...]:
            return (candidate,)

        def begin_nested(self) -> _Nested:
            return _Nested()

        def add(self, _model: object) -> None:
            pass

        def flush(self) -> None:
            raise error

    return SqlReviewTaskRepository(_CollisionSession()), state  # type: ignore[arg-type]


def test_replacement_unique_collision_rolls_back_savepoint_and_translates() -> None:
    error = IntegrityError(
        "INSERT INTO review_items",
        {},
        _sqlite_error_code(
            sqlite3.IntegrityError(
                "UNIQUE constraint failed: review_items.round_id, review_items.image_asset_id"
            ),
            sqlite3.SQLITE_CONSTRAINT_UNIQUE,
        ),
    )
    repository, state = _replacement_repository_with_flush_error(error)


    with pytest.raises(ConcurrentAllocationError):
        repository.add_replacement("collision-project", "collision-round", "collision-item")
    assert state["nested_exited"] is True


def test_replacement_not_null_collision_propagates_original_integrity_error() -> None:
    error = IntegrityError(
        "INSERT INTO review_items",
        {},
        _sqlite_error_code(
            sqlite3.IntegrityError("NOT NULL constraint failed: review_items.filename"),
            sqlite3.SQLITE_CONSTRAINT_NOTNULL,
        ),
    )
    repository, state = _replacement_repository_with_flush_error(error)

    with pytest.raises(IntegrityError) as raised:
        repository.add_replacement("collision-project", "collision-round", "collision-item")
    assert raised.value is error
    assert state["nested_exited"] is True


def test_uncommitted_reservation_race_returns_retryable_concurrency_conflict(
    decision_context: _DecisionContext,
) -> None:
    calls = 0

    class _RacingIdempotency:
        def __init__(self, inner: object) -> None:
            self._inner = inner

        def get(self, key: str) -> object:
            return self._inner.get(key)  # type: ignore[attr-defined]

        def reserve(self, record: IdempotencyRecord) -> None:
            raise IdempotencyReservationConflict(record.key)

        def set_response(self, key: str, response: dict[str, object]) -> None:
            self._inner.set_response(key, response)  # type: ignore[attr-defined]

    class _RacingUow:
        def __init__(self, inner: SqlAlchemyUnitOfWork) -> None:
            self._inner = inner

        def __enter__(self) -> _RacingUow:
            self._inner.__enter__()
            self.projects = self._inner.projects
            self.review_tasks = self._inner.review_tasks
            self.idempotency = _RacingIdempotency(self._inner.idempotency)
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            self._inner.__exit__(exc_type, exc, tb)  # type: ignore[arg-type]

        def commit(self) -> None:
            self._inner.commit()

        def rollback(self) -> None:
            self._inner.rollback()

    def racing_factory() -> object:
        nonlocal calls
        calls += 1
        inner = SqlAlchemyUnitOfWork(decision_context.session_factory)
        return _RacingUow(inner) if calls == 2 else inner

    service = ReviewDecisionService(
        racing_factory,  # type: ignore[arg-type]
        artifact_store=decision_context.store,
        annotation_codec=_JsonAnnotationCodec(),
        class_names=("Cr", "In", "Pa", "PS", "RS", "Sc"),
    )

    with pytest.raises(Exception) as conflict:
        service.decide(
            decision_context.project_id,
            decision_context.round_id,
            decision_context.item_id,
            _command("excluded", boxes=(), note="concurrent"),
            "reservation-race",
        )
    assert getattr(conflict.value, "code", None) == "concurrency_conflict"
    assert getattr(conflict.value, "status_code", None) == 409
    assert getattr(conflict.value, "details", None) == {"retryable": True}
