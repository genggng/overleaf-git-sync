"""Opt-in compatibility checks for a disposable real Overleaf CE project."""

from __future__ import annotations

import os
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from ol_ce_sync.backends.http_backend import HttpBackend
from ol_ce_sync.config import AuthConfig, default_config
from ol_ce_sync.errors import BackendError, UnsupportedBackendOperation


def live_backend(tmp_path: Path) -> tuple[HttpBackend, str]:
    host = os.environ.get("OL_LIVE_HOST")
    project_id = os.environ.get("OL_LIVE_PROJECT_ID")
    session_file = os.environ.get("OL_LIVE_SESSION_FILE")
    if not host or not project_id or not session_file:
        pytest.skip(
            "set OL_LIVE_HOST, OL_LIVE_PROJECT_ID, and OL_LIVE_SESSION_FILE "
            "to run live Overleaf checks"
        )
    config = default_config(tmp_path, host=host, project_id=project_id)
    config = replace(config, auth=AuthConfig(session_file=session_file))
    return HttpBackend(config), project_id


def test_live_download_and_entity_tree(tmp_path: Path) -> None:
    backend, project_id = live_backend(tmp_path)

    backend.authenticate()
    backend.download_project_snapshot(project_id, tmp_path / "snapshot")
    backend.list_project_tree(project_id)


def test_live_disposable_write_replace_delete(tmp_path: Path) -> None:
    if os.environ.get("OL_LIVE_ALLOW_WRITE") != "1":
        pytest.skip("set OL_LIVE_ALLOW_WRITE=1 only for a disposable project")
    backend, project_id = live_backend(tmp_path)
    probe = f"ol-live-probe-{uuid.uuid4().hex}.tex"
    content = f"overleaf-git-sync live probe {uuid.uuid4().hex}\n"

    backend.authenticate()
    created = False
    try:
        backend.write_text_file(project_id, probe, content)
        created = True
        backend.download_project_snapshot(project_id, tmp_path / "after-write")
        assert (tmp_path / "after-write" / probe).read_text(encoding="utf-8") == content
        entity = backend._find_entity(project_id, probe, refresh=True)
        assert entity is not None
        assert entity.type == "doc"

        updated = content.rstrip("\n") + " 中文\n"
        backend.write_text_file(project_id, probe, updated)
        backend.download_project_snapshot(project_id, tmp_path / "after-ot-update")
        assert (tmp_path / "after-ot-update" / probe).read_text(
            encoding="utf-8"
        ) == updated

        updated_after_unicode = updated.rstrip("\n") + " — versioned OT update\n"
        backend.write_text_file(project_id, probe, updated_after_unicode)
        backend.download_project_snapshot(project_id, tmp_path / "after-utf16-update")
        assert (tmp_path / "after-utf16-update" / probe).read_text(
            encoding="utf-8"
        ) == updated_after_unicode

        with pytest.raises(UnsupportedBackendOperation, match="non-BMP"):
            backend.write_text_file(
                project_id,
                probe,
                updated_after_unicode.rstrip("\n") + " 😀\n",
            )
        backend.download_project_snapshot(project_id, tmp_path / "after-rejected-emoji")
        assert (tmp_path / "after-rejected-emoji" / probe).read_text(
            encoding="utf-8"
        ) == updated_after_unicode
    finally:
        if created:
            backend.delete_path(project_id, probe)

    backend.download_project_snapshot(project_id, tmp_path / "after-delete")
    assert not (tmp_path / "after-delete" / probe).exists()


