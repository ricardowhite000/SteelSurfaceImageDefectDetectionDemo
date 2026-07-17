from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypedDict
from uuid import uuid4

from steel_platform.application.errors import ApplicationError, NotFoundError, RevisionConflictError
from steel_platform.domain.ports import UnitOfWork
from steel_platform.domain.workspace import Collection, ExplorerResource, Project, ReviewTaskItems


class ExplorerNode(TypedDict):
    id: str
    type: str
    name: str
    count: int
    status: str
    children: list[ExplorerNode]


class ProjectNode(TypedDict):
    id: str
    name: str


class ExplorerTree(TypedDict):
    project: ProjectNode
    groups: list[ExplorerNode]


class ExplorerService:
    _GROUPS = (
        ("sources", "Data sources", "source"),
        ("collections", "Collections", "collection"),
        ("review-rounds", "Review tasks", "review_round"),
        ("datasets", "Dataset versions", "dataset"),
        ("models", "Models", "model"),
        ("inference", "Inference runs", "inference"),
    )

    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self.uow_factory = uow_factory

    def tree(self, project_id: str) -> ExplorerTree:
        with self.uow_factory() as uow:
            project = self._require_project(uow, project_id)
            resources = list(uow.explorer.list_resources(project_id))
            collection_nodes = self._collection_nodes(uow, project_id)
            groups: list[ExplorerNode] = []
            for group_id, name, resource_type in self._GROUPS:
                children = (
                    collection_nodes
                    if resource_type == "collection"
                    else [self._resource_node(resource) for resource in resources if resource.type == resource_type]
                )
                groups.append(self._node(group_id, "group", name, len(children), "available", children))
            return {
                "project": {"id": project.id, "name": project.name},
                "groups": groups,
            }

    def create_collection(
        self,
        project_id: str,
        name: str,
        parent_id: str | None = None,
    ) -> Collection:
        clean_name = name.strip()
        if not clean_name:
            raise ApplicationError("validation_error", "collection name is required", status_code=422)
        with self.uow_factory() as uow:
            self._require_project(uow, project_id)
            if parent_id is not None:
                self._require_collection(uow, project_id, parent_id)
            collection = Collection(
                id=str(uuid4()),
                project_id=project_id,
                name=clean_name,
                parent_id=parent_id,
                revision=0,
            )
            uow.collections.add(project_id, collection)
            uow.commit()
            return collection

    def rename_collection(
        self,
        project_id: str,
        collection_id: str,
        name: str,
        *,
        expected_revision: int,
    ) -> Collection:
        clean_name = name.strip()
        if not clean_name:
            raise ApplicationError("validation_error", "collection name is required", status_code=422)
        with self.uow_factory() as uow:
            current = self._require_collection(uow, project_id, collection_id)
            changed = uow.collections.rename(
                project_id,
                collection_id,
                clean_name,
                expected_revision,
            )
            if changed is None:
                raise RevisionConflictError(expected_revision, current.revision)
            uow.commit()
            return changed

    def add_members(
        self,
        project_id: str,
        collection_id: str,
        asset_ids: Iterable[str],
        *,
        expected_revision: int,
    ) -> Collection:
        member_ids = tuple(dict.fromkeys(asset_ids))
        with self.uow_factory() as uow:
            current = self._require_collection(uow, project_id, collection_id)
            for asset_id in member_ids:
                if not uow.explorer.asset_exists(project_id, asset_id):
                    raise NotFoundError(f"asset {asset_id!r} was not found in project {project_id!r}")
            changed = uow.collections.bump_revision(project_id, collection_id, expected_revision)
            if changed is None:
                raise RevisionConflictError(expected_revision, current.revision)
            uow.collections.add_members(project_id, collection_id, member_ids)
            uow.commit()
            return changed

    def remove_member(
        self,
        project_id: str,
        collection_id: str,
        asset_id: str,
        *,
        expected_revision: int,
    ) -> Collection:
        with self.uow_factory() as uow:
            current = self._require_collection(uow, project_id, collection_id)
            if not uow.explorer.asset_exists(project_id, asset_id):
                raise NotFoundError(f"asset {asset_id!r} was not found in project {project_id!r}")
            if asset_id not in uow.collections.list_members(project_id, collection_id):
                raise NotFoundError(f"asset {asset_id!r} is not a member of collection {collection_id!r}")
            changed = uow.collections.bump_revision(project_id, collection_id, expected_revision)
            if changed is None:
                raise RevisionConflictError(expected_revision, current.revision)
            uow.collections.remove_member(project_id, collection_id, asset_id)
            uow.commit()
            return changed

    def list_members(self, project_id: str, collection_id: str) -> tuple[str, ...]:
        with self.uow_factory() as uow:
            self._require_collection(uow, project_id, collection_id)
            return tuple(uow.collections.list_members(project_id, collection_id))

    @staticmethod
    def _require_project(uow: UnitOfWork, project_id: str) -> Project:
        project = uow.projects.get(project_id)
        if project is None:
            raise NotFoundError(f"project {project_id!r} was not found")
        return project

    @staticmethod
    def _require_collection(uow: UnitOfWork, project_id: str, collection_id: str) -> Collection:
        collection = uow.collections.get(project_id, collection_id)
        if collection is None:
            raise NotFoundError(
                f"collection {collection_id!r} was not found in project {project_id!r}"
            )
        return collection

    def _collection_nodes(self, uow: UnitOfWork, project_id: str) -> list[ExplorerNode]:
        collections = list(uow.collections.list(project_id))
        nodes = {
            collection.id: self._node(
                collection.id,
                "collection",
                collection.name,
                len(uow.collections.list_members(project_id, collection.id)),
                "available",
                [],
            )
            for collection in collections
        }
        roots: list[ExplorerNode] = []
        for collection in collections:
            if collection.parent_id is None:
                roots.append(nodes[collection.id])
            elif collection.parent_id in nodes:
                nodes[collection.parent_id]["children"].append(nodes[collection.id])
        return roots

    @classmethod
    def _resource_node(cls, resource: ExplorerResource) -> ExplorerNode:
        return cls._node(
            resource.id,
            resource.type,
            resource.name,
            resource.count,
            resource.status,
            [],
        )

    @staticmethod
    def _node(
        node_id: str,
        node_type: str,
        name: str,
        count: int,
        status: str,
        children: list[ExplorerNode],
    ) -> ExplorerNode:
        return {
            "id": node_id,
            "type": node_type,
            "name": name,
            "count": count,
            "status": status,
            "children": children,
        }


