"use strict";

const assert = require("node:assert/strict");
const Module = require("node:module");
const path = require("node:path");
const test = require("node:test");

test("status tree exposes project health, uncommitted files, and push plan", () => {
  const vscodeStub = {
    EventEmitter: class EventEmitter {
      constructor() { this.event = () => ({ dispose() {} }); }
      dispose() {}
      fire() {}
    },
    ThemeIcon: class ThemeIcon { constructor(id) { this.id = id; } },
    TreeItem: class TreeItem {
      constructor(label, collapsibleState) {
        this.label = label;
        this.collapsibleState = collapsibleState;
      }
    },
    TreeItemCollapsibleState: { None: 0, Collapsed: 1, Expanded: 2 },
    Uri: {
      joinPath(base, ...parts) { return { fsPath: path.join(base.fsPath, ...parts) }; },
    },
  };
  const originalLoad = Module._load;
  Module._load = function patchedLoad(request, parent, isMain) {
    if (request === "vscode") return vscodeStub;
    return originalLoad.call(this, request, parent, isMain);
  };
  try {
    const modulePath = require.resolve("../src/tree");
    delete require.cache[modulePath];
    const { StatusTreeProvider } = require(modulePath);
    const provider = new StatusTreeProvider();
    const project = {
      label: "paper",
      root: { fsPath: "/workspace/paper" },
    };
    provider.setProjects([project]);
    provider.setStatus(project, {
      project: { name: "My Paper" },
      branch: "main",
      working_tree_clean: false,
      working_tree_changes: [{ status: " M", path: "draft.tex", old_path: null }],
      conflicts: [],
      interrupted_push: false,
      last_synced_commit: "1234567890",
      last_synced_at: "2026-07-11T06:30:00+00:00",
      changes_known: true,
      changes: [{ display_status: "A", path: "figure.pdf", old_path: null }],
    });

    const [projectItem] = provider.getChildren();
    assert.equal(projectItem.label, "My Paper");
    assert.equal(projectItem.description, "Uncommitted changes");
    const children = provider.getChildren(projectItem);
    const worktree = children.find((item) => item.label === "Uncommitted changes (1)");
    const pushPlan = children.find((item) => item.label === "Committed changes to push (1)");
    const lastSynced = children.find((item) => item.label === "Last synced");
    assert.ok(worktree);
    assert.ok(pushPlan);
    assert.match(lastSynced.description, /^2026-07-(10|11) \d{2}:30$/);
    assert.match(lastSynced.tooltip, /Commit: 1234567890/);
    assert.equal(provider.getChildren(worktree)[0].label, "draft.tex");
    assert.equal(provider.getChildren(pushPlan)[0].label, "figure.pdf");
    provider.dispose();
  } finally {
    Module._load = originalLoad;
  }
});
