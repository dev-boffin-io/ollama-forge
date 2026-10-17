// build.mjs
// Build a single, self-contained native binary with Node's Single Executable
// Application (SEA) support.
//
//   npm run build            # bundle + SEA blob + native binary (dist/)
//   node build.mjs --bundle-only   # just the bundled .cjs (no native binary)
//
// Build-time only dependencies: esbuild (bundling) and postject (blob
// injection). The produced binary has no runtime dependencies.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const root = path.dirname(fileURLToPath(import.meta.url));
const distDir = path.join(root, "dist");
const publicDir = path.join(root, "public");
const isWindows = process.platform === "win32";
const bundleOnly = process.argv.includes("--bundle-only");

const SENTINEL = "NODE_SEA_FUSE_fce680ab2cc467b6e072b8b5df1996b2";
const binName = isWindows ? "ollama-forge.exe" : "ollama-forge";
const binPath = path.join(distDir, binName);
const bundlePath = path.join(distDir, "ollama-forge.cjs");
const blobPath = path.join(distDir, "sea-prep.blob");
const seaConfigPath = path.join(distDir, "sea-config.json");

function log(msg) {
  console.log(`[build] ${msg}`);
}

function collectAssets() {
  const assets = {};
  for (const entry of fs.readdirSync(publicDir, { withFileTypes: true })) {
    if (!entry.isFile()) continue;
    assets[entry.name] = fs.readFileSync(path.join(publicDir, entry.name), "utf8");
  }
  return assets;
}

async function bundle(assets) {
  // esbuild is a build-time dependency; fail with a helpful message if absent.
  let esbuild;
  try {
    esbuild = await import("esbuild");
  } catch {
    console.error("Missing dev dependencies. Run: npm install");
    process.exit(1);
  }

  await esbuild.build({
    entryPoints: [path.join(root, "main.js")],
    outfile: bundlePath,
    bundle: true,
    platform: "node",
    target: "node18",
    format: "cjs",
    legalComments: "none",
    // Embed the front-end so the binary is fully self-contained.
    define: {
      __EMBEDDED_ASSETS__: JSON.stringify(assets),
      // esbuild can't keep import.meta under cjs; shim it via the banner below.
      "import.meta.url": "__importMetaUrl",
    },
    banner: {
      js: 'const __importMetaUrl = require("node:url").pathToFileURL(__filename).href;',
    },
  });
  log(`bundled -> ${path.relative(root, bundlePath)}`);
}

function buildBlob() {
  const seaConfig = {
    main: bundlePath,
    output: blobPath,
    disableExperimentalSEAWarning: true,
  };
  fs.writeFileSync(seaConfigPath, JSON.stringify(seaConfig, null, 2));
  log("generating SEA blob...");
  execFileSync(process.execPath, ["--experimental-sea-config", seaConfigPath], {
    stdio: "inherit",
  });
  log(`blob -> ${path.relative(root, blobPath)}`);
}

function injectBinary() {
  fs.copyFileSync(process.execPath, binPath);
  log(`copied runtime -> ${path.relative(root, binPath)}`);

  if (process.platform === "darwin") {
    // macOS refuses to inject into a signed binary.
    try {
      execFileSync("codesign", ["--remove-signature", binPath], { stdio: "inherit" });
    } catch {
      log("codesign --remove-signature skipped");
    }
  }

  const postjectCli = require.resolve("postject/dist/cli.js");
  const args = [
    postjectCli,
    binPath,
    "NODE_SEA_BLOB",
    blobPath,
    "--sentinel-fuse",
    SENTINEL,
  ];
  if (process.platform === "darwin") args.push("--macho-segment-name", "NODE_SEA");

  log("injecting blob...");
  execFileSync(process.execPath, args, { stdio: "inherit" });

  if (process.platform === "darwin") {
    try {
      execFileSync("codesign", ["--sign", "-", binPath], { stdio: "inherit" });
    } catch {
      log("codesign --sign skipped");
    }
  }

  if (!isWindows) fs.chmodSync(binPath, 0o755);
  log(`binary -> ${path.relative(root, binPath)}`);
}

async function main() {
  fs.rmSync(distDir, { recursive: true, force: true });
  fs.mkdirSync(distDir, { recursive: true });

  const assets = collectAssets();
  log(`embedded ${Object.keys(assets).length} front-end asset(s)`);
  await bundle(assets);

  if (bundleOnly) {
    log("done (bundle only)");
    return;
  }

  buildBlob();
  injectBinary();
  log("done");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
