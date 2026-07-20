from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import Boolean, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def new_id() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class StringTupleJSON(TypeDecorator[tuple[str, ...]]):
    """Store an ordered string tuple as JSON without exposing a mutable list."""

    impl = JSON
    cache_ok = True

    def process_bind_param(
        self,
        value: tuple[str, ...] | list[str] | None,
        _dialect: Any,
    ) -> list[str] | None:
        if value is None:
            return None
        return list(value)

    def process_result_value(
        self,
        value: list[str] | tuple[str, ...] | None,
        _dialect: Any,
    ) -> tuple[str, ...] | None:
        if value is None:
            return None
        return tuple(value)


class ProjectModel(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), default="steel-defects-v1")
    class_schema_id: Mapped[str | None] = mapped_column(ForeignKey("class_schemas.id"))
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    annotation_policy_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=lambda: {
            "mode": "multi_class",
            "allow_empty_labels": True,
            "class_inference": "manual",
        },
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class ClassSchemaModel(Base):
    __tablename__ = "class_schemas"
    id: Mapped[str] = mapped_column(String(100), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        default=lambda context: context.get_current_parameters()["kind"],
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    names_json: Mapped[tuple[str, ...]] = mapped_column(StringTupleJSON(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    __table_args__ = (
        UniqueConstraint("project_id", "name", "version", name="uq_class_schema_version"),
    )


class SourceRootModel(Base):
    __tablename__ = "source_roots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        default=lambda context: context.get_current_parameters()["kind"],
    )
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    mode: Mapped[str] = mapped_column(String(30), default="external", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="available", nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    read_only: Mapped[bool] = mapped_column(Boolean, default=True)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    last_verified_at: Mapped[datetime | None] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_source_root_name"),)


class WorkspaceNodeModel(Base):
    __tablename__ = "workspace_nodes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class SourceBindingModel(Base):
    __tablename__ = "source_bindings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_root_id: Mapped[str] = mapped_column(ForeignKey("source_roots.id"), nullable=False)
    node_id: Mapped[str] = mapped_column(ForeignKey("workspace_nodes.id"), nullable=False)
    locator: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="available", nullable=False)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    last_verified_at: Mapped[datetime | None] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    __table_args__ = (
        UniqueConstraint("source_root_id", "node_id", name="uq_source_binding_node"),
    )


class AssetModel(Base):
    __tablename__ = "assets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    source_root_id: Mapped[str | None] = mapped_column(ForeignKey("source_roots.id"))
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    relative_path: Mapped[str | None] = mapped_column(Text)
    storage_key: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    modified_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    __table_args__ = (UniqueConstraint("project_id", "kind", "relative_path", name="uq_asset_path"),)


class AnnotationRevisionModel(Base):
    __tablename__ = "annotation_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    image_asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("annotation_revisions.id"))
    origin: Mapped[str] = mapped_column(String(30), nullable=False)
    decision: Mapped[str | None] = mapped_column(String(30))
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    box_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), default="local-user", nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class InferenceRunModel(Base):
    __tablename__ = "inference_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    model_version_id: Mapped[str | None] = mapped_column(ForeignKey("model_versions.id"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="succeeded")
    manifest_key: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class CandidatePredictionModel(Base):
    __tablename__ = "candidate_predictions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    inference_run_id: Mapped[str] = mapped_column(ForeignKey("inference_runs.id"), nullable=False)
    image_asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), nullable=False)
    annotation_revision_id: Mapped[str | None] = mapped_column(ForeignKey("annotation_revisions.id"))
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    expected_class_id: Mapped[int] = mapped_column(Integer, nullable=False)
    predicted_class_ids: Mapped[str] = mapped_column(Text, default="")
    box_count: Mapped[int] = mapped_column(Integer, default=0)
    min_confidence: Mapped[float | None] = mapped_column(Float)
    max_confidence: Mapped[float | None] = mapped_column(Float)
    source_status: Mapped[str] = mapped_column(String(100), nullable=False)
    diversity_hash: Mapped[int] = mapped_column(Integer, nullable=False)
    comparison_score: Mapped[float] = mapped_column(Float, default=0.0)
    comparison_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("inference_run_id", "image_asset_id", name="uq_prediction_image"),)


