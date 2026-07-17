from __future__ import annotations

from collections.abc import Callable
from types import TracebackType

from sqlalchemy.orm import Session

from steel_platform.infrastructure.repositories import (
    SqlCollectionRepository,
    SqlDataSourceRepository,
    SqlExplorerRepository,
    SqlIdempotencyRepository,
    SqlImportRepository,
    SqlProjectRepository,
    SqlReviewTaskRepository,
)


class _ContextSession:
    """Forwards repository access only while its Unit of Work is active."""

    def __init__(self, uow: SqlAlchemyUnitOfWork, generation: int) -> None:
        self._uow = uow
        self._generation = generation

    def __getattr__(self, name: str) -> object:
        return getattr(self._uow._require_session(self._generation), name)


class SqlAlchemyUnitOfWork:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory
        self.session: Session | None = None
        self._generation = 0

    def __enter__(self) -> SqlAlchemyUnitOfWork:
        if self.session is not None:
            raise RuntimeError("Unit of Work is already active")
        self._generation += 1
        self.session = self._session_factory()
        repository_session = _ContextSession(self, self._generation)
        self.projects = SqlProjectRepository(repository_session)  # type: ignore[arg-type]
        self.sources = SqlDataSourceRepository(repository_session)  # type: ignore[arg-type]
        self.collections = SqlCollectionRepository(repository_session)  # type: ignore[arg-type]
        self.imports = SqlImportRepository(repository_session)  # type: ignore[arg-type]
        self.review_tasks = SqlReviewTaskRepository(repository_session)  # type: ignore[arg-type]
        self.explorer = SqlExplorerRepository(repository_session)  # type: ignore[arg-type]
        self.idempotency = SqlIdempotencyRepository(repository_session)  # type: ignore[arg-type]
        self.data_sources = self.sources
        self.reviews = self.review_tasks
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        session = self.session
        if session is None:
            return
        try:
            if exc_type is not None:
                session.rollback()
        finally:
            try:
                session.close()
            finally:
                self.session = None

    def commit(self) -> None:
        self._require_session().commit()

    def rollback(self) -> None:
        self._require_session().rollback()

    def _require_session(self, generation: int | None = None) -> Session:
        if self.session is None or (generation is not None and generation != self._generation):
            raise RuntimeError("Unit of Work has not been entered")
        return self.session
