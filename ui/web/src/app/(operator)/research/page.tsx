"use client";

import { EmptyState, PageHeader, StatusBadge } from "@/components/ui";
import { useT } from "@/lib/i18n";
import { useSnapshot } from "@/lib/snapshot";

type Candidate = {
  strategy?: string;
  real_wins?: number;
  random_wins?: number;
  shuffled_wins?: number;
  adjusted_p?: number;
  blockers?: string[];
  window_nets?: number[];
  window_p?: number[];
  window_power_nets?: number[];
};

const BLOCKER_COPY: Record<string, string> = {
  real_below_min_windows: "Real evidence clears fewer windows than the frozen minimum",
  multiplicity: "Holm correction over the candidate set did not clear alpha",
  random_control_dirty: "Random timing reproduced the result — drift, not timing",
  shuffled_control_dirty: "Shuffled returns reproduced the result — no time order edge",
  power_inadequate: "The statistic could not detect the injected weak edge",
  no_eligible_candidate: "No candidate cleared every gate",
  no_candidates: "The lab received no candidates",
};

function explain(code: string): string {
  return BLOCKER_COPY[code] ?? code;
}

function NetChart({ nets, power }: { nets: number[]; power: number[] }) {
  const all = [...nets, ...power, 0];
  const lo = Math.min(...all);
  const hi = Math.max(...all);
  const span = hi - lo || 1;
  const width = 360;
  const height = 120;
  const x = (i: number, n: number) => 12 + (i * (width - 24)) / Math.max(1, n - 1);
  const y = (v: number) => height - 12 - ((v - lo) / span) * (height - 24);
  const path = (values: number[]) =>
    values.map((v, i) => `${i ? "L" : "M"}${x(i, values.length).toFixed(1)},${y(v).toFixed(1)}`).join(" ");

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-32" role="img" aria-label="per-window net and injected-power net">
      <line x1={12} x2={width - 12} y1={y(0)} y2={y(0)} stroke="var(--muted)" strokeDasharray="4 4" />
      {nets.length > 0 && <path d={path(nets)} fill="none" stroke="#3b82f6" strokeWidth={2} />}
      {power.length > 0 && <path d={path(power)} fill="none" stroke="#22c55e" strokeWidth={2} strokeDasharray="5 3" />}
      {nets.map((v, i) => <circle key={i} cx={x(i, nets.length)} cy={y(v)} r={3} fill="#3b82f6" />)}
    </svg>
  );
}

export default function ResearchPage() {
  const { snap } = useSnapshot();
  const t = useT();
  const r = snap?.strategy_research;

  if (!snap || snap.empty) {
    return (
      <div className="space-y-6">
        <PageHeader title={t("nav.research")} />
        <EmptyState title={t("web.waiting")} />
      </div>
    );
  }

  return (
    <div className="space-y-6 fade-in">
      <PageHeader
        title={t("nav.research")}
        description="Strategy math · per-window evidence · selection rationale"
      />

      {!r?.ok ? (
        <div className="panel">
          <StatusBadge status={r?.reason ?? "no_report"} />
          <p className="text-sm text-[var(--muted)] mt-2">
            Run: python tools/strategy_lab.py --symbol BTCUSDT --block earlier --out strategy_lab.json
          </p>
        </div>
      ) : (
        <>
          <div className="panel space-y-2">
            <div className="flex items-center gap-3 flex-wrap">
              <StatusBadge status={r.selected?.length ? "ok" : "warn"} />
              <span className="text-sm">{r.selected?.length ? "selected" : "refused"}</span>
              <span className="text-sm">{r.symbol} · {r.block}</span>
              <span className="text-xs text-[var(--muted)]">
                alpha={String(r.config?.alpha)} · Holm · draws={String(r.config?.draws)} · min windows={String(r.config?.min_windows)}
              </span>
            </div>
            <p className="text-sm">{r.selected?.length ? r.selected.join(", ") : (r.blockers ?? []).map(explain).join(" · ")}</p>
          </div>

          <div className="panel space-y-2">
            <h2 className="text-base font-semibold">Math used for the decision</h2>
            <code className="block text-xs whitespace-pre-wrap text-[var(--muted)]">
              {"skill = Σ_{held}(r_i − μ_window)·N − flips·7bps·N\n"}
              {"Holm: p_(k) = max_{j≤k} (M − j + 1)·p_(j), M = number of candidates\n"}
              {"power: +2 bps per held bar into evaluated skill only (never into prices)\n"}
              {"controls: random timing at the same activity rate; returns shuffled within the window"}
            </code>
            <p className="text-sm text-[var(--muted)]">
              Demeaning removes market drift by construction: a strategy that is merely long during a rising market scores zero.
            </p>
          </div>

          {(r.candidates ?? []).map((c: Candidate) => (
            <div className="panel space-y-3" key={c.strategy}>
              <div className="flex items-center gap-3 flex-wrap">
                <h2 className="text-base font-semibold">{c.strategy}</h2>
                <StatusBadge status={c.blockers?.length ? "warn" : "ok"} />
                <span className="text-sm">{c.blockers?.length ? "blocked" : "eligible"}</span>
                <span className="text-xs text-[var(--muted)]">
                  real {c.real_wins}/4 · random {c.random_wins}/4 · shuffled {c.shuffled_wins}/4 · adjusted p={Number(c.adjusted_p).toFixed(4)}
                </span>
              </div>
              <NetChart nets={c.window_nets ?? []} power={c.window_power_nets ?? []} />
              <div className="grid grid-cols-4 gap-2 text-xs text-[var(--muted)]">
                {(c.window_p ?? []).map((p, i) => <div key={i}>window {i + 1}: p={p.toFixed(4)}</div>)}
              </div>
              {(c.blockers ?? []).length > 0 ? (
                <ul className="text-sm list-disc pl-5">
                  {c.blockers?.map((b) => <li key={b}>{explain(b)}</li>)}
                </ul>
              ) : (
                <p className="text-sm">All gates cleared under the frozen config.</p>
              )}
            </div>
          ))}
        </>
      )}
    </div>
  );
}
