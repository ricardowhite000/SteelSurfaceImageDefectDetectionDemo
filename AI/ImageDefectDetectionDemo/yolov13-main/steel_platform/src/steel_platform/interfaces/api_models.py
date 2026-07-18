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


class JobPreparePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)


class JobUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    name: str
    parameters: dict[str, object] = Field(default_factory=dict)


class ModelImportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    weights_asset_id: str
    format: str
    purpose: str
    class_names: list[str] | None = None
    source_note: str = ""
