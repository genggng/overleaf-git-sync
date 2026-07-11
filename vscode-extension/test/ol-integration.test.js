"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { runProcess } = require("../src/process");

const repoRoot = path.resolve(__dirname, "..", "..");
const executable = path.join(repoRoot, ".venv", "bin", "ol");
const paperDebug = path.join(repoRoot, "paper_debug");

test("runner invokes the real ol CLI with project cwd", { skip: !fs.existsSync(executable) }, async () => {
  const version = await runProcess(executable, ["--version"], { cwd: repoRoot });
  assert.equal(version.code, 0);
  assert.match(version.stdout, /^ol 0\.2\.2/m);

  if (fs.existsSync(path.join(paperDebug, ".ol-sync", "config.toml"))) {
    const status = await runProcess(executable, ["status", "--json"], { cwd: paperDebug });
    assert.equal(status.code, 0);
    const payload = JSON.parse(status.stdout);
    assert.equal(payload.schema_version, 1);
    assert.equal(payload.interrupted_push, false);
  }
});
