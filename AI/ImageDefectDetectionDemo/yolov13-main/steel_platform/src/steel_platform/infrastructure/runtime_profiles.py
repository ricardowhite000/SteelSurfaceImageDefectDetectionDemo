from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
from uuid import uuid4

from steel_platform.application.errors import ApplicationError, NotFoundError


class RuntimeProfileStore:
    """Machine-local registry for replaceable YOLO execution environments."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def list(self) -> list[dict[str, object]]:
        if not self.path.is_file():
            return []
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") not in {1, 2}:
            raise ApplicationError("runtime_registry_invalid", "运行环境注册表格式无效", status_code=500)
        profiles = list(payload.get("profiles") or [])
        if payload.get("schema_version") == 1:
            profiles = [self._normalize_profile(item) for item in profiles]
            self._write(profiles)
        return profiles

    def add(
        self,
        *,
        name: str,
        python_executable: str,
        project_root: str,
        devices: list[str],
        backend: str | None = None,
    ) -> dict[str, object]:
        if not name.strip() or not python_executable.strip() or not project_root.strip():
            raise ApplicationError("validation_error", "名称、Python解释器和项目目录不能为空", status_code=422)
        if not devices or any(not str(device).strip() for device in devices):
            raise ApplicationError("validation_error", "至少配置一个运行设备", status_code=422)
        profiles = self.list()
        if any(item["name"].casefold() == name.strip().casefold() for item in profiles):
            raise ApplicationError("runtime_name_exists", "运行环境名称已存在", status_code=409)
        normalized_backend = self._validate_backend(backend or self._infer_backend(devices))
        profile: dict[str, object] = {
            "id": str(uuid4()),
            "name": name.strip(),
            "python_executable": str(Path(python_executable).expanduser()),
            "project_root": str(Path(project_root).expanduser()),
            "devices": [str(device).strip() for device in devices],
            "backend": normalized_backend,
            "capabilities": {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        profiles.append(profile)
        self._write(profiles)
        return profile

    def upsert(
        self,
        *,
        name: str,
        python_executable: str,
        project_root: str,
        devices: list[str],
        backend: str,
    ) -> dict[str, object]:
        if not name.strip() or not python_executable.strip() or not project_root.strip():
            raise ApplicationError("validation_error", "名称、Python解释器和项目目录不能为空", status_code=422)
        if not devices or any(not str(device).strip() for device in devices):
            raise ApplicationError("validation_error", "至少配置一个运行设备", status_code=422)
        normalized_backend = self._validate_backend(backend)
        profiles = self.list()
        existing = next((item for item in profiles if str(item["name"]).casefold() == name.strip().casefold()), None)
        now = datetime.now(timezone.utc).isoformat()
        if existing is None:
            profile: dict[str, object] = {
                "id": str(uuid4()),
                "name": name.strip(),
                "python_executable": str(Path(python_executable).expanduser()),
                "project_root": str(Path(project_root).expanduser()),
                "devices": [str(device).strip() for device in devices],
                "backend": normalized_backend,
                "capabilities": {},
                "created_at": now,
                "updated_at": now,
            }
            profiles.append(profile)
        else:
            existing.update(
                {
                    "name": name.strip(),
                    "python_executable": str(Path(python_executable).expanduser()),
                    "project_root": str(Path(project_root).expanduser()),
                    "devices": [str(device).strip() for device in devices],
                    "backend": normalized_backend,
                    "updated_at": now,
                }
            )
            existing.setdefault("capabilities", {})
            profile = existing
        self._write(profiles)
        return profile

    def get(self, profile_id: str) -> dict[str, object]:
        profile = next((item for item in self.list() if item["id"] == profile_id), None)
        if profile is None:
            raise NotFoundError("运行环境不存在")
        return profile

    def check(self, profile_id: str) -> dict[str, object]:
        profile = self.get(profile_id)
        executable = Path(str(profile["python_executable"]))
        project_root = Path(str(profile["project_root"]))
        checks = {"python": executable.is_file(), "project_root": project_root.is_dir(), "torch": False, "cuda": False, "yolo": False, "platform": False}
        details = "Python解释器不存在"
        if checks["python"]:
            command = [
                str(executable), "-c",
                "import json; import torch; import ultralytics; import steel_platform; print(json.dumps({'torch':torch.__version__,'cuda':torch.cuda.is_available(),'ultralytics':ultralytics.__version__,'platform':getattr(steel_platform,'__version__','installed')}))",
            ]
            try:
                completed = subprocess.run(command, cwd=project_root if project_root.is_dir() else None, shell=False, capture_output=True, text=True, timeout=30, check=False)
                details = (completed.stdout or completed.stderr).strip()
                if completed.returncode == 0:
                    parsed = json.loads(completed.stdout.strip().splitlines()[-1])
                    checks["torch"] = bool(parsed.get("torch"))
                    checks["cuda"] = bool(parsed.get("cuda"))
                    checks["yolo"] = bool(parsed.get("ultralytics"))
                    checks["platform"] = bool(parsed.get("platform"))
            except (OSError, subprocess.TimeoutExpired, ValueError, json.JSONDecodeError) as exc:
                details = str(exc)
        backend_ready = checks["cuda"] if profile.get("backend") == "cuda" else True
        available = checks["python"] and checks["project_root"] and checks["torch"] and checks["yolo"] and checks["platform"] and backend_ready
        return {"profile": profile, "available": available, "checks": checks, "details": details}

    def _write(self, profiles: list[dict[str, object]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        data = json.dumps({"schema_version": 2, "profiles": profiles}, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        try:
            with temporary.open("wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _infer_backend(devices: list[str]) -> str:
        return "cuda" if any(str(device).strip().lower() != "cpu" for device in devices) else "cpu"

    @staticmethod
    def _validate_backend(backend: str) -> str:
        normalized = backend.strip().lower()
        if normalized not in {"cpu", "cuda"}:
            raise ApplicationError("validation_error", "运行后端只能是cpu或cuda", status_code=422)
        return normalized

    @classmethod
    def _normalize_profile(cls, profile: dict[str, object]) -> dict[str, object]:
        normalized = dict(profile)
        devices = [str(item) for item in normalized.get("devices") or []]
        normalized["backend"] = cls._validate_backend(
            str(normalized.get("backend") or cls._infer_backend(devices))
        )
        normalized.setdefault("capabilities", {})
        normalized.setdefault("updated_at", normalized.get("created_at"))
        return normalized
