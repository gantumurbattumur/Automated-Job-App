// Relays requests from content scripts to the local JobPrep server (pages can't call it directly because of their CSP).
const DEFAULT_API = 'http://127.0.0.1:8000';
const apiBase = async () => (await chrome.storage.local.get('apiBase')).apiBase || DEFAULT_API;

async function call(path, method = 'GET', body) {
  const r = await fetch(`${await apiBase()}${path}`, {
    method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 200)}`);
  return r.json();
}

async function fileAsBase64(path) {
  const r = await fetch(`${await apiBase()}${path}`);
  if (!r.ok) throw new Error(`resume ${r.status}`);
  const name = /filename="?([^";]+)"?/.exec(r.headers.get('content-disposition') || '')?.[1] || 'resume.pdf';
  const buf = new Uint8Array(await r.arrayBuffer());
  let bin = ''; for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
  return { name, type: r.headers.get('content-type') || 'application/pdf', b64: btoa(bin) };
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  (async () => {
    try {
      if (msg.type === 'api') return reply({ ok: true, data: await call(msg.path, msg.method, msg.body) });
      if (msg.type === 'file') return reply({ ok: true, data: await fileAsBase64(msg.path) });
      if (msg.type === 'apiBase') return reply({ ok: true, data: await apiBase() });
      if (msg.type === 'inject') {  // popup: "show Autofill on this page" for sites outside the built-in list
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true },
          files: ['content/match.js', 'content/fill.js', 'content/workday.js', 'content/ui.js', 'content/main.js'] });
        return reply({ ok: true });
      }
      reply({ ok: false, error: 'unknown message' });
    } catch (e) {
      reply({ ok: false, error: String(e.message || e) });
    }
  })();
  return true;  // async reply
});
