---
name: overleaf-git-sync
description: Safe instructions for agents using the `ol` command to sync Overleaf projects with a local Git workflow.
---

# overleaf-git-sync skill

## Purpose

This file teaches coding agents how to use the `ol` command safely when working
on an Overleaf project through a local Git repository.

The core model is:

```text
Overleaf project <-> ol <-> local Git repository <-> agent / editor
```

Treat Overleaf as the remote project state. Treat the local Git repository as
the real working area.

## Core Rule

```text
Never push stale local content over newer Overleaf content.
```

Always prefer the normal Git-style flow:

```bash
ol pull
git diff --cached
git commit -m "overleaf: import latest remote snapshot"
# edit locally
git add -A
git commit -m "agent: make requested changes"
ol push --dry-run
ol push
```

## What Agents Should Do

### 1. Find the tool repo and activate its environment

Before running `ol`, the agent should first locate the `overleaf-git-sync`
repository, activate the virtual environment where `ol` was installed, and only
then switch to the target paper directory.

Typical setup:

```bash
cd /path/to/overleaf-git-sync
source .venv/bin/activate
cd /path/to/paper-project
ol --help
```

Do not assume `ol` is available before the environment is activated.

### 2. Log in once per project directory

For local or self-hosted Overleaf:

```bash
ol auth login --host http://localhost --email you@example.com
```

For the official Overleaf site, cookie login is usually more reliable:

```bash
ol auth login --host https://www.overleaf.com --cookie 'overleaf_session2=...'
```

This stores the session in `.ol-sync/session.json`.

### 3. Initialize the project directory

```bash
ol init --project-id YOUR_PROJECT_ID --project-name my-paper
```

Notes:

- If the current directory does not have its own `.git`, `ol init` will run
  `git init`.
- If `.ol-sync/config.toml` already exists, `ol init` overwrites it by default.
- Use `--keep-config` if the existing config should be kept.

### 4. Pull before editing

Always pull before changing files:

```bash
ol pull
```

If `ol pull` stages remote changes, inspect and commit them first:

```bash
git diff --cached
git commit -m "overleaf: import latest remote snapshot"
```

### 5. Edit only after pull is clean

Typical editable files:

- `.tex`
- `.bib`
- `.sty`
- `.cls`
- `.bst`
- `.md`
- `.txt`
- `.csv`

After editing:

```bash
git diff
git add -A
git commit -m "agent: update manuscript"
```

### 6. Push carefully

Preview first:

```bash
ol push --dry-run
```

Then push:

```bash
ol push
```

Use fast mode only when the user explicitly accepts the risk and the project is
effectively single-user:

```bash
ol push --fast
```

`--fast` skips the freshness pull, but still verifies after push.

### 7. Check or verify when needed

Quick local status:

```bash
ol status
```

Full remote comparison:

```bash
ol verify
```

## What Agents Should Not Do

- Do **not** edit Overleaf internal storage directly.
- Do **not** skip `ol pull` in a normal workflow.
- Do **not** push with unresolved Git conflicts.
- Do **not** push with a dirty worktree.
- Do **not** assume `ol status` means the remote is fresh; use `ol verify` for
  real remote comparison.
- Do **not** use `ol push --fast` unless the user explicitly wants speed over
  the extra freshness check.

## Recommended Agent Workflow

For most tasks, use this exact sequence:

```bash
ol pull
git diff --cached
# if staged remote changes exist:
git commit -m "overleaf: import latest remote snapshot"

# make local edits
git diff
git add -A
git commit -m "agent: requested changes"

ol push --dry-run
ol push
```

## Conflict Handling

If `ol pull` or `ol push` reports conflicts:

1. Stop.
2. Inspect `git status`.
3. Resolve the conflicted files locally.
4. Commit the resolution.
5. Retry `ol push`.

Example:

```bash
git status
git add conflicted-file.tex
git commit -m "resolve Overleaf sync conflict"
ol push
```

## Minimal Command Reference

- `ol auth login` — save Overleaf login session
- `ol auth status` — check whether the saved session still works
- `ol auth logout` — delete the saved local session
- `ol init` — initialize sync metadata in the current directory
- `ol pull` — import the latest remote snapshot and stage it for review
- `ol push` — push committed local changes back to Overleaf
- `ol push --fast` — skip the pre-push freshness pull
- `ol status` — show local sync state
- `ol verify` — compare local contents with the latest remote snapshot
