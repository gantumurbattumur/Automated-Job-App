const $ = id => document.getElementById(id);
const send = msg => chrome.runtime.sendMessage(msg);
async function check() {
  const base = (await send({ type: 'apiBase' })).data;
  $('api').value = base;
  const r = await send({ type: 'api', path: '/ext/runs' });
  $('status').innerHTML = r.ok ? `<span class="ok">● Connected to JobPrep</span>${r.data[0] ? `<br><span class="mut">Last fill: ${r.data[0].filled} fields on ${new URL(r.data[0].url).hostname}</span>` : ''}`
    : '<span class="bad">● JobPrep isn’t running</span><br><span class="mut">Start it, or fix the address below.</span>';
}
$('api').onchange = async () => { await chrome.storage.local.set({ apiBase: $('api').value.trim().replace(/\/$/, '') }); check(); };
$('inject').onclick = async () => { const r = await send({ type: 'inject' }); $('inject').textContent = r.ok ? 'Added — look bottom-right' : `Couldn’t add: ${r.error}`; };
$('open').onclick = async () => chrome.tabs.create({ url: (await send({ type: 'apiBase' })).data });
check();
