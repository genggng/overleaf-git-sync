from __future__ import annotations

from pathlib import Path

import pytest

from ol_ce_sync import git_ops
from ol_ce_sync.backends.base import ProjectTree
from ol_ce_sync.config import default_config, write_default_config
from ol_ce_sync.errors import DirtyWorktreeError
from ol_ce_sync.sync_engine import SyncEngine
from tests.conftest import write


class FakeBackend:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    def authenticate(self) -> None:
        return

    def download_project_snapshot(self, project_id: str, dest_dir: Path) -> None:
        dest_dir.mkdir(parents=True, exist_ok=True)
        for rel_path, content in self.files.items():
            path = dest_dir / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

    def list_project_tree(self, project_id: str) -> ProjectTree:
        return ProjectTree(entries=())

    def write_text_file(self, project_id: str, path: str, content: str) -> None:
        raise AssertionError("unexpected write_text_file call")

    def upload_binary_file(self, project_id: str, path: str, content: bytes) -> None:
        raise AssertionError("unexpected upload_binary_file call")

    def create_folder(self, project_id: str, path: str) -> None:
        raise AssertionError("unexpected create_folder call")

    def delete_path(self, project_id: str, path: str) -> None:
        raise AssertionError("unexpected delete_path call")

    def move_path(self, project_id: str, old_path: str, new_path: str) -> None:
        raise AssertionError("unexpected move_path call")


def write_config(repo: Path) -> None:
    write_default_config(default_config(repo, project_id="project123"))


def prepare_synced_repo(repo: Path) -> str:
    git_ops.ensure_git_repo(repo, "main")
    write_config(repo)
    snapshot = repo.parent / f"{repo.name}-initial-snapshot"
    snapshot.mkdir(parents=True, exist_ok=True)
    write(snapshot / "main.tex", "base\n")
    remote_commit = git_ops.import_snapshot_to_branch(
        repo,
        snapshot,
        branch="overleaf-remote",
        patterns=[".ol-sync/"],
        message="overleaf: initial snapshot",
    )
    git_ops.merge_branch(repo, "overleaf-remote")
    head = git_ops.head_commit(repo)
    metadata_dir = repo / ".ol-sync"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    (metadata_dir / "last_synced_commit").write_text(head + "\n", encoding="utf-8")
    (metadata_dir / "last_remote_snapshot_commit").write_text(
        remote_commit + "\n", encoding="utf-8"
    )
    return head


def test_pull_requires_clean_worktree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    prepare_synced_repo(repo)
    write(repo / "notes.tex", "dirty\n")
    monkeypatch.setattr("ol_ce_sync.sync_engine.create_backend", lambda config: FakeBackend({}))

    with pytest.raises(DirtyWorktreeError):
        SyncEngine(repo).pull()


def test_pull_stages_remote_changes_instead_of_committing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    previous_head = prepare_synced_repo(repo)
    metadata_path = repo / ".ol-sync" / "last_synced_commit"
    monkeypatch.setattr(
        "ol_ce_sync.sync_engine.create_backend",
        lambda config: FakeBackend({"main.tex": b"remote\n"}),
    )

    SyncEngine(repo).pull()

    assert git_ops.head_commit(repo) == previous_head
    assert git_ops.has_staged_changes(repo)
    assert (repo / "main.tex").read_text(encoding="utf-8") == "remote\n"
    assert (repo / ".git" / "MERGE_HEAD").exists()
    assert metadata_path.read_text(encoding="utf-8").strip() == previous_head


def test_init_appends_default_gitignore_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git_ops.ensure_git_repo(repo, "main")
    (repo / ".gitignore").write_text("custom.log\n", encoding="utf-8")
    monkeypatch.setattr(
        "ol_ce_sync.sync_engine.create_backend",
        lambda config: FakeBackend({"main.tex": b"hello\n"}),
    )

    SyncEngine(repo).init(project_id="project123")

    gitignore = (repo / ".gitignore").read_text(encoding="utf-8")
    assert "custom.log" in gitignore
    assert ".ol-sync/" in gitignore
    assert "*.aux" in gitignore
    assert "*.run.xml" in gitignore
