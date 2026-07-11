"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const extensionRoot = path.resolve(__dirname, "..");
const manifest = JSON.parse(
  fs.readFileSync(path.join(extensionRoot, "package.json"), "utf8"),
);
const extensionSource = fs.readFileSync(
  path.join(extensionRoot, "src", "extension.js"),
  "utf8",
);

test("manifest entry point and every contributed command exist", () => {
  assert.ok(fs.existsSync(path.resolve(extensionRoot, manifest.main)));
  for (const contribution of manifest.contributes.commands) {
    assert.match(extensionSource, new RegExp(`register\\(\"${contribution.command.replaceAll(".", "\\.")}\"`));
  }
});

test("manifest contributes a native Activity Bar status view", () => {
  const container = manifest.contributes.viewsContainers.activitybar[0];
  assert.equal(container.id, "overleafGitSync");
  assert.ok(fs.existsSync(path.join(extensionRoot, container.icon)));
  assert.equal(
    manifest.contributes.views.overleafGitSync[0].id,
    "overleafGitSync.projects",
  );
  assert.ok(
    manifest.contributes.menus["view/title"].some(
      (item) => item.command === "overleafGitSync.push",
    ),
  );
  assert.ok(
    manifest.contributes.menus["view/item/context"].some(
      (item) => item.command === "overleafGitSync.recover",
    ),
  );
});

test("manifest requires trust for executable settings", () => {
  const untrusted = manifest.capabilities.untrustedWorkspaces;
  assert.equal(untrusted.supported, "limited");
  assert.deepEqual(untrusted.restrictedConfigurations, ["overleafGitSync.executable"]);
  assert.equal(manifest.capabilities.virtualWorkspaces, false);
});

test("extension never invokes a shell", () => {
  const processSource = fs.readFileSync(
    path.join(extensionRoot, "src", "process.js"),
    "utf8",
  );
  assert.match(processSource, /shell: false/);
  assert.doesNotMatch(extensionSource, /--password[\"']/);
  assert.doesNotMatch(extensionSource, /--cookie[\"']/);
});
