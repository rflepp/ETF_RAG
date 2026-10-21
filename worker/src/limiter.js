// Rate limits and paid-spend budget, kept in one Durable Object so counts are exact (no KV races).
// LimiterCore holds the logic over a tiny async storage interface (get/put), so tests run in plain Node.

const HOUR = 3600_000, DAY = 24 * HOUR;

export class LimiterCore {
  constructor(storage, now = () => Date.now()) { this.storage = storage; this.now = now; }

  // kind: 'chat' | 'embed'. Sliding windows over the last hour and the last 24 hours.
  async take(code, kind, perHour, perDay) {
    const key = `u:${kind}:${code}`;
    const t = this.now();
    const hits = ((await this.storage.get(key)) || []).filter((x) => x > t - DAY);
    const lastHour = hits.filter((x) => x > t - HOUR);
    if (perHour && lastHour.length >= perHour) return { ok: false, reason: 'hour', retryAt: lastHour[0] + HOUR, ...counts(hits, lastHour, perHour, perDay) };
    if (perDay && hits.length >= perDay) return { ok: false, reason: 'day', retryAt: hits[0] + DAY, ...counts(hits, lastHour, perHour, perDay) };
    hits.push(t);
    await this.storage.put(key, hits);
    return { ok: true, ...counts(hits, [...lastHour, t], perHour, perDay) };
  }

  // Give a slot back when the request failed for reasons that are not the user's fault.
  async refund(code, kind) {
    const key = `u:${kind}:${code}`;
    const hits = (await this.storage.get(key)) || [];
    hits.pop();
    await this.storage.put(key, hits);
  }

  async usage(code, kind, perHour, perDay) {
    const t = this.now();
    const hits = ((await this.storage.get(`u:${kind}:${code}`)) || []).filter((x) => x > t - DAY);
    return counts(hits, hits.filter((x) => x > t - HOUR), perHour, perDay);
  }

  async spendToday() {
    return (await this.storage.get(`spend:${utcDay(this.now())}`)) || 0;
  }

  // Reserve an estimated amount before a paid call; settle with the real cost afterwards.
  async reserve(chf, capChf) {
    const key = `spend:${utcDay(this.now())}`;
    const spent = (await this.storage.get(key)) || 0;
    if (spent + chf > capChf) return { ok: false, spent };
    await this.storage.put(key, spent + chf);
    return { ok: true, spent: spent + chf };
  }

  async settle(reservedChf, actualChf) {
    const key = `spend:${utcDay(this.now())}`;
    const spent = (await this.storage.get(key)) || 0;
    await this.storage.put(key, Math.max(0, spent - reservedChf + actualChf));
  }
}

function counts(hits, lastHour, perHour, perDay) {
  return { usedHour: lastHour.length, usedDay: hits.length, perHour, perDay };
}

export const utcDay = (t) => new Date(t).toISOString().slice(0, 10);
