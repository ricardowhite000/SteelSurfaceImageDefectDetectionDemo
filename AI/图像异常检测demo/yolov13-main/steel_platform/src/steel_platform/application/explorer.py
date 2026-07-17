from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypedDict
from uuid import uuid4

from steel_platform.application.errors import ApplicationError, NotFoundError, RevisionConflictError
from steel_platform.application.projects import canonical_scope, require_idempotency_key, require_matching_scope
from steel_platform.domain.ports import UnitOfWork
from steel_platform.domain.workspace import (
    Collection,
    ConcurrentAllocationError,
    ExplorerResource,
    IdempotencyRecord,
    IdempotencyReservationConflict,
    Project,
    ReviewTaskItems,
)


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
        idempotency_key: str,
    ) -> Collection:
        key = require_idempotency_key(idempotency_key)
        member_ids = tuple(dict.fromkeys(asset_ids))
        scope = canonical_scope(
            "collection-add-members",
            {
                "project_id": project_id,
                "collection_id": collection_id,
                "asset_ids": sorted(member_ids),
                "expected_revision": expected_revision,
            },
        )
        try:
            with self.uow_factory() as uow:
                prior = uow.idempotency.get(key)
                if prior is not None:
                    return self._replay_collection(prior, scope)
                current = self._require_collection(uow, project_id, collection_id)
                for asset_id in member_ids:
                    if not uow.explorer.asset_exists(project_id, asset_id):
                        raise NotFoundError(f"asset {asset_id!r} was not found in project {project_id!r}")
                uow.idempotency.reserve(IdempotencyRecord(key=key, scope=scope, response={}))
                changed = uow.collections.bump_revision(project_id, collection_id, expected_revision)
                if changed is None:
                    raise RevisionConflictError(expected_revision, current.revision)
                uow.collections.add_members(project_id, collection_id, member_ids)
                uow.idempotency.set_response(key, self._collection_response(changed))
                uow.commit()
                return changed
        except IdempotencyReservationConflict:
            return self._replay_committed_collection(key, scope)

    def remove_member(
        self,
        project_id: str,
        collection_id: str,
        asset_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> Collection:
        key = require_idempotency_key(idempotency_key)
        scope = canonical_scope(
            "collection-remove-member",
            {
                "project_id": project_id,
                "collection_id": collection_id,
                "asset_id": asset_id,
                "expected_revision": expected_revision,
            },
        )
        try:
            with self.uow_factory() as uow:
                prior = uow.idempotency.get(key)
                if prior is not None:
                    return self._replay_collection(prior, scope)
                current = self._require_collection(uow, project_id, collection_id)
                if not uow.explorer.asset_exists(project_id, asset_id):
                    raise NotFoundError(f"asset {asset_id!r} was not found in project {project_id!r}")
                if asset_id not in uow.collections.list_members(project_id, collection_id):
                    raise NotFoundError(f"asset {asset_id!r} is not a member of collection {collection_id!r}")
                uow.idempotency.reserve(IdempotencyRecord(key=key, scope=scope, response={}))
                changed = uow.collections.bump_revision(project_id, collection_id, expected_revision)
                if changed is None:
                    raise RevisionConflictError(expected_revision, current.revision)
                uow.collections.remove_member(project_id, collection_id, asset_id)
                uow.idempotency.set_response(key, self._collection_response(changed))
                uow.commit()
                return changed
        except IdempotencyReservationConflict:
            return self._replay_committed_collection(key, scope)

    def list_members(self, project_id: str, collection_id: str) -> tuple[str, ...]:
        with self.uow_factory() as uow:
            self._require_collection(uow, project_id, collection_id)
            return tuple(uow.collections.list_members(project_id, collection_id))

    def _replay_committed_collection(self, key: str, scope: str) -> Collection:
        for _ in range(3):
            with self.uow_factory() as uow:
                prior = uow.idempotency.get(key)
                if prior is not None:
                    return self._replay_collection(prior, scope)
        raise ApplicationError(
            "concurrency_conflict",
            "Concurrent idempotency reservation did not become visible",
            status_code=409,
        )

    @staticmethod
    def _collection_response(collection: Collection) -> dict[str, object]:
        return {
            "id": collection.id,
            "project_id": collection.project_id,
            "name": collection.name,
            "parent_id": collection.parent_id,
            "revision": collection.revision,
        }

    @staticmethod
    def _replay_collection(prior: IdempotencyRecord, scope: str) -> Collection:
        require_matching_scope(prior, scope)
        response = prior.response
        try:
            return Collection(
                id=str(response["id"]),
                project_id=str(response["project_id"]),
                name=str(response["name"]),
                parent_id=str(response["parent_id"]) if response["parent_id"] is not None else None,
                revision=int(response["revision"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ApplicationError(
                "idempotency_conflict",
                "Stored idempotency response is incomplete",
                status_code=409,
            ) from exc

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

    _MAX_ALLOCATION_ATTEMPTS = 3

    def create_from_collection(
        self,
        project_id: str,
        collection_id: str,
        *,
        sample_size: int,
        idempotency_key: str,
    ) -> str:
        if sample_size <= 0:
            raise ApplicationError("validation_error", "sample_size must be positive", status_code=422)
        key = require_idempotency_key(idempotency_key)
        scope = canonical_scope(
            "review-task-create",
            {
                "project_id": project_id,
                "collection_id": collection_id,
                "sample_size": sample_size,
            },
        )
        for attempt in range(self._MAX_ALLOCATION_ATTEMPTS):
            try:
                with self.uow_factory() as uow:
                    prior = uow.idempotency.get(key)
                    if prior is not None:
                        return self._replay_round(uow, prior, scope)
                    ExplorerService._require_project(uow, project_id)
                    ExplorerService._require_collection(uow, project_id, collection_id)
                    uow.idempotency.reserve(IdempotencyRecord(key=key, scope=scope, response={}))
                    round_id = uow.review_tasks.create_from_collection(project_id, collection_id, sample_size)
                    uow.idempotency.set_response(
                        key,
                        {"project_id": project_id, "round_id": round_id},
                    )
                    uow.commit()
                    return round_id
            except IdempotencyReservationConflict:
                return self._replay_committed_round(key, scope)
            except ConcurrentAllocationError as exc:
                if attempt + 1 == self._MAX_ALLOCATION_ATTEMPTS:
                    raise ApplicationError(
                        "concurrency_conflict",
                        "Could not allocate a review task number after bounded retries",
                        status_code=409,
                    ) from exc
        raise AssertionError("unreachable")

    def list_items(self, project_id: str, round_id: str) -> ReviewTaskItems:
        with self.uow_factory() as uow:
            ExplorerService._require_project(uow, project_id)
            if uow.review_tasks.get_round(project_id, round_id) is None:
                raise NotFoundError(f"review task {round_id!r} was not found in project {project_id!r}")
            items = uow.review_tasks.list_items(project_id, round_id, None)
            asset_ids = tuple(item.image_asset_id for item in items)
            return ReviewTaskItems(total=len(asset_ids), asset_ids=asset_ids)

    def _replay_committed_round(self, key: str, scope: str) -> str:
        for _ in range(3):
            with self.uow_factory() as uow:
                prior = uow.idempotency.get(key)
                if prior is not None:
                    return self._replay_round(uow, prior, scope)
        raise ApplicationError(
            "concurrency_conflict",
            "Concurrent idempotency reservation did not become visible",
            status_code=409,
        )

    @staticmethod
    def _replay_round(uow: UnitOfWork, prior: IdempotencyRecord, scope: str) -> str:
        require_matching_scope(prior, scope)
        project_id = prior.response.get("project_id")
        round_id = prior.response.get("round_id")
        if not isinstance(project_id, str) or not isinstance(round_id, str):
            raise ApplicationError(
                "idempotency_conflict",
                "Stored idempotency response is incomplete",
                status_code=409,
            )
        if uow.review_tasks.get_round(project_id, round_id) is None:
            raise NotFoundError("Idempotent review task result no longer exists")
        return round_id
