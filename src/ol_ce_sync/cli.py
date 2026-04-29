"""Command-line interface for ol."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ol_ce_sync.auth import (
    check_session,
    load_auth_session,
    login_with_password,
    session_from_cookie_header,
)
from ol_ce_sync.config import load_config
from ol_ce_sync.errors import OlSyncError
from ol_ce_sync.sync_engine import SyncEngine


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ol")
    subcommands = parser.add_subparsers(dest="command", required=True)

    init = subcommands.add_parser("init", help="initialize sync metadata")
    init.add_argument("--host", default="http://localhost")
    init.add_argument("--project-id", required=True)
    init.add_argument("--project-name", default="overleaf-project")
    init.add_argument("--backend", default="http", choices=["http", "pyoverleaf"])
    init.add_argument("--force", action="store_true")

    pull = subcommands.add_parser("pull", help="import latest remote snapshot and stage it")
    pull.add_argument("--wait", action="store_true")

    push = subcommands.add_parser("push", help="apply committed local changes to Overleaf")
    push.add_argument("--dry-run", action="store_true")
    push.add_argument("--wait", action="store_true")

    subcommands.add_parser("status", help="show local sync status")

    verify = subcommands.add_parser("verify", help="compare local state with remote snapshot")
    verify.add_argument("--allow-diff", action="store_true")

    auth = subcommands.add_parser("auth", help="manage Overleaf HTTP auth session")
    auth_subcommands = auth.add_subparsers(dest="auth_command", required=True)

    login = auth_subcommands.add_parser("login", help="save an Overleaf login session")
    login.add_argument("--host", help="Overleaf base URL, e.g. http://localhost")
    login.add_argument("--profile", default="default")
    login.add_argument("--session-file", help="path to session JSON file")
    login.add_argument("--email", help="Overleaf email for password login")
    login.add_argument("--password", help="Overleaf password; prompts if omitted with --email")
    login.add_argument("--cookie", help='raw Cookie header, e.g. "sharelatex.sid=..."')
    login.add_argument("--insecure", action="store_true", help="disable TLS verification")

    auth_status = auth_subcommands.add_parser("status", help="check saved Overleaf session")
    auth_status.add_argument("--host", help="override Overleaf base URL")
    auth_status.add_argument("--session-file", help="path to session JSON file")
    auth_status.add_argument("--insecure", action="store_true", help="disable TLS verification")

    logout = auth_subcommands.add_parser("logout", help="delete saved Overleaf session")
    logout.add_argument("--session-file", help="path to session JSON file")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    engine = SyncEngine(Path.cwd())
    try:
        if args.command == "init":
            engine.init(
                host=args.host,
                project_id=args.project_id,
                project_name=args.project_name,
                backend_type=args.backend,
                force=args.force,
            )
        elif args.command == "pull":
            engine.pull(wait=args.wait)
        elif args.command == "push":
            engine.push(dry_run=args.dry_run, wait=args.wait)
        elif args.command == "status":
            engine.status()
        elif args.command == "verify":
            engine.verify(allow_diff=args.allow_diff)
        elif args.command == "auth":
            handle_auth(args)
        else:
            parser.error(f"unknown command: {args.command}")
        return 0
    except OlSyncError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _config_or_none():
    try:
        return load_config(Path.cwd())
    except OlSyncError:
        return None


def _session_file_from_args(args) -> Path:
    if args.session_file:
        return Path(args.session_file)
    config = _config_or_none()
    if config is not None:
        return config.resolve_repo_path(config.auth.session_file)
    return Path.cwd() / ".ol-sync" / "session.json"


def _host_from_args(args) -> str:
    if getattr(args, "host", None):
        return args.host
    config = _config_or_none()
    if config is not None:
        return config.project.host
    raise OlSyncError("Missing --host and no .ol-sync/config.toml was found.")


def handle_auth(args) -> None:
    session_file = _session_file_from_args(args)
    if args.auth_command == "login":
        if bool(args.email) == bool(args.cookie):
            raise OlSyncError("Use exactly one login method: --email or --cookie.")
        host = _host_from_args(args)
        if args.cookie:
            auth_session = session_from_cookie_header(host, args.profile, args.cookie)
        else:
            auth_session = login_with_password(
                host=host,
                profile=args.profile,
                email=args.email,
                password=args.password,
                ssl_verify=not args.insecure,
            )
        auth_session.save(session_file)
        print(f"Saved Overleaf session to {session_file}")
        return

    if args.auth_command == "status":
        host = _host_from_args(args)
        auth_session = load_auth_session(session_file, expected_host=host)
        session = auth_session.build_requests_session(ssl_verify=not args.insecure)
        status = check_session(host=host, session=session)
        if status.logged_in:
            identity = status.email or status.user_id or "unknown user"
            print(f"Logged in to {host} as {identity}")
        else:
            raise OlSyncError(f"Not logged in to {host}: {status.error}")
        return

    if args.auth_command == "logout":
        if session_file.exists():
            session_file.unlink()
            print(f"Deleted Overleaf session {session_file}")
        else:
            print(f"No Overleaf session found at {session_file}")
        return

    raise OlSyncError(f"Unknown auth command: {args.auth_command}")


if __name__ == "__main__":
    raise SystemExit(main())
