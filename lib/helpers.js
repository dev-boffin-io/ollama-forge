// lib/helpers.js
// Pure Node helpers — no external dependencies.
// Handles: config persistence, binary detection, username parsing, formatting.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";

// ── Persistent config ────────────────────────────────────────────────────────
export const HOME = os.homedir();
export const CONFIG_DIR = path.join(HOME, ".ollama_gui");
export const SETTINGS_FILE = path.join(CONFIG_DIR, "settings.json");

export const OLLAMA_PATHS = [
  "/usr/local/bin/ollama",
  "/usr/local/lib/ollama",
  "/usr/share/ollama",
  "/etc/systemd/system/ollama.service",
  "/etc/systemd/system/ollama.service.d",
];

// Default Ollama HTTP base URL (shared with the web UI).
export const OLLAMA_DEFAULT_HOST = "http://localhost:11434";

export const IS_WINDOWS = process.platform === "win32";

// ── Config I/O ───────────────────────────────────────────────────────────────

export function loadSettings() {
  try {
    return JSON.parse(fs.readFileSync(SETTINGS_FILE, "utf8")) || {};
  } catch {
    return {};
  }
}

export function saveSettings(patch) {
  const data = loadSettings();
  Object.assign(data, patch);
  try {
    fs.mkdirSync(CONFIG_DIR, { recursive: true });
    fs.writeFileSync(SETTINGS_FILE, JSON.stringify(data, null, 2));
    return true;
  } catch {
    return false;
  }
}

/**
 * Ollama HTTP base URL for status/model checks.
 * Reads settings.json ("ollama_host"), falling back to the OLLAMA_HOST
 * env var, then the default local URL.
 */
export function loadOllamaHost() {
  const settings = loadSettings();
  let host = settings.ollama_host || process.env.OLLAMA_HOST || "";
  host = String(host).trim().replace(/\/+$/, "");
  if (host.startsWith("http://") || host.startsWith("https://")) return host;
  return OLLAMA_DEFAULT_HOST;
}

export function loadOllamaBin() {
  return loadSettings().ollama_bin || "";
}

export function saveOllamaBin(p) {
  return saveSettings({ ollama_bin: p });
}

// ── Binary detection ──────────────────────────────────────────────────────────

function isExecutable(p) {
  try {
    if (!fs.statSync(p).isFile()) return false;
    if (IS_WINDOWS) return true;
    fs.accessSync(p, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

export function autodetectOllama() {
  const exe = IS_WINDOWS ? "ollama.exe" : "ollama";
  const dirs = (process.env.PATH || "").split(path.delimiter);
  for (const dir of dirs) {
    if (!dir) continue;
    const p = path.join(dir, exe);
    if (isExecutable(p)) return p;
  }

  const candidates = IS_WINDOWS
    ? [path.join(process.env.LOCALAPPDATA || "", "Programs", "Ollama", "ollama.exe")]
    : [
        "/usr/local/bin/ollama",
        "/usr/bin/ollama",
        path.join(HOME, "bin", "ollama"),
        "/data/data/com.termux/files/usr/bin/ollama",
      ];

  for (const p of candidates) {
    if (isExecutable(p)) return p;
  }
  return "";
}

// ── Auth helpers ──────────────────────────────────────────────────────────────

export function readOllamaUsername() {
  const candidates = [
    path.join(HOME, ".ollama", "config"),
    path.join(HOME, ".config", "ollama", "config"),
  ];
  for (const p of candidates) {
    if (!fs.existsSync(p)) continue;
    try {
      const d = JSON.parse(fs.readFileSync(p, "utf8"));
      const u = d.username || d.user || d.name;
      if (u) return String(u);
    } catch {
      /* ignore */
    }
  }
  return null;
}

export function parseSigninUsername(line) {
  const patterns = [
    /signed in as user\s+['"]?([A-Za-z0-9_-]+)['"]?/i,
    /already signed in as\s+['"]?([A-Za-z0-9_-]+)['"]?/i,
    /logged in as\s+['"]?([A-Za-z0-9_-]+)['"]?/i,
    /welcome[,\s]+['"]?([A-Za-z0-9_-]+)['"]?/i,
  ];
  for (const re of patterns) {
    const m = line.match(re);
    if (m) return m[1];
  }
  return null;
}

// ── Misc ──────────────────────────────────────────────────────────────────────

export function safeRemove(p) {
  try {
    fs.unlinkSync(p);
  } catch {
    /* ignore */
  }
}

export function fmtSize(bytes) {
  if (!bytes) return "?";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let b = Number(bytes);
  for (const u of units) {
    if (b < 1024) return `${b.toFixed(1)} ${u}`;
    b /= 1024;
  }
  return `${b.toFixed(1)} PB`;
}

export function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

export function openBrowser(url) {
  try {
    let cmd, args;
    if (IS_WINDOWS) {
      cmd = "cmd";
      args = ["/c", "start", "", url];
    } else if (process.platform === "darwin") {
      cmd = "open";
      args = [url];
    } else {
      cmd = "xdg-open";
      args = [url];
    }
    const p = spawn(cmd, args, { detached: true, stdio: "ignore" });
    p.unref();
    return true;
  } catch {
    return false;
  }
}
