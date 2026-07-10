from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ol_ce_sync.backends.http_backend import HttpBackend, HttpEntity
from ol_ce_sync.config import default_config
from ol_ce_sync.errors import BackendError


def make_backend(tmp_path: Path) -> HttpBackend:
    config = default_config(
        tmp_path,
        host="http://localhost",
        project_id="project123",
        backend_type="http",
    )
    return HttpBackend(config)


def test_http_backend_list_project_tree_flattens_entities(tmp_path: Path) -> None:
    backend = make_backend(tmp_path)
    backend._tree_cache = HttpEntity(
        id="root",
        name="root",
        type="folder",
        path="",
        children=[
            HttpEntity(
                id="folder1",
                name="sections",
                type="folder",
                path="sections",
                children=[
                    HttpEntity(
                        id="doc1",
                        name="intro.tex",
                        type="doc",
                        path="sections/intro.tex",
                    )
                ],
            ),
            HttpEntity(id="file1", name="fig.png", type="file", path="fig.png"),
        ],
    )
    backend._load_tree_from_socket = lambda project_id: backend._tree_cache

    tree = backend.list_project_tree("project123")

    assert [(entry.path, entry.is_dir) for entry in tree.entries] == [
        ("sections", True),
        ("sections/intro.tex", False),
        ("fig.png", False),
    ]


def test_http_backend_find_entity_by_path(tmp_path: Path) -> None:
    backend = make_backend(tmp_path)
    backend._tree_cache = HttpEntity(
        id="root",
        name="root",
        type="folder",
        path="",
        children=[HttpEntity(id="doc1", name="main.tex", type="doc", path="main.tex")],
    )

    entity = backend._find_entity("project123", "main.tex")

    assert entity is not None
    assert entity.id == "doc1"


def test_text_replacement_operation_uses_minimal_common_edges(tmp_path: Path) -> None:
    backend = make_backend(tmp_path)

    operation = backend._text_replacement_operation(
        "prefix old suffix",
        "prefix new suffix",
    )

    assert operation == [
        {"d": "old", "p": 7},
        {"i": "new", "p": 7},
    ]
    assert backend._text_replacement_operation("same", "same") == []
    assert backend._text_replacement_operation("😀old", "😀new") == [
        {"d": "old", "p": 2},
        {"i": "new", "p": 2},
    ]


def test_write_existing_text_doc_uses_versioned_ot(tmp_path: Path) -> None:
    backend = make_backend(tmp_path)
    entity = HttpEntity(id="doc1", name="main.tex", type="doc", path="main.tex")

    class FakeSocket:
        def __init__(self) -> None:
            self.sent: list[str] = []
            self.responses = iter(
                [
                    '6:::1+[null,["cafÃ©"],7,[],{},"sharejs-text-ot"]',
                    "6:::2",
                    '5:::{"name":"otUpdateApplied","args":[{"doc":"doc1","v":7}]}',
                ]
            )
            self.closed = False

        def send(self, value: str) -> None:
            self.sent.append(value)

        def recv(self) -> str:
            return next(self.responses)

        def close(self) -> None:
            self.closed = True

    socket = FakeSocket()
    backend._find_entity = lambda project_id, path, refresh=False: entity
    backend._open_joined_socket = lambda project_id: (
        socket,
        {"name": "joinProjectResponse"},
    )
    verified: list[tuple[str, str]] = []
    backend._wait_for_text_content = lambda project_id, entity, content: verified.append(
        (entity.id, content)
    )

    backend.write_text_file("project123", "main.tex", "café updated")

    assert socket.closed
    assert verified == [("doc1", "café updated")]
    assert len(socket.sent) == 2
    join_packet = json.loads(socket.sent[0].split("::", 1)[1])
    assert join_packet["name"] == "joinDoc"
    update_packet = json.loads(socket.sent[1].split("::", 1)[1])
    assert update_packet == {
        "name": "applyOtUpdate",
        "args": [
            "doc1",
            {
                "v": 7,
                "op": [{"i": " updated", "p": 4}],
            },
        ],
    }


def test_write_existing_text_refuses_unknown_ot_type(tmp_path: Path) -> None:
    backend = make_backend(tmp_path)
    entity = HttpEntity(id="doc1", name="main.tex", type="doc", path="main.tex")

    class FakeSocket:
        def send(self, value: str) -> None:
            pass

        def recv(self) -> str:
            return '6:::1+[null,["text"],3,[],{},"history-ot"]'

        def close(self) -> None:
            pass

    backend._open_joined_socket = lambda project_id: (
        FakeSocket(),
        {"name": "joinProjectResponse"},
    )

    with pytest.raises(BackendError, match="unsupported text OT type"):
        backend._write_text_doc_via_ot("project123", entity, "new text")


