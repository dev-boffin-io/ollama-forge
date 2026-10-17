// server.js
// Zero-dependency HTTP backend + static host for the Ollama Forge web UI.

import http from "node:http";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";

import {
  autodetectOllama,
  loadOllamaBin,
  loadOllamaHost,
  readOllamaUsername,
  saveOllamaBin,
  parseSigninUsername,
  openBrowser,
  safeRemove,
  sleep,
  fmtSize,
  IS_WINDOWS,
} from "./lib/helpers.js";
import { spawnStream, run } from "./lib/exec.js";
import { checkServer, listModels, showModel, chatStream } from "./lib/ollama.js";
import { runManage, getCurrentVersion, getLatestVersion } from "./lib/manage.js";
import { getEmbeddedAsset, isEmbedded } from "./lib/assets.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PUBLIC_DIR = path.join(__dirname, "public");

const HOST = process.env.FORGE_HOST || "127.0.0.1";
const PORT = parseInt(process.env.FORGE_PORT || "11435", 10);
const isRoot =
  !IS_WINDOWS && typeof process.getuid === "function" && process.getuid() === 0;

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
};

// ── shared process state ─────────────────────────────────────────────────────
let serverProc = null;
let activeProc = null;
let activeChatAbort = null;

function resolveBin() {
  return loadOllamaBin() || autodetectOllama() || "ollama";
}

// ── helpers ──────────────────────────────────────────────────────────────────

function sendJson(res, code, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(code, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
  });
  res.end(body);
}

function readBody(req, limit = 8 * 1024 * 1024) {
  return new Promise((resolve) => {
    let data = "";
    let size = 0;
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > limit) {
        req.destroy();
        return resolve(null);
      }
      data += chunk;
    });
    req.on("end", () => {
      try {
        resolve(data ? JSON.parse(data) : {});
      } catch {
        resolve(null);
      }
    });
    req.on("error", () => resolve(null));
  });
}

function ndjsonStart(res) {
  res.writeHead(200, {
    "content-type": "application/x-ndjson; charset=utf-8",
    "cache-control": "no-store",
    connection: "keep-alive",
    "x-accel-buffering": "no",
  });
  return (obj) => {
    if (!res.writableEnded) res.write(JSON.stringify(obj) + "\n");
  };
}

function serveStatic(req, res, pathname) {
  const rel = pathname === "/" ? "index.html" : pathname.replace(/^\/+/, "");
  const type = MIME[path.extname(rel)] || "application/octet-stream";

  // Assets embedded in a single-file binary take precedence.
  const embedded = getEmbeddedAsset(rel);
  if (embedded != null) {
    res.writeHead(200, { "content-type": type, "cache-control": "no-cache" });
    return res.end(embedded);
  }

  const full = path.join(PUBLIC_DIR, rel);
  // prevent path traversal
  if (!full.startsWith(PUBLIC_DIR)) {
    res.writeHead(403);
    return res.end("Forbidden");
  }
  fs.readFile(full, (err, buf) => {
    if (err) {
      res.writeHead(404, { "content-type": "text/plain" });
      return res.end("Not found");
    }
    res.writeHead(200, { "content-type": type, "cache-control": "no-cache" });
    res.end(buf);
  });
}

// ── API handlers ─────────────────────────────────────────────────────────────

async function apiState(res) {
  const host = loadOllamaHost();
  const running = await checkServer(host, 2500);
  const username = readOllamaUsername();
  sendJson(res, 200, {
    host,
    ollamaBin: resolveBin(),
    serverRunning: running,
    signedIn: Boolean(username),
    username: username || "",
    needsSudo: !IS_WINDOWS && !isRoot,
    platform: process.platform,
  });
}

async function apiModels(res) {
  try {
    const models = await listModels(loadOllamaHost());
    sendJson(res, 200, {
      models: models.map((m) => ({
        name: m.name,
        size: m.size || 0,
        sizeHuman: fmtSize(m.size || 0),
        modified: (m.modified_at || "").slice(0, 10),
      })),
    });
  } catch (e) {
    sendJson(res, 502, { error: String(e) });
  }
}

