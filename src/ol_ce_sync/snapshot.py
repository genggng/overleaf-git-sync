"""Snapshot extraction, normalization, and comparison."""

from __future__ import annotations

import shutil
import unicodedata
import zipfile
from dataclasses import dataclass
from fnmatch import fnmatch
from hashlib import sha256
from pathlib import Path

from ol_ce_sync.errors import OlSyncError
from ol_ce_sync.utils.paths import is_special_sync_path, normalize_project_path


@dataclass(frozen=True)
class TreeDiff:
    added: tuple[str, ...]
    modified: tuple[str, ...]
    deleted: tuple[str, ...]

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.modified or self.deleted)


def is_ignored(path: str, patterns: list[str]) -> bool:
    normalized = normalize_project_path(path)
    for pattern in patterns:
        pat = pattern.strip()
        if not pat:
            continue
        if pat.endswith("/"):
            prefix = pat.rstrip("/")
            if normalized == prefix or normalized.startswith(prefix + "/"):
                return True
        elif fnmatch(normalized, pat) or fnmatch(Path(normalized).name, pat):
            return True
    return False


def safe_extract_zip(zip_path: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        collision_keys: dict[str, str] = {}
        for member in archive.infolist():
            normalized = normalize_project_path(member.filename)
            if not normalized:
                continue
            collision_key = unicodedata.normalize("NFC", normalized).casefold()
            previous = collision_keys.get(collision_key)
            if previous is not None and previous != normalized:
                raise OlSyncError(
                    "Remote snapshot contains paths that collide on common local filesystems: "
                    f"{previous!r} and {normalized!r}"
                )
            if previous == normalized and not member.is_dir():
                raise OlSyncError(f"Remote snapshot contains a duplicate path: {normalized}")
            collision_keys[collision_key] = normalized
            target = dest_dir / normalized
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as dest:
                shutil.copyfileobj(source, dest)


def collect_tree(root: Path, patterns: list[str]) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    if not root.exists():
        return files
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        try:
            normalized = normalize_project_path(rel)
        except OlSyncError:
            continue
        if is_ignored(normalized, patterns):
            continue
        files[normalized] = path.read_bytes()
    return files


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_tree_hashes(root: Path, patterns: list[str]) -> dict[str, str]:
    files: dict[str, str] = {}
    if not root.exists():
        return files
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        try:
            normalized = normalize_project_path(rel)
        except OlSyncError:
            continue
        if is_ignored(normalized, patterns):
            continue
        files[normalized] = file_sha256(path)
    return files


def compare_trees(expected: dict[str, object], actual: dict[str, object]) -> TreeDiff:
    expected_keys = set(expected)
    actual_keys = set(actual)
    added = tuple(sorted(actual_keys - expected_keys))
    deleted = tuple(sorted(expected_keys - actual_keys))
    modified = tuple(
        sorted(path for path in expected_keys & actual_keys if expected[path] != actual[path])
    )
    return TreeDiff(added=added, modified=modified, deleted=deleted)


def reset_directory_from_snapshot(repo_root: Path, snapshot_dir: Path, patterns: list[str]) -> None:
    """Replace syncable repo contents with snapshot contents, preserving Git and metadata."""
    for path in sorted(repo_root.rglob("*"), reverse=True):
        rel = path.relative_to(repo_root).as_posix()
        try:
            normalized = normalize_project_path(rel)
        except OlSyncError:
            continue
        if not normalized:
            continue
        if is_special_sync_path(normalized):
            continue
        if is_ignored(normalized + ("/" if path.is_dir() else ""), patterns):
            continue
        if path.is_file() or path.is_symlink():
            path.unlink()

    for path in sorted(repo_root.rglob("*"), reverse=True):
        if not path.is_dir():
            continue
        rel = path.relative_to(repo_root).as_posix()
        try:
            normalized = normalize_project_path(rel)
        except OlSyncError:
            continue
        if not normalized or is_special_sync_path(normalized):
            continue
        if is_ignored(normalized + "/", patterns):
            continue
        if not any(path.iterdir()):
            path.rmdir()

    for source in sorted(snapshot_dir.rglob("*")):
        if not source.is_file():
            continue
        rel = source.relative_to(snapshot_dir).as_posix()
        normalized = normalize_project_path(rel)
        if is_ignored(normalized, patterns):
            continue
        target = repo_root / normalized
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
