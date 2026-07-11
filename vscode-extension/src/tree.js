"use strict";

const vscode = require("vscode");
const { summarizeStatus } = require("./status");

class StatusTreeProvider {
  constructor() {
    this.projects = [];
    this.statuses = new Map();
    this.emitter = new vscode.EventEmitter();
    this.onDidChangeTreeData = this.emitter.event;
  }

  dispose() {
    this.emitter.dispose();
  }

  setProjects(projects) {
    this.projects = projects;
    const roots = new Set(projects.map((project) => project.root.fsPath));
    for (const root of this.statuses.keys()) {
      if (!roots.has(root)) this.statuses.delete(root);
    }
    this.refresh();
  }

  setStatus(project, status, error) {
    this.statuses.set(project.root.fsPath, { status, error });
    this.refresh();
  }

  refresh() {
    this.emitter.fire(undefined);
  }

  getTreeItem(element) {
    return element;
  }

  getChildren(element) {
    if (!element) {
      return this.projects.map((project) => {
        const state = this.statuses.get(project.root.fsPath) ?? {};
        return new ProjectItem(project, state.status, state.error);
      });
    }
    if (element instanceof ProjectItem) return projectChildren(element);
    if (element instanceof GroupItem) return element.children;
    return [];
  }
}

class ProjectItem extends vscode.TreeItem {
  constructor(project, status, error) {
    super(
      status?.project?.name || project.label,
      vscode.TreeItemCollapsibleState.Expanded,
    );
    this.project = project;
    this.status = status;
    this.error = error;
    this.resourceUri = project.root;
    this.contextValue = status?.interrupted_push ? "syncProjectRecovery" : "syncProject";
    if (error) {
      this.description = "status unavailable";
      this.iconPath = new vscode.ThemeIcon("error");
      this.tooltip = error;
    } else if (!status) {
      this.description = "loading…";
      this.iconPath = new vscode.ThemeIcon("loading~spin");
    } else {
      const summary = summarizeStatus(status);
      this.description = summary.label;
      this.iconPath = new vscode.ThemeIcon(summary.icon);
      this.tooltip = `${project.root.fsPath}\nRemote freshness: not checked`;
    }
  }
}

class DetailItem extends vscode.TreeItem {
  constructor(label, description, icon, contextValue) {
    super(label, vscode.TreeItemCollapsibleState.None);
    this.description = description;
    this.iconPath = new vscode.ThemeIcon(icon);
    this.contextValue = contextValue;
  }
}

class GroupItem extends vscode.TreeItem {
  constructor(label, children, icon, expanded = true) {
    super(
      label,
      expanded ? vscode.TreeItemCollapsibleState.Expanded : vscode.TreeItemCollapsibleState.Collapsed,
    );
    this.children = children;
    this.iconPath = new vscode.ThemeIcon(icon);
    this.contextValue = "syncGroup";
  }
}

class ChangeItem extends vscode.TreeItem {
  constructor(project, change) {
    super(change.path, vscode.TreeItemCollapsibleState.None);
    this.project = project;
    this.change = change;
    this.description = change.display_status;
    this.contextValue = "syncChange";
    this.iconPath = new vscode.ThemeIcon(changeIcon(change.display_status));
    this.tooltip = change.old_path
      ? `${change.old_path} → ${change.path}`
      : change.path;
    if (change.display_status !== "D") {
      this.command = {
        command: "vscode.open",
        title: "Open file",
        arguments: [vscode.Uri.joinPath(project.root, ...change.path.split("/"))],
      };
    }
  }
}

class WorktreeChangeItem extends vscode.TreeItem {
  constructor(project, change) {
    super(change.path, vscode.TreeItemCollapsibleState.None);
    this.project = project;
    this.change = change;
    this.description = worktreeDescription(change.status);
    this.contextValue = "syncWorktreeChange";
    this.iconPath = new vscode.ThemeIcon(changeIcon(worktreeDisplayStatus(change.status)));
    this.tooltip = change.old_path
      ? `${change.old_path} → ${change.path}`
      : `${change.path} · ${this.description}`;
    if (!change.status.includes("D")) {
      this.command = {
        command: "vscode.open",
        title: "Open file",
        arguments: [vscode.Uri.joinPath(project.root, ...change.path.split("/"))],
      };
    }
  }
}

function projectChildren(item) {
  if (item.error) {
    return [new DetailItem("Unable to read status", item.error, "error", "syncError")];
  }
  const status = item.status;
  if (!status) return [new DetailItem("Loading status…", "", "loading~spin")];
  const summary = summarizeStatus(status);
  const children = [
    new DetailItem(summary.label, "", summary.icon, `syncHealth-${summary.severity}`),
    new DetailItem("Branch", status.branch, "git-branch"),
    new DetailItem(
      "Working tree",
      status.working_tree_clean ? "clean" : "has uncommitted changes",
      status.working_tree_clean ? "check" : "edit",
    ),
    new DetailItem("Remote freshness", "not checked", "cloud"),
    lastSyncedItem(status),
  ];
  if (status.conflicts.length > 0) {
    children.push(new GroupItem(
      `Conflicts (${status.conflicts.length})`,
      status.conflicts.map((file) => new DetailItem(file, "conflict", "warning")),
      "warning",
    ));
  }
  if (status.working_tree_changes.length > 0) {
    children.push(new GroupItem(
      `Uncommitted changes (${status.working_tree_changes.length})`,
      status.working_tree_changes.map(
        (change) => new WorktreeChangeItem(item.project, change),
      ),
      "edit",
    ));
  }
  if (status.changes_known) {
    children.push(new GroupItem(
      `Committed changes to push (${status.changes.length})`,
      status.changes.length > 0
        ? status.changes.map((change) => new ChangeItem(item.project, change))
        : [new DetailItem("No committed changes", "", "check")],
      "list-tree",
      status.changes.length > 0,
    ));
  } else {
    children.push(new DetailItem("Committed changes", "unknown", "question"));
  }
  return children;
}

function shortCommit(value) {
  return value ? value.slice(0, 8) : "never";
}

function lastSyncedItem(status) {
  const item = new DetailItem(
    "Last synced",
    formatLocalDateTime(status.last_synced_at),
    "history",
  );
  item.tooltip = status.last_synced_commit
    ? `Last successful sync: ${status.last_synced_at || "time unavailable"}\nCommit: ${status.last_synced_commit}`
    : "This project has not completed a sync yet.";
  return item;
}

function formatLocalDateTime(value) {
  if (!value) return "never";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "time unavailable";
  const pad = (number) => String(number).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} `
    + `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function changeIcon(status) {
  if (status === "A") return "diff-added";
  if (status === "D") return "diff-removed";
  if (status === "R") return "diff-renamed";
  return "diff-modified";
}

function worktreeDisplayStatus(status) {
  if (status === "??") return "A";
  if (status.includes("D")) return "D";
  if (status.includes("R")) return "R";
  if (status.includes("A")) return "A";
  return "M";
}

function worktreeDescription(status) {
  if (status === "??") return "untracked";
  const parts = [];
  if (status[0] !== " ") parts.push(`staged ${gitStatusWord(status[0])}`);
  if (status[1] !== " ") parts.push(gitStatusWord(status[1]));
  return parts.join(", ");
}

function gitStatusWord(status) {
  if (status === "A") return "added";
  if (status === "D") return "deleted";
  if (status === "R") return "renamed";
  if (status === "C") return "copied";
  if (status === "U") return "conflict";
  return "modified";
}

module.exports = { StatusTreeProvider, formatLocalDateTime };
