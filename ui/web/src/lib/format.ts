export function fmtNum(n: unknown, digits = 2): string {
  if (n == null || Number.isNaN(Number(n))) return "—";
  return Number(n).toLocaleString(undefined, { maximumFractionDigits: digits });
}

export function fmtTime(t?: number): string {
  if (!t) return "—";
  return new Date(t * 1000).toLocaleTimeString();
}

export function pct(w: number | undefined): string {
  if (w == null || Number.isNaN(w)) return "—";
  return `${(w * 100).toFixed(1)}%`;
}
