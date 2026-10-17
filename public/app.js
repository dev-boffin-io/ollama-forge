// app.js — vanilla frontend for Ollama Forge. No external dependencies.

const $ = (id) => document.getElementById(id);

const POPULAR_MODELS = [
  "llama3.2", "llama3.2:1b", "llama3.2:3b",
  "llama3.1", "llama3.1:8b", "llama3.1:70b",
  "llama3", "llama2", "llama2:13b",
  "mistral", "mistral:7b", "mistral-nemo",
  "mixtral", "mixtral:8x7b",
  "gemma3", "gemma3:1b", "gemma3:4b", "gemma3:12b", "gemma3:27b",
  "gemma2", "gemma2:2b", "gemma2:9b", "gemma2:27b",
  "qwen2.5", "qwen2.5:0.5b", "qwen2.5:1.5b", "qwen2.5:3b",
  "qwen2.5:7b", "qwen2.5:14b", "qwen2.5:32b", "qwen2.5:72b",
  "qwen2.5-coder", "qwen2.5-coder:1.5b", "qwen2.5-coder:7b",
  "qwen2.5-coder:14b", "qwen2.5-coder:32b",
  "deepseek-r1", "deepseek-r1:1.5b", "deepseek-r1:7b", "deepseek-r1:8b",
  "deepseek-r1:14b", "deepseek-r1:32b", "deepseek-r1:70b", "deepseek-r1:671b",
  "deepseek-coder-v2", "deepseek-v3",
  "phi4", "phi4-mini", "phi3", "phi3:mini", "phi3:medium", "phi3.5",
  "codellama", "codellama:7b", "codellama:13b", "codellama:34b", "codellama:70b",
  "starcoder2", "starcoder2:3b", "starcoder2:7b", "starcoder2:15b",
  "dolphin3", "dolphin-mistral", "dolphin-llama3",
  "nous-hermes2", "openhermes", "wizardlm2",
  "vicuna", "orca-mini", "solar",
  "command-r", "command-r-plus", "zephyr",
  "llava", "llava:13b", "llava:34b", "llava-llama3", "moondream", "bakllava",
  "nomic-embed-text", "mxbai-embed-large",
  "all-minilm", "snowflake-arctic-embed", "bge-m3", "bge-large",
];

const state = {
  serverRunning: false,
  signedIn: false,
  username: "",
  currentModel: null,
  ollamaBin: "",
  needsSudo: false,
};

// ── API helpers ───────────────────────────────────────────────────────────────

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function postJSON(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return r.json().catch(() => ({}));
}

async function streamRun(params, onEvent) {
  const res = await fetch("/api/run", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n")) !== -1) {
      const line = buf.slice(0, idx);
      buf = buf.slice(idx + 1);
      if (!line.trim()) continue;
      try {
        onEvent(JSON.parse(line));
      } catch {
        /* ignore malformed frame */
      }
    }
  }
}

// ── Logging / progress ────────────────────────────────────────────────────────

function appendLog(text) {
  const el = $("log");
  el.textContent += (el.textContent ? "\n" : "") + text;
  const lines = el.textContent.split("\n");
  if (lines.length > 1500) el.textContent = lines.slice(-1200).join("\n");
  el.scrollTop = el.scrollHeight;
}

function manageAppend(text) {
  const el = $("manageLog");
  el.textContent += (el.textContent ? "\n" : "") + text;
  const lines = el.textContent.split("\n");
  if (lines.length > 1500) el.textContent = lines.slice(-1200).join("\n");
  el.scrollTop = el.scrollHeight;
}

function setProgress(pct) {
  $("progressWrap").hidden = false;
  $("progressBar").style.width = `${Math.max(0, Math.min(100, pct))}%`;
}

function clearProgress() {
  $("progressBar").style.width = "0%";
  $("progressWrap").hidden = true;
}

// ── Busy state (models tab) ───────────────────────────────────────────────────