def test_write_existing_text_refuses_non_bmp_insert(tmp_path: Path) -> None:
    backend = make_backend(tmp_path)
    entity = HttpEntity(id="doc1", name="main.tex", type="doc", path="main.tex")

    class FakeSocket:
        def send(self, value: str) -> None:
            pass

        def recv(self) -> str:
            return '6:::1+[null,["plain"],3,[],{},"sharejs-text-ot"]'

        def close(self) -> None:
            pass

    backend._open_joined_socket = lambda project_id: (
        FakeSocket(),
        {"name": "joinProjectResponse"},
    )

    with pytest.raises(BackendError, match="cannot safely insert non-BMP"):
        backend._write_text_doc_via_ot("project123", entity, "plain 😀")


def test_entity_from_socket_folder_preserves_top_level_folder_path(tmp_path: Path) -> None:
    backend = make_backend(tmp_path)

    folder = backend._entity_from_socket_folder(
        {
            "_id": "folder-chapter",
            "name": "Chapter",
            "folders": [],
            "fileRefs": [],
            "docs": [{"_id": "doc-7", "name": "Chapter_07_system_eval.tex"}],
        },
        "",
    )

    assert folder.path == "Chapter"
    assert folder.children[0].path == "Chapter/Chapter_07_system_eval.tex"


def test_create_folder_returns_new_folder_entity_without_requery(tmp_path: Path) -> None:
    backend = make_backend(tmp_path)
    backend._tree_cache = HttpEntity(
        id="root",
        name="root",
        type="folder",
        path="",
        children=[],
    )

    def request(method: str, path: str, **kwargs):
        class Response:
            def json(self):
                return {"_id": "folder-bib", "name": "Bib", "type": "folder"}

        return Response()

    backend._request = request

    folder = backend.create_folder("project123", "Bib")

    assert folder.id == "folder-bib"
    assert folder.name == "Bib"
    assert folder.type == "folder"


def test_http_backend_replace_file_renames_old_to_backup_before_delete(tmp_path: Path) -> None:
    backend = make_backend(tmp_path)
    calls: list[tuple[str, str]] = []
    existing = HttpEntity(id="old-id", name="main.tex", type="doc", path="main.tex")
    parent = HttpEntity(id="root", name="root", type="folder", path="")

    backend.create_folder = lambda project_id, path: parent
    backend._find_folder = lambda project_id, path, refresh=False: parent
    backend._find_entity = lambda project_id, path, refresh=False: existing

    def upload(project_id: str, folder_id: str, file_name: str, content: bytes) -> HttpEntity:
        calls.append(("UPLOAD", file_name))
        return HttpEntity(id="temp-id", name=file_name, type="doc", path=file_name)

    def request(method: str, path: str, **kwargs):
        calls.append((method, path))

        class Response:
            def json(self):
                return {}

        return Response()

    backend._upload_new_file = upload
    backend._request = request

    backend._write_file("project123", "main.tex", b"new")

    assert calls[0][0] == "UPLOAD"
    assert calls[1] == ("POST", "/project/project123/doc/old-id/rename")
    assert calls[2] == ("POST", "/project/project123/doc/temp-id/rename")
    assert calls[3] == ("DELETE", "/project/project123/doc/old-id")


def test_http_backend_compensates_when_promoting_temp_file_fails(tmp_path: Path) -> None:
    backend = make_backend(tmp_path)
    calls: list[tuple[str, str]] = []
    existing = HttpEntity(id="old-id", name="main.tex", type="doc", path="main.tex")
    parent = HttpEntity(id="root", name="root", type="folder", path="")

    backend.create_folder = lambda project_id, path: parent
    backend._find_entity = lambda project_id, path, refresh=False: existing
    backend._upload_new_file = lambda *args: HttpEntity(
        id="temp-id", name=".ol-upload-main.tex", type="doc", path=".ol-upload-main.tex"
    )

    def request(method: str, path: str, **kwargs):
        calls.append((method, path))
        if calls == [
            ("POST", "/project/project123/doc/old-id/rename"),
            ("POST", "/project/project123/doc/temp-id/rename"),
        ]:
            raise BackendError("simulated promotion failure")
        return SimpleNamespace(json=lambda: {})

    backend._request = request

    with pytest.raises(BackendError, match="Run `ol recover`"):
        backend._write_file("project123", "main.tex", b"new")

    assert calls[-2:] == [
        ("POST", "/project/project123/doc/old-id/rename"),
        ("DELETE", "/project/project123/doc/temp-id"),
    ]


def test_http_backend_deletes_temp_when_first_rename_fails(tmp_path: Path) -> None:
    backend = make_backend(tmp_path)
    existing = HttpEntity(id="old-id", name="main.tex", type="doc", path="main.tex")
    parent = HttpEntity(id="root", name="root", type="folder", path="")
    calls: list[tuple[str, str]] = []
    backend.create_folder = lambda project_id, path: parent
    backend._find_entity = lambda project_id, path, refresh=False: existing
    backend._upload_new_file = lambda *args: HttpEntity(
        id="temp-id", name="temp", type="doc", path="temp"
    )

    def request(method: str, path: str, **kwargs):
        calls.append((method, path))
        if len(calls) == 1:
            raise BackendError("rename failed")
        return SimpleNamespace(json=lambda: {})

    backend._request = request

    with pytest.raises(BackendError, match="recover"):
        backend._write_file("project123", "main.tex", b"new")

    assert calls[-1] == ("DELETE", "/project/project123/doc/temp-id")


