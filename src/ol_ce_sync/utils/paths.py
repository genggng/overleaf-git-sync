"""Path normalization and safe relative path handling."""

from __future__ import annotations

from pathlib import PurePosixPath

from ol_ce_sync.errors import OlSyncError


def normalize_project_path(path: str) -> str:
    raw = path.replace("\\", "/")
    posix = PurePosixPath(raw)
    if posix.is_absolute():
        raise OlSyncError(f"Unsafe absolute project path: {path}")
    parts = []
    for part in posix.parts:
        if part in ("", "."):
            continue
        if part == "..":
            raise OlSyncError(f"Unsafe project path traversal: {path}")
        parts.append(part)
    return "/".join(parts)


def is_special_sync_path(path: str) -> bool:
    normalized = normalize_project_path(path)
    return (
        normalized == ".git"
        or normalized.startswith(".git/")
        or normalized.startswith(".ol-sync/")
    )
