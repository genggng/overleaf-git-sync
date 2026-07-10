from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from ol_ce_sync.errors import LockError
from ol_ce_sync.lock import SyncLock


def test_active_lock_is_not_removed(tmp_path: Path) -> None:
    path = tmp_path / "lock"
    path.write_text(str(os.getpid()), encoding="utf-8")

    with pytest.raises(LockError, match="already held"):
        with SyncLock(path):
            pass


def test_stale_legacy_lock_is_recovered(tmp_path: Path) -> None:
    path = tmp_path / "lock"
    path.write_text("99999999", encoding="utf-8")

    with SyncLock(path):
        assert path.exists()

    assert not path.exists()


def test_old_malformed_lock_is_recovered(tmp_path: Path) -> None:
    path = tmp_path / "lock"
    path.write_text("", encoding="utf-8")
    old = time.time() - 10
    os.utime(path, (old, old))

    with SyncLock(path):
        assert path.exists()

    assert not path.exists()
