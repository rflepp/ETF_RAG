// Helper for tests/test_agent_domain.py: prints the JS shortlist and cost numbers for given profiles as JSON.
import { readFileSync } from 'node:fs';
import { firstYearPct, tradeCosts } from '../site/js/costs.js';
import { shortlist } from '../site/js/rank.js';

const [dataPath, profilesJson] = process.argv.slice(2);
const funds = JSON.parse(readFileSync(dataPath, 'utf8')).funds;
const rules = JSON.parse(readFileSync(new URL('../site/rules/ch_trading.json', import.meta.url)));
const out = JSON.parse(profilesJson).map((p) => {
  const b = rules.brokers[p.broker] || rules.brokers.other_ch;
  return {
    roles: shortlist({ funds }, p, b, rules).map((r) => ({ role: r.role, isins: r.candidates.map((c) => c.fund.isin), differs: r.candidates.map((c) => c.differs || null) })),
    costs: funds.slice(0, 40).map((f) => ({ isin: f.isin, ...tradeCosts(f, b, rules), first: firstYearPct(f, tradeCosts(f, b, rules)), line: undefined })),
  };
});
console.log(JSON.stringify(out));
