// Pure-logic tests for the site modules (run: node --test site/test/*.test.js).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

import { validateAnswer } from '../js/chat.js';
import { effectiveAnnualCost, firstYearPct, tradeCosts } from '../js/costs.js';
import { renderMarkdown } from '../js/md.js';
import { shortlist } from '../js/rank.js';
import { BM25, rrf } from '../js/search.js';

const rules = JSON.parse(readFileSync(new URL('../rules/ch_trading.json', import.meta.url)));
const fund = (o) => ({ isin: 'IE00B4L5Y983', name: { v: 'X' }, theme_tags: [], docs: [], ...o });

test('stamp duty depends on broker type and domicile', () => {
  const ie = fund({ domicile: { v: 'Ireland' } });
  const ch = fund({ isin: 'CH0237935652', domicile: { v: 'Switzerland' } });
  assert.equal(tradeCosts(ie, rules.brokers.swissquote, rules).stampPct, 0.15);
  assert.equal(tradeCosts(ch, rules.brokers.swissquote, rules).stampPct, 0.075);
  assert.equal(tradeCosts(ie, rules.brokers.ibkr, rules).stampPct, 0);
});

test('a CHF line on SIX needs no FX', () => {
  const f = fund({ listings: { v: [{ exchange: 'SIX Swiss Exchange', ticker: 'VWRL', ccy: 'CHF' }] } });
  const g = fund({ listings: { v: [{ exchange: 'SIX Swiss Exchange', ticker: 'SWDA', ccy: 'USD' }] } });
  assert.equal(tradeCosts(f, rules.brokers.swissquote, rules).fxPct, 0);
  assert.equal(tradeCosts(g, rules.brokers.swissquote, rules).fxPct, 0.95);
  assert.equal(tradeCosts(g, rules.brokers.swissquote, rules).fxCcy, 'USD');
});

test('first-year cost adds one-off costs to TER', () => {
  const f = fund({ ter: { v: 0.2 }, domicile: { v: 'Ireland' } });
  assert.equal(firstYearPct(f, tradeCosts(f, rules.brokers.yuh, rules)), 0.2 + 0.15 + 0.95 + 0.5);
});

test('tracking difference beats TER as cost measure', () => {
  assert.deepEqual(effectiveAnnualCost(fund({ ter: { v: 0.2 }, td_avg_5y: 0.07 })), { pct: -0.07, basis: 'td5' });
  assert.equal(effectiveAnnualCost(fund({ ter: { v: 0.2 } })).basis, 'ter');
});

test('validateAnswer drops unknown citations and foreign ISINs', () => {
  const a = validateAnswer({ reply: 'ok [IE00B4L5Y983-kid#3] bad [IE0000000000-kid#9]', picks: [{ isin: 'IE00B4L5Y983', citations: ['IE00B4L5Y983-kid#3', 'x'] }, { isin: 'US0378331005' }], allocation: [{ isin: 'US0378331005', weight: 100 }] },
    new Set(['IE00B4L5Y983-kid#3']), new Set(['IE00B4L5Y983']));
  assert.equal(a.reply, 'ok [IE00B4L5Y983-kid#3] bad ');
  assert.equal(a.picks.length, 1);
  assert.deepEqual(a.picks[0].citations, ['IE00B4L5Y983-kid#3']);
  assert.deepEqual(a.allocation, []);
  assert.equal(a.droppedPicks, 1);
});

test('markdown escapes html and renders citations through the callback', () => {
  const html = renderMarkdown('**Cheap** <script>x</script>\n- a [IE00B4L5Y983-factsheet#d0]', (id) => `<a>${id}</a>`);
  assert.ok(html.includes('<strong>Cheap</strong> &lt;script&gt;'));
  assert.ok(html.includes('<li>a <a>IE00B4L5Y983-factsheet#d0</a></li>'));
});

test('bm25 and rrf', () => {
  const bm = new BM25(['semiconductor chips', 'swiss stocks', 'world equity']);
  const s = bm.scores('semiconductor');
  assert.ok(s[0] > 0 && s[1] === 0);
  assert.deepEqual(rrf([[2, 1], [1, 0]]).map(([i]) => i), [1, 2, 0]);
});

test('shortlist roles and filters on the published data', () => {
  const data = JSON.parse(readFileSync(new URL('../data/etfs.json', import.meta.url)));
  const index = { funds: data.funds };
  const profile = { horizon: 'long', approach: 'home-bias', homeBias: 20, em: 'yes', bonds: 'global-hedged', esg: 'any', income: 'acc', hedge: 'none', broker: 'swissquote', themes: [] };
  const roles = shortlist(index, profile, rules.brokers.swissquote, rules);
  assert.deepEqual(roles.map((r) => r.role), ['core', 'home-bias', 'bonds']);
  for (const c of roles[0].candidates) {
    assert.ok(c.fund.theme_tags.includes('all-world'), `${c.fund.name.v} is all-world`);
    assert.notEqual(c.fund.use_of_income?.v, 'Distributing');
  }
  for (const c of roles[1].candidates) assert.ok(c.fund.theme_tags.includes('switzerland'));
  const scores = roles[0].candidates.map((c) => c.score);
  assert.deepEqual(scores, [...scores].sort((a, b) => a - b));
});
