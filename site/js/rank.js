// Deterministic shortlist: hard filters from the interview profile, then a transparent cost-first score.
import { effectiveAnnualCost, sixLine, tradeCosts } from './costs.js';

const tags = (f) => new Set(f.theme_tags || []);
const has = (f, ...ts) => ts.some((t) => tags(f).has(t));
const NARROW = ['semiconductors', 'technology', 'artificial-intelligence', 'healthcare', 'clean-energy', 'energy', 'financials',
  'real-estate', 'infrastructure', 'water', 'defense', 'gold', 'commodities', 'small-cap', 'value', 'momentum', 'quality',
  'min-volatility', 'dividend', 'multifactor', 'china', 'india'];

export const THEMES = {
  semiconductors: { label: 'Semiconductors', tags: ['semiconductors'] },
  technology: { label: 'Technology', tags: ['technology'] },
  ai: { label: 'AI & robotics', tags: ['artificial-intelligence'] },
  healthcare: { label: 'Healthcare', tags: ['healthcare'] },
  clean_energy: { label: 'Clean energy', tags: ['clean-energy'] },
  small_caps: { label: 'Small caps', tags: ['small-cap'] },
  value: { label: 'Value', tags: ['value'] },
  dividend: { label: 'Dividends', tags: ['dividend'] },
  real_estate: { label: 'Real estate', tags: ['real-estate'] },
  gold: { label: 'Gold', tags: ['gold'] },
  emerging: { label: 'Emerging markets', tags: ['emerging-markets'] },
};

const isEquity = (f) => (f.asset_class?.v || '').toLowerCase() === 'equity';
const isBond = (f) => (f.asset_class?.v || '').toLowerCase().includes('fixed');

function passesCommon(f, p) {
  if (p.esg === 'only' && !has(f, 'esg')) return false;
  if (p.income === 'acc' && f.use_of_income?.v && f.use_of_income.v !== 'Accumulating') return false;
  if (p.income === 'dist' && f.use_of_income?.v && f.use_of_income.v !== 'Distributing') return false;
  if (p.maxTer && f.ter?.v != null && f.ter.v > p.maxTer) return false;
  return true;
}

const ROLE_FILTERS = {
  core: (f, p) => isEquity(f) && !has(f, 'active') && has(f, 'world', 'all-world') && !has(f, ...NARROW) && !has(f, 'emerging-markets') &&
    (p.em === 'no' ? !has(f, 'all-world') : p.em === 'yes' ? has(f, 'all-world') : true) && (p.hedge === 'all' ? has(f, 'chf-hedged') : !has(f, 'currency-hedged')),
  'home-bias': (f) => isEquity(f) && has(f, 'switzerland') && !has(f, 'small-cap', 'dividend', 'value', 'min-volatility', 'esg'),
  bonds: (f, p) => isBond(f) && (p.bonds === 'chf' ? has(f, 'switzerland') : has(f, 'chf-hedged')) && !has(f, 'high-yield', 'money-market'),
};

export function score(f, p, broker, rules) {
  const eff = effectiveAnnualCost(f);
  const horizon = { long: 10, medium: 7, short: 3 }[p.horizon] || 10;
  const tc = tradeCosts(f, broker, rules);
  const oneOff = tc.stampPct + (tc.fxPct ?? 0.5) + (tc.commissionPct || 0);
  let s = eff.pct + oneOff / horizon;
  const reasons = [];
  const size = f.fund_size_m?.v;
  if (size != null && size < 100) { s += 0.15; reasons.push('small fund'); } else if (size != null && size < 500) s += 0.03;
  const age = f.inception?.v ? (Date.now() - Date.parse(f.inception.v)) / 3.15e10 : null;
  if (age != null && age < 3) { s += 0.03; reasons.push('young fund'); }
  if (!sixLine(f)) { s += 0.05; reasons.push('not listed on SIX'); }
  if (p.esg === 'prefer' && has(f, 'esg')) s -= 0.03;
  if (p.income === 'any' || !p.income) { /* neutral */ }
  return { score: s, eff, reasons };
}

export function shortlist(index, profile, broker, rules, { perRole = 4 } = {}) {
  const roles = [];
  const approach = profile.approach;
  roles.push({ role: 'core', title: approach === 'max-div' ? 'Global core (one fund)' : 'Global core', filter: ROLE_FILTERS.core });
  if (approach === 'home-bias' || (profile.homeBias && profile.homeBias > 0)) roles.push({ role: 'home-bias', title: 'Swiss home bias', filter: ROLE_FILTERS['home-bias'] });
  if (approach === 'themes' || approach === 'specific') {
    for (const key of profile.themes || []) {
      const t = THEMES[key];
      if (t) roles.push({ role: 'satellite', key, title: t.label, filter: (f) => isEquity(f) && has(f, ...t.tags) });
    }
  }
  if (profile.bonds && profile.bonds !== 'none') roles.push({ role: 'bonds', title: profile.bonds === 'chf' ? 'CHF bonds' : 'Global bonds, CHF-hedged', filter: ROLE_FILTERS.bonds });

  return roles.map((r) => {
    const cands = index.funds.filter((f) => passesCommon(f, profile) && r.filter(f, profile))
      .map((f) => ({ fund: f, ...score(f, profile, broker, rules) }))
      .sort((a, b) => a.score - b.score);
    return { ...r, candidates: dedupeShareClasses(cands).slice(0, perRole) };
  });
}

// Several share classes of one fund (same benchmark, provider, hedging) crowd a list; keep the best-scored one
// unless the profile asks for a specific class.
function dedupeShareClasses(cands) {
  const seen = new Set(), out = [];
  for (const c of cands) {
    const f = c.fund;
    const key = [f.provider, f.benchmark?.v, f.hedged?.v, f.use_of_income?.v].join('|');
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(c);
  }
  return out;
}
