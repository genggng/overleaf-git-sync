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
    assert collect_tree(repo, [".git/", ".ol-sync/"]) == {}

    git_ops.merge_branch(repo, "overleaf-remote")

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


def test_diff_mapping_preserves_unicode_filename(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    write(repo / "main.tex", "base")
    commit_all(repo, "base")
    base = git_ops.head_commit(repo)

    write(repo / "测试.tex", "unicode")
    commit_all(repo, "add unicode path")

    plan = build_push_plan(repo, base, [".ol-sync/"])

    assert [(op.status, op.path) for op in plan] == [("A", "测试.tex")]


def test_diff_mapping_preserves_tabs_and_newlines_in_filename(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    git_ops.run_git(repo, ["commit", "--allow-empty", "-m", "base"], identity=True)
    base = git_ops.head_commit(repo)
    names = ["tab\tname.tex", "line\nbreak.tex"]
    for name in names:
        write(repo / name, "content")
    commit_all(repo, "add special paths")

    plan = build_push_plan(repo, base, [".ol-sync/"])

    assert {op.path for op in plan} == set(names)


def test_worktree_collection_keeps_tracked_file_that_is_gitignored(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    write(repo / "main.tex", "tracked")
    commit_all(repo, "track main")
    write(repo / ".gitignore", "main.tex\n")

    files = git_ops.worktree_sync_files(repo, [".ol-sync/"])

    assert files["main.tex"] == b"tracked"


def test_nested_directory_inside_parent_repo_is_not_treated_as_initialized_repo(
    tmp_path: Path,
) -> None:
    outer = tmp_path / "outer"
    outer.mkdir()
    init_repo(outer)
    nested = outer / "nested"
    nested.mkdir()

    assert not git_ops.is_git_repo(nested)

    git_ops.ensure_git_repo(nested, "main")

    assert git_ops.is_git_repo(nested)
    assert (nested / ".git").exists()


def test_import_snapshot_ignores_gitignored_top_level_paths_when_staging(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    write(snapshot / "main.tex", "hello")
    init_repo(repo)
    write(repo / ".gitignore", "build/\n*.log\n")
    commit_all(repo, "track gitignore")
    write(repo / "build" / "local.tmp", "keep")
    write(repo / "main.log", "keep")

    commit = git_ops.import_snapshot_to_branch(
        repo,
        snapshot,
        branch="overleaf-remote",
        patterns=[".ol-sync/", "build/", "*.log"],
        message="overleaf: snapshot",
    )

    assert commit
    assert (repo / "build" / "local.tmp").exists()
    assert (repo / "main.log").exists()


def test_import_snapshot_preserves_tracked_sync_ignored_parent_file(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    init_repo(repo)
    write(repo / "main.tex", "old remote")
    write(repo / ".gitignore", ".ol-sync/\n")
    commit_all(repo, "working baseline")
    git_ops.force_branch_to_ref(repo, "overleaf-remote", "HEAD")
    write(snapshot / "main.tex", "new remote")

    git_ops.import_snapshot_to_branch(
        repo,
        snapshot,
        branch="overleaf-remote",
        patterns=[".ol-sync/", ".gitignore"],
        message="overleaf: snapshot",
    )

    assert (
        git_ops.run_git(repo, ["show", "overleaf-remote:.gitignore"]).stdout
        == ".ol-sync/\n"
    )
    git_ops.merge_branch(repo, "overleaf-remote")
    assert (repo / ".gitignore").read_text(encoding="utf-8") == ".ol-sync/\n"
    assert (repo / "main.tex").read_text(encoding="utf-8") == "new remote"


def test_reports_managed_branch_checked_out_in_another_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    write(snapshot / "main.tex", "remote")
    init_repo(repo)
    git_ops.run_git(repo, ["commit", "--allow-empty", "-m", "base"], identity=True)
    git_ops.import_snapshot_to_branch(
        repo,
        snapshot,
        branch="overleaf-remote",
        patterns=[".ol-sync/"],
        message="remote snapshot",
    )
    worktree = tmp_path / "remote-worktree"
    git_ops.run_git(repo, ["worktree", "add", str(worktree), "overleaf-remote"])

    assert git_ops.worktrees_for_branch(repo, "overleaf-remote") == [str(worktree)]
