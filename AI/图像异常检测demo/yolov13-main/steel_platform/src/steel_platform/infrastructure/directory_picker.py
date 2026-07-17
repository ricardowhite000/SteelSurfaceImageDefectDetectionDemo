from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
import mimetypes
from pathlib import Path
from typing import BinaryIO

from steel_platform.domain.workspace import ManifestEntry, normalize_relative_path


class UnavailableDirectoryPicker:
    def pick(self) -> None:
        return None

    def pick_directory(self, *, title: str) -> None:
        return None


class WindowsDirectoryPicker:
    """Windows picker boundary; the UI callback is supplied by the interface layer."""

    def __init__(self, callback: Callable[[str], Path | None] | None = None) -> None:
        self._callback = callback

    def pick(self, *, title: str = "Choose folder") -> Path | None:
        if self._callback is None:
            return None
        return self._callback(title)

    def pick_directory(self, *, title: str) -> str | None:
        selected = self.pick(title=title)
        return selected.as_posix() if selected is not None else None


class LocalFolderReader:
    def canonicalize(self, locator: str) -> str:
        root = Path(locator).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("source locator must identify a directory")
        return root.as_posix()

    def scan(self, locator: str) -> tuple[ManifestEntry, ...]:
        root = Path(self.canonicalize(locator))
        entries: list[ManifestEntry] = []
        for candidate in sorted(root.rglob("*")):
            if not candidate.is_file():
                continue
            resolved = candidate.resolve(strict=True)
            if root != resolved and root not in resolved.parents:
                raise ValueError("source file escapes the registered root")
            relative_path = normalize_relative_path(candidate.relative_to(root).as_posix())
            digest = sha256()
            size_bytes = 0
            with candidate.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
                    size_bytes += len(chunk)
            media_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            entries.append(ManifestEntry(relative_path, size_bytes, media_type, digest.hexdigest()))
        return tuple(entries)

    def open_readonly(self, locator: str, relative_path: str) -> BinaryIO:
        normalized = normalize_relative_path(relative_path)
        root = Path(self.canonicalize(locator))
        candidate = (root / Path(normalized)).resolve(strict=True)
        if root not in candidate.parents or not candidate.is_file():
            raise ValueError("source file escapes the registered root")
        return candidate.open("rb")
