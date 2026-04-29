from __future__ import annotations

from pathlib import Path

from ol_ce_sync import git_ops


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def commit_all(repo: Path, message: str) -> str:
    commit = git_ops.commit_all(repo, message)
    return commit or git_ops.head_commit(repo)
