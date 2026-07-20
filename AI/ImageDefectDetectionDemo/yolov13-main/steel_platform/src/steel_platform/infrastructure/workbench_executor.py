from __future__ import annotations

from datetime import timedelta
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
from typing import Protocol

from sqlalchemy.orm import Session

from steel_platform.application.errors import ApplicationError, NotFoundError
from steel_platform.infrastructure.config import PlatformSettings
from steel_platform.infrastructure.database import make_engine
from steel_platform.infrastructure.models import (
    DomainEventModel,
    JobModel,
    OutboxEventModel,
    utc_now,
)
from steel_platform.infrastructure.workbench_results import ingest_job_outputs


class TerminalLauncher(Protocol):
    def launch(self, wrapper: Path, *, working_directory: Path) -> None: ...


class RecordingTerminalLauncher:
    """Deterministic launcher used by API and integration tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, Path]] = []

    def launch(self, wrapper: Path, *, working_directory: Path) -> None:
        self.calls.append((wrapper.resolve(), working_directory.resolve()))


class WindowsPowerShellLauncher:
    def launch(self, wrapper: Path, *, working_directory: Path) -> None:
        if os.name != "nt":
            raise ApplicationError(
                "terminal_not_supported", "当前版本只支持在Windows本机打开PowerShell", status_code=501
            )
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoExit",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(wrapper),
            ],
            cwd=str(working_directory),
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )


def _safe_workspace_path(settings: PlatformSettings, storage_key: str) -> Path:
    root = settings.artifact_root.resolve()
    path = (root / Path(storage_key)).resolve()
    if root not in path.parents:
        raise ApplicationError("illegal_workspace_key", "任务工作目录越界", status_code=500)
    return path


def _device_lock(settings: PlatformSettings, device: str) -> Path:
    safe_device = re.sub(r"[^A-Za-z0-9_.-]", "_", device)
    return settings.artifact_root / "workbench" / "locks" / f"device-{safe_device}.lock"


def _line_progress(line: str, *, job_kind: str) -> tuple[int, int] | None:
    if job_kind == "train":
        epoch = re.search(r"(?<!\d)(\d+)\s*/\s*(\d+)\s+\d+(?:\.\d+)?G", line)
        if not epoch:
            return None
        current, total = (int(value) for value in epoch.groups())
        return (current, total) if 0 <= current <= total and total > 0 else None
    matches = re.findall(r"(?<![\d.])(\d+)\s*/\s*(\d+)(?![\d.])", line)
    if not matches:
        return None
    current, total = (int(value) for value in matches[-1])
    if total <= 0 or current < 0 or current > total:
        return None
    return current, total


def _echo_console(line: str) -> None:
    try:
        print(line, end="")
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe_line = line.encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe_line, end="")


def execute_job(settings: PlatformSettings, job_id: str) -> str:
    engine = make_engine(settings.database_url)
    lock_path: Path | None = None
    with Session(engine) as session:
        job = session.get(JobModel, job_id)
        if job is None:
            raise NotFoundError("任务不存在")
        if job.status != "ready":
            raise ApplicationError("job_not_ready", "只有就绪任务可以执行", status_code=409)
        runtime = job.spec_json.get("runtime") or {}
        arguments = runtime.get("arguments")
        if not isinstance(arguments, list) or not arguments or not all(isinstance(item, str) for item in arguments):
            raise ApplicationError("invalid_job_runtime", "任务缺少安全的参数数组", status_code=422)
        cwd = Path(str(runtime.get("cwd", ""))).resolve()
        if not cwd.is_dir():
            raise ApplicationError("invalid_job_runtime", "任务工作目录不存在", status_code=422)
        if not job.workspace_key or not job.log_key:
            raise ApplicationError("invalid_job_runtime", "任务尚未生成工作区或日志路径", status_code=422)
        workspace = _safe_workspace_path(settings, job.workspace_key)
        log_path = _safe_workspace_path(settings, job.log_key)
        workspace.mkdir(parents=True, exist_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        output_dir = Path(str(runtime.get("output_dir", ""))).resolve()
        if workspace != output_dir and workspace not in output_dir.parents:
            raise ApplicationError("illegal_output_path", "任务输出目录越界", status_code=500)
        output_dir.mkdir(parents=True, exist_ok=True)
        job_parameters = dict(job.spec_json.get("parameters", {}))
        device = str(job_parameters.get("device", "cpu"))
        lock_path = _device_lock(settings, device)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise ApplicationError("device_busy", f"设备 {device} 正被其他任务占用", status_code=409) from exc
        with os.fdopen(descriptor, "w", encoding="utf-8") as lock_stream:
            lock_stream.write(job.id)
            lock_stream.flush()
            os.fsync(lock_stream.fileno())
        job.status = "running"
        job_kind = job.kind
        job.started_at = utc_now()
        job.heartbeat_at = job.started_at
        job.finished_at = None
        job.exit_code = None
        job.error_message = None
        job.revision += 1
        session.commit()

    exit_code = -1
    final_status = "failed"
    error_message: str | None = None
    cancelled = False
    timed_out = False
    try:
        environment = os.environ.copy()
        environment.setdefault("PYTHONUTF8", "1")
        with log_path.open("a", encoding="utf-8", newline="") as log_stream:
            process = subprocess.Popen(
                arguments,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                shell=False,
                env=environment,
            )
            assert process.stdout is not None
            lines: queue.Queue[str | None] = queue.Queue()

            def read_output() -> None:
                try:
                    for raw_line in process.stdout:
                        lines.put(raw_line)
                finally:
                    lines.put(None)

            reader = threading.Thread(target=read_output, name=f"job-log-{job_id}", daemon=True)
            reader.start()
            last_heartbeat = utc_now() - timedelta(seconds=3)
            timeout_seconds = job_parameters.get("timeout_seconds")
            deadline = (
                utc_now() + timedelta(seconds=int(timeout_seconds))
                if isinstance(timeout_seconds, int) and timeout_seconds > 0
                else None
            )
            output_closed = False
            pending_progress: tuple[int, int] | None = None
            while process.poll() is None or not output_closed:
                try:
                    line = lines.get(timeout=0.25)
                    if line is None:
                        output_closed = True
                    else:
                        _echo_console(line)
                        log_stream.write(line)
                        log_stream.flush()
                        pending_progress = _line_progress(line, job_kind=job_kind) or pending_progress
                except queue.Empty:
                    line = ""
                now = utc_now()
                if deadline is not None and now >= deadline and process.poll() is None:
                    timed_out = True
                    process.terminate()
                    log_stream.write(f"\n[平台] 任务运行超过 {timeout_seconds} 秒，已请求终止。\n")
                    log_stream.flush()
                    deadline = None
                if now - last_heartbeat >= timedelta(seconds=2):
                    with Session(engine) as heartbeat_session:
                        running = heartbeat_session.get(JobModel, job_id)
                        if running is not None:
                            if running.cancel_requested_at is not None and process.poll() is None:
                                cancelled = True
                                process.terminate()
                            running.heartbeat_at = now
                            if pending_progress:
                                progress = dict(running.progress_json or {})
                                progress["current"], progress["total"] = pending_progress
                                running.progress_json = progress
                                pending_progress = None
                            heartbeat_session.commit()
                    last_heartbeat = now
            reader.join(timeout=1)
            exit_code = process.wait()
        missing = [
            relative
            for relative in runtime.get("expected_outputs", [])
            if not (Path(str(runtime.get("output_dir"))) / relative).is_file()
        ]
        if timed_out:
            final_status = "failed"
            error_message = f"任务超时：超过{timeout_seconds}秒"
        elif cancelled:
            final_status = "cancelled"
            error_message = "用户请求取消"
        elif exit_code != 0:
            final_status = "failed"
            error_message = f"进程退出码：{exit_code}"
        elif missing:
            final_status = "failed"
            error_message = f"任务输出不完整：{', '.join(missing)}"
        else:
            ingest_job_outputs(settings, job_id)
            final_status = "succeeded"
    except KeyboardInterrupt:
        final_status = "cancelled"
        error_message = "用户在终端中断任务"
    except Exception as exc:
        final_status = "failed"
        error_message = str(exc)
    finally:
        with Session(engine) as session:
            job = session.get(JobModel, job_id)
            if job is not None:
                job.status = final_status
                job.exit_code = exit_code
                job.error_message = error_message
                job.finished_at = utc_now()
                job.heartbeat_at = job.finished_at
                progress = dict(job.progress_json or {})
                if final_status == "succeeded":
                    progress["current"] = progress.get("total", 1)
                job.progress_json = progress
                job.revision += 1
                event = DomainEventModel(
                    project_id=job.project_id,
                    event_type="job.status.changed",
                    payload_json={"job_id": job.id, "status": final_status, "error": error_message},
                )
                session.add(event)
                session.flush()
                session.add(OutboxEventModel(domain_event_id=event.id))
                session.commit()
        if lock_path is not None:
            try:
                if lock_path.read_text(encoding="utf-8") == job_id:
                    lock_path.unlink(missing_ok=True)
            except FileNotFoundError:
                pass
    return final_status
