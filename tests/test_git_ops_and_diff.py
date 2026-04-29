from __future__ import annotations

from pathlib import Path

import pytest

from ol_ce_sync import git_ops
from ol_ce_sync.diff import build_push_plan
from ol_ce_sync.errors import SyncConflictError
from ol_ce_sync.snapshot import collect_tree
from tests.conftest import commit_all, write


def init_repo(repo: Path) -> None:
    git_ops.ensure_git_repo(repo, "main")


def test_import_snapshot_creates_remote_branch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    write(snapshot / "main.tex", "hello")
    init_repo(repo)

    commit = git_ops.import_snapshot_to_branch(
        repo,
        snapshot,
        branch="overleaf-remote",
        patterns=[".ol-sync/"],
        message="overleaf: snapshot",
    )

    assert commit
    assert git_ops.branch_exists(repo, "overleaf-remote")
    assert collect_tree(repo, [".git/", ".ol-sync/"]) == {"main.tex": b"hello"}


def test_import_empty_snapshot_creates_empty_commit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    init_repo(repo)

    commit = git_ops.import_snapshot_to_branch(
        repo,
        snapshot,
        branch="overleaf-remote",
        patterns=[".ol-sync/"],
        message="overleaf: empty snapshot",
    )

    assert commit
    assert git_ops.branch_exists(repo, "overleaf-remote")


def test_merge_conflict_is_detected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    write(snapshot / "main.tex", "base\n")
    init_repo(repo)
    git_ops.import_snapshot_to_branch(
        repo,
        snapshot,
        branch="overleaf-remote",
        patterns=[".ol-sync/"],
        message="overleaf: snapshot",
    )
    git_ops.merge_branch(repo, "overleaf-remote")
    write(repo / "main.tex", "local\n")
    commit_all(repo, "local change")

    write(snapshot / "main.tex", "remote\n")
    git_ops.import_snapshot_to_branch(
        repo,
        snapshot,
        branch="overleaf-remote",
        patterns=[".ol-sync/"],
        message="overleaf: snapshot 2",
    )

    with pytest.raises(SyncConflictError) as exc:
        git_ops.merge_branch(repo, "overleaf-remote")
    assert exc.value.files == ["main.tex"]


def test_diff_mapping_handles_add_modify_delete_rename(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    write(repo / "modify.tex", "old")
    write(repo / "delete.tex", "delete")
    write(repo / "old.tex", "rename")
    commit_all(repo, "base")
    base = git_ops.head_commit(repo)

    write(repo / "modify.tex", "new")
    (repo / "delete.tex").unlink()
    write(repo / "add.tex", "add")
    (repo / "old.tex").rename(repo / "new.tex")
    commit_all(repo, "local changes")

    plan = build_push_plan(repo, base, [".ol-sync/"])
    simplified = {(op.display_status, op.old_path, op.path) for op in plan}

    assert ("A", None, "add.tex") in simplified
    assert ("M", None, "modify.tex") in simplified
    assert ("D", None, "delete.tex") in simplified
    assert ("R", "old.tex", "new.tex") in simplified
