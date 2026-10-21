import assert from 'node:assert/strict';
import { test } from 'node:test';

import { handle, sha256Hex } from '../src/handler.js';
import { LimiterCore } from '../src/limiter.js';
import { parseAnswer } from '../src/prompt.js';

function memoryStorage() {
  const m = new Map();
  return { get: async (k) => structuredClone(m.get(k)), put: async (k, v) => { m.set(k, structuredClone(v)); } };
}

const CODE = 'alpine-test-code';
const PAID_CODE = 'roman-test-code';

async function setup({ now = () => 1_800_000_000_000, mistral, gemini, ai } = {}) {
  const core = new LimiterCore(memoryStorage(), now);
  const codes = {
    [await sha256Hex(CODE)]: { name: 'friend', perHour: 10, perDay: 30, paidFallback: false },
    [await sha256Hex(PAID_CODE)]: { name: 'roman', perHour: 10, perDay: 30, paidFallback: true },
  };
  const env = {
    ALLOWED_ORIGINS: 'https://rflepp.github.io', FREE_CHAIN: 'mistral-free,workers-ai',
    MISTRAL_MODEL: 'm', WORKERS_AI_MODEL: 'w', GEMINI_MODEL: 'g', EMBED_MODEL: 'e',
    PAID_CAP_CHF_PER_DAY: '1.0', GEMINI_PRICE_IN: '0.09', GEMINI_PRICE_OUT: '0.36', EMBED_PER_DAY: '200',
    CODES: JSON.stringify(codes), MISTRAL_API_KEY: 'k1', GEMINI_API_KEY: 'k2',
    LIMITER: { idFromName: () => 'id', get: () => core },
    AI: ai || { run: async (model, input) => (input.text ? { data: [[3, 4]] } : { response: JSON.stringify({ reply: 'from workers ai', picks: [] }) }) },
  };
  const calls = [];
  const fetchFn = async (url, init) => {
    calls.push(url);
    if (url.includes('mistral')) return mistral ? mistral() : new Response(JSON.stringify({ choices: [{ message: { content: '{"reply":"from mistral","picks":[{"isin":"IE00B4L5Y983","role":"core"}]}' } }], usage: { prompt_tokens: 10, completion_tokens: 5 } }));
    if (url.includes('generativelanguage')) return gemini ? gemini() : new Response(JSON.stringify({ candidates: [{ content: { parts: [{ text: '{"reply":"from gemini"}' }] } }], usageMetadata: { promptTokenCount: 1000, candidatesTokenCount: 500 } }));
    throw new Error('unexpected fetch ' + url);
  };
  return { env, core, calls, fetchFn };
}

const chatReq = (code, body = { messages: [{ role: 'user', content: 'Which world ETF?' }], context: { funds: [], passages: [] } }) =>
  new Request('https://w.example/chat', { method: 'POST', headers: { Authorization: `Bearer ${code}`, Origin: 'https://rflepp.github.io', 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

test('rejects unknown codes', async () => {
  const { env, fetchFn } = await setup();
  const r = await handle(chatReq('nope-nope'), env, { fetch: fetchFn });
  assert.equal(r.status, 401);
});

test('answers via the first free provider', async () => {
  const { env, fetchFn } = await setup();
  const r = await handle(chatReq(CODE), env, { fetch: fetchFn });
  const d = await r.json();
  assert.equal(r.status, 200);
  assert.equal(d.provider, 'mistral-free');
  assert.equal(d.answer.picks[0].isin, 'IE00B4L5Y983');
  assert.equal(r.headers.get('Access-Control-Allow-Origin'), 'https://rflepp.github.io');
});

test('11th chat in an hour is refused, and the window slides', async () => {
  let t = 1_800_000_000_000;
  const { env, fetchFn } = await setup({ now: () => t });
  for (let i = 0; i < 10; i++) assert.equal((await handle(chatReq(CODE), env, { fetch: fetchFn })).status, 200);
  const r = await handle(chatReq(CODE), env, { fetch: fetchFn });
  assert.equal(r.status, 429);
  assert.equal((await r.json()).reason, 'hour');
  t += 3600_001;
  assert.equal((await handle(chatReq(CODE), env, { fetch: fetchFn })).status, 200);
});

test('daily limit applies across hours', async () => {
  let t = 1_800_000_000_000;
  const { env, fetchFn } = await setup({ now: () => t });
  for (let h = 0; h < 3; h++) {
    for (let i = 0; i < 10; i++) assert.equal((await handle(chatReq(CODE), env, { fetch: fetchFn })).status, 200);
    t += 3600_001;
  }
  const r = await handle(chatReq(CODE), env, { fetch: fetchFn });
  assert.equal(r.status, 429);
  assert.equal((await r.json()).reason, 'day');
});

test('falls over to workers ai when mistral is rate limited', async () => {
  const { env, fetchFn } = await setup({ mistral: () => new Response('slow down', { status: 429 }) });
  const d = await (await handle(chatReq(CODE), env, { fetch: fetchFn })).json();
  assert.equal(d.provider, 'workers-ai');
  assert.equal(d.answer.reply, 'from workers ai');
});

test('friends never reach the paid provider; their slot is refunded', async () => {
  const { env, core, fetchFn, calls } = await setup({
    mistral: () => new Response('quota', { status: 429 }),
    ai: { run: async () => { throw new Error('daily allocation exceeded'); } },
  });
  const r = await handle(chatReq(CODE), env, { fetch: fetchFn });
  assert.equal(r.status, 503);
  assert.ok(!calls.some((u) => u.includes('generativelanguage')));
  assert.equal((await core.usage(await sha256Hex(CODE), 'chat', 10, 30)).usedDay, 0);
});

test('paid fallback for the owner stops at the daily CHF cap', async () => {
  const { env, core, fetchFn } = await setup({
    mistral: () => new Response('quota', { status: 429 }),
    ai: { run: async () => { throw new Error('daily allocation exceeded'); } },
  });
  env.PAID_CAP_CHF_PER_DAY = '0.0007';  // one reservation (~0.0006) fits, a second on top of the settled cost does not
  const r1 = await (await handle(chatReq(PAID_CODE), env, { fetch: fetchFn })).json();
  assert.equal(r1.provider, 'gemini-paid');
  const spent = await core.spendToday();
  assert.ok(Math.abs(spent - (1000 * 0.09 + 500 * 0.36) / 1e6) < 1e-12);  // settled to the real usage
  // The reservation for the next call (estimate incl. max output tokens) no longer fits under the cap.
  const r2 = await handle(chatReq(PAID_CODE), env, { fetch: fetchFn });
  assert.equal(r2.status, 503);
  assert.match(JSON.stringify(await r2.json()), /budget used up/);
});

test('embed returns a normalised vector', async () => {
  const { env, fetchFn } = await setup();
  const r = await handle(new Request('https://w.example/embed', { method: 'POST', headers: { Authorization: `Bearer ${CODE}` }, body: JSON.stringify({ text: 'semis' }) }), env, { fetch: fetchFn });
  assert.deepEqual((await r.json()).vector, [0.6, 0.8]);
});

test('parseAnswer tolerates fences and prose', () => {
  const a = parseAnswer('Here you go:\n```json\n{"reply":"hi","picks":[{"isin":"X"}],"allocation":[{"isin":"X","weight":100}]}\n```');
  assert.equal(a.reply, 'hi');
  assert.equal(a.allocation[0].weight, 100);
  assert.equal(parseAnswer('no json at all').malformed, true);
});