class ReviewRoundModel(Base):
    __tablename__ = "review_rounds"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    parent_work_order_id: Mapped[str | None] = mapped_column(ForeignKey("review_rounds.id"))
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(30), default="training")
    name: Mapped[str] = mapped_column(String(200), default="复核任务", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    task_type: Mapped[str] = mapped_column(String(40), default="inference_review", nullable=False)
    source_type: Mapped[str | None] = mapped_column(String(40))
    source_id: Mapped[str | None] = mapped_column(String(36))
    selection_spec_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    manifest_key: Mapped[str | None] = mapped_column(Text)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), default="local-user", nullable=False)
    source_collection_id: Mapped[str | None] = mapped_column(ForeignKey("collections.id"))
    class_schema_id: Mapped[str | None] = mapped_column(ForeignKey("class_schemas.id"))
    target_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="active")
    per_class: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column()
    archived_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    __table_args__ = (UniqueConstraint("project_id", "number", "kind", name="uq_review_round"),)


class CollectionModel(Base):
    __tablename__ = "collections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("collections.id"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    __table_args__ = (
        UniqueConstraint("project_id", "parent_id", "name", name="uq_collection_sibling_name"),
    )


class CollectionMemberModel(Base):
    __tablename__ = "collection_members"
    collection_id: Mapped[str] = mapped_column(ForeignKey("collections.id"), primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class ImportSessionModel(Base):
    __tablename__ = "import_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    data_source_id: Mapped[str] = mapped_column(ForeignKey("source_roots.id"), nullable=False)
    collection_id: Mapped[str] = mapped_column(ForeignKey("collections.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="planned", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)


class ImportEntryModel(Base):
    __tablename__ = "import_entries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    import_session_id: Mapped[str] = mapped_column(ForeignKey("import_sessions.id"), nullable=False)
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    modified_at: Mapped[datetime | None] = mapped_column()
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    expected_sha256: Mapped[str | None] = mapped_column(String(64))
    actual_sha256: Mapped[str | None] = mapped_column(String(64))
    storage_key: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="planned", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)
    __table_args__ = (
        UniqueConstraint("import_session_id", "relative_path", name="uq_import_entry_path"),
    )


class ReviewItemModel(Base):
    __tablename__ = "review_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    round_id: Mapped[str] = mapped_column(ForeignKey("review_rounds.id"), nullable=False)
    image_asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), nullable=False)
    candidate_revision_id: Mapped[str | None] = mapped_column(ForeignKey("annotation_revisions.id"))
    current_revision_id: Mapped[str | None] = mapped_column(ForeignKey("annotation_revisions.id"))
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    expected_class_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_status: Mapped[str] = mapped_column(String(100), nullable=False)
    min_confidence: Mapped[float | None] = mapped_column(Float)
    max_confidence: Mapped[float | None] = mapped_column(Float)
    box_count: Mapped[int] = mapped_column(Integer, default=0)
    selection_reason: Mapped[str] = mapped_column(String(30), nullable=False)
    split_role: Mapped[str] = mapped_column(String(20), nullable=False)
    state: Mapped[str] = mapped_column(String(30), default="pending")
    note: Mapped[str] = mapped_column(Text, default="")
    revision: Mapped[int] = mapped_column(Integer, default=0)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)
    __table_args__ = (UniqueConstraint("round_id", "image_asset_id", name="uq_review_item_image"),)


class ReviewDraftModel(Base):
    __tablename__ = "review_drafts"
    item_id: Mapped[str] = mapped_column(ForeignKey("review_items.id"), primary_key=True)
    boxes_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    note: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)


class AnnotationRevisionCheckModel(Base):
    __tablename__ = "annotation_revision_checks"
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("annotation_revisions.id"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(50))
    message: Mapped[str | None] = mapped_column(Text)
    repaired_by_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("annotation_revisions.id")
    )
    checked_at: Mapped[datetime] = mapped_column(default=utc_now)


