# overleaf-git-sync

Map an online Overleaf project to a local Git-backed working repository.

![Introduction diagram](images/introduction.png)

中文文档: [README.zh-CN.md](README.zh-CN.md)

Use local Git as the safety layer between your editor and Overleaf. The tool
treats Overleaf as a remote project snapshot, imports that snapshot into a
managed local Git branch, lets Git detect conflicts, and only writes back after
a freshness check and verification pass.

This is not a replacement for the official Overleaf Git integration. It is an
MVP tool for working on an online Overleaf project through a normal local Git
repository.

## Current MVP Status

Important commands:

- `ol auth login` — save a logged-in Overleaf session in `.ol-sync/session.json`
- `ol init` — create local sync metadata and connect the current folder to an
  Overleaf project
- `ol pull` — import the latest remote snapshot and stage it for review
- `ol push` — push committed local changes back to Overleaf after a freshness
  check
- `ol push --fast` — skip the freshness pull when you know the local remote
  snapshot is already up to date
- `ol status` — show the current branch, sync metadata, and local changes
- `ol verify` — download the latest remote snapshot and compare it with the
  local tree

## Installation

Install the tool once into a virtual environment, then use `ol` from any
Overleaf project directory after activating that environment.

From this repository root:

```bash
uv venv --python 3.13
uv pip install -e .
source .venv/bin/activate
ol --help
```

After activation, `ol` is available on your shell `PATH`. You do **not** need to
stay in this repository directory. A typical flow is:

```bash
cd /path/to/overleaf-git-sync
source .venv/bin/activate
cd ~/papers/my-paper
ol auth login --host http://localhost --email you@example.com
```

## End-to-End Workflow

This is the recommended full flow from an empty local folder to a successful
push back to Overleaf.

### 1. Log in to Overleaf

Run login inside the local project directory. The session is stored in
`.ol-sync/session.json`, and `ol init` will reuse the host from that saved
session, so you usually do not need to pass `--host` again.

For local Docker or self-hosted deployments, password login is usually the
simplest option:

```bash
mkdir -p ~/papers/my-paper
cd ~/papers/my-paper
ol auth login --host http://localhost --email you@example.com
```

For the official Overleaf site (`https://www.overleaf.com/`), browser-cookie
login is recommended. Log in on the web first, then reuse the cookie value,
typically `overleaf_session2=...`:

```bash
ol auth login --host https://www.overleaf.com --cookie 'overleaf_session2=...'
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

## Using with Agents

If you want a coding agent to help revise the paper, you can also give the
agent this repository's `SKILL.md` file. It explains the safe `ol` workflow,
including when to run `ol pull`, how to review staged remote changes, and how
to push back without overwriting newer Overleaf edits.

## Development

If you are developing `overleaf-git-sync` itself, install the development
dependencies as well:

```bash
uv venv --python 3.13
uv pip install -e ".[dev]"
.venv/bin/python -m pytest
.venv/bin/ruff check .
.venv/bin/ol --help
```
