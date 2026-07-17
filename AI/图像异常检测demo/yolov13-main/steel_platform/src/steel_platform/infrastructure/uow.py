from __future__ import annotations

from collections.abc import Callable
from types import TracebackType

from sqlalchemy.orm import Session

from steel_platform.infrastructure.repositories import (
    SqlCollectionRepository,
    SqlDataSourceRepository,
    SqlImportRepository,
    SqlProjectRepository,
    SqlReviewTaskRepository,
)


class SqlAlchemyUnitOfWork:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory
        self.session: Session | None = None

    def __enter__(self) -> SqlAlchemyUnitOfWork:
        self.session = self._session_factory()
        self.projects = SqlProjectRepository(self.session)
        self.sources = SqlDataSourceRepository(self.session)
        self.collections = SqlCollectionRepository(self.session)
        self.imports = SqlImportRepository(self.session)
        self.reviews = SqlReviewTaskRepository(self.session)
        self.data_sources = self.sources
        self.review_tasks = self.reviews
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self.session is None:
            return
        try:
            if exc_type is not None:
                self.session.rollback()
        finally:
            self.session.close()

    def commit(self) -> None:
        self._require_session().commit()

    def rollback(self) -> None:
        self._require_session().rollback()

    def _require_session(self) -> Session:
        if self.session is None:
            raise RuntimeError("Unit of Work has not been entered")
        return self.session
