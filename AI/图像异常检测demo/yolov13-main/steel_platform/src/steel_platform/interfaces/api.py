from __future__ import annotations

from pathlib import Path
import logging
import time
from typing import Any, Annotated
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from steel_platform.application.errors import ApplicationError, NotFoundError
from steel_platform.application.review import ReviewService
from steel_platform.infrastructure.config import PlatformSettings
from steel_platform.infrastructure.database import make_engine, require_current_database
from steel_platform.infrastructure.models import AssetModel, SourceRootModel


class BoxPayload(BaseModel):
    class_id: int
    x_center: float
    y_center: float
    width: float
    height: float


class DecisionPayload(BaseModel):
    expected_revision: int = Field(ge=0)
    decision: str
    boxes: list[BoxPayload]
    note: str = ""


def _error(request: Request, code: str, message: str, details: Any, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "code": code,
            "message": message,
            "details": details,
            "request_id": getattr(request.state, "request_id", "unknown"),
        },
    )


def create_app(settings: PlatformSettings) -> FastAPI:
    app = FastAPI(title="Steel Vision Platform", version="0.1.0")
    service = ReviewService(settings)
    engine = make_engine(settings.database_url)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-ID") or str(uuid4())
        started=time.perf_counter();response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        logging.getLogger("steel_platform.http").info("request",extra={"request_id":request.state.request_id,"method":request.method,"path":request.url.path,"status_code":response.status_code,"duration_ms":round((time.perf_counter()-started)*1000,2)})
        return response

    @app.exception_handler(ApplicationError)
    async def application_error(request: Request, exc: ApplicationError):
        return _error(request, exc.code, exc.message, exc.details, exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return _error(request, "validation_error", "请求参数校验失败", exc.errors(), 422)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return _error(request, "http_error", str(exc.detail), None, exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        logging.getLogger("steel_platform.http").exception(
            "unhandled_error", extra={"request_id":getattr(request.state,"request_id","unknown")}
        )
        return _error(request, "internal_error", "服务器内部错误", None, 500)

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready() -> dict[str, str]:
        if not settings.artifact_root.is_dir():
            raise ApplicationError("not_ready", "资产目录不存在", status_code=503)
        try:
            require_current_database(settings.database_url)
        except RuntimeError as exc:
            raise ApplicationError("database_upgrade_required",str(exc),status_code=503) from exc
        with Session(engine) as session:
            session.execute(__import__("sqlalchemy").text("SELECT 1"))
        return {"status": "ready"}

    @app.get("/api/v1/overview")
    def overview() -> dict[str, Any]:
        return service.overview()

    @app.get("/api/v1/datasets")
    def datasets() -> list[dict[str, Any]]:
        return service.datasets()

    @app.get("/api/v1/runs")
    def runs() -> list[dict[str, Any]]:
        return service.runs()

    @app.get("/api/v1/models")
    def models() -> list[dict[str, Any]]:
        return service.models()

    @app.get("/api/v1/review/queues")
    def queue(
        state: str | None = None,
        class_id: int | None = None,
        source_status: str | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        return service.list_queue(state=state, class_id=class_id, source_status=source_status, search=search)

    @app.get("/api/v1/review/items/{item_id}")
    def item(item_id: str) -> dict[str, Any]:
        return service.get_item(item_id)

    @app.put("/api/v1/review/items/{item_id}/decision")
    def decide(
        item_id: str,
        payload: DecisionPayload,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> dict[str, Any]:
        return service.decide(
            item_id,
            idempotency_key=idempotency_key,
            expected_revision=payload.expected_revision,
            action=payload.decision,
            boxes_data=[box.model_dump() for box in payload.boxes],
            note=payload.note,
        )

    @app.get("/api/v1/assets/{asset_id}/content")
    def asset_content(asset_id: str):
        with Session(engine) as session:
            asset = session.get(AssetModel, asset_id)
            if asset is None or asset.kind != "image" or asset.source_root_id is None:
                raise NotFoundError("图片资产不存在")
            root = session.get(SourceRootModel, asset.source_root_id)
            if root is None or asset.relative_path is None:
                raise NotFoundError("图片来源不存在")
            base = Path(root.path).resolve()
            source = (base / asset.relative_path).resolve()
            if base not in source.parents or not source.is_file():
                raise NotFoundError("图片源文件不存在或路径非法")
            return FileResponse(source, media_type=asset.media_type)

    static_root = Path(__file__).with_name("static")
    app.mount("/static", StaticFiles(directory=static_root), name="static")

    @app.get("/", include_in_schema=False)
    def browser_shell():
        return FileResponse(static_root / "index.html", media_type="text/html")

    return app