async function apiShow(res, url) {
  const name = url.searchParams.get("name") || "";
  try {
    const info = await showModel(loadOllamaHost(), name);
    const mf = info.modelfile || "";
    const fromLine = mf
      .split("\n")
      .map((l) => l.trim())
      .find((l) => l.toUpperCase().startsWith("FROM "));
    const base = fromLine ? fromLine.slice(5).trim() : "Unknown";
    const det = info.details || {};
    sendJson(res, 200, {
      base,
      format: det.format || "?",
      family: det.family || "?",
      quantization: det.quantization_level || "?",
      size: info.size || 0,
      sizeHuman: fmtSize(info.size || 0),
      digest: String(info.digest || "").slice(0, 24),
    });
  } catch (e) {
    sendJson(res, 502, { error: String(e) });
  }
}

async function apiFs(res, url) {
  const requested = url.searchParams.get("path") || os.homedir();
  let target = path.resolve(requested);
  try {
    const stat = fs.statSync(target);
    if (stat.isFile()) target = path.dirname(target);
  } catch {
    target = os.homedir();
  }
  try {
    const entries = fs
      .readdirSync(target, { withFileTypes: true })
      .filter((e) => !e.name.startsWith("."))
      .map((e) => ({
        name: e.name,
        path: path.join(target, e.name),
        dir: e.isDirectory(),
      }))
      .sort((a, b) => (a.dir === b.dir ? a.name.localeCompare(b.name) : a.dir ? -1 : 1));
    sendJson(res, 200, {
      path: target,
      parent: path.dirname(target),
      home: os.homedir(),
      entries,
    });
  } catch (e) {
    sendJson(res, 500, { error: String(e) });
  }
}

async function apiRead(res, url) {
  const p = url.searchParams.get("path") || "";
  if (!p) return sendJson(res, 400, { error: "missing path" });
  try {
    const stat = fs.statSync(p);
    if (stat.isDirectory()) return sendJson(res, 400, { error: "is a directory" });
    if (stat.size > 1024 * 1024)
      return sendJson(res, 413, { error: "file too large (max 1 MB)" });
    const content = fs.readFileSync(p, "utf8");
    sendJson(res, 200, { content });
  } catch (e) {
    sendJson(res, 500, { error: String(e) });
  }
}

function openTaskProcess(proc) {
  activeProc = proc;
  proc.on("close", () => {
    if (activeProc === proc) activeProc = null;
  });
}

