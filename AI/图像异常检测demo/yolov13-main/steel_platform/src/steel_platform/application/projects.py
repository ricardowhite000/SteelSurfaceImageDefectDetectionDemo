from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from steel_platform.application.errors import ApplicationError, NotFoundError
from steel_platform.domain.ports import UnitOfWork
from steel_platform.domain.workspace import IdempotencyRecord, Project


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
        key = idempotency_key.strip()
        if not key:
            raise ApplicationError(
                "validation_error",
                "Idempotency-Key is required",
                status_code=422,
            )
        scope = f"project-create:{command.slug}"
        with self.uow_factory() as uow:
            prior = uow.idempotency.get(key)
            if prior is not None:
                if prior.scope != scope:
                    raise ApplicationError(
                        "idempotency_conflict",
                        "Idempotency-Key has already been used for another operation",
                        status_code=409,
                    )
                project_id = prior.response.get("project_id")
                project = uow.projects.get(project_id) if isinstance(project_id, str) else None
                if project is None:
                    raise NotFoundError("Idempotent project result no longer exists")
                return project

            project = uow.projects.add_project(
                command.name.strip(),
                command.class_schema_name.strip(),
                command.class_names,
                project_id=command.slug.strip(),
            )
            uow.idempotency.add(
                IdempotencyRecord(
                    key=key,
                    scope=scope,
                    response={"project_id": project.id},
                )
            )
            uow.commit()
            return project
