from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import hashlib
import logging
import time
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from steel_platform.application.errors import ApplicationError
from steel_platform.application.annotation_work_orders import AnnotationWorkOrderService
from steel_platform.application.portability import SourceBindingService
from steel_platform.application.explorer import ExplorerService
from steel_platform.application.imports import DataSourceImportService
from steel_platform.application.projects import ProjectCatalogService
from steel_platform.application.review_decisions import ReviewDecisionService
from steel_platform.application.review_queries import ReviewTaskQueryService
from steel_platform.application.resource_browser import ResourceBrowserService, ThumbnailService
from steel_platform.application.workbench import WorkbenchService
from steel_platform.infrastructure.artifacts import LocalArtifactStore
from steel_platform.infrastructure.config import PlatformSettings
from steel_platform.infrastructure.database import make_engine, require_current_database
from steel_platform.infrastructure.directory_picker import LocalFolderReader
from steel_platform.infrastructure.runtime_profiles import RuntimeProfileStore
from steel_platform.infrastructure.uow import SqlAlchemyUnitOfWork
from steel_platform.infrastructure.yolo import YoloAnnotationCodec
from steel_platform.infrastructure.workbench import SqlWorkbenchGateway
from steel_platform.infrastructure.workbench_executor import TerminalLauncher
from steel_platform.interfaces.routes import annotation_work_orders, assets, imports, portability, projects, resources, review, workbench


_ERROR_MESSAGES_ZH = {
    "not_found": "请求的资源不存在或不属于当前项目",
    "validation_error": "请求参数无效，请检查后重试",
    "internal_error": "服务器处理请求时发生错误",
    "source_offline": "外部数据源当前不可访问",
    "source_changed": "外部数据源内容已经变化，请先重新校验",
    "artifact_missing": "平台产物缺失",
    "source_manifest_mismatch": "所选文件夹与已登记的数据源清单不一致",
    "import_hash_mismatch": "导入文件与清单校验值不一致",
    "concurrency_conflict": "数据已被其他操作更新，请刷新后重试",
    "database_upgrade_required": "数据库版本需要升级",
    "not_ready": "平台尚未就绪",
    "http_error": "请求地址不存在或当前操作不可用",
    "not_image": "所选文件不是可预览图片",
    "invalid_image": "图片损坏或无法读取",
    "class_mismatch": "候选框类别与预期类别不一致",
    "import_not_verified": "导入内容尚未完成校验",
    "invalid_source_mode": "当前数据源模式不支持此操作",
    "invalid_import_state": "导入任务当前状态不允许此操作",
    "seed_dataset_invalid": "种子数据集未通过完整性检查",
    "dataset_not_ready": "数据集尚未达到发布条件",
    "temporary_asset_exists": "发现上次中断遗留的临时产物",
    "source_missing": "原图来源尚未登记",
    "source_hash_mismatch": "原图校验值不一致或文件已丢失",
    "duplicate_filename": "数据集中存在重复文件名",
    "missing_outputs": "任务输出文件不完整",
    "unknown_prediction_asset": "推理结果引用了未登记图片",
    "audit_quota_shortage": "可用于抽查的样本数量不足",
    "job_already_running": "任务已经处于运行状态",
    "invalid_job_spec": "任务配置无效，请检查输入、预设和参数范围",
    "job_not_editable": "当前任务状态不允许修改或重新准备",
    "job_not_ready": "任务尚未准备完成",
    "job_still_running": "任务仍在运行，不能导入已有结果",
    "job_not_cancellable": "当前任务状态不能取消",
    "device_busy": "所选GPU设备正被另一项任务使用",
    "model_not_ready": "模型尚未通过可加载性校验",
    "model_purpose_mismatch": "基础迁移权重不能直接用于推理",
    "model_schema_mismatch": "模型类别数量或顺序与当前项目不一致",
    "model_hash_missing": "模型缺少可验证的SHA256",
    "invalid_model_file": "模型文件无效，只允许.pt或.onnx",
    "invalid_inference_file": "推理文件无效，只允许常见图片或视频",
    "origin_not_allowed": "终端只能从当前本机平台页面打开",
    "artifact_hash_mismatch": "资产内容与登记哈希不一致",
    "dataset_materialization_missing": "数据集尚未物化为可训练目录",
    "empty_inference_source": "所选数据源没有已登记图片",
}


def _error(request: Request, code: str, message: str, details: Any, status_code: int, headers: dict[str, str] | None = None) -> JSONResponse:
    localized = _ERROR_MESSAGES_ZH.get(code, message)
    return JSONResponse(status_code=status_code, headers=headers, content={"code": code, "message": localized, "details": details, "request_id": getattr(request.state, "request_id", "unknown")})


