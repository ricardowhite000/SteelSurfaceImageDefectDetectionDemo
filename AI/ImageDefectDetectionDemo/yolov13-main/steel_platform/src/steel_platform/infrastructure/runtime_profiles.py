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
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ApplicationError("runtime_registry_invalid", "运行环境注册表格式无效", status_code=500)
        return list(payload.get("profiles") or [])

    def add(
        self,
        *,
        name: str,
        python_executable: str,
        project_root: str,
        devices: list[str],
    ) -> dict[str, object]:
        if not name.strip() or not python_executable.strip() or not project_root.strip():
            raise ApplicationError("validation_error", "名称、Python解释器和项目目录不能为空", status_code=422)
        if not devices or any(not str(device).strip() for device in devices):
            raise ApplicationError("validation_error", "至少配置一个运行设备", status_code=422)
        profiles = self.list()
        if any(item["name"].casefold() == name.strip().casefold() for item in profiles):
            raise ApplicationError("runtime_name_exists", "运行环境名称已存在", status_code=409)
        profile: dict[str, object] = {
            "id": str(uuid4()),
            "name": name.strip(),
            "python_executable": str(Path(python_executable).expanduser()),
            "project_root": str(Path(project_root).expanduser()),
            "devices": [str(device).strip() for device in devices],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        profiles.append(profile)
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
        checks = {"python": executable.is_file(), "project_root": project_root.is_dir(), "torch": False, "cuda": False, "yolo": False}
        details = "Python解释器不存在"
        if checks["python"]:
            command = [
                str(executable), "-c",
                "import json; import torch; import ultralytics; print(json.dumps({'torch':torch.__version__,'cuda':torch.cuda.is_available(),'ultralytics':ultralytics.__version__}))",
            ]
            try:
                completed = subprocess.run(command, cwd=project_root if project_root.is_dir() else None, shell=False, capture_output=True, text=True, timeout=30, check=False)
                details = (completed.stdout or completed.stderr).strip()
                if completed.returncode == 0:
                    parsed = json.loads(completed.stdout.strip().splitlines()[-1])
                    checks["torch"] = bool(parsed.get("torch"))
                    checks["cuda"] = bool(parsed.get("cuda"))
                    checks["yolo"] = bool(parsed.get("ultralytics"))
            except (OSError, subprocess.TimeoutExpired, ValueError, json.JSONDecodeError) as exc:
                details = str(exc)
        return {"profile": profile, "available": checks["python"] and checks["project_root"] and checks["torch"] and checks["yolo"], "checks": checks, "details": details}

    def _write(self, profiles: list[dict[str, object]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        data = json.dumps({"schema_version": 1, "profiles": profiles}, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        try:
            with temporary.open("wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
