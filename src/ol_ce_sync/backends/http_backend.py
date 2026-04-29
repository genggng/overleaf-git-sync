"""HTTP/session-cookie backend for self-hosted Overleaf CE."""

from __future__ import annotations

import json
import ssl
import tempfile
import time
from base64 import b64encode
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import urlencode, urlparse

import requests
from websocket import create_connection

from ol_ce_sync.auth import extract_csrf_token, load_auth_session, normalize_host
from ol_ce_sync.backends.base import ProjectTree, TreeEntry
from ol_ce_sync.config import Config
from ol_ce_sync.errors import BackendError, UnsupportedBackendOperation
from ol_ce_sync.snapshot import safe_extract_zip
from ol_ce_sync.utils.paths import normalize_project_path


@dataclass
class HttpEntity:
    id: str
    name: str
    type: str
    path: str
    children: list[HttpEntity] = field(default_factory=list)


class HttpBackend:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.host = normalize_host(config.project.host)
        self.timeout = config.backend.timeout
        self.ssl_verify = config.backend.ssl_verify
        self._session: requests.Session | None = None
        self._csrf_cache: tuple[str, str] | None = None
        self._tree_cache: HttpEntity | None = None

    def authenticate(self) -> None:
        session_file = self.config.resolve_repo_path(self.config.auth.session_file)
        auth_session = load_auth_session(session_file, expected_host=self.host)
        self._session = auth_session.build_requests_session(ssl_verify=self.ssl_verify)
        response = self._session.get(
            f"{self.host}/user/personal_info",
            headers={"Accept": "application/json"},
            timeout=self.timeout,
            allow_redirects=False,
        )
        if response.status_code != 200:
            raise BackendError(
                "Overleaf session is not logged in. Run `ol auth login` again."
            )

    def download_project_snapshot(self, project_id: str, dest_dir: Path) -> None:
        response = self._request("GET", f"/project/{project_id}/download/zip")
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp.write(response.content)
            zip_path = Path(tmp.name)
        try:
            safe_extract_zip(zip_path, dest_dir)
        finally:
            zip_path.unlink(missing_ok=True)

    def list_project_tree(self, project_id: str) -> ProjectTree:
        root = self._load_tree(project_id, refresh=True)
        entries: list[TreeEntry] = []
        for entity in self._flatten_entities(root):
            if entity.path:
                entries.append(TreeEntry(path=entity.path, is_dir=entity.type == "folder"))
        return ProjectTree(entries=tuple(entries))

    def write_text_file(self, project_id: str, path: str, content: str) -> None:
        self._write_file(project_id, path, content.encode("utf-8"))

    def upload_binary_file(self, project_id: str, path: str, content: bytes) -> None:
        self._write_file(project_id, path, content)

    def create_folder(self, project_id: str, path: str) -> None:
        normalized = normalize_project_path(path)
        if not normalized:
            return
        current = self._load_tree(project_id)
        for part in PurePosixPath(normalized).parts:
            found = self._child_named(current, part, "folder")
            if found is None:
                payload = {"parent_folder_id": current.id, "name": part}
                response = self._request(
                    "POST",
                    f"/project/{project_id}/folder",
                    json=payload,
                    csrf_project_id=project_id,
                )
                found = self._entity_from_response(response.json(), parent_path=current.path)
                current.children.append(found)
            current = found

    def delete_path(self, project_id: str, path: str) -> None:
        entity = self._find_entity(project_id, path)
        if entity is None:
            return
        self._request(
            "DELETE",
            f"/project/{project_id}/{entity.type}/{entity.id}",
            json={},
            csrf_project_id=project_id,
        )
        self._tree_cache = None

    def move_path(self, project_id: str, old_path: str, new_path: str) -> None:
        entity = self._find_entity(project_id, old_path)
        if entity is None:
            raise BackendError(f"Cannot move missing remote path: {old_path}")

        new_path = normalize_project_path(new_path)
        new_parent = PurePosixPath(new_path).parent.as_posix()
        new_name = PurePosixPath(new_path).name
        self.create_folder(project_id, "" if new_parent == "." else new_parent)
        parent = self._find_folder(project_id, "" if new_parent == "." else new_parent)
        if parent is None:
            raise BackendError(f"Cannot find destination folder for {new_path}")

        if entity.name != new_name:
            self._request(
                "POST",
                f"/project/{project_id}/{entity.type}/{entity.id}/rename",
                json={"name": new_name, "source": "editor"},
                csrf_project_id=project_id,
            )
            entity.name = new_name

        old_parent = PurePosixPath(entity.path).parent.as_posix()
        target_parent = PurePosixPath(new_path).parent.as_posix()
        if old_parent != target_parent:
            self._request(
                "POST",
                f"/project/{project_id}/{entity.type}/{entity.id}/move",
                json={"folder_id": parent.id, "source": "editor"},
                csrf_project_id=project_id,
            )
        self._tree_cache = None

    def _write_file(self, project_id: str, path: str, content: bytes) -> None:
        normalized = normalize_project_path(path)
        parent_path = PurePosixPath(normalized).parent.as_posix()
        file_name = PurePosixPath(normalized).name
        parent_path = "" if parent_path == "." else parent_path
        self.create_folder(project_id, parent_path)
        parent = self._find_folder(project_id, parent_path)
        if parent is None:
            raise BackendError(f"Cannot find remote folder: {parent_path}")

        existing = self._find_entity(project_id, normalized)
        if existing is None:
            self._upload_new_file(project_id, parent.id, file_name, content)
            self._tree_cache = None
            return
        if existing.type == "folder":
            raise BackendError(f"Cannot replace folder with file: {normalized}")

        temp_name = f".ol-upload-{int(time.time() * 1000)}-{file_name}"
        backup_name = f".ol-backup-{int(time.time() * 1000)}-{file_name}"
        temp_entity = self._upload_new_file(project_id, parent.id, temp_name, content)
        self._request(
            "POST",
            f"/project/{project_id}/{existing.type}/{existing.id}/rename",
            json={"name": backup_name, "source": "editor"},
            csrf_project_id=project_id,
        )
        self._request(
            "POST",
            f"/project/{project_id}/{temp_entity.type}/{temp_entity.id}/rename",
            json={"name": file_name, "source": "editor"},
            csrf_project_id=project_id,
        )
        self._request(
            "DELETE",
            f"/project/{project_id}/{existing.type}/{existing.id}",
            json={},
            csrf_project_id=project_id,
        )
        self._tree_cache = None

    def _upload_new_file(
        self,
        project_id: str,
        folder_id: str,
        file_name: str,
        content: bytes,
    ) -> HttpEntity:
        response = self._request(
            "POST",
            f"/Project/{project_id}/upload?{urlencode({'folder_id': folder_id})}",
            files={
                "relativePath": (None, "null"),
                "name": (None, file_name),
                "type": (None, "application/octet-stream"),
                "qqfile": (file_name, content, "application/octet-stream"),
            },
            csrf_project_id=project_id,
        )
        data = response.json()
        if not data.get("success"):
            raise BackendError(f"Overleaf upload failed for {file_name}: {data}")
        entity_id = data.get("entity_id")
        entity_type = data.get("entity_type")
        if not entity_id or entity_type not in {"doc", "file"}:
            raise BackendError(f"Overleaf upload returned unexpected payload: {data}")
        return HttpEntity(id=entity_id, name=file_name, type=entity_type, path=file_name)

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        session = self._require_session()
        csrf_project_id = kwargs.pop("csrf_project_id", None)
        headers = kwargs.pop("headers", {})
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{self.host}/project/{csrf_project_id or self.config.project.project_id}",
            **headers,
        }
        if csrf_project_id is not None:
            headers["x-csrf-token"] = self._get_csrf_token(csrf_project_id)
        response = session.request(
            method,
            f"{self.host}{path}",
            headers=headers,
            timeout=self.timeout,
            **kwargs,
        )
        if response.status_code >= 400:
            raise BackendError(
                f"Overleaf HTTP {method} {path} failed with HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )
        return response

    def _get_csrf_token(self, project_id: str) -> str:
        if self._csrf_cache is not None and self._csrf_cache[0] == project_id:
            return self._csrf_cache[1]
        response = self._request("GET", f"/project/{project_id}")
        token = extract_csrf_token(response.text)
        self._csrf_cache = (project_id, token)
        return token

    def _load_tree(self, project_id: str, *, refresh: bool = False) -> HttpEntity:
        if self._tree_cache is not None and not refresh:
            return self._tree_cache
        root = self._load_tree_from_socket(project_id)
        self._tree_cache = root
        return root

    def _load_tree_from_socket(self, project_id: str) -> HttpEntity:
        socket = self._open_socket(project_id)
        try:
            while True:
                line = socket.recv()
                if line.startswith("7:"):
                    raise BackendError("Overleaf socket rejected project access.")
                if line.startswith("5:"):
                    break
            data = json.loads(line[len("5:") :].lstrip(":"))
        finally:
            socket.close()
        if data.get("name") != "joinProjectResponse":
            raise BackendError("Unexpected Overleaf socket response while loading project tree.")
        roots = data["args"][0]["project"]["rootFolder"]
        if len(roots) != 1:
            raise BackendError("Unexpected Overleaf project root folder payload.")
        return self._entity_from_socket_folder(roots[0], "")

    def _open_socket(self, project_id: str):
        session = self._require_session()
        parsed = urlparse(self.host)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        netloc = parsed.netloc
        base_path = parsed.path.rstrip("/")
        timestamp = int(time.time() * 1000)
        response = session.get(
            f"{self.host}/socket.io/1/?{urlencode({'projectId': project_id, 't': timestamp})}",
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise BackendError(f"Could not open Overleaf socket: HTTP {response.status_code}")
        socket_id = response.text.split(":", 1)[0]
        cookie_header = "; ".join(
            f"{cookie.name}={cookie.value}" for cookie in session.cookies
        )
        headers = dict(session.headers)
        if session.auth is not None and "Authorization" not in headers:
            if not isinstance(session.auth, tuple):
                raise UnsupportedBackendOperation("Only basic auth tuples are supported.")
            token = b64encode(f"{session.auth[0]}:{session.auth[1]}".encode()).decode()
            headers["Authorization"] = "Basic " + token
        kwargs = {
            "header": headers,
            "cookie": cookie_header,
            "timeout": self.timeout,
            "enable_multithread": True,
        }
        if not self.ssl_verify:
            kwargs["sslopt"] = {"cert_reqs": ssl.CERT_NONE}
        return create_connection(
            f"{scheme}://{netloc}{base_path}/socket.io/1/websocket/{socket_id}"
            f"?{urlencode({'projectId': project_id})}",
            **kwargs,
        )

    def _entity_from_socket_folder(self, data: dict, parent_path: str) -> HttpEntity:
        path = self._join_path(parent_path, data["name"]) if parent_path else ""
        folder = HttpEntity(id=data["_id"], name=data["name"], type="folder", path=path)
        for child in data.get("folders", []):
            folder.children.append(self._entity_from_socket_folder(child, path))
        for child in data.get("fileRefs", []):
            child_path = self._join_path(path, child["name"])
            folder.children.append(
                HttpEntity(id=child["_id"], name=child["name"], type="file", path=child_path)
            )
        for child in data.get("docs", []):
            child_path = self._join_path(path, child["name"])
            folder.children.append(
                HttpEntity(id=child["_id"], name=child["name"], type="doc", path=child_path)
            )
        return folder

    def _entity_from_response(self, data: dict, *, parent_path: str) -> HttpEntity:
        name = data["name"]
        entity_type = data.get("type", "folder")
        return HttpEntity(
            id=data["_id"],
            name=name,
            type=entity_type,
            path=self._join_path(parent_path, name),
        )

    def _find_entity(self, project_id: str, path: str) -> HttpEntity | None:
        normalized = normalize_project_path(path)
        if not normalized:
            return self._load_tree(project_id)
        for entity in self._flatten_entities(self._load_tree(project_id)):
            if entity.path == normalized:
                return entity
        return None

    def _find_folder(self, project_id: str, path: str) -> HttpEntity | None:
        entity = self._find_entity(project_id, path)
        if entity is None or entity.type != "folder":
            return None
        return entity

    def _child_named(
        self,
        parent: HttpEntity,
        name: str,
        entity_type: str | None = None,
    ) -> HttpEntity | None:
        for child in parent.children:
            if child.name == name and (entity_type is None or child.type == entity_type):
                return child
        return None

    def _flatten_entities(self, root: HttpEntity) -> list[HttpEntity]:
        entities: list[HttpEntity] = []
        for child in root.children:
            entities.append(child)
            if child.type == "folder":
                entities.extend(self._flatten_entities(child))
        return entities

    def _require_session(self) -> requests.Session:
        if self._session is None:
            self.authenticate()
        assert self._session is not None
        return self._session

    def _join_path(self, parent: str, name: str) -> str:
        if not parent:
            return normalize_project_path(name)
        return normalize_project_path(f"{parent}/{name}")
