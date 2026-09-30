"use client";

export function EquityChart({ values }: { values: number[] }) {
  if (!values.length) {
    return (
      <div className="h-[200px] flex items-center justify-center text-sm text-[var(--muted)]">
        no equity curve
      </div>
    );
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const w = 640;
  const h = 200;
  const pad = 12;
  const pts = values
    .map((v, i) => {
      const x = pad + (i / Math.max(1, values.length - 1)) * (w - pad * 2);
      const y = pad + (1 - (v - min) / span) * (h - pad * 2);
      return `${x},${y}`;
    })
    .join(" ");
  const last = values[values.length - 1];
  const up = last >= values[0];

  return (
    <div>
      <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-[200px]">
        <polyline
          fill="none"
          stroke={up ? "var(--success)" : "var(--danger)"}
          strokeWidth="2"
          points={pts}
        />
      </svg>
      <p className="text-xs text-[var(--muted)] mt-1">
        n={values.length} · last {last.toLocaleString(undefined, { maximumFractionDigits: 2 })} ·
        range {min.toFixed(2)}…{max.toFixed(2)}
      </p>
    </div>
  );
}
