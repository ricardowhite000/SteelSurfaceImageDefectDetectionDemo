"""Add machine nodes and per-machine source bindings."""

from alembic import op
import sqlalchemy as sa


revision = "0005_portable_workspace"
down_revision = "0004_annotation_work_orders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_nodes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("fingerprint", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fingerprint"),
    )
    op.create_table(
        "source_bindings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_root_id", sa.String(length=36), nullable=False),
        sa.Column("node_id", sa.String(length=36), nullable=False),
        sa.Column("locator", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="available", nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(), nullable=True),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["source_root_id"], ["source_roots.id"]),
        sa.ForeignKeyConstraint(["node_id"], ["workspace_nodes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_root_id", "node_id", name="uq_source_binding_node"),
    )
    connection = op.get_bind()
    connection.exec_driver_sql(
        "INSERT INTO workspace_nodes (id,name,fingerprint,created_at) "
        "VALUES ('default-local-node','默认本机','legacy-default',CURRENT_TIMESTAMP)"
    )
    connection.exec_driver_sql(
        "INSERT INTO source_bindings "
        "(id,source_root_id,node_id,locator,status,manifest_sha256,last_verified_at,revision) "
        "SELECT lower(hex(randomblob(16))),id,'default-local-node',path,status,manifest_sha256,last_verified_at,revision "
        "FROM source_roots"
    )


def downgrade() -> None:
    op.drop_table("source_bindings")
    op.drop_table("workspace_nodes")
