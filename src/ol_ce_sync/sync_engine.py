"""High-level sync transaction engine."""

from __future__ import annotations

import json
import tempfile
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from ol_ce_sync import git_ops
from ol_ce_sync.backends import create_backend
from ol_ce_sync.backends.base import OverleafBackend
from ol_ce_sync.config import (
    Config,
    default_config,
    effective_ignore_patterns,
    ensure_default_gitignore,
    load_config,
    write_default_config,
)
from ol_ce_sync.diff import PushOperation, build_push_plan, format_push_plan
from ol_ce_sync.errors import (
    BackendError,
    ConfigError,
    DirtyWorktreeError,
    PartialPushError,
    RecoveryRequiredError,
    RemoteChangedError,
    SyncConflictError,
    UnsupportedBackendOperation,
    VerificationError,
)
from ol_ce_sync.journal import PushJournal
from ol_ce_sync.lock import SyncLock
from ol_ce_sync.project_safety import validate_local_project_tree
from ol_ce_sync.snapshot import collect_tree_hashes, compare_trees, file_sha256
from ol_ce_sync.utils.files import atomic_write_text
from ol_ce_sync.utils.logging import info, warn


class SyncEngine:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self._temporary_directories: list[tempfile.TemporaryDirectory[str]] = []

    def close(self) -> None:
        while self._temporary_directories:
            self._temporary_directories.pop().cleanup()

    def init(
        self,
        *,
        project_id: str,
        host: str = "http://localhost",
        project_name: str = "overleaf-project",
        backend_type: str = "http",
        ssl_verify: bool = True,
        force: bool = False,
        overwrite_config: bool = True,
    ) -> None:
        is_git_repo = git_ops.is_git_repo(self.repo_root)
        has_existing_content = self._has_non_sync_content()
        if not force and not is_git_repo and has_existing_content:
            raise ConfigError(
                "Refusing to initialize a non-empty directory without --force. "
                "Run `ol init` in a dedicated local project directory."
            )

        config_path = self.repo_root / ".ol-sync" / "config.toml"
        previous_config = load_config(self.repo_root) if config_path.exists() else None
        requested_binding = (host.rstrip("/"), project_id)
        if previous_config is not None:
            previous_binding = (
                previous_config.project.host.rstrip("/"),
                previous_config.project.project_id,
            )
            if previous_binding != requested_binding and not force and overwrite_config:
                raise ConfigError(
                    "This repository is already bound to a different Overleaf project. "
                    "Re-run with --force only if you intend to replace that binding."
                )
        if config_path.exists() and not overwrite_config:
            config = load_config(self.repo_root)
            should_write_config = False
        else:
            config = default_config(
                self.repo_root,
                host=host,
                project_id=project_id,
                project_name=project_name,
                backend_type=backend_type,
                ssl_verify=ssl_verify,
            )
            should_write_config = True
        backend = create_backend(config)
        backend.authenticate()

        validate_local_project_tree(self.repo_root)
        if is_git_repo:
            git_ops.require_clean_worktree(self.repo_root)
        else:
            git_ops.ensure_git_repo(self.repo_root, config.git.main_branch)
            if has_existing_content:
                info("Creating a local safety commit before importing Overleaf...")
                git_ops.commit_all(
                    self.repo_root,
                    "local: pre-sync safety snapshot",
                    allow_empty=False,
                    patterns=[".ol-sync/"],
                )
        with SyncLock(config.resolve_repo_path(config.sync.lock_file)):
            self._ensure_remote_branch_owned(
                config,
                allow_claim=previous_config is not None,
                allow_rebind_from=previous_config if force else None,
            )
            patterns = self._ignore_patterns(config)
            snapshot_dir = self._download_snapshot(config, backend)
            if should_write_config:
                write_default_config(config, force=True)
            info(f"Importing initial remote snapshot into {config.git.remote_branch}...")
            remote_commit = git_ops.import_snapshot_to_branch(
                self.repo_root,
                snapshot_dir,
                branch=config.git.remote_branch,
                patterns=patterns,
                message="overleaf: initial snapshot",
            )
            self._write_remote_branch_owner(config)
            if not git_ops.branch_exists(self.repo_root, config.git.main_branch):
                git_ops.initialize_unborn_branch_from_ref(
                    self.repo_root,
                    config.git.remote_branch,
                )
            self._ensure_on_branch(config.git.main_branch)
            info(f"Merging {config.git.remote_branch} into {config.git.main_branch}...")
            git_ops.merge_branch(
                self.repo_root,
                config.git.remote_branch,
                allow_unrelated_histories=is_git_repo or has_existing_content,
            )
            ensure_default_gitignore(self.repo_root)
            head = git_ops.head_commit(self.repo_root)
            self._write_metadata(config.sync.last_synced_file, head)
            self._write_metadata(config.sync.last_remote_snapshot_file, remote_commit)
            info(f"Initialized sync metadata at {config.config_path}.")

    def pull(self, *, wait: bool = False) -> None:
        config = load_config(self.repo_root)
        validate_local_project_tree(self.repo_root)
        backend = create_backend(config)
        with SyncLock(config.resolve_repo_path(config.sync.lock_file), wait=wait):
            if self._push_journal().exists():
                raise RecoveryRequiredError(
                    "An interrupted push must be recovered first. Run `ol recover`."
                )
            self._ensure_remote_branch_owned(config, allow_claim=True)
            self._pull_no_lock(config, backend)

    def recover(self, *, wait: bool = False) -> None:
        config = load_config(self.repo_root)
        validate_local_project_tree(self.repo_root)
        journal = self._push_journal()
        data = journal.load()
        if data.project_id != config.project.project_id:
            raise ConfigError("Push journal belongs to a different Overleaf project.")
        backend = create_backend(config)
        with SyncLock(config.resolve_repo_path(config.sync.lock_file), wait=wait):
            self._ensure_remote_branch_owned(config, allow_claim=True)
            try:
                self._pull_no_lock(config, backend)
            except SyncConflictError:
                journal.clear()
                raise
            journal.clear()
            info(
                "Interrupted push state was imported through Git. "
                "Review and commit any staged changes before pushing again."
            )

    def push(self, *, dry_run: bool | None = None, fast: bool = False, wait: bool = False) -> None:
        config = load_config(self.repo_root)
        validate_local_project_tree(self.repo_root)
        backend = create_backend(config)
        dry_run = config.sync.dry_run_default if dry_run is None else dry_run
        with SyncLock(config.resolve_repo_path(config.sync.lock_file), wait=wait):
            if self._push_journal().exists():
                raise RecoveryRequiredError(
                    "An interrupted push must be recovered first. Run `ol recover`."
                )
            self._ensure_remote_branch_owned(config, allow_claim=True)
            if git_ops.has_unresolved_conflicts(self.repo_root):
                raise SyncConflictError(git_ops.conflicted_files(self.repo_root))
            if config.git.require_clean_worktree_before_push:
                git_ops.require_clean_worktree(self.repo_root)

            if dry_run:
                base = git_ops.head_commit(self.repo_root, config.git.remote_branch)
                plan = build_push_plan(self.repo_root, base, self._ignore_patterns(config))
                self._validate_push_plan(plan)
                print(format_push_plan(plan))
                print("\nDry-run used the saved remote snapshot; remote freshness was not checked.")
                return

            freshness_snapshot: Path | None = None
            if fast:
                info("Skipping freshness pull because --fast was set.")
            else:
                info("Running freshness pull before push...")
                freshness_snapshot = self._pull_no_lock(config, backend)
                if git_ops.has_unresolved_conflicts(self.repo_root):
                    raise SyncConflictError(git_ops.conflicted_files(self.repo_root))
                if git_ops.has_dirty_worktree(self.repo_root):
                    raise DirtyWorktreeError(
                        "Freshness pull staged newer remote changes. "
                        "Review and commit them before push."
                    )

            base = git_ops.head_commit(self.repo_root, config.git.remote_branch)
            plan = build_push_plan(self.repo_root, base, self._ignore_patterns(config))
            self._validate_push_plan(plan)
            print(format_push_plan(plan))
            if not plan:
                info("No local changes to push.")
                self._write_metadata(
                    config.sync.last_synced_file,
                    git_ops.head_commit(self.repo_root),
                )
                return

            patterns = self._ignore_patterns(config)
            if freshness_snapshot is not None:
                expected_remote = collect_tree_hashes(freshness_snapshot, patterns)
            else:
                expected_remote = git_ops.ref_tree_hashes(
                    self.repo_root,
                    config.git.remote_branch,
                    patterns,
                )

            journal = self._push_journal()
            journal_data = journal.start(
                project_id=config.project.project_id,
                base_commit=base,
                operations=plan,
            )
            try:
                info("Applying push plan through backend...")
                previous_remote: dict[str, str] | None = None
                for index, operation in enumerate(plan):
                    self._assert_remote_tree_unchanged(
                        config,
                        backend,
                        expected_remote,
                        previous=previous_remote,
                    )
                    journal.before_operation(journal_data, index)
                    self._apply_push_plan(config, backend, [operation])
                    journal.after_operation(journal_data, index)
                    previous_remote = expected_remote
                    expected_remote = self._expected_tree_after_operation(
                        expected_remote,
                        operation,
                    )

                verification_snapshot = self._download_snapshot(config, backend)
                self._verify_snapshot_matches_local(
                    config,
                    backend,
                    verification_snapshot,
                    plan=plan,
                )
                info("Verification succeeded.")

                git_ops.import_snapshot_to_branch(
                    self.repo_root,
                    verification_snapshot,
                    branch=config.git.remote_branch,
                    patterns=patterns,
                    message="overleaf: snapshot after push",
                )
                synced_commit = git_ops.head_commit(self.repo_root)
                git_ops.force_branch_to_ref(
                    self.repo_root,
                    config.git.remote_branch,
                    synced_commit,
                )
                self._write_metadata(config.sync.last_synced_file, synced_commit)
                self._write_metadata(config.sync.last_remote_snapshot_file, synced_commit)
            except RemoteChangedError as exc:
                if not journal_data.completed_indices and journal_data.active_index is None:
                    journal.clear()
                    raise
                raise PartialPushError(
                    "Overleaf changed during a multi-file push. Remote state may be partial. "
                    "Run `ol recover` before retrying."
                ) from exc
            except Exception as exc:
                if not journal_data.completed_indices and journal_data.active_index is None:
                    journal.clear()
                    raise
                raise PartialPushError(
                    "Push stopped after remote mutation may have started. "
                    "Run `ol recover` before retrying."
                ) from exc
            else:
                journal.clear()

    def status(self) -> None:
        data = self.status_data()
        print(f"Current branch: {data['branch']}")
        print(f"Working tree clean: {'yes' if data['working_tree_clean'] else 'no'}")
        print(f"Last synced commit: {data['last_synced_commit'] or '(none)'}")
        print(f"Last synced at: {data['last_synced_at'] or '(never)'}")
        print(
            "Last remote snapshot commit: "
            f"{data['last_remote_snapshot_commit'] or '(none)'}"
        )
        conflicts = data["conflicts"]
        if conflicts:
            print("Pending conflicts:")
            for path in conflicts:
                print(f"  - {path}")
        else:
            print("Pending conflicts: none")
        if data["interrupted_push"]:
            print("Interrupted push: yes (run `ol recover`)")
        else:
            print("Interrupted push: none")

        if data["changes_known"]:
            print("Files changed locally since latest remote snapshot:")
            changes = data["changes"]
            if changes:
                for operation in changes:
                    if operation["old_path"]:
                        print(
                            f"  {operation['display_status']} "
                            f"{operation['old_path']} -> {operation['path']}"
                        )
                    else:
                        print(f"  {operation['display_status']} {operation['path']}")
            else:
                print("  (none)")
        else:
            print("Files changed locally since latest remote snapshot: unknown")
        print("Remote freshness: not checked")

    def status_data(self) -> dict[str, object]:
        config = load_config(self.repo_root)
        conflicts = git_ops.conflicted_files(self.repo_root)
        working_tree_changes = git_ops.worktree_changes(self.repo_root)
        base = self._status_base(config)
        changes = []
        if base:
            plan = build_push_plan(self.repo_root, base, self._ignore_patterns(config))
            changes = [
                {
                    "status": operation.status,
                    "display_status": operation.display_status,
                    "path": operation.path,
                    "old_path": operation.old_path,
                    "is_text": operation.is_text,
                }
                for operation in plan
            ]
        last_synced_commit = self._read_metadata(config.sync.last_synced_file)
        return {
            "schema_version": 1,
            "project": {
                "name": config.project.project_name,
                "project_id": config.project.project_id,
                "host": config.project.host,
            },
            "repo_root": str(self.repo_root.resolve()),
            "branch": git_ops.current_branch(self.repo_root),
            "working_tree_clean": not working_tree_changes,
            "working_tree_changes": working_tree_changes,
            "conflicts": conflicts,
            "interrupted_push": self._push_journal().exists(),
            "last_synced_commit": last_synced_commit,
            "last_synced_at": self._metadata_modified_at(config.sync.last_synced_file)
            if last_synced_commit
            else None,
            "last_remote_snapshot_commit": self._read_metadata(
                config.sync.last_remote_snapshot_file
            ),
            "changes_known": base is not None,
            "changes": changes,
            "remote_freshness": "not_checked",
        }

    def verify(self, *, allow_diff: bool = False, wait: bool = False) -> None:
        config = load_config(self.repo_root)
        validate_local_project_tree(self.repo_root)
        backend = create_backend(config)
        with SyncLock(config.resolve_repo_path(config.sync.lock_file), wait=wait):
            snapshot_dir = self._download_snapshot(config, backend)
            diff = self._snapshot_diff_against_local(config, snapshot_dir)
            self._print_tree_diff(diff)
            if diff.has_changes and not allow_diff:
                raise VerificationError(
                    "Remote snapshot differs from local normalized project state."
                )

    def _pull_no_lock(
        self,
        config: Config,
        backend: OverleafBackend,
    ) -> Path:
        backend.authenticate()
        git_ops.require_clean_worktree(self.repo_root)
        patterns = self._ignore_patterns(config)
        snapshot_dir = self._download_snapshot(config, backend)
        info(f"Importing remote snapshot into {config.git.remote_branch}...")
        remote_commit = git_ops.import_snapshot_to_branch(
            self.repo_root,
            snapshot_dir,
            branch=config.git.remote_branch,
            patterns=patterns,
            message="overleaf: snapshot",
        )
        if not git_ops.has_diff_between(
            self.repo_root,
            config.git.remote_branch,
            git_ops.current_branch(self.repo_root),
        ):
            self._write_metadata(config.sync.last_remote_snapshot_file, remote_commit)
            self._write_metadata(config.sync.last_synced_file, git_ops.head_commit(self.repo_root))
            info("Pull completed; local branch already matches the latest remote snapshot.")
            return snapshot_dir
        info(
            f"Staging {config.git.remote_branch} into {git_ops.current_branch(self.repo_root)}..."
        )
        git_ops.merge_branch(self.repo_root, config.git.remote_branch, commit=False)
        self._write_metadata(config.sync.last_remote_snapshot_file, remote_commit)
        if not git_ops.has_dirty_worktree(self.repo_root):
            if git_ops.has_merge_in_progress(self.repo_root):
                git_ops.quit_merge(self.repo_root)
            info(
                "Pull completed; the remote snapshot has no new content. "
                "Local committed changes remain ahead of Overleaf."
            )
            return snapshot_dir
        info("Pull staged remote changes. Review them, then commit the merge result.")
        return snapshot_dir

    def _apply_push_plan(
        self,
        config: Config,
        backend: OverleafBackend,
        operations: Iterable[PushOperation],
    ) -> None:
        for op in operations:
            parent = Path(op.path).parent.as_posix()
            if op.status != "D" and parent and parent != ".":
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

    def _validate_push_plan(self, operations: Iterable[PushOperation]) -> None:
        for op in operations:
            if op.status == "D":
                continue
            path = self.repo_root / op.path
            if path.is_symlink():
                raise ConfigError(f"Refusing to upload symbolic link: {op.path}")
            if not path.is_file():
                raise ConfigError(f"Push source is not a regular file: {op.path}")
            if op.is_text:
                try:
                    path.read_bytes().decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ConfigError(
                        f"Text file is not valid UTF-8: {op.path}. "
                        "Convert it to UTF-8 before pushing."
                    ) from exc

    def _assert_remote_tree_unchanged(
        self,
        config: Config,
        backend: OverleafBackend,
        expected: dict[str, str],
        *,
        previous: dict[str, str] | None = None,
    ) -> None:
        delay = 0.25
        actual: dict[str, str] = {}
        for attempt in range(4):
            snapshot = self._download_snapshot(config, backend)
            actual = collect_tree_hashes(snapshot, self._ignore_patterns(config))
            diff = compare_trees(expected, actual)
            if not diff.has_changes:
                return
            if previous is None or actual != previous or attempt == 3:
                break
            time.sleep(delay)
            delay *= 2
        changed = sorted({*diff.added, *diff.modified, *diff.deleted})
        details = "\n".join(f"  - {path}" for path in changed)
        raise RemoteChangedError(
            "Overleaf changed after the push baseline was captured. "
            "No next operation was applied.\nChanged paths:\n"
            f"{details}"
        )

    def _expected_tree_after_operation(
        self,
        tree: dict[str, str],
        operation: PushOperation,
    ) -> dict[str, str]:
        updated = dict(tree)
        if operation.status == "D":
            updated.pop(operation.path, None)
            return updated
        if operation.old_path is not None:
            updated.pop(operation.old_path, None)
        updated[operation.path] = file_sha256(self.repo_root / operation.path)
        return updated

    def _push_journal(self) -> PushJournal:
        return PushJournal(self.repo_root / ".ol-sync" / "push_journal.json")

    def _download_snapshot(self, config: Config, backend: OverleafBackend) -> Path:
        snapshot_root = config.resolve_repo_path(config.sync.snapshot_dir)
        snapshot_root.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="ol-", dir=snapshot_root)
        self._temporary_directories.append(temporary)
        temp_dir = Path(temporary.name)
        snapshot_dir = temp_dir / "snapshot"
        info(f"Downloading Overleaf snapshot for project {config.project.project_id}...")
        backend.download_project_snapshot(config.project.project_id, snapshot_dir)
        return snapshot_dir

    def _verify_snapshot_matches_local(
        self,
        config: Config,
        backend: OverleafBackend,
        snapshot_dir: Path,
        *,
        plan: Iterable[PushOperation] | None = None,
    ) -> None:
        push_plan = tuple(plan or ())
        retries = 3 if push_plan else 0
        delay = 0.5
        for attempt in range(retries + 1):
            diff = self._snapshot_diff_against_local(config, snapshot_dir)
            if not diff.has_changes:
                return
            if attempt < retries and self._can_retry_verification(diff, push_plan):
                time.sleep(delay)
                delay *= 2
                snapshot_dir = self._download_snapshot(config, backend)
                continue
            self._print_tree_diff(diff)
            raise VerificationError("Remote verification failed; sync metadata was not updated.")

    def _can_retry_verification(self, diff, plan: tuple[PushOperation, ...]) -> bool:
        deleted_paths = {op.path for op in plan if op.status == "D"}
        if not deleted_paths:
            return False
        if diff.modified or diff.deleted:
            return False
        if not diff.added:
            return False
        return set(diff.added).issubset(deleted_paths)

    def _snapshot_diff_against_local(self, config: Config, snapshot_dir: Path):
        patterns = self._ignore_patterns(config)
        expected = git_ops.worktree_sync_hashes(self.repo_root, patterns)
        actual = collect_tree_hashes(snapshot_dir, patterns)
        return compare_trees(expected, actual)

    def _ignore_patterns(self, config: Config) -> list[str]:
        return effective_ignore_patterns(config)

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
        atomic_write_text(path, value + "\n", mode=0o600)

    def _metadata_modified_at(self, relative_path: str) -> str | None:
        path = self.repo_root / relative_path
        try:
            modified_at = path.stat().st_mtime
        except FileNotFoundError:
            return None
        return datetime.fromtimestamp(modified_at, tz=UTC).isoformat()

    def _remote_branch_owner_path(self) -> Path:
        return self.repo_root / ".ol-sync" / "remote_branch_owner.json"

    def _ensure_remote_branch_owned(
        self,
        config: Config,
        *,
        allow_claim: bool,
        allow_rebind_from: Config | None = None,
    ) -> None:
        if not git_ops.branch_exists(self.repo_root, config.git.remote_branch):
            return
        checked_out = git_ops.worktrees_for_branch(self.repo_root, config.git.remote_branch)
        if checked_out:
            locations = "\n".join(f"  - {path}" for path in checked_out)
            raise ConfigError(
                f"Managed branch {config.git.remote_branch!r} is checked out in a worktree:\n"
                f"{locations}\nSwitch that worktree to another branch before syncing."
            )
        path = self._remote_branch_owner_path()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ConfigError(f"Invalid remote branch ownership marker: {path}") from exc
            expected = {
                "host": config.project.host.rstrip("/"),
                "project_id": config.project.project_id,
                "branch": config.git.remote_branch,
            }
            if data != expected:
                previous = None
                if allow_rebind_from is not None:
                    previous = {
                        "host": allow_rebind_from.project.host.rstrip("/"),
                        "project_id": allow_rebind_from.project.project_id,
                        "branch": allow_rebind_from.git.remote_branch,
                    }
                if previous is not None and data == previous:
                    return
                raise ConfigError(
                    f"Remote branch {config.git.remote_branch!r} belongs to a different "
                    "Overleaf project."
                )
            return
        if allow_claim and self._read_metadata(config.sync.last_remote_snapshot_file):
            self._write_remote_branch_owner(config)
            return
        raise ConfigError(
            f"Refusing to reuse existing unowned branch {config.git.remote_branch!r}. "
            "Rename the branch or configure a different remote_branch."
        )

    def _write_remote_branch_owner(self, config: Config) -> None:
        payload = {
            "host": config.project.host.rstrip("/"),
            "project_id": config.project.project_id,
            "branch": config.git.remote_branch,
        }
        atomic_write_text(
            self._remote_branch_owner_path(),
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            mode=0o600,
        )

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
            if child.name in {".git", ".ol-sync", ".gitignore"}:
                continue
            return True
        return False
