// The floating JobPrep button and results panel (Shadow DOM, so the site's CSS can't break it).
var JP = window.JP || (window.JP = {});

JP.ui = (() => {
  const host = document.createElement('div');
  host.id = 'jobprep-root';
  host.style.cssText = 'position:fixed;z-index:2147483647;right:18px;bottom:18px;';
  const root = host.attachShadow({ mode: 'open' });
  root.innerHTML = `<style>
    :host { all: initial; } * { box-sizing: border-box; font: 13px/1.4 system-ui, -apple-system, sans-serif; }
    .btn { display:flex; align-items:center; gap:8px; background:#2563eb; color:#fff; border:0; border-radius:999px; padding:10px 16px;
      font-weight:600; cursor:pointer; box-shadow:0 4px 14px rgba(0,0,0,.25); }
    .btn:hover { filter:brightness(1.08); } .btn[disabled] { opacity:.7; cursor:default; }
    .logo { width:20px; height:20px; border-radius:6px; background:#fff; color:#2563eb; display:grid; place-items:center; font-weight:800; }
    .panel { position:absolute; right:0; bottom:52px; width:340px; max-height:70vh; overflow:auto; background:#fff; color:#16181d;
      border:1px solid #e4e7ec; border-radius:12px; box-shadow:0 8px 30px rgba(0,0,0,.2); padding:14px; display:none; }
    .panel.open { display:block; } h3 { margin:0 0 6px; font-size:14px; } .mut { color:#667085; } .ok { color:#15803d; } .bad { color:#b42318; }
    ul { margin:6px 0; padding-left:18px; } li { margin:3px 0; cursor:pointer; } li:hover { color:#2563eb; }
    .row { display:flex; gap:6px; flex-wrap:wrap; margin-top:10px; }
    .sm { border:1px solid #d0d5dd; background:#fff; border-radius:6px; padding:5px 10px; cursor:pointer; }
    .sm.primary { background:#2563eb; color:#fff; border-color:#2563eb; } .x { float:right; cursor:pointer; color:#98a2b3; }
    @media (prefers-color-scheme: dark) { .panel { background:#171a21; color:#e7e9ee; border-color:#262b36; } .sm { background:#1c2029; color:#e7e9ee; border-color:#343a48; } }
  </style>
  <div class="panel" part="panel"></div>
  <button class="btn" title="Fill this application from your JobPrep profile (never submits)"><span class="logo">J</span><span class="lbl">Autofill</span></button>`;
  const btn = root.querySelector('.btn'), lbl = root.querySelector('.lbl'), panel = root.querySelector('.panel');
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  return {
    mount() {
      const put = () => { if (!host.isConnected) (document.body || document.documentElement).appendChild(host); };
      put();
      setInterval(put, 1000);  // pages that re-render the whole document (Greenhouse's new boards) remove it
    },
    onClick(fn) { btn.addEventListener('click', fn); },
    busy(on, text = 'Filling…') { btn.disabled = on; lbl.textContent = on ? text : 'Autofill'; },
    show(html, handlers = {}) {
      panel.innerHTML = `<span class="x" data-act="close">✕</span>${html}`;
      panel.classList.add('open');
      panel.querySelectorAll('[data-act]').forEach(el => el.addEventListener('click', e => {
        const act = el.dataset.act;
        if (act === 'close') panel.classList.remove('open'); else handlers[act]?.(el, e);
      }));
    },
    hide() { panel.classList.remove('open'); },
    esc,
  };
})();
