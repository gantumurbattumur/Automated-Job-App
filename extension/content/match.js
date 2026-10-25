// Which value goes into a field with a given label. Port of backend/autofill.py + profile.match_answer
// (the regexes themselves come from the server in ctx.fields so there is one source of truth).
var JP = window.JP || (window.JP = {});

JP.flat = s => String(s || '').toLowerCase().replace(/[^\w\s{}]/g, ' ').replace(/\s+/g, ' ').trim();
JP.escapeRe = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
JP.cleanLabel = l => String(l || '').replace(/\s+/g, ' ').replace(/\s*[*✱]+\s*$/, '').trim();

// canned answer whose pattern appears in the question; longest pattern wins ("now or in the future" beats "sponsorship")
JP.matchAnswer = (label, ctx) => {
  const l = JP.flat(label);
  let best = null, bestLen = 0;
  for (const a of ctx.answers) {
    for (const raw of a.patterns) {
      const pat = JP.flat(raw);
      if (pat && pat.length > bestLen && new RegExp(`(?<!\\w)${JP.escapeRe(pat)}(?!\\w)`).test(l)) { best = a; bestLen = pat.length; }
    }
  }
  return best;
};

JP.answerFor = (label, ctx) => {
  const clean = JP.cleanLabel(label);
  if (!clean) return null;
  const a = JP.matchAnswer(clean, ctx);
  if (a && a.answer) return a.answer;
  for (const [pat, key] of ctx.fields) {
    if (new RegExp(pat, 'i').test(clean) && ctx.values[key]) return ctx.values[key];
  }
  return null;
};

const STOP = new Set(['of', 'the', 'and', 'in', 'at']);
JP.tokens = s => new Set(String(s).toLowerCase().replace(/'s\b/g, '').split(/[^a-z0-9]+/).filter(t => t && !STOP.has(t)));
JP.expand = (answer, ctx) => {
  let a = answer.replace(/,\s*([A-Z]{2})\b/g, (m, c) => `, ${ctx.states[c] || c}`);  // "Carson, CA" -> "Carson, California"
  if (/\bmaster|\bm\.?s\.?\b/i.test(a) && !/degree/i.test(a)) a += " master's degree";
  if (/\bbachelor|\bb\.?s\.?\b/i.test(a) && !/degree/i.test(a)) a += " bachelor's degree";
  return a;
};
const norm = s => String(s).toLowerCase().replace(/[^a-z0-9]/g, '');

// best option for an answer: exact (ignoring punctuation) > prefix > "won't say" synonyms > token overlap
JP.pick = (options, answer, ctx) => {
  const a = norm(answer);
  if (!a) return null;
  const decline = new RegExp(ctx.decline, 'i');
  if (decline.test(answer)) {
    const anyDecline = /decline|prefer not|(don.?t|do not) (wish|want)|not to (answer|disclose|say|self)|choose not|rather not/i;
    const hit = options.find(o => anyDecline.test(o));
    if (hit) return hit;
  }
  let hit = options.find(o => norm(o) === a) || options.find(o => norm(o).startsWith(a));
  if (hit) return hit;
  const want = JP.tokens(JP.expand(answer, ctx));
  let best = null, bestScore = 0;
  for (const o of options) {
    const have = JP.tokens(o);
    if (!have.size) continue;
    const n = [...want].filter(t => have.has(t)).length;
    const score = n / have.size;
    if (score > bestScore) { best = o; bestScore = score; }
  }
  return bestScore >= 0.5 ? best : null;
};

// what to type into a searchable dropdown before picking
JP.query = (label, answer) => {
  const first = answer.split(/[,(\-–]/)[0].trim();
  if (/degree/i.test(label)) return first.split(/\s+/)[0];
  return (first || answer).slice(0, 40);
};

JP.isEssay = (label, el) => el.tagName === 'TEXTAREA' &&
  /why|describe|tell us|explain|what (interests|excites|motivates)|cover letter|additional information|anything else/i.test(label);
