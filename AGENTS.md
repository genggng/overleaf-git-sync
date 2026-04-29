# AGENTS.md

## Project: overleaf-ce-agent-sync

## Status: V1 Implemented

`overleaf-ce-agent-sync` is now a working V1 Python CLI for agent-safe synchronization between a self-hosted Overleaf Community Edition project and a local Git repository.

The tool is intentionally not a Git remote helper and not a replacement for Overleaf Server Pro Git integration. It treats Overleaf CE as a remote project state source and publishing target, while local Git remains the conflict-resolution and review layer.

Core data flow:

```text
Overleaf CE project  <-->  ol-ce-sync HTTP backend  <-->  local Git repo  <-->  agent / editor
```

The highest-priority invariant remains:

```text
Never silently overwrite newer Overleaf changes with stale local agent output.
```

---

## V1 User Workflow

Authenticate once:

```bash
ol-ce-sync auth login --host http://localhost --email you@example.com
ol-ce-sync auth status --host http://localhost
```

If password login is blocked by captcha, SSO, or local deployment policy, log in through the browser and pass a raw Cookie header:

```bash
ol-ce-sync auth login --host http://localhost --cookie 'sharelatex.sid=...'
```

Initialize a local repo against a real Overleaf project:

```bash
ol-ce-sync init --host http://localhost --project-id YOUR_PROJECT_ID --project-name my-paper
```

Normal edit loop:

```bash
ol-ce-sync pull
# agent/editor modifies .tex/.bib/.sty/.cls/etc.
ol-ce-sync status
git diff
git add -A
git commit -m "agent: revise introduction"
ol-ce-sync push --dry-run
ol-ce-sync push
```

If Git reports conflicts, resolve locally, commit, then retry:

```bash
git status
git add conflicted-file.tex
git commit -m "resolve Overleaf sync conflict"
ol-ce-sync push
```

---

## Implemented Commands

### `ol-ce-sync auth login`

Creates `.ol-sync/session.json` containing Overleaf session cookies. This file is ignored by Git and must never be committed.

Supported modes:

- `--email` plus password prompt, using Overleaf `/login`
- `--cookie`, using a raw browser Cookie header

### `ol-ce-sync auth status`

Checks the saved session against Overleaf `/user/personal_info`.

### `ol-ce-sync auth logout`

Deletes the local session file.

### `ol-ce-sync init`

Initializes sync metadata and Git state for a real Overleaf CE project.

Current behavior:

1. Requires `--project-id`; there is no fake default project.
2. Writes `.ol-sync/config.toml`.
3. Validates the saved HTTP session.
4. Downloads the remote project zip snapshot.
5. Initializes Git if needed.
6. Imports the remote snapshot into `overleaf-remote`.
7. Creates or switches back to `main`.
8. Merges `overleaf-remote` into `main`.
9. Records sync metadata under `.ol-sync/`.

`--force` is required to initialize in a non-empty non-Git directory.

### `ol-ce-sync pull`

Downloads the latest Overleaf snapshot, imports it into `overleaf-remote`, and merges it into the current working branch.

Conservative behavior:

- Acquires `.ol-sync/lock`.
- Aborts on a dirty worktree unless `--autostash` is passed.
- Uses Git merge for conflict detection.
- Stops and reports conflicted files on merge conflict.

### `ol-ce-sync push`

Applies committed local changes back to Overleaf.

Current behavior:

1. Acquires `.ol-sync/lock`.
2. Requires a clean worktree by default.
3. Performs an internal freshness pull before generating the push plan.
4. Computes changes with `git diff --name-status --find-renames`.
5. Prints the planned operations.
6. Honors `--dry-run`.
7. Applies file operations through the HTTP backend.
8. Downloads a fresh Overleaf snapshot.
9. Verifies remote normalized state against local normalized state.
10. Updates sync metadata only after verification succeeds.

### `ol-ce-sync status`

Prints local sync state:

- current branch
- clean/dirty worktree
- last synced commit
- last remote snapshot commit
- conflicted files
- local changes since the latest remote snapshot

### `ol-ce-sync verify`

Downloads the latest Overleaf snapshot and compares it with the local normalized project tree. Exits nonzero on mismatch unless `--allow-diff` is passed.

---

## Current Config Format

Repo-local config lives at:

```text
.ol-sync/config.toml
```

Example:

```toml
[project]
host = "http://localhost"
project_id = "YOUR_PROJECT_ID"
project_name = "my-paper"

[git]
main_branch = "main"
remote_branch = "overleaf-remote"
remote_name = "origin"
require_clean_worktree_before_push = true
commit_remote_snapshots = true

[backend]
type = "http"
timeout = 16
ssl_verify = true

[auth]
profile = "default"
session_file = ".ol-sync/session.json"

[sync]
lock_file = ".ol-sync/lock"
snapshot_dir = ".ol-sync/snapshots"
last_synced_file = ".ol-sync/last_synced_commit"
last_remote_snapshot_file = ".ol-sync/last_remote_snapshot_commit"
dry_run_default = false

[ignore]
patterns = [
  ".git/",
  ".ol-sync/",
  "*.aux",
  "*.bbl",
  "*.blg",
  "*.fdb_latexmk",
  "*.fls",
  "*.log",
  "*.out",
  "*.synctex.gz",
  "*.toc",
  "*.pdf"
]
```

Ignored local files:

```gitignore
.ol-sync/snapshots/
.ol-sync/tmp/
.ol-sync/session.json
.ol-sync/cookies.json
.ol-sync/*.lock
.env
.venv/
.uv-cache/
```

Do not store plaintext passwords in config. Do not commit cookies, session files, tokens, or `.env`.

---

## Code Design

