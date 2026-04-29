"""Backend adapter protocol."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class TreeEntry:
    path: str
    is_dir: bool


@dataclass(frozen=True)
class ProjectTree:
    entries: tuple[TreeEntry, ...]


class OverleafBackend(Protocol):
    def authenticate(self) -> None:
        """Authenticate before project operations."""

    def download_project_snapshot(self, project_id: str, dest_dir: Path) -> None:
        """Download the project source snapshot into dest_dir."""

    def list_project_tree(self, project_id: str) -> ProjectTree:
        """Return a project tree listing."""

    def write_text_file(self, project_id: str, path: str, content: str) -> None:
        """Create or update an editable text document."""

    def upload_binary_file(self, project_id: str, path: str, content: bytes) -> None:
        """Create or update a binary asset."""

    def create_folder(self, project_id: str, path: str) -> None:
        """Create a folder if it does not already exist."""

    def delete_path(self, project_id: str, path: str) -> None:
        """Delete a file or folder."""

    def move_path(self, project_id: str, old_path: str, new_path: str) -> None:
        """Move or rename a path atomically when supported."""
