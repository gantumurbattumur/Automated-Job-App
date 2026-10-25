// Orchestrates one Autofill click: fetch context from JobPrep, fill every field we know, report what's left.
(() => {
  if (window.__jobprepLoaded) return;  // injected twice (popup "show on this page" + manifest)
  window.__jobprepLoaded = true;
  if (window.top !== window && !document.querySelector('form, input')) return;  // iframes without a form

  const api = (path, method, body) => chrome.runtime.sendMessage({ type: 'api', path, method, body })
    .then(r => { if (!r?.ok) throw new Error(r?.error || 'JobPrep not reachable'); return r.data; });
  const pageCompany = () => document.querySelector('meta[property="og:site_name"]')?.content || document.title.split(/[|\-–]/).pop().trim();
  const pageTitle = () => document.querySelector('h1')?.innerText?.trim() || document.title.split(/[|\-–]/)[0].trim();
  let ctxPromise = null;
  const getCtx = () => ctxPromise || (ctxPromise = api(`/ext/context?url=${encodeURIComponent(location.href)}&company=${encodeURIComponent(pageCompany())}&title=${encodeURIComponent(pageTitle())}`)
    .catch(e => { ctxPromise = null; throw e; }));

  async function autofill() {
    const ui = JP.ui;
    ui.busy(true);
    let ctx;
    try { ctx = await getCtx(); }
    catch (e) {
      ui.busy(false);
      return ui.show(`<h3>JobPrep isn’t running</h3><p class="mut">Start it (<b>.venv/bin/uvicorn backend.api:app</b>) or check the address in the extension popup.</p><p class="bad">${ui.esc(e.message)}</p>`);
    }
    if (JP.workday.is() && JP.workday.signInScreen()) {
      ui.busy(false);
      return ui.show('<h3>Sign in first</h3><p class="mut">Workday needs you to sign in or create an account. Then click Autofill on each step.</p>');
    }
    const fields = JP.scan();
    if (!fields.length) {
      ui.busy(false);
      const lever = /jobs\.lever\.co$/.test(location.hostname) && !/\/apply\/?$/.test(location.pathname);
      return ui.show(`<h3>No application form here</h3><p class="mut">Open the application (the site’s <b>Apply</b> button), then click Autofill again.</p>
        ${lever ? '<div class="row"><button class="sm primary" data-act="open">Open application form</button></div>' : ''}`,
        { open: () => { location.href = location.href.replace(/\/?(\?.*)?$/, '/apply$1'); } });
    }
    let filled = 0;
    const missing = [], essays = [], done = new Set();
    for (const f of fields) {
      try {
        if (f.kind === 'file') {
          const isResume = JP.workday.is() ? JP.workday.isResumeInput(f)
            : /resume|cv\b/i.test(`${f.label} ${f.el.id} ${f.el.name}`) || (f.required && !/cover/i.test(f.label));
          if (isResume && !f.el.files?.length && await JP.fillFile(f, ctx)) filled++;
          continue;
        }
        if (f.kind === 'radio' || f.kind === 'checkbox') {
          const q = f.group || f.label;
          if (done.has(q)) continue;
          done.add(q);
          if (f.kind === 'radio' && (f.options || []).some(o => o.checked)) continue;
          const v = JP.answerFor(q, ctx);
          if (v && (f.kind === 'radio' ? JP.fillRadio(f, v, ctx) : JP.fillCheckbox(f, v, ctx))) filled++;
          else if (f.required && !(f.kind === 'checkbox' && f.el.checked)) missing.push({ label: q, el: f.el });
          continue;
        }
        if (f.kind === 'listbutton') {
          const cur = (f.el.innerText || '').trim();
          if (cur && !/^select( one)?$/i.test(cur)) continue;  // already chosen
        } else if (f.el.value) continue;  // already filled (by you or the site)
        const v = JP.answerFor(f.label, ctx);
        if (!v) {
          if (JP.isEssay(f.label, f.el)) essays.push(f);
          else if (f.required) missing.push({ label: f.label, el: f.el });
          continue;
        }
        const ok = f.kind === 'select' ? JP.fillSelect(f, v, ctx)
          : f.kind === 'combo' ? await JP.fillCombo(f, v, ctx)
          : f.kind === 'listbutton' ? await JP.fillListButton(f, v, ctx)
          : JP.fillText(f, v);
        if (ok) filled++; else if (f.required) missing.push({ label: f.label, el: f.el });
      } catch (e) {
        if (f.required) missing.push({ label: f.label, el: f.el });
      }
    }
    // essay boxes: a "Draft with Claude" helper instead of a made-up answer
    for (const f of [...essays, ...fields.filter(x => x.kind === 'textarea' && !x.el.value && JP.isEssay(x.label, x.el) && !essays.includes(x))]) {
      if (f.el.dataset.jobprepEssay) continue;
      f.el.dataset.jobprepEssay = '1';
      const b = document.createElement('button');
      b.type = 'button'; b.textContent = '✎ Draft with Claude (copies a prompt)';
      b.style.cssText = 'margin:4px 0;padding:4px 10px;border:1px solid #2563eb;color:#2563eb;background:#fff;border-radius:6px;cursor:pointer;font:12px system-ui';
      b.onclick = () => navigator.clipboard.writeText(`${ctx.essay_prompt}\n\nQUESTION: ${JP.cleanLabel(f.label)}`).then(() => { b.textContent = '✓ Prompt copied — paste into Claude'; });
      f.el.insertAdjacentElement('afterend', b);
    }
    missing.forEach(m => JP.flag(m.el));
    ui.busy(false);
    const step = JP.workday.is() ? JP.workday.step() : '';
    ui.show(`<h3>Filled ${filled} field${filled === 1 ? '' : 's'}${step ? ` · ${ui.esc(step)}` : ''}</h3>
      <p class="mut">${ctx.job ? `${ui.esc(ctx.job.title)} · ${ui.esc(ctx.job.company)}` : ui.esc(ctx.company || 'This job isn’t in JobPrep yet')}<br>
      Resume: ${ui.esc(ctx.resume.label)}${ctx.worked_here ? ' · <span class="ok">you worked here before</span>' : ''}</p>
      ${missing.length ? `<p class="bad"><b>${missing.length} need you</b> (outlined in red):</p><ul>${missing.map((m, i) => `<li data-act="go" data-i="${i}">${ui.esc(JP.cleanLabel(m.label) || 'Unlabeled field')}</li>`).join('')}</ul>` : '<p class="ok">Nothing left that JobPrep could see.</p>'}
      ${essays.length ? `<p class="mut">${essays.length} essay question${essays.length > 1 ? 's' : ''}: use the “Draft with Claude” button under each.</p>` : ''}
      <p class="mut">Review everything, then submit yourself — JobPrep never submits.${step ? ' On Workday, click Autofill again on each step.' : ''}</p>
      <div class="row"><button class="sm primary" data-act="applied">I submitted it</button><button class="sm" data-act="again">Fill again</button></div>`,
      {
        go: el => { const m = missing[+el.dataset.i]; m.el.scrollIntoView({ block: 'center' }); m.el.focus?.(); },
        again: () => autofill(),
        applied: el => api('/ext/result', 'POST', { url: location.href, status: 'applied', job_id: ctx.job?.id, company: ctx.company, title: ctx.job?.title || pageTitle() })
          .then(() => { el.textContent = '✓ Marked applied in JobPrep'; el.disabled = true; }),
      });
    api('/ext/result', 'POST', { url: location.href, status: 'opened', filled, missing: missing.map(m => JP.cleanLabel(m.label)),
      job_id: ctx.job?.id, company: ctx.company, title: ctx.job?.title || pageTitle() }).catch(() => {});
  }

  // after you submit: the site's "thank you" page marks the job applied automatically
  let reported = false;
  const watch = () => {
    if (reported || !ctxPromise) return;
    ctxPromise.then(ctx => {
      if (!reported && new RegExp(ctx.confirm, 'i').test((document.body?.innerText || '').slice(0, 6000))) {
        reported = true;
        api('/ext/result', 'POST', { url: location.href, status: 'applied', job_id: ctx.job?.id, company: ctx.company, title: ctx.job?.title || pageTitle() })
          .then(() => JP.ui.show('<h3 class="ok">Marked applied in JobPrep ✓</h3><p class="mut">Good luck!</p>')).catch(() => {});
      }
    }).catch(() => {});
  };
  let t;
  new MutationObserver(() => { clearTimeout(t); t = setTimeout(watch, 1200); }).observe(document.documentElement, { childList: true, subtree: true });

  if (window.top === window) {
    JP.ui.mount();
    JP.ui.onClick(autofill);
    getCtx().catch(() => {});  // warm up so the click feels instant
  }
  window.__jobprepAutofill = autofill;  // for tests
})();