Package root:

```text
src/ol_ce_sync/
```

Key modules:

- `cli.py`: argparse CLI and `auth` subcommands.
- `auth.py`: cookie session storage, password login, cookie login, CSRF extraction, session status checks.
- `config.py`: `.ol-sync/config.toml` dataclasses, defaults, load/write helpers.
- `sync_engine.py`: transaction-level orchestration for `init`, `pull`, `push`, `status`, and `verify`.
- `git_ops.py`: all Git subprocess calls; Git remains the only merge/conflict engine.
- `snapshot.py`: safe zip extraction, ignore matching, normalized tree collection, tree comparison.
- `diff.py`: maps Git name-status diff to push operations.
- `lock.py`: `.ol-sync/lock` transaction lock.
- `errors.py`: user-facing domain exceptions.
- `utils/paths.py`: project path normalization and traversal rejection.
- `utils/text.py`: text/binary classification.
- `utils/logging.py`: simple `[ol-ce-sync]` output.

Backend modules:

- `backends/base.py`: `OverleafBackend` protocol.
- `backends/http_backend.py`: V1 real backend for self-hosted Overleaf CE.
- `backends/pyoverleaf_backend.py`: placeholder only; it raises a clear unsupported error.
- `backends/__init__.py`: backend factory.

There is intentionally no mock backend in V1. Development should target the real HTTP backend and keep unit tests focused on local logic that does not require a live Overleaf server.

---

## HTTP Backend Behavior

`HttpBackend` uses Overleaf CE web/session behavior:

- downloads project snapshots from `/project/{project_id}/download/zip`
- validates sessions through `/user/personal_info`
- gets CSRF tokens from the project page meta tag `ol-csrfToken`
- opens the Overleaf socket.io project session to read the editor entity tree and entity IDs
- creates folders through `/project/{project_id}/folder`
- uploads files through `/Project/{project_id}/upload?folder_id=...`
- deletes entities through `/project/{project_id}/{entity_type}/{entity_id}`
- renames/moves entities through editor HTTP routes

Updating an existing remote file is implemented conservatively:

```text
upload temp replacement -> rename old file to backup -> rename temp to final name -> delete backup
```

The sync engine still performs a full snapshot verification after push, and does not mark sync success until verification passes.

---

## Git Model

The tool manages two conceptual branches:

```text
main              user working branch
overleaf-remote   latest imported Overleaf CE snapshot
```

The `overleaf-remote` branch is tool-managed. Agents and users should not edit it manually.

Snapshot import behavior:

1. switch/create `overleaf-remote`
2. replace syncable repo contents with the normalized remote snapshot
3. commit snapshot, allowing an empty initial snapshot commit when necessary
4. switch back to the working branch
5. merge `overleaf-remote`

All merge conflict detection is delegated to Git.

---

## Safety Rules

Hard rules for future changes:

1. Do not directly modify Docker volumes, MongoDB, Redis, or Overleaf compilation caches.
2. Do not implement character-level or save-level realtime sync.
3. Do not bypass Git for conflict detection.
4. Do not push with unresolved Git conflicts.
5. Do not push with a dirty worktree unless an explicit option allows it.
6. Do not mark push success until remote snapshot verification passes.
7. Do not silently ignore failed Overleaf file operations.
8. Do not auto-resolve LaTeX merge conflicts using heuristics.
9. Do not commit credentials, cookies, tokens, `.env`, or session files.
10. Prefer aborting with a clear error over guessing when remote state is ambiguous.

Conflict message shape should stay direct:

```text
Sync conflict detected.
Conflicted files:
  - main.tex
  - sections/method.tex

Resolve conflicts locally, commit the result, then run:
  ol-ce-sync push
```

---

## Path and Snapshot Safety

Remote zip extraction must:

- reject absolute paths
- reject `..` traversal after normalization
- normalize path separators to `/`
- preserve Unicode filenames
- never write into `.git/` or `.ol-sync/`
- apply ignore rules before import/compare

Generated LaTeX artifacts remain ignored by default.

---

## Testing and Verification

Use the project-local uv environment:

```bash
uv venv --python 3.13
uv pip install -e ".[dev]"
.venv/bin/python -m pytest
.venv/bin/ruff check .
```

Current tests cover:

- auth session JSON round-trip
- CSRF token extraction
- HTTP backend entity flattening and conservative replacement ordering
- safe zip extraction
- ignore rules and normalized tree comparison
- text/binary detection
- Git snapshot import, including empty initial snapshot commits
- Git merge conflict detection
- Git diff mapping for A/M/D/R

Tests do not require a live Overleaf CE instance. End-to-end verification against a real Overleaf deployment should be performed manually with a disposable project before changing write behavior.

---

## Development Preferences

- Python 3.11+.
- Use `argparse` for CLI unless there is a strong reason to add CLI dependencies.
- Use `subprocess.run` for Git; do not add GitPython.
- Use `pathlib.Path` for paths.
- Use `tomllib` for reading TOML.
- Keep dependencies minimal and justified.
- Current runtime dependencies are `requests` and `websocket-client`.
- Use `pytest` and `ruff` for verification.

When adding sync behavior, add tests for the local deterministic pieces and document any manual real-Overleaf verification that cannot be automated.

---

## Terminology

Use these terms consistently:

- `remote snapshot`: latest downloaded state of the Overleaf CE project.
- `local repo`: the user's Git working repository.
- `working branch`: usually `main`.
- `remote branch`: local Git branch managed by this tool, usually `overleaf-remote`.
- `sync transaction`: one pull/merge or push/verify operation protected by a lock.
- `backend adapter`: code that talks to Overleaf.
- `push plan`: the file operation list generated from Git diff.
