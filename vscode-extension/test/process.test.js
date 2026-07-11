"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { formatCommand, ProcessStartError, runProcess } = require("../src/process");

test("runProcess captures output and exit code", async () => {
  const result = await runProcess(
    process.execPath,
    ["-e", "process.stdout.write('out'); process.stderr.write('err'); process.exit(7)"],
    { cwd: process.cwd() },
  );
  assert.deepEqual(result, { code: 7, stdout: "out", stderr: "err" });
});

test("runProcess sends secret input through stdin", async () => {
  const result = await runProcess(
    process.execPath,
    ["-e", "process.stdin.pipe(process.stdout)"],
    { cwd: process.cwd(), input: "secret-value\n" },
  );
  assert.equal(result.code, 0);
  assert.equal(result.stdout, "secret-value\n");
});

test("runProcess reports a missing executable", async () => {
  await assert.rejects(
    runProcess("definitely-not-an-overleaf-command", [], { cwd: process.cwd() }),
    (error) => error instanceof ProcessStartError && error.causeCode === "ENOENT",
  );
});

test("formatCommand quotes display-only arguments", () => {
  assert.equal(
    formatCommand("/path/to/ol", ["init", "--project-name", "my paper"]),
    '/path/to/ol init --project-name "my paper"',
  );
});
