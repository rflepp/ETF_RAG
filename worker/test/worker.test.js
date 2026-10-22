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

async function setup({ now = () => 1_800_000_000_000, mistral, gemini, ai, langsmith } = {}) {
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
  const traces = [];
  const fetchFn = async (url, init) => {
    calls.push(url);
    if (url.includes('smith.langchain.com')) {
      traces.push({ url, key: init.headers['x-api-key'], body: JSON.parse(init.body) });
      return langsmith ? langsmith() : new Response('{}', { status: 202 });
    }
    if (url.includes('mistral')) return mistral ? mistral() : new Response(JSON.stringify({ choices: [{ message: { content: '{"reply":"from mistral","picks":[{"isin":"IE00B4L5Y983","role":"core"}]}' } }], usage: { prompt_tokens: 10, completion_tokens: 5 } }));
    if (url.includes('generativelanguage')) return gemini ? gemini() : new Response(JSON.stringify({ candidates: [{ content: { parts: [{ text: '{"reply":"from gemini"}' }] } }], usageMetadata: { promptTokenCount: 1000, candidatesTokenCount: 500 } }));
    throw new Error('unexpected fetch ' + url);
  };
  return { env, core, calls, traces, fetchFn };
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
  const logged = [];
  const orig = console.error;
  console.error = (...a) => logged.push(a);
  try {
    const r2 = await handle(chatReq(PAID_CODE), env, { fetch: fetchFn });
    assert.equal(r2.status, 503);
    const body = JSON.stringify(await r2.json());
    assert.doesNotMatch(body, /budget used up|allocation exceeded|details/);  // provider details stay in the log
  } finally { console.error = orig; }
  assert.match(JSON.stringify(logged), /budget used up/);
});

test('responses carry no-store and nosniff headers', async () => {
  const { env, fetchFn } = await setup();
  const r = await handle(chatReq('nope-nope'), env, { fetch: fetchFn });
  assert.equal(r.headers.get('Cache-Control'), 'no-store');
  assert.equal(r.headers.get('X-Content-Type-Options'), 'nosniff');
});

test('oversized bodies and profiles are refused before any model call', async () => {
  const { env, calls, fetchFn } = await setup();
  const big = { messages: [{ role: 'user', content: 'x' }], context: { funds: [], passages: [{ id: 'a', text: 'y'.repeat(300_000) }] } };
  assert.equal((await handle(chatReq(CODE, big), env, { fetch: fetchFn })).status, 413);
  const profile = { messages: [{ role: 'user', content: 'x' }], profile: { notes: 'z'.repeat(3000) }, context: {} };
  assert.equal((await handle(chatReq(CODE, profile), env, { fetch: fetchFn })).status, 400);
  assert.equal(calls.length, 0);
});

