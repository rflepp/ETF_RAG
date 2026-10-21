// Swiss trading-cost model: stamp duty by broker type and fund domicile, FX by broker and trading line.

export function sixLine(fund) {
  const ls = fund.listings?.v || [];
  const six = ls.filter((l) => l.exchange === 'SIX Swiss Exchange');
  return six.find((l) => l.ccy === 'CHF') || six[0] || null;
}

export function isSwissDomiciled(fund) {
  return fund.isin.startsWith('CH') || (fund.domicile?.v || '').toLowerCase() === 'switzerland';
}

// broker: one entry of rules.brokers (possibly with user overrides). Returns percentages per purchase.
export function tradeCosts(fund, broker, rules) {
  const sd = rules.stamp_duty;
  const stamp = broker.swiss_dealer ? (isSwissDomiciled(fund) ? sd.swiss_pct : sd.foreign_pct) : 0;
  const line = sixLine(fund);
  // Buying a CHF line on SIX needs no conversion. Otherwise assume CHF is converted to the trading currency.
  const needsFx = !(line && line.ccy === 'CHF');
  const fx = needsFx ? broker.fx_pct : 0;
  return {
    stampPct: stamp,
    fxPct: fx,                      // null = unknown for this broker
    fxCcy: needsFx ? (line?.ccy || fund.share_class_currency?.v || null) : null,
    commissionPct: broker.commission_pct ?? null,
    line,
  };
}

// Cost of holding for one year after buying once, in % of the amount (TER plus one-off buy costs).
export function firstYearPct(fund, tc) {
  const ter = fund.ter?.v;
  if (ter == null) return null;
  return ter + tc.stampPct + (tc.fxPct || 0) + (tc.commissionPct || 0);
}

// Annual cost measure used for ranking: observed tracking difference beats the stated TER when available.
export function effectiveAnnualCost(fund) {
  if (fund.td_avg_5y != null) return { pct: -fund.td_avg_5y, basis: 'td5' };
  if (fund.td_avg_3y != null) return { pct: -fund.td_avg_3y, basis: 'td3' };
  if (fund.ter?.v != null) return { pct: fund.ter.v + 0.05, basis: 'ter' };  // unknown tracking: small penalty
  return { pct: 1, basis: 'none' };
}
