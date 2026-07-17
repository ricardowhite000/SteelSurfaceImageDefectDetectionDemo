"""Add project resource scoping and deterministic legacy backfill."""

from alembic import op
import sqlalchemy as sa


revision = "0002_resource_scoping"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

STEEL_CLASSES = '["Cr","In","Pa","PS","RS","Sc"]'


def upgrade() -> None:
    op.create_table(
        "class_schemas",
        sa.Column("id", sa.String(length=100), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("names_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_class_schemas_project_id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "name", "version", name="uq_class_schema_version"),
    )
    op.create_index("ix_class_schemas_project_id", "class_schemas", ["project_id"])

    op.create_table(
        "collections",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("parent_id", sa.String(length=36), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["collections.id"], name="fk_collections_parent_id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_collections_project_id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "parent_id", "name", name="uq_collection_sibling_name"),
    )
    op.create_index("ix_collections_project_id", "collections", ["project_id"])
    op.create_index("ix_collections_parent_id", "collections", ["parent_id"])

    op.create_table(
        "collection_members",
        sa.Column("collection_id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["asset_id"], ["assets.id"], name="fk_collection_members_asset_id"
        ),
        sa.ForeignKeyConstraint(
            ["collection_id"], ["collections.id"], name="fk_collection_members_collection_id"
        ),
        sa.PrimaryKeyConstraint("collection_id", "asset_id"),
    )
    op.create_index("ix_collection_members_asset_id", "collection_members", ["asset_id"])

    op.create_table(
        "import_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("data_source_id", sa.String(length=36), nullable=False),
        sa.Column("collection_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="planned", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["collection_id"], ["collections.id"], name="fk_import_sessions_collection_id"
        ),
        sa.ForeignKeyConstraint(
            ["data_source_id"], ["source_roots.id"], name="fk_import_sessions_data_source_id"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_import_sessions_project_id"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_import_sessions_project_id", "import_sessions", ["project_id"])
    op.create_index("ix_import_sessions_status", "import_sessions", ["status"])

    op.create_table(
        "import_entries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("import_session_id", sa.String(length=36), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("modified_at", sa.DateTime(), nullable=True),
        sa.Column("media_type", sa.String(length=100), nullable=False),
        sa.Column("expected_sha256", sa.String(length=64), nullable=True),
        sa.Column("actual_sha256", sa.String(length=64), nullable=True),
        sa.Column("storage_key", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=30), server_default="planned", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["import_session_id"],
            ["import_sessions.id"],
            name="fk_import_entries_import_session_id",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_import_entries_project_id"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("import_session_id", "relative_path", name="uq_import_entry_path"),
    )
    op.create_index("ix_import_entries_project_id", "import_entries", ["project_id"])
    op.create_index("ix_import_entries_status", "import_entries", ["status"])

    with op.batch_alter_table("projects") as batch_op:
        batch_op.add_column(sa.Column("class_schema_id", sa.String(length=100), nullable=True))
        batch_op.add_column(
            sa.Column("revision", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.create_foreign_key(
            "fk_projects_class_schema_id", "class_schemas", ["class_schema_id"], ["id"]
        )
    op.create_index("ix_projects_class_schema_id", "projects", ["class_schema_id"])

    with op.batch_alter_table("source_roots") as batch_op:
        batch_op.add_column(sa.Column("name", sa.String(length=200), nullable=True))
        batch_op.add_column(
            sa.Column("mode", sa.String(length=30), server_default="external", nullable=False)
        )
        batch_op.add_column(
            sa.Column("status", sa.String(length=30), server_default="available", nullable=False)
        )
        batch_op.add_column(sa.Column("manifest_sha256", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("last_verified_at", sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column("revision", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.drop_constraint("uq_source_root_kind", type_="unique")
        batch_op.create_unique_constraint("uq_source_root_name", ["project_id", "name"])

    with op.batch_alter_table("assets") as batch_op:
        batch_op.add_column(sa.Column("modified_at", sa.DateTime(), nullable=True))

    with op.batch_alter_table("review_rounds") as batch_op:
        batch_op.add_column(sa.Column("name", sa.String(length=200), nullable=True))
        batch_op.add_column(
            sa.Column("description", sa.Text(), server_default="", nullable=False)
        )
        batch_op.add_column(sa.Column("source_collection_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("class_schema_id", sa.String(length=100), nullable=True))
        batch_op.add_column(
            sa.Column("target_count", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.add_column(sa.Column("completed_at", sa.DateTime(), nullable=True))
        batch_op.create_foreign_key(
            "fk_review_rounds_source_collection_id",
            "collections",
            ["source_collection_id"],
            ["id"],
        )
        batch_op.create_foreign_key(
            "fk_review_rounds_class_schema_id",
            "class_schemas",
            ["class_schema_id"],
            ["id"],
        )

    op.execute(
        sa.text(
            """
            INSERT INTO class_schemas (id, project_id, name, version, names_json, created_at)
            SELECT 'steel-defects-v1-' || id, id, 'steel-defects-v1', 1, :names, created_at
            FROM projects
            """
        ).bindparams(names=STEEL_CLASSES)
    )
    op.execute(
        "UPDATE projects "
        "SET class_schema_id = 'steel-defects-v1-' || id, revision = 0"
    )
    op.execute(
        "UPDATE source_roots "
        "SET name = kind, mode = 'external', status = 'available', revision = 0"
    )
    op.execute(
        "UPDATE review_rounds "
        "SET name = CASE WHEN kind='audit' THEN '第二轮质量抽查' ELSE '首轮主动学习' END"
    )
    op.execute(
        "UPDATE review_rounds "
        "SET class_schema_id = ("
        "  SELECT class_schema_id FROM projects WHERE projects.id = review_rounds.project_id"
        "), target_count = ("
        "  SELECT count(*) FROM review_items WHERE review_items.round_id = review_rounds.id"
        ")"
    )

    with op.batch_alter_table("source_roots") as batch_op:
        batch_op.alter_column("name", existing_type=sa.String(length=200), nullable=False)
    with op.batch_alter_table("review_rounds") as batch_op:
        batch_op.alter_column("name", existing_type=sa.String(length=200), nullable=False)

    op.create_index("ix_source_roots_project_id", "source_roots", ["project_id"])
    op.create_index("ix_assets_project_id", "assets", ["project_id"])
    op.create_index("ix_review_rounds_project_id", "review_rounds", ["project_id"])
    op.create_index(
        "ix_review_rounds_source_collection_id", "review_rounds", ["source_collection_id"]
    )
    op.create_index("ix_review_rounds_class_schema_id", "review_rounds", ["class_schema_id"])


def downgrade() -> None:
    op.drop_index("ix_review_rounds_class_schema_id", table_name="review_rounds")
    op.drop_index("ix_review_rounds_source_collection_id", table_name="review_rounds")
    op.drop_index("ix_review_rounds_project_id", table_name="review_rounds")
    op.drop_index("ix_assets_project_id", table_name="assets")
    op.drop_index("ix_source_roots_project_id", table_name="source_roots")

    with op.batch_alter_table("review_rounds") as batch_op:
        batch_op.drop_constraint("fk_review_rounds_class_schema_id", type_="foreignkey")
        batch_op.drop_constraint("fk_review_rounds_source_collection_id", type_="foreignkey")
        batch_op.drop_column("completed_at")
        batch_op.drop_column("target_count")
        batch_op.drop_column("class_schema_id")
        batch_op.drop_column("source_collection_id")
        batch_op.drop_column("description")
        batch_op.drop_column("name")

    with op.batch_alter_table("assets") as batch_op:
        batch_op.drop_column("modified_at")

    with op.batch_alter_table("source_roots") as batch_op:
        batch_op.drop_constraint("uq_source_root_name", type_="unique")
        batch_op.drop_column("revision")
        batch_op.drop_column("last_verified_at")
        batch_op.drop_column("manifest_sha256")
        batch_op.drop_column("status")
        batch_op.drop_column("mode")
        batch_op.drop_column("name")
        batch_op.create_unique_constraint("uq_source_root_kind", ["project_id", "kind"])

    op.drop_index("ix_projects_class_schema_id", table_name="projects")
    with op.batch_alter_table("projects") as batch_op:
        batch_op.drop_constraint("fk_projects_class_schema_id", type_="foreignkey")
        batch_op.drop_column("revision")
        batch_op.drop_column("class_schema_id")

    op.drop_index("ix_import_entries_status", table_name="import_entries")
    op.drop_index("ix_import_entries_project_id", table_name="import_entries")
    op.drop_table("import_entries")
    op.drop_index("ix_import_sessions_status", table_name="import_sessions")
    op.drop_index("ix_import_sessions_project_id", table_name="import_sessions")
    op.drop_table("import_sessions")
    op.drop_index("ix_collection_members_asset_id", table_name="collection_members")
    op.drop_table("collection_members")
    op.drop_index("ix_collections_parent_id", table_name="collections")
    op.drop_index("ix_collections_project_id", table_name="collections")
    op.drop_table("collections")
    op.drop_index("ix_class_schemas_project_id", table_name="class_schemas")
    op.drop_table("class_schemas")