def _static_version(static_root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(candidate for candidate in static_root.rglob("*") if candidate.is_file()):
        digest.update(path.relative_to(static_root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def create_app(
    settings: PlatformSettings, *, terminal_launcher: TerminalLauncher | None = None
) -> FastAPI:
    app = FastAPI(title="钢材视觉平台", version="0.1.0")
    static_root = Path(__file__).with_name("static")
    static_version = _static_version(static_root)
    engine = make_engine(settings.database_url)
    session_factory = sessionmaker(bind=engine)
    uow_factory = lambda: SqlAlchemyUnitOfWork(session_factory)
    store = LocalArtifactStore(settings.artifact_root)
    codec = YoloAnnotationCodec()
    runtime_profiles = RuntimeProfileStore(
        settings.artifact_root / "machine" / "runtime-profiles.json"
    )
    import_service = DataSourceImportService(uow_factory, store, LocalFolderReader())
    app.state.services = SimpleNamespace(
        projects=ProjectCatalogService(uow_factory), explorer=ExplorerService(uow_factory),
        imports=import_service,
        resources=ResourceBrowserService(
            uow_factory, artifact_store=store, annotation_codec=codec,
            asset_opener=import_service.open_asset,
        ),
        thumbnails=ThumbnailService(
            settings.artifact_root / "cache" / "thumbnails",
            asset_getter=import_service.get_asset,
            asset_opener=import_service.open_asset,
        ),
        review_queries=ReviewTaskQueryService(uow_factory, class_names=settings.classes, artifact_store=store, annotation_codec=codec),
        review_decisions=ReviewDecisionService(uow_factory, artifact_store=store, annotation_codec=codec, class_names=settings.classes),
        annotation_work_orders=AnnotationWorkOrderService(session_factory, store),
        runtime_profiles=runtime_profiles,
        source_bindings=SourceBindingService(session_factory, import_service),
        workbench=WorkbenchService(
            SqlWorkbenchGateway(
                settings,
                store,
                terminal_launcher,
                runtime_profiles=runtime_profiles,
            ),
            allowed_devices=(settings.device,),
        ),
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-ID") or str(uuid4())
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        logging.getLogger("steel_platform.http").info("request", extra={"request_id": request.state.request_id, "method": request.method, "path": request.url.path, "status_code": response.status_code, "duration_ms": round((time.perf_counter() - started) * 1000, 2)})
        return response

    @app.exception_handler(ApplicationError)
    async def application_error(request: Request, exc: ApplicationError):
        return _error(request, exc.code, exc.message, exc.details, exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return _error(request, "validation_error", "请求参数校验失败", exc.errors(), 422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        return _error(request, "http_error", str(exc.detail), {"detail": exc.detail}, exc.status_code, dict(exc.headers) if exc.headers else None)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        logging.getLogger("steel_platform.http").exception("unhandled_error", extra={"request_id": getattr(request.state, "request_id", "unknown")})
        return _error(request, "internal_error", "服务器内部错误", None, 500)

    @app.get("/health/live")
    def live() -> dict[str, str]: return {"status": "ok"}

    @app.get("/health/ready")
    def ready() -> dict[str, str]:
        if not settings.artifact_root.is_dir(): raise ApplicationError("not_ready", "Artifact directory does not exist", status_code=503)
        try: require_current_database(settings.database_url)
        except RuntimeError as exc: raise ApplicationError("database_upgrade_required", str(exc), status_code=503) from exc
        with session_factory() as session: session.execute(text("SELECT 1"))
        return {"status": "ready"}

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        return Response(status_code=204)

    app.include_router(projects.router)
    app.include_router(imports.router)
    app.include_router(review.router)
    app.include_router(review.legacy_router)
    app.include_router(annotation_work_orders.router)
    app.include_router(assets.router)
    app.include_router(resources.router)
    app.include_router(workbench.router)
    app.include_router(portability.router)

    @app.get("/static/js/{module_name:path}", include_in_schema=False)
    def javascript_module(module_name: str) -> Response:
        """Serve one coherent, content-versioned ES module graph.

        A version on main.js alone is insufficient because browsers cache its
        relative imports independently.  Replacing the marker in every module
        prevents a new entry module from executing against stale dependencies.
        """
        javascript_root = (static_root / "js").resolve()
        module_path = (javascript_root / module_name).resolve()
        if (
            module_path.suffix.lower() != ".js"
            or not module_path.is_relative_to(javascript_root)
            or not module_path.is_file()
        ):
            raise StarletteHTTPException(status_code=404, detail="JavaScript module not found")
        source = module_path.read_text(encoding="utf-8").replace("__STATIC_VERSION__", static_version)
        return Response(source, media_type="text/javascript", headers={"Cache-Control": "no-store"})

    app.mount("/static", StaticFiles(directory=static_root), name="static")

    @app.get("/", include_in_schema=False)
    def browser_shell() -> HTMLResponse:
        html = (static_root / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html.replace("__STATIC_VERSION__", static_version), headers={"Cache-Control": "no-store"})
    return app
