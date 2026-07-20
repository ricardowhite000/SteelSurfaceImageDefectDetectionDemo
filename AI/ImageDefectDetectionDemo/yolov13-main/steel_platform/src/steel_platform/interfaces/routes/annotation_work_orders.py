from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Request, status

from steel_platform.interfaces.api_models import (
    AmendmentCreatePayload,
    WorkOrderCreatePayload,
    WorkOrderFreezePayload,
)


router = APIRouter(
    prefix="/api/v1/projects/{project_id}/annotation-work-orders",
    tags=["annotation-work-orders"],
)


@router.get("")
def list_work_orders(project_id: str, request: Request) -> list[dict[str, object]]:
    return request.app.state.services.annotation_work_orders.list(project_id)


@router.get("/options")
def work_order_options(project_id: str, request: Request) -> dict[str, object]:
    return request.app.state.services.annotation_work_orders.options(project_id)


@router.post("/preview")
def preview_work_order(
    project_id: str, payload: WorkOrderCreatePayload, request: Request
) -> dict[str, object]:
    preview = request.app.state.services.annotation_work_orders.preview(
        project_id, payload.model_dump()
    )
    return {
        "matched": preview.matched,
        "selected": preview.selected,
        "by_class": preview.by_class,
        "by_risk": preview.by_risk,
        "sample_asset_ids": list(preview.sample_asset_ids),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_work_order(
    project_id: str,
    payload: WorkOrderCreatePayload,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    return request.app.state.services.annotation_work_orders.create(
        project_id, payload.model_dump(), idempotency_key=idempotency_key
    )


@router.get("/{work_order_id}")
def get_work_order(
    project_id: str, work_order_id: str, request: Request
) -> dict[str, object]:
    return request.app.state.services.annotation_work_orders.get(project_id, work_order_id)


@router.post("/{work_order_id}/freeze")
def freeze_work_order(
    project_id: str,
    work_order_id: str,
    payload: WorkOrderFreezePayload,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    return request.app.state.services.annotation_work_orders.freeze(
        project_id,
        work_order_id,
        expected_revision=payload.expected_revision,
        idempotency_key=idempotency_key,
    )


@router.post("/{work_order_id}/amendments", status_code=status.HTTP_201_CREATED)
def create_amendment(
    project_id: str,
    work_order_id: str,
    payload: AmendmentCreatePayload,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict[str, object]:
    return request.app.state.services.annotation_work_orders.create_amendment(
        project_id,
        work_order_id,
        name=payload.name,
        item_ids=payload.item_ids,
        idempotency_key=idempotency_key,
    )


@router.get("/{work_order_id}/items")
def list_work_order_items(
    project_id: str, work_order_id: str, request: Request
) -> dict[str, object]:
    page = request.app.state.services.review_queries.list_items(project_id, work_order_id)
    from dataclasses import asdict

    return {"items": [asdict(item) for item in page.items], "total": page.total}


@router.get("/{work_order_id}/history")
def work_order_history(
    project_id: str, work_order_id: str, request: Request
) -> list[dict[str, object]]:
    return request.app.state.services.annotation_work_orders.history(project_id, work_order_id)


@router.get("/{work_order_id}/report")
def work_order_report(
    project_id: str, work_order_id: str, request: Request
) -> dict[str, object]:
    from dataclasses import asdict

    return asdict(request.app.state.services.resources.review_report(project_id, work_order_id))
