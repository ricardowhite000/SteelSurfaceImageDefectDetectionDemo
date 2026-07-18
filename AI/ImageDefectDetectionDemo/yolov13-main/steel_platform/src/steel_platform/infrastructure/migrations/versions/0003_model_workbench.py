"""Add the human-operated model workbench schema."""

from alembic import op
import sqlalchemy as sa


revision = "0003_model_workbench"
down_revision = "0002_resource_scoping"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.add_column(sa.Column("name", sa.String(length=200), nullable=True))
        batch_op.add_column(
            sa.Column("preset", sa.String(length=40), server_default="legacy", nullable=False)
        )
        batch_op.add_column(
            sa.Column("revision", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.add_column(sa.Column("workspace_key", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("log_key", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("result_manifest_key", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("progress_json", sa.JSON(), server_default="{}", nullable=False)
        )
        batch_op.add_column(sa.Column("started_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("finished_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("heartbeat_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("cancel_requested_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("exit_code", sa.Integer(), nullable=True))
    op.execute("UPDATE jobs SET name = kind || '-' || substr(id, 1, 8), status = CASE WHEN status='planned' THEN 'ready' ELSE status END")
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.alter_column("name", existing_type=sa.String(length=200), nullable=False)

    op.create_table(
        "job_lineage_refs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("direction", sa.String(length=20), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("ref_type", sa.String(length=40), nullable=False),
        sa.Column("ref_id", sa.String(length=36), nullable=False),
        sa.Column("sha256_snapshot", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], name="fk_job_lineage_refs_job_id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id", "direction", "role", "ref_type", "ref_id", name="uq_job_lineage_ref"
        ),
    )
    op.create_index("ix_job_lineage_refs_job_id", "job_lineage_refs", ["job_id"])
    op.create_index("ix_jobs_project_status", "jobs", ["project_id", "status"])

    with op.batch_alter_table("model_versions") as batch_op:
        batch_op.alter_column(
            "experiment_run_id", existing_type=sa.String(length=36), nullable=True
        )
        batch_op.add_column(sa.Column("source_asset_id", sa.String(length=36), nullable=True))
        batch_op.add_column(
            sa.Column("format", sa.String(length=20), server_default="pt", nullable=False)
        )
        batch_op.add_column(
            sa.Column("purpose", sa.String(length=30), server_default="detector", nullable=False)
        )
        batch_op.add_column(
            sa.Column("verification_status", sa.String(length=30), server_default="ready", nullable=False)
        )
        batch_op.add_column(
            sa.Column("evaluation_status", sa.String(length=30), server_default="not_evaluated", nullable=False)
        )
        batch_op.add_column(sa.Column("class_schema_json", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("weights_sha256", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("source_note", sa.Text(), nullable=True))
        batch_op.create_foreign_key(
            "fk_model_versions_source_asset_id", "assets", ["source_asset_id"], ["id"]
        )
    op.execute(
        "UPDATE model_versions SET class_schema_json='[\"Cr\",\"In\",\"Pa\",\"PS\",\"RS\",\"Sc\"]' "
        "WHERE purpose='detector' AND class_schema_json IS NULL"
    )
    op.execute(
        "UPDATE model_versions SET weights_sha256=substr(weights_key, -64) "
        "WHERE weights_sha256 IS NULL AND weights_key LIKE 'sha256/%' AND length(weights_key) >= 64"
    )
    op.create_index("ix_model_versions_project_status", "model_versions", ["project_id", "verification_status"])


def downgrade() -> None:
    op.drop_index("ix_model_versions_project_status", table_name="model_versions")
    op.execute("DELETE FROM model_versions WHERE experiment_run_id IS NULL")
    with op.batch_alter_table("model_versions") as batch_op:
        batch_op.drop_constraint("fk_model_versions_source_asset_id", type_="foreignkey")
        batch_op.drop_column("source_note")
        batch_op.drop_column("weights_sha256")
        batch_op.drop_column("class_schema_json")
        batch_op.drop_column("evaluation_status")
        batch_op.drop_column("verification_status")
        batch_op.drop_column("purpose")
        batch_op.drop_column("format")
        batch_op.drop_column("source_asset_id")
        batch_op.alter_column(
            "experiment_run_id", existing_type=sa.String(length=36), nullable=False
        )

    op.drop_index("ix_jobs_project_status", table_name="jobs")
    op.drop_index("ix_job_lineage_refs_job_id", table_name="job_lineage_refs")
    op.drop_table("job_lineage_refs")
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_column("exit_code")
        batch_op.drop_column("cancel_requested_at")
        batch_op.drop_column("heartbeat_at")
        batch_op.drop_column("finished_at")
        batch_op.drop_column("started_at")
        batch_op.drop_column("progress_json")
        batch_op.drop_column("result_manifest_key")
        batch_op.drop_column("log_key")
        batch_op.drop_column("workspace_key")
        batch_op.drop_column("revision")
        batch_op.drop_column("preset")
        batch_op.drop_column("name")
