from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from io import BytesIO
import math
import os
from pathlib import Path
import tempfile
from typing import BinaryIO, Literal

from PIL import Image, ImageOps, UnidentifiedImageError

from steel_platform.application.errors import ApplicationError, NotFoundError
from steel_platform.domain.ports import AnnotationCodec, ArtifactStore, UnitOfWork
from steel_platform.domain.workspace import ExplorerResource, ResourceItem


ResourceType = Literal["source", "collection", "review_round", "dataset", "model", "inference"]
SortField = Literal["name", "created_at", "size", "status"]
SortOrder = Literal["asc", "desc"]


@dataclass(frozen=True, slots=True)
class ResourceItemView:
    id: str
    asset_id: str | None
    name: str
    item_type: str
    media_type: str
    size_bytes: int
    created_at: datetime | None
    status: str
    is_image: bool


@dataclass(frozen=True, slots=True)
class ResourcePage:
    resource: dict[str, object]
    items: tuple[ResourceItemView, ...]
    pagination: dict[str, int]


@dataclass(frozen=True, slots=True)
class OverlaySetView:
    id: str
    parent_id: str | None
    origin: str
    decision: str | None
    box_count: int
    created_at: datetime | None
    sha256: str
    boxes: tuple[dict[str, int | float], ...]


@dataclass(frozen=True, slots=True)
class AssetDetailView:
    asset_id: str
    name: str
    media_type: str
    size_bytes: int
    sha256: str
    created_at: datetime | None
    source_name: str | None
    width: int
    height: int
    resource_type: str
    resource_id: str
    status: str
    selected_overlay_id: str | None
    overlays: tuple[OverlaySetView, ...]


@dataclass(frozen=True, slots=True)
class ReviewReportView:
    round: dict[str, object]
    summary: dict[str, int | float]
    by_class: dict[str, dict[str, int | float]]
    by_risk: dict[str, int]
    by_selection_reason: dict[str, int]
    problems: tuple[dict[str, object], ...]


