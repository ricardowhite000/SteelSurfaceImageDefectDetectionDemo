from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from steel_platform.infrastructure import workspace_lock


def test_windows_invalid_parameter_probe_marks_pid_as_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    def invalid_parameter(_pid: int, _signal: int) -> None:
        raise SystemError("Windows error 87")

    monkeypatch.setattr(workspace_lock.os, "kill", invalid_parameter)

    assert workspace_lock._process_is_alive(32792) is False


def test_system_error_stale_lock_is_replaced_and_cleaned_on_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / ".steel-platform.lock"
    lock_path.write_text(json.dumps({"pid": 32792}), encoding="utf-8")

    def invalid_parameter(_pid: int, _signal: int) -> None:
        raise SystemError("Windows error 87")

    monkeypatch.setattr(workspace_lock.os, "kill", invalid_parameter)

    with workspace_lock.single_instance_lock(lock_path):
        assert json.loads(lock_path.read_text(encoding="utf-8")) == {"pid": os.getpid()}

    assert not lock_path.exists()


def test_live_lock_rejects_a_second_instance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock_path = tmp_path / ".steel-platform.lock"
    monkeypatch.setattr(workspace_lock.os, "kill", lambda _pid, _signal: None)

    with workspace_lock.single_instance_lock(lock_path):
        with pytest.raises(workspace_lock.WorkspaceLockedError):
            with workspace_lock.single_instance_lock(lock_path):
                pass

    assert not lock_path.exists()
