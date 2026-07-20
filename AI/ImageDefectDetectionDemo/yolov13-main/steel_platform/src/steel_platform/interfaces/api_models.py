from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from steel_platform.application.projects import CreateProjectCommand
from steel_platform.application.review_decisions import ReviewDecisionCommand
from steel_platform.application.review_queries import ReviewFilters
from steel_platform.domain.annotations import AnnotationBox
from steel_platform.domain.workspace import ManifestEntry, SourceMode


class BoxPayload(BaseModel):
    class_id: int
    x_center: float
    y_center: float
    width: float
    height: float

    def to_domain(self) -> AnnotationBox:
        return AnnotationBox(**self.model_dump())


class DecisionPayload(BaseModel):
    expected_revision: int = Field(ge=0)
    decision: str
    boxes: list[BoxPayload]
    note: str = ""

    def to_domain(self) -> ReviewDecisionCommand:
        return ReviewDecisionCommand(self.expected_revision, self.decision, tuple(box.to_domain() for box in self.boxes), self.note)


class ReviewFiltersPayload(BaseModel):
    state: str | None = None
    class_id: int | None = None
    source_status: str | None = None
    search: str | None = None

    def to_domain(self) -> ReviewFilters:
        return ReviewFilters(**self.model_dump())


class CreateProjectPayload(BaseModel):
    name: str
    slug: str
    class_schema_name: str
    class_names: list[str]

    def to_domain(self) -> CreateProjectCommand:
        return CreateProjectCommand(self.name, self.slug, self.class_schema_name, tuple(self.class_names))


class ImportStartPayload(BaseModel):
    name: str
    mode: SourceMode
    locator: str | None = None


class ManifestEntryPayload(BaseModel):
    relative_path: str
    size_bytes: int = Field(ge=0)
    media_type: str
    sha256: str

    def to_domain(self) -> ManifestEntry:
        return ManifestEntry(**self.model_dump())


class JobInputRefPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str
    ref_type: str
    ref_id: str


class JobCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    kind: str
    preset: str
    input_refs: list[JobInputRefPayload]
    parameters: dict[str, object] = Field(default_factory=dict)
    runtime_profile_id: str | None = None


class JobPreparePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)


class JobUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    name: str
    parameters: dict[str, object] = Field(default_factory=dict)
    runtime_profile_id: str | None = None


class ModelImportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    weights_asset_id: str
    format: str
    purpose: str
    class_names: list[str] | None = None
    source_note: str = ""


class WorkOrderFiltersPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    class_ids: list[int] = Field(default_factory=list)
    risk_statuses: list[str] = Field(default_factory=list)
    max_min_confidence: float | None = Field(default=None, ge=0, le=1)
    include_no_box: bool = False
    box_count_min: int | None = Field(default=None, ge=0)
    box_count_max: int | None = Field(default=None, ge=0)
    comparison_score_min: float | None = Field(default=None, ge=0)
    total_limit: int | None = Field(default=None, ge=1, le=10000)
    per_class_limit: int | None = Field(default=None, ge=1, le=10000)
    exclude_reviewed: bool = True
    seed: int = 42


class WorkOrderCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    task_type: str
    source_type: str
    source_id: str
    filters: WorkOrderFiltersPayload = Field(default_factory=WorkOrderFiltersPayload)


class WorkOrderFreezePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)


class AmendmentCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    item_ids: list[str] = Field(min_length=1)


class RuntimeProfilePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    python_executable: str = Field(min_length=1)
    project_root: str = Field(min_length=1)
    devices: list[str] = Field(min_length=1)


class SourceBindingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    locator: str = Field(min_length=1)
