"""High-level sync transaction engine."""

from __future__ import annotations

import tempfile
from collections.abc import Iterable
from pathlib import Path

from ol_ce_sync import git_ops
from ol_ce_sync.backends import create_backend
from ol_ce_sync.backends.base import OverleafBackend
from ol_ce_sync.config import (
    Config,
    default_config,
    ensure_default_gitignore,
    load_config,
    write_default_config,
)
from ol_ce_sync.diff import PushOperation, build_push_plan, format_push_plan
from ol_ce_sync.errors import (
    BackendError,
    ConfigError,
    DirtyWorktreeError,
    SyncConflictError,
    UnsupportedBackendOperation,
    VerificationError,
)
from ol_ce_sync.lock import SyncLock
from ol_ce_sync.snapshot import collect_tree, compare_trees
from ol_ce_sync.utils.logging import info, warn


class SyncEngine:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root

    def init(
        self,
        *,
        project_id: str,
        host: str = "http://localhost",
        project_name: str = "overleaf-project",
        backend_type: str = "http",
        force: bool = False,
    ) -> None:
        if not force and not git_ops.is_git_repo(self.repo_root) and self._has_non_sync_content():
            raise ConfigError("Refusing to initialize a non-empty directory without --force.")

        config = default_config(
            self.repo_root,
            host=host,
            project_id=project_id,
            project_name=project_name,
            backend_type=backend_type,
        )
        write_default_config(config, force=force)
        ensure_default_gitignore(self.repo_root)
        backend = create_backend(config)
        backend.authenticate()

        git_ops.ensure_git_repo(self.repo_root, config.git.main_branch)
        with SyncLock(config.resolve_repo_path(config.sync.lock_file)):
            snapshot_dir = self._download_snapshot(config, backend)
            info(f"Importing initial remote snapshot into {config.git.remote_branch}...")
            remote_commit = git_ops.import_snapshot_to_branch(
                self.repo_root,
                snapshot_dir,
                branch=config.git.remote_branch,
                patterns=config.ignore.patterns,
                message="overleaf: initial snapshot",
            )
            self._ensure_on_branch(config.git.main_branch)
            info(f"Merging {config.git.remote_branch} into {config.git.main_branch}...")
            git_ops.merge_branch(self.repo_root, config.git.remote_branch)
            ensure_default_gitignore(self.repo_root)
            head = git_ops.head_commit(self.repo_root)
            self._write_metadata(config.sync.last_synced_file, head)
            self._write_metadata(config.sync.last_remote_snapshot_file, remote_commit)
            info(f"Initialized sync metadata at {config.config_path}.")

    def pull(self, *, wait: bool = False) -> None:
        config = load_config(self.repo_root)
        backend = create_backend(config)
        with SyncLock(config.resolve_repo_path(config.sync.lock_file), wait=wait):
            self._pull_no_lock(config, backend)

    def push(self, *, dry_run: bool | None = None, wait: bool = False) -> None:
        config = load_config(self.repo_root)
        backend = create_backend(config)
        dry_run = config.sync.dry_run_default if dry_run is None else dry_run
        with SyncLock(config.resolve_repo_path(config.sync.lock_file), wait=wait):
            if git_ops.has_unresolved_conflicts(self.repo_root):
                raise SyncConflictError(git_ops.conflicted_files(self.repo_root))
            if config.git.require_clean_worktree_before_push:
                git_ops.require_clean_worktree(self.repo_root)

            info("Running freshness pull before push...")
            self._pull_no_lock(config, backend)
            if git_ops.has_unresolved_conflicts(self.repo_root):
                raise SyncConflictError(git_ops.conflicted_files(self.repo_root))
            if git_ops.has_dirty_worktree(self.repo_root):
                raise DirtyWorktreeError(
                    "Freshness pull staged newer remote changes. "
                    "Review and commit them before push."
                )

            base = git_ops.head_commit(self.repo_root, config.git.remote_branch)
            plan = build_push_plan(self.repo_root, base, config.ignore.patterns)
            print(format_push_plan(plan))
            if dry_run:
                print("\nNo changes were applied because --dry-run was set.")
                return
            if not plan:
                info("No local changes to push.")
                self._write_metadata(
                    config.sync.last_synced_file,
                    git_ops.head_commit(self.repo_root),
                )
                return

            info("Applying push plan through backend...")
            self._apply_push_plan(config, backend, plan)
            verification_snapshot = self._download_snapshot(config, backend)
            self._verify_snapshot_matches_local(config, verification_snapshot)
            info("Verification succeeded.")

            remote_commit = git_ops.import_snapshot_to_branch(
                self.repo_root,
                verification_snapshot,
                branch=config.git.remote_branch,
                patterns=config.ignore.patterns,
                message="overleaf: snapshot after push",
            )
            self._ensure_on_branch(config.git.main_branch, fallback_to_current=True)
            self._write_metadata(config.sync.last_synced_file, git_ops.head_commit(self.repo_root))
            self._write_metadata(config.sync.last_remote_snapshot_file, remote_commit)

    def status(self) -> None:
        config = load_config(self.repo_root)
        print(f"Current branch: {git_ops.current_branch(self.repo_root)}")
        clean = "yes" if not git_ops.has_dirty_worktree(self.repo_root) else "no"
        print(f"Working tree clean: {clean}")
        last_synced = self._read_metadata(config.sync.last_synced_file) or "(none)"
        print(f"Last synced commit: {last_synced}")
        print(
            "Last remote snapshot commit: "
            f"{self._read_metadata(config.sync.last_remote_snapshot_file) or '(none)'}"
        )
        conflicts = git_ops.conflicted_files(self.repo_root)
        if conflicts:
            print("Pending conflicts:")
            for path in conflicts:
                print(f"  - {path}")
        else:
            print("Pending conflicts: none")

        base = self._status_base(config)
        if base:
            plan = build_push_plan(self.repo_root, base, config.ignore.patterns)
            print("Files changed locally since latest remote snapshot:")
            if plan:
                for op in plan:
                    print(f"  {op.display_status} {op.path}")
            else:
                print("  (none)")
        else:
            print("Files changed locally since latest remote snapshot: unknown")
        print("Remote freshness: not checked")

    def verify(self, *, allow_diff: bool = False) -> None:
        config = load_config(self.repo_root)
        backend = create_backend(config)
        snapshot_dir = self._download_snapshot(config, backend)
        diff = self._snapshot_diff_against_local(config, snapshot_dir)
        self._print_tree_diff(diff)
        if diff.has_changes and not allow_diff:
            raise VerificationError("Remote snapshot differs from local normalized project state.")

    def _pull_no_lock(
        self,
        config: Config,
        backend: OverleafBackend,
    ) -> None:
        backend.authenticate()
        git_ops.require_clean_worktree(self.repo_root)
        snapshot_dir = self._download_snapshot(config, backend)
        info(f"Importing remote snapshot into {config.git.remote_branch}...")
        remote_commit = git_ops.import_snapshot_to_branch(
            self.repo_root,
            snapshot_dir,
            branch=config.git.remote_branch,
            patterns=config.ignore.patterns,
            message="overleaf: snapshot",
        )
        current = git_ops.current_branch(self.repo_root)
        if current == config.git.remote_branch:
            self._ensure_on_branch(config.git.main_branch)
        info(
            f"Staging {config.git.remote_branch} into {git_ops.current_branch(self.repo_root)}..."
        )
        git_ops.merge_branch(self.repo_root, config.git.remote_branch, commit=False)
        self._write_metadata(config.sync.last_remote_snapshot_file, remote_commit)
        if not git_ops.has_dirty_worktree(self.repo_root):
            self._write_metadata(config.sync.last_synced_file, git_ops.head_commit(self.repo_root))
            info("Pull completed; local branch already matches the latest remote snapshot.")
            return
        info("Pull staged remote changes. Review them, then commit the merge result.")

    def _apply_push_plan(
        self,
        config: Config,
        backend: OverleafBackend,
        operations: Iterable[PushOperation],
    ) -> None:
        for op in operations:
            parent = Path(op.path).parent.as_posix()
            if parent and parent != ".":
                backend.create_folder(config.project.project_id, parent)
            if op.status == "A" or op.status == "M":
                self._write_file(config, backend, op.path, op.is_text)
            elif op.status == "D":
                backend.delete_path(config.project.project_id, op.path)
            elif op.status.startswith("R") and op.old_path:
                self._apply_rename(config, backend, op)
            else:
                raise BackendError(f"Unsupported push operation: {op}")

    def _apply_rename(self, config: Config, backend: OverleafBackend, op: PushOperation) -> None:
        assert op.old_path is not None
        try:
            backend.move_path(config.project.project_id, op.old_path, op.path)
            self._write_file(config, backend, op.path, op.is_text)
            return
        except UnsupportedBackendOperation:
            warn(
                "Backend cannot move paths atomically; "
                f"falling back for {op.old_path} -> {op.path}."
            )

        self._write_file(config, backend, op.path, op.is_text)
        snapshot_dir = self._download_snapshot(config, backend)
        uploaded_path = snapshot_dir / op.path
        uploaded = uploaded_path.read_bytes() if uploaded_path.exists() else None
        expected = (self.repo_root / op.path).read_bytes()
        if uploaded != expected:
            raise VerificationError(f"Rename fallback upload verification failed for {op.path}")
        backend.delete_path(config.project.project_id, op.old_path)

    def _write_file(
        self,
        config: Config,
        backend: OverleafBackend,
        path: str,
        is_text: bool,
    ) -> None:
        content = (self.repo_root / path).read_bytes()
        if is_text:
            backend.write_text_file(config.project.project_id, path, content.decode("utf-8"))
        else:
            backend.upload_binary_file(config.project.project_id, path, content)

    def _download_snapshot(self, config: Config, backend: OverleafBackend) -> Path:
        temp_dir = Path(tempfile.mkdtemp(prefix="ol-"))
        snapshot_dir = temp_dir / "snapshot"
        info(f"Downloading Overleaf snapshot for project {config.project.project_id}...")
        backend.download_project_snapshot(config.project.project_id, snapshot_dir)
        return snapshot_dir

    def _verify_snapshot_matches_local(self, config: Config, snapshot_dir: Path) -> None:
        diff = self._snapshot_diff_against_local(config, snapshot_dir)
        if diff.has_changes:
            self._print_tree_diff(diff)
            raise VerificationError("Remote verification failed; sync metadata was not updated.")

    def _snapshot_diff_against_local(self, config: Config, snapshot_dir: Path):
        expected = collect_tree(self.repo_root, config.ignore.patterns)
        actual = collect_tree(snapshot_dir, config.ignore.patterns)
        return compare_trees(expected, actual)

    def _print_tree_diff(self, diff) -> None:
        if not diff.has_changes:
            print("Remote snapshot matches local normalized project state.")
            return
        print("Remote snapshot differs from local normalized project state:")
        for path in diff.added:
            print(f"  remote added: {path}")
        for path in diff.modified:
            print(f"  modified: {path}")
        for path in diff.deleted:
            print(f"  remote missing: {path}")

    def _read_metadata(self, relative_path: str) -> str | None:
        path = self.repo_root / relative_path
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8").strip() or None

    def _write_metadata(self, relative_path: str, value: str) -> None:
        path = self.repo_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value + "\n", encoding="utf-8")

    def _has_local_changes_since_synced(self, config: Config) -> bool:
        last = self._read_metadata(config.sync.last_synced_file)
        if not last:
            return True
        return git_ops.has_diff_between(self.repo_root, last)

    def _status_base(self, config: Config) -> str | None:
        if git_ops.branch_exists(self.repo_root, config.git.remote_branch):
            return git_ops.head_commit(self.repo_root, config.git.remote_branch)
        return self._read_metadata(config.sync.last_synced_file)

    def _ensure_on_branch(self, branch: str, *, fallback_to_current: bool = False) -> None:
        if git_ops.current_branch(self.repo_root) == branch:
            return
        if git_ops.branch_exists(self.repo_root, branch):
            git_ops.switch_branch(self.repo_root, branch)
        elif not fallback_to_current:
            git_ops.run_git(self.repo_root, ["switch", "-c", branch])

    def _has_non_sync_content(self) -> bool:
        for child in self.repo_root.iterdir():
            if child.name in {".ol-sync"}:
                continue
            return True
        return False
