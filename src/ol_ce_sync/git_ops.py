"""Transparent Git subprocess wrapper."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from ol_ce_sync.errors import DirtyWorktreeError, GitError, SyncConflictError
from ol_ce_sync.snapshot import reset_directory_from_snapshot

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
) -> GitResult:
    cmd = ["git", *GIT_IDENTITY, *args] if identity else ["git", *args]
    result = subprocess.run(cmd, cwd=repo, text=True, capture_output=True, check=False)
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
    return (repo / ".git" / "MERGE_HEAD").exists()


def quit_merge(repo: Path) -> None:
    run_git(repo, ["merge", "--quit"], check=False)


def head_commit(repo: Path, ref: str = "HEAD") -> str:
    return run_git(repo, ["rev-parse", ref]).stdout.strip()


def has_staged_changes(repo: Path) -> bool:
    return run_git(repo, ["diff", "--cached", "--quiet"], check=False).returncode == 1


def has_diff_between(repo: Path, base: str, head: str = "HEAD") -> bool:
    result = run_git(repo, ["diff", "--quiet", base, head, *SYNC_EXCLUDES], check=False)
    return result.returncode == 1


def sync_pathspecs(repo: Path) -> list[str]:
    entries = {
        child.name
        for child in repo.iterdir()
        if child.name not in {".git", ".ol-sync"}
    }
    tracked = run_git(repo, ["ls-files", "-z"], check=False).stdout.split("\0")
    for path in tracked:
        if not path:
            continue
        entries.add(path.split("/", 1)[0])
    return sorted(entries)


def commit_all(repo: Path, message: str, *, allow_empty: bool = False) -> str | None:
    pathspecs = sync_pathspecs(repo)
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
    original_branch = current_branch(repo) if is_git_repo(repo) else ""
    if branch_exists(repo, branch):
        switch_branch(repo, branch)
    else:
        run_git(repo, ["switch", "--orphan", branch])
        run_git(repo, ["rm", "-r", "--cached", "."], check=False)
    reset_directory_from_snapshot(repo, snapshot_dir, patterns)
    commit = commit_all(repo, message, allow_empty=True)
    remote_commit = commit or head_commit(repo)
    if original_branch and original_branch != branch:
        if branch_exists(repo, original_branch):
            switch_branch(repo, original_branch)
        else:
            run_git(repo, ["switch", "-c", original_branch])
    return remote_commit


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
    result = run_git(repo, ["diff", "--name-status", "--find-renames", base, head])
    rows: list[list[str]] = []
    for line in result.stdout.splitlines():
        if line.strip():
            rows.append(line.split("\t"))
    return rows
