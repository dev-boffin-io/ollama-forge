// lib/manage.js
// Install / upgrade / uninstall / version-check Ollama — zero dependencies.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import https from "node:https";
import { run, runElevated, spawnStream } from "./exec.js";
import { IS_WINDOWS, OLLAMA_PATHS } from "./helpers.js";

const GITHUB_API = "https://api.github.com/repos/ollama/ollama/releases/latest";
const FALLBACK_API = "https://api.github.com/repos/ollama/ollama/releases";
export const INSTALL_CMD = "curl -fsSL https://ollama.com/install.sh | sh";

// Windows installs per-user via OllamaSetup.exe — no admin/UAC needed.
const WIN_INSTALLER_URL = "https://ollama.com/download/OllamaSetup.exe";
const LOCALAPPDATA =
  process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local");
const WIN_INSTALL_DIR = path.join(LOCALAPPDATA, "Programs", "Ollama");
const WIN_UNINSTALLER = path.join(WIN_INSTALL_DIR, "unins000.exe");
const WIN_PROCESS_NAMES = ["ollama.exe", "ollama app.exe"];

const isRoot =
  !IS_WINDOWS && typeof process.getuid === "function" && process.getuid() === 0;

// ── helpers ──────────────────────────────────────────────────────────────────

function httpsGetJson(url, timeout = 8000) {
  return new Promise((resolve, reject) => {
    const req = https.get(
      url,
      { headers: { Accept: "application/vnd.github+json", "User-Agent": "ollama-forge" } },
      (res) => {
        if (res.statusCode !== 200) {
          res.resume();
          return reject(new Error(`HTTP ${res.statusCode}`));
        }
        let body = "";
        res.on("data", (d) => (body += d));
        res.on("end", () => {
          try {
            resolve(JSON.parse(body));
          } catch (e) {
            reject(e);
          }
        });
      }
    );
    req.on("error", reject);
    req.setTimeout(timeout, () => req.destroy(new Error("timeout")));
  });
}

export function extractVersion(text) {
  const m = String(text).match(/(\d+\.\d+\.\d+(?:[-\w.]*)?)/);
  return m ? m[1] : null;
}

/** Compare dotted versions, tolerating rc/beta suffixes. Returns -1 | 0 | 1. */
export function compareVersions(a, b) {
  const parse = (v) =>
    String(v)
      .split(/[.+-]/)
      .map((p) => (/^\d+$/.test(p) ? parseInt(p, 10) : p));
  const pa = parse(a);
  const pb = parse(b);
  const len = Math.max(pa.length, pb.length);
  for (let i = 0; i < len; i++) {
    const x = pa[i];
    const y = pb[i];
    if (x === undefined) return -1;
    if (y === undefined) return 1;
    const xn = typeof x === "number";
    const yn = typeof y === "number";
    if (xn && yn) {
      if (x !== y) return x < y ? -1 : 1;
    } else if (xn !== yn) {
      // numeric segment outranks a prerelease tag
      return xn ? 1 : -1;
    } else if (x !== y) {
      return x < y ? -1 : 1;
    }
  }
  return 0;
}

// ── version detection ────────────────────────────────────────────────────────

export async function getCurrentVersion(bin) {
  const r = await run(bin || "ollama", ["--version"]);
  if (r.error && r.error.code === "ENOENT") return null;
  return extractVersion(r.stdout + r.stderr);
}

export async function getLatestVersion() {
  try {
    const data = await httpsGetJson(GITHUB_API);
    const tag = Array.isArray(data) ? data[0]?.tag_name : data?.tag_name;
    if (tag) return String(tag).replace(/^v/, "");
  } catch {
    /* fall through */
  }
  try {
    const data = await httpsGetJson(FALLBACK_API);
    const tag = Array.isArray(data) ? data[0]?.tag_name : null;
    if (tag) return String(tag).replace(/^v/, "");
  } catch {
    /* ignore */
  }
  return null;
}

// ── platform actions ─────────────────────────────────────────────────────────

