from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request

from steel_platform.application.errors import ApplicationError
from steel_platform.interfaces.api_models import DecisionPayload, ReviewFiltersPayload

router = APIRouter(prefix="/api/v1/projects/{project_id}/review-rounds", tags=["review"])
legacy_router = APIRouter(tags=["review"])


@router.get("")
def list_rounds(project_id: str, request: Request) -> list[dict[str, object]]:
    return [asdict(row) for row in request.app.state.services.review_queries.list_rounds(project_id)]


@router.get("/{round_id}")
def get_round(project_id: str, round_id: str, request: Request) -> dict[str, object]:
    return asdict(request.app.state.services.review_queries.get_round(project_id, round_id))


@router.get("/{round_id}/report")
def get_report(project_id: str, round_id: str, request: Request) -> dict[str, object]:
    return asdict(request.app.state.services.resources.review_report(project_id, round_id))


@router.get("/{round_id}/items")
def list_items(project_id: str, round_id: str, request: Request, filters: Annotated[ReviewFiltersPayload, Query()]) -> dict[str, object]:
    page = request.app.state.services.review_queries.list_items(project_id, round_id, filters.to_domain())
    return {"items": [asdict(item) for item in page.items], "total": page.total}


@router.get("/{round_id}/items/{item_id}")
def get_item(project_id: str, round_id: str, item_id: str, request: Request) -> dict[str, object]:
    return asdict(request.app.state.services.review_queries.get_item(project_id, round_id, item_id))


@router.put("/{round_id}/items/{item_id}/decision")
def decide(project_id: str, round_id: str, item_id: str, payload: DecisionPayload, request: Request, idempotency_key: Annotated[str, Header(alias="Idempotency-Key")]) -> dict[str, object]:
    return request.app.state.services.review_decisions.decide(project_id, round_id, item_id, payload.to_domain(), idempotency_key).as_response()


@legacy_router.get("/api/v1/review/queues")
def legacy_queue() -> None:
    raise ApplicationError("scope_required", "Select a project and review round before listing items", status_code=410)
