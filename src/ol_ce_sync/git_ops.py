"""Transparent Git subprocess wrapper."""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from ol_ce_sync.errors import DirtyWorktreeError, GitError, SyncConflictError
from ol_ce_sync.snapshot import collect_tree, file_sha256, is_ignored

GIT_IDENTITY = ["-c", "user.name=ol", "-c", "user.email=ol@example.invalid"]
SYNC_EXCLUDES = ["--", ".", ":(exclude).ol-sync"]


@dataclass(frozen=True)
class GitResult:
    stdout: str
    stderr: str
    returncode: int


def run_git(
    repo: Path,
    args: list[str],
    *,
    check: bool = True,
    identity: bool = False,
    env: dict[str, str] | None = None,
) -> GitResult:
    cmd = ["git", *GIT_IDENTITY, *args] if identity else ["git", *args]
    process_env = os.environ.copy()
    if env:
        process_env.update(env)
    result = subprocess.run(
        cmd,
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
        env=process_env,
    )
    if check and result.returncode != 0:
        raise GitError(
            f"Git command failed: {' '.join(cmd)}\n{result.stderr.strip() or result.stdout.strip()}"
        )
    return GitResult(result.stdout, result.stderr, result.returncode)


def is_git_repo(repo: Path) -> bool:
    result = run_git(repo, ["rev-parse", "--show-toplevel"], check=False)
    if result.returncode != 0:
        return False
    try:
        top_level = Path(result.stdout.strip()).resolve()
    except OSError:
        return False
    return top_level == repo.resolve()


def ensure_git_repo(repo: Path, main_branch: str) -> None:
    if not is_git_repo(repo):
        result = run_git(repo, ["init", "-b", main_branch], check=False)
        if result.returncode != 0:
            run_git(repo, ["init"])
            run_git(repo, ["switch", "-c", main_branch])
    elif current_branch(repo) != main_branch and not branch_exists(repo, main_branch):
        run_git(repo, ["switch", "-c", main_branch])


def current_branch(repo: Path) -> str:
    result = run_git(repo, ["branch", "--show-current"])
    branch = result.stdout.strip()
    if not branch:
        raise GitError("Detached HEAD is not supported for sync operations")
    return branch


def branch_exists(repo: Path, branch: str) -> bool:
    result = run_git(repo, ["show-ref", "--verify", f"refs/heads/{branch}"], check=False)
    return result.returncode == 0


def switch_branch(repo: Path, branch: str) -> None:
    run_git(repo, ["switch", branch])


def force_branch_to_ref(repo: Path, branch: str, ref: str = "HEAD") -> None:
    run_git(repo, ["branch", "-f", branch, ref])


def has_dirty_worktree(repo: Path) -> bool:
    result = run_git(repo, ["status", "--porcelain", "--untracked-files=all", *SYNC_EXCLUDES])
    return bool(result.stdout.strip())


def require_clean_worktree(repo: Path) -> None:
    if has_dirty_worktree(repo):
        raise DirtyWorktreeError(
            "Working tree has uncommitted changes. Commit or stash them, then retry."
        )


def has_unresolved_conflicts(repo: Path) -> bool:
    return bool(conflicted_files(repo))


def conflicted_files(repo: Path) -> list[str]:
    result = run_git(repo, ["diff", "--name-only", "--diff-filter=U"], check=False)
    return [line for line in result.stdout.splitlines() if line.strip()]


def has_merge_in_progress(repo: Path) -> bool:
    result = run_git(repo, ["rev-parse", "--git-path", "MERGE_HEAD"], check=False)
    if result.returncode != 0:
        return False
    path = Path(result.stdout.strip())
    if not path.is_absolute():
        path = repo / path
    return path.exists()


def quit_merge(repo: Path) -> None:
    run_git(repo, ["merge", "--quit"], check=False)


def head_commit(repo: Path, ref: str = "HEAD") -> str:
    return run_git(repo, ["rev-parse", ref]).stdout.strip()


def has_staged_changes(repo: Path) -> bool:
    return run_git(repo, ["diff", "--cached", "--quiet"], check=False).returncode == 1


