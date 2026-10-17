#!/usr/bin/env node
// cli.js
// Ollama CLI manager — install, upgrade, check, remove.
// Zero external dependencies.

import process from "node:process";
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

import { IS_WINDOWS, OLLAMA_PATHS } from "./lib/helpers.js";
import {
  getCurrentVersion,
  getLatestVersion,
  compareVersions,
  windowsRunInstaller,
  windowsUninstall,
  INSTALL_CMD,
} from "./lib/manage.js";

const Color = {
  GREEN: "\x1b[92m",
  RED: "\x1b[91m",
  YELLOW: "\x1b[93m",
  BLUE: "\x1b[94m",
  RESET: "\x1b[0m",
};

const paint = (color, text) => `${color}${text}${Color.RESET}`;

function sudoPrefix() {
  if (IS_WINDOWS) return [];
  const isRoot = typeof process.getuid === "function" && process.getuid() === 0;
  return isRoot ? [] : ["sudo"];
}

// ── Install ──────────────────────────────────────────────────────────────────

async function install() {
  if (await getCurrentVersion("ollama")) {
    console.log(paint(Color.GREEN, "✔ Ollama already installed"));
    return;
  }
  console.log(paint(Color.BLUE, "Installing Ollama..."));
  if (IS_WINDOWS) {
    await windowsRunInstaller((m) => console.log(paint(Color.BLUE, m)));
  } else {
    spawnSync("sh", ["-c", INSTALL_CMD], { stdio: "inherit" });
  }
}

// ── Upgrade ──────────────────────────────────────────────────────────────────

async function upgrade() {
  const current = await getCurrentVersion("ollama");
  const latest = await getLatestVersion();

  if (!current) {
    console.log(paint(Color.YELLOW, "Ollama not installed — installing..."));
    await install();
    return;
  }
  if (!latest) {
    console.log(paint(Color.RED, "Unable to check latest version (network/API issue)"));
    return;
  }
  if (compareVersions(current, latest) >= 0) {
    console.log(paint(Color.GREEN, `Already latest version (${current})`));
    return;
  }

  console.log(paint(Color.BLUE, `Upgrading Ollama ${current} → ${latest}`));
  if (IS_WINDOWS) {
    await windowsRunInstaller((m) => console.log(paint(Color.BLUE, m)));
  } else {
    spawnSync("sh", ["-c", INSTALL_CMD], { stdio: "inherit" });
  }
}

// ── Update (version status check) ────────────────────────────────────────────

async function update() {
  const current = await getCurrentVersion("ollama");
  const latest = await getLatestVersion();

  if (!current) {
    console.log(paint(Color.RED, "Ollama not installed"));
    return;
  }
  console.log(`${paint(Color.BLUE, "Current version:")} ${current}`);

  if (!latest) {
    console.log(paint(Color.RED, "Could not fetch latest version (network issue)"));
    return;
  }
  console.log(`${paint(Color.BLUE, "Latest version :")} ${latest}`);

  if (compareVersions(current, latest) < 0) {
    console.log(paint(Color.YELLOW, "Update available — run: ollama-forge upgrade"));
  } else {
    console.log(paint(Color.GREEN, "Up to date"));
  }
}

// ── Uninstall ────────────────────────────────────────────────────────────────

function runPrefixed(prefix, args) {
  const [cmd, ...rest] = [...prefix, ...args];
  return spawnSync(cmd, rest, { stdio: "inherit" });
}

async function uninstall() {
  const current = await getCurrentVersion("ollama");
  if (!current) {
    console.log(paint(Color.YELLOW, "Ollama is not installed"));
    return;
  }
  console.log(paint(Color.YELLOW, "Removing Ollama..."));

  if (IS_WINDOWS) {
    await windowsUninstall((m) => console.log(paint(Color.YELLOW, m)));
    return;
  }

  const prefix = sudoPrefix();

  // Stop and disable systemd service if present
  const active = spawnSync("systemctl", ["is-active", "--quiet", "ollama"], {
    stdio: "ignore",
  });
  if (active.status === 0) {
    runPrefixed(prefix, ["systemctl", "stop", "ollama"]);
    runPrefixed(prefix, ["systemctl", "disable", "ollama"]);
  }

  // Remove all known ollama paths
  const existing = OLLAMA_PATHS.filter((p) => fs.existsSync(p));
  if (existing.length) {
    runPrefixed(prefix, ["rm", "-rf", ...existing]);
  }

  // Reload systemd if service file was removed
  if (existing.some((p) => p.includes("systemd"))) {
    runPrefixed(prefix, ["systemctl", "daemon-reload"]);
  }

  console.log(paint(Color.GREEN, "Ollama removed"));
}

// ── CLI ──────────────────────────────────────────────────────────────────────

function printHelp() {
  console.log(`usage: ollama-forge [command]

Ollama Forge manager — install, upgrade, check, remove

commands:
  install    Install Ollama
  upgrade    Upgrade Ollama to latest
  update     Check for available updates
  uninstall  Remove Ollama from system

With no command, starts the web UI instead.`);
}

export async function runCli(command) {
  const commands = { install, upgrade, update, uninstall };

  if (commands[command]) {
    await commands[command]();
    return 0;
  }
  printHelp();
  return command ? 1 : 0;
}

// Run automatically when this file is the entry point (e.g. `node cli.js`).
// Uses the basename so it stays inert when bundled into the unified binary.
if (path.basename(process.argv[1] || "") === "cli.js") {
  runCli(process.argv[2]).then((code) => {
    if (code) process.exitCode = code;
  });
}
