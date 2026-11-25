from __future__ import annotations

import os
from pathlib import Path


class SingleInstance:
    """Prevent two bot processes from polling the same Telegram token.

    The lock is held by the OS for the lifetime of the process and is released
    automatically even if the bot crashes. The first byte of the lock file is
    the lock marker, the rest stores the PID of the running instance.
    """

    def __init__(self, lock_path: Path) -> None:
        self._path = lock_path
        self._handle: object | None = None

    def acquire(self) -> bool:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self._path, "a+b")  # noqa: SIM115 - held until process exit
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() < 1:
                handle.seek(0)
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False

        handle.seek(1)
        handle.truncate()
        handle.write(str(os.getpid()).encode("ascii"))
        handle.flush()
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            self._handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None

    def existing_pid(self) -> int | None:
        try:
            with open(self._path, "rb") as handle:
                handle.seek(1)
                raw = handle.read().decode("ascii").strip()
        except OSError:
            return None
        try:
            return int(raw)
        except ValueError:
            return None
