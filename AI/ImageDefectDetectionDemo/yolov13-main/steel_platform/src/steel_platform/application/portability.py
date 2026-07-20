from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from steel_platform.application.errors import NotFoundError
from steel_platform.application.imports import DataSourceImportService
from steel_platform.infrastructure.models import SourceBindingModel, SourceRootModel, WorkspaceNodeModel, utc_now


class SourceBindingService:
    def __init__(self, factory: sessionmaker[Session], imports: DataSourceImportService) -> None:
        self._factory = factory
        self._imports = imports

    def list(self, project_id: str) -> list[dict[str, object]]:
        with self._factory() as session:
            rows = session.execute(
                select(SourceRootModel, SourceBindingModel, WorkspaceNodeModel)
                .outerjoin(SourceBindingModel, SourceBindingModel.source_root_id == SourceRootModel.id)
                .outerjoin(WorkspaceNodeModel, WorkspaceNodeModel.id == SourceBindingModel.node_id)
                .where(SourceRootModel.project_id == project_id)
                .order_by(SourceRootModel.name, SourceRootModel.id)
            ).all()
            return [
                {
                    "source_id": source.id, "source_name": source.name, "mode": source.mode,
                    "logical_status": source.status, "node_id": binding.node_id if binding else None,
                    "node_name": node.name if node else None, "locator": binding.locator if binding else None,
                    "binding_status": binding.status if binding else "unbound",
                    "manifest_sha256": binding.manifest_sha256 if binding else source.manifest_sha256,
                    "revision": binding.revision if binding else 0,
                }
                for source, binding, node in rows
            ]

    def bind(self, project_id: str, source_id: str, locator: Path) -> dict[str, object]:
        rebound = self._imports.rebind(project_id, source_id, locator)
        with self._factory.begin() as session:
            node = session.scalar(select(WorkspaceNodeModel).order_by(WorkspaceNodeModel.created_at))
            if node is None:
                node = WorkspaceNodeModel(name="默认本机", fingerprint="local-default")
                session.add(node)
                session.flush()
            binding = session.scalar(
                select(SourceBindingModel).where(
                    SourceBindingModel.source_root_id == source_id,
                    SourceBindingModel.node_id == node.id,
                )
            )
            if binding is None:
                binding = SourceBindingModel(source_root_id=source_id, node_id=node.id, locator=str(rebound.root_path))
                session.add(binding)
            binding.locator = str(rebound.root_path)
            binding.status = rebound.status.value
            binding.manifest_sha256 = rebound.manifest_sha256
            binding.last_verified_at = utc_now()
            binding.revision += 1
        rows = [item for item in self.list(project_id) if item["source_id"] == source_id]
        if not rows:
            raise NotFoundError("数据源绑定不存在")
        return rows[0]
