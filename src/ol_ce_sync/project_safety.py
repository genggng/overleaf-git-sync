"""Preflight checks for filesystem entries that cannot be synchronized safely."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from ol_ce_sync import git_ops
from ol_ce_sync.errors import ConfigError


def validate_local_project_tree(repo_root: Path) -> None:
    """Reject entries whose Git/filesystem semantics Overleaf cannot preserve."""
    for current, dir_names, file_names in os.walk(repo_root, followlinks=False):
        current_path = Path(current)
        relative_current = current_path.relative_to(repo_root)

        if relative_current == Path("."):
            dir_names[:] = [name for name in dir_names if name not in {".git", ".ol-sync"}]

        for name in [*dir_names, *file_names]:
            path = current_path / name
            relative = path.relative_to(repo_root).as_posix()
            if path.is_symlink():
                raise ConfigError(
                    f"Unsupported symbolic link in project: {relative}. "
                    "Replace it with a regular file before syncing."
                )
            if name == ".git" and relative != ".git":
                raise ConfigError(
                    f"Unsupported nested Git repository: {relative}. "
                    "Move it outside the Overleaf project before syncing."
                )
            if path.is_file():
                mode = path.stat().st_mode
                if not stat.S_ISREG(mode):
                    raise ConfigError(f"Unsupported special filesystem entry: {relative}")

    if git_ops.is_git_repo(repo_root):
        gitlinks = git_ops.gitlink_paths(repo_root)
        if gitlinks:
            formatted = "\n".join(f"  - {path}" for path in gitlinks)
            raise ConfigError(
                "Git submodules are not supported in an Overleaf sync repository:\n"
                f"{formatted}"
            )
