// lib/exec.js
// Subprocess helpers built on node:child_process — no external dependencies.

import { spawn } from "node:child_process";
import { StringDecoder } from "node:string_decoder";
import { IS_WINDOWS } from "./helpers.js";

/**
 * Run a command to completion, buffering stdout/stderr.
 * Never throws — resolves with { code, stdout, stderr }.
 */
export function run(cmd, args = [], opts = {}) {
  return new Promise((resolve) => {
    let stdout = "";
    let stderr = "";
    let proc;
    try {
      proc = spawn(cmd, args, { env: opts.env || process.env, cwd: opts.cwd });
    } catch (e) {
      return resolve({ code: 1, stdout: "", stderr: String(e), error: e });
    }
    proc.stdout?.on("data", (d) => (stdout += d));
    proc.stderr?.on("data", (d) => (stderr += d));
    proc.on("error", (e) =>
      resolve({ code: 1, stdout, stderr: stderr + String(e), error: e })
    );
    proc.on("close", (code) => resolve({ code: code ?? 0, stdout, stderr }));
  });
}

/**
 * Spawn a command and stream its combined stdout/stderr line-by-line.
 * Splits on both \n and \r so carriage-return progress lines surface too.
 *
 * @param {string} cmd
 * @param {string[]} args
 * @param {object} opts
 * @param {(line:string)=>void} [opts.onLine]
 * @param {(proc:import('node:child_process').ChildProcess)=>void} [opts.onSpawn]
 * @param {Object} [opts.env]
 * @param {string|null} [opts.stdinData]  written to stdin then closed
 * @returns {Promise<number>} exit code
 */
export function spawnStream(cmd, args, opts = {}) {
  const { onLine, onSpawn, env, stdinData } = opts;
  return new Promise((resolve) => {
    let proc;
    try {
      proc = spawn(cmd, args, {
        env: env || process.env,
        stdio: ["pipe", "pipe", "pipe"],
      });
    } catch (e) {
      onLine?.(`❌ ${e}`);
      return resolve(1);
    }

    onSpawn?.(proc);

    const decoders = [new StringDecoder("utf8"), new StringDecoder("utf8")];
    let buffers = ["", ""];

    const feed = (stream, i) => {
      buffers[i] += decoders[i].write(stream);
      let idx;
      while ((idx = buffers[i].search(/[\r\n]/)) !== -1) {
        const line = buffers[i].slice(0, idx);
        buffers[i] = buffers[i].slice(idx + 1);
        if (line.trim()) onLine?.(line.trim());
      }
    };

    proc.stdout.on("data", (d) => feed(d, 0));
    proc.stderr.on("data", (d) => feed(d, 1));

    if (stdinData != null) {
      proc.stdin.write(stdinData);
    }
    proc.stdin.end();

    proc.on("error", (e) => {
      onLine?.(`❌ ${e}`);
      resolve(1);
    });
    proc.on("close", (code) => {
      for (let i = 0; i < 2; i++) {
        const rest = decoders[i].end();
        if (rest) buffers[i] += rest;
        if (buffers[i].trim()) onLine?.(buffers[i].trim());
      }
      resolve(code ?? 0);
    });
  });
}

/**
 * Run a command, optionally elevating with sudo (piping the password via -S).
 */
export async function runElevated(args, opts = {}) {
  const { sudo = false, password = "", ...rest } = opts;
  const isRoot =
    !IS_WINDOWS && typeof process.getuid === "function" && process.getuid() === 0;
  if (sudo && !IS_WINDOWS && !isRoot) {
    return spawnStream("sudo", ["-S", ...args], {
      ...rest,
      stdinData: (password || "") + "\n",
    });
  }
  return spawnStream(args[0], args.slice(1), rest);
}
