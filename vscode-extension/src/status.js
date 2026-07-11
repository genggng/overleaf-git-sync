"use strict";

function parseStatusJson(output) {
  let value;
  try {
    value = JSON.parse(output);
  } catch (error) {
    throw new Error(`ol status --json returned invalid JSON: ${error}`);
  }
  if (!value || value.schema_version !== 1) {
    throw new Error("Unsupported ol status schema. Update the CLI and extension together.");
  }
  for (const key of [
    "branch",
    "working_tree_clean",
    "working_tree_changes",
    "changes",
    "conflicts",
    "last_synced_at",
  ]) {
    if (!(key in value)) throw new Error(`ol status JSON is missing '${key}'.`);
  }
  if (
    !Array.isArray(value.working_tree_changes)
    || !Array.isArray(value.changes)
    || !Array.isArray(value.conflicts)
  ) {
    throw new Error("ol status JSON contains malformed file changes or conflicts.");
  }
  return value;
}

function summarizeStatus(status) {
  if (status.interrupted_push) {
    return { label: "Recovery required", icon: "warning", severity: "error" };
  }
  if (status.conflicts.length > 0) {
    return { label: `${status.conflicts.length} merge conflict(s)`, icon: "warning", severity: "error" };
  }
  if (!status.working_tree_clean) {
    return { label: "Uncommitted changes", icon: "edit", severity: "warning" };
  }
  if (status.changes.length > 0) {
    return {
      label: `${status.changes.length} committed change(s) to push`,
      icon: "cloud-upload",
      severity: "info",
    };
  }
  return { label: "No local changes", icon: "check", severity: "ok" };
}

module.exports = { parseStatusJson, summarizeStatus };