def has_diff_between(repo: Path, base: str, head: str = "HEAD") -> bool:
    result = run_git(repo, ["diff", "--quiet", base, head, *SYNC_EXCLUDES], check=False)
    return result.returncode == 1


def is_ancestor(repo: Path, ancestor: str, descendant: str = "HEAD") -> bool:
    result = run_git(repo, ["merge-base", "--is-ancestor", ancestor, descendant], check=False)
    return result.returncode == 0


def sync_pathspecs(repo: Path, patterns: list[str] | None = None) -> list[str]:
    ignore_patterns = patterns or []
    entries = {
        child.name
        for child in repo.iterdir()
        if child.name not in {".git", ".ol-sync"}
        and not is_ignored(child.name + ("/" if child.is_dir() else ""), ignore_patterns)
    }
    tracked = run_git(repo, ["ls-files", "-z"], check=False).stdout.split("\0")
    for path in tracked:
        if not path:
            continue
        top = path.split("/", 1)[0]
        if is_ignored(top, ignore_patterns) or is_ignored(top + "/", ignore_patterns):
            continue
        entries.add(top)
    return sorted(entries)


def commit_all(
    repo: Path,
    message: str,
    *,
    allow_empty: bool = False,
    patterns: list[str] | None = None,
) -> str | None:
    pathspecs = sync_pathspecs(repo, patterns)
    if pathspecs:
        run_git(repo, ["add", "-A", "--", *pathspecs])
    if not has_staged_changes(repo):
        if allow_empty:
            run_git(repo, ["commit", "--allow-empty", "-m", message], identity=True)
            return head_commit(repo)
        return None
    run_git(repo, ["commit", "-m", message], identity=True)
    return head_commit(repo)


def import_snapshot_to_branch(
    repo: Path,
    snapshot_dir: Path,
    *,
    branch: str,
    patterns: list[str],
    message: str,
) -> str:
    """Create a snapshot commit without touching the user's index or working tree."""
    files = collect_tree(snapshot_dir, patterns)
    parent = head_commit(repo, branch) if branch_exists(repo, branch) else None
    if parent is not None:
        # Paths outside sync scope belong to the local repository, not to the
        # remote snapshot. Carry them forward so merging a new snapshot cannot
        # stage their deletion merely because Overleaf never contained them.
        files.update(_ignored_ref_tree_files(repo, parent, patterns))

    with tempfile.TemporaryDirectory(prefix="ol-index-") as temp_dir:
        index_path = Path(temp_dir) / "index"
        env = {"GIT_INDEX_FILE": str(index_path)}
        run_git(repo, ["read-tree", "--empty"], env=env)
        for path, content in files.items():
            object_id = _hash_blob(repo, content)
            run_git(
                repo,
                ["update-index", "--add", "--cacheinfo", "100644", object_id, path],
                env=env,
            )
        tree = run_git(repo, ["write-tree"], env=env).stdout.strip()

    commit_args = ["commit-tree", tree, "-m", message]
    if parent is not None:
        commit_args.extend(["-p", parent])
    remote_commit = run_git(repo, commit_args, identity=True).stdout.strip()
    ref = f"refs/heads/{branch}"
    update_args = ["update-ref", ref, remote_commit]
    if parent is not None:
        update_args.append(parent)
    run_git(repo, update_args)
    return remote_commit


