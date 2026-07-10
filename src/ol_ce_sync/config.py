"""Repo-local configuration loading and writing."""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ol_ce_sync.errors import ConfigError
from ol_ce_sync.utils.files import atomic_write_text

LATEX_AUXILIARY_PATTERNS = [
    "*.aux",
    "*.bcf",
    "*.blg",
    "*.fdb_latexmk",
    "*.fls",
    "*.glg",
    "*.glo",
    "*.gls",
    "*.idx",
    "*.ilg",
    "*.ind",
    "*.lof",
    "*.log",
    "*.lot",
    "*.nav",
    "*.out",
    "*.run.xml",
    "*.snm",
    "*.synctex.gz",
    "*.toc",
    "*.vrb",
    "*.xdv",
]

DEFAULT_IGNORE_PATTERNS = [
    ".git/",
    ".gitignore",
    ".env",
    ".ol-sync/",
    *LATEX_AUXILIARY_PATTERNS,
]

LEGACY_AMBIGUOUS_IGNORE_PATTERNS = ["*.bbl", "*.ist", "*.pdf"]

DEFAULT_GITIGNORE_LINES = [
    ".ol-sync/",
    ".env",
    *LATEX_AUXILIARY_PATTERNS,
]


@dataclass(frozen=True)
class ProjectConfig:
    host: str
    project_id: str
    project_name: str


@dataclass(frozen=True)
class GitConfig:
    main_branch: str = "main"
    remote_branch: str = "overleaf-remote"
    require_clean_worktree_before_push: bool = True


@dataclass(frozen=True)
class BackendConfig:
    type: str = "http"
    timeout: int = 16
    ssl_verify: bool = True


@dataclass(frozen=True)
class AuthConfig:
    profile: str = "default"
    session_file: str = ".ol-sync/session.json"


@dataclass(frozen=True)
class SyncConfig:
    lock_file: str = ".ol-sync/lock"
    snapshot_dir: str = ".ol-sync/snapshots"
    last_synced_file: str = ".ol-sync/last_synced_commit"
    last_remote_snapshot_file: str = ".ol-sync/last_remote_snapshot_commit"
    dry_run_default: bool = False


@dataclass(frozen=True)
class IgnoreConfig:
    patterns: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORE_PATTERNS))


@dataclass(frozen=True)
class Config:
    repo_root: Path
    project: ProjectConfig
    git: GitConfig = field(default_factory=GitConfig)
    backend: BackendConfig = field(default_factory=BackendConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)
    ignore: IgnoreConfig = field(default_factory=IgnoreConfig)

    @property
    def config_path(self) -> Path:
        return self.repo_root / ".ol-sync" / "config.toml"

    def resolve_repo_path(self, value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else self.repo_root / path


def default_config(
    repo_root: Path,
    *,
    host: str = "http://localhost",
    project_id: str,
    project_name: str = "overleaf-project",
    backend_type: str = "http",
    ssl_verify: bool = True,
) -> Config:
    return Config(
        repo_root=repo_root,
        project=ProjectConfig(host=host, project_id=project_id, project_name=project_name),
        backend=BackendConfig(type=backend_type, ssl_verify=ssl_verify),
    )


def load_config(repo_root: Path) -> Config:
    config_path = repo_root / ".ol-sync" / "config.toml"
    if not config_path.exists():
        raise ConfigError(f"Missing config file: {config_path}")
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Invalid config file: {config_path}: {exc}") from exc
    try:
        project = data["project"]
    except KeyError as exc:
        raise ConfigError("Missing [project] section in .ol-sync/config.toml") from exc
    git_data = dict(data.get("git", {}))
    git_data.pop("remote_name", None)
    git_data.pop("commit_remote_snapshots", None)
    ignore_patterns = list(data.get("ignore", {}).get("patterns", DEFAULT_IGNORE_PATTERNS))
    if (
        len(ignore_patterns) == len(DEFAULT_IGNORE_PATTERNS) + 3
        and set(ignore_patterns)
        == set(DEFAULT_IGNORE_PATTERNS) | set(LEGACY_AMBIGUOUS_IGNORE_PATTERNS)
    ):
        ignore_patterns = list(DEFAULT_IGNORE_PATTERNS)
    try:
        return Config(
            repo_root=repo_root,
            project=ProjectConfig(
                host=str(project.get("host", "http://localhost")),
                project_id=str(project["project_id"]),
                project_name=str(project.get("project_name", project["project_id"])),
            ),
            git=GitConfig(**git_data),
            backend=BackendConfig(**data.get("backend", {})),
            auth=AuthConfig(**data.get("auth", {})),
            sync=SyncConfig(**data.get("sync", {})),
            ignore=IgnoreConfig(patterns=ignore_patterns),
        )
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"Invalid config values in {config_path}: {exc}") from exc