async function handleRun(params, send, res) {
  const bin = resolveBin();
  const host = loadOllamaHost();
  const onSpawn = (proc) => openTaskProcess(proc);
  const onLine = (text) => {
    send({ stream: "line", text });
    const m = text.match(/(\d+)%/);
    if (m) send({ stream: "progress", value: parseInt(m[1], 10) });
  };

  try {
    switch (params.task) {
      case "pull": {
        const model = String(params.model || "").trim();
        if (!model) {
          send({ stream: "line", text: "⚠️ Enter a model name." });
          break;
        }
        if (!(await checkServer(host, 3000))) {
          send({ stream: "line", text: "⚠️ Start the server first." });
          break;
        }
        send({ stream: "line", text: `⬇ Pulling ${model}…` });
        const code = await spawnStream(bin, ["pull", model], { onLine, onSpawn });
        if (code === 0) send({ stream: "reload", what: "models" });
        break;
      }

      case "push": {
        const model = String(params.model || "").trim();
        send({ stream: "line", text: `⬆ Pushing ${model}…` });
        await spawnStream(bin, ["push", model], { onLine, onSpawn });
        break;
      }

      case "create": {
        const name = String(params.name || "").trim();
        const content = String(params.modelfile || "");
        if (!name || !content.trim()) break;
        if (!(await checkServer(host, 3000))) {
          send({ stream: "line", text: "⚠️ Start the server first." });
          break;
        }
        const tmp = path.join(os.tmpdir(), `forge-${Date.now()}.Modelfile`);
        fs.writeFileSync(tmp, content);
        send({ stream: "line", text: `🛠 Creating '${name}'…` });
        const code = await spawnStream(bin, ["create", name, "-f", tmp], {
          onLine,
          onSpawn,
        });
        safeRemove(tmp);
        if (code === 0) send({ stream: "reload", what: "models" });
        break;
      }

      case "chat": {
        const model = String(params.model || "").trim();
        const messages = Array.isArray(params.messages) ? params.messages : [];
        if (!model || messages.length === 0) {
          send({ stream: "line", text: "⚠️ Pick a model and type a message." });
          break;
        }
        if (!(await checkServer(host, 3000))) {
          send({ stream: "line", text: "⚠️ Start the server first." });
          break;
        }
        const controller = new AbortController();
        activeChatAbort = controller;
        // If the browser goes away, stop streaming from Ollama too.
        res.on("close", () => {
          if (!res.writableEnded) controller.abort();
        });
        try {
          await chatStream(host, {
            model,
            messages,
            signal: controller.signal,
            onThinking: (text) => send({ stream: "chat", type: "thinking", text }),
            onContent: (text) => send({ stream: "chat", type: "content", text }),
            onDone: (info) => send({ stream: "chat", type: "stats", ...info }),
          });
        } catch (e) {
          if (controller.signal.aborted) send({ stream: "line", text: "⛔ Stopped." });
          else send({ stream: "line", text: `❌ ${e}` });
        } finally {
          if (activeChatAbort === controller) activeChatAbort = null;
        }
        break;
      }

      case "remove": {
        const model = String(params.model || "").trim();
        const r = await run(bin, ["rm", model], {});
        if (r.code === 0) {
          send({ stream: "line", text: `🗑 Removed ${model}` });
          send({ stream: "reload", what: "models" });
        } else {
          send({ stream: "line", text: `❌ Remove failed: ${r.stderr.trim()}` });
        }
        break;
      }

      case "serve-start": {
        send({ stream: "line", text: "▶ Starting ollama serve…" });
        try {
          serverProc = spawn(bin, ["serve"], { stdio: "ignore" });
          serverProc.on("exit", () => {
            if (serverProc && serverProc.exitCode !== null) serverProc = null;
          });
        } catch (e) {
          send({ stream: "line", text: `❌ 'ollama' not found: ${e}` });
          break;
        }
        let ready = false;
        for (let i = 0; i < 30; i++) {
          await sleep(1000);
          if (await checkServer(host, 2000)) {
            ready = true;
            break;
          }
        }
        if (ready) send({ stream: "line", text: "🟢 Server ready!" });
        else
          send({
            stream: "line",
            text: "⚠️ Server may be up but not responding — try Refresh.",
          });
        send({ stream: "status" });
        break;
      }

      case "serve-stop": {
        if (serverProc && serverProc.exitCode === null) {
          serverProc.kill("SIGTERM");
          serverProc = null;
        } else {
          if (IS_WINDOWS) spawn("taskkill", ["/IM", "ollama.exe", "/F"], { stdio: "ignore" });
          else spawn("pkill", ["-TERM", "-x", "ollama"], { stdio: "ignore" });
        }
        for (let i = 0; i < 12; i++) {
          await sleep(400);
          if (!(await checkServer(host, 1000))) break;
        }
        send({ stream: "line", text: "🛑 Ollama stopped." });
        send({ stream: "status" });
        break;
      }

      case "signin": {
        send({ stream: "line", text: "🔑 Running ollama signin…" });
        let urlOpened = false;
        let foundUser = null;
        await spawnStream(bin, ["signin"], {
          onSpawn,
          onLine: (line) => {
            send({ stream: "line", text: `  ${line}` });
            const u = parseSigninUsername(line);
            if (u) foundUser = u;
            if (!urlOpened) {
              const m = line.match(/https?:\/\/\S+/);
              if (m) {
                urlOpened = true;
                const url = m[0].replace(/[.)]+$/, "");
                if (openBrowser(url)) {
                  send({ stream: "line", text: `🌐 Opened browser: ${url}` });
                } else {
                  send({ stream: "line", text: "⚠️ Browser open failed — copy URL above." });
                }
              }
            }
          },
        });
        const username = foundUser || readOllamaUsername();
        if (username) {
          send({ stream: "line", text: `✅ Signed in as: ${username}` });
          send({ stream: "auth", signedIn: true, username });
        } else {
          send({ stream: "line", text: "🔴 Not signed in — try again." });
          send({ stream: "auth", signedIn: false, username: "" });
        }
        break;
      }

      case "signout": {
        const r = await run(bin, ["signout"], {});
        let ok = r.code === 0;
        if (!ok) {
          for (const p of [
            path.join(os.homedir(), ".ollama", "config"),
            path.join(os.homedir(), ".config", "ollama", "config"),
          ]) {
            if (!fs.existsSync(p)) continue;
            try {
              const d = JSON.parse(fs.readFileSync(p, "utf8"));
              for (const k of ["username", "user", "name", "token"]) delete d[k];
              fs.writeFileSync(p, JSON.stringify(d));
              ok = true;
            } catch {
              /* ignore */
            }
          }
        }
        if (ok) {
          send({ stream: "line", text: "🚪 Signed out." });
          send({ stream: "auth", signedIn: false, username: "" });
        } else {
          send({ stream: "line", text: "❌ Could not sign out." });
        }
        break;
      }

      case "manage": {
        const action = String(params.action || "");
        await runManage(action, {
          bin,
          password: params.password || "",
          emit: (text) => {
            send({ stream: "line", text });
            const m = text.match(/(\d+)%/);
            if (m) send({ stream: "progress", value: parseInt(m[1], 10) });
          },
          onSpawn,
        });
        send({ stream: "manage-done" });
        break;
      }

      default:
        send({ stream: "line", text: `⚠️ Unknown task '${params.task}'.` });
    }
  } catch (e) {
    send({ stream: "line", text: `❌ ${e}` });
  }

  send({ stream: "done" });
  if (!res.writableEnded) res.end();
}

