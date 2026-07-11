"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { parseStatusJson, summarizeStatus } = require("../src/status");

function status(overrides = {}) {
  return {
    schema_version: 1,
    branch: "main",
    working_tree_clean: true,
    working_tree_changes: [],
    changes: [],
    conflicts: [],
    interrupted_push: false,
    last_synced_at: null,
    ...overrides,
  };
}

test("parseStatusJson accepts schema version 1", () => {
  assert.deepEqual(parseStatusJson(JSON.stringify(status())), status());
});

test("parseStatusJson rejects incompatible or malformed data", () => {
  assert.throws(() => parseStatusJson("not json"), /invalid JSON/);
  assert.throws(() => parseStatusJson(JSON.stringify({ schema_version: 2 })), /Unsupported/);
  assert.throws(
    () => parseStatusJson(JSON.stringify(status({ changes: "bad" }))),
    /malformed/,
  );
});

test("summarizeStatus prioritizes recovery, conflicts, dirty state, and push plan", () => {
  assert.equal(summarizeStatus(status({ interrupted_push: true })).label, "Recovery required");
  assert.match(summarizeStatus(status({ conflicts: ["main.tex"] })).label, /1 merge conflict/);
  assert.equal(
    summarizeStatus(status({ working_tree_clean: false })).label,
    "Uncommitted changes",
  );
  assert.match(summarizeStatus(status({ changes: [{ path: "main.tex" }] })).label, /1 committed/);
  assert.equal(summarizeStatus(status()).label, "No local changes");
});