class AnnotationActionModel(Base):
    __tablename__ = "annotation_actions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    work_order_id: Mapped[str] = mapped_column(ForeignKey("review_rounds.id"), nullable=False)
    item_id: Mapped[str | None] = mapped_column(ForeignKey("review_items.id"))
    actor: Mapped[str] = mapped_column(String(100), default="local-user", nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    from_state: Mapped[str | None] = mapped_column(String(30))
    to_state: Mapped[str | None] = mapped_column(String(30))
    annotation_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("annotation_revisions.id")
    )
    request_id: Mapped[str | None] = mapped_column(String(100))
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class DatasetVersionModel(Base):
    __tablename__ = "dataset_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("dataset_versions.id"))
    schema_version: Mapped[str] = mapped_column(String(50), default="steel-defects-v1")
    manifest_key: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class DatasetMemberModel(Base):
    __tablename__ = "dataset_members"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dataset_version_id: Mapped[str] = mapped_column(ForeignKey("dataset_versions.id"), nullable=False)
    image_asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), nullable=False)
    annotation_revision_id: Mapped[str] = mapped_column(ForeignKey("annotation_revisions.id"), nullable=False)
    split: Mapped[str] = mapped_column(String(20), nullable=False)
    __table_args__ = (UniqueConstraint("dataset_version_id", "image_asset_id", name="uq_dataset_member"),)


class JobModel(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        default=lambda context: f"{context.get_current_parameters()['kind']}-task",
    )
    preset: Mapped[str] = mapped_column(String(40), default="custom", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="draft", nullable=False)
    spec_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    command_key: Mapped[str | None] = mapped_column(Text)
    workspace_key: Mapped[str | None] = mapped_column(Text)
    log_key: Mapped[str | None] = mapped_column(Text)
    result_manifest_key: Mapped[str | None] = mapped_column(Text)
    progress_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()
    heartbeat_at: Mapped[datetime | None] = mapped_column()
    cancel_requested_at: Mapped[datetime | None] = mapped_column()
    exit_code: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class JobLineageRefModel(Base):
    __tablename__ = "job_lineage_refs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    direction: Mapped[str] = mapped_column(String(20), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    ref_type: Mapped[str] = mapped_column(String(40), nullable=False)
    ref_id: Mapped[str] = mapped_column(String(36), nullable=False)
    sha256_snapshot: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    __table_args__ = (
        UniqueConstraint("job_id", "direction", "role", "ref_type", "ref_id", name="uq_job_lineage_ref"),
    )


class ExperimentRunModel(Base):
    __tablename__ = "experiment_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    dataset_version_id: Mapped[str] = mapped_column(ForeignKey("dataset_versions.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    run_path: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class ModelVersionModel(Base):
    __tablename__ = "model_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    experiment_run_id: Mapped[str | None] = mapped_column(ForeignKey("experiment_runs.id"))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("model_versions.id"))
    source_asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    format: Mapped[str] = mapped_column(String(20), default="pt", nullable=False)
    purpose: Mapped[str] = mapped_column(String(30), default="detector", nullable=False)
    verification_status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)
    evaluation_status: Mapped[str] = mapped_column(String(30), default="not_evaluated", nullable=False)
    class_schema_json: Mapped[list[str] | None] = mapped_column(JSON)
    weights_sha256: Mapped[str | None] = mapped_column(String(64))
    source_note: Mapped[str | None] = mapped_column(Text)
    weights_key: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_key: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class MetricSnapshotModel(Base):
    __tablename__ = "metric_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(40), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(36), nullable=False)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class DomainEventModel(Base):
    __tablename__ = "domain_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class OutboxEventModel(Base):
    __tablename__ = "outbox_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    domain_event_id: Mapped[str] = mapped_column(ForeignKey("domain_events.id"), nullable=False)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class IdempotencyRecordModel(Base):
    __tablename__ = "idempotency_records"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    scope: Mapped[str] = mapped_column(String(100), nullable=False)
    response_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