// ── router ───────────────────────────────────────────────────────────────────

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);
  const { pathname } = url;

  if (pathname === "/api/state" && req.method === "GET") return apiState(res);
  if (pathname === "/api/models" && req.method === "GET") return apiModels(res);
  if (pathname === "/api/show" && req.method === "GET") return apiShow(res, url);
  if (pathname === "/api/fs" && req.method === "GET") return apiFs(res, url);
  if (pathname === "/api/read" && req.method === "GET") return apiRead(res, url);
  if (pathname === "/api/detect" && req.method === "GET")
    return sendJson(res, 200, { path: autodetectOllama() });

  if (pathname === "/api/settings" && req.method === "POST") {
    const body = await readBody(req);
    if (body && typeof body.ollama_bin === "string") saveOllamaBin(body.ollama_bin);
    return sendJson(res, 200, { ok: true });
  }

  if (pathname === "/api/manage-version" && req.method === "GET") {
    const bin = resolveBin();
    const [current, latest] = await Promise.all([
      getCurrentVersion(bin),
      getLatestVersion(),
    ]);
    return sendJson(res, 200, { current, latest });
  }

  if (pathname === "/api/cancel" && req.method === "POST") {
    if (activeChatAbort) {
      activeChatAbort.abort();
      activeChatAbort = null;
    }
    if (activeProc && activeProc.exitCode === null) {
      activeProc.kill("SIGTERM");
      setTimeout(() => {
        try {
          if (activeProc && activeProc.exitCode === null) activeProc.kill("SIGKILL");
        } catch {
          /* ignore */
        }
      }, 4000);
    }
    return sendJson(res, 200, { ok: true });
  }

  if (pathname === "/api/run" && req.method === "POST") {
    const body = await readBody(req);
    if (!body) return sendJson(res, 400, { error: "invalid body" });
    const send = ndjsonStart(res);
    await handleRun(body, send, res);
    return;
  }

  if (pathname.startsWith("/api/")) return sendJson(res, 404, { error: "not found" });

  if (req.method === "GET" || req.method === "HEAD") return serveStatic(req, res, pathname);

  res.writeHead(405);
  res.end("Method not allowed");
});

export function startServer(opts = {}) {
  const host = opts.host || HOST;
  const port = opts.port || PORT;
  server.listen(port, host, () => {
    console.log(`🦙 Ollama Forge running at http://${host}:${port}`);
    console.log(`   Ollama host: ${loadOllamaHost()}`);
    console.log(`   Binary     : ${resolveBin()}`);
    if (isEmbedded()) console.log("   Assets     : embedded (single-file binary)");
  });
  return server;
}

// Start automatically when this file is the entry point (e.g. `node server.js`).
// Uses the basename so it stays inert when bundled into the unified binary.
if (path.basename(process.argv[1] || "") === "server.js") startServer();
