"""Repo-local configuration loading and writing."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ol_ce_sync.errors import ConfigError

LATEX_AUXILIARY_PATTERNS = [
    "*.aux",
    "*.bbl",
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
    "*.ist",
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
    "*.pdf",
]

DEFAULT_IGNORE_PATTERNS = [
    ".git/",
    ".gitignore",
    ".env",
    ".ol-sync/",
    *LATEX_AUXILIARY_PATTERNS,
]

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
    remote_name: str = "origin"
    require_clean_worktree_before_push: bool = True
    commit_remote_snapshots: bool = True


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
) -> Config:
    return Config(
        repo_root=repo_root,
        project=ProjectConfig(host=host, project_id=project_id, project_name=project_name),
        backend=BackendConfig(type=backend_type),
    )


def load_config(repo_root: Path) -> Config:
    config_path = repo_root / ".ol-sync" / "config.toml"
    if not config_path.exists():
        raise ConfigError(f"Missing config file: {config_path}")
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    try:
        project = data["project"]
    except KeyError as exc:
        raise ConfigError("Missing [project] section in .ol-sync/config.toml") from exc
    ignore_patterns = data.get("ignore", {}).get("patterns", DEFAULT_IGNORE_PATTERNS)
    return Config(
        repo_root=repo_root,
        project=ProjectConfig(
            host=str(project.get("host", "http://localhost")),
            project_id=str(project["project_id"]),
            project_name=str(project.get("project_name", project["project_id"])),
        ),
        git=GitConfig(**data.get("git", {})),
        backend=BackendConfig(**data.get("backend", {})),
        auth=AuthConfig(**data.get("auth", {})),
        sync=SyncConfig(**data.get("sync", {})),
        ignore=IgnoreConfig(patterns=list(ignore_patterns)),
    )


def write_default_config(config: Config, *, force: bool = False) -> None:
    path = config.config_path
    if path.exists() and not force:
        raise ConfigError(f"Config already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    patterns = ",\n  ".join(f'"{pattern}"' for pattern in config.ignore.patterns)
    text = f"""[project]
host = "{config.project.host}"
project_id = "{config.project.project_id}"
project_name = "{config.project.project_name}"

[git]
main_branch = "{config.git.main_branch}"
remote_branch = "{config.git.remote_branch}"
remote_name = "{config.git.remote_name}"
require_clean_worktree_before_push = true
commit_remote_snapshots = true

[backend]
type = "{config.backend.type}"
timeout = {config.backend.timeout}
ssl_verify = true

[auth]
profile = "{config.auth.profile}"
session_file = "{config.auth.session_file}"

[sync]
lock_file = "{config.sync.lock_file}"
snapshot_dir = "{config.sync.snapshot_dir}"
last_synced_file = "{config.sync.last_synced_file}"
last_remote_snapshot_file = "{config.sync.last_remote_snapshot_file}"
dry_run_default = false

[ignore]
patterns = [
  {patterns}
]
"""
    path.write_text(text, encoding="utf-8")


def ensure_default_gitignore(repo_root: Path) -> None:
    path = repo_root / ".gitignore"
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        existing_lines = existing.splitlines()
    else:
        existing = ""
        existing_lines = []

    existing_set = {line.strip() for line in existing_lines if line.strip()}
    missing = [line for line in DEFAULT_GITIGNORE_LINES if line not in existing_set]
    if not missing:
        return

    if not existing:
        path.write_text("\n".join(missing) + "\n", encoding="utf-8")
        return

    separator = "\n" if not existing.endswith("\n") else ""
    if existing_lines and existing_lines[-1].strip():
        separator += "\n"
    path.write_text(existing + separator + "\n".join(missing) + "\n", encoding="utf-8")
