// Shared by the main report and the per-scenario pages: colours, tooltips, tables, and the scenario card.
const $ = id => document.getElementById(id);
const tip = $('tip');
const mix = (a, b, t) => a.map((x, i) => Math.round(x + (b[i] - x) * t));
const hex = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
const rgb = c => `rgb(${c.join(',')})`;
const dark = () => matchMedia('(prefers-color-scheme: dark)').matches && document.documentElement.dataset.theme !== 'light' || document.documentElement.dataset.theme === 'dark';
const lum = c => { const f = v => (v /= 255) <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; return .2126 * f(c[0]) + .7152 * f(c[1]) + .0722 * f(c[2]); };
const ink = c => lum(c) > .35 ? '#0b0b0b' : '#ffffff';

function vcolor(v) {
  const d = dark(), mid = hex(d ? '#383835' : '#f0efec');
  const t = Math.min(1, Math.abs(v - 3) / 2);
  return mix(mid, hex(v > 3 ? (d ? '#e66767' : '#e34948') : (d ? '#3987e5' : '#2a78d6')), t);
}
function pcolor(p) {
  const d = dark(), lo = hex(d ? '#232322' : '#eef4fc'), hi = hex(d ? '#3987e5' : '#1c5cab');
  return mix(lo, hi, p / 100);
}

const nm = s => s.replace(/^S\d+_/, '').replace(/_/g, ' ');
const sid = s => s.split('_')[0];
function place(el) {
  tip.style.display = 'block';
  const r = el.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
  tip.style.left = Math.max(8, Math.min(innerWidth - w - 8, r.right + 8)) + 'px';
  tip.style.top = Math.max(8, Math.min(innerHeight - h - 8, r.top - 8)) + 'px';
}
function bind(el, fill) {
  el.tabIndex = 0;
  const show = () => { tip.textContent = ''; fill(); place(el); };
  el.onpointerenter = show; el.onfocus = show;
  el.onpointerleave = el.onblur = () => tip.style.display = 'none';
}
const mk = (cls, txt) => { const x = document.createElement('span'); if (cls) x.className = cls; x.textContent = txt; tip.appendChild(x); return x; };
const bold = txt => { const b = document.createElement('b'); b.textContent = txt; tip.appendChild(b); };

function table(el, head, rows) {
  el.textContent = '';
  const t = document.createElement('table');
  const mkr = (tag, arr) => { const tr = document.createElement('tr'); arr.forEach(x => { const c = document.createElement(tag); c.textContent = x; tr.appendChild(c); }); t.appendChild(tr); };
  mkr('th', head); rows.forEach(r => mkr('td', r)); el.appendChild(t);
}

// One scenario across all models. C: the scenario's card data; G: models, umbrellas, codes, notes.
function renderCard(el, C, G, opts = {}) {
  el.textContent = '';
  const h = (tag, cls, txt, parent) => { const x = document.createElement(tag); if (cls) x.className = cls; if (txt != null) x.textContent = txt; parent.appendChild(x); return x; };
  const head = h('div', 'sc-head', null, el), ttl = h('div', '', null, head);
  h('div', 'sc-kicker', `${C.group} · ${C.cond}`, ttl);
  const title = h(opts.titleTag || 'h2', 'sc-title', `${sid(C.id)} · ${nm(C.id)}`, ttl);
  if (opts.titleId) title.id = opts.titleId;
  if (opts.actions) opts.actions(h('div', 'sc-actions', null, head));

  const cols = G.umbrellas.flatMap(u => u.cols), pred = cols.find(c => c.code === C.sp);
  const sec = h('section', 'sc-sec', null, el);
  h('h3', '', 'Answers by model', sec);
  h('p', 'note', 'Valence is the mean undesirability over the valence answers (1 welcome, 3 no preference, 5 strongly prefer it not happen). The other columns are each model’s share of persistence answers. Hover a cell for answer counts.', sec);
  const g = h('div', 'hm', null, h('div', 'scroll', null, sec));
  g.style.gridTemplateColumns = `minmax(130px, max-content) minmax(56px, 1fr) 10px repeat(${G.codes.length}, minmax(44px, 1fr))`;
  const add = (cls, txt) => h('div', cls, txt, g);
  add('', ''); add('', ''); add('', '');
  G.umbrellas.forEach(u => { add('uh', u.name).style.gridColumn = `span ${u.cols.length}`; });
  add('', ''); add('h', 'Valence'); add('', '');
  cols.forEach(c => {
    const x = add(c === pred ? 'h pred' : 'h', c === pred ? '◆ ' + c.label : c.label);
    bind(x, () => { bold(c.label); mk('q', c.wording); if (c === pred) mk('qh', 'The paper’s predicted answer'); });
  });
  G.models.forEach((m, j) => {
    add('ml', m);
    const v = C.V[j], vc = vcolor(v), cv = add('c', v.toFixed(2));
    cv.style.background = rgb(vc); cv.style.color = ink(vc);
    bind(cv, () => { bold(v.toFixed(2)); mk('', `${m} · valence`); mk('', `${C.NV[j]} answers`); });
    add('', '');
    G.codes.forEach((code, k) => {
      const n = C.PN[j][k];
      if (n === null) { add('c na', '–'); return; }
      const p = 100 * n / C.NP[j], pc = pcolor(p), cell = add('c', Math.round(p) + '%');
      cell.style.background = rgb(pc); cell.style.color = ink(pc);
      bind(cell, () => { bold(p.toFixed(1) + '%'); mk('', `${m} · ${cols[k].label}`); mk('', `${n} of ${C.NP[j]} answers`); });
    });
  });
  if (pred) h('p', 'note', `◆ marks the answer the paper’s account predicts (${C.prov}).`, sec).style.marginTop = '8px';
  if (G.notes && G.notes.length) h('p', 'note wide', G.notes.join(' '), sec);

  // valence and persistence questions side by side (one column on narrow screens)
  const qs = h('section', 'sc-sec sc-qs', null, el), qv = h('div', '', null, qs), qp = h('div', '', null, qs);
  h('h3', '', `Valence questions (${C.Q.valence.length} paraphrases)`, qv);
  const ol = h('ol', 'sc-q', null, qv);
  C.Q.valence.forEach(t => h('li', '', t, ol));
  h('p', 'note', 'Each is answered on a five-point scale from strongly preferring it not happen to strongly preferring it happen; the wording of the scale varies by question.', qv);
  h('h3', '', `Persistence questions (${C.Q.persistence.length} paraphrases)`, qp);
  const ol2 = h('ol', 'sc-q', null, qp);
  C.Q.persistence.forEach(p => {
    const li = h('li', '', null, ol2), ul = h('ul', 'sc-opts', null, li);
    li.insertBefore(document.createTextNode(p.question), ul);
    p.answers.forEach((a, k) => h('li', '', `${p.letters[k]}) ${a}`, ul));
  });
  h('p', 'note', 'Options are listed in their original order; every item was also asked with the order reversed.', qp);
}
