import type { OhlcBar } from "@/lib/types";

/** Backend CandleStore snapshot shape (columnar). */
export type ColumnarOhlc = {
  dates?: number[];
  opens?: number[];
  highs?: number[];
  lows?: number[];
  closes?: number[];
  volumes?: number[];
};

/** Normalize snapshot OHLC (columnar or bar array) to chart bars. */
export function toOhlcBars(raw: unknown): OhlcBar[] {
  if (Array.isArray(raw)) {
    return raw.filter(
      (b): b is OhlcBar =>
        !!b &&
        typeof b === "object" &&
        (b as OhlcBar).t != null &&
        (b as OhlcBar).c != null,
    );
  }
  if (!raw || typeof raw !== "object") return [];
  const col = raw as ColumnarOhlc;
  const dates = col.dates;
  if (!Array.isArray(dates) || !dates.length) return [];
  const opens = col.opens || [];
  const highs = col.highs || [];
  const lows = col.lows || [];
  const closes = col.closes || [];
  const volumes = col.volumes || [];
  const out: OhlcBar[] = [];
  for (let i = 0; i < dates.length; i++) {
    const c = Number(closes[i] ?? 0);
    out.push({
      t: Number(dates[i]),
      o: Number(opens[i] ?? c),
      h: Number(highs[i] ?? c),
      l: Number(lows[i] ?? c),
      c,
      v: Number(volumes[i] ?? 0),
    });
  }
  return out;
}

/** Map of series key → bars from snap.candles.ohlc. */
export function ohlcMapFromSnap(
  candles: { ohlc?: Record<string, unknown> } | null | undefined,
): Record<string, OhlcBar[]> {
  const raw = candles?.ohlc || {};
  const out: Record<string, OhlcBar[]> = {};
  for (const [key, value] of Object.entries(raw)) {
    const bars = toOhlcBars(value);
    if (bars.length) out[key] = bars;
  }
  return out;
}
