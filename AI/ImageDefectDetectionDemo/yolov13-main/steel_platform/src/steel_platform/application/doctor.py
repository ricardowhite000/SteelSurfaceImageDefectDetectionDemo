from __future__ import annotations

import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from steel_platform.application.maintenance import verify_artifacts
from steel_platform.infrastructure.config import PlatformSettings
from steel_platform.infrastructure.database import database_version, make_engine
from steel_platform.infrastructure.models import (
    AnnotationRevisionModel,
    AssetModel,
    DatasetVersionModel,
    ModelVersionModel,
    ProjectModel,
)
from steel_platform.infrastructure.runtime_profiles import RuntimeProfileStore


MIN_FREE_BYTES = 2 * 1024 * 1024 * 1024


def _system_checks() -> dict[str, bool]:
    return {
        "windows": os.name == "nt",
        "powershell": bool(shutil.which("powershell.exe") or shutil.which("pwsh.exe")),
        "conda": bool(shutil.which("conda.exe") or shutil.which("conda")),
        "python": sys.version_info[:2] == (3, 11),
    }


def _port_available(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
            stream.bind((host, port))
        return True
    except OSError:
        return False


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(prefix=".doctor-", dir=path)
        os.close(handle)
        Path(name).unlink(missing_ok=True)
        return True
    except OSError:
        return False


def build_doctor_report(
    settings: PlatformSettings,
    *,
    strict: bool,
    system_checks: dict[str, bool] | None = None,
    runtime_reports: list[dict[str, Any]] | None = None,
    port_available: bool | None = None,
) -> dict[str, Any]:
    current, head = database_version(settings.database_url)
    database_ready = current == head and settings.database_path.is_file()
    artifact_writable = _writable(settings.artifact_root)
    try:
        free_bytes = shutil.disk_usage(settings.workspace_root or settings.artifact_root).free
    except OSError:
        free_bytes = 0
    if runtime_reports is None:
        store = RuntimeProfileStore(settings.artifact_root / "machine" / "runtime-profiles.json")
        runtime_reports = [store.check(profile["id"]) for profile in store.list()]
    demo = {"projects": 0, "images": 0, "annotations": 0, "datasets": 0, "models": 0, "ready": False}
    artifact_report = {"checked": 0, "invalid": 0}
    if database_ready:
        with Session(make_engine(settings.database_url)) as session:
            demo.update(
                {
                    "projects": session.scalar(select(func.count()).select_from(ProjectModel)) or 0,
                    "images": session.scalar(select(func.count()).select_from(AssetModel).where(AssetModel.kind == "image")) or 0,
                    "annotations": session.scalar(select(func.count()).select_from(AnnotationRevisionModel)) or 0,
                    "datasets": session.scalar(select(func.count()).select_from(DatasetVersionModel)) or 0,
                    "models": session.scalar(select(func.count()).select_from(ModelVersionModel)) or 0,
                }
            )
        demo["ready"] = demo["images"] >= 60 and demo["annotations"] >= 60 and demo["datasets"] >= 1 and demo["models"] >= 2
        try:
            artifact_report = verify_artifacts(settings)
        except Exception as exc:
            artifact_report = {"checked": 0, "invalid": 1, "error": str(exc)}
    systems = system_checks or _system_checks()
    port_is_available = _port_available(settings.host, settings.port) if port_available is None else port_available
    checks = {
        "system": all(systems.values()),
        "database": database_ready,
        "artifact_root": settings.artifact_root.is_dir() and artifact_writable,
        "disk_space": free_bytes >= MIN_FREE_BYTES,
        "port": port_is_available,
        "artifact_integrity": artifact_report.get("invalid", 1) == 0,
        "runtime_profiles": any(report.get("available") is True for report in runtime_reports),
        "demo_package": bool(demo["ready"]),
    }
    required = list(checks) if strict else ["database", "artifact_root", "artifact_integrity"]
    failed = [name for name in required if not checks[name]]
    return {
        "ready": not failed,
        "strict": strict,
        "failed_checks": failed,
        "checks": checks,
        "system": systems,
        "database": {"current": current, "head": head, "ready": database_ready, "path": str(settings.database_path)},
        "artifact_root": {"path": str(settings.artifact_root), "writable": artifact_writable, **artifact_report},
        "workspace": {"path": str(settings.workspace_root or settings.artifact_root.parent), "free_bytes": free_bytes},
        "runtime_profiles": runtime_reports,
        "demo_package": demo,
        "listen": {"url": f"http://{settings.host}:{settings.port}", "available": port_is_available},
    }
