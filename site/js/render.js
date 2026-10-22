// Cards and comparison table. Every number links to the document page it was read from.
import { firstYearPct, sixLine, tradeCosts } from './costs.js';
import { el } from './interview.js';
import { isHttps } from './search.js';

export const pct = (x, d = 2) => (x == null || Number.isNaN(x) ? '–' : `${x.toFixed(d)}%`);
const signed = (x) => (x == null ? '–' : `${x > 0 ? '+' : ''}${x.toFixed(2)} pp`);
const size = (m, ccy) => (m == null ? '–' : m >= 1000 ? `${(m / 1000).toFixed(1)} bn ${ccy || ''}` : `${Math.round(m)} m ${ccy || ''}`);
const REPL = { 'physical-full': 'Physical, full', 'physical-sampling': 'Physical, sampling', synthetic: 'Synthetic (swap)', active: 'Active (no index tracking)' };
const DOC_LABEL = { factsheet: 'Factsheet', kid: 'KID', holdings: 'Holdings file' };

export function srcLink(index, fund, fact) {
  if (!fact || !fact.s || fact.s.startsWith('api:')) {
    if (fact?.s?.startsWith('api:') && isHttps(fund.product_url)) {
      const a = el('a', 'src', '↗');
      Object.assign(a, { href: fund.product_url, target: '_blank', rel: 'noopener noreferrer', title: `Source: ${fund.provider} website` });
      return a;
    }
    return null;
  }
  const doc = fund.docs.find((d) => d.doc_id === fact.s);
  const href = index.docUrl(fund.isin, fact.s, fact.p);
  if (!href) return null;
  const a = el('a', 'src', '↗');
  Object.assign(a, { href, target: '_blank', rel: 'noopener noreferrer', title: `Source: ${DOC_LABEL[doc?.doc_type] || 'document'}${fact.p && doc?.doc_type !== 'holdings' ? `, page ${fact.p}` : ''}${doc?.as_of ? ` (as of ${doc.as_of})` : ''}` });
  return a;
}

function cell(index, fund, text, fact) {
  const td = el('td');
  td.append(document.createTextNode(text));
  const a = text === '–' ? null : srcLink(index, fund, fact);
  if (a) td.append(' ', a);
  return td;
}

const tdFact = (f) => f.calendar_returns;  // TD is derived from the calendar-year table

export function rows(broker, rules, amount) {
  const chfAmt = (p) => (amount && p != null ? ` · CHF ${Math.round(amount * p / 100).toLocaleString('de-CH')}` : '');
  return [
    ['Costs', null],
    ['TER (p.a.)', (f) => [pct(f.ter?.v), f.ter]],
    ['Tracking difference, 3y avg', (f) => [signed(f.td_avg_3y), tdFact(f)], 'Fund return minus benchmark return per year. Closer to zero (or positive) is better.'],
    ['Tracking difference, 5y avg', (f) => [signed(f.td_avg_5y), tdFact(f)]],
    ['KID ongoing costs', (f) => [pct(f.kid_ongoing_pct?.v), f.kid_ongoing_pct]],
    ['KID transaction costs', (f) => [pct(f.kid_transaction_pct?.v), f.kid_transaction_pct]],
    ['Stamp duty per trade', (f) => [pct(tradeCosts(f, broker, rules).stampPct, 3), null], `${rules.stamp_duty.note}`],
    ['FX conversion per trade', (f) => { const t = tradeCosts(f, broker, rules); return [t.fxPct == null ? 'depends on broker' : t.fxCcy ? `${pct(t.fxPct, 3)} (CHF→${t.fxCcy})` : '0% (CHF line)', null]; }, rules.fx_note],
    ['First year, buy once + hold', (f) => { const p = firstYearPct(f, tradeCosts(f, broker, rules)); return [p == null ? '–' : pct(p, 3) + chfAmt(p), null]; }, 'TER plus one-off purchase costs (stamp duty, FX, commission). Excludes broker custody fees.'],
    ['Structure', null],
    ['Replication', (f) => [REPL[f.replication?.v] || '–', f.replication]],
    ['Dividends', (f) => [f.use_of_income?.v || '–', f.use_of_income]],
    ['Currency hedged', (f) => [f.hedged?.v ? `Yes (${f.hedge_currency?.v || '?'})` : f.hedged ? 'No' : '–', f.hedged]],
    ['Domicile', (f) => [f.domicile?.v || '–', f.domicile]],
    ['Fund size', (f) => [size(f.fund_size_m?.v, f.fund_size_ccy), f.fund_size_m]],
    ['Launched', (f) => [f.inception?.v || '–', f.inception]],
    ['Holdings', (f) => [f.holdings_count?.v?.toLocaleString('de-CH') || '–', f.holdings_count]],
    ['Risk class (SRI 1-7)', (f) => [f.sri?.v ?? '–', f.sri]],
    ['Benchmark', (f) => [f.benchmark?.v || '–', f.benchmark]],
    ['Trading', null],
    ['SIX listing', (f) => { const l = sixLine(f); return [l ? `${l.ticker || '?'} in ${l.ccy || '?'}` : 'not on SIX', f.listings]; }],
    ['Exposure', null],
    ['Top countries', (f) => [(f.countries?.v || []).slice(0, 3).map((w) => `${w.name} ${w.w.toFixed(0)}%`).join(', ') || '–', f.countries]],
    ['Top sectors', (f) => [(f.sectors?.v || []).slice(0, 3).map((w) => `${w.name} ${w.w.toFixed(0)}%`).join(', ') || '–', f.sectors]],
  ];
}

