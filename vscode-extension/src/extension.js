"use strict";

const path = require("node:path");
const vscode = require("vscode");
const { formatCommand, ProcessStartError, runProcess } = require("./process");
const { parseStatusJson } = require("./status");
const { StatusTreeProvider } = require("./tree");
const {
  discoverProjects,
  pickProject,
  pickProjectOrWorkspace,
  pickWorkspaceFolder,
  projectFromFolder,
} = require("./projects");

class OverleafController {
  constructor() {
    this.output = vscode.window.createOutputChannel("Overleaf Git Sync", { log: true });
    this.statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 80);
    this.statusBar.name = "Overleaf Git Sync";
    this.statusBar.show();
    this.tree = new StatusTreeProvider();
    this.disposables = [];
    this.projects = [];
    this.statuses = new Map();
    this.running = new Set();
    this.refreshingStatus = false;
    this.refreshQueued = false;
    this.refreshTimer = undefined;
    this.refreshDebounce = undefined;
    this.disposed = false;
  }

  async activate() {
    this.register("overleafGitSync.init", () => this.initialize());
    this.register("overleafGitSync.authLogin", () => this.authLogin());
    this.register("overleafGitSync.authStatus", () => this.authStatus());
    this.register("overleafGitSync.status", (target) => this.showStatus(target));
    this.register("overleafGitSync.pull", (target) => this.projectCommand(
      ["pull"], "Pulling from Overleaf", false, target,
    ));
    this.register("overleafGitSync.pushDryRun", (target) => this.projectCommand(
      ["push", "--dry-run"], "Planning Overleaf push", true, target,
    ));
    this.register("overleafGitSync.push", (target) => this.push(target));
    this.register("overleafGitSync.verify", (target) => this.projectCommand(
      ["verify"], "Verifying Overleaf project", false, target,
    ));
    this.register("overleafGitSync.recover", (target) => this.recover(target));
    this.register("overleafGitSync.showOutput", () => this.output.show(true));
    this.register("overleafGitSync.refreshProjects", () => this.refreshProjects());

    const configWatcher = this.refreshWatcher("**/.ol-sync/config.toml", true);
    const journalWatcher = this.refreshWatcher("**/.ol-sync/push_journal.json");
    const gitIndexWatcher = this.refreshWatcher("**/.git/index");
    const gitHeadWatcher = this.refreshWatcher("**/.git/HEAD");
    const mergeWatcher = this.refreshWatcher("**/.git/MERGE_HEAD");
    this.disposables.push(
      vscode.window.registerTreeDataProvider("overleafGitSync.projects", this.tree),
      configWatcher,
      journalWatcher,
      gitIndexWatcher,
      gitHeadWatcher,
      mergeWatcher,
      vscode.workspace.onDidChangeWorkspaceFolders(() => void this.refreshProjects()),
      vscode.workspace.onDidGrantWorkspaceTrust(() => void this.refreshProjects()),
      vscode.workspace.onDidSaveTextDocument(() => this.scheduleStatusRefresh()),
      vscode.workspace.onDidCreateFiles(() => this.scheduleStatusRefresh()),
      vscode.workspace.onDidDeleteFiles(() => this.scheduleStatusRefresh()),
      vscode.workspace.onDidRenameFiles(() => this.scheduleStatusRefresh()),
      vscode.workspace.onDidChangeConfiguration((event) => {
        if (event.affectsConfiguration("overleafGitSync.refreshIntervalSeconds")) {
          this.startRefreshTimer();
        }
        if (event.affectsConfiguration("overleafGitSync.executable")) {
          this.scheduleStatusRefresh();
        }
      }),
    );
    this.startRefreshTimer();
    await this.refreshProjects();
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    for (const disposable of this.disposables) {
      disposable.dispose();
    }
    if (this.refreshTimer) clearInterval(this.refreshTimer);
    if (this.refreshDebounce) clearTimeout(this.refreshDebounce);
    this.tree.dispose();
    this.output.dispose();
    this.statusBar.dispose();
  }

  register(command, callback) {
    this.disposables.push(vscode.commands.registerCommand(command, async (...args) => {
      try {
        await callback(...args);
      } catch (error) {
        this.output.error(String(error));
        this.output.show(true);
        void vscode.window.showErrorMessage(`Overleaf Git Sync failed: ${String(error)}`);
      }
    }));
  }

  refreshWatcher(pattern, projectsChanged = false) {
    const watcher = vscode.workspace.createFileSystemWatcher(pattern);
    const refresh = () => projectsChanged
      ? void this.refreshProjects()
      : this.scheduleStatusRefresh();
    watcher.onDidCreate(refresh);
    watcher.onDidDelete(refresh);
    watcher.onDidChange(refresh);
    return watcher;
  }

  async refreshProjects() {
    this.projects = await discoverProjects();
    this.tree.setProjects(this.projects);
    await vscode.commands.executeCommand(
      "setContext", "overleafGitSync.hasProject", this.projects.length > 0,
    );
    if (!vscode.workspace.isTrusted) {
      this.statusBar.text = "$(shield) Overleaf: Restricted";
      this.statusBar.tooltip = "Trust this workspace to run ol commands.";
      this.statusBar.command = undefined;
    } else if (this.projects.length === 0) {
      this.statusBar.text = "$(cloud) Overleaf: Not initialized";
      this.statusBar.tooltip = "No .ol-sync/config.toml found.";
      this.statusBar.command = "overleafGitSync.init";
    } else {
      this.statusBar.text = "$(sync~spin) Overleaf: Reading status";
      await this.refreshStatuses();
    }
  }

  async refreshStatuses() {
    if (!vscode.workspace.isTrusted || this.projects.length === 0) return;
    if (this.refreshingStatus) {
      this.refreshQueued = true;
      return;
    }
    this.refreshingStatus = true;
    try {
      await Promise.all(this.projects.map(async (project) => {
        if (this.running.has(project.root.fsPath)) return;
        const executable = this.executableFor(project);
        if (!executable) {
          const error = "overleafGitSync.executable is empty.";
          this.statuses.set(project.root.fsPath, { error });
          this.tree.setStatus(project, undefined, error);
          return;
        }
        try {
          const result = await runProcess(executable, ["status", "--json"], {
            cwd: project.root.fsPath,
          });
          if (result.code !== 0) {
            throw new Error(result.stderr.trim() || `ol exited with code ${result.code}`);
          }
          const status = parseStatusJson(result.stdout);
          this.statuses.set(project.root.fsPath, { status });
          this.tree.setStatus(project, status, undefined);
        } catch (error) {
          const message = error instanceof ProcessStartError && error.causeCode === "ENOENT"
            ? `Cannot find '${executable}'. Configure overleafGitSync.executable.`
            : String(error);
          this.statuses.set(project.root.fsPath, { error: message });
          this.tree.setStatus(project, undefined, message);
        }
      }));
      this.updateStatusBar();
    } finally {
      this.refreshingStatus = false;
      if (this.refreshQueued) {
        this.refreshQueued = false;
        this.scheduleStatusRefresh();
      }
    }
  }

  updateStatusBar() {
    const loaded = this.projects
      .map((project) => this.statuses.get(project.root.fsPath)?.status)
      .filter(Boolean);
    const errors = this.projects.length - loaded.length;
    const recoveries = loaded.filter((status) => status.interrupted_push).length;
    const conflicts = loaded.reduce((count, status) => count + status.conflicts.length, 0);
    const dirty = loaded.filter((status) => !status.working_tree_clean).length;
    const changes = loaded.reduce((count, status) => count + status.changes.length, 0);
    if (recoveries > 0) {
      this.statusBar.text = `$(warning) Overleaf: ${recoveries} recovery needed`;
      this.statusBar.tooltip = "An interrupted push journal exists.";
      this.statusBar.command = "overleafGitSync.recover";
    } else if (conflicts > 0) {
      this.statusBar.text = `$(warning) Overleaf: ${conflicts} conflict(s)`;
      this.statusBar.tooltip = "Resolve Git conflicts before pushing.";
      this.statusBar.command = "overleafGitSync.status";
    } else if (dirty > 0) {
      this.statusBar.text = `$(edit) Overleaf: ${dirty} dirty project(s)`;
      this.statusBar.tooltip = "Uncommitted working-tree changes exist.";
      this.statusBar.command = "overleafGitSync.status";
    } else if (changes > 0) {
      this.statusBar.text = `$(cloud-upload) Overleaf: ${changes} to push`;
      this.statusBar.tooltip = "Committed changes are ahead of the remote snapshot.";
      this.statusBar.command = "overleafGitSync.status";
    } else if (errors > 0) {
      this.statusBar.text = "$(error) Overleaf: Status unavailable";
      this.statusBar.tooltip = "Open the Overleaf view for details.";
      this.statusBar.command = "overleafGitSync.status";
    } else {
      this.statusBar.text = "$(check) Overleaf: No local changes";
      this.statusBar.tooltip = "Remote freshness has not been checked.";
      this.statusBar.command = "overleafGitSync.status";
    }
  }

  scheduleStatusRefresh() {
    if (this.refreshDebounce) clearTimeout(this.refreshDebounce);
    this.refreshDebounce = setTimeout(() => {
      this.refreshDebounce = undefined;
      void this.refreshStatuses();
    }, 250);
  }

  startRefreshTimer() {
    if (this.refreshTimer) clearInterval(this.refreshTimer);
    const configured = vscode.workspace.getConfiguration("overleafGitSync")
      .get("refreshIntervalSeconds", 5);
    const seconds = Number.isFinite(configured) ? Math.max(0, configured) : 5;
    if (seconds > 0) {
      this.refreshTimer = setInterval(() => void this.refreshStatuses(), seconds * 1000);
    } else {
      this.refreshTimer = undefined;
    }
  }

  executableFor(project) {
    return vscode.workspace
      .getConfiguration("overleafGitSync", project.root)
      .get("executable", "ol").trim();
  }

  async showStatus() {
    if (!this.ensureTrusted()) return;
    await this.refreshStatuses();
    await vscode.commands.executeCommand("overleafGitSync.projects.focus");
  }

  async targetProject(target) {
    if (target?.project) return target.project;
    if (target?.root?.fsPath) return target;
    return pickProject(this.projects);
  }

  ensureTrusted() {
    if (vscode.workspace.isTrusted) {
      return true;
    }
    void vscode.window.showWarningMessage(
      "Trust this workspace before running Overleaf Git Sync commands.",
    );
    return false;
  }

  async initialize() {
    if (!this.ensureTrusted()) return;
    const folder = await pickWorkspaceFolder();
    if (!folder) return;
    if (this.projects.some((project) => project.root.fsPath === folder.uri.fsPath)) {
      void vscode.window.showWarningMessage(`${folder.name} is already initialized for Overleaf sync.`);
      return;
    }
    const projectId = await vscode.window.showInputBox({
      title: "Initialize Overleaf Git Sync",
      prompt: "Project ID (the segment after /project/ in the Overleaf URL)",
      validateInput: required("Project ID"),
      ignoreFocusOut: true,
    });
    if (!projectId) return;
    const projectName = await vscode.window.showInputBox({
      title: "Initialize Overleaf Git Sync",
      prompt: "Local descriptive project name",
      value: path.basename(folder.uri.fsPath),
      validateInput: required("Project name"),
      ignoreFocusOut: true,
    });
    if (!projectName) return;
    const host = await vscode.window.showInputBox({
      title: "Initialize Overleaf Git Sync",
      prompt: "Overleaf host; leave empty to use the saved login session",
      placeHolder: "https://overleaf.example.com",
      ignoreFocusOut: true,
    });
    if (host === undefined) return;
    const args = ["init", "--project-id", projectId.trim(), "--project-name", projectName.trim()];
    if (host.trim()) args.push("--host", host.trim());
    if (await this.runOl(projectFromFolder(folder), args, "Initializing Overleaf sync")) {
      await this.refreshProjects();
    }
  }

  async authLogin() {
    if (!this.ensureTrusted()) return;
    const project = await pickProjectOrWorkspace(this.projects, "Select where to save the session");
    if (!project) return;
    const host = await vscode.window.showInputBox({
      title: "Log in to Overleaf",
      prompt: "Overleaf base URL",
      placeHolder: "https://overleaf.example.com",
      validateInput: required("Host"),
      ignoreFocusOut: true,
    });
    if (!host) return;
    const method = await vscode.window.showQuickPick([
      { label: "Browser Cookie", description: "Recommended for SSO/captcha", value: "cookie" },
      { label: "Email and Password", description: "Uses the login form", value: "password" },
    ], { title: "Choose Overleaf login method", ignoreFocusOut: true });
    if (!method) return;
    if (method.value === "cookie") {
      const cookie = await vscode.window.showInputBox({
        title: "Log in to Overleaf",
        prompt: "Paste the complete Cookie header value",
        password: true,
        ignoreFocusOut: true,
      });
      if (!cookie) return;
      await this.runOl(
        project,
        ["auth", "login", "--host", host.trim(), "--cookie-stdin"],
        "Saving Overleaf session",
        { input: `${cookie}\n` },
      );
      return;
    }
    const email = await vscode.window.showInputBox({
      title: "Log in to Overleaf",
      prompt: "Email address",
      validateInput: required("Email"),
      ignoreFocusOut: true,
    });
    if (!email) return;
    const password = await vscode.window.showInputBox({
      title: "Log in to Overleaf",
      prompt: "Password",
      password: true,
      ignoreFocusOut: true,
    });
    if (!password) return;
    await this.runOl(
      project,
      ["auth", "login", "--host", host.trim(), "--email", email.trim(), "--password-stdin"],
      "Logging in to Overleaf",
      { input: `${password}\n` },
    );
  }

  async authStatus() {
    if (!this.ensureTrusted()) return;
    const project = await pickProjectOrWorkspace(this.projects, "Select a saved login session");
    if (project) await this.runOl(project, ["auth", "status"], "Checking Overleaf login");
  }

  async projectCommand(args, title, showOutput = false, target) {
    if (!this.ensureTrusted()) return;
    const project = await this.targetProject(target);
    if (project) await this.runOl(project, args, title, { showOutput });
  }

  async push(target) {
    if (!this.ensureTrusted()) return;
    const project = await this.targetProject(target);
    if (!project) return;
    const previewOk = await this.runOl(
      project, ["push", "--dry-run"], "Planning Overleaf push", { showOutput: true },
    );
    if (!previewOk) return;
    const choice = await vscode.window.showWarningMessage(
      `Push committed changes from ${project.label} to Overleaf?`,
      { modal: true, detail: "Review the dry-run plan in the Output channel first." },
      "Push",
    );
    if (choice === "Push") {
      await this.runOl(project, ["push"], "Pushing to Overleaf");
    }
  }

  async recover(target) {
    if (!this.ensureTrusted()) return;
    const project = await this.targetProject(target);
    if (!project) return;
    const choice = await vscode.window.showWarningMessage(
      `Recover the interrupted push for ${project.label}?`,
      { modal: true, detail: "This imports the current Overleaf state through Git." },
      "Recover",
    );
    if (choice === "Recover") {
      await this.runOl(project, ["recover"], "Recovering Overleaf push");
    }
  }

  async runOl(project, args, title, options = {}) {
    const key = project.root.fsPath;
    if (this.running.has(key)) {
      void vscode.window.showWarningMessage(`An ol command is already running for ${project.label}.`);
      return false;
    }
    const executable = this.executableFor(project);
    if (!executable) {
      void vscode.window.showErrorMessage("overleafGitSync.executable is empty.");
      return false;
    }

    this.running.add(key);
    this.statusBar.text = `$(sync~spin) Overleaf: ${title}`;
    this.output.info(`$ ${formatCommand(executable, args)}`);
    const abortController = new AbortController();
    try {
      const result = await vscode.window.withProgress({
        location: vscode.ProgressLocation.Notification,
        title,
        cancellable: true,
      }, async (_progress, token) => {
        token.onCancellationRequested(() => abortController.abort());
        return runProcess(executable, args, {
          cwd: project.root.fsPath,
          input: options.input,
          signal: abortController.signal,
          onStdout: (chunk) => this.output.append(chunk),
          onStderr: (chunk) => this.output.append(chunk),
        });
      });
      this.output.info(`Command exited with code ${result.code}.`);
      if (abortController.signal.aborted) {
        void vscode.window.showInformationMessage(`${title} was cancelled.`);
        return false;
      }
      if (result.code !== 0) {
        this.output.show(true);
        this.showCommandError(project, result.code);
        return false;
      }
      const configuredShow = vscode.workspace.getConfiguration("overleafGitSync")
        .get("showOutputOnSuccess", false);
      if (options.showOutput || configuredShow) this.output.show(true);
      void vscode.window.showInformationMessage(`${title} completed for ${project.label}.`);
      return true;
    } catch (error) {
      this.output.error(String(error));
      this.output.show(true);
      if (error instanceof ProcessStartError && error.causeCode === "ENOENT") {
        void vscode.window.showErrorMessage(
          `Could not find '${executable}'. Install the CLI or configure its absolute path.`,
          "Open Settings",
        ).then((choice) => {
          if (choice === "Open Settings") {
            void vscode.commands.executeCommand(
              "workbench.action.openSettings", "overleafGitSync.executable",
            );
          }
        });
      } else {
        void vscode.window.showErrorMessage(`Could not run ol: ${String(error)}`);
      }
      return false;
    } finally {
      this.running.delete(key);
      await this.refreshStatuses();
    }
  }

  showCommandError(project, code) {
    void vscode.window.showErrorMessage(
      `Overleaf command failed for ${project.label} (exit ${code}).`, "Show Output",
    ).then((choice) => {
      if (choice === "Show Output") this.output.show(true);
    });
  }
}

function required(label) {
  return (value) => value.trim() ? undefined : `${label} is required.`;
}

let controller;

async function activate(context) {
  controller = new OverleafController();
  context.subscriptions.push(controller);
  await controller.activate();
}

function deactivate() {
  controller?.dispose();
  controller = undefined;
}

module.exports = { activate, deactivate };
