// Local Worker for development and e2e tests: the real handle() with in-memory limiter,
// embeddings from pipeline/serve_embed.py, and a scripted model unless a real one is configured:
//   MISTRAL_API_KEY=... node tools/dev-worker.mjs        Mistral API (free tier), as in production
//   OLLAMA_MODEL=qwen3:8b node tools/dev-worker.mjs       local model through Ollama, no key needed
// Invite code for local use: "dev-code". PORT overrides 8787.
import { createServer } from 'node:http';

import { handle, sha256Hex } from '../worker/src/handler.js';
import { LimiterCore } from '../worker/src/limiter.js';

const store = new Map();
const core = new LimiterCore({ get: async (k) => structuredClone(store.get(k)), put: async (k, v) => { store.set(k, structuredClone(v)); } });

// Scripted model: picks the first fund per role from FUND DATA and cites the first passage of each pick.
function scripted(user) {
  const funds = JSON.parse(user.match(/FUND DATA[^\n]*\n(.*)\n\nPASSAGES/s)[1]);
  const passages = [...user.matchAll(/^\[([^\]]+)\] /gm)].map((m) => m[1]);
  const byRole = new Map();
  for (const f of funds) if (!byRole.has(f.role)) byRole.set(f.role, f);
  const picks = [...byRole.values()].map((f) => ({ isin: f.isin, role: f.role, why: `Lowest cost in its group (scripted dev answer).`,
    citations: passages.filter((p) => p.startsWith(f.isin)).slice(0, 1) }));
  const lines = picks.map((p) => `- **${funds.find((f) => f.isin === p.isin).name.v}** for ${p.role} ${p.citations.map((c) => `[${c}]`).join(' ')}`);
  return JSON.stringify({ reply: `Here is a scripted answer for local testing:\n${lines.join('\n')}\n\nThis cites a fake id [IE0000000000-kid#99] that must be removed.`,
    picks, allocation: picks.length > 1 ? picks.map((p, i) => ({ isin: p.isin, weight: i === 0 ? 80 : Math.round(20 / (picks.length - 1)) })) : null,
    follow_up_questions: ['Why is the tracking difference different?'], need_more: null });
}

// Local model through Ollama in place of Workers AI (same messages, JSON output).
async function ollama(input) {
  const r = await fetch('http://127.0.0.1:11434/api/chat', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: process.env.OLLAMA_MODEL, messages: input.messages, stream: false, format: 'json', think: false,
      options: { temperature: input.temperature, num_predict: input.max_tokens, num_ctx: 24576 } }),
  });
  if (!r.ok) throw new Error(`ollama ${r.status}: ${(await r.text()).slice(0, 200)}`);
  const d = await r.json();
  return { response: d.message?.content || '', usage: { prompt_tokens: d.prompt_eval_count, completion_tokens: d.eval_count } };
}

const env = {
  ALLOWED_ORIGINS: 'http://localhost:8800,http://127.0.0.1:8800', FREE_CHAIN: process.env.MISTRAL_API_KEY ? 'mistral-free,workers-ai' : 'workers-ai',
  MISTRAL_MODEL: 'mistral-small-latest', WORKERS_AI_MODEL: process.env.OLLAMA_MODEL ? `ollama:${process.env.OLLAMA_MODEL}` : 'scripted-dev-model', GEMINI_MODEL: 'none', EMBED_MODEL: 'bge-small-local',
  PAID_CAP_CHF_PER_DAY: '0', GEMINI_PRICE_IN: '0', GEMINI_PRICE_OUT: '0', EMBED_PER_DAY: '1000',
  MISTRAL_API_KEY: process.env.MISTRAL_API_KEY,
  // traced to LangSmith (tag "dev") only when these are set, e.g. with node --env-file=.env
  LANGSMITH_API_KEY: process.env.LANGSMITH_API_KEY, LANGSMITH_ENDPOINT: process.env.LANGSMITH_ENDPOINT,
  LANGSMITH_PROJECT: process.env.LANGSMITH_PROJECT, DEPLOYMENT: 'dev',
  CODES: JSON.stringify({ [await sha256Hex('dev-code')]: { name: 'dev', perHour: Number(process.env.PER_HOUR || 10), perDay: 30 } }),
  LIMITER: { idFromName: () => 'g', get: () => core },
  AI: {
    async run(model, input) {
      if (input.text) {
        const r = await fetch('http://127.0.0.1:8788', { method: 'POST', body: JSON.stringify({ text: input.text }), headers: { 'Content-Type': 'application/json' } });
        return r.json();
      }
      if (process.env.OLLAMA_MODEL) return ollama(input);
      return { response: scripted(input.messages[1].content), usage: {} };
    },
  },
};

// localhost only: the dev server knows the dev code and may hold your Mistral key
createServer(async (req, res) => {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const body = chunks.length ? Buffer.concat(chunks) : undefined;
  const request = new Request(`http://localhost:8787${req.url}`, { method: req.method, headers: req.headers, body: ['GET', 'HEAD'].includes(req.method) ? undefined : body });
  const r = await handle(request, env);
  res.writeHead(r.status, Object.fromEntries(r.headers));
  res.end(Buffer.from(await r.arrayBuffer()));
}).listen(Number(process.env.PORT || 8787), '127.0.0.1', () => console.log(`dev worker on :${process.env.PORT || 8787} (code: dev-code, model: ${env.FREE_CHAIN.startsWith('mistral') ? 'mistral' : env.WORKERS_AI_MODEL})`));
