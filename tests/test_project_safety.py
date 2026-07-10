from __future__ import annotations

from pathlib import Path

import pytest

from ol_ce_sync import git_ops
from ol_ce_sync.errors import ConfigError
from ol_ce_sync.project_safety import validate_local_project_tree


def test_rejects_symbolic_link(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "secret.tex").symlink_to(outside)

    with pytest.raises(ConfigError, match="symbolic link"):
        validate_local_project_tree(repo)


def test_rejects_nested_git_repository(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    nested_git = repo / "vendor" / ".git"
    nested_git.mkdir(parents=True)

    with pytest.raises(ConfigError, match="nested Git"):
        validate_local_project_tree(repo)


def test_rejects_gitlink_submodule(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git_ops.ensure_git_repo(repo, "main")
    git_ops.run_git(repo, ["commit", "--allow-empty", "-m", "base"], identity=True)
    commit = git_ops.head_commit(repo)
    git_ops.run_git(
        repo,
        ["update-index", "--add", "--cacheinfo", "160000", commit, "vendor"],
    )

    with pytest.raises(ConfigError, match="submodules"):
        validate_local_project_tree(repo)