def test_live_lost_response_after_remote_rename_is_compensated(tmp_path: Path) -> None:
    if os.environ.get("OL_LIVE_ALLOW_FAULT_INJECTION") != "1":
        pytest.skip(
            "set OL_LIVE_ALLOW_FAULT_INJECTION=1 only for a disposable project"
        )
    backend, project_id = live_backend(tmp_path)
    probe = f".ol-live-fault-{uuid.uuid4().hex}.bin"
    initial = f"initial-{uuid.uuid4().hex}".encode()
    replacement = f"replacement-{uuid.uuid4().hex}".encode()

    backend.authenticate()
    original_request = backend._request
    created = False
    try:
        backend.upload_binary_file(project_id, probe, initial)
        created = True
        entity = backend._find_entity(project_id, probe, refresh=True)
        assert entity is not None
        rename_path = f"/project/{project_id}/{entity.type}/{entity.id}/rename"
        injected = False

        def request_with_lost_response(method: str, path: str, **kwargs):
            nonlocal injected
            response = original_request(method, path, **kwargs)
            if not injected and method == "POST" and path == rename_path:
                injected = True
                raise BackendError(
                    "simulated connection loss after accepted rename response"
                )
            return response

        backend._request = request_with_lost_response
        with pytest.raises(BackendError, match="Run `ol recover`"):
            backend.upload_binary_file(project_id, probe, replacement)
        assert injected
        backend._request = original_request

        backend.download_project_snapshot(project_id, tmp_path / "after-fault")
        assert (tmp_path / "after-fault" / probe).read_bytes() == initial
        residual = [
            entry.path
            for entry in backend.list_project_tree(project_id).entries
            if entry.path.endswith(probe)
            and (".ol-upload-" in entry.path or ".ol-backup-" in entry.path)
        ]
        assert residual == []
    finally:
        backend._request = original_request
        if created:
            for entry in backend.list_project_tree(project_id).entries:
                if entry.path == probe or (
                    entry.path.endswith(probe)
                    and (".ol-upload-" in entry.path or ".ol-backup-" in entry.path)
                ):
                    backend.delete_path(project_id, entry.path)


def test_live_concurrent_ot_is_transformed_and_reported(tmp_path: Path) -> None:
    if os.environ.get("OL_LIVE_ALLOW_CONCURRENT_OT") != "1":
        pytest.skip("set OL_LIVE_ALLOW_CONCURRENT_OT=1 only for a disposable project")
    backend, project_id = live_backend(tmp_path)
    collaborator, _ = live_backend(tmp_path)
    probe = f"ol-live-concurrent-{uuid.uuid4().hex}.tex"
    uploaded = "anchor\n"
    initial = "anchor 文\n"
    local_content = "anchor 文-local\n"
    remote_content = "remote-anchor 文\n"

    backend.authenticate()
    collaborator.authenticate()
    created = False
    original_rpc = backend._socket_rpc
    try:
        backend.write_text_file(project_id, probe, uploaded)
        created = True
        backend.write_text_file(project_id, probe, initial)
        backend.download_project_snapshot(project_id, tmp_path / "before-concurrent-ot")
        assert (tmp_path / "before-concurrent-ot" / probe).read_text(
            encoding="utf-8"
        ) == initial
        entity = backend._find_entity(project_id, probe, refresh=True)
        assert entity is not None
        assert entity.type == "doc"
        interleaved = False

        def rpc_with_concurrent_edit(socket, ack_id, event_name, args):
            nonlocal interleaved
            response = original_rpc(socket, ack_id, event_name, args)
            if event_name == "joinDoc" and not interleaved:
                collaborator.write_text_file(project_id, probe, remote_content)
                interleaved = True
            return response

        backend._socket_rpc = rpc_with_concurrent_edit
        backend.timeout = 3
        error: BackendError | None = None
        try:
            backend.write_text_file(project_id, probe, local_content)
        except BackendError as exc:
            error = exc
        assert interleaved
        backend._socket_rpc = original_rpc

        backend.download_project_snapshot(project_id, tmp_path / "after-concurrent-ot")
        actual = (tmp_path / "after-concurrent-ot" / probe).read_text(
            encoding="utf-8"
        )
        assert actual == "remote-anchor 文-local\n"
        assert actual != local_content
        assert actual != remote_content
        assert error is not None
        assert "did not converge" in str(error)
    finally:
        backend._socket_rpc = original_rpc
        if created:
            backend.delete_path(project_id, probe)
