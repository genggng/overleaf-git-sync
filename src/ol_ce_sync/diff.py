"""Map Git name-status output to Overleaf push operations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ol_ce_sync import git_ops
from ol_ce_sync.snapshot import is_ignored
from ol_ce_sync.utils.text import is_text_path


@dataclass(frozen=True)
class PushOperation:
    status: str
    path: str
    old_path: str | None = None
    is_text: bool = True

    @property
    def display_status(self) -> str:
        return "R" if self.status.startswith("R") else self.status


def build_push_plan(
    repo: Path,
    base: str,
    patterns: list[str],
    head: str = "HEAD",
) -> list[PushOperation]:
    operations: list[PushOperation] = []
    for row in git_ops.name_status_diff(repo, base, head):
        status = row[0]
        if status.startswith("R"):
            old_path, path = row[1], row[2]
        else:
            path = row[1]
            old_path = None
        if is_ignored(path, patterns) or (old_path and is_ignored(old_path, patterns)):
            continue
        content = None
        if not status.startswith("D"):
            target = repo / path
            content = target.read_bytes() if target.exists() else b""
        operations.append(
            PushOperation(
                status=status,
                path=path,
                old_path=old_path,
                is_text=is_text_path(path, content),
            )
        )
    return operations


def format_push_plan(operations: list[PushOperation]) -> str:
    if not operations:
        return "Planned Overleaf operations:\n  (none)"
    lines = ["Planned Overleaf operations:"]
    for op in operations:
        if op.old_path:
            lines.append(f"  {op.display_status} {op.old_path} -> {op.path}")
        else:
            lines.append(f"  {op.display_status} {op.path}")
    return "\n".join(lines)
