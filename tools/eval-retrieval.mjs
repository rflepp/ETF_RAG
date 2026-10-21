// Retrieval eval: recall@k and MRR for BM25-only, vector-only and hybrid, using the same search.js as the site.
// Needs the published site/data and the local embed server (python -m pipeline.serve_embed).
import { readFileSync } from 'node:fs';

import { Index } from '../site/js/search.js';

const K = Number(process.env.K || 10);
const fsFetch = async (url) => {
  const path = 'site/' + url.split('?')[0];
  const buf = readFileSync(path);
  return { ok: true, status: 200, json: async () => JSON.parse(buf.toString('utf8')), arrayBuffer: async () => buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) };
};
const PREFIX = 'Represent this sentence for searching relevant passages: ';
async function embed(q) {
  const r = await fetch('http://127.0.0.1:8788', { method: 'POST', body: JSON.stringify({ text: [PREFIX + q] }) });
  return Float32Array.from((await r.json()).data[0]);
}

const index = await new Index('data', fsFetch).load();
const Q = JSON.parse(readFileSync('eval/queries.json', 'utf8'));
const modes = ['bm25', 'vector', 'hybrid'];
const result = {};
for (const mode of modes) result[mode] = { passages: { hit: 0, rr: 0, n: 0 }, funds: { hit: 0, rr: 0, n: 0 } };
const misses = [];

for (const item of Q.passages) {
  const qv = await embed(item.q);
  for (const mode of modes) {
    const res = await index.searchPassages(item.q, qv, item.isins, { k: K, perFund: K, mode });
    const rank = res.findIndex((p) => p.isin === item.expect.isin && p.text.toLowerCase().includes(item.expect.text.toLowerCase()));
    const r = result[mode].passages; r.n++;
    if (rank >= 0) { r.hit++; r.rr += 1 / (rank + 1); } else if (mode === 'hybrid') misses.push(`passage: ${item.q}`);
  }
}
for (const item of Q.funds) {
  const qv = await embed(item.q);
  const rx = new RegExp(item.expect_name, 'i');
  for (const mode of modes) {
    const res = index.searchFunds(item.q, qv, { k: K, mode });
    const rank = res.findIndex((x) => rx.test(index.byIsin.get(x.isin).name.v));
    const r = result[mode].funds; r.n++;
    if (rank >= 0) { r.hit++; r.rr += 1 / (rank + 1); } else if (mode === 'hybrid') misses.push(`fund: ${item.q}`);
  }
}
console.log(`k=${K}  (${index.funds.length} funds)`);
console.log('mode     passages recall / MRR    funds recall / MRR');
for (const mode of modes) {
  const p = result[mode].passages, f = result[mode].funds;
  console.log(`${mode.padEnd(8)} ${(p.hit / p.n).toFixed(2)} / ${(p.rr / p.n).toFixed(2)}             ${(f.hit / f.n).toFixed(2)} / ${(f.rr / f.n).toFixed(2)}`);
}
if (misses.length) console.log('\nhybrid misses:\n  ' + misses.join('\n  '));
