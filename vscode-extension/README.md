# Overleaf Git Sync for VS Code

MVP VS Code interface for the `ol` command from
[`overleaf-git-sync`](https://github.com/genggng/overleaf-git-sync).

The extension intentionally delegates authentication, Git conflict detection,
remote freshness checks, push journaling, OT updates, and verification to the
Python CLI. It does not implement a second synchronization engine.

## Requirements

Install `overleaf-git-sync` once as an isolated global command:

```bash
uv tool install --editable /path/to/overleaf-ce-agent-sync
uv tool update-shell
```

If a GUI-launched VS Code does not inherit the shell `PATH`, point the extension
at the stable tool executable rather than the repository `.venv`:

```json
{
  "overleafGitSync.executable": "/Users/you/.local/bin/ol"
}
```

Open a trusted local workspace. Sync commands are disabled in VS Code Restricted
Mode because they execute `ol` and modify the local Git repository and remote
Overleaf project.

## Sidebar GUI

Open the **Overleaf** icon in the Activity Bar. The native Sync Status view
shows, per project:

- current Git branch and clean/dirty working-tree state;
- merge conflicts and interrupted-push recovery state;
- committed file operations waiting to be pushed;
- latest successful sync time and whether remote freshness has been checked;
  the corresponding commit remains available in the time row's tooltip.

The view toolbar and project-row buttons provide Pull, Preview Push, Push,
Verify, Recover, and Refresh actions. Local status refreshes on Git/file events
and every five seconds by default. Configure or disable polling with
`overleafGitSync.refreshIntervalSeconds`.

The GUI requires `ol` 0.2.2 or newer because it consumes the sync timestamp
from `ol status --json`.

## Commands

- `Overleaf: Initialize Project`
- `Overleaf: Log In`
- `Overleaf: Check Login`
- `Overleaf: Show Sync Status`
- `Overleaf: Pull`
- `Overleaf: Preview Push`
- `Overleaf: Push`
- `Overleaf: Verify`
- `Overleaf: Recover Interrupted Push`
- `Overleaf: Show Output`
- `Overleaf: Refresh Projects`

The extension discovers projects by locating `.ol-sync/config.toml`, supports
multi-root workspaces, serializes commands per project, shows progress in VS
Code, mirrors important state in the global status bar, and writes complete
command output to the `Overleaf Git Sync` channel.

`Push` always runs `ol push --dry-run` first and requires explicit confirmation.
It never automatically pushes on save.

## Development

```bash
cd vscode-extension
npm test
npm install --cache .npm-cache
npm run package
```

The extension runtime and tests have no npm dependencies. `npm install` is only
needed to fetch `@vscode/vsce` before creating a VSIX. The project-local cache
avoids depending on permissions in the user's global npm cache.

Open the repository root in VS Code and press `F5` using the included
**Run Overleaf Git Sync Extension** launch configuration, or install the
generated `.vsix` with **Extensions: Install from VSIX...**.

## Current limitations

- The `ol` CLI must be installed separately.
- Remote freshness is never inferred from local state; use Verify or Pull when
  an actual remote check is required.
- Marketplace publishing and automatic CLI installation are not included.