function setBusy(busy) {
  $("btnPull").disabled = busy;
  $("btnCancel").disabled = !busy;
  if (busy) setProgress(0);
  else clearProgress();
  updatePush();
}

function setManageBusy(busy) {
  for (const id of ["btnCheckVer", "btnInstall", "btnUpgrade", "btnUninstall"]) {
    $(id).disabled = busy;
  }
  $("btnManageCancel").hidden = !busy;
}

// ── State / rendering ─────────────────────────────────────────────────────────

async function refreshState() {
  try {
    const s = await getJSON("/api/state");
    const wasRunning = state.serverRunning;
    Object.assign(state, s);
    if (!state.binPathInitialized) {
      $("binPath").value = s.ollamaBin || "";
      state.binPathInitialized = true;
    }
    renderServer();
    renderAuth();
    updatePush();
    if (s.serverRunning && !wasRunning) loadModels();
    if (!s.serverRunning && wasRunning) clearModels();
  } catch {
    /* server not reachable */
  }
}

function renderServer() {
  const pill = $("serverPill");
  if (state.serverRunning) {
    pill.textContent = "🟢 Ollama Server: Running";
    pill.className = "pill on";
    $("btnServe").textContent = "⏹ Stop Ollama Serve";
    $("btnRefresh").disabled = false;
  } else {
    pill.textContent = "🔴 Ollama Server: Stopped";
    pill.className = "pill off";
    $("btnServe").textContent = "▶ Start Ollama Serve";
    $("btnRefresh").disabled = true;
  }
}

function renderAuth() {
  const lbl = $("authLabel");
  $("btnSignin").disabled = state.signedIn;
  $("btnSignout").disabled = !state.signedIn;
  if (state.signedIn) {
    lbl.textContent = `🟢 Signed in as ${state.username}`;
    lbl.className = "auth on";
  } else {
    lbl.textContent = "🔴 Not signed in to ollama.com";
    lbl.className = "auth off";
  }
}

function clearModels() {
  $("modelList").innerHTML = "";
  $("modelDetails").textContent = "Select a model to see details…";
  state.currentModel = null;
  $("selectedLabel").textContent = "No model selected";
  $("btnRemove").disabled = true;
  updatePush();
}

async function loadModels() {
  let data;
  try {
    data = await getJSON("/api/models");
  } catch (e) {
    appendLog(`⚠️ Could not load models: ${e.message}`);
    return;
  }
  const list = $("modelList");
  list.innerHTML = "";
  for (const m of data.models) {
    const li = document.createElement("li");
    li.textContent = `${m.name}  |  ${m.sizeHuman}  |  ${m.modified}`;
    li.dataset.name = m.name;
    li.addEventListener("click", () => selectModel(m.name, li));
    list.appendChild(li);
  }
  appendLog(`✅ ${data.models.length} models loaded.`);
}

function selectModel(name, li) {
  state.currentModel = name;
  for (const el of document.querySelectorAll("#modelList li")) {
    el.classList.toggle("selected", el === li);
  }
  $("selectedLabel").textContent = `Selected: ${name}`;
  $("btnRemove").disabled = false;
  updatePush();
  showDetails(name);
}

async function showDetails(name) {
  const box = $("modelDetails");
  box.textContent = "Loading…";
  try {
    const d = await getJSON(`/api/show?name=${encodeURIComponent(name)}`);
    box.innerHTML =
      `<b>Base:</b> ${escapeHtml(d.base)}<br>` +
      `<b>Format:</b> ${escapeHtml(d.format)}<br>` +
      `<b>Family:</b> ${escapeHtml(d.family)}<br>` +
      `<b>Quantization:</b> ${escapeHtml(d.quantization)}<br>` +
      `<b>Size:</b> ${escapeHtml(d.sizeHuman)}<br>` +
      `<b>Digest:</b> ${escapeHtml(d.digest)}…`;
  } catch (e) {
    box.textContent = `Failed to load details: ${e.message}`;
  }
}

