from __future__ import annotations

from pathlib import Path

from ol_ce_sync.backends.http_backend import HttpBackend, HttpEntity
from ol_ce_sync.config import default_config


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


def test_http_backend_replace_file_renames_old_to_backup_before_delete(tmp_path: Path) -> None:
    backend = make_backend(tmp_path)
    calls: list[tuple[str, str]] = []
    existing = HttpEntity(id="old-id", name="main.tex", type="doc", path="main.tex")
    parent = HttpEntity(id="root", name="root", type="folder", path="")

    backend.create_folder = lambda project_id, path: None
    backend._find_folder = lambda project_id, path: parent
    backend._find_entity = lambda project_id, path: existing

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
