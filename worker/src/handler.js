// Request handling: invite-code auth, per-code limits, provider chain with a paid budget cap.
// Routes: POST /chat, POST /embed, GET /usage. The invite code travels in the Authorization header.
// No Cloudflare-only imports here, so tests and tools/dev-worker.mjs can run it in Node.

import { buildChain, ProviderError } from './providers.js';
import { buildUser, parseAnswer, sanitize, SYSTEM } from './prompt.js';

const QUERY_PREFIX = 'Represent this sentence for searching relevant passages: ';
const MAX_OUT_TOKENS = 1500;

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
  };
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
  const h = cors(env, request);
  if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: h });
  const url = new URL(request.url);

  const user = await authenticate(request, env);
  if (!user) return json({ error: 'unauthorized', message: 'Unknown or revoked invite code.' }, 401, h);
  const lim = limiter(env);

  if (url.pathname === '/usage' && request.method === 'GET') {
    const chat = await lim.usage(user.hash, 'chat', user.perHour, user.perDay);
    return json({ name: user.name, chat, paidFallback: user.paidFallback }, 200, h);
  }

  if (url.pathname === '/embed' && request.method === 'POST') {
    const body = await request.json().catch(() => ({}));
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
      return json({ error: 'embed_failed', message: e.message }, 503, h);
    }
  }

  if (url.pathname === '/chat' && request.method === 'POST') {
    let input;
    try { input = sanitize(await request.json()); } catch (e) { return json({ error: 'bad_request', message: e.message }, 400, h); }
    const slot = await lim.take(user.hash, 'chat', user.perHour, user.perDay);
    if (!slot.ok) {
      return json({ error: 'rate_limited', reason: slot.reason, retryAt: slot.retryAt,
        message: `Limit reached (${slot.reason === 'hour' ? `${user.perHour} per hour` : `${user.perDay} per day`}).` }, 429, h);
    }
    const userText = buildUser(input);
    const chain = buildChain(env, fetchFn, { paidFallback: user.paidFallback });
    const errors = [];
    for (const p of chain) {
      let reserved = 0;
      if (p.paid) {
        reserved = chf(env, estimateTokens(SYSTEM + userText), MAX_OUT_TOKENS);
        const r = await lim.reserve(reserved, Number(env.PAID_CAP_CHF_PER_DAY));
        if (!r.ok) { errors.push(`${p.name}: daily budget used up`); continue; }
      }
      try {
        const out = await p.complete({ system: SYSTEM, user: userText, maxTokens: MAX_OUT_TOKENS });
        if (p.paid) await lim.settle(reserved, chf(env, out.usage.in, out.usage.out));
        const answer = parseAnswer(out.text);
        return json({ answer, provider: p.name, model: p.model,
          usage: { usedHour: slot.usedHour, usedDay: slot.usedDay, perHour: user.perHour, perDay: user.perDay } }, 200, h);
      } catch (e) {
        if (p.paid) await lim.settle(reserved, 0);
        errors.push(`${p.name}: ${e.message}`);
        if (e instanceof ProviderError && !e.retryable) break;
      }
    }
    await lim.refund(user.hash, 'chat');
    return json({ error: 'unavailable', message: 'All free AI providers are busy or out of quota right now. Please try again later.',
      details: errors }, 503, h);
  }

  return json({ error: 'not_found' }, 404, h);
}
