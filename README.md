# overleaf-ce-agent-sync

Agent-safe Git-style synchronization for self-hosted Overleaf Community Edition.

中文文档: [README.zh-CN.md](README.zh-CN.md)

Use local Git as the safety layer between AI coding agents and Overleaf CE. The
tool treats Overleaf as a single remote project snapshot, imports that snapshot
into a managed local Git branch, lets Git detect conflicts, and only writes back
after a freshness check and verification pass.

This is not a replacement for the official Overleaf Server Pro Git integration.
It is an MVP synchronization tool for Community Edition workflows where agents
edit a local repository.

## Current MVP Status

This repository currently provides:

- `ol init`
- `ol pull`
- `ol push`
- `ol status`
- `ol verify`
- `ol auth login/status/logout`
- An HTTP/session-cookie backend for self-hosted Overleaf CE
- A backend interface for future `pyoverleaf` implementations

The HTTP backend uses the same web/session behavior as the Overleaf editor. It
stores cookies in `.ol-sync/session.json`, which is ignored by Git.

## Typical Workflow

```bash
cd ~/papers/my-paper
ol pull
# let an agent or editor modify .tex/.bib/.sty/.cls files
ol status
git diff --cached
git commit -m "overleaf: import latest remote snapshot"
git diff
git add -A
git commit -m "agent: revise introduction"
ol push --dry-run
ol push
```

`ol pull` now stages remote changes instead of auto-creating a merge commit. You
review the staged merge result, then commit it yourself. If the remote snapshot
is already fully merged, `ol pull` exits cleanly without staging anything.

## Commands

### `ol init`

Initialize sync metadata in the current directory.

- Creates `.ol-sync/config.toml`
- Creates or appends `.gitignore` with `.ol-sync/` and common LaTeX build
  artifacts such as `*.aux`, `*.log`, `*.synctex.gz`, `*.run.xml`, and `*.pdf`
- Authenticates against the configured Overleaf CE host
- Downloads the first remote snapshot into the managed `overleaf-remote` branch
- Sets up the working branch and records sync metadata

Example:

```bash
ol init --host http://localhost --project-id YOUR_PROJECT_ID --project-name my-paper
```

### `ol pull`

Download the latest Overleaf snapshot into `overleaf-remote`, then stage the
merge into your current branch without committing it.

- Requires a clean working tree before it starts
- Leaves merge results in the index for review
- Stops on Git conflicts and never silently overwrites local work
- Updates the managed remote snapshot branch even when the working branch still
  needs your review

Example:

```bash
ol pull
git diff --cached
git commit -m "overleaf: import latest remote snapshot"
```

### `ol push`

Apply committed local changes back to Overleaf through the backend adapter.

- Requires a clean working tree by default
- Runs a freshness pull first
- Refuses to continue if that freshness pull stages newer remote changes
- Prints a push plan before writing to Overleaf
- Supports `--dry-run` to preview operations without changing the remote project
- Verifies the remote snapshot after upload before updating sync metadata

Examples:

```bash
ol push --dry-run
ol push
```

### `ol status`

Print a sync summary for the current repository.

- Current branch
- Whether the working tree is clean
- Last synced commit
- Last imported remote snapshot commit
- Pending conflicts
- Files changed locally since the latest imported remote snapshot

Example:

```bash
ol status
```

### `ol verify`

Download the latest Overleaf snapshot and compare it with the normalized local
project tree.

- Prints added, modified, and missing paths
- Exits non-zero when differences exist unless `--allow-diff` is passed

Example:

```bash
ol verify
```

### `ol auth login`

Save an authenticated Overleaf web session for later sync commands.

Use password login:

```bash
ol auth login --host http://localhost --email you@example.com
```

Or reuse a browser cookie:

```bash
ol auth login --host http://localhost --cookie 'sharelatex.sid=...'
```

### `ol auth status`

Check whether the saved session is still valid.

```bash
ol auth status --host http://localhost
```

### `ol auth logout`

Delete the saved local session file.

```bash
ol auth logout
```

## Configuration

Example `.ol-sync/config.toml`:

```toml
[project]
host = "http://localhost"
project_id = "YOUR_PROJECT_ID"
project_name = "my-paper"

[backend]
type = "http"

[auth]
session_file = ".ol-sync/session.json"
```

## Development

```bash
uv venv --python 3.13
uv pip install -e ".[dev]"
.venv/bin/python -m pytest
.venv/bin/ruff check .
.venv/bin/ol --help
```
