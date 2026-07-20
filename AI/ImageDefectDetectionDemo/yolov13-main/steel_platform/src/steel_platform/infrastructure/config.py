from __future__ import annotations

from pathlib import Path
import os
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, field_validator
import yaml


class PlatformSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_name: str
    database_url: str
    artifact_root: Path
    source_images: Path
    candidate_labels: Path
    review_csv: Path
    seed_manifest: Path
    seed_dataset: Path
    classes: tuple[str, ...]
    host: str = "127.0.0.1"
    port: int = 8765
    seed: int = 42
    per_class: int = 30
    validation_per_class: int = 10
    yolo_python: str = "python"
    yolo_project_root: Path | None = None
    parent_weights: Path | None = None
    device: str = "0"

    @field_validator("classes")
    @classmethod
    def validate_classes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(name.strip() for name in value)
        if not normalized or any(not name for name in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("必须配置至少一个非空且不重复的类别")
        return normalized

    @property
    def database_path(self) -> Path:
        parsed = urlparse(self.database_url)
        if parsed.scheme != "sqlite":
            raise ValueError("本地Demo当前只支持sqlite数据库URL")
        return Path(parsed.path.lstrip("/")).resolve()


def load_settings(path: Path) -> PlatformSettings:
    config_path = path.resolve()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("配置文件根节点必须是对象")
    # New portable layout: the committed project file contains business rules,
    # while the ignored machine file contains paths and execution capabilities.
    if raw.get("project_config") or raw.get("machine_config"):
        merged: dict[str, object] = {}
        for key in ("project_config", "machine_config"):
            reference = raw.get(key)
            if not reference:
                continue
            referenced_path = (config_path.parent / str(reference)).resolve()
            fragment = yaml.safe_load(referenced_path.read_text(encoding="utf-8"))
            if not isinstance(fragment, dict):
                raise ValueError(f"配置片段根节点必须是对象：{referenced_path}")
            merged.update(fragment)
        merged.update({key: value for key, value in raw.items() if key not in {"project_config", "machine_config"}})
        raw = merged
    environment_overrides = {
        "database_url": "STEEL_PLATFORM_DATABASE_URL",
        "artifact_root": "STEEL_PLATFORM_ARTIFACT_ROOT",
        "host": "STEEL_PLATFORM_HOST",
        "port": "STEEL_PLATFORM_PORT",
        "yolo_python": "STEEL_PLATFORM_YOLO_PYTHON",
        "device": "STEEL_PLATFORM_DEVICE",
    }
    for field, variable in environment_overrides.items():
        if variable in os.environ:
            raw[field] = int(os.environ[variable]) if field == "port" else os.environ[variable]
    base = config_path.parent
    for field in ("artifact_root", "source_images", "candidate_labels", "review_csv", "seed_manifest", "seed_dataset"):
        raw[field] = (base / Path(raw[field])).resolve()
    for field in ("yolo_project_root", "parent_weights"):
        if raw.get(field):
            raw[field] = (base / Path(raw[field])).resolve()
    database_url = str(raw["database_url"])
    prefix = "sqlite:///"
    if database_url.startswith(prefix):
        database_path = (base / database_url[len(prefix):]).resolve()
        raw["database_url"] = f"sqlite:///{database_path.as_posix()}"
    return PlatformSettings.model_validate(raw)