def _hash_blob(repo: Path, content: bytes) -> str:
    cmd = ["git", "hash-object", "-w", "--stdin"]
    result = subprocess.run(cmd, cwd=repo, input=content, capture_output=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise GitError(f"Git command failed: {' '.join(cmd)}\n{detail}")
    return result.stdout.decode("ascii").strip()


def initialize_unborn_branch_from_ref(repo: Path, ref: str) -> None:
    """Populate the current unborn branch from ref."""
    run_git(repo, ["reset", "--hard", ref])


def gitlink_paths(repo: Path) -> list[str]:
    result = run_git(repo, ["ls-files", "--stage", "-z"], check=False)
    paths: list[str] = []
    for record in result.stdout.split("\0"):
        if not record or "\t" not in record:
            continue
        metadata, path = record.split("\t", 1)
        mode = metadata.split(" ", 1)[0]
        if mode == "160000":
            paths.append(path)
    return paths


def worktrees_for_branch(repo: Path, branch: str) -> list[str]:
    result = run_git(repo, ["worktree", "list", "--porcelain"], check=False)
    worktrees: list[str] = []
    current_path: str | None = None
    expected_ref = f"refs/heads/{branch}"
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            current_path = line.removeprefix("worktree ")
        elif line == f"branch {expected_ref}" and current_path is not None:
            worktrees.append(current_path)
    return worktrees


def worktree_sync_files(repo: Path, patterns: list[str]) -> dict[str, bytes]:
    """Collect tracked files plus untracked files that Git does not ignore."""
    result = run_git(repo, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"])
    files: dict[str, bytes] = {}
    for path in result.stdout.split("\0"):
        if not path or is_ignored(path, patterns):
            continue
        target = repo / path
        if target.is_file():
            files[path] = target.read_bytes()
    return files


def worktree_sync_hashes(repo: Path, patterns: list[str]) -> dict[str, str]:
    result = run_git(repo, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"])
    files: dict[str, str] = {}
    for path in result.stdout.split("\0"):
        if not path or is_ignored(path, patterns):
            continue
        target = repo / path
        if target.is_file():
            files[path] = file_sha256(target)
    return files


def ref_tree_files(repo: Path, ref: str, patterns: list[str]) -> dict[str, bytes]:
    result = run_git(repo, ["ls-tree", "-r", "-z", ref])
    files: dict[str, bytes] = {}
    for record in result.stdout.split("\0"):
        if not record or "\t" not in record:
            continue
        metadata, path = record.split("\t", 1)
        _mode, object_type, object_id = metadata.split(" ", 2)
        if object_type != "blob" or is_ignored(path, patterns):
            continue
        files[path] = _read_blob(repo, object_id)
    return files


def ref_tree_hashes(repo: Path, ref: str, patterns: list[str]) -> dict[str, str]:
    return {
        path: sha256(content).hexdigest()
        for path, content in ref_tree_files(repo, ref, patterns).items()
    }


def _read_blob(repo: Path, object_id: str) -> bytes:
    cmd = ["git", "cat-file", "blob", object_id]
    result = subprocess.run(cmd, cwd=repo, capture_output=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise GitError(f"Git command failed: {' '.join(cmd)}\n{detail}")
    return result.stdout


def _ignored_ref_tree_files(
    repo: Path,
    ref: str,
    patterns: list[str],
) -> dict[str, bytes]:
    result = run_git(repo, ["ls-tree", "-r", "-z", ref])
    files: dict[str, bytes] = {}
    for record in result.stdout.split("\0"):
        if not record or "\t" not in record:
            continue
        metadata, path = record.split("\t", 1)
        _mode, object_type, object_id = metadata.split(" ", 2)
        if object_type == "blob" and is_ignored(path, patterns):
            files[path] = _read_blob(repo, object_id)
    return files


def merge_branch(
    repo: Path,
    branch: str,
    *,
    commit: bool = True,
    allow_unrelated_histories: bool = False,
) -> None:
    if commit:
        args = ["merge", branch, "--no-edit"]
    else:
        args = ["merge", "--no-commit", "--no-ff", branch]
    if allow_unrelated_histories:
        args.append("--allow-unrelated-histories")
    result = run_git(repo, args, check=False)
    if result.returncode != 0:
        files = conflicted_files(repo)
        if files:
            raise SyncConflictError(files)
        raise GitError(result.stderr.strip() or result.stdout.strip())


def name_status_diff(repo: Path, base: str, head: str = "HEAD") -> list[list[str]]:
    result = run_git(repo, ["diff", "--name-status", "-z", "--find-renames", base, head])
    fields = result.stdout.split("\0")
    rows: list[list[str]] = []
    index = 0
    while index < len(fields) and fields[index]:
        status = fields[index]
        index += 1
        if status.startswith(("R", "C")):
            if index + 1 >= len(fields):
                raise GitError("Git returned an incomplete rename/copy record")
            rows.append([status, fields[index], fields[index + 1]])
            index += 2
        else:
            if index >= len(fields):
                raise GitError("Git returned an incomplete name-status record")
            rows.append([status, fields[index]])
            index += 1
    return rows
