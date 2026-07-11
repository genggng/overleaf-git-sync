"use strict";

const { spawn } = require("node:child_process");

class ProcessStartError extends Error {
  constructor(message, causeCode) {
    super(message);
    this.name = "ProcessStartError";
    this.causeCode = causeCode;
  }
}

function runProcess(executable, args, options) {
  return new Promise((resolve, reject) => {
    let child;
    try {
      child = spawn(executable, args, {
        cwd: options.cwd,
        env: process.env,
        shell: false,
        windowsHide: true,
        stdio: ["pipe", "pipe", "pipe"],
      });
    } catch (error) {
      reject(new ProcessStartError(String(error)));
      return;
    }

    let stdout = "";
    let stderr = "";
    let settled = false;
    const abort = () => {
      if (!child.killed) {
        child.kill();
      }
    };
    options.signal?.addEventListener("abort", abort, { once: true });

    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
      options.onStdout?.(chunk);
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
      options.onStderr?.(chunk);
    });
    child.on("error", (error) => {
      if (settled) {
        return;
      }
      settled = true;
      options.signal?.removeEventListener("abort", abort);
      reject(new ProcessStartError(error.message, error.code));
    });
    child.on("close", (code, signal) => {
      if (settled) {
        return;
      }
      settled = true;
      options.signal?.removeEventListener("abort", abort);
      resolve({ code: code ?? (signal ? 130 : 1), stdout, stderr });
    });

    child.stdin.end(options.input);
  });
}

function formatCommand(executable, args) {
  return [executable, ...args].map(formatArgument).join(" ");
}

function formatArgument(value) {
  if (/^[A-Za-z0-9_./:@%+=,-]+$/.test(value)) {
    return value;
  }
  return JSON.stringify(value);
}

module.exports = { ProcessStartError, formatCommand, runProcess };
