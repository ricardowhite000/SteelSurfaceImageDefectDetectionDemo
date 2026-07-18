from __future__ import annotations

import json
import hashlib
from pathlib import Path
import sqlite3
import csv

import pytest
from sqlalchemy import text
from typer.testing import CliRunner

import steel_platform.application.maintenance as maintenance
import steel_platform.interfaces.cli as cli
from steel_platform.application.maintenance import create_backup, snapshot_database_counts
from steel_platform.infrastructure.config import load_settings
from steel_platform.infrastructure.database import make_engine, upgrade_database
from steel_platform.interfaces.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def legacy_config(tmp_path: Path) -> Path:
    """A real 0001 workspace whose rows exercise migration preservation checks."""
    for directory in ("images", "labels", "seed"):
        (tmp_path / directory).mkdir()
    image_bytes = b"legacy-image"
    (tmp_path / "images" / "Cr_1.bmp").write_bytes(image_bytes)
    for filename in ("review.csv", "seed.csv"):
        (tmp_path / filename).write_text("header\n", encoding="utf-8")
    config = tmp_path / "platform.yaml"
    config.write_text(
        "\n".join(
            (
                "project_name: legacy-cli",
                "database_url: sqlite:///workspace/platform.db",
                "artifact_root: workspace/artifacts",
                "source_images: images",
                "candidate_labels: labels",
                "review_csv: review.csv",
                "seed_manifest: seed.csv",
                "seed_dataset: seed",
                "classes: [Cr, In, Pa, PS, RS, Sc]",
            )
        ),
        encoding="utf-8",
    )
    database_url = f"sqlite:///{(tmp_path / 'workspace' / 'platform.db').as_posix()}"
    upgrade_database(database_url, "0001_initial")
    engine = make_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO projects (id, name, schema_version, created_at) "
                    "VALUES ('project-1', 'legacy-cli', 'steel-defects-v1', '2026-01-01 00:00:00')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO source_roots (id, project_id, kind, path, read_only) "
                    "VALUES ('source-1', 'project-1', 'images', :path, 1)"
                ),
                {"path": str(tmp_path / "images")},
            )
            connection.execute(
                text(
                    "INSERT INTO assets (id, project_id, source_root_id, kind, relative_path, storage_key, sha256, size_bytes, media_type, created_at) "
                    "VALUES ('asset-1', 'project-1', 'source-1', 'image', 'Cr_1.bmp', NULL, :sha, :size, 'image/bmp', '2026-01-01 00:00:00')"
                ),
                {"sha": hashlib.sha256(image_bytes).hexdigest(), "size": len(image_bytes)},
            )
            connection.execute(
                text(
                    "INSERT INTO annotation_revisions (id, project_id, image_asset_id, parent_id, origin, decision, storage_key, sha256, box_count, created_at) "
                    "VALUES ('revision-1', 'project-1', 'asset-1', NULL, 'candidate', NULL, 'labels/revision-1.txt', :sha, 1, '2026-01-01 00:00:00')"
                ),
                {"sha": "b" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO review_rounds (id, project_id, number, kind, status, per_class, created_at) "
                    "VALUES ('round-1', 'project-1', 1, 'training', 'completed', 1, '2026-01-02 00:00:00')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO review_items (id, round_id, image_asset_id, candidate_revision_id, current_revision_id, filename, expected_class_id, source_status, min_confidence, max_confidence, box_count, selection_reason, split_role, state, note, revision, rank, updated_at) "
                    "VALUES ('item-1', 'round-1', 'asset-1', 'revision-1', 'revision-1', 'Cr_1.bmp', 0, 'review', 0.1, 0.9, 1, 'active_learning', 'train', 'accepted', '', 1, 1, '2026-01-04 00:00:00')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO projects (id, name, schema_version, created_at) "
                    "VALUES ('project-2', 'legacy-cli-2', 'steel-defects-v1', '2026-01-01 00:00:00')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO source_roots (id, project_id, kind, path, read_only) VALUES "
                    "('source-2', 'project-1', 'labels', :path, 1), "
                    "('source-3', 'project-2', 'images', :path, 1)"
                ),
                {"path": str(tmp_path / "images")},
            )
            connection.execute(
                text(
                    "INSERT INTO assets (id, project_id, source_root_id, kind, relative_path, storage_key, sha256, size_bytes, media_type, created_at) "
                    "VALUES ('asset-2', 'project-2', 'source-3', 'image', 'In_1.bmp', NULL, :sha, :size, 'image/bmp', '2026-01-01 00:00:00')"
                ),
                {"sha": hashlib.sha256(image_bytes).hexdigest(), "size": len(image_bytes)},
            )
            connection.execute(
                text(
                    "INSERT INTO annotation_revisions (id, project_id, image_asset_id, parent_id, origin, decision, storage_key, sha256, box_count, created_at) "
                    "VALUES ('revision-2', 'project-2', 'asset-2', NULL, 'candidate', NULL, 'labels/revision-2.txt', :sha, 1, '2026-01-01 00:00:00')"
                ),
                {"sha": "c" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO review_rounds (id, project_id, number, kind, status, per_class, created_at) VALUES "
                    "('round-2', 'project-1', 2, 'audit', 'active', 1, '2026-01-03 00:00:00'), "
                    "('round-3', 'project-2', 1, 'training', 'active', 1, '2026-01-03 00:00:00')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO review_items (id, round_id, image_asset_id, candidate_revision_id, current_revision_id, filename, expected_class_id, source_status, min_confidence, max_confidence, box_count, selection_reason, split_role, state, note, revision, rank, updated_at) VALUES "
                    "('item-2', 'round-2', 'asset-1', 'revision-1', 'revision-1', 'Cr_1.bmp', 0, 'review', 0.1, 0.9, 1, 'audit', 'validation', 'pending', '', 0, 1, '2026-01-04 00:00:00'), "
                    "('item-3', 'round-3', 'asset-2', 'revision-2', 'revision-2', 'In_1.bmp', 1, 'review', 0.1, 0.9, 1, 'active_learning', 'train', 'excluded', '', 2, 1, '2026-01-04 00:00:00')"
                )
            )
    finally:
        engine.dispose()
    return config


