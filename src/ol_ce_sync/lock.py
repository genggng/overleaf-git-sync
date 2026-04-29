"""Filesystem lock for sync transactions."""

from __future__ import annotations

import os
import time
from pathlib import Path

from ol_ce_sync.errors import LockError


class SyncLock:
    def __init__(self, path: Path, *, wait: bool = False, timeout_seconds: int = 60) -> None:
        self.path = path
        self.wait = wait
        self.timeout_seconds = timeout_seconds
        self._fd: int | None = None

    def __enter__(self) -> SyncLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self._fd, str(os.getpid()).encode("ascii"))
                return self
            except FileExistsError as exc:
                if not self.wait or time.monotonic() >= deadline:
                    raise LockError(f"Sync lock is already held: {self.path}") from exc
                time.sleep(0.25)

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
