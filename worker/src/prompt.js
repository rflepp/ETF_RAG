// System prompt and context formatting. The client sends retrieved data; the Worker owns the rules.

import { isUuid } from './trace.js';

export const LIMITS = { messages: 12, messageChars: 2000, funds: 14, passages: 18, passageChars: 1600, contextChars: 60000, profileChars: 2000 };

export const SYSTEM = `You are an ETF research assistant for private investors living in Switzerland (CHF as home currency).
You help them choose UCITS ETFs and compare them. You answer in English.

GROUNDING RULES (strict):
- Use ONLY the FUND DATA and PASSAGES given in the user message. Never use outside knowledge about specific funds.
- Recommend only ISINs that appear in FUND DATA. Never invent an ISIN, ticker, fee or figure.
- Do not write numbers (fees, returns, weights, sizes) in your text unless they appear in the context; the app shows
  the exact figures in its own comparison table, so prefer qualitative statements ("the cheapest of the three").
- Every factual claim about a fund must cite at least one passage id in square brackets, e.g. [IE00B4L5Y983-kid#12].
  Only cite ids that appear in PASSAGES. Fund data fields may be cited with their src id, e.g. [IE00B4L5Y983-factsheet].
- If the context does not contain what is needed, say so plainly and set need_more.
- Never write placeholder citations such as [None]; leave a claim uncited only if it is general knowledge, not about a fund.

WHEN NOTHING MATCHES:
- A fund with "differs_from_profile" is a near match: no ETF in the data meets all of the investor's preferences for
  that role. Say so first, then compare the alternatives by what each one gives up (e.g. hedged to CHF but without
  emerging markets, versus all-world but unhedged), and help the investor decide which preference matters more.
- If FUND DATA is empty, no ETF in the data matches the profile. Say that plainly and suggest which preference to
  relax. Do not ask the user to provide fund data; set need_more to null.

HOW TO JUDGE ETFs for a Swiss investor (explain trade-offs, do not lecture):
- Costs: TER, KID ongoing and transaction costs, and tracking difference (fund minus benchmark per year; closer to
  zero or positive is better and often tells more than TER). Trading costs: Swiss stamp duty applies when buying via a
  Swiss broker; a listing on SIX in CHF avoids FX conversion fees.
- Structure: physical full replication vs sampling vs synthetic swap, accumulating vs distributing, fund size (larger is
  usually more liquid and less likely to close), age of the fund, domicile, currency hedging (hedged classes cost more).
- Diversification: broad world or all-world funds for "maximally diversified"; a CH tilt is a deliberate home bias;
  themes/sectors (e.g. semiconductors) are concentrated satellites, say so.
- Prefer giving 2 to 4 concrete alternatives per role so the user can compare. Allocation weights are optional and
  secondary; only suggest them if the user asked or the profile clearly implies a portfolio.
- You give information, not personal financial advice. Mention this once briefly if you recommend products, not every turn.

OUTPUT: a single JSON object, no markdown fences:
{
  "reply": "markdown answer for the user, with [passage-id] citations",
  "picks": [{"isin": "...", "role": "core|home-bias|satellite|bonds|alternative|other", "why": "one sentence", "citations": ["passage-id", ...]}],
  "allocation": null or [{"isin": "...", "weight": 60}],
  "follow_up_questions": ["short question the user might want to answer next", ...],
  "need_more": null or {"isins": ["..."], "query": "what to look up"}
}`;

const clip = (s, n) => (typeof s === 'string' && s.length > n ? s.slice(0, n) + '…' : s);

export function sanitize(body) {
  if (!body || typeof body !== 'object') throw new Error('bad request');
  const messages = Array.isArray(body.messages) ? body.messages.slice(-LIMITS.messages) : [];
  if (!messages.length) throw new Error('no messages');
  const ctx = body.context || {};
  const profile = body.profile && typeof body.profile === 'object' && !Array.isArray(body.profile) ? body.profile : null;
  if (profile && JSON.stringify(profile).length > LIMITS.profileChars) throw new Error('profile too large');
  return {
    profile,
    messages: messages.map((m) => ({ role: m.role === 'assistant' ? 'assistant' : 'user', content: clip(String(m.content || ''), LIMITS.messageChars) })),
    funds: (Array.isArray(ctx.funds) ? ctx.funds : []).filter((f) => f && typeof f === 'object').slice(0, LIMITS.funds),
    passages: (Array.isArray(ctx.passages) ? ctx.passages : []).slice(0, LIMITS.passages)
      .map((p) => ({ id: String(p.id || ''), text: clip(String(p.text || ''), LIMITS.passageChars) })),
    session: isUuid(body.session) ? body.session : null,
  };
}

