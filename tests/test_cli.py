from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest

from ol_ce_sync.auth import session_from_cookie_header
from ol_ce_sync.cli import _init_host_from_args, build_parser, handle_auth


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


def test_init_uses_saved_session_host_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = session_from_cookie_header("http://localhost:3012", "default", "sharelatex.sid=abc")
    session.save(tmp_path / ".ol-sync" / "session.json")
    parser = build_parser()
    args = parser.parse_args(["init", "--project-id", "project123"])

    assert _init_host_from_args(args) == "http://localhost:3012"


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