test('the per-IP limiter answers 429 before checking the code', async () => {
  const { env, fetchFn } = await setup();
  const keys = [];
  env.IP_LIMITER = { limit: async ({ key }) => { keys.push(key); return { success: false }; } };
  const req = chatReq(CODE);
  req.headers.set('CF-Connecting-IP', '203.0.113.7');
  const r = await handle(req, env, { fetch: fetchFn });
  assert.equal(r.status, 429);
  assert.deepEqual(keys, ['203.0.113.7']);
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

const TRACING = { LANGSMITH_API_KEY: 'ls-key', LANGSMITH_ENDPOINT: 'https://eu.api.smith.langchain.com', LANGSMITH_PROJECT: 'p' };

test('without a LangSmith key nothing is traced', async () => {
  const { env, calls, fetchFn } = await setup();
  const d = await (await handle(chatReq(CODE), env, { fetch: fetchFn })).json();
  assert.equal(d.run_id, null);
  assert.ok(!calls.some((u) => u.includes('smith')));
});

test('a chat is traced as one root run with a child per provider attempt', async () => {
  const reply = 'Core [IE00B4L5Y983-kid#3] [IE00B4L5Y983-factsheet] [IE00BK5BQT80-kid#1]';
  const { env, traces, fetchFn } = await setup({
    mistral: () => new Response('{}', { status: 429 }),
    ai: { run: async () => ({ response: JSON.stringify({ reply, picks: [{ isin: 'IE00BK5BQT80', role: 'core' }] }) }) },
  });
  Object.assign(env, TRACING);
  const pending = [];
  const body = { messages: [{ role: 'user', content: 'Which world ETF?' }], session: '0b6f8f4e-1c2d-4e5f-8a9b-0c1d2e3f4a5b',
    context: { funds: [{ isin: 'IE00B4L5Y983' }], passages: [{ id: 'IE00B4L5Y983-kid#3', text: 'costs' }] } };
  const r = await handle(chatReq(CODE, body), env, { fetch: fetchFn, waitUntil: (p) => pending.push(p) });
  const d = await r.json();
  await Promise.all(pending);
  assert.equal(traces.length, 1);
  assert.equal(traces[0].url, 'https://eu.api.smith.langchain.com/runs/batch');
  assert.equal(traces[0].key, 'ls-key');
  const [root, first, second] = traces[0].body.post;
  assert.equal(root.id, d.run_id);
  assert.equal(root.session_name, 'p');
  assert.equal(root.inputs.question, 'Which world ETF?');
  assert.deepEqual(root.tags, ['website', 'production']);
  assert.equal(root.extra.metadata.user, 'friend');
  assert.equal(root.extra.metadata.thread_id, body.session);
  assert.equal(root.extra.metadata.provider, 'workers-ai');
  assert.deepEqual(root.outputs.checks, { citations: 3, unknown_citations: ['IE00BK5BQT80-kid#1'],
    picks_outside_candidates: ['IE00BK5BQT80'], malformed: false });
  assert.equal(first.name, 'mistral-free');
  assert.match(first.error, /429/);
  assert.equal(second.run_type, 'llm');
  assert.equal(second.parent_run_id, root.id);
  assert.ok(second.dotted_order.startsWith(root.dotted_order + '.'));
  assert.match(root.dotted_order, /^\d{8}T\d{12}Z[0-9a-f-]{36}$/);
});

test('a failing trace upload does not affect the answer', async () => {
  const { env, fetchFn } = await setup({ langsmith: () => { throw new Error('down'); } });
  Object.assign(env, TRACING);
  const errors = [];
  const orig = console.error;
  console.error = (...a) => errors.push(a.join(' '));
  const pending = [];
  try {
    const r = await handle(chatReq(CODE), env, { fetch: fetchFn, waitUntil: (p) => pending.push(p) });
    assert.equal(r.status, 200);
    await Promise.all(pending);
  } finally { console.error = orig; }
  assert.match(errors.join('\n'), /langsmith trace failed/);
});

test('feedback is validated and forwarded to LangSmith', async () => {
  const { env, traces, fetchFn } = await setup();
  const fb = (body) => new Request('https://w.example/feedback', { method: 'POST', headers: { Authorization: `Bearer ${CODE}` }, body: JSON.stringify(body) });
  const runId = '0b6f8f4e-1c2d-4e5f-8a9b-0c1d2e3f4a5b';
  assert.equal((await handle(fb({ run_id: runId, score: 1 }), env, { fetch: fetchFn })).status, 503);
  Object.assign(env, TRACING);
  assert.equal((await handle(fb({ run_id: 'x', score: 1 }), env, { fetch: fetchFn })).status, 400);
  assert.equal((await handle(fb({ run_id: runId, score: 5 }), env, { fetch: fetchFn })).status, 400);
  assert.equal((await handle(fb({ run_id: runId, score: 0, comment: 'wrong fund' }), env, { fetch: fetchFn })).status, 202);
  assert.equal(traces.length, 1);
  assert.equal(traces[0].url, 'https://eu.api.smith.langchain.com/feedback');
  assert.deepEqual(traces[0].body, { run_id: runId, key: 'user_score', score: 0, comment: 'wrong fund' });
});

test('parseAnswer splits grouped citations', () => {
  const a = parseAnswer('{"reply":"x [IE00B4L5Y983-kid#2, IE00BK5BQT80-kid#10] y"}');
  assert.equal(a.reply, 'x [IE00B4L5Y983-kid#2] [IE00BK5BQT80-kid#10] y');
});