def write_default_config(config: Config, *, force: bool = False) -> None:
    path = config.config_path
    if path.exists() and not force:
        raise ConfigError(f"Config already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    def quote(value: object) -> str:
        return json.dumps(str(value), ensure_ascii=False)

    patterns = ",\n  ".join(quote(pattern) for pattern in config.ignore.patterns)
    text = f"""[project]
host = {quote(config.project.host)}
project_id = {quote(config.project.project_id)}
project_name = {quote(config.project.project_name)}

[git]
main_branch = {quote(config.git.main_branch)}
remote_branch = {quote(config.git.remote_branch)}
require_clean_worktree_before_push = {str(config.git.require_clean_worktree_before_push).lower()}

[backend]
type = {quote(config.backend.type)}
timeout = {config.backend.timeout}
ssl_verify = {str(config.backend.ssl_verify).lower()}

[auth]
profile = {quote(config.auth.profile)}
session_file = {quote(config.auth.session_file)}

[sync]
lock_file = {quote(config.sync.lock_file)}
snapshot_dir = {quote(config.sync.snapshot_dir)}
last_synced_file = {quote(config.sync.last_synced_file)}
last_remote_snapshot_file = {quote(config.sync.last_remote_snapshot_file)}
dry_run_default = {str(config.sync.dry_run_default).lower()}

[ignore]
patterns = [
  {patterns}
]
"""
    atomic_write_text(path, text, mode=0o600)


def ensure_gitignore_entry(repo_root: Path, entry: str) -> None:
    path = repo_root / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if entry in {line.strip() for line in existing.splitlines()}:
        return
    separator = "" if not existing or existing.endswith("\n") else "\n"
    atomic_write_text(path, f"{existing}{separator}{entry}\n", mode=0o644)


def ensure_default_gitignore(repo_root: Path) -> None:
    path = repo_root / ".gitignore"
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        existing_lines = existing.splitlines()
    else:
        existing = ""
        existing_lines = []

    existing_set = {line.strip() for line in existing_lines if line.strip()}
    legacy_entries = set(LEGACY_AMBIGUOUS_IGNORE_PATTERNS)
    if set(DEFAULT_GITIGNORE_LINES).issubset(existing_set) and legacy_entries.issubset(
        existing_set
    ):
        existing_lines = [
            line for line in existing_lines if line.strip() not in legacy_entries
        ]
        existing = "\n".join(existing_lines)
        if existing:
            existing += "\n"
        existing_set -= legacy_entries
    missing = [line for line in DEFAULT_GITIGNORE_LINES if line not in existing_set]
    if not missing:
        if path.exists() and legacy_entries.isdisjoint(existing_set):
            atomic_write_text(path, existing, mode=0o644)
        return

    if not existing:
        atomic_write_text(path, "\n".join(missing) + "\n", mode=0o644)
        return

    separator = "\n" if not existing.endswith("\n") else ""
    if existing_lines and existing_lines[-1].strip():
        separator += "\n"
    atomic_write_text(
        path,
        existing + separator + "\n".join(missing) + "\n",
        mode=0o644,
    )


def effective_ignore_patterns(config: Config) -> list[str]:
    # Sync filtering is intentionally independent from .gitignore. Git-ignored
    # untracked files are excluded when collecting the local worktree, while
    # tracked files remain syncable exactly as Git treats them.
    mandatory = [".git/", ".ol-sync/", ".env"]
    return list(dict.fromkeys([*mandatory, *config.ignore.patterns]))
