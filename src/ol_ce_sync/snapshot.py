"""Snapshot extraction, normalization, and comparison."""

from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from ol_ce_sync.errors import OlSyncError
from ol_ce_sync.utils.paths import normalize_project_path


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
        for member in archive.infolist():
            normalized = normalize_project_path(member.filename)
            if not normalized:
                continue
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


def compare_trees(expected: dict[str, bytes], actual: dict[str, bytes]) -> TreeDiff:
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
    for child in repo_root.iterdir():
        name = child.name
        if name in {".git", ".ol-sync"}:
            continue
        if is_ignored(name + ("/" if child.is_dir() else ""), patterns):
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()

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
