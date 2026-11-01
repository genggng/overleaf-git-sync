from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest

from ol_ce_sync.auth import session_from_cookie_header
from ol_ce_sync.cli import _init_host_from_args, build_parser, handle_auth, main


def test_parser_uses_short_command_name() -> None:
    parser = build_parser()

    assert parser.prog == "ol"


def test_pull_no_longer_accepts_autostash() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["pull", "--autostash"])


def test_push_accepts_fast_flag() -> None:
    parser = build_parser()

    args = parser.parse_args(["push", "--fast"])

    assert args.fast is True


def test_status_accepts_json_flag() -> None:
    args = build_parser().parse_args(["status", "--json"])

    assert args.json is True


def test_status_json_is_machine_readable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = {
        "schema_version": 1,
        "branch": "main",
        "working_tree_clean": True,
        "changes": [],
    }

    class FakeEngine:
        def __init__(self, repo_root: Path) -> None:
            self.repo_root = repo_root

        def status_data(self) -> dict[str, object]:
            return payload

        def close(self) -> None:
            return

    monkeypatch.setattr("ol_ce_sync.cli.SyncEngine", FakeEngine)

    assert main(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == payload


def test_init_uses_saved_session_host_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = session_from_cookie_header("http://localhost:3012", "default", "sharelatex.sid=abc")
    session.save(tmp_path / ".ol-sync" / "session.json")
    parser = build_parser()
    args = parser.parse_args(["init", "--project-id", "project123"])

    assert _init_host_from_args(args) == "http://localhost:3012"


def test_auth_status_uses_saved_session_host_before_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    session = session_from_cookie_header("http://localhost:3012", "default", "sharelatex.sid=abc")
    session.save(tmp_path / ".ol-sync" / "session.json")

    class FakeStatus:
        logged_in = True
        email = "user@example.com"
        user_id = None
        error = None

    monkeypatch.setattr("ol_ce_sync.cli.check_session", lambda *, host, session: FakeStatus())
    args = build_parser().parse_args(["auth", "status"])

    handle_auth(args)

    assert "Logged in to http://localhost:3012 as user@example.com" in capsys.readouterr().out


def test_cookie_stdin_is_saved_and_ignored_before_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin", StringIO("sharelatex.sid=secret\n"))
    args = build_parser().parse_args(
        ["auth", "login", "--host", "http://localhost", "--cookie-stdin"]
    )

    handle_auth(args)

    assert (tmp_path / ".ol-sync" / "session.json").exists()
    assert ".ol-sync/" in (tmp_path / ".gitignore").read_text(encoding="utf-8")
