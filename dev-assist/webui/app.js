const chatLog = document.getElementById('chatLog');
const input = document.getElementById('input');
const sendBtn = document.getElementById('sendBtn');
const clearBtn = document.getElementById('clearBtn');
const fileInput = document.getElementById('fileInput');
const fileNameEl = document.getElementById('fileName');
const engineSelect = document.getElementById('engineSelect');
const modelSelect = document.getElementById('modelSelect');
const statusDot = document.getElementById('statusDot');
const statusText = document.getElementById('statusText');
const ollamaToggleBtn = document.getElementById('ollamaToggleBtn');
const refreshBtn = document.getElementById('refreshBtn');

let currentStatus = 'unknown';
let pendingFile = null;

function addMessage(role, content, author) {
  const div = document.createElement('div');
  div.className = `msg ${role}`;
  if (author) {
    const a = document.createElement('span');
    a.className = 'author';
    a.textContent = author;
    div.appendChild(a);
  }
  const body = document.createElement('span');
  body.textContent = content;
  div.appendChild(body);
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
  return body; // return text node holder so callers can stream-append
}

function renderStatus(status) {
  currentStatus = status;
  statusDot.className = `dot dot-${status}`;
  const labels = {
    running: '🟢 running',
    stopped: '🔴 stopped',
    not_installed: '⚫ not installed',
    unknown: '❓ unknown',
  };
  statusText.textContent = labels[status] || status;

  if (status === 'running') {
    ollamaToggleBtn.textContent = '⏹ Stop';
    ollamaToggleBtn.disabled = false;
  } else if (status === 'stopped') {
    ollamaToggleBtn.textContent = '▶ Start';
    ollamaToggleBtn.disabled = false;
  } else {
    ollamaToggleBtn.textContent = '—';
    ollamaToggleBtn.disabled = true;
  }
}

function setModels(models, selected) {
  modelSelect.innerHTML = '';
  if (!models || models.length === 0) {
    const opt = document.createElement('option');
    opt.value = '';
    opt.textContent = '⚠ no models — start ollama';
    modelSelect.appendChild(opt);
    return;
  }
  for (const m of models) {
    const opt = document.createElement('option');
    opt.value = m;
    opt.textContent = m;
    if (m === selected) opt.selected = true;
    modelSelect.appendChild(opt);
  }
}

async function init() {
  try {
    const res = await fetch('/api/init');
    const data = await res.json();
    engineSelect.value = data.engine || 'ollama';
    setModels(data.models, data.ollama_model);
    renderStatus(data.ollama_status);

    for (const m of (data.history || [])) {
      addMessage(m.role === 'user' ? 'user' : 'assistant', m.content);
    }
    if (!data.history || data.history.length === 0) {
      addMessage('system', `dev-assist ready — ${data.engine}/${data.ollama_model || '(no model)'}\n📎 Attach files · "clear" · "history" · "ollama on/off/status" · "help"`);
    }
  } catch (e) {
    addMessage('system', `⚠️ Could not reach backend: ${e}`);
  }
}

async function refreshStatus() {
  const res = await fetch('/api/status');
  const data = await res.json();
  renderStatus(data.status);
}

async function toggleOllama() {
  ollamaToggleBtn.disabled = true;
  const action = currentStatus === 'running' ? 'stop' : 'start';
  addMessage('system', action === 'start' ? '⏳ Starting ollama…' : '⏳ Stopping ollama…', 'ollama');
  const res = await fetch(`/api/ollama/${action}`, { method: 'POST' });
  const data = await res.json();
  addMessage('system', data.message, 'ollama');
  renderStatus(data.status);
  if (data.models) setModels(data.models, data.models[0]);
}

async function saveSettings() {
  await fetch('/api/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      engine: engineSelect.value,
      ollama_model: modelSelect.value,
    }),
  });
}

async function clearChat() {
  await fetch('/api/clear', { method: 'POST' });
  chatLog.innerHTML = '';
  addMessage('system', '🗑️ Chat history cleared.');
}

async function sendMessage() {
  const text = input.value.trim();
  if (!text && !pendingFile) return;

  addMessage('user', text + (pendingFile ? `\n[attached: ${pendingFile.name}]` : ''));
  input.value = '';
  autoGrow();

  const formData = new FormData();
  formData.append('message', text);
  if (pendingFile) {
    formData.append('file', pendingFile);
  }
  const attachedFile = pendingFile;
  pendingFile = null;
  fileNameEl.textContent = '';
  fileInput.value = '';

  sendBtn.disabled = true;
  const bodyEl = addMessage('assistant', '', 'AI');

  try {
    const res = await fetch('/api/chat', { method: 'POST', body: formData });
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let full = '';

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n\n');
      buf = lines.pop(); // keep incomplete chunk
      for (const line of lines) {
        if (!line.startsWith('data:')) continue;
        const jsonStr = line.slice(5).trim();
        if (!jsonStr) continue;
        const evt = JSON.parse(jsonStr);
        if (evt.token) {
          full += evt.token;
          bodyEl.textContent = full;
          chatLog.scrollTop = chatLog.scrollHeight;
        }
        if (evt.done) {
          // If ollama status might have changed (start/stop commands), refresh
          refreshStatus();
        }
      }
    }
  } catch (e) {
    bodyEl.textContent = `⚠️ Error: ${e}`;
  } finally {
    sendBtn.disabled = false;
  }
}

function autoGrow() {
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 160) + 'px';
}

// ---- Event wiring ----------------------------------------------------------
sendBtn.addEventListener('click', sendMessage);
input.addEventListener('input', autoGrow);
input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    sendMessage();
  }
});
clearBtn.addEventListener('click', clearChat);
refreshBtn.addEventListener('click', refreshStatus);
ollamaToggleBtn.addEventListener('click', toggleOllama);
engineSelect.addEventListener('change', saveSettings);
modelSelect.addEventListener('change', saveSettings);
fileInput.addEventListener('change', () => {
  pendingFile = fileInput.files[0] || null;
  fileNameEl.textContent = pendingFile ? pendingFile.name : '';
});

// Poll status every 10s so the dot stays fresh even if changed elsewhere
setInterval(refreshStatus, 10000);

init();
