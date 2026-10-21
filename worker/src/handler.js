// Request handling: invite-code auth, per-code limits, provider chain with a paid budget cap.
// Routes: POST /chat, POST /embed, POST /feedback, GET /usage. The invite code travels in the Authorization header.
// With LANGSMITH_API_KEY set, every /chat is traced to LangSmith (src/trace.js) and /feedback scores that trace.
// No Cloudflare-only imports here, so tests and tools/dev-worker.mjs can run it in Node.

import { buildChain, ProviderError } from './providers.js';
import { buildUser, groundingChecks, parseAnswer, sanitize, SYSTEM } from './prompt.js';
import { isUuid, sendFeedback, tracer } from './trace.js';

const QUERY_PREFIX = 'Represent this sentence for searching relevant passages: ';
const MAX_OUT_TOKENS = 1500;
const MAX_BODY = 256_000; // bytes; a full chat context is about 60 kB

export async function sha256Hex(s) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

function cors(env, request) {
  const origin = request.headers.get('Origin') || '';
  const allowed = (env.ALLOWED_ORIGINS || '').split(',').map((s) => s.trim());
  return {
    'Access-Control-Allow-Origin': allowed.includes(origin) ? origin : allowed[0] || '',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Authorization, Content-Type',
    'Access-Control-Max-Age': '86400',
    Vary: 'Origin',
    // JSON only: never cached, sniffed, framed or rendered as a page
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
    'Content-Security-Policy': "default-src 'none'; frame-ancestors 'none'",
    'Referrer-Policy': 'no-referrer',
  };
}

// Reads a JSON body of at most MAX_BODY bytes; null when it is too large.
async function readJson(request) {
  if (Number(request.headers.get('Content-Length') || 0) > MAX_BODY) return null;
  const text = await request.text();
  if (text.length > MAX_BODY) return null;
  try { return JSON.parse(text); } catch { return {}; }
}

const json = (obj, status, headers) => new Response(JSON.stringify(obj), { status, headers: { 'Content-Type': 'application/json', ...headers } });

async function authenticate(request, env) {
  const m = (request.headers.get('Authorization') || '').match(/^Bearer\s+(.{4,100})$/);
  if (!m) return null;
  let codes;
  try { codes = JSON.parse(env.CODES || '{}'); } catch { return null; }
  const hash = await sha256Hex(m[1].trim());
  const info = codes[hash];
  return info ? { hash, perHour: 10, perDay: 30, paidFallback: false, ...info } : null;
}

function limiter(env) {
  return env.LIMITER.get(env.LIMITER.idFromName('global'));
}

const estimateTokens = (s) => Math.ceil(s.length / 3.5);
const chf = (env, tin, tout) => (tin * Number(env.GEMINI_PRICE_IN) + tout * Number(env.GEMINI_PRICE_OUT)) / 1e6;

