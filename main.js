#!/usr/bin/env node
// main.js
// Unified entry point for Ollama Forge.
//
//   ollama-forge                 start the web UI (default)
//   ollama-forge install|upgrade|update|uninstall   CLI management commands
//
// This is the file bundled into the single-file native binary.

import { startServer } from "./server.js";
import { runCli } from "./cli.js";

const CLI_COMMANDS = new Set(["install", "upgrade", "update", "uninstall"]);
const SERVE_COMMANDS = new Set(["serve", "start", "web", "server", "--serve"]);
const HELP_COMMANDS = new Set(["help", "-h", "--help"]);

async function main() {
  const command = process.argv[2];

  if (CLI_COMMANDS.has(command)) {
    const code = await runCli(command);
    if (code) process.exitCode = code;
    return;
  }

  if (!command || SERVE_COMMANDS.has(command)) {
    startServer();
    return;
  }

  if (HELP_COMMANDS.has(command)) {
    await runCli(command); // prints help; exit 0
    return;
  }

  // Unknown command — show CLI help and exit non-zero.
  const code = await runCli(command);
  if (code) process.exitCode = code;
}

main();
