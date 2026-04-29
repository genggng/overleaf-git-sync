from __future__ import annotations

from pathlib import Path

from ol_ce_sync.auth import (
    AuthSession,
    StoredCookie,
    extract_csrf_token,
    load_auth_session,
    session_from_cookie_header,
)


def test_extract_csrf_token_from_meta() -> None:
    html = '<html><meta content="abc123" name="ol-csrfToken"></html>'

    assert extract_csrf_token(html) == "abc123"


def test_cookie_header_session_roundtrip(tmp_path: Path) -> None:
    session = session_from_cookie_header(
        "http://localhost",
        "default",
        "sharelatex.sid=abc; other=value",
    )
    path = tmp_path / "session.json"
    session.save(path)

    loaded = load_auth_session(path, expected_host="http://localhost")

    assert loaded.host == "http://localhost"
    assert [cookie.name for cookie in loaded.cookies] == ["sharelatex.sid", "other"]


def test_build_requests_session_from_saved_cookies() -> None:
    auth_session = AuthSession(
        host="http://localhost",
        profile="default",
        cookies=(StoredCookie("sharelatex.sid", "abc", "localhost"),),
        created_at=1,
    )

    session = auth_session.build_requests_session()

    assert session.cookies.get("sharelatex.sid", domain="localhost") == "abc"
