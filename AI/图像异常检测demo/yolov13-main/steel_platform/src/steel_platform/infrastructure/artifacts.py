from __future__ import annotations

from dataclasses import dataclass
import hashlib
from io import BytesIO
import os
from pathlib import Path
import tempfile
from typing import BinaryIO


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
        expected_sha256 = hashlib.sha256(content).hexdigest()
        return self.put_stream(BytesIO(content), media_type=media_type, expected_sha256=expected_sha256)

    def put_stream(
        self,
        stream: BinaryIO,
        *,
        media_type: str,
        expected_sha256: str | None = None,
    ) -> ArtifactRef:
        if expected_sha256 is not None:
            return self._put_expected_stream(stream, media_type=media_type, expected_sha256=expected_sha256)
        digest = hashlib.sha256()
        size_bytes = 0
        with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as spool:
            while chunk := stream.read(1024 * 1024):
                spool.write(chunk)
                digest.update(chunk)
                size_bytes += len(chunk)
            sha256 = digest.hexdigest()
            key = f"sha256/{sha256[:2]}/{sha256}"
            target = self.root / Path(key)
            target.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary_name = tempfile.mkstemp(prefix=".artifact-", dir=target.parent)
            temporary = Path(temporary_name)
            try:
                spool.seek(0)
                with os.fdopen(handle, "wb") as output:
                    while chunk := spool.read(1024 * 1024):
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if target.exists():
                    if not _same_bytes(temporary, target):
                        raise RuntimeError(f"artifact hash collision for {sha256}")
                else:
                    os.replace(temporary, target)
                return ArtifactRef(key, sha256, size_bytes, media_type)
            finally:
                temporary.unlink(missing_ok=True)

    def _put_expected_stream(
        self,
        stream: BinaryIO,
        *,
        media_type: str,
        expected_sha256: str,
    ) -> ArtifactRef:
        if len(expected_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in expected_sha256
        ):
            raise ValueError("expected_sha256 must be a lowercase hexadecimal digest")
        key = f"sha256/{expected_sha256[:2]}/{expected_sha256}"
        target = self.root / Path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(prefix=".artifact-", dir=target.parent)
        temporary = Path(temporary_name)
        digest = hashlib.sha256()
        size_bytes = 0
        try:
            with os.fdopen(handle, "wb") as output:
                while chunk := stream.read(1024 * 1024):
                    output.write(chunk)
                    digest.update(chunk)
                    size_bytes += len(chunk)
                output.flush()
                os.fsync(output.fileno())
            actual_sha256 = digest.hexdigest()
            if actual_sha256 != expected_sha256:
                raise ValueError("stream does not match expected_sha256")
            if target.exists():
                if not _same_bytes(temporary, target):
                    raise RuntimeError(f"artifact hash collision for {expected_sha256}")
            else:
                os.replace(temporary, target)
            return ArtifactRef(key, actual_sha256, size_bytes, media_type)
        finally:
            temporary.unlink(missing_ok=True)

    def resolve(self, artifact: ArtifactRef) -> Path:
        return self._resolve_key(artifact.storage_key)

    def _resolve_key(self, storage_key: str) -> Path:
        candidate = (self.root / Path(storage_key)).resolve()
        if self.root not in candidate.parents:
            raise ValueError("illegal artifact storage key")
        return candidate

    def open(self, storage_key: str) -> BinaryIO:
        return self._resolve_key(storage_key).open("rb")

    def verify(self, artifact: ArtifactRef) -> bool:
        path = self.resolve(artifact)
        if not path.is_file() or path.stat().st_size != artifact.size_bytes:
            return False
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return digest == artifact.sha256


def _same_bytes(left: Path, right: Path) -> bool:
    if left.stat().st_size != right.stat().st_size:
        return False
    with left.open("rb") as first, right.open("rb") as second:
        while True:
            first_chunk = first.read(1024 * 1024)
            second_chunk = second.read(1024 * 1024)
            if first_chunk != second_chunk:
                return False
            if not first_chunk:
                return True
