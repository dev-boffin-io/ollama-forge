// lib/ollama.js
// Thin client for the Ollama HTTP API using the built-in global fetch (Node 18+).

function withTimeout(ms) {
  return AbortSignal.timeout(ms);
}

export async function checkServer(host, timeout = 4000) {
  try {
    const r = await fetch(`${host}/api/tags`, { signal: withTimeout(timeout) });
    return r.ok;
  } catch {
    return false;
  }
}

export async function listModels(host) {
  const r = await fetch(`${host}/api/tags`, { signal: withTimeout(5000) });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const data = await r.json();
  return data.models || [];
}

export async function showModel(host, name) {
  const r = await fetch(`${host}/api/show`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ name }),
    signal: withTimeout(8000),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

/**
 * Stream a chat completion from the Ollama `/api/chat` endpoint.
 *
 * @param {string} host
 * @param {object} opts
 * @param {string} opts.model
 * @param {{role:string, content:string}[]} opts.messages
 * @param {AbortSignal} [opts.signal]
 * @param {(text:string)=>void} [opts.onThinking]  incremental reasoning deltas
 * @param {(text:string)=>void} [opts.onContent]   incremental answer deltas
 * @param {(info:object)=>void} [opts.onDone]      final stats ({ eval_count, ... })
 * @returns {Promise<void>}
 */
export async function chatStream(host, opts = {}) {
  const { model, messages, signal, onThinking, onContent, onDone } = opts;
  const r = await fetch(`${host}/api/chat`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ model, messages, stream: true }),
    signal,
  });
  if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);

  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";

  const handleLine = (line) => {
    if (!line.trim()) return;
    let obj;
    try {
      obj = JSON.parse(line);
    } catch {
      return;
    }
    if (obj.error) throw new Error(obj.error);
    const msg = obj.message || {};
    if (msg.thinking) onThinking?.(msg.thinking);
    if (msg.content) onContent?.(msg.content);
    if (obj.done) {
      onDone?.({
        evalCount: obj.eval_count || 0,
        evalDuration: obj.eval_duration || 0,
        totalDuration: obj.total_duration || 0,
        doneReason: obj.done_reason || "",
      });
    }
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n")) !== -1) {
      handleLine(buf.slice(0, idx));
      buf = buf.slice(idx + 1);
    }
  }
  buf += dec.decode();
  if (buf.trim()) handleLine(buf);
}
