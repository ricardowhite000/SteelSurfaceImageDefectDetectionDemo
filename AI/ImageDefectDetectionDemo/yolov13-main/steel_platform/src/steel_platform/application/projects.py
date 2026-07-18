from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any

from steel_platform.application.errors import ApplicationError, NotFoundError
from steel_platform.domain.ports import UnitOfWork
from steel_platform.domain.workspace import IdempotencyRecord, IdempotencyReservationConflict, Project


def canonical_scope(operation: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return f"{operation}:{sha256(encoded).hexdigest()}"


def require_idempotency_key(idempotency_key: str) -> str:
    key = idempotency_key.strip()
    if not key:
        raise ApplicationError(
            "validation_error",
            "Idempotency-Key is required",
            status_code=422,
        )
    return key


def require_matching_scope(record: IdempotencyRecord, scope: str) -> None:
    if record.scope != scope:
        raise ApplicationError(
            "idempotency_conflict",
            "Idempotency-Key has already been used with a different payload",
            status_code=409,
        )


@dataclass(frozen=True, slots=True)
class CreateProjectCommand:
    name: str
    slug: str
    class_schema_name: str
    class_names: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "class_names", tuple(self.class_names))
        if not self.name.strip() or not self.slug.strip() or not self.class_schema_name.strip():
            raise ValueError("project name, slug, and class schema name are required")
        if not self.class_names or len(set(self.class_names)) != len(self.class_names):
            raise ValueError("class names must be non-empty and unique")


class ProjectCatalogService:
    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self.uow_factory = uow_factory

    def list_projects(self) -> list[Project]:
        with self.uow_factory() as uow:
            return list(uow.projects.list())

    def create_project(self, command: CreateProjectCommand, idempotency_key: str) -> Project:
        key = require_idempotency_key(idempotency_key)
        scope = canonical_scope(
            "project-create",
            {
                "slug": command.slug.strip(),
                "name": command.name.strip(),
                "class_schema_name": command.class_schema_name.strip(),
                "class_names": list(command.class_names),
            },
        )
        try:
            with self.uow_factory() as uow:
                prior = uow.idempotency.get(key)
                if prior is not None:
                    return self._replay(uow, prior, scope)
                uow.idempotency.reserve(IdempotencyRecord(key=key, scope=scope, response={}))
                project = uow.projects.add_project(
                    command.name.strip(),
                    command.class_schema_name.strip(),
                    command.class_names,
                    project_id=command.slug.strip(),
                )
                uow.idempotency.set_response(key, {"project_id": project.id})
                uow.commit()
                return project
        except IdempotencyReservationConflict:
            return self._replay_committed(key, scope)

    def _replay_committed(self, key: str, scope: str) -> Project:
        for _ in range(3):
            with self.uow_factory() as uow:
                prior = uow.idempotency.get(key)
                if prior is not None:
                    return self._replay(uow, prior, scope)
        raise ApplicationError(
            "concurrency_conflict",
            "Concurrent idempotency reservation did not become visible",
            status_code=409,
        )

    @staticmethod
    def _replay(uow: UnitOfWork, prior: IdempotencyRecord, scope: str) -> Project:
        require_matching_scope(prior, scope)
        project_id = prior.response.get("project_id")
        project = uow.projects.get(project_id) if isinstance(project_id, str) else None
        if project is None:
            raise NotFoundError("Idempotent project result no longer exists")
        return project
