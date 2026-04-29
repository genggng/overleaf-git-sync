"""Cookie-session authentication for Overleaf HTTP access."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from getpass import getpass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import requests

from ol_ce_sync.errors import BackendError, ConfigError


class CsrfParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.token: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "meta":
            return
        attr_map = {key.lower(): value for key, value in attrs}
        if attr_map.get("name") == "ol-csrfToken":
            self.token = attr_map.get("content")


@dataclass(frozen=True)
class StoredCookie:
    name: str
    value: str
    domain: str
    path: str = "/"
    secure: bool = False


@dataclass(frozen=True)
class AuthSession:
    host: str
    profile: str
    cookies: tuple[StoredCookie, ...]
    created_at: float

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "host": self.host,
            "profile": self.profile,
            "created_at": self.created_at,
            "cookies": [cookie.__dict__ for cookie in self.cookies],
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        path.chmod(0o600)

    @classmethod
    def load(cls, path: Path) -> AuthSession:
        if not path.exists():
            raise ConfigError(f"Missing auth session file: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        cookies = tuple(StoredCookie(**cookie) for cookie in data.get("cookies", []))
        return cls(
            host=str(data["host"]),
            profile=str(data.get("profile", "default")),
            cookies=cookies,
            created_at=float(data.get("created_at", 0)),
        )

    def build_requests_session(self, *, ssl_verify: bool = True) -> requests.Session:
        session = requests.Session()
        session.verify = ssl_verify
        for cookie in self.cookies:
            session.cookies.set(
                cookie.name,
                cookie.value,
                domain=cookie.domain,
                path=cookie.path,
            )
        return session


def normalize_host(host: str) -> str:
    if not host:
        raise ConfigError("Overleaf host is required.")
    if "://" not in host:
        host = "https://" + host
    return host.rstrip("/")


def host_cookie_domain(host: str) -> str:
    parsed = urlparse(normalize_host(host))
    if not parsed.hostname:
        raise ConfigError(f"Invalid Overleaf host: {host}")
    return parsed.hostname


def extract_csrf_token(html: str) -> str:
    parser = CsrfParser()
    parser.feed(html)
    if not parser.token:
        raise BackendError("Could not find Overleaf CSRF token in page HTML.")
    return parser.token


def session_from_cookie_header(host: str, profile: str, cookie_header: str) -> AuthSession:
    domain = host_cookie_domain(host)
    cookies: list[StoredCookie] = []
    for item in cookie_header.split(";"):
        if not item.strip():
            continue
        if "=" not in item:
            raise ConfigError(f"Invalid cookie pair: {item.strip()}")
        name, value = item.strip().split("=", 1)
        cookies.append(StoredCookie(name=name, value=value, domain=domain))
    if not cookies:
        raise ConfigError("No cookies were provided.")
    return AuthSession(
        host=normalize_host(host),
        profile=profile,
        cookies=tuple(cookies),
        created_at=time.time(),
    )


def login_with_password(
    *,
    host: str,
    profile: str,
    email: str,
    password: str | None,
    timeout: int = 16,
    ssl_verify: bool = True,
) -> AuthSession:
    host = normalize_host(host)
    if password is None:
        password = getpass("Overleaf password: ")
    session = requests.Session()
    session.verify = ssl_verify

    login_page = session.get(f"{host}/login", timeout=timeout)
    login_page.raise_for_status()
    csrf = extract_csrf_token(login_page.text)

    response = session.post(
        f"{host}/login",
        data={"email": email, "password": password, "_csrf": csrf},
        headers={
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{host}/login",
        },
        timeout=timeout,
        allow_redirects=False,
    )
    if response.status_code >= 400:
        raise BackendError(
            "Overleaf login failed. Check credentials, captcha settings, and server logs."
        )

    status = check_session(host=host, session=session, timeout=timeout)
    if not status.logged_in:
        raise BackendError("Overleaf login did not produce a valid logged-in session.")

    cookies = tuple(
        StoredCookie(
            name=cookie.name,
            value=cookie.value,
            domain=cookie.domain or host_cookie_domain(host),
            path=cookie.path,
            secure=cookie.secure,
        )
        for cookie in session.cookies
    )
    return AuthSession(host=host, profile=profile, cookies=cookies, created_at=time.time())


@dataclass(frozen=True)
class AuthStatus:
    logged_in: bool
    email: str | None = None
    user_id: str | None = None
    error: str | None = None


def check_session(
    *,
    host: str,
    session: requests.Session,
    timeout: int = 16,
) -> AuthStatus:
    host = normalize_host(host)
    try:
        response = session.get(
            f"{host}/user/personal_info",
            headers={"Accept": "application/json"},
            timeout=timeout,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        return AuthStatus(logged_in=False, error=str(exc))
    if response.status_code != 200:
        return AuthStatus(logged_in=False, error=f"HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError as exc:
        return AuthStatus(logged_in=False, error=f"Invalid JSON response: {exc}")
    return AuthStatus(
        logged_in=True,
        email=data.get("email"),
        user_id=data.get("_id") or data.get("id"),
    )


def load_auth_session(path: Path, *, expected_host: str | None = None) -> AuthSession:
    session = AuthSession.load(path)
    if expected_host is not None and normalize_host(session.host) != normalize_host(expected_host):
        raise ConfigError(
            f"Session host {session.host!r} does not match configured host "
            f"{normalize_host(expected_host)!r}."
        )
    return session