def test_http_backend_preserves_promoted_file_when_backup_delete_fails(tmp_path: Path) -> None:
    backend = make_backend(tmp_path)
    existing = HttpEntity(id="old-id", name="main.tex", type="doc", path="main.tex")
    parent = HttpEntity(id="root", name="root", type="folder", path="")
    calls: list[tuple[str, str]] = []
    backend.create_folder = lambda project_id, path: parent
    backend._find_entity = lambda project_id, path, refresh=False: existing
    backend._upload_new_file = lambda *args: HttpEntity(
        id="temp-id", name="temp", type="doc", path="temp"
    )

    def request(method: str, path: str, **kwargs):
        calls.append((method, path))
        if len(calls) == 3:
            raise BackendError("delete failed")
        return SimpleNamespace(json=lambda: {})

    backend._request = request

    with pytest.raises(BackendError, match="recover"):
        backend._write_file("project123", "main.tex", b"new")

    assert calls[-1] == ("DELETE", "/project/project123/doc/old-id")
    assert calls.count(("DELETE", "/project/project123/doc/temp-id")) == 0


def test_compensation_reconciles_lost_rename_response_from_remote_tree(
    tmp_path: Path,
) -> None:
    backend = make_backend(tmp_path)
    original = HttpEntity(
        id="old-id",
        name=".ol-backup-main.tex",
        type="doc",
        path=".ol-backup-main.tex",
    )
    temporary = HttpEntity(
        id="temp-id",
        name=".ol-upload-main.tex",
        type="doc",
        path=".ol-upload-main.tex",
    )
    root = HttpEntity(
        id="root",
        name="root",
        type="folder",
        path="",
        children=[original, temporary],
    )
    calls: list[tuple[str, str]] = []
    backend._load_tree = lambda project_id, refresh=False: root
    backend._request = lambda method, path, **kwargs: calls.append((method, path))

    errors = backend._compensate_failed_replacement(
        project_id="project123",
        original=original,
        temporary=temporary,
        temporary_path=".ol-upload-main.tex",
        final_name="main.tex",
        old_was_renamed=False,
        temp_was_promoted=False,
    )

    assert errors == []
    assert calls == [
        ("DELETE", "/project/project123/doc/temp-id"),
        ("POST", "/project/project123/doc/old-id/rename"),
    ]


def test_delete_path_deletes_after_refresh_hit(tmp_path: Path) -> None:
    backend = make_backend(tmp_path)
    calls: list[tuple[str, str]] = []
    entity = HttpEntity(
        id="doc-7",
        name="Chapter_07_system_eval.tex",
        type="doc",
        path="Chapter/Chapter_07_system_eval.tex",
    )
    lookups = iter([entity, None])

    def find_entity(project_id: str, path: str, refresh: bool = False):
        return next(lookups)

    def request(method: str, path: str, **kwargs):
        calls.append((method, path))

        class Response:
            def json(self):
                return {}

        return Response()

    backend._find_entity = find_entity
    backend._request = request
    backend._path_exists_in_entities = lambda project_id, path: False

    backend.delete_path("project123", "Chapter/Chapter_07_system_eval.tex")

    assert calls == [("DELETE", "/project/project123/doc/doc-7")]


def test_delete_path_raises_when_remote_entity_cannot_be_resolved(tmp_path: Path) -> None:
    backend = make_backend(tmp_path)
    backend._find_entity = lambda project_id, path, refresh=False: None
    backend._path_exists_in_entities = lambda project_id, path: True

    with pytest.raises(BackendError, match="Cannot resolve remote entity for deletion"):
        backend.delete_path("project123", "Chapter/Chapter_07_system_eval.tex")


def test_delete_path_returns_when_entity_is_absent_from_public_entities(tmp_path: Path) -> None:
    backend = make_backend(tmp_path)
    backend._find_entity = lambda project_id, path, refresh=False: None
    backend._path_exists_in_entities = lambda project_id, path: False

    backend.delete_path("project123", "Chapter/Chapter_07_system_eval.tex")


def test_request_rejects_unexpected_redirects(tmp_path: Path) -> None:
    backend = make_backend(tmp_path)

    class Session:
        def request(self, method: str, url: str, **kwargs):
            return SimpleNamespace(
                status_code=302,
                headers={"Location": "/login"},
                text="",
            )

    backend._session = Session()

    with pytest.raises(BackendError, match="redirected unexpectedly"):
        backend._request("DELETE", "/project/project123/doc/doc-7")
