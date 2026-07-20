"""Add unified annotation work orders and revision audit records."""

from alembic import op
import sqlalchemy as sa


revision = "0004_annotation_work_orders"
down_revision = "0003_model_workbench"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch_op:
        batch_op.add_column(
            sa.Column(
                "annotation_policy_json",
                sa.JSON(),
                server_default='{"mode":"multi_class","allow_empty_labels":true,"class_inference":"manual"}',
                nullable=False,
            )
        )
    op.get_bind().exec_driver_sql(
        "UPDATE projects SET annotation_policy_json="
        "'{\"mode\":\"single_class_locked\",\"allow_empty_labels\":false,\"class_inference\":\"filename_prefix\"}' "
        "WHERE schema_version='steel-defects-v1'"
    )

    with op.batch_alter_table("review_rounds") as batch_op:
        batch_op.add_column(sa.Column("parent_work_order_id", sa.String(length=36), nullable=True))
        batch_op.add_column(
            sa.Column("task_type", sa.String(length=40), server_default="inference_review", nullable=False)
        )
        batch_op.add_column(sa.Column("source_type", sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column("source_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("selection_spec_json", sa.JSON(), server_default="{}", nullable=False))
        batch_op.add_column(sa.Column("manifest_key", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("manifest_sha256", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("revision", sa.Integer(), server_default="0", nullable=False))
        batch_op.add_column(sa.Column("created_by", sa.String(length=100), server_default="local-user", nullable=False))
        batch_op.add_column(sa.Column("archived_at", sa.DateTime(), nullable=True))
        batch_op.create_foreign_key(
            "fk_review_rounds_parent_work_order", "review_rounds", ["parent_work_order_id"], ["id"]
        )
    op.execute(
        "UPDATE review_rounds SET task_type=CASE WHEN kind='audit' THEN 'inference_review' ELSE 'inference_review' END"
    )

    with op.batch_alter_table("annotation_revisions") as batch_op:
        batch_op.add_column(
            sa.Column("created_by", sa.String(length=100), server_default="local-user", nullable=False)
        )

    op.create_table(
        "annotation_revision_checks",
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("repaired_by_revision_id", sa.String(length=36), nullable=True),
        sa.Column("checked_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["revision_id"], ["annotation_revisions.id"]),
        sa.ForeignKeyConstraint(["repaired_by_revision_id"], ["annotation_revisions.id"]),
        sa.PrimaryKeyConstraint("revision_id"),
    )
    op.create_table(
        "annotation_actions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("work_order_id", sa.String(length=36), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=True),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("from_state", sa.String(length=30), nullable=True),
        sa.Column("to_state", sa.String(length=30), nullable=True),
        sa.Column("annotation_revision_id", sa.String(length=36), nullable=True),
        sa.Column("request_id", sa.String(length=100), nullable=True),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["work_order_id"], ["review_rounds.id"]),
        sa.ForeignKeyConstraint(["item_id"], ["review_items.id"]),
        sa.ForeignKeyConstraint(["annotation_revision_id"], ["annotation_revisions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_annotation_actions_work_order", "annotation_actions", ["work_order_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_annotation_actions_work_order", table_name="annotation_actions")
    op.drop_table("annotation_actions")
    op.drop_table("annotation_revision_checks")
    with op.batch_alter_table("annotation_revisions") as batch_op:
        batch_op.drop_column("created_by")
    with op.batch_alter_table("review_rounds") as batch_op:
        batch_op.drop_constraint("fk_review_rounds_parent_work_order", type_="foreignkey")
        for name in (
            "archived_at", "created_by", "revision", "manifest_sha256", "manifest_key",
            "selection_spec_json", "source_id", "source_type", "task_type", "parent_work_order_id",
        ):
            batch_op.drop_column(name)
    with op.batch_alter_table("projects") as batch_op:
        batch_op.drop_column("annotation_policy_json")