def _snapshot(path: Path) -> dict[str, object]:
    with sqlite3.connect(path) as database:
        tables = ("projects", "source_roots", "assets", "annotation_revisions", "review_rounds", "review_items")
        return {
            "counts": {table: database.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables},
            "identifiers": {table: tuple(row[0] for row in database.execute(f"SELECT id FROM {table} ORDER BY id")) for table in tables},
            "review_states": tuple(database.execute("SELECT round_id, state, count(*) FROM review_items GROUP BY round_id, state ORDER BY round_id, state")),
        }


def test_db_upgrade_backs_up_existing_database_and_preserves_identity_and_states(
    runner: CliRunner, legacy_config: Path
) -> None:
    database = legacy_config.parent / "workspace" / "platform.db"
    before = _snapshot(database)

    result = runner.invoke(app, ["db", "upgrade", "--config", str(legacy_config)])

    assert result.exit_code == 0, result.output
    assert "备份" in result.output
    backups = list((legacy_config.parent / "workspace" / "artifacts" / "backups").glob("*/platform.db"))
    assert len(backups) == 1
    assert _snapshot(backups[0]) == before
    assert _snapshot(database) == before


def test_snapshot_uses_database_path_and_captures_all_legacy_core_records(
    legacy_config: Path,
) -> None:
    database = legacy_config.parent / "workspace" / "platform.db"

    snapshot = snapshot_database_counts(database)

    assert snapshot == _snapshot(database)
    assert snapshot["counts"] == {
        "projects": 2,
        "source_roots": 3,
        "assets": 2,
        "annotation_revisions": 2,
        "review_rounds": 3,
        "review_items": 3,
    }
    assert snapshot["review_states"] == (
        ("round-1", "accepted", 1),
        ("round-2", "pending", 1),
        ("round-3", "excluded", 1),
    )


