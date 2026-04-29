"""Placeholder for a future pyoverleaf-backed adapter."""

from __future__ import annotations

from pathlib import Path

from ol_ce_sync.backends.base import ProjectTree
from ol_ce_sync.errors import UnsupportedBackendOperation


class PyOverleafBackend:
    def __init__(self, host: str, profile: str) -> None:
        self.host = host
        self.profile = profile

    def _not_implemented(self) -> None:
        raise UnsupportedBackendOperation(
            "The pyoverleaf backend is not implemented in this MVP. "
            "Use backend.type = \"http\" for self-hosted Overleaf CE."
        )

    def authenticate(self) -> None:
        self._not_implemented()

    def download_project_snapshot(self, project_id: str, dest_dir: Path) -> None:
        self._not_implemented()

    def list_project_tree(self, project_id: str) -> ProjectTree:
        self._not_implemented()

    def write_text_file(self, project_id: str, path: str, content: str) -> None:
        self._not_implemented()

    def upload_binary_file(self, project_id: str, path: str, content: bytes) -> None:
        self._not_implemented()

    def create_folder(self, project_id: str, path: str) -> None:
        self._not_implemented()

    def delete_path(self, project_id: str, path: str) -> None:
        self._not_implemented()

    def move_path(self, project_id: str, old_path: str, new_path: str) -> None:
        self._not_implemented()
