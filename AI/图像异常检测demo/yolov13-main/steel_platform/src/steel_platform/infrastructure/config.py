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
        if len(value) != 6 or len(set(value)) != 6:
            raise ValueError("必须配置六个不重复类别")
        return value

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
