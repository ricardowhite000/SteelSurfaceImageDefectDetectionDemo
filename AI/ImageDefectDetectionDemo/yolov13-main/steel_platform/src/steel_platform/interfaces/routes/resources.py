from __future__ import annotations

from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Query, Request

router = APIRouter(prefix="/api/v1/projects/{project_id}/resources", tags=["resources"])

ResourceType = Literal["source", "collection", "review_round", "dataset", "model", "inference"]
SortField = Literal["name", "created_at", "size", "status"]
SortOrder = Literal["asc", "desc"]


@router.get("/{resource_type}/{resource_id}/items")
def resource_items(
    project_id: str,
    resource_type: ResourceType,
    resource_id: str,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=48, ge=1, le=100),
    q: str = Query(default="", max_length=200),
    sort: SortField = "name",
    order: SortOrder = "asc",
) -> dict[str, object]:
    result = request.app.state.services.resources.list_items(
        project_id,
        resource_type,
        resource_id,
        page=page,
        page_size=page_size,
        q=q,
        sort=sort,
        order=order,
    )
    return {
        "resource": result.resource,
        "items": [asdict(item) for item in result.items],
        "pagination": result.pagination,
    }


@router.get("/{resource_type}/{resource_id}/assets/{asset_id}")
def asset_detail(
    project_id: str,
    resource_type: ResourceType,
    resource_id: str,
    asset_id: str,
    request: Request,
) -> dict[str, object]:
    return asdict(
        request.app.state.services.resources.detail(
            project_id, resource_type, resource_id, asset_id
        )
    )
