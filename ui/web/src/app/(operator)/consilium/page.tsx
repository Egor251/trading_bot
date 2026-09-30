"use client";

import { useEffect, useMemo, useState } from "react";
import { ActionPill, EmptyState, PageHeader, StatCard } from "@/components/ui";
import { Collapsible } from "@/components/collapsible";
import { useStatus } from "@/hooks/useStatus";
import { pct } from "@/lib/format";
import { useT } from "@/lib/i18n";
import { useSnapshot } from "@/lib/snapshot";

const WEIGHT_MODES = ["equal", "risk_parity", "bandit_ucb", "manual"] as const;

export default function ConsiliumPage() {
  const { snap } = useSnapshot();
  const { status, busy, run } = useStatus();
  const t = useT();
  const ens = snap?.ensemble;
  const weightsSnap = ens?.weights || {};
  const [mode, setMode] = useState("equal");
  const [weights, setWeights] = useState<Record<string, number>>({});

  useEffect(() => {
    setMode(String(ens?.weight_mode || "equal"));
  }, [ens?.weight_mode]);

  useEffect(() => {
    const next: Record<string, number> = {};
    for (const [k, v] of Object.entries(weightsSnap)) {
      next[k] = Number(v) || 0;
    }
    // Ensure live strategies appear even if weight map is sparse.
    for (const id of ens?.strategies || []) {
      if (!(id in next)) next[id] = 0;
    }
    setWeights(next);
  }, [weightsSnap, ens?.strategies]);

  const rows = useMemo(
    () => Object.entries(weights).sort((a, b) => b[1] - a[1]),
    [weights],
  );
  const weightSum = rows.reduce((s, [, w]) => s + w, 0);

  if (!snap || snap.empty) {
    return (
      <div className="space-y-6">
        <PageHeader title={t("nav.consilium")} />
        <EmptyState title={t("web.waiting")} />
      </div>
    );
  }

  async function applyMode() {
    if (mode === "manual") {
      await run("SET_ENSEMBLE", { weight_mode: "manual", weights, persist: true });
    } else {
      await run("SET_ENSEMBLE", { weight_mode: mode, persist: true });
    }
  }

  async function applyWeights() {
    await run("SET_ENSEMBLE", { weights, persist: true });
    setMode("manual");
  }

  function normalize() {
    if (weightSum <= 0) return;
    const next: Record<string, number> = {};
    for (const [k, v] of Object.entries(weights)) {
      next[k] = v / weightSum;
    }
    setWeights(next);
  }

  return (
    <div className="space-y-4 fade-in">
      <PageHeader
        title={t("nav.consilium")}
        description={t("web.section.votes")}
        actions={
          <div className="flex gap-2 items-center flex-wrap">
            <select
              className="input w-auto"
              value={mode}
              onChange={(e) => setMode(e.target.value)}
            >
              {WEIGHT_MODES.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="btn-primary"
              disabled={busy}
              onClick={() => void applyMode()}
            >
              Apply mode
            </button>
          </div>
        }
      />
      {status ? <p className="text-sm text-[var(--muted)]">{status}</p> : null}
      <Collapsible id="con.stats" title={t("web.section.stats")} defaultOpen>
        <div className="grid gap-4 sm:grid-cols-3">
          <StatCard title="Weight mode" value={String(ens?.weight_mode || mode)} />
          <StatCard title="Decisions" value={String(ens?.decisions ?? 0)} />
          <StatCard
            title="Last"
            value={
              ens?.last ? `${ens.last.action || "?"} ${ens.last.ticker || ""}` : "—"
            }
          />
        </div>
      </Collapsible>

      <Collapsible id="con.weights" title="Weights" defaultOpen>
        <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
          <p className="text-xs text-[var(--muted)]">
            {mode === "manual" || ens?.weight_mode === "manual" ? "editable" : "from engine"}
          </p>
          <div className="flex gap-2">
            <button type="button" className="btn-ghost text-xs" onClick={normalize}>
              Normalize Σ={weightSum.toFixed(3)}
            </button>
            <button
              type="button"
              className="btn-primary text-xs"
              disabled={busy}
              onClick={() => void applyWeights()}
            >
              Apply weights (manual)
            </button>
          </div>
        </div>
        {rows.length ? (
          <table className="data-table">
            <thead>
              <tr>
                <th>{t("web.strategy")}</th>
                <th>{t("web.weight")}</th>
                <th>pct</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(([id, w]) => (
                <tr key={id}>
                  <td className="font-mono text-sm">{id}</td>
                  <td>
                    <input
                      className="input w-28"
                      type="number"
                      step="0.01"
                      min="0"
                      value={Number.isFinite(w) ? w : 0}
                      onChange={(e) =>
                        setWeights((prev) => ({
                          ...prev,
                          [id]: Number(e.target.value),
                        }))
                      }
                    />
                  </td>
                  <td>{pct(w)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-sm text-[var(--muted)]">no weights</p>
        )}
      </Collapsible>

      <Collapsible id="con.members" title={t("web.section.votes")} defaultOpen>
        {(ens?.members || []).length ? (
          <table className="data-table">
            <thead>
              <tr>
                <th>id</th>
                <th>{t("web.action")}</th>
                <th>{t("web.stance")}</th>
                <th>{t("web.ticker")}</th>
                <th>{t("web.weight")}</th>
              </tr>
            </thead>
            <tbody>
              {(ens?.members || []).map((m, i) => (
                <tr key={`${m.strategy_id}-${i}`}>
                  <td>{m.strategy_id}</td>
                  <td>
                    <ActionPill action={m.action} />
                  </td>
                  <td>{m.stance || "—"}</td>
                  <td>{m.ticker || "—"}</td>
                  <td>{pct(m.weight)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-sm text-[var(--muted)]">{t("web.empty.votes")}</p>
        )}
      </Collapsible>
    </div>
  );
}