export async function handle(request, env, deps = {}) {
  const fetchFn = deps.fetch || globalThis.fetch.bind(globalThis);
  // work that may finish after the response (Cloudflare ctx.waitUntil); elsewhere it just runs
  const waitUntil = deps.waitUntil || ((p) => { p.catch(() => {}); });
  const h = cors(env, request);
  if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: h });
  const url = new URL(request.url);

  // Per-IP request cap (Cloudflare rate limiting binding) before any work: slows down code guessing and floods.
  if (env.IP_LIMITER) {
    const ip = request.headers.get('CF-Connecting-IP') || 'unknown';
    const { success } = await env.IP_LIMITER.limit({ key: ip });
    if (!success) return json({ error: 'rate_limited', message: 'Too many requests, slow down.' }, 429, h);
  }

  const user = await authenticate(request, env);
  if (!user) return json({ error: 'unauthorized', message: 'Unknown or revoked invite code.' }, 401, h);
  const lim = limiter(env);

  if (url.pathname === '/usage' && request.method === 'GET') {
    const chat = await lim.usage(user.hash, 'chat', user.perHour, user.perDay);
    return json({ name: user.name, chat, paidFallback: user.paidFallback }, 200, h);
  }

  if (url.pathname === '/embed' && request.method === 'POST') {
    const body = await readJson(request);
    if (!body) return json({ error: 'too_large' }, 413, h);
    const text = String(body.text || '').slice(0, 1000);
    if (!text) return json({ error: 'bad_request' }, 400, h);
    const slot = await lim.take(user.hash, 'embed', 0, Number(env.EMBED_PER_DAY || 200));
    if (!slot.ok) return json({ error: 'rate_limited', retryAt: slot.retryAt }, 429, h);
    try {
      const out = await env.AI.run(env.EMBED_MODEL, { text: [QUERY_PREFIX + text] });
      const v = out.data[0];
      const n = Math.hypot(...v) || 1;
      return json({ vector: v.map((x) => x / n), model: env.EMBED_MODEL }, 200, h);
    } catch (e) {
      await lim.refund(user.hash, 'embed');
      console.error('embed failed', e.message);
      return json({ error: 'embed_failed', message: 'Embedding is unavailable right now.' }, 503, h);
    }
  }

  if (url.pathname === '/feedback' && request.method === 'POST') {
    const body = await readJson(request);
    if (!body) return json({ error: 'too_large' }, 413, h);
    if (!env.LANGSMITH_API_KEY) return json({ error: 'unavailable', message: 'Feedback is not enabled.' }, 503, h);
    if (!isUuid(body.run_id) || ![0, 1].includes(body.score)) return json({ error: 'bad_request' }, 400, h);
    const slot = await lim.take(user.hash, 'feedback', 0, user.perDay * 2);
    if (!slot.ok) return json({ error: 'rate_limited', retryAt: slot.retryAt }, 429, h);
    try {
      await sendFeedback(env, fetchFn, { runId: body.run_id, score: body.score,
        comment: typeof body.comment === 'string' ? body.comment.slice(0, 500) : null });
      return json({ ok: true }, 202, h);
    } catch (e) {
      console.error('feedback failed', e.message);
      return json({ error: 'unavailable', message: 'Feedback could not be saved.' }, 503, h);
    }
  }

  if (url.pathname === '/chat' && request.method === 'POST') {
    const body = await readJson(request);
    if (!body) return json({ error: 'too_large' }, 413, h);
    let input;
    try { input = sanitize(body); } catch (e) { return json({ error: 'bad_request', message: e.message }, 400, h); }
    const slot = await lim.take(user.hash, 'chat', user.perHour, user.perDay);
    if (!slot.ok) {
      return json({ error: 'rate_limited', reason: slot.reason, retryAt: slot.retryAt,
        message: `Limit reached (${slot.reason === 'hour' ? `${user.perHour} per hour` : `${user.perDay} per day`}).` }, 429, h);
    }
    const userText = buildUser(input);
    const chain = buildChain(env, fetchFn, { paidFallback: user.paidFallback });
    const errors = [];
    const trace = tracer(env, fetchFn, {
      name: 'website-chat',
      inputs: { question: input.messages[input.messages.length - 1].content, history: input.messages.slice(0, -1),
        profile: input.profile, funds: input.funds.map((f) => f.isin), passages: input.passages.map((p) => p.id) },
      tags: ['website', env.DEPLOYMENT || 'production'],
      metadata: { user: user.name, paid_fallback: user.paidFallback, used_day: slot.usedDay,
        ...(input.session ? { thread_id: input.session } : {}) },
    });
    for (const p of chain) {
      let reserved = 0;
      if (p.paid) {
        reserved = chf(env, estimateTokens(SYSTEM + userText), MAX_OUT_TOKENS);
        const r = await lim.reserve(reserved, Number(env.PAID_CAP_CHF_PER_DAY));
        if (!r.ok) { errors.push(`${p.name}: daily budget used up`); continue; }
      }
      const llm = trace?.child(p.name, 'llm',
        { messages: [{ role: 'system', content: SYSTEM }, { role: 'user', content: userText }] },
        { ls_provider: p.name, ls_model_name: p.model, paid: p.paid });
      try {
        const out = await p.complete({ system: SYSTEM, user: userText, maxTokens: MAX_OUT_TOKENS });
        if (p.paid) await lim.settle(reserved, chf(env, out.usage.in, out.usage.out));
        const answer = parseAnswer(out.text);
        llm?.end({ choices: [{ message: { role: 'assistant', content: out.text } }],
          usage_metadata: { input_tokens: out.usage.in, output_tokens: out.usage.out, total_tokens: out.usage.in + out.usage.out } });
        if (trace) {
          trace.end({ answer, checks: groundingChecks(answer, input) }, null,
            { provider: p.name, model: p.model, failed_providers: errors.length });
          waitUntil(trace.flush());
        }
        return json({ answer, provider: p.name, model: p.model, run_id: trace?.id || null,
          usage: { usedHour: slot.usedHour, usedDay: slot.usedDay, perHour: user.perHour, perDay: user.perDay } }, 200, h);
      } catch (e) {
        if (p.paid) await lim.settle(reserved, 0);
        llm?.end(null, e.message);
        errors.push(`${p.name}: ${e.message}`);
        if (e instanceof ProviderError && !e.retryable) break;
      }
    }
    await lim.refund(user.hash, 'chat');
    // provider errors can quote upstream responses, so they go to the Worker log only
    console.error('all providers failed', errors);
    if (trace) {
      trace.end(null, 'all providers failed: ' + errors.join('; '), { failed_providers: errors.length });
      waitUntil(trace.flush());
    }
    return json({ error: 'unavailable', message: 'All free AI providers are busy or out of quota right now. Please try again later.' }, 503, h);
  }

  return json({ error: 'not_found' }, 404, h);
}