export async function windowsRunInstaller(emit) {
  const installerPath = path.join(os.tmpdir(), "OllamaSetup.exe");
  emit(`Downloading ${WIN_INSTALLER_URL}...`);
  try {
    const res = await fetch(WIN_INSTALLER_URL, { signal: AbortSignal.timeout(120000) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const buf = Buffer.from(await res.arrayBuffer());
    fs.writeFileSync(installerPath, buf);
  } catch (e) {
    emit(`Download failed: ${e}`);
    return false;
  }
  emit("Running installer (silent)...");
  const r = await run(installerPath, ["/SILENT"]);
  if (r.code !== 0) {
    emit(`Installer exited with code ${r.code}`);
    return false;
  }
  return true;
}

export async function windowsUninstall(emit) {
  for (const name of WIN_PROCESS_NAMES) {
    await run("taskkill", ["/IM", name, "/F"]);
  }
  if (!fs.existsSync(WIN_UNINSTALLER)) {
    emit(`Uninstaller not found at ${WIN_UNINSTALLER}.`);
    emit("Remove Ollama via Windows Settings → Apps instead.");
    return;
  }
  emit("Running uninstaller...");
  const r = await run(WIN_UNINSTALLER, [
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
  ]);
  if (r.code === 0) emit("Ollama removed.");
  else emit(`Uninstaller exited with code ${r.code}`);
}

async function streamShell(cmd, { password, emit, onSpawn }) {
  const env = { ...process.env };
  if (!IS_WINDOWS && !isRoot && password) {
    await spawnStream("sudo", ["-S", "sh", "-c", cmd], {
      env,
      stdinData: password + "\n",
      onLine: emit,
      onSpawn,
    });
  } else {
    await spawnStream("sh", ["-c", cmd], { env, onLine: emit, onSpawn });
  }
}

// ── commands ─────────────────────────────────────────────────────────────────

export async function runManage(action, { bin, password = "", emit, onSpawn } = {}) {
  const send = typeof emit === "function" ? emit : () => {};
  const perform = {
    update: () => cmdUpdate(bin, send),
    install: () => cmdInstall(bin, send, password, onSpawn),
    upgrade: () => cmdUpgrade(bin, send, password, onSpawn),
    uninstall: () => cmdUninstall(bin, send, password, onSpawn),
  }[action];

  if (!perform) {
    send(`Error: unknown action '${action}'`);
    return 1;
  }
  try {
    return (await perform()) ?? 0;
  } catch (e) {
    send(`Error: ${e}`);
    return 1;
  }
}

async function cmdUpdate(bin, emit) {
  const cur = await getCurrentVersion(bin);
  const lat = await getLatestVersion();
  emit(`Current version: ${cur || "not installed"}`);
  emit(`Latest version : ${lat || "unknown (network error)"}`);
  if (cur && lat) {
    if (compareVersions(cur, lat) < 0) emit("Update available — use Upgrade Ollama.");
    else emit("Already up to date.");
  }
  return 0;
}

async function cmdInstall(bin, emit, password, onSpawn) {
  if (await getCurrentVersion(bin)) {
    emit("Ollama already installed.");
    return 0;
  }
  if (IS_WINDOWS) {
    emit("Installing via official OllamaSetup.exe...");
    await windowsRunInstaller(emit);
  } else {
    emit("Installing via official install.sh...");
    await streamShell(INSTALL_CMD, { password, emit, onSpawn });
  }
  return 0;
}

async function cmdUpgrade(bin, emit, password, onSpawn) {
  const cur = await getCurrentVersion(bin);
  const lat = await getLatestVersion();
  if (!cur) {
    emit("Not installed, installing...");
    if (IS_WINDOWS) await windowsRunInstaller(emit);
    else await streamShell(INSTALL_CMD, { password, emit, onSpawn });
    return 0;
  }
  if (!lat) {
    emit("Cannot reach GitHub. Aborted.");
    return 0;
  }
  if (compareVersions(cur, lat) >= 0) {
    emit(`Already latest (${cur}).`);
    return 0;
  }
  emit(`Upgrading ${cur} to ${lat}...`);
  if (IS_WINDOWS) await windowsRunInstaller(emit);
  else await streamShell(INSTALL_CMD, { password, emit, onSpawn });
  return 0;
}

async function cmdUninstall(bin, emit, password, onSpawn) {
  const cur = await getCurrentVersion(bin);
  if (!cur) {
    emit("Ollama is not installed.");
    return 0;
  }
  if (IS_WINDOWS) {
    await windowsUninstall(emit);
    return 0;
  }

  const active = await run("systemctl", ["is-active", "--quiet", "ollama"]);
  if (active.code === 0) {
    await runElevated(["systemctl", "stop", "ollama"], { sudo: true, password, onLine: emit, onSpawn });
    await runElevated(["systemctl", "disable", "ollama"], { sudo: true, password, onLine: emit, onSpawn });
    emit("Stopped ollama.service");
  }

  const existing = OLLAMA_PATHS.filter((p) => fs.existsSync(p));
  if (existing.length) {
    await runElevated(["rm", "-rf", ...existing], { sudo: true, password, onLine: emit, onSpawn });
    emit(`Removed: ${existing.join(", ")}`);
  }
  if (existing.some((p) => p.includes("systemd"))) {
    await runElevated(["systemctl", "daemon-reload"], { sudo: true, password, onLine: emit, onSpawn });
  }
  emit("Ollama removed.");
  return 0;
}
