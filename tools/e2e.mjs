// End-to-end check in headless Chrome. Needs: site on :8800, tools/dev-worker.mjs on :8787,
// pipeline.serve_embed on :8788.  Usage: node tools/e2e.mjs [outdir]
import { mkdirSync, readFileSync } from 'node:fs';

import { launch } from './cdp.mjs';

const OUT = process.argv[2] || 'data/logs/e2e';
mkdirSync(OUT, { recursive: true });
const SITE = 'http://localhost:8800/';
const etfs = JSON.parse(readFileSync('site/data/etfs.json', 'utf8')).funds;
const byIsin = new Map(etfs.map((f) => [f.isin, f]));
let failures = 0;
const check = (ok, msg) => { console.log(`${ok ? 'ok  ' : 'FAIL'} ${msg}`); if (!ok) failures++; };

const b = await launch({ width: 1400, height: 1000, mobile: false, port: 9351 });
await b.goto(SITE);
await b.eval('localStorage.clear()');
await b.goto(SITE);
await b.waitFor(`document.querySelector('#asof').textContent.includes('ETFs')`);

// interview: 10y+, core + themes, semiconductors, EM yes, no bonds, ESG any, acc, unhedged, Swissquote, CHF 20000
// Tag the element whose text matches, then tap it with real mouse events.
const clickText = async (selector, label) => {
  const find = `[...document.querySelectorAll(${JSON.stringify(selector)})].find(c => c.textContent.includes(${JSON.stringify(label)}))`;
  await b.waitFor(find);
  await b.eval(`document.querySelectorAll('[data-e2e]').forEach(e => e.removeAttribute('data-e2e')); ${find}.setAttribute('data-e2e', '1')`);
  await b.tap('[data-e2e="1"]');
};
const pick = (label) => clickText('.iv-card .chip', label);
const next = (label = 'Next') => clickText('.iv-nav .btn', label);
await pick('10 years or more');
await pick('Global core + themes');
await pick('Semiconductors');
await next();
await pick('Yes, all-world');
await pick('No bonds');
await pick('No preference');
await pick('Reinvest');
await pick('No, unhedged');
await pick('Swissquote');
await b.waitFor(`document.querySelector('.iv-input')`);
await b.type('.iv-input', '20000');
await next('Show results');

await b.waitFor(`!document.querySelector('#results').hidden && document.querySelectorAll('.card').length > 0`);
const roles = await b.eval(`[...document.querySelectorAll('.role h3')].map(h => h.textContent)`);
check(roles.includes('Global core') && roles.includes('Semiconductors'), `roles shown: ${roles.join(' | ')}`);
const semis = await b.eval(`[...document.querySelectorAll('.role')].find(r => r.querySelector('h3').textContent === 'Semiconductors').querySelectorAll('.card').length`);
check(semis >= 1, `semiconductor candidates: ${semis}`);

// comparison table values must equal etfs.json
const head = await b.eval(`[...document.querySelectorAll('table.cmp thead .th-meta')].map(x => x.textContent.split(' · ').pop())`);
check(head.length >= 2, `compared funds: ${head.length}`);
const terRow = await b.eval(`[...document.querySelectorAll('table.cmp tbody tr')].find(r => r.querySelector('th')?.textContent === 'TER (p.a.)')?.querySelectorAll('td') ? [...[...document.querySelectorAll('table.cmp tbody tr')].find(r => r.querySelector('th')?.textContent === 'TER (p.a.)').querySelectorAll('td')].map(td => td.firstChild.textContent) : []`);
const terOk = head.every((isin, i) => terRow[i] === `${byIsin.get(isin).ter.v.toFixed(2)}%`);
check(terOk, `TER cells match etfs.json: ${terRow.join(', ')}`);
const stamp = await b.eval(`[...[...document.querySelectorAll('table.cmp tbody tr')].find(r => r.querySelector('th')?.textContent === 'Stamp duty per trade').querySelectorAll('td')].map(td => td.textContent.trim())`);
check(stamp.every((s, i) => s === (head[i].startsWith('CH') ? '0.075%' : '0.150%')), `stamp duty for Swissquote by domicile: ${stamp.join(', ')}`);
const srcLinks = await b.eval(`[...document.querySelectorAll('table.cmp a.src')].map(a => a.href)`);
check(srcLinks.length > 10 && srcLinks.every((h) => h.startsWith('https://')), `source links in table: ${srcLinks.length}`);
check(srcLinks.some((h) => /\.pdf(\?.*)?#page=\d+$/.test(h)), 'table links point to a PDF page');

await b.shot(`${OUT}/desktop-results.png`, { full: true });

// assistant: no code yet -> hint; enter code
check(await b.eval(`document.querySelector('#chat-log').textContent.includes('invite code')`), 'assistant asks for an invite code');
await b.eval(`document.querySelector('#chat-log .btn').click()`);
await b.waitFor(`document.querySelector('#code-dialog').open`);
const before = (await (await fetch('http://localhost:8787/usage', { headers: { Authorization: 'Bearer dev-code' } })).json()).chat;
await b.type('#code-input', 'dev-code');
await b.eval(`document.querySelector('#code-form').requestSubmit()`);
await b.waitFor(`document.querySelector('.msg-ai')`, 20000);
const ai = await b.eval(`document.querySelector('.msg-ai').innerHTML`);
check(/class="cite"/.test(ai), 'answer contains citation chips');
check(!ai.includes('IE0000000000'), 'invented citation id was removed');
check(await b.eval(`document.querySelectorAll('.card-ai').length`) >= 1, 'assistant picks are highlighted on cards');
const left = before.perHour - before.usedHour - 1;
check(await b.eval(`document.querySelector('#quota').textContent.startsWith('${left}/${before.perHour}')`), `quota counts down to ${left}: ${await b.eval(`document.querySelector('#quota').textContent`)}`);
const citeHrefs = await b.eval(`[...document.querySelectorAll('.msg-ai a.cite')].map(a => a.href)`);
check(citeHrefs.length > 0 && citeHrefs.every((h) => h.startsWith('https://')), `citation links resolve: ${citeHrefs[0]}`);

// follow-up via text box
await b.type('#chat-input', 'What about VWRL instead?');
await b.eval(`document.querySelector('#chat-form').requestSubmit()`);
await b.waitFor(`document.querySelectorAll('.msg-ai').length >= 2`, 20000);
check(await b.eval(`[...document.querySelectorAll('table.cmp thead .th-meta')].some(x => x.textContent.includes('IE00B3RBWM25'))`), 'named ticker VWRL was pulled into the comparison');
await b.shot(`${OUT}/desktop-chat.png`, { full: true });

// mobile layout: no horizontal page scroll
await b.device(390, 844, true);
await b.goto(SITE);
await b.waitFor(`document.querySelectorAll('.card').length > 0`);
const overflow = await b.eval(`document.documentElement.scrollWidth - document.documentElement.clientWidth`);
check(overflow <= 0, `no horizontal page scroll on mobile (overflow ${overflow}px)`);
await b.shot(`${OUT}/mobile-results.png`, { full: false });

const errs = b.logs.filter((l) => l.startsWith('[error]') || l.startsWith('[exception]'));
check(errs.length === 0, `console errors: ${errs.length}${errs.length ? '\n  ' + errs.join('\n  ') : ''}`);
await b.close();
console.log(failures ? `\n${failures} check(s) failed` : '\nall checks passed');
process.exit(failures ? 1 : 0);