class ResourceBrowserService:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        *,
        artifact_store: ArtifactStore,
        annotation_codec: AnnotationCodec,
        asset_opener: Callable[[str, str], BinaryIO],
    ) -> None:
        self._uow_factory = uow_factory
        self._artifacts = artifact_store
        self._codec = annotation_codec
        self._asset_opener = asset_opener

    def list_items(
        self,
        project_id: str,
        resource_type: ResourceType,
        resource_id: str,
        *,
        page: int,
        page_size: int,
        q: str,
        sort: SortField,
        order: SortOrder,
    ) -> ResourcePage:
        with self._uow_factory() as uow:
            resource = uow.resources.get_resource(project_id, resource_type, resource_id)
            if resource is None:
                raise NotFoundError("资源不存在或不属于当前项目")
            items = list(uow.resources.list_items(project_id, resource_type, resource_id))
        query = q.strip().casefold()
        if query:
            items = [item for item in items if query in item.name.casefold()]
        key_functions = {
            "name": lambda item: item.name.casefold(),
            "created_at": lambda item: item.created_at.timestamp() if item.created_at else float("-inf"),
            "size": lambda item: item.size_bytes,
            "status": lambda item: item.status.casefold(),
        }
        # ID is always the stable secondary key, independent of DB row order.
        items.sort(key=lambda item: item.id)
        items.sort(key=key_functions[sort], reverse=order == "desc")
        total = len(items)
        start = (page - 1) * page_size
        visible = items[start : start + page_size]
        return ResourcePage(
            resource=self._resource_dict(resource),
            items=tuple(self._item_view(item) for item in visible),
            pagination={
                "page": page,
                "page_size": page_size,
                "total": total,
                "pages": math.ceil(total / page_size) if total else 0,
            },
        )

    def detail(
        self,
        project_id: str,
        resource_type: ResourceType,
        resource_id: str,
        asset_id: str,
    ) -> AssetDetailView:
        with self._uow_factory() as uow:
            item = uow.resources.get_item(project_id, resource_type, resource_id, asset_id)
            if item is None or item.asset_id is None:
                raise NotFoundError("文件不存在或不属于当前资源")
            asset = uow.assets.get(project_id, asset_id)
            if asset is None:
                raise NotFoundError("图片资产不存在")
            revisions = tuple(uow.resources.list_revisions(project_id, asset_id))
        if not item.media_type.startswith("image/"):
            raise ApplicationError("not_image", "该文件不是可预览图片", status_code=422)
        try:
            with self._asset_opener(project_id, asset_id) as stream:
                with Image.open(stream) as image:
                    width, height = image.size
        except (UnidentifiedImageError, OSError) as exc:
            raise ApplicationError("invalid_image", "图片损坏或无法读取", status_code=422) from exc
        overlays: list[OverlaySetView] = []
        for revision in revisions:
            try:
                with self._artifacts.open(revision.storage_key) as stream:
                    boxes = self._codec.decode(stream.read())
            except (OSError, ValueError, UnicodeError) as exc:
                raise ApplicationError(
                    "invalid_annotation", f"标注版本 {revision.id} 无法解析", status_code=422
                ) from exc
            overlays.append(
                OverlaySetView(
                    id=revision.id,
                    parent_id=revision.parent_id,
                    origin=revision.origin,
                    decision=revision.decision,
                    box_count=revision.box_count,
                    created_at=revision.created_at,
                    sha256=revision.sha256,
                    boxes=tuple(asdict(box) for box in boxes),
                )
            )
        selected = item.context_revision_id
        if selected is None and overlays:
            human = next((overlay for overlay in overlays if overlay.origin == "human"), None)
            selected = (human or overlays[0]).id
        if selected is not None and all(overlay.id != selected for overlay in overlays):
            selected = None
        return AssetDetailView(
            asset_id=asset.id,
            name=item.name,
            media_type=item.media_type,
            size_bytes=item.size_bytes,
            sha256=asset.sha256,
            created_at=item.created_at,
            source_name=item.source_name,
            width=width,
            height=height,
            resource_type=resource_type,
            resource_id=resource_id,
            status=item.status,
            selected_overlay_id=selected,
            overlays=tuple(overlays),
        )

    def review_report(self, project_id: str, round_id: str) -> ReviewReportView:
        with self._uow_factory() as uow:
            review_round = uow.review_tasks.get_round(project_id, round_id)
            if review_round is None:
                raise NotFoundError("复核任务不存在")
            project = uow.projects.get(project_id)
            schema = (
                uow.projects.get_class_schema(project_id, project.class_schema_id)
                if project is not None and project.class_schema_id else None
            )
            rows = tuple(uow.review_tasks.list_items(project_id, round_id))
        class_names = schema.names if schema is not None else ()
        states = ("pending", "accepted", "corrected", "doubtful", "excluded")
        counts = Counter(str(row.state) for row in rows)
        total = len(rows)
        completed = total - counts["pending"]
        summary: dict[str, int | float] = {
            "total": total,
            **{state: counts[state] for state in states},
            "completed": completed,
            "valid_completed": counts["accepted"] + counts["corrected"],
            "completion_rate": round(completed / total * 100, 2) if total else 0.0,
        }
        by_class_rows: dict[str, list[object]] = defaultdict(list)
        for row in rows:
            class_id = int(row.expected_class_id)
            name = class_names[class_id] if 0 <= class_id < len(class_names) else str(class_id)
            by_class_rows[name].append(row)
        by_class: dict[str, dict[str, int | float]] = {}
        for name in class_names or tuple(by_class_rows):
            class_rows = by_class_rows.get(name, [])
            class_counts = Counter(str(row.state) for row in class_rows)
            valid = class_counts["accepted"] + class_counts["corrected"]
            by_class[name] = {
                "total": len(class_rows),
                **{state: class_counts[state] for state in states},
                "valid_completed": valid,
                "effective_rate": round(valid / len(class_rows) * 100, 2) if class_rows else 0.0,
            }
        problems = tuple(
            {
                "item_id": row.id,
                "asset_id": row.image_asset_id,
                "filename": row.filename,
                "class_id": row.expected_class_id,
                "class_name": (
                    class_names[row.expected_class_id]
                    if 0 <= row.expected_class_id < len(class_names) else str(row.expected_class_id)
                ),
                "state": row.state,
                "note": row.note,
                "revision": row.revision,
            }
            for row in sorted(rows, key=lambda item: item.rank)
            if row.state in {"doubtful", "excluded"}
        )
        return ReviewReportView(
            round={
                "id": review_round.id,
                "number": review_round.number,
                "name": review_round.name,
                "kind": review_round.kind,
                "status": review_round.status,
                "target_count": review_round.target_count,
                "created_at": review_round.created_at,
                "completed_at": review_round.completed_at,
            },
            summary=summary,
            by_class=by_class,
            by_risk=dict(sorted(Counter(str(row.source_status) for row in rows).items())),
            by_selection_reason=dict(sorted(Counter(str(row.selection_reason) for row in rows).items())),
            problems=problems,
        )

    @staticmethod
    def _resource_dict(resource: ExplorerResource) -> dict[str, object]:
        return {
            "id": resource.id,
            "type": resource.type,
            "name": resource.name,
            "count": resource.count,
            "status": resource.status,
        }

    @staticmethod
    def _item_view(item: ResourceItem) -> ResourceItemView:
        return ResourceItemView(
            id=item.id,
            asset_id=item.asset_id,
            name=item.name,
            item_type=item.item_type,
            media_type=item.media_type,
            size_bytes=item.size_bytes,
            created_at=item.created_at,
            status=item.status,
            is_image=item.media_type.startswith("image/"),
        )


