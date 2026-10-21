// LLM adapters. Each takes {system, user} and returns {text, usage:{in,out}} or throws ProviderError.
// "retryable" errors (quota, rate limit, outage) make the chain move on to the next provider.

export class ProviderError extends Error {
  constructor(message, { retryable = true, status = 0 } = {}) { super(message); this.retryable = retryable; this.status = status; }
}

async function postJson(fetchFn, url, body, headers) {
  const r = await fetchFn(url, { method: 'POST', headers: { 'Content-Type': 'application/json', ...headers }, body: JSON.stringify(body) });
  const text = await r.text();
  if (!r.ok) throw new ProviderError(`${url.split('?')[0]} ${r.status}: ${text.slice(0, 300)}`, { retryable: r.status === 429 || r.status >= 500 || r.status === 403, status: r.status });
  try { return JSON.parse(text); } catch { throw new ProviderError('invalid JSON from provider'); }
}

export function mistral(env, fetchFn) {
  return {
    name: 'mistral-free', model: env.MISTRAL_MODEL, paid: false,
    available: () => Boolean(env.MISTRAL_API_KEY),
    async complete({ system, user, maxTokens }) {
      const d = await postJson(fetchFn, 'https://api.mistral.ai/v1/chat/completions', {
        model: env.MISTRAL_MODEL, temperature: 0.2, max_tokens: maxTokens, response_format: { type: 'json_object' },
        messages: [{ role: 'system', content: system }, { role: 'user', content: user }],
      }, { Authorization: `Bearer ${env.MISTRAL_API_KEY}` });
      return { text: d.choices?.[0]?.message?.content || '', usage: { in: d.usage?.prompt_tokens || 0, out: d.usage?.completion_tokens || 0 } };
    },
  };
}

export function workersAi(env) {
  return {
    name: 'workers-ai', model: env.WORKERS_AI_MODEL, paid: false,
    available: () => Boolean(env.AI),
    async complete({ system, user, maxTokens }) {
      try {
        const d = await env.AI.run(env.WORKERS_AI_MODEL, {
          messages: [{ role: 'system', content: system }, { role: 'user', content: user }],
          max_tokens: maxTokens, temperature: 0.2, response_format: { type: 'json_object' },
        });
        const text = typeof d.response === 'string' ? d.response : JSON.stringify(d.response ?? '');
        return { text, usage: { in: d.usage?.prompt_tokens || 0, out: d.usage?.completion_tokens || 0 } };
      } catch (e) {
        throw new ProviderError(`workers-ai: ${e.message}`);
      }
    },
  };
}

export function gemini(env, fetchFn, { paid }) {
  const key = paid ? env.GEMINI_API_KEY : env.GEMINI_FREE_API_KEY;
  return {
    name: paid ? 'gemini-paid' : 'gemini-free', model: env.GEMINI_MODEL, paid,
    available: () => Boolean(key),
    async complete({ system, user, maxTokens }) {
      const url = `https://generativelanguage.googleapis.com/v1beta/models/${env.GEMINI_MODEL}:generateContent`;
      const d = await postJson(fetchFn, url, {
        systemInstruction: { parts: [{ text: system }] },
        contents: [{ role: 'user', parts: [{ text: user }] }],
        generationConfig: { temperature: 0.2, maxOutputTokens: maxTokens, responseMimeType: 'application/json' },
      }, { 'x-goog-api-key': key });
      const text = d.candidates?.[0]?.content?.parts?.map((p) => p.text || '').join('') || '';
      return { text, usage: { in: d.usageMetadata?.promptTokenCount || 0, out: d.usageMetadata?.candidatesTokenCount || 0 } };
    },
  };
}

export function buildChain(env, fetchFn, { paidFallback }) {
  const make = { 'mistral-free': () => mistral(env, fetchFn), 'workers-ai': () => workersAi(env), 'gemini-free': () => gemini(env, fetchFn, { paid: false }) };
  const chain = (env.FREE_CHAIN || '').split(',').map((s) => s.trim()).filter((s) => make[s]).map((s) => make[s]());
  if (paidFallback) chain.push(gemini(env, fetchFn, { paid: true }));
  return chain.filter((p) => p.available());
}
