// Hybrid retrieval over the static index: int8 vectors + BM25, merged with reciprocal rank fusion.
// Pure ES module with an injectable fetch, so the same code runs in the browser and in Node (eval).

const STOP = new Set(('a an and are as at be by for from has have in is it its of on or that the this to was were ' +
  'will with what which who how does do i my me we you your can should would could about into than then there ' +
  'their they them these those any all also etf fund ucits').split(' '));

export function tokenize(text) {
  return (text.toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '')
    .match(/[a-z0-9]+(?:[.][0-9]+)?/g) || []).filter((t) => t.length > 1 && !STOP.has(t));
}

export function b64ToInt8(b64) {
  const bin = typeof atob === 'function' ? atob(b64) : Buffer.from(b64, 'base64').toString('binary');
  const out = new Int8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = (bin.charCodeAt(i) << 24) >> 24;
  return out;
}

export class BM25 {
  constructor(texts, { k1 = 1.2, b = 0.75 } = {}) {
    this.k1 = k1; this.b = b;
    this.docs = texts.map((t) => { const tf = new Map(); const toks = tokenize(t); for (const w of toks) tf.set(w, (tf.get(w) || 0) + 1); return { tf, len: toks.length }; });
    this.avg = this.docs.reduce((s, d) => s + d.len, 0) / Math.max(1, this.docs.length);
    this.df = new Map();
    for (const d of this.docs) for (const w of d.tf.keys()) this.df.set(w, (this.df.get(w) || 0) + 1);
  }
  scores(query) {
    const q = [...new Set(tokenize(query))];
    const N = this.docs.length;
    return this.docs.map((d) => {
      let s = 0;
      for (const w of q) {
        const f = d.tf.get(w); if (!f) continue;
        const n = this.df.get(w);
        const idf = Math.log(1 + (N - n + 0.5) / (n + 0.5));
        s += idf * (f * (this.k1 + 1)) / (f + this.k1 * (1 - this.b + this.b * d.len / this.avg));
      }
      return s;
    });
  }
}

// rows: Int8Array of n*dim, scales: per-row float. q: Float32Array (normalised).
export function cosineScores(q, rows, scales, dim) {
  const n = scales.length, out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    let s = 0; const o = i * dim;
    for (let j = 0; j < dim; j++) s += rows[o + j] * q[j];
    out[i] = s * scales[i];
  }
  return out;
}

const rankOf = (scores, idxs) => [...idxs].sort((a, b) => scores[b] - scores[a]);

// Reciprocal rank fusion of several ranked index lists. Items absent from a list get no credit from it.
export function rrf(rankings, k = 60) {
  const acc = new Map();
  for (const list of rankings) list.forEach((idx, r) => acc.set(idx, (acc.get(idx) || 0) + 1 / (k + r + 1)));
  return [...acc.entries()].sort((a, b) => b[1] - a[1]);
}

export class Index {
  constructor(base = 'data', fetchFn = globalThis.fetch.bind(globalThis)) {
    this.base = base; this.fetch = fetchFn; this.docCache = new Map();
  }

  async load() {
    const [etfs, vmeta, meta] = await Promise.all(['etfs.json', 'funds.vec.json', 'meta.json'].map((f) => this._json(f)));
    if (etfs.version !== vmeta.version) throw new Error('index version mismatch, reload the page');
    this.meta = meta; this.version = vmeta.version; this.dim = vmeta.dim;
    this.funds = etfs.funds;
    this.byIsin = new Map(this.funds.map((f) => [f.isin, f]));
    this.fundIsins = vmeta.isins;
    this.fundScales = Float32Array.from(vmeta.scales);
    this.fundVecs = new Int8Array(await this._bin('funds.vec'));
    this.fundBm25 = new BM25(vmeta.texts);
    this.fundTexts = vmeta.texts;
    return this;
  }

  async _json(file) { const r = await this.fetch(`${this.base}/${file}${this.version ? `?v=${this.version}` : ''}`); if (!r.ok) throw new Error(`${file}: ${r.status}`); return r.json(); }
  async _bin(file) { const r = await this.fetch(`${this.base}/${file}?v=${this.version}`); if (!r.ok) throw new Error(`${file}: ${r.status}`); return r.arrayBuffer(); }

  async docs(isin) {
    if (!this.docCache.has(isin)) {
      this.docCache.set(isin, this._json(`docs/${isin}.json`).then((d) => {
        if (d.version !== this.version) throw new Error('index version mismatch, reload the page');
        d.chunks.forEach((c) => { c.vec = b64ToInt8(c.v); delete c.v; });
        return d;
      }));
    }
    return this.docCache.get(isin);
  }

  // Level 1: which funds match a free-text need. filter(fund) narrows the candidate set (hard constraints).
  // Share classes of the same fund (same provider and benchmark) are collapsed to the best-ranked one.
  searchFunds(query, qvec, { k = 20, filter = null, mode = 'hybrid', dedupe = true } = {}) {
    const idxs = [];
    for (let i = 0; i < this.fundIsins.length; i++) if (!filter || filter(this.byIsin.get(this.fundIsins[i]))) idxs.push(i);
    if (!idxs.length) return [];
    const lists = [];
    if (mode !== 'bm25' && qvec) lists.push(rankOf(cosineScores(qvec, this.fundVecs, this.fundScales, this.dim), idxs));
    if (mode !== 'vector') {
      const bm = this.fundBm25.scores(query);
      lists.push(rankOf(bm, idxs.filter((i) => bm[i] > 0)));
    }
    const out = [], seen = new Set();
    for (const [i, score] of rrf(lists)) {
      const f = this.byIsin.get(this.fundIsins[i]);
      const key = dedupe ? `${f.provider}|${f.benchmark?.v || f.name.v}` : f.isin;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ isin: f.isin, score });
      if (out.length >= k) break;
    }
    return out;
  }

  // Level 2: passages from the given funds. perFund caps how many chunks one fund may contribute.
  async searchPassages(query, qvec, isins, { k = 12, perFund = 4, mode = 'hybrid' } = {}) {
    const docs = await Promise.all(isins.map((i) => this.docs(i)));
    const chunks = docs.flatMap((d) => d.chunks.map((c) => ({ ...c, isin: d.isin })));
    if (!chunks.length) return [];
    const all = chunks.map((_, i) => i);
    const lists = [];
    if (mode !== 'bm25' && qvec) {
      const rows = new Int8Array(chunks.length * this.dim);
      chunks.forEach((c, i) => rows.set(c.vec, i * this.dim));
      lists.push(rankOf(cosineScores(qvec, rows, Float32Array.from(chunks.map((c) => c.s)), this.dim), all));
    }
    if (mode !== 'vector') {
      const bm = new BM25(chunks.map((c) => c.t)).scores(query);
      lists.push(rankOf(bm, all.filter((i) => bm[i] > 0)));
    }
    const per = new Map(), out = [];
    for (const [i, score] of rrf(lists)) {
      const c = chunks[i], n = per.get(c.isin) || 0;
      if (n >= perFund) continue;
      per.set(c.isin, n + 1);
      out.push({ id: c.id, isin: c.isin, doc: c.doc, page: c.p, text: c.t, score });
      if (out.length >= k) break;
    }
    return out;
  }

  docUrl(isin, docId, page) {
    const f = this.byIsin.get(isin);
    const d = f && f.docs.find((x) => x.doc_id === docId);
    if (!d) return null;
    return d.doc_type === 'holdings' ? d.url : `${d.url}#page=${page || 1}`;
  }
}