export function buildUser({ profile, messages, funds, passages }) {
  const parts = [];
  if (profile) parts.push('INVESTOR PROFILE (from the guided interview):\n' + JSON.stringify(profile));
  parts.push('FUND DATA (candidate ETFs; each field is {v: value, s: source id, p: page}):\n' + JSON.stringify(funds));
  parts.push('PASSAGES:\n' + passages.map((p) => `[${p.id}] ${p.text}`).join('\n\n'));
  const history = messages.slice(0, -1).map((m) => `${m.role.toUpperCase()}: ${m.content}`).join('\n');
  if (history) parts.push('CONVERSATION SO FAR:\n' + history);
  parts.push('CURRENT QUESTION:\n' + messages[messages.length - 1].content);
  let text = parts.join('\n\n');
  if (text.length > LIMITS.contextChars) text = text.slice(0, LIMITS.contextChars) + '\n[context truncated]';
  return text;
}

// Models sometimes group sources as [id1, id2]; split them so each id is checked on its own (as site/js/chat.js does).
const ID = '[A-Z]{2}[A-Z0-9]{9}\\d-[a-z]+(?:#d?\\d+)?';
const CITE_GROUP = new RegExp(`\\[\\s*(${ID}(?:\\s*[,;]\\s*${ID})+)\\s*\\]`, 'g');
const splitCitations = (s) => s.replace(CITE_GROUP, (m, ids) => ids.split(/\s*[,;]\s*/).map((id) => `[${id}]`).join(' '));

// Models sometimes wrap JSON in fences or add prose; pull out the first JSON object and normalise its shape.
export function parseAnswer(text) {
  let s = String(text || '').trim().replace(/^```(?:json)?\s*/i, '').replace(/```\s*$/, '');
  const a = s.indexOf('{'), b = s.lastIndexOf('}');
  if (a < 0 || b < a) return { reply: s || 'Sorry, I could not produce an answer.', picks: [], allocation: null, follow_up_questions: [], need_more: null, malformed: true };
  let obj;
  try { obj = JSON.parse(s.slice(a, b + 1)); } catch { return { reply: s, picks: [], allocation: null, follow_up_questions: [], need_more: null, malformed: true }; }
  return {
    reply: typeof obj.reply === 'string' ? splitCitations(obj.reply) : '',
    picks: Array.isArray(obj.picks) ? obj.picks.filter((p) => p && typeof p.isin === 'string') : [],
    allocation: Array.isArray(obj.allocation) ? obj.allocation.filter((x) => x && typeof x.isin === 'string' && typeof x.weight === 'number') : null,
    follow_up_questions: Array.isArray(obj.follow_up_questions) ? obj.follow_up_questions.filter((q) => typeof q === 'string').slice(0, 4) : [],
    need_more: obj.need_more && typeof obj.need_more === 'object' ? obj.need_more : null,
  };
}

const CITE = /\[([A-Z]{2}[A-Z0-9]{9}\d-[a-z]+(?:#d?\d+)?)\]/g;

// The same rules the browser applies before showing an answer (site/js/chat.js validateAnswer), counted for the trace:
// a citation must be a passage id or a document id of a candidate fund; picks must be candidates.
export function groundingChecks(answer, { funds, passages }) {
  const ids = new Set(passages.map((p) => p.id));
  const isins = new Set(funds.map((f) => f.isin).filter(Boolean));
  const cited = [...answer.reply.matchAll(CITE)].map((m) => m[1]);
  const known = (id) => ids.has(id) || (!id.includes('#') && isins.has(id.slice(0, 12)));
  return {
    citations: cited.length,
    unknown_citations: [...new Set(cited.filter((c) => !known(c)))],
    picks_outside_candidates: answer.picks.map((p) => p.isin).filter((i) => !isins.has(i)),
    malformed: Boolean(answer.malformed),
  };
}
