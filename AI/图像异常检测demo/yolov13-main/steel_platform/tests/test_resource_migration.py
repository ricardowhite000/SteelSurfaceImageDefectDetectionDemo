from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import shutil

from alembic import command
import pytest
import sqlalchemy as sa
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from steel_platform.infrastructure.database import (
    _alembic_config,
    database_version,
    make_engine,
    upgrade_database,
)
from steel_platform.infrastructure.models import ClassSchemaModel, ReviewRoundModel, SourceRootModel


@dataclass(frozen=True)
class LegacyCounts:
    assets: int
    asset_ids: tuple[str, ...]
    annotation_revisions: int
    annotation_revision_ids: tuple[str, ...]
    review_items: int
    review_states: Counter[str]
    review_item_states: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class LegacyDatabase:
    url: str
    path: Path

    def snapshot_counts(self) -> LegacyCounts:
        engine = make_engine(self.url)
        try:
            with engine.connect() as connection:
                return LegacyCounts(
                    assets=connection.scalar(sa.text("SELECT count(*) FROM assets")),
                    asset_ids=tuple(
                        connection.scalars(sa.text("SELECT id FROM assets ORDER BY id"))
                    ),
                    annotation_revisions=connection.scalar(
                        sa.text("SELECT count(*) FROM annotation_revisions")
                    ),
                    annotation_revision_ids=tuple(
                        connection.scalars(
                            sa.text("SELECT id FROM annotation_revisions ORDER BY id")
                        )
                    ),
                    review_items=connection.scalar(sa.text("SELECT count(*) FROM review_items")),
                    review_states=Counter(
                        {
                            state: count
                            for state, count in connection.execute(
                                sa.text("SELECT state, count(*) FROM review_items GROUP BY state")
                            )
                        }
                    ),
                    review_item_states=tuple(
                        connection.execute(
                            sa.text("SELECT id, state FROM review_items ORDER BY id")
                        )
                    ),
                )
        finally:
            engine.dispose()


def _insert_legacy_rows(database_url: str) -> None:
    engine = make_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO projects (id, name, schema_version, created_at) "
                    "VALUES ('project-1', 'legacy', 'steel-defects-v1', '2026-01-01 00:00:00')"
                )
            )
            connection.execute(
                sa.text(
                    "INSERT INTO source_roots (id, project_id, kind, path, read_only) "
                    "VALUES ('source-1', 'project-1', 'images', 'G:/legacy/images', 1)"
                )
            )
            connection.execute(
                sa.text(
                    "INSERT INTO assets "
                    "(id, project_id, source_root_id, kind, relative_path, storage_key, sha256, "
                    " size_bytes, media_type, created_at) VALUES "
                    "('asset-1', 'project-1', 'source-1', 'image', 'Cr_1.bmp', NULL, :sha, "
                    " 100, 'image/bmp', '2026-01-01 00:00:00')"
                ),
                {"sha": "a" * 64},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO annotation_revisions "
                    "(id, project_id, image_asset_id, parent_id, origin, decision, storage_key, "
                    " sha256, box_count, created_at) VALUES "
                    "('revision-1', 'project-1', 'asset-1', NULL, 'candidate', NULL, "
                    " 'labels/revision-1.txt', :sha, 1, '2026-01-01 00:00:00')"
                ),
                {"sha": "b" * 64},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO review_rounds "
                    "(id, project_id, number, kind, status, per_class, created_at) VALUES "
                    "('round-1', 'project-1', 1, 'training', 'completed', 1, "
                    " '2026-01-02 00:00:00'), "
                    "('round-2', 'project-1', 2, 'audit', 'active', 1, "
                    " '2026-01-03 00:00:00')"
                )
            )
            connection.execute(
                sa.text(
                    "INSERT INTO review_items "
                    "(id, round_id, image_asset_id, candidate_revision_id, current_revision_id, "
                    " filename, expected_class_id, source_status, min_confidence, max_confidence, "
                    " box_count, selection_reason, split_role, state, note, revision, rank, updated_at) "
                    "VALUES "
                    "('item-1', 'round-1', 'asset-1', 'revision-1', 'revision-1', 'Cr_1.bmp', "
                    " 0, 'review', 0.1, 0.9, 1, 'active_learning', 'train', 'accepted', '', 1, 1, "
                    " '2026-01-04 00:00:00'), "
                    "('item-2', 'round-2', 'asset-1', 'revision-1', 'revision-1', 'Cr_1.bmp', "
                    " 0, 'review', 0.1, 0.9, 1, 'audit', 'validation', 'pending', '', 0, 1, "
                    " '2026-01-05 00:00:00')"
                )
            )
    finally:
        engine.dispose()


