from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
from typing import Iterator


class WorkspaceLockedError(RuntimeError):
    pass


def _process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, SystemError, ValueError):
        return False
    return True


@contextmanager
def single_instance_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError as exc:
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
                pid = int(current["pid"])
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                pid = -1
            if pid > 0 and _process_is_alive(pid):
                raise WorkspaceLockedError(f"工作区已由进程 {pid} 打开：{path}") from exc
            path.unlink(missing_ok=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"pid": os.getpid()}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        yield
    finally:
        path.unlink(missing_ok=True)