class ReviewTaskCreationService:
    """Creates a review task by copying a collection's current asset IDs."""

    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        *,
        explorer: ExplorerService | None = None,
    ) -> None:
        self.uow_factory = uow_factory
        self.explorer = explorer or ExplorerService(uow_factory)

    def create_from_collection(self, project_id: str, collection_id: str, *, sample_size: int) -> str:
        if sample_size <= 0:
            raise ApplicationError("validation_error", "sample_size must be positive", status_code=422)
        with self.uow_factory() as uow:
            ExplorerService._require_project(uow, project_id)
            ExplorerService._require_collection(uow, project_id, collection_id)
            round_id = uow.reviews.create_from_collection(project_id, collection_id, sample_size)
            uow.commit()
            return round_id

    def list_items(self, project_id: str, round_id: str) -> ReviewTaskItems:
        with self.uow_factory() as uow:
            ExplorerService._require_project(uow, project_id)
            if uow.reviews.get_round(project_id, round_id) is None:
                raise NotFoundError(f"review task {round_id!r} was not found in project {project_id!r}")
            items = uow.reviews.list_items(project_id, round_id, None)
            asset_ids = tuple(item.image_asset_id for item in items)
            return ReviewTaskItems(total=len(asset_ids), asset_ids=asset_ids)
