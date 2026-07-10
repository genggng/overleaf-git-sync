"""HTTP/session-cookie backend for self-hosted Overleaf CE."""

from __future__ import annotations

import json
import ssl
import tempfile
import time
import zipfile
from base64 import b64encode
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import urlencode, urlparse

import requests
from websocket import WebSocketException, create_connection

from ol_ce_sync.auth import extract_csrf_token, load_auth_session, normalize_host
from ol_ce_sync.backends.base import ProjectTree, TreeEntry
from ol_ce_sync.config import Config
from ol_ce_sync.errors import BackendError, UnsupportedBackendOperation
from ol_ce_sync.snapshot import safe_extract_zip
from ol_ce_sync.utils.logging import warn
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
        if not self.ssl_verify:
            warn(f"TLS certificate verification is disabled for {self.host}.")
        try:
            response = self._session.get(
                f"{self.host}/user/personal_info",
                headers={"Accept": "application/json"},
                timeout=self.timeout,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise BackendError(f"Could not validate Overleaf session: {exc}") from exc
        if response.status_code != 200:
            raise BackendError(
                "Overleaf session is not logged in. Run `ol auth login` again."
            )

    def download_project_snapshot(self, project_id: str, dest_dir: Path) -> None:
        response = self._request(
            "GET",
            f"/project/{project_id}/download/zip",
            stream=True,
        )
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    tmp.write(chunk)
            zip_path = Path(tmp.name)
        try:
            try:
                safe_extract_zip(zip_path, dest_dir)
            except (zipfile.BadZipFile, OSError) as exc:
                raise BackendError(
                    "Overleaf snapshot download was not a valid ZIP archive. "
                    "The session may have expired."
                ) from exc
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
        normalized = normalize_project_path(path)
        existing = self._find_entity(project_id, normalized, refresh=True)
        if existing is not None and existing.type == "doc":
            self._write_text_doc_via_ot(project_id, existing, content)
            return
        self._write_file(project_id, normalized, content.encode("utf-8"))

    def upload_binary_file(self, project_id: str, path: str, content: bytes) -> None:
        self._write_file(project_id, path, content)

    def create_folder(self, project_id: str, path: str) -> HttpEntity:
        normalized = normalize_project_path(path)
        if not normalized:
            return self._load_tree(project_id)
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
                found = self._entity_from_response(
                    self._response_json(response, "create folder"),
                    parent_path=current.path,
                )
                current.children.append(found)
            current = found
        return current

    def delete_path(self, project_id: str, path: str) -> None:
        normalized = normalize_project_path(path)
        for _ in range(2):
            entity = self._find_entity(project_id, normalized, refresh=True)
            if entity is None:
                if not self._path_exists_in_entities(project_id, normalized):
                    return
                raise BackendError(
                    f"Cannot resolve remote entity for deletion: {normalized}"
                )
            self._request(
                "DELETE",
                f"/project/{project_id}/{entity.type}/{entity.id}",
                json={},
                csrf_project_id=project_id,
            )
            self._tree_cache = None
            if not self._path_exists_in_entities(project_id, normalized):
                return
            time.sleep(0.2)
        raise BackendError(f"Remote delete did not remove path: {normalized}")

    def move_path(self, project_id: str, old_path: str, new_path: str) -> None:
        entity = self._find_entity(project_id, old_path)
        if entity is None:
            raise BackendError(f"Cannot move missing remote path: {old_path}")

        new_path = normalize_project_path(new_path)
        new_parent = PurePosixPath(new_path).parent.as_posix()
        new_name = PurePosixPath(new_path).name
        parent = self.create_folder(project_id, "" if new_parent == "." else new_parent)

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
        parent = self.create_folder(project_id, parent_path)

        existing = self._find_entity(project_id, normalized)
        if existing is None:
            self._upload_new_file(project_id, parent.id, file_name, content)
            self._tree_cache = None
            return
        if existing.type == "folder":
            raise BackendError(f"Cannot replace folder with file: {normalized}")

        temp_name = f".ol-upload-{int(time.time() * 1000)}-{file_name}"
        backup_name = f".ol-backup-{int(time.time() * 1000)}-{file_name}"
        temp_path = self._join_path(parent_path, temp_name)
        temp_entity: HttpEntity | None = None
        old_was_renamed = False
        temp_was_promoted = False
        try:
            temp_entity = self._upload_new_file(
                project_id,
                parent.id,
                temp_name,
                content,
            )
            self._request(
                "POST",
                f"/project/{project_id}/{existing.type}/{existing.id}/rename",
                json={"name": backup_name, "source": "editor"},
                csrf_project_id=project_id,
            )
            old_was_renamed = True
            self._request(
                "POST",
                f"/project/{project_id}/{temp_entity.type}/{temp_entity.id}/rename",
                json={"name": file_name, "source": "editor"},
                csrf_project_id=project_id,
            )
            temp_was_promoted = True
            self._request(
                "DELETE",
                f"/project/{project_id}/{existing.type}/{existing.id}",
                json={},
                csrf_project_id=project_id,
            )
        except Exception as exc:
            cleanup_errors = self._compensate_failed_replacement(
                project_id=project_id,
                original=existing,
                temporary=temp_entity,
                temporary_path=temp_path,
                final_name=file_name,
                old_was_renamed=old_was_renamed,
                temp_was_promoted=temp_was_promoted,
            )
            detail = ""
            if cleanup_errors:
                detail = " Cleanup also failed: " + "; ".join(cleanup_errors)
            raise BackendError(
                f"Remote replacement failed for {normalized}.{detail} "
                "Run `ol recover` before retrying."
            ) from exc
        finally:
            self._tree_cache = None

    def _write_text_doc_via_ot(
        self,
        project_id: str,
        entity: HttpEntity,
        content: str,
    ) -> None:
        socket = None
        try:
            socket, _ = self._open_joined_socket(project_id)
            joined = self._socket_rpc(
                socket,
                1,
                "joinDoc",
                [
                    entity.id,
                    {
                        "encodeRanges": True,
                        "supportsHistoryOT": True,
                    },
                ],
            )
            if len(joined) < 3:
                raise BackendError("Overleaf joinDoc returned an incomplete response.")
            if joined[0] is not None:
                raise BackendError(f"Overleaf rejected joinDoc: {joined[0]}")
            lines = joined[1]
            version = joined[2]
            ot_type = joined[5] if len(joined) > 5 else "sharejs-text-ot"
            if not isinstance(lines, list) or not all(
                isinstance(line, str) for line in lines
            ):
                raise BackendError("Overleaf joinDoc returned malformed document lines.")
            if not isinstance(version, int):
                raise BackendError("Overleaf joinDoc returned an invalid document version.")
            if ot_type != "sharejs-text-ot":
                raise UnsupportedBackendOperation(
                    "This Overleaf project uses an unsupported text OT type "
                    f"({ot_type!r}); refusing an unversioned replacement."
                )

            current = "\n".join(self._decode_overleaf_line(line) for line in lines)
            operation = self._text_replacement_operation(current, content)
            if not operation:
                return
            if any(
                ord(character) > 0xFFFF
                for component in operation
                for character in component.get("i", "")
            ):
                raise UnsupportedBackendOperation(
                    "This Overleaf sharejs-text-ot backend cannot safely insert "
                    f"non-BMP Unicode characters in {entity.path}; the server would "
                    "replace their UTF-16 surrogate units with replacement characters."
                )

            applied = self._socket_rpc(
                socket,
                2,
                "applyOtUpdate",
                [entity.id, {"v": version, "op": operation}],
            )
            if applied and applied[0] is not None:
                raise BackendError(f"Overleaf rejected the text update: {applied[0]}")
            self._wait_for_text_content(project_id, entity, content)
        except (BackendError, UnsupportedBackendOperation):
            raise
        except (WebSocketException, OSError, ValueError, KeyError, TypeError) as exc:
            raise BackendError(
                f"Could not apply versioned text update for {entity.path}: {exc}"
            ) from exc
        finally:
            if socket is not None:
                socket.close()
            self._tree_cache = None

    def _open_joined_socket(self, project_id: str):
        socket = self._open_socket(project_id)
        try:
            event = self._receive_socket_event(socket, "joinProjectResponse")
            return socket, event
        except Exception:
            socket.close()
            raise

    def _socket_rpc(
        self,
        socket,
        ack_id: int,
        event_name: str,
        args: list,
    ) -> list:
        payload = json.dumps(
            {"name": event_name, "args": args},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        socket.send(f"5:{ack_id}+::{payload}")
        ack_prefix = f"6:::{ack_id}"
        while True:
            line = self._receive_socket_line(socket)
            if line.startswith("7:"):
                raise BackendError(f"Overleaf socket rejected {event_name}.")
            if line == ack_prefix:
                return []
            if not line.startswith(ack_prefix + "+"):
                continue
            raw = line[len(ack_prefix) + 1 :]
            if not raw:
                return []
            response = json.loads(raw)
            if not isinstance(response, list):
                raise BackendError(
                    f"Overleaf returned a malformed acknowledgement for {event_name}."
                )
            return response

    def _receive_socket_line(self, socket) -> str:
        while True:
            line = socket.recv()
            if not isinstance(line, str):
                raise BackendError("Overleaf socket returned a non-text frame.")
            if line.startswith("2::"):
                socket.send("2::")
                continue
            return line

    def _receive_socket_event(self, socket, event_name: str) -> dict:
        while True:
            line = self._receive_socket_line(socket)
            if line.startswith("7:"):
                raise BackendError("Overleaf socket rejected project access.")
            data = self._parse_socket_event_line(line)
            if data is None or data.get("name") != event_name:
                continue
            return data

    @staticmethod
    def _parse_socket_event_line(line: str) -> dict | None:
        if not line.startswith("5:"):
            return None
        data = json.loads(line[len("5:") :].lstrip(":"))
        if not isinstance(data, dict):
            raise BackendError("Overleaf socket returned a malformed event.")
        return data

    def _wait_for_text_content(
        self,
        project_id: str,
        entity: HttpEntity,
        expected: str,
    ) -> None:
        deadline = time.monotonic() + self.timeout
        while True:
            response = self._request(
                "GET",
                f"/Project/{project_id}/doc/{entity.id}/download",
            )
            try:
                actual = response.content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise BackendError(
                    f"Overleaf returned non-UTF-8 content for {entity.path}."
                ) from exc
            if actual == expected:
                return
            if time.monotonic() >= deadline:
                raise BackendError(
                    f"Versioned text update for {entity.path} was acknowledged but "
                    "did not converge to the expected content. A concurrent edit may "
                    "have been transformed with it; run `ol recover` and pull."
                )
            time.sleep(0.2)

    @staticmethod
    def _decode_overleaf_line(line: str) -> str:
        # Legacy ShareJS stores UTF-8 bytes in a JavaScript string. This is the
        # Python equivalent of the editor's decodeURIComponent(escape(line)).
        try:
            return line.encode("latin-1").decode("utf-8")
        except UnicodeError:
            return line

    @staticmethod
    def _text_replacement_operation(old: str, new: str) -> list[dict]:
        if old == new:
            return []
        prefix = 0
        limit = min(len(old), len(new))
        while prefix < limit and old[prefix] == new[prefix]:
            prefix += 1

        suffix = 0
        old_remaining = len(old) - prefix
        new_remaining = len(new) - prefix
        while (
            suffix < old_remaining
            and suffix < new_remaining
            and old[len(old) - suffix - 1] == new[len(new) - suffix - 1]
        ):
            suffix += 1

        old_end = len(old) - suffix if suffix else len(old)
        new_end = len(new) - suffix if suffix else len(new)
        operation: list[dict] = []
        deleted = old[prefix:old_end]
        inserted = new[prefix:new_end]
        # ShareJS positions are JavaScript string offsets (UTF-16 code units),
        # while Python indexes Unicode code points.
        position = len(old[:prefix].encode("utf-16-le")) // 2
        if deleted:
            operation.append({"d": deleted, "p": position})
        if inserted:
            operation.append({"i": inserted, "p": position})
        return operation

    def _compensate_failed_replacement(
        self,
        *,
        project_id: str,
        original: HttpEntity,
        temporary: HttpEntity | None,
        temporary_path: str,
        final_name: str,
        old_was_renamed: bool,
        temp_was_promoted: bool,
    ) -> list[str]:
        errors: list[str] = []

        def attempt(method: str, path: str, **kwargs) -> None:
            try:
                self._request(
                    method,
                    path,
                    csrf_project_id=project_id,
                    **kwargs,
                )
            except Exception as cleanup_error:
                errors.append(str(cleanup_error))

        # A request exception is ambiguous: the server may have committed the
        # operation before the response was lost. Re-read the entity tree and
        # reconcile by stable entity IDs before consulting local step flags.
        try:
            root = self._load_tree(project_id, refresh=True)
            entities = {entity.id: entity for entity in self._flatten_entities(root)}
            current_original = entities.get(original.id)
            current_temporary = (
                entities.get(temporary.id) if temporary is not None else None
            )
            if current_temporary is None:
                current_temporary = next(
                    (
                        entity
                        for entity in entities.values()
                        if entity.path == temporary_path
                    ),
                    None,
                )

            if current_original is not None:
                # Roll back to the original entity. Remove a promoted or staged
                # replacement first so the final name becomes available.
                if current_temporary is not None:
                    attempt(
                        "DELETE",
                        f"/project/{project_id}/{current_temporary.type}/"
                        f"{current_temporary.id}",
                        json={},
                    )
                    if errors:
                        return errors
                if current_original.name != final_name:
                    attempt(
                        "POST",
                        f"/project/{project_id}/{current_original.type}/"
                        f"{current_original.id}/rename",
                        json={"name": final_name, "source": "editor"},
                    )
                return errors

            if current_temporary is not None:
                # Deleting the original may also have succeeded before its
                # response was lost. In that case the replacement is the only
                # surviving copy, so preserve/promote it rather than creating a
                # data-loss gap.
                if current_temporary.name != final_name:
                    attempt(
                        "POST",
                        f"/project/{project_id}/{current_temporary.type}/"
                        f"{current_temporary.id}/rename",
                        json={"name": final_name, "source": "editor"},
                    )
                return errors

            errors.append(
                "neither the original nor temporary replacement exists remotely"
            )
            return errors
        except Exception:
            # Older or partially failing deployments may not permit a fresh
            # tree read here. Fall back to the known completed-step state.
            pass

        if temp_was_promoted:
            # The replacement is already visible under the final name. Preserve
            # it and retry cleanup of the old backup instead of risking a gap.
            attempt(
                "DELETE",
                f"/project/{project_id}/{original.type}/{original.id}",
                json={},
            )
        elif old_was_renamed:
            attempt(
                "POST",
                f"/project/{project_id}/{original.type}/{original.id}/rename",
                json={"name": final_name, "source": "editor"},
            )
            if temporary is not None:
                attempt(
                    "DELETE",
                    f"/project/{project_id}/{temporary.type}/{temporary.id}",
                    json={},
                )
        elif temporary is not None:
            attempt(
                "DELETE",
                f"/project/{project_id}/{temporary.type}/{temporary.id}",
                json={},
            )
        return errors

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
        data = self._response_json(response, f"upload {file_name}")
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
        allow_redirects = kwargs.pop("allow_redirects", False)
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{self.host}/project/{csrf_project_id or self.config.project.project_id}",
            **headers,
        }
        if csrf_project_id is not None:
            headers["x-csrf-token"] = self._get_csrf_token(csrf_project_id)
        try:
            response = session.request(
                method,
                f"{self.host}{path}",
                headers=headers,
                timeout=self.timeout,
                allow_redirects=allow_redirects,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise BackendError(f"Overleaf HTTP {method} {path} failed: {exc}") from exc
        if 300 <= response.status_code < 400:
            raise BackendError(
                f"Overleaf HTTP {method} {path} redirected unexpectedly to "
                f"{response.headers.get('Location', '(unknown)')}"
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
        socket = None
        try:
            socket, data = self._open_joined_socket(project_id)
        except BackendError:
            raise
        except (WebSocketException, OSError, ValueError, KeyError, TypeError) as exc:
            raise BackendError(f"Could not load Overleaf project tree: {exc}") from exc
        finally:
            if socket is not None:
                socket.close()
        try:
            if not isinstance(data, dict) or data.get("name") != "joinProjectResponse":
                raise BackendError(
                    "Unexpected Overleaf socket response while loading project tree."
                )
            roots = data["args"][0]["project"]["rootFolder"]
            if len(roots) != 1:
                raise BackendError("Unexpected Overleaf project root folder payload.")
            return self._entity_from_socket_folder(roots[0], "")
        except BackendError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise BackendError("Malformed Overleaf project tree response.") from exc

    def _open_socket(self, project_id: str):
        session = self._require_session()
        parsed = urlparse(self.host)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        netloc = parsed.netloc
        base_path = parsed.path.rstrip("/")
        timestamp = int(time.time() * 1000)
        try:
            response = session.get(
                f"{self.host}/socket.io/1/?"
                f"{urlencode({'projectId': project_id, 't': timestamp})}",
                timeout=self.timeout,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise BackendError(f"Could not open Overleaf socket session: {exc}") from exc
        if 300 <= response.status_code < 400:
            raise BackendError("Overleaf socket session redirected to another page.")
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
        try:
            return create_connection(
                f"{scheme}://{netloc}{base_path}/socket.io/1/websocket/{socket_id}"
                f"?{urlencode({'projectId': project_id})}",
                **kwargs,
            )
        except (WebSocketException, OSError) as exc:
            raise BackendError(f"Could not connect to Overleaf socket: {exc}") from exc

    def _entity_from_socket_folder(self, data: dict, parent_path: str) -> HttpEntity:
        if not parent_path and data["name"] == "rootFolder":
            path = ""
        else:
            path = self._join_path(parent_path, data["name"])
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

    def _find_entity(
        self,
        project_id: str,
        path: str,
        *,
        refresh: bool = False,
    ) -> HttpEntity | None:
        normalized = normalize_project_path(path)
        if not normalized:
            return self._load_tree(project_id, refresh=refresh)
        for entity in self._flatten_entities(self._load_tree(project_id, refresh=refresh)):
            if entity.path == normalized:
                return entity
        return None

    def _find_folder(
        self,
        project_id: str,
        path: str,
        *,
        refresh: bool = False,
    ) -> HttpEntity | None:
        entity = self._find_entity(project_id, path, refresh=refresh)
        if entity is None or entity.type != "folder":
            return None
        return entity

    def _path_exists_in_entities(self, project_id: str, path: str) -> bool:
        normalized = normalize_project_path(path)
        response = self._request("GET", f"/project/{project_id}/entities")
        data = self._response_json(response, "list project entities")
        for entity in data.get("entities", []):
            entity_path = entity.get("path")
            if not entity_path:
                continue
            candidate = normalize_project_path(entity_path.lstrip("/"))
            if candidate == normalized:
                return True
        return False

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

    def _response_json(self, response: requests.Response, operation: str) -> dict:
        try:
            data = response.json()
        except (ValueError, TypeError) as exc:
            raise BackendError(
                f"Overleaf returned invalid JSON while trying to {operation}."
            ) from exc
        if not isinstance(data, dict):
            raise BackendError(f"Overleaf returned unexpected JSON while trying to {operation}.")
        return data

    def _join_path(self, parent: str, name: str) -> str:
        if not parent:
            return normalize_project_path(name)
        return normalize_project_path(f"{parent}/{name}")
