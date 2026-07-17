from __future__ import annotations

from collections.abc import Callable

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from steel_platform.domain.workspace import Collection, DataSource, SourceMode, SourceStatus
from steel_platform.infrastructure.models import Base
from steel_platform.infrastructure.uow import SqlAlchemyUnitOfWork


@pytest.fixture
def uow_factory() -> Callable[[], SqlAlchemyUnitOfWork]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return lambda: SqlAlchemyUnitOfWork(sessionmaker(bind=engine, class_=Session))


def test_repository_never_returns_resource_from_another_project(
    uow_factory: Callable[[], SqlAlchemyUnitOfWork],
) -> None:
    with uow_factory() as uow:
        first = uow.projects.add_project("one", "one", ("Cr",))
        second = uow.projects.add_project("two", "two", ("Cr",))
        source = DataSource(
            id="source-one",
            project_id=first.id,
            name="images",
            mode=SourceMode.EXTERNAL,
            root_path="G:/one",
            status=SourceStatus.AVAILABLE,
            revision=0,
        )
        uow.sources.add(source)
        uow.commit()

    with uow_factory() as uow:
        assert uow.sources.get(first.id, source.id) == source
        assert uow.sources.get(second.id, source.id) is None
        assert uow.sources.list(second.id) == []


def test_uow_rolls_back_failed_collection_write(
    uow_factory: Callable[[], SqlAlchemyUnitOfWork],
) -> None:
    with uow_factory() as uow:
        project = uow.projects.add_project("one", "one", ("Cr",))
        uow.commit()

    with pytest.raises(RuntimeError), uow_factory() as uow:
        uow.collections.add(
            Collection(id="temporary", project_id=project.id, name="temporary", parent_id=None, revision=0)
        )
        raise RuntimeError("abort")

    with uow_factory() as uow:
        assert uow.collections.list(project.id) == []