@pytest.fixture
def legacy_database(tmp_path: Path) -> LegacyDatabase:
    database_path = tmp_path / "legacy-0001.db"
    database = LegacyDatabase(url=f"sqlite:///{database_path.as_posix()}", path=database_path)
    upgrade_database(database.url, "0001_initial")
    assert "class_schemas" not in inspect(make_engine(database.url)).get_table_names()
    _insert_legacy_rows(database.url)
    return database


def test_fresh_upgrade_creates_explicit_resource_schema(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}"

    upgrade_database(database_url)

    inspector = inspect(make_engine(database_url))
    assert {
        "class_schemas",
        "collections",
        "collection_members",
        "import_sessions",
        "import_entries",
    } <= set(inspector.get_table_names())
    assert {"class_schema_id", "revision"} <= {
        column["name"] for column in inspector.get_columns("projects")
    }
    assert {"name", "mode", "status", "manifest_sha256", "last_verified_at", "revision"} <= {
        column["name"] for column in inspector.get_columns("source_roots")
    }
    assert {"modified_at"} <= {column["name"] for column in inspector.get_columns("assets")}
    assert {
        "name",
        "description",
        "source_collection_id",
        "class_schema_id",
        "target_count",
        "completed_at",
    } <= {column["name"] for column in inspector.get_columns("review_rounds")}
    assert database_version(database_url) == ("0002_resource_scoping", "0002_resource_scoping")


def test_0002_backfills_legacy_project_without_changing_review_items(
    legacy_database: LegacyDatabase,
) -> None:
    before = legacy_database.snapshot_counts()

    upgrade_database(legacy_database.url, "head")

    after = legacy_database.snapshot_counts()
    assert after.assets == before.assets
    assert after.asset_ids == before.asset_ids
    assert after.annotation_revisions == before.annotation_revisions
    assert after.annotation_revision_ids == before.annotation_revision_ids
    assert after.review_items == before.review_items
    assert after.review_states == before.review_states
    assert after.review_item_states == before.review_item_states
    with Session(make_engine(legacy_database.url)) as session:
        schema = session.scalar(select(ClassSchemaModel))
        rounds = session.scalars(select(ReviewRoundModel).order_by(ReviewRoundModel.number)).all()
        assert schema is not None
        assert schema.names_json == ("Cr", "In", "Pa", "PS", "RS", "Sc")
        assert [row.name for row in rounds] == ["首轮主动学习", "第二轮质量抽查"]
        assert [row.target_count for row in rounds] == [1, 1]
        assert all(row.class_schema_id == schema.id for row in rounds)


def test_class_schema_names_reload_as_an_immutable_tuple(
    legacy_database: LegacyDatabase,
) -> None:
    upgrade_database(legacy_database.url)

    with Session(make_engine(legacy_database.url)) as session:
        schema = session.scalar(select(ClassSchemaModel))
        assert schema is not None
        assert schema.names_json == ("Cr", "In", "Pa", "PS", "RS", "Sc")
        with pytest.raises(AttributeError):
            schema.names_json.append("Other")  # type: ignore[attr-defined]


def test_source_roots_allow_same_kind_with_different_names(legacy_database: LegacyDatabase) -> None:
    upgrade_database(legacy_database.url)

    with Session(make_engine(legacy_database.url)) as session:
        session.add(
            SourceRootModel(
                project_id="project-1",
                name="images-copy",
                kind="images",
                mode="external",
                status="available",
                path="G:/legacy/images-copy",
            )
        )
        session.commit()

        sources = session.scalars(
            select(SourceRootModel).where(SourceRootModel.project_id == "project-1")
        ).all()

    assert {(source.kind, source.name) for source in sources} == {
        ("images", "images"),
        ("images", "images-copy"),
    }


def test_downgrade_is_rehearsed_on_a_copy_only(legacy_database: LegacyDatabase, tmp_path: Path) -> None:
    upgrade_database(legacy_database.url)
    copied_path = tmp_path / "downgrade-copy.db"
    shutil.copy2(legacy_database.path, copied_path)
    copied_url = f"sqlite:///{copied_path.as_posix()}"

    command.downgrade(_alembic_config(copied_url), "0001_initial")

    copied_tables = set(inspect(make_engine(copied_url)).get_table_names())
    assert "class_schemas" not in copied_tables
    assert database_version(copied_url)[0] == "0001_initial"
    assert database_version(legacy_database.url)[0] == "0002_resource_scoping"
    assert legacy_database.snapshot_counts() == LegacyCounts(
        assets=1,
        asset_ids=("asset-1",),
        annotation_revisions=1,
        annotation_revision_ids=("revision-1",),
        review_items=2,
        review_states=Counter({"accepted": 1, "pending": 1}),
        review_item_states=(("item-1", "accepted"), ("item-2", "pending")),
    )
