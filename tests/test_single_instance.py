from __future__ import annotations

import os
from pathlib import Path

from app.utils.single_instance import SingleInstance


def test_acquire_stores_pid_and_allows_reacquire(tmp_path: Path) -> None:
    lock_path = tmp_path / "bot.lock"

    first = SingleInstance(lock_path)
    assert first.acquire() is True
    assert first.existing_pid() == os.getpid()
    first.release()

    second = SingleInstance(lock_path)
    assert second.acquire() is True
    second.release()


def test_existing_pid_without_lock_file(tmp_path: Path) -> None:
    assert SingleInstance(tmp_path / "missing.lock").existing_pid() is None
