# overleaf-git-sync

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

## End-to-End Workflow

This is the recommended full flow from an empty local folder to a successful
push back to Overleaf.

### 1. Log in to Overleaf CE

Run login inside the local project directory. The session is stored in
`.ol-sync/session.json`, and `ol init` will reuse the host from that saved
session, so you usually do not need to pass `--host` again.

```bash
mkdir -p ~/papers/my-paper
cd ~/papers/my-paper
ol auth login --host http://localhost --email you@example.com
```

If password login is blocked by captcha or SSO, reuse a browser cookie:

```bash
ol auth login --host http://localhost --cookie 'sharelatex.sid=...'
```

### 2. Initialize the local sync repository

```bash
ol init --project-id YOUR_PROJECT_ID --project-name my-paper
```

This step:

- creates `.ol-sync/config.toml`
- creates or updates `.gitignore`
- runs `git init` only when the current directory does not already have its own
  `.git`
- downloads the initial remote snapshot
- creates the managed `overleaf-remote` branch

If `.ol-sync/config.toml` already exists, `ol init` overwrites it by default.
Use `--keep-config` to keep the existing config file.

### 3. Pull the latest remote snapshot

Always pull before editing:

```bash
ol pull
git diff --cached
git commit -m "overleaf: import latest remote snapshot"
```

`ol pull` stages remote changes instead of auto-creating a merge commit. If the
remote snapshot is already fully merged, it exits without staging anything.

### 4. Edit locally and commit

```bash
$EDITOR main.tex
git diff
git add -A
git commit -m "agent: revise introduction"
```

### 5. Push back to Overleaf

Preview the plan first:

```bash
ol push --dry-run
```

Then apply it:

```bash
ol push
```

`ol push` performs a freshness pull first. If Overleaf changed again while you
were editing, it stops and asks you to handle the new staged changes or merge
conflicts locally before continuing.

If you are the only person editing the project and you know your local
`overleaf-remote` branch is already fresh, you can skip that pre-push refresh:

```bash
ol push --fast
```

`--fast` skips the freshness pull, but still keeps the clean-worktree check and
the post-push verification step.

### 6. Check status or verify

```bash
ol status
ol verify
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