class ThumbnailService:
    def __init__(
        self,
        cache_root: Path,
        *,
        asset_getter: Callable[[str, str], object],
        asset_opener: Callable[[str, str], BinaryIO],
    ) -> None:
        self._cache_root = cache_root
        self._asset_getter = asset_getter
        self._asset_opener = asset_opener

    def get(self, project_id: str, asset_id: str, size: int) -> tuple[Path, str]:
        asset = self._asset_getter(project_id, asset_id)
        media_type = str(getattr(asset, "media_type", ""))
        if not media_type.startswith("image/"):
            raise ApplicationError("not_image", "该资产不是图片", status_code=422)
        digest = str(getattr(asset, "sha256"))
        target = self._cache_root / digest[:2] / f"{digest}-{size}.jpg"
        etag = f'"{digest}-{size}"'
        if target.is_file():
            return target, etag
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._asset_opener(project_id, asset_id) as stream:
                with Image.open(stream) as source:
                    image = ImageOps.exif_transpose(source)
                    image.thumbnail((size, size), Image.Resampling.LANCZOS)
                    if image.mode != "RGB":
                        background = Image.new("RGB", image.size, "white")
                        if "A" in image.getbands():
                            background.paste(image, mask=image.getchannel("A"))
                        else:
                            background.paste(image)
                        image = background
                    with tempfile.NamedTemporaryFile(
                        mode="w+b", suffix=".tmp", dir=target.parent, delete=False
                    ) as temporary:
                        temp_path = Path(temporary.name)
                        image.save(temporary, format="JPEG", quality=84, optimize=True)
                        temporary.flush()
                        os.fsync(temporary.fileno())
            os.replace(temp_path, target)
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            if "temp_path" in locals():
                Path(temp_path).unlink(missing_ok=True)
            raise ApplicationError("invalid_image", "图片损坏或无法生成缩略图", status_code=422) from exc
        return target, etag