function updatePush() {
  const can =
    Boolean(state.currentModel) &&
    String(state.currentModel).includes("/") &&
    state.signedIn;
  $("btnPush").disabled = !can;
}

function escapeHtml(s) {
  return String(s ?? "?").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

// ── Streaming event handlers ──────────────────────────────────────────────────

function handleModelEvent(ev) {
  if (ev.stream === "line") appendLog(ev.text);
  else if (ev.stream === "progress") setProgress(ev.value);
  else if (ev.stream === "reload" && ev.what === "models") loadModels();
  else if (ev.stream === "status") refreshState();
  else if (ev.stream === "auth") {
    state.signedIn = ev.signedIn;
    state.username = ev.username || "";
    renderAuth();
    updatePush();
  }
}

function handleManageEvent(ev) {
  if (ev.stream === "line") {
    manageAppend(ev.text);
    if (ev.text.startsWith("Current version:")) {
      $("curVer").textContent = "Installed version:  " + ev.text.split(":").slice(1).join(":").trim();
    } else if (ev.text.startsWith("Latest version")) {
      $("latVer").textContent = "Latest version:  " + ev.text.split(":").slice(1).join(":").trim();
    }
  } else if (ev.stream === "progress") {
    setProgress(ev.value);
  } else if (ev.stream === "manage-done") {
    refreshVersion();
    loadModels();
  }
}

async function runModelTask(params) {
  setBusy(true);
  try {
    await streamRun(params, handleModelEvent);
  } catch (e) {
    appendLog(`❌ ${e.message}`);
  } finally {
    setBusy(false);
  }
}

// ── Models tab actions ────────────────────────────────────────────────────────

async function toggleServe() {
  const task = state.serverRunning ? "serve-stop" : "serve-start";
  $("btnServe").disabled = true;
  try {
    await streamRun({ task }, handleModelEvent);
  } catch (e) {
    appendLog(`❌ ${e.message}`);
  } finally {
    $("btnServe").disabled = false;
    refreshState();
  }
}

function doPull() {
  const model = $("inpPull").value.trim();
  if (!model) return appendLog("⚠️ Enter a model name.");
  if (!state.serverRunning) return appendLog("⚠️ Start the server first.");
  appendLog(`⬇ Pulling ${model}…`);
  runModelTask({ task: "pull", model });
}

function doPush() {
  if (!state.currentModel || !state.currentModel.includes("/")) {
    return appendLog("⚠️ Model name must contain username/ prefix.");
  }
  appendLog(`⬆ Pushing ${state.currentModel}…`);
  runModelTask({ task: "push", model: state.currentModel });
}

function doCreate() {
  const name = $("inpCreate").value.trim();
  const modelfile = $("editMf").value;
  if (!name || !modelfile.trim()) return;
  if (!state.serverRunning) return appendLog("⚠️ Start the server first.");
  appendLog(`🛠 Creating '${name}'…`);
  runModelTask({ task: "create", name, modelfile });
}

async function doRemove() {
  if (!state.currentModel) return;
  const ok = await confirmDialog("Confirm", `Permanently delete '${state.currentModel}'?`);
  if (!ok) return;
  appendLog(`🗑 Removing ${state.currentModel}…`);
  runModelTask({ task: "remove", model: state.currentModel });
}

async function cancelModel() {
  await postJSON("/api/cancel", {});
  appendLog("⛔ Cancel requested.");
}

function updCreate() {
  const ok = Boolean($("inpCreate").value.trim() && $("editMf").value.trim());
  $("btnCreate").disabled = !ok;
}

// ── Auth ──────────────────────────────────────────────────────────────────────

async function doSignin() {
  $("btnSignin").disabled = true;
  try {
    await streamRun({ task: "signin" }, handleModelEvent);
  } catch (e) {
    appendLog(`❌ signin error: ${e.message}`);
  } finally {
    refreshState();
    renderAuth();
  }
}

async function doSignout() {
  const ok = await confirmDialog("Confirm", "Sign out from ollama.com?");
  if (!ok) return;
  try {
    await streamRun({ task: "signout" }, handleModelEvent);
  } catch (e) {
    appendLog(`❌ signout error: ${e.message}`);
  } finally {
    refreshState();
  }
}

// ── Manage tab ────────────────────────────────────────────────────────────────

async function refreshVersion() {
  try {
    const v = await getJSON("/api/manage-version");
    $("curVer").textContent = `Installed version:  ${v.current || "not installed"}`;
    $("latVer").textContent = `Latest version:  ${v.latest || "unknown"}`;
  } catch {
    /* ignore */
  }
}

async function runManageAction(action) {
  let password = "";
  if (state.needsSudo) {
    const p = await askPassword();
    if (p === null) return;
    password = p;
  }
  const labels = {
    update: "🔍 Checking versions…",
    install: "⬇ Installing Ollama…",
    upgrade: "⬆ Upgrading Ollama…",
    uninstall: "🗑 Uninstalling Ollama…",
  };
  manageAppend(labels[action] || `Running ${action}…`);
  setManageBusy(true);
  try {
    await streamRun({ task: "manage", action, password }, handleManageEvent);
  } catch (e) {
    manageAppend(`❌ ${e.message}`);
  } finally {
    setManageBusy(false);
  }
}

async function manageInstall() {
  await runManageAction("install");
}

async function manageUpgrade() {
  await runManageAction("upgrade");
}

async function manageUninstall() {
  const ok = await confirmDialog(
    "Uninstall Ollama",
    "This will remove Ollama and all its files.\nAre you sure?"
  );
  if (!ok) return;
  await runManageAction("uninstall");
}

async function manageCancel() {
  await postJSON("/api/cancel", {});
  manageAppend("⚠️ Cancel requested.");
}

async function manageAuto() {
  try {
    const d = await getJSON("/api/detect");
    if (d.path) {
      $("binPath").value = d.path;
      manageAppend(`✅ Auto-detected: ${d.path}`);
    } else {
      manageAppend("⚠️ Could not auto-detect ollama binary.");
    }
  } catch (e) {
    manageAppend(`❌ ${e.message}`);
  }
}

async function manageSave() {
  const p = $("binPath").value.trim();
  if (!p) return manageAppend("⚠️ Path is empty.");
  await postJSON("/api/settings", { ollama_bin: p });
  state.ollamaBin = p;
  manageAppend(`✅ Saved path: ${p}`);
  refreshState();
}

// ── File browser modal ────────────────────────────────────────────────────────

let fsMode = "mf";
let fsCurrent = "";

function openFs(mode) {
  fsMode = mode;
  $("fsTitle").textContent = mode === "bin" ? "Select Ollama Binary" : "Select Modelfile";
  $("fsModal").hidden = false;
  fsNavigate(state.ollamaBin && mode === "bin" ? state.ollamaBin : "");
}

function closeFs() {
  $("fsModal").hidden = true;
}

async function fsNavigate(target) {
  let data;
  try {
    data = await getJSON(`/api/fs?path=${encodeURIComponent(target || "")}`);
  } catch (e) {
    manageAppend(`❌ ${e.message}`);
    return;
  }
  fsCurrent = data.path;
  $("fsPath").value = data.path;
  const ul = $("fsList");
  ul.innerHTML = "";
  for (const entry of data.entries) {
    const li = document.createElement("li");
    li.textContent = entry.dir ? `📁 ${entry.name}` : `📄 ${entry.name}`;
    li.addEventListener("click", () => {
      if (entry.dir) fsNavigate(entry.path);
      else fsPickFile(entry.path);
    });
    ul.appendChild(li);
  }
}

async function fsPickFile(p) {
  if (fsMode === "bin") {
    $("binPath").value = p;
    closeFs();
    return;
  }
  try {
    const d = await getJSON(`/api/read?path=${encodeURIComponent(p)}`);
    $("editMf").value = d.content || "";
    $("mfLabel").textContent = p.split("/").pop();
    appendLog(`📂 Loaded: ${p.split("/").pop()}`);
    updCreate();
    closeFs();
  } catch (e) {
    appendLog(`❌ ${e.message}`);
  }
}

// ── Password modal ────────────────────────────────────────────────────────────

function askPassword() {
  return new Promise((resolve) => {
    const modal = $("pwdModal");
    const input = $("pwdInput");
    modal.hidden = false;
    input.value = "";
    input.focus();
    const done = (val) => {
      modal.hidden = true;
      input.onkeydown = null;
      $("pwdOk").onclick = null;
      $("pwdCancel").onclick = null;
      resolve(val);
    };
    $("pwdOk").onclick = () => done(input.value);
    $("pwdCancel").onclick = () => done(null);
    input.onkeydown = (e) => {
      if (e.key === "Enter") done(input.value);
      if (e.key === "Escape") done(null);
    };
  });
}

// ── Confirm modal ─────────────────────────────────────────────────────────────

function confirmDialog(title, text) {
  return new Promise((resolve) => {
    const modal = $("confirmModal");
    $("confirmTitle").textContent = title;
    $("confirmText").textContent = text;
    modal.hidden = false;
    const done = (val) => {
      modal.hidden = true;
      $("confirmYes").onclick = null;
      $("confirmNo").onclick = null;
      resolve(val);
    };
    $("confirmYes").onclick = () => done(true);
    $("confirmNo").onclick = () => done(false);
  });
}

// ── Tabs ──────────────────────────────────────────────────────────────────────

function initTabs() {
  for (const btn of document.querySelectorAll(".tab")) {
    btn.addEventListener("click", () => {
      for (const t of document.querySelectorAll(".tab")) t.classList.toggle("active", t === btn);
      for (const p of document.querySelectorAll(".tab-panel"))
        p.classList.toggle("active", p.id === `tab-${btn.dataset.tab}`);
    });
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────

function initPopularModels() {
  const dl = $("popularModels");
  for (const m of POPULAR_MODELS) {
    const opt = document.createElement("option");
    opt.value = m;
    dl.appendChild(opt);
  }
}

function wire() {
  initTabs();
  initPopularModels();

  $("btnServe").addEventListener("click", toggleServe);
  $("btnRefresh").addEventListener("click", loadModels);
  $("btnPull").addEventListener("click", doPull);
  $("btnCancel").addEventListener("click", cancelModel);
  $("btnCreate").addEventListener("click", doCreate);
  $("btnPush").addEventListener("click", doPush);
  $("btnRemove").addEventListener("click", doRemove);
  $("btnSignin").addEventListener("click", doSignin);
  $("btnSignout").addEventListener("click", doSignout);

  $("inpPull").addEventListener("keydown", (e) => {
    if (e.key === "Enter") doPull();
  });
  $("inpCreate").addEventListener("input", updCreate);
  $("editMf").addEventListener("input", updCreate);

  $("btnBrowseMf").addEventListener("click", () => openFs("mf"));
  $("btnPathAuto").addEventListener("click", manageAuto);
  $("btnPathBrowse").addEventListener("click", () => openFs("bin"));
  $("btnPathSave").addEventListener("click", manageSave);
  $("btnCheckVer").addEventListener("click", () => runManageAction("update"));
  $("btnInstall").addEventListener("click", manageInstall);
  $("btnUpgrade").addEventListener("click", manageUpgrade);
  $("btnUninstall").addEventListener("click", manageUninstall);
  $("btnManageCancel").addEventListener("click", manageCancel);

  $("fsUp").addEventListener("click", () => {
    const parts = fsCurrent.split("/");
    parts.pop();
    fsNavigate(parts.join("/") || "/");
  });
  $("fsHome").addEventListener("click", () => fsNavigate(""));
  $("fsClose").addEventListener("click", closeFs);

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeFs();
      $("pwdModal").hidden = true;
      $("confirmModal").hidden = true;
    }
  });
}

wire();
refreshState();
setInterval(refreshState, 5000);
