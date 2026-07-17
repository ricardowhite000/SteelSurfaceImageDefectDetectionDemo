from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import tempfile


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    storage_key: str
    sha256: str
    size_bytes: int
    media_type: str


class LocalArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put_bytes(self, content: bytes, *, media_type: str) -> ArtifactRef:
        sha256 = hashlib.sha256(content).hexdigest()
        key = f"sha256/{sha256[:2]}/{sha256}"
        target = self.root / Path(key)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary_name = tempfile.mkstemp(prefix=".artifact-", dir=target.parent)
            try:
                with os.fdopen(handle, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary_name, target)
            finally:
                Path(temporary_name).unlink(missing_ok=True)
        return ArtifactRef(key, sha256, len(content), media_type)

    def resolve(self, artifact: ArtifactRef) -> Path:
        candidate = (self.root / Path(artifact.storage_key)).resolve()
        if self.root not in candidate.parents:
            raise ValueError("非法资产存储键")
        return candidate

    def verify(self, artifact: ArtifactRef) -> bool:
        path = self.resolve(artifact)
        if not path.is_file() or path.stat().st_size != artifact.size_bytes:
            return False
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return digest == artifact.sha256

