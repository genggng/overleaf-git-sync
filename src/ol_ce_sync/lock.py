"""Filesystem lock for sync transactions."""

from __future__ import annotations

import json
import os
import socket
import time
import uuid
from pathlib import Path

from ol_ce_sync.errors import LockError


class SyncLock:
    def __init__(self, path: Path, *, wait: bool = False, timeout_seconds: int = 60) -> None:
        self.path = path
        self.wait = wait
        self.timeout_seconds = timeout_seconds
        self._fd: int | None = None
        self._token = uuid.uuid4().hex

    def __enter__(self) -> SyncLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                payload = {
                    "pid": os.getpid(),
                    "hostname": socket.gethostname(),
                    "created_at": time.time(),
                    "token": self._token,
                }
                os.write(self._fd, json.dumps(payload, sort_keys=True).encode("utf-8"))
                os.fsync(self._fd)
                return self
            except FileExistsError as exc:
                if self._remove_stale_lock():
                    continue
                if not self.wait or time.monotonic() >= deadline:
                    raise LockError(f"Sync lock is already held: {self.path}") from exc
                time.sleep(0.25)

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("token") == self._token:
                self.path.unlink()
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass

    def _remove_stale_lock(self) -> bool:
        try:
            raw = self.path.read_text(encoding="utf-8")
            try:
                data = json.loads(raw)
                pid = int(data["pid"])
                hostname = str(data.get("hostname", socket.gethostname()))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                try:
                    pid = int(raw.strip())
                    hostname = socket.gethostname()
                except ValueError:
                    return self._remove_old_malformed_lock(raw)
        except (FileNotFoundError, OSError):
            return False

        if hostname != socket.gethostname() or self._pid_is_active(pid):
            return False
        try:
            if self.path.read_text(encoding="utf-8") != raw:
                return False
            self.path.unlink()
            return True
        except (FileNotFoundError, OSError):
            return False

    def _remove_old_malformed_lock(self, raw: str) -> bool:
        try:
            if time.time() - self.path.stat().st_mtime < 5:
                return False
            if self.path.read_text(encoding="utf-8") != raw:
                return False
            self.path.unlink()
            return True
        except (FileNotFoundError, OSError):
            return False

    @staticmethod
    def _pid_is_active(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
