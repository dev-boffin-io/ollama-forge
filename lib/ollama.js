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
