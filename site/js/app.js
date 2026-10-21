import { ask, compactFund, embedQuery, sendFeedback, usage, validateAnswer } from './chat.js';
import { tradeCosts } from './costs.js';
import { describe, el, runInterview } from './interview.js';
import { esc, renderMarkdown } from './md.js';
import { shortlist } from './rank.js';
import { renderCard, renderTable } from './render.js';
import { Index } from './search.js';

const $ = (s) => document.querySelector(s);
const store = {
  get(k) { try { return JSON.parse(localStorage.getItem(k)); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
  del(k) { try { localStorage.removeItem(k); } catch { /* ignore */ } },
};

const S = { index: null, rules: null, profile: null, roles: [], selected: new Set(), code: store.get('etf.code'), messages: [], picks: new Map(), busy: false,
  session: crypto.randomUUID() }; // one LangSmith thread per page visit

async function boot() {
  // GitHub Pages cannot send frame-ancestors, so refuse to run inside another site's frame (clickjacking)
  if (window.top !== window.self) { document.body.replaceChildren(el('p', 'error', 'ETF Compass cannot be embedded in other pages.')); return; }
  try {
    const [index, rules] = await Promise.all([new Index('data').load(), fetch('rules/ch_trading.json').then((r) => r.json())]);
    S.index = index; S.rules = rules;
  } catch (e) {
    $('#main').replaceChildren(el('p', 'error', `Could not load the ETF data (${e.message}).`));
    return;
  }
  const m = S.index.meta;
  $('#asof').textContent = `${m.funds} ETFs · ${m.providers.join(', ')} · documents as of ${m.as_of || '?'}`;
  bindHeader();
  refreshQuota();
  const saved = store.get('etf.profile');
  if (saved) showResults(saved); else startInterview();
}

function bindHeader() {
  $('#btn-restart').addEventListener('click', () => startInterview(S.profile || {}));
  $('#btn-code').addEventListener('click', () => askCode());
}

function broker() {
  const b = S.rules.brokers[S.profile?.broker] || S.rules.brokers.other_ch;
  return b;
}

// ---------- interview ----------
function startInterview(start = {}) {
  $('#results').hidden = true;
  const root = $('#interview');
  root.hidden = false;
  runInterview(root, start, (profile) => { store.set('etf.profile', profile); showResults(profile); });
}

// ---------- results ----------
function showResults(profile) {
  S.profile = profile;
  $('#interview').hidden = true;
  $('#results').hidden = false;
  S.roles = shortlist(S.index, profile, broker(), S.rules);
  S.selected = new Set(S.roles.flatMap((r) => r.candidates.slice(0, r.role === 'core' ? 3 : 2).map((c) => c.fund.isin)));
  S.picks = new Map();
  S.messages = [];
  $('#chat-log').innerHTML = '';
  renderProfile();
  renderRoles();
  renderCompare();
  if (S.code) runAssistant(initialQuestion());
  else renderChatHint();
}

function renderProfile() {
  const root = $('#profile');
  root.innerHTML = '';
  for (const { key, a } of describe(S.profile)) {
    const b = el('button', 'pill', a);
    b.type = 'button';
    b.title = 'Change';
    b.addEventListener('click', () => startInterview(S.profile));
    b.dataset.key = key;
    root.append(b);
  }
}

function renderRoles() {
  const root = $('#roles');
  root.innerHTML = '';
  for (const r of S.roles) {
    const sec = el('section', 'role');
    sec.append(el('h3', null, r.title));
    if (!r.candidates.length) {
      sec.append(el('p', 'muted', 'No ETF in the current data set matches this role with your filters. Try relaxing a preference.'));
    }
    const grid = el('div', 'cards');
    for (const c of r.candidates) {
      grid.append(renderCard(S.index, c, {
        selected: S.selected.has(c.fund.isin),
        aiPick: S.picks.get(c.fund.isin),
        onToggle: (isin, on) => { on ? S.selected.add(isin) : S.selected.delete(isin); renderCompare(); },
      }));
    }
    sec.append(grid);
    root.append(sec);
  }
}

function candidates() {
  return S.roles.flatMap((r) => r.candidates.map((c) => ({ ...c, role: r.role, roleTitle: r.title })));
}

function renderCompare() {
  const funds = [...S.selected].map((i) => S.index.byIsin.get(i)).filter(Boolean);
  const roleOf = (isin) => candidates().find((c) => c.fund.isin === isin)?.roleTitle || (S.picks.has(isin) ? 'suggested' : '');
  renderTable($('#compare'), S.index, funds, { broker: broker(), rules: S.rules, amount: S.profile.amount, roleOf });
}

// ---------- assistant ----------
function initialQuestion() {
  return 'Based on my profile, which of the candidate ETFs would you pick for each role and why? Compare their costs and structure.';
}

function renderChatHint() {
  const log = $('#chat-log');
  log.innerHTML = '';
  const p = el('div', 'msg msg-sys');
  p.append(el('p', null, 'The shortlist and the comparison table work without the assistant. To get a written analysis with sources, enter your invite code.'));
  const b = el('button', 'btn', 'Enter invite code');
  b.type = 'button';
  b.addEventListener('click', () => askCode());
  p.append(b);
  log.append(p);
}

function askCode() {
  const dlg = $('#code-dialog');
  $('#code-input').value = '';
  $('#code-error').textContent = '';
  dlg.showModal();
}

async function submitCode(e) {
  e.preventDefault();
  const code = $('#code-input').value.trim();
  if (!code) return;
  try {
    await usage(code);
  } catch (err) {
    $('#code-error').textContent = err.status === 401 ? 'This code is not valid.' : 'Could not reach the assistant service. Try again later.';
    return;
  }
  S.code = code;
  store.set('etf.code', code);
  $('#code-dialog').close();
  $('#chat-log').querySelectorAll('.msg-sys').forEach((m) => m.remove());
  refreshQuota();
  if (S.profile && !S.messages.length) runAssistant(initialQuestion());
}

async function refreshQuota() {
  const q = $('#quota');
  if (!S.code) { q.textContent = 'No invite code'; return; }
  try {
    const u = await usage(S.code);
    q.textContent = `${u.chat.perHour - u.chat.usedHour}/${u.chat.perHour} questions left this hour`;
  } catch (e) {
    if (e.status === 401) { S.code = null; store.del('etf.code'); q.textContent = 'Invite code revoked'; }
    else q.textContent = 'Assistant offline';
  }
}

// Funds explicitly named in a question (ISIN or ticker) are added to the context.
function namedFunds(text) {
  const out = new Set();
  for (const m of text.toUpperCase().matchAll(/\b[A-Z]{2}[A-Z0-9]{9}\d\b/g)) if (S.index.byIsin.has(m[0])) out.add(m[0]);
  const words = new Set(text.toUpperCase().match(/\b[A-Z0-9]{3,6}\b/g) || []);
  for (const f of S.index.funds) {
    const tickers = [f.ticker?.v, ...(f.listings?.v || []).map((l) => l.ticker)].filter(Boolean);
    if (tickers.some((t) => words.has(t))) out.add(f.isin);
  }
  return [...out];
}

const WANTS_MORE = /\b(what about|alternative|other|instead|also|any|find|show me|looking for|similar)\b/i;

async function runAssistant(question, { round = 0 } = {}) {
  if (S.busy) return;
  S.busy = true;
  setBusy(true);
  if (round === 0) { S.messages.push({ role: 'user', content: question }); addMsg('user', question); }
  try {
    let qvec = null;
    try { qvec = await embedQuery(S.code, question); } catch { /* keyword-only retrieval */ }
    const base = [...S.selected];
    const named = namedFunds(question);
    const discovered = WANTS_MORE.test(question) && qvec ? S.index.searchFunds(question, qvec, { k: 3 }).map((x) => x.isin) : [];
    const isins = [...new Set([...base, ...named, ...discovered])].slice(0, 12);
    const passages = await S.index.searchPassages(`${question} costs replication risks securities lending`, qvec, isins, { k: 16, perFund: 3 });
    const roleOf = new Map(candidates().map((c) => [c.fund.isin, c.role]));
    const funds = isins.map((i) => S.index.byIsin.get(i)).map((f) => {
      const t = tradeCosts(f, broker(), S.rules);
      return compactFund(f, roleOf.get(f.isin) || 'other', { stamp_duty_pct: t.stampPct, fx_pct: t.fxPct, broker: broker().label });
    });
    const res = await ask(S.code, { profile: S.profile, messages: S.messages, funds, passages: passages.map((p) => ({ id: p.id, text: p.text })), session: S.session });
    const allowedIds = new Set([...passages.map((p) => p.id), ...funds.flatMap((f) => S.index.byIsin.get(f.isin).docs.map((d) => d.doc_id))]);
    const answer = validateAnswer(res.answer, allowedIds, new Set(isins));
    if (answer.need_more && round === 0 && answer.need_more.isins?.some((i) => S.index.byIsin.has(i) && !isins.includes(i))) {
      for (const i of answer.need_more.isins) if (S.index.byIsin.has(i)) S.selected.add(i);
      S.busy = false;
      return runAssistant(answer.need_more.query || question, { round: 1 });
    }
    S.messages.push({ role: 'assistant', content: answer.reply });
    for (const p of answer.picks) { S.picks.set(p.isin, p); S.selected.add(p.isin); }
    addAnswer(answer, res, passages);
    renderRoles();
    renderCompare();
    refreshQuota();
  } catch (e) {
    addMsg('sys', e.status === 429 ? `${e.message} Try again after ${new Date(e.data?.retryAt).toLocaleTimeString('de-CH', { hour: '2-digit', minute: '2-digit' })}.` : e.message);
    if (e.status === 401) { S.code = null; store.del('etf.code'); }
  } finally {
    S.busy = false;
    setBusy(false);
  }
}

function citeChip(passagesById) {
  return (id) => {
    const p = passagesById.get(id);
    const isin = id.slice(0, 12);
    const docId = id.split('#')[0];
    const page = p?.page;
    const href = S.index.docUrl(isin, docId, page);
    const f = S.index.byIsin.get(isin);
    const label = `${f?.ticker?.v || isin}${page ? ` p.${page}` : ''}`;
    return href ? `<a class="cite" href="${esc(href)}" target="_blank" rel="noopener noreferrer" title="${esc((p?.text || docId).slice(0, 220))}">${esc(label)}</a>` : '';
  };
}

function addAnswer(answer, res, passages) {
  const byId = new Map(passages.map((p) => [p.id, p]));
  const div = el('div', 'msg msg-ai');
  div.innerHTML = renderMarkdown(answer.reply, citeChip(byId));
  if (answer.allocation?.length) {
    const a = el('div', 'alloc');
    a.append(el('p', 'alloc-title', 'Possible allocation (secondary, adjust to taste)'));
    const ul = el('ul');
    for (const x of answer.allocation) {
      const f = S.index.byIsin.get(x.isin);
      ul.append(el('li', null, `${x.weight}% ${f?.name.v || x.isin}`));
    }
    a.append(ul);
    div.append(a);
  }
  if (answer.follow_up_questions?.length) {
    const fq = el('div', 'followups');
    for (const q of answer.follow_up_questions) {
      const b = el('button', 'pill', q);
      b.type = 'button';
      b.addEventListener('click', () => runAssistant(q));
      fq.append(b);
    }
    div.append(fq);
  }
  div.append(el('p', 'msg-meta', `Answered by ${res.model} (${res.provider}). Information only, not financial advice.`));
  if (res.run_id) div.append(feedbackButtons(res.run_id));
  $('#chat-log').append(div);
  div.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function feedbackButtons(runId) {
  const box = el('div', 'feedback');
  const label = el('span', 'msg-meta', 'Helpful?');
  box.append(label);
  for (const [text, score, name] of [['👍', 1, 'Helpful'], ['👎', 0, 'Not helpful']]) {
    const b = el('button', 'pill fb', text);
    b.type = 'button';
    b.setAttribute('aria-label', name);
    b.addEventListener('click', async () => {
      box.querySelectorAll('button').forEach((x) => { x.disabled = true; });
      try { await sendFeedback(S.code, runId, score); label.textContent = 'Thanks for the feedback.'; } catch { label.textContent = 'Feedback could not be sent.'; }
    });
    box.append(b);
  }
  return box;
}

function addMsg(kind, text) {
  const div = el('div', `msg msg-${kind}`);
  div.append(el('p', null, text));
  $('#chat-log').append(div);
}

function setBusy(on) {
  $('#chat-send').disabled = on;
  $('#chat-status').textContent = on ? 'Reading the documents…' : '';
}

$('#chat-form').addEventListener('submit', (e) => {
  e.preventDefault();
  const q = $('#chat-input').value.trim();
  if (!q) return;
  if (!S.code) { askCode(); return; }
  $('#chat-input').value = '';
  runAssistant(q);
});
$('#code-form').addEventListener('submit', submitCode);
$('#code-cancel').addEventListener('click', () => $('#code-dialog').close());

boot();
