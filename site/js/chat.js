// Worker client and context building. Numbers shown to the user come from etfs.json, never from the model.
import { WORKER_URL } from './config.js';

const FUND_FIELDS = ['name', 'ticker', 'provider', 'domicile', 'share_class_currency', 'use_of_income', 'hedged', 'hedge_currency',
  'replication', 'benchmark', 'inception', 'fund_size_m', 'fund_size_ccy', 'ter', 'kid_ongoing_pct', 'kid_transaction_pct',
  'kid_one_off_secondary_exempt', 'sri', 'td_avg_3y', 'td_avg_5y', 'holdings_count', 'theme_tags', 'listings', 'sfdr'];

export function compactFund(f, role, costs) {
  const out = { isin: f.isin, role };
  for (const k of FUND_FIELDS) if (f[k] != null) out[k] = f[k];
  if (f.countries) out.top_countries = { ...f.countries, v: f.countries.v.slice(0, 5) };
  if (f.sectors) out.top_sectors = { ...f.sectors, v: f.sectors.v.slice(0, 5) };
  if (costs) out.trade_costs_for_user = costs;
  return out;
}

export async function embedQuery(code, text) {
  const r = await fetch(`${WORKER_URL}/embed`, { method: 'POST', headers: { Authorization: `Bearer ${code}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) });
  if (!r.ok) throw new Error(`embed ${r.status}`);
  return Float32Array.from((await r.json()).vector);
}

export async function usage(code) {
  const r = await fetch(`${WORKER_URL}/usage`, { headers: { Authorization: `Bearer ${code}` } });
  if (!r.ok) throw Object.assign(new Error(`usage ${r.status}`), { status: r.status });
  return r.json();
}

export async function ask(code, { profile, messages, funds, passages, session }) {
  const r = await fetch(`${WORKER_URL}/chat`, {
    method: 'POST', headers: { Authorization: `Bearer ${code}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ profile, messages, session, context: { funds, passages } }),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(d.message || `chat ${r.status}`), { status: r.status, data: d });
  return d;
}

// Thumbs up (1) or down (0) on one answer; the Worker stores it as LangSmith feedback on that answer's trace.
export async function sendFeedback(code, runId, score) {
  const r = await fetch(`${WORKER_URL}/feedback`, {
    method: 'POST', headers: { Authorization: `Bearer ${code}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_id: runId, score }),
  });
  if (!r.ok) throw new Error(`feedback ${r.status}`);
}

// Keep only citations the model was actually given; keep only picks among candidate ISINs.
export function validateAnswer(answer, allowedIds, allowedIsins) {
  const okId = (id) => allowedIds.has(id);
  const reply = (answer.reply || '').replace(/\[([A-Z]{2}[A-Z0-9]{9}\d-[a-z]+(?:#d?\d+)?)\]/g, (m, id) => (okId(id) ? m : ''));
  const picks = (answer.picks || []).filter((p) => allowedIsins.has(p.isin))
    .map((p) => ({ ...p, citations: (p.citations || []).filter(okId) }));
  const allocation = answer.allocation ? answer.allocation.filter((a) => allowedIsins.has(a.isin)) : null;
  const dropped = (answer.picks || []).length - picks.length;
  return { ...answer, reply, picks, allocation, droppedPicks: dropped };
}
