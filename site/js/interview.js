// Guided interview: scripted questions, no LLM calls. Produces the profile object used by rank.js and the prompt.
import { THEMES } from './rank.js';

export const QUESTIONS = [
  { key: 'horizon', q: 'How long do you plan to stay invested?', options: [
    ['long', '10 years or more'], ['medium', '5 to 10 years'], ['short', 'Less than 5 years']] },
  { key: 'approach', q: 'How should the portfolio be built?', help: 'You can change this later.', options: [
    ['max-div', 'Maximally diversified', 'One global fund that holds everything'],
    ['home-bias', 'Global core + Swiss home bias', 'Overweight Swiss companies on purpose'],
    ['themes', 'Global core + themes', 'Add satellites like semiconductors or healthcare'],
    ['specific', 'Only a specific area', 'I already know what I want to cover']] },
  { key: 'homeBias', q: 'How much should be invested in Swiss stocks?', when: (p) => p.approach === 'home-bias',
    help: 'Switzerland is only about 2 to 3% of a world index.', options: [[10, 'About 10%'], [20, 'About 20%'], [30, '30% or more']] },
  { key: 'themes', q: 'Which areas do you want to cover?', multi: true, when: (p) => p.approach === 'themes' || p.approach === 'specific',
    options: Object.entries(THEMES).map(([k, t]) => [k, t.label]) },
  { key: 'em', q: 'Include emerging markets (China, India, Taiwan, ...)?', when: (p) => p.approach !== 'specific', options: [
    ['yes', 'Yes, all-world'], ['no', 'No, developed markets only'], ['any', 'No preference']] },
  { key: 'bonds', q: 'Do you want a bond part?', options: [
    ['none', 'No bonds'], ['global-hedged', 'Global bonds, hedged to CHF'], ['chf', 'Swiss franc bonds']] },
  { key: 'esg', q: 'Sustainability (ESG) preference?', options: [
    ['any', 'No preference'], ['prefer', 'Prefer ESG if costs are similar'], ['only', 'ESG funds only']] },
  { key: 'income', q: 'What should happen with dividends?', options: [
    ['acc', 'Reinvest (accumulating)'], ['dist', 'Pay out (distributing)'], ['any', 'No preference']] },
  { key: 'hedge', q: 'Currency hedging to CHF for stocks?', help: 'Hedged share classes remove currency swings but cost more.', options: [
    ['none', 'No, unhedged'], ['all', 'Yes, hedge stocks too']] },
  { key: 'broker', q: 'Which broker do you use?', help: 'Decides Swiss stamp duty and currency conversion costs.', options: [
    ['ibkr', 'Interactive Brokers'], ['saxo', 'Saxo'], ['swissquote', 'Swissquote'], ['yuh', 'Yuh'], ['postfinance', 'PostFinance'],
    ['neon', 'Neon'], ['other_ch', 'Other Swiss broker'], ['other_foreign', 'Other foreign broker']] },
  { key: 'amount', q: 'Roughly how much will you invest? (optional)', input: 'number', help: 'Only used to show costs in CHF.', options: [] },
];

export const DEFAULT_PROFILE = { horizon: 'long', approach: 'max-div', em: 'yes', bonds: 'none', esg: 'any', income: 'any', hedge: 'none', broker: 'ibkr', themes: [] };

export function visibleQuestions(profile) {
  return QUESTIONS.filter((q) => !q.when || q.when(profile));
}

export function describe(profile) {
  const lab = (key) => {
    const q = QUESTIONS.find((x) => x.key === key);
    const v = profile[key];
    if (v == null || v === '' || (Array.isArray(v) && !v.length)) return null;
    if (Array.isArray(v)) return v.map((x) => q.options.find((o) => o[0] === x)?.[1] || x).join(', ');
    if (q.input) return `CHF ${Number(v).toLocaleString('de-CH')}`;
    return q.options.find((o) => o[0] === v)?.[1] || String(v);
  };
  return visibleQuestions(profile).map((q) => ({ key: q.key, q: q.q, a: lab(q.key) })).filter((x) => x.a);
}

// Renders one question at a time into `root`; calls onDone(profile) at the end.
export function runInterview(root, start, onDone) {
  const profile = { ...DEFAULT_PROFILE, ...start };
  let i = 0;
  const draw = () => {
    const qs = visibleQuestions(profile);
    if (i >= qs.length) return onDone(profile);
    const q = qs[i];
    root.innerHTML = '';
    const card = el('section', 'iv-card');
    card.append(el('p', 'iv-step', `Question ${i + 1} of ${qs.length}`), el('h2', 'iv-q', q.q));
    if (q.help) card.append(el('p', 'iv-help', q.help));
    const opts = el('div', 'iv-options');
    if (q.input === 'number') {
      const inp = el('input', 'iv-input');
      Object.assign(inp, { type: 'number', min: 0, step: 1000, placeholder: 'e.g. 20000', value: profile[q.key] || '' });
      inp.setAttribute('aria-label', q.q);
      opts.append(inp);
      inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') next(); });
      const next = () => { profile[q.key] = inp.value ? Number(inp.value) : null; i++; draw(); };
      card.append(opts, nav(() => { i--; draw(); }, next, 'Show results'));
    } else if (q.multi) {
      const chosen = new Set(profile[q.key] || []);
      for (const [v, label] of q.options) {
        const b = chip(label, chosen.has(v));
        b.addEventListener('click', () => { chosen.has(v) ? chosen.delete(v) : chosen.add(v); b.setAttribute('aria-pressed', chosen.has(v)); });
        opts.append(b);
      }
      card.append(opts, nav(i > 0 && (() => { i--; draw(); }), () => { profile[q.key] = [...chosen]; i++; draw(); }, 'Next'));
    } else {
      for (const [v, label, sub] of q.options) {
        const b = chip(label, profile[q.key] === v, sub);
        b.addEventListener('click', () => { profile[q.key] = v; i++; draw(); });
        opts.append(b);
      }
      card.append(opts, nav(i > 0 && (() => { i--; draw(); }), null));
    }
    root.append(card);
    card.querySelector('button.chip, input')?.focus();
  };
  draw();
}

function chip(label, pressed, sub) {
  const b = el('button', 'chip');
  b.type = 'button';
  b.setAttribute('aria-pressed', String(Boolean(pressed)));
  b.append(el('span', 'chip-label', label));
  if (sub) b.append(el('span', 'chip-sub', sub));
  return b;
}

function nav(back, next, nextLabel) {
  const n = el('div', 'iv-nav');
  if (back) { const b = el('button', 'btn-ghost', 'Back'); b.type = 'button'; b.addEventListener('click', back); n.append(b); }
  if (next) { const b = el('button', 'btn', nextLabel || 'Next'); b.type = 'button'; b.addEventListener('click', next); n.append(b); }
  return n;
}

export function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