export function renderTable(root, index, funds, { broker, rules, amount, roleOf }) {
  root.innerHTML = '';
  if (!funds.length) { root.append(el('p', 'muted', 'Select funds above to compare them.')); return; }
  const wrap = el('div', 'table-wrap');
  const t = el('table', 'cmp');
  const head = el('tr');
  head.append(el('th', 'rowhead', ''));
  for (const f of funds) {
    const th = el('th');
    th.append(el('span', 'th-name', f.name.v), el('span', 'th-meta', `${f.ticker?.v || ''} · ${f.isin}`));
    if (roleOf?.(f.isin)) th.append(el('span', 'tag', roleOf(f.isin)));
    head.append(th);
  }
  const thead = el('thead'); thead.append(head); t.append(thead);
  const tb = el('tbody');
  for (const [label, get, help] of rows(broker, rules, amount)) {
    const tr = el('tr');
    if (!get) { const th = el('th', 'group', label); th.colSpan = funds.length + 1; tr.append(th); tb.append(tr); continue; }
    const th = el('th', 'rowhead', label);
    if (help) th.title = help;
    tr.append(th);
    for (const f of funds) { const [txt, fact] = get(f); tr.append(cell(index, f, String(txt), fact)); }
    tb.append(tr);
  }
  t.append(tb);
  wrap.append(t);
  root.append(wrap);
}

export function renderCard(index, c, { selected, onToggle, aiPick }) {
  const f = c.fund;
  const card = el('article', 'card' + (aiPick ? ' card-ai' : ''));
  const top = el('div', 'card-top');
  const title = el('div', 'card-title');
  title.append(el('h4', null, f.name.v), el('p', 'card-meta', `${f.ticker?.v ? f.ticker.v + ' · ' : ''}${f.isin} · ${f.provider}`));
  const cb = el('label', 'cmp-toggle');
  const input = el('input'); input.type = 'checkbox'; input.checked = selected;
  input.addEventListener('change', () => onToggle(f.isin, input.checked));
  cb.append(input, document.createTextNode(' Compare'));
  top.append(title, cb);
  card.append(top);
  if (c.differs) card.append(el('p', 'card-differs', `Differs from your profile: ${c.differs}`));
  const stats = el('dl', 'stats');
  const stat = (k, v, fact) => { const d = el('div'); const dd = el('dd', null, v); const a = v === '–' ? null : srcLink(index, f, fact); if (a) dd.append(' ', a); d.append(el('dt', null, k), dd); stats.append(d); };
  stat('TER', pct(f.ter?.v), f.ter);
  stat(f.td_avg_5y != null ? 'TD 5y' : 'TD 3y', signed(f.td_avg_5y ?? f.td_avg_3y), f.calendar_returns);
  stat('Size', size(f.fund_size_m?.v, f.fund_size_ccy), f.fund_size_m);
  stat('Replication', { 'physical-full': 'Full', 'physical-sampling': 'Sampled', synthetic: 'Swap', active: 'Active' }[f.replication?.v] || '–', f.replication);
  card.append(stats);
  const notes = el('p', 'card-notes', [f.use_of_income?.v, f.domicile?.v, f.hedged?.v ? `hedged ${f.hedge_currency?.v || ''}` : null,
    sixLine(f) ? `SIX ${sixLine(f).ccy}` : 'not on SIX'].filter(Boolean).join(' · '));
  card.append(notes);
  if (c.reasons?.length) card.append(el('p', 'card-warn', c.reasons.join(' · ')));
  if (aiPick?.why) { const p = el('p', 'card-why'); p.append(el('strong', null, 'Assistant: '), document.createTextNode(aiPick.why)); card.append(p); }
  const docs = el('p', 'card-docs');
  for (const d of f.docs) {
    if (d.doc_type === 'holdings' || !isHttps(d.url)) continue;
    const a = el('a', null, DOC_LABEL[d.doc_type]);
    Object.assign(a, { href: d.url, target: '_blank', rel: 'noopener noreferrer' });
    docs.append(a);
  }
  card.append(docs);
  return card;
}