def test_db_upgrade_reports_backup_path_before_upgrade_failure(
    runner: CliRunner, legacy_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup = (legacy_config.parent / "workspace" / "artifacts" / "backups" / "known-backup").resolve()
    monkeypatch.setattr(maintenance, "create_backup", lambda *_args, **_kwargs: backup)

    def fail_upgrade(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("upgrade exploded")

    monkeypatch.setattr(cli, "upgrade_database", fail_upgrade)
    result = runner.invoke(app, ["db", "upgrade", "--config", str(legacy_config)])

    assert result.exit_code != 0
    assert str(backup) in result.output
    assert "manually restore" in result.output
    assert isinstance(result.exception, RuntimeError)
    assert "upgrade exploded" in str(result.exception)


def test_db_upgrade_reports_backup_path_before_verify_failure(
    runner: CliRunner, legacy_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup = (legacy_config.parent / "workspace" / "artifacts" / "backups" / "known-backup").resolve()
    monkeypatch.setattr(maintenance, "create_backup", lambda *_args, **_kwargs: backup)
    monkeypatch.setattr(cli, "upgrade_database", lambda *_args, **_kwargs: None)

    def fail_verify(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("verification exploded")

    monkeypatch.setattr(maintenance, "verify_upgrade_counts", fail_verify)
    result = runner.invoke(app, ["db", "upgrade", "--config", str(legacy_config)])

    assert result.exit_code != 0
    assert str(backup) in result.output
    assert "manually restore" in result.output
    assert isinstance(result.exception, RuntimeError)
    assert "verification exploded" in str(result.exception)


def test_db_upgrade_of_a_new_database_does_not_create_a_backup(
    runner: CliRunner, legacy_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = load_settings(legacy_config)
    settings.database_path.unlink()
    calls = 0

    def unexpected_backup(*_args: object, **_kwargs: object) -> Path:
        nonlocal calls
        calls += 1
        raise AssertionError("a fresh database must not be backed up")

    monkeypatch.setattr(maintenance, "create_backup", unexpected_backup)
    result = runner.invoke(app, ["db", "upgrade", "--config", str(legacy_config)])

    assert result.exit_code == 0, result.output
    assert calls == 0
    assert not (settings.artifact_root / "backups").exists()


def test_backup_from_a_wal_database_includes_committed_wal_data(legacy_config: Path) -> None:
    settings = load_settings(legacy_config)
    with sqlite3.connect(settings.database_path) as database:
        assert database.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
        database.execute("UPDATE assets SET size_bytes = 123 WHERE id = 'asset-1'")
        database.commit()

    backup = create_backup(settings, verify_artifact_references=False)

    with sqlite3.connect(backup / "platform.db") as database:
        assert database.execute("SELECT size_bytes FROM assets WHERE id = 'asset-1'").fetchone()[0] == 123


def test_backup_failure_does_not_publish_a_half_complete_backup(legacy_config: Path) -> None:
    settings = load_settings(legacy_config)
    settings.database_path.unlink()

    with pytest.raises(FileNotFoundError):
        create_backup(settings, verify_artifact_references=False)

    backups = settings.artifact_root / "backups"
    assert not backups.exists() or not list(backups.glob("*"))


def test_backup_create_defers_artifact_verification_for_a_real_0001_database(
    runner: CliRunner, legacy_config: Path
) -> None:
    result = runner.invoke(app, ["backup", "create", "--config", str(legacy_config)])

    assert result.exit_code == 0, result.output
    assert "deferred_until_schema_upgrade" in result.output
    backups = list((legacy_config.parent / "workspace" / "artifacts" / "backups").glob("*/manifest.json"))
    assert len(backups) == 1
    manifest = json.loads(backups[0].read_text(encoding="utf-8"))
    assert manifest["artifact_verification"]["status"] == "deferred_until_schema_upgrade"


def test_backup_create_on_head_runs_normal_artifact_verification(
    runner: CliRunner, legacy_config: Path
) -> None:
    assert runner.invoke(app, ["db", "upgrade", "--config", str(legacy_config)]).exit_code == 0

    result = runner.invoke(app, ["backup", "create", "--config", str(legacy_config)])

    assert result.exit_code == 0, result.output
    assert "deferred_until_schema_upgrade" not in result.output
    manifests = list((legacy_config.parent / "workspace" / "artifacts" / "backups").glob("*/manifest.json"))
    assert len(manifests) == 2
    manifest = max(manifests, key=lambda path: path.stat().st_mtime_ns)
    verification = json.loads(manifest.read_text(encoding="utf-8"))["artifact_verification"]
    assert verification.get("status") != "deferred_until_schema_upgrade"
    assert {"checked", "invalid", "invalid_keys"} <= verification.keys()


def test_project_check_refuses_outdated_database_with_explicit_upgrade_command(
    runner: CliRunner, legacy_config: Path
) -> None:
    result = runner.invoke(app, ["project", "check", "--config", str(legacy_config)])

    assert result.exit_code == 2
    assert "steel-platform db upgrade" in result.output


def test_serve_refuses_outdated_database_without_starting_uvicorn(
    runner: CliRunner, legacy_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = False

    def should_not_start(*_args: object, **_kwargs: object) -> None:
        nonlocal started
        started = True

    monkeypatch.setattr("uvicorn.run", should_not_start)
    result = runner.invoke(app, ["serve", "--config", str(legacy_config)])

    assert result.exit_code == 2
    assert "steel-platform db upgrade" in result.output
    assert not started


def test_resource_commands_are_json_scoped_and_export_requires_resource_ids(
    runner: CliRunner, legacy_config: Path
) -> None:
    assert runner.invoke(app, ["db", "upgrade", "--config", str(legacy_config)]).exit_code == 0

    projects = runner.invoke(app, ["project", "list", "--json", "--config", str(legacy_config)])
    assert projects.exit_code == 0, projects.output
    assert [project["id"] for project in json.loads(projects.output)] == ["project-1", "project-2"]

    rounds = runner.invoke(
        app,
        ["review", "round-list", "--project", "project-1", "--json", "--config", str(legacy_config)],
    )
    assert rounds.exit_code == 0, rounds.output
    assert json.loads(rounds.output)[0]["id"] == "round-1"

    missing_scope = runner.invoke(app, ["review", "export", "--config", str(legacy_config)])
    assert missing_scope.exit_code == 2
    assert "--project" in missing_scope.output
    export_help = runner.invoke(app, ["review", "export", "--help"])
    assert "--project" in export_help.output and "--round-id" in export_help.output

    source_verify = runner.invoke(
        app,
        ["source", "verify", "--project", "project-1", "--source", "source-1", "--config", str(legacy_config)],
    )
    assert source_verify.exit_code == 0, source_verify.output
    assert json.loads(source_verify.output)["id"] == "source-1"

    import_status = runner.invoke(
        app,
        ["import", "status", "--project", "project-1", "--json", "--config", str(legacy_config)],
    )
    assert import_status.exit_code == 0, import_status.output
    assert json.loads(import_status.output) == []


def test_review_export_progress_requires_project_and_round_id_and_rejects_wrong_project(
    runner: CliRunner, legacy_config: Path, tmp_path: Path
) -> None:
    assert runner.invoke(app, ["db", "upgrade", "--config", str(legacy_config)]).exit_code == 0
    output = tmp_path / "round-2.csv"

    result = runner.invoke(
        app,
        ["review", "export-progress", "--project", "project-1", "--round-id", "round-2", "--output", str(output), "--config", str(legacy_config)],
    )

    assert result.exit_code == 0, result.output
    with output.open(newline="", encoding="utf-8-sig") as stream:
        assert list(csv.DictReader(stream)) == [
            {
                "item_id": "item-2", "filename": "Cr_1.bmp", "class_id": "0", "split": "validation",
                "selection_reason": "audit", "source_status": "review", "state": "pending", "revision": "0", "note": "",
            }
        ]
    wrong_project = runner.invoke(
        app,
        ["review", "export-progress", "--project", "project-2", "--round-id", "round-2", "--output", str(tmp_path / "wrong.csv"), "--config", str(legacy_config)],
    )
    assert wrong_project.exit_code != 0
    old_round = runner.invoke(
        app,
        ["review", "export-progress", "--round", "2", "--config", str(legacy_config)],
    )
    assert old_round.exit_code == 2
    assert "No such option: --round" in old_round.output
    alias_old_round = runner.invoke(
        app,
        ["review", "export", "--round", "2", "--config", str(legacy_config)],
    )
    assert alias_old_round.exit_code == 2
    assert "No such option: --round" in alias_old_round.output


def test_source_rebind_requires_the_selected_project_and_verifies_the_new_root(
    runner: CliRunner, legacy_config: Path
) -> None:
    assert runner.invoke(app, ["db", "upgrade", "--config", str(legacy_config)]).exit_code == 0
    replacement = legacy_config.parent / "replacement-images"
    replacement.mkdir()
    replacement.joinpath("Cr_1.bmp").write_bytes(b"legacy-image")

    rebound = runner.invoke(
        app,
        ["source", "rebind", "--project", "project-1", "--source", "source-1", "--path", str(replacement), "--config", str(legacy_config)],
    )

    assert rebound.exit_code == 0, rebound.output
    assert json.loads(rebound.output)["path"] == replacement.resolve().as_posix()
    with sqlite3.connect(legacy_config.parent / "workspace" / "platform.db") as database:
        database.execute(
            "INSERT INTO projects (id, name, schema_version, revision, created_at) VALUES ('project-3', 'other', 'steel-defects-v1', 0, '2026-01-01 00:00:00')"
        )
    wrong_scope = runner.invoke(
        app,
        ["source", "rebind", "--project", "project-3", "--source", "source-1", "--path", str(replacement), "--config", str(legacy_config)],
    )
    assert wrong_scope.exit_code != 0
