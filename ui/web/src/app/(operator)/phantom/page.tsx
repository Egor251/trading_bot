"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { EmptyState, PageHeader, StatCard } from "@/components/ui";
import { useStatus } from "@/hooks/useStatus";
import { fmtNum } from "@/lib/format";
import { useT } from "@/lib/i18n";
import { useSnapshot } from "@/lib/snapshot";
import type { PluginRow } from "@/lib/types";

export default function PhantomPage() {
  const { snap } = useSnapshot();
  const { status, busy, run } = useStatus();
  const t = useT();
  const ph = (snap?.phantom || {}) as Record<string, unknown>;
  const plugins = (snap?.plugins || []) as PluginRow[];
  const strategyIds = useMemo(() => {
    const ids = plugins
      .filter((p) => p.is_strategy || String(p.kind || "").includes("strategy"))
      .map((p) => String(p.id || ""))
      .filter(Boolean);
    return ids.length ? ids : ["ma_cross_example"];
  }, [plugins]);

  const [ticker, setTicker] = useState("SBER");
  const [classCode, setClassCode] = useState("TQBR");
  const [limit, setLimit] = useState("60");
  const [provider, setProvider] = useState("auto");
  const [feed, setFeed] = useState("history");
  const [strategyId, setStrategyId] = useState("ma_cross_example");
  const [mode, setMode] = useState("grid");
  const [maxVariants, setMaxVariants] = useState("24");
  const [startEquity, setStartEquity] = useState("10000");
  const [feeBps, setFeeBps] = useState("5");
  const [slipBps, setSlipBps] = useState("2");
  const [seed, setSeed] = useState("42");
  const [trainBars, setTrainBars] = useState("60");
  const [testBars, setTestBars] = useState("20");
  const [minFolds, setMinFolds] = useState("5");
  const [lastAck, setLastAck] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    if (strategyIds[0] && !strategyIds.includes(strategyId)) {
      setStrategyId(strategyIds[0]);
    }
  }, [strategyIds, strategyId]);

  if (!snap || snap.empty) {
    return (
      <div className="space-y-6">
        <PageHeader title={t("nav.phantom")} />
        <EmptyState title="Waiting for phantom snapshot" />
      </div>
    );
  }

  const grid = (Array.isArray(ph.last_grid)
    ? ph.last_grid
    : Array.isArray(lastAck?.grid)
      ? lastAck.grid
      : []) as Record<string, unknown>[];
  const fills = (Array.isArray(ph.recent_fills) ? ph.recent_fills : []) as Record<
    string,
    unknown
  >[];

  function collectRun(): Record<string, unknown> {
    return {
      ticker: ticker.trim().toUpperCase(),
      class_code: classCode.trim().toUpperCase(),
      limit: Number(limit) || 60,
      provider,
      feed,
      strategy_id: strategyId,
      mode,
      max_variants: Number(maxVariants) || 24,
      start_equity: Number(startEquity) || 10_000,
      fee_bps: Number(feeBps) || 5,
      slippage_bps: Number(slipBps) || 2,
    };
  }

  async function phantomRun() {
    const res = await run("PHANTOM_RUN", collectRun(), 180);
    if (res.ok !== false) setLastAck(res);
  }

  async function other(cmd: string) {
    const base = {
      ticker: ticker.trim().toUpperCase(),
      class_code: classCode.trim().toUpperCase(),
      strategy_id: strategyId,
      feed: feed === "both" ? "history" : feed,
      bars: Number(limit) || 500,
      seed: Number(seed) || 42,
      train_bars: Number(trainBars) || 60,
      test_bars: Number(testBars) || 20,
      min_folds: Number(minFolds) || 5,
      start_equity: Number(startEquity) || 10_000,
    };
    const timeout = cmd === "PHANTOM_WALK_FORWARD" ? 600 : 120;
    const res = await run(cmd, base, timeout);
    if (res.ok !== false) setLastAck(res);
  }

  return (
    <div className="space-y-6 fade-in">
      <PageHeader
        title={t("nav.phantom")}
        description="Shadow sandbox · grid / walk-forward / capture"
      />
      <div className="grid gap-4 sm:grid-cols-4">
        <StatCard title="Fills" value={String(ph.fills ?? 0)} />
        <StatCard title="Running" value={String(ph.running ?? false)} />
        <StatCard title="Capturing" value={String(ph.capturing ?? false)} />
        <StatCard title="Grid rows" value={String(grid.length)} />
      </div>

      <section className="panel p-5 space-y-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Ticker">
            <input className="input" value={ticker} onChange={(e) => setTicker(e.target.value)} />
          </Field>
          <Field label="Class">
            <input
              className="input"
              value={classCode}
              onChange={(e) => setClassCode(e.target.value)}
            />
          </Field>
          <Field label="Limit bars">
            <input className="input" value={limit} onChange={(e) => setLimit(e.target.value)} />
          </Field>
          <Field label="Strategy">
            <select
              className="input"
              value={strategyId}
              onChange={(e) => setStrategyId(e.target.value)}
            >
              {strategyIds.map((id) => (
                <option key={id} value={id}>
                  {id}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Mode">
            <select className="input" value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="grid">grid</option>
              <option value="single">single</option>
              <option value="walk_forward">walk_forward</option>
            </select>
          </Field>
          <Field label="Feed">
            <select className="input" value={feed} onChange={(e) => setFeed(e.target.value)}>
              <option value="history">history</option>
              <option value="patterns">patterns</option>
              <option value="both">both</option>
            </select>
          </Field>
          <Field label="Provider">
            <select
              className="input"
              value={provider}
              onChange={(e) => setProvider(e.target.value)}
            >
              <option value="auto">auto</option>
              <option value="quik">quik</option>
              <option value="mock">mock</option>
              <option value="ccxt">ccxt</option>
            </select>
          </Field>
          <Field label="Max variants">
            <input
              className="input"
              value={maxVariants}
              onChange={(e) => setMaxVariants(e.target.value)}
            />
          </Field>
          <Field label="Start equity">
            <input
              className="input"
              value={startEquity}
              onChange={(e) => setStartEquity(e.target.value)}
            />
          </Field>
          <Field label="WF seed">
            <input className="input" value={seed} onChange={(e) => setSeed(e.target.value)} />
          </Field>
          <Field label="WF train">
            <input
              className="input"
              value={trainBars}
              onChange={(e) => setTrainBars(e.target.value)}
            />
          </Field>
          <Field label="WF test">
            <input
              className="input"
              value={testBars}
              onChange={(e) => setTestBars(e.target.value)}
            />
          </Field>
          <Field label="WF min folds">
            <input
              className="input"
              value={minFolds}
              onChange={(e) => setMinFolds(e.target.value)}
            />
          </Field>
          <Field label="Fee bps">
            <input className="input" value={feeBps} onChange={(e) => setFeeBps(e.target.value)} />
          </Field>
          <Field label="Slippage bps">
            <input className="input" value={slipBps} onChange={(e) => setSlipBps(e.target.value)} />
          </Field>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className="btn-primary"
            disabled={busy}
            onClick={() => void phantomRun()}
          >
            PHANTOM_RUN
          </button>
          <button
            type="button"
            className="btn-ghost"
            disabled={busy}
            onClick={() => void other("PHANTOM_WALK_FORWARD")}
          >
            Walk-forward
          </button>
          <button
            type="button"
            className="btn-ghost"
            disabled={busy}
            onClick={() => void run("PHANTOM_CAPTURE", { capturing: true })}
          >
            Capture on
          </button>
          <button
            type="button"
            className="btn-ghost"
            disabled={busy}
            onClick={() => void run("PHANTOM_CAPTURE", { capturing: false })}
          >
            Capture off
          </button>
          <button
            type="button"
            className="btn-ghost"
            disabled={busy}
            onClick={() => void other("PHANTOM_RESET")}
          >
            Reset
          </button>
        </div>
        {status ? <p className="text-sm text-[var(--muted)]">{status}</p> : null}
      </section>

      <section className="panel p-5">
        <h3 className="text-xs uppercase tracking-wide text-[var(--muted)] mb-3">
          Grid
        </h3>
        {grid.length ? (
          <div className="overflow-auto max-h-[420px]">
            <table className="data-table">
              <thead>
                <tr>
                  {Object.keys(grid[0] || {})
                    .slice(0, 8)
                    .map((k) => (
                      <th key={k}>{k}</th>
                    ))}
                </tr>
              </thead>
              <tbody>
                {grid.slice(0, 80).map((row, i) => (
                  <tr key={i}>
                    {Object.keys(grid[0] || {})
                      .slice(0, 8)
                      .map((k) => (
                        <td key={k} className="font-mono text-xs">
                          {typeof row[k] === "number"
                            ? fmtNum(row[k], 4)
                            : String(row[k] ?? "—")}
                        </td>
                      ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-[var(--muted)]">no grid yet</p>
        )}
      </section>

      <section className="panel p-5">
        <h3 className="text-xs uppercase tracking-wide text-[var(--muted)] mb-3">
          Recent fills
        </h3>
        {fills.length ? (
          <table className="data-table">
            <thead>
              <tr>
                <th>ticker</th>
                <th>side</th>
                <th>qty</th>
                <th>price</th>
                <th>pnl</th>
              </tr>
            </thead>
            <tbody>
              {fills.slice(0, 40).map((f, i) => (
                <tr key={i}>
                  <td>{String(f.ticker || "—")}</td>
                  <td>{String(f.side || f.action || "—")}</td>
                  <td>{fmtNum(f.quantity ?? f.qty, 4)}</td>
                  <td>{fmtNum(f.price)}</td>
                  <td>{fmtNum(f.pnl)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-sm text-[var(--muted)]">no fills</p>
        )}
      </section>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="space-y-1 text-sm block">
      <span className="text-[var(--muted)]">{label}</span>
      {children}
    </label>
  );
}
