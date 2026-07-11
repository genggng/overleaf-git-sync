"use strict";

const path = require("node:path");
const vscode = require("vscode");

async function discoverProjects() {
  const folders = vscode.workspace.workspaceFolders ?? [];
  const projects = new Map();
  for (const folder of folders) {
    if (folder.uri.scheme !== "file") {
      continue;
    }
    const configs = await vscode.workspace.findFiles(
      new vscode.RelativePattern(folder, "**/.ol-sync/config.toml"),
      "**/{.git,node_modules,.venv}/**",
      50,
    );
    for (const config of configs) {
      const rootPath = path.dirname(path.dirname(config.fsPath));
      const key = path.resolve(rootPath);
      projects.set(key, {
        root: vscode.Uri.file(rootPath),
        workspaceFolder: folder,
        label: path.basename(rootPath),
      });
    }
  }
  return [...projects.values()].sort((left, right) =>
    left.root.fsPath.localeCompare(right.root.fsPath),
  );
}

async function pickProject(projects, placeHolder = "Select an Overleaf sync project") {
  if (projects.length === 0) {
    void vscode.window.showWarningMessage(
      "No .ol-sync/config.toml was found. Run Overleaf: Initialize Project first.",
    );
    return undefined;
  }
  if (projects.length === 1) {
    return projects[0];
  }
  const selected = await vscode.window.showQuickPick(
    projects.map((project) => ({
      label: project.label,
      description: vscode.workspace.asRelativePath(project.root, false),
      project,
    })),
    { placeHolder },
  );
  return selected?.project;
}

async function pickWorkspaceFolder() {
  const folders = (vscode.workspace.workspaceFolders ?? []).filter(
    (folder) => folder.uri.scheme === "file",
  );
  if (folders.length === 0) {
    void vscode.window.showWarningMessage("Open a local folder before using Overleaf sync.");
    return undefined;
  }
  if (folders.length === 1) {
    return folders[0];
  }
  return vscode.window.showWorkspaceFolderPick({
    placeHolder: "Select a local folder",
  });
}

function projectFromFolder(folder) {
  return { root: folder.uri, workspaceFolder: folder, label: folder.name };
}

async function pickProjectOrWorkspace(projects, placeHolder) {
  const candidates = projects.map((project) => ({
    label: project.label,
    description: `Sync project · ${vscode.workspace.asRelativePath(project.root, false)}`,
    project,
  }));
  const projectRoots = new Set(projects.map((project) => path.resolve(project.root.fsPath)));
  for (const folder of vscode.workspace.workspaceFolders ?? []) {
    if (folder.uri.scheme !== "file" || projectRoots.has(path.resolve(folder.uri.fsPath))) {
      continue;
    }
    candidates.push({
      label: folder.name,
      description: "Workspace folder · not initialized",
      project: projectFromFolder(folder),
    });
  }
  if (candidates.length === 0) {
    void vscode.window.showWarningMessage("Open a local folder before using Overleaf sync.");
    return undefined;
  }
  if (candidates.length === 1) {
    return candidates[0].project;
  }
  const selected = await vscode.window.showQuickPick(candidates, { placeHolder });
  return selected?.project;
}

module.exports = {
  discoverProjects,
  pickProject,
  pickProjectOrWorkspace,
  pickWorkspaceFolder,
  projectFromFolder,
};
