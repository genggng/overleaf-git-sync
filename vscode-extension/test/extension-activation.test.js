"use strict";

const assert = require("node:assert/strict");
const Module = require("node:module");
const path = require("node:path");
const test = require("node:test");

test("extension activates, discovers a project, and registers every command", async () => {
  const registered = new Map();
  const contexts = new Map();
  const disposables = [];
  const statusBar = disposable({ show() {} });
  const output = disposable({
    append() {},
    error() {},
    info() {},
    show() {},
  });
  const watcher = disposable({
    onDidChange() {},
    onDidCreate() {},
    onDidDelete() {},
  });
  const folderPath = path.join(path.sep, "workspace", "paper");
  const folder = {
    name: "paper",
    index: 0,
    uri: uri(folderPath),
  };
  const vscodeStub = {
    EventEmitter: class EventEmitter {
      constructor() {
        this.event = () => disposable();
      }
      dispose() {}
      fire() {}
    },
    RelativePattern: class RelativePattern {
      constructor(base, pattern) {
        this.base = base;
        this.pattern = pattern;
      }
    },
    ThemeIcon: class ThemeIcon {
      constructor(id) { this.id = id; }
    },
    TreeItem: class TreeItem {
      constructor(label, collapsibleState) {
        this.label = label;
        this.collapsibleState = collapsibleState;
      }
    },
    TreeItemCollapsibleState: { None: 0, Collapsed: 1, Expanded: 2 },
    StatusBarAlignment: { Left: 1 },
    Uri: {
      file: uri,
      joinPath(base, ...parts) { return uri(path.join(base.fsPath, ...parts)); },
    },
    commands: {
      registerCommand(command, callback) {
        registered.set(command, callback);
        return disposable();
      },
      async executeCommand(command, key, value) {
        if (command === "setContext") contexts.set(key, value);
      },
    },
    window: {
      createOutputChannel() { return output; },
      createStatusBarItem() { return statusBar; },
      registerTreeDataProvider() { return disposable(); },
      showErrorMessage() {},
    },
    workspace: {
      isTrusted: true,
      workspaceFolders: [folder],
      fs: { async stat() { throw new Error("missing"); } },
      asRelativePath(value) { return value.fsPath; },
      createFileSystemWatcher() { return watcher; },
      getConfiguration() {
        return {
          get(key, fallback) {
            if (key === "refreshIntervalSeconds") return 0;
            if (key === "executable") return "ol";
            return fallback;
          },
        };
      },
      async findFiles() {
        return [uri(path.join(folderPath, ".ol-sync", "config.toml"))];
      },
      onDidChangeWorkspaceFolders() { return disposable(); },
      onDidChangeConfiguration() { return disposable(); },
      onDidCreateFiles() { return disposable(); },
      onDidDeleteFiles() { return disposable(); },
      onDidGrantWorkspaceTrust() { return disposable(); },
      onDidRenameFiles() { return disposable(); },
      onDidSaveTextDocument() { return disposable(); },
    },
  };

  const originalLoad = Module._load;
  Module._load = function patchedLoad(request, parent, isMain) {
    if (request === "vscode") return vscodeStub;
    if (request === "./process" && parent?.filename.endsWith("extension.js")) {
      return {
        ProcessStartError: class ProcessStartError extends Error {},
        formatCommand() { return "ol"; },
        async runProcess() {
          return {
            code: 0,
            stderr: "",
            stdout: JSON.stringify({
              schema_version: 1,
              project: { name: "paper", project_id: "p1", host: "http://localhost" },
              branch: "main",
              working_tree_clean: true,
              working_tree_changes: [],
              changes_known: true,
              changes: [],
              conflicts: [],
              interrupted_push: false,
              last_synced_commit: "1234567890",
              last_synced_at: "2026-07-11T06:30:00+00:00",
              last_remote_snapshot_commit: "1234567890",
              remote_freshness: "not_checked",
            }),
          };
        },
      };
    }
    return originalLoad.call(this, request, parent, isMain);
  };
  try {
    const extensionPath = require.resolve("../src/extension");
    delete require.cache[extensionPath];
    const extension = require(extensionPath);
    await extension.activate({ subscriptions: disposables });

    assert.equal(registered.size, 11);
    assert.equal(contexts.get("overleafGitSync.hasProject"), true);
    assert.equal(statusBar.text, "$(check) Overleaf: No local changes");
    assert.equal(statusBar.command, "overleafGitSync.status");
    for (const item of disposables.reverse()) item.dispose();
    extension.deactivate();
  } finally {
    Module._load = originalLoad;
  }
});

function uri(fsPath) {
  return { scheme: "file", fsPath };
}

function disposable(properties = {}) {
  return { ...properties, dispose() {} };
}
