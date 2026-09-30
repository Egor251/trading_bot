"use client";

import { useMemo, useState } from "react";
import { Collapsible } from "@/components/collapsible";
import { WithHelp } from "@/components/help-tip";
import { OhlcChart } from "@/components/ohlc-chart";
import { ActionPill, EmptyState, PageHeader, StatCard, StatusBadge } from "@/components/ui";
import { useStatus } from "@/hooks/useStatus";
import { fmtNum, fmtTime } from "@/lib/format";
import { useT } from "@/lib/i18n";
import { ohlcMapFromSnap } from "@/lib/ohlc";
import { useSnapshot } from "@/lib/snapshot";
import type { EnsembleMember, OrderRow, PositionRow, TapeEvent } from "@/lib/types";

function exposureRows(raw: unknown): Array<{ key: string; value: string }> {
  if (raw == null) return [];
  if (Array.isArray(raw)) {
    return raw.slice(0, 40).map((row, i) => {
      if (row && typeof row === "object") {
        const o = row as Record<string, unknown>;
        const key = String(o.ticker || o.symbol || o.key || o.strategy_id || i);
        const value = [o.exposure, o.qty, o.quantity, o.value, o.notional]
          .filter((x) => x != null)
          .map((x) => fmtNum(x))
          .join(" · ");
        return { key, value: value || JSON.stringify(row) };
      }
      return { key: String(i), value: String(row) };
    });
  }
  if (typeof raw === "object") {
    return Object.entries(raw as Record<string, unknown>)
      .slice(0, 40)
      .map(([k, v]) => ({
        key: k,
        value: typeof v === "object" ? JSON.stringify(v) : fmtNum(v),
      }));
  }
  return [{ key: "exposure", value: String(raw) }];
}

export default function DeskPage() {
  const { snap } = useSnapshot();
  const { status, busy, run } = useStatus();
  const t = useT();
  const ohlcMap = useMemo(
    () => ohlcMapFromSnap(snap?.candles as { ohlc?: Record<string, unknown> }),
    [snap?.candles],
  );
  const chartSymbols = useMemo(() => {
    const fromOhlc = Object.keys(ohlcMap);
    const fromBook = snap?.active_keys || [];
    return Array.from(new Set([...fromOhlc, ...fromBook])).sort();
  }, [ohlcMap, snap?.active_keys]);
  const [chartSymbol, setChartSymbol] = useState("");
  const activeChart = chartSymbol || chartSymbols[0] || "";
  const chartBars = activeChart ? ohlcMap[activeChart] || [] : [];

  if (!snap || snap.empty) {
    return (
      <div className="space-y-6">
        <PageHeader title={t("nav.desk")} description={t("web.waiting")} tip={t("tip.nav.desk")} />
        <EmptyState title={t("web.no_snapshot")} hint={t("web.engine_hint")} />
      </div>
    );
  }

  const members = (snap.ensemble?.members || []) as EnsembleMember[];
  const tape = (snap.recent || []) as TapeEvent[];
  const uni = snap.active_keys || [];
  const positions = (snap.open_positions || snap.positions || []) as PositionRow[];
  const orders = (snap.open_orders || []) as OrderRow[];
  const ensEnabled = !!snap.ensemble?.enabled;
  const risk = (snap.risk || {}) as Record<string, unknown>;
  const limits = (risk.limits || {}) as Record<string, unknown>;
  const exposures = exposureRows(snap.exposures);
  const mode = String(snap.mode || "").toLowerCase();
  const liveLike = mode === "live" || mode === "quik";

  async function closeOne(ticker: string) {
    if (!confirm(t("web.actions.confirm_close", { ticker }))) return;
    await run("CLOSE_POSITION", {
      ticker,
      confirm_live: liveLike,
      reason: "operator_desk",
    });
  }

  async function closeAll() {
    if (!confirm(t("web.actions.confirm_close_all"))) return;
    await run("CLOSE_ALL", {
      confirm_close_all: true,
      confirm_live: liveLike,
    });
  }

  return (
    <div className="space-y-4 fade-in">
      <PageHeader
        title={t("nav.desk")}
        description={t("web.desk.desc")}
        tip={t("tip.nav.desk")}
        actions={
          <div className="flex flex-wrap gap-2 items-center">
            <WithHelp tip={t("tip.desk.kill")}>
              <button
                type="button"
                className="btn-kill"
                disabled={busy}
                title={t("tip.desk.kill")}
                onClick={() => void run("KILL")}
              >
                {t("desk.kill")}
              </button>
            </WithHelp>
            <WithHelp tip={t("tip.desk.resume")}>
              <button
                type="button"
                className="btn-ghost"
                disabled={busy}
                title={t("tip.desk.resume")}
                onClick={() => void run("RESUME")}
              >
                {t("desk.resume")}
              </button>
            </WithHelp>
            <WithHelp tip={t("tip.desk.autotrade")}>
              <button
                type="button"
                className={ensEnabled ? "btn-ghost" : "btn-primary"}
                disabled={busy}
                title={t("tip.desk.autotrade")}
                onClick={() =>
                  void run("SET_ENSEMBLE", { enabled: !ensEnabled, persist: true })
                }
              >
                {t("web.autotrade")} {ensEnabled ? "ON" : "OFF"}
              </button>
            </WithHelp>
            <WithHelp tip={t("tip.desk.close_all")}>
              <button
                type="button"
                className="btn-ghost"
                disabled={busy || !positions.length}
                title={t("tip.desk.close_all")}
                onClick={() => void closeAll()}
              >
                {t("web.actions.close_all")}
              </button>
            </WithHelp>
            <WithHelp tip={t("tip.desk.reset_paper")}>
              <button
                type="button"
                className="btn-ghost"
                disabled={busy}
                title={t("tip.desk.reset_paper")}
                onClick={() => {
                  if (confirm(t("web.actions.confirm_reset"))) void run("RESET_PAPER");
                }}
              >
                {t("desk.trade.reset_paper")}
              </button>
            </WithHelp>
            <WithHelp tip={t("tip.desk.save_config")}>
              <button
                type="button"
                className="btn-ghost"
                disabled={busy}
                title={t("tip.desk.save_config")}
                onClick={() => void run("SAVE_CONFIG")}
              >
                {t("web.actions.save_config")}
              </button>
            </WithHelp>
          </div>
        }
      />
      {status ? <p className="text-sm text-[var(--muted)]">{status}</p> : null}

      {snap.halted ? (
        <div className="panel p-4 border-[var(--danger)]/50 text-[var(--danger)] text-sm">
          {t("desk.banner.halted", { reason: snap.halt_reason || "—" })}
        </div>
      ) : (
        <p className="text-xs text-[var(--muted)]">{t("desk.trade.hint")}</p>
      )}

      {(() => {
        const m = (snap.market || {}) as {
          feed_source?: string;
          exchange?: string;
          error?: string;
        };
        const src = String(m.feed_source || "none");
        if (src === "api" || !src || src === "none") return null;
        return (
          <div className="panel p-3 text-sm border-[var(--warn)]/40 text-[var(--warn)]">
            {t("desk.banner.feed", {
              source: src,
              exchange: m.exchange || "—",
              error: m.error || "",
            })}
          </div>
        );
      })()}

      <Collapsible id="desk.stats" title={t("web.section.stats")} tip={t("tip.desk.stats")} defaultOpen>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          <StatCard
            title={t("web.state")}
            value={snap.halted ? t("desk.state.halted") : t("desk.state.live")}
            tone={snap.halted ? "bad" : "ok"}
            tip={t("tip.desk.state")}
          />
          <StatCard
            title={t("web.mode")}
            value={(snap.mode || "—").toUpperCase()}
            tip={t("tip.desk.mode")}
          />
          <StatCard title={t("web.equity")} value={fmtNum(snap.equity)} tip={t("tip.desk.equity")} />
          <StatCard
            title={t("web.gross")}
            value={fmtNum(snap.gross_exposure)}
            tip={t("tip.desk.gross")}
          />
          <StatCard
            title={t("web.fills")}
            value={String(snap.fills ?? "—")}
            tip={t("tip.desk.fills")}
          />
        </div>
      </Collapsible>

      <Collapsible
        id="desk.chart"
        title={t("desk.chart")}
        tip={t("tip.desk.chart")}
        defaultOpen
        actions={
          <select
            className="input w-auto text-xs py-1"
            value={activeChart}
            title={t("tip.desk.chart_symbol")}
            onChange={(e) => setChartSymbol(e.target.value)}
          >
            {chartSymbols.length ? (
              chartSymbols.map((s) => (
                <option key={s} value={s}>
                  {s}
                  {ohlcMap[s]?.length ? ` (${ohlcMap[s].length})` : ""}
                </option>
              ))
            ) : (
              <option value="">{t("web.empty_book")}</option>
            )}
          </select>
        }
      >
        {chartBars.length ? (
          <OhlcChart bars={chartBars} height={280} />
        ) : (
          <p className="text-sm text-[var(--muted)] py-10 text-center">{t("chart.no_bars")}</p>
        )}
      </Collapsible>

      <div className="grid gap-4 lg:grid-cols-3">
        <Collapsible id="desk.risk" title={t("web.section.risk")} tip={t("tip.desk.risk")} defaultOpen>
          <div className="space-y-2 text-sm">
            <div className="flex justify-between items-center gap-2">
              <span className="text-[var(--muted)] inline-flex items-center gap-1.5">
                {t("web.autotrade")}
              </span>
              <StatusBadge status={ensEnabled ? "enabled" : "disabled"} />
            </div>
            <div className="flex justify-between font-mono text-xs">
              <span className="text-[var(--muted)]">{t("desk.risk.mdd")}</span>
              <span>{fmtNum(limits.max_portfolio_drawdown)}</span>
            </div>
            <div className="flex justify-between font-mono text-xs">
              <span className="text-[var(--muted)]">{t("desk.risk.pos")}</span>
              <span>{fmtNum(limits.max_position_pct)}</span>
            </div>
            <div className="flex justify-between font-mono text-xs">
              <span className="text-[var(--muted)]">{t("desk.risk.lev")}</span>
              <span>{fmtNum(limits.max_leverage, 2)}</span>
            </div>
          </div>
        </Collapsible>

        <Collapsible
          id="desk.universe"
          title={t("web.section.universe")}
          tip={t("tip.desk.universe")}
          defaultOpen
          className="lg:col-span-2"
        >
          <div className="flex flex-wrap gap-2">
            {uni.length ? (
              uni.map((k) => (
                <span
                  key={k}
                  className="border border-[var(--panel-border)] rounded px-2 py-1 text-xs font-mono"
                >
                  {k}
                </span>
              ))
            ) : (
              <span className="text-[var(--muted)] text-sm">{t("web.empty_book")}</span>
            )}
          </div>
          <div className="mt-4">
            <h4 className="text-xs uppercase tracking-wide text-[var(--muted)] mb-2">
              {t("web.last_decision")}
            </h4>
            {snap.ensemble?.last ? (
              <div className="flex items-center gap-3">
                <ActionPill action={snap.ensemble.last.action} />
                <span className="font-mono">{snap.ensemble.last.ticker || "—"}</span>
                <span className="text-[var(--muted)] text-sm">
                  {t("web.decisions")} {snap.ensemble.decisions ?? 0}
                </span>
              </div>
            ) : (
              <span className="text-[var(--muted)] text-sm">{t("web.no_decision")}</span>
            )}
          </div>
        </Collapsible>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Collapsible
          id="desk.positions"
          title={t("web.section.positions")}
          tip={t("tip.desk.positions")}
          defaultOpen
          badge={
            <span className="text-[10px] text-[var(--muted)] font-mono">
              {positions.length}
            </span>
          }
          actions={
            positions.length ? (
              <button
                type="button"
                className="btn-ghost text-[11px] py-1"
                disabled={busy}
                title={t("tip.desk.close_all")}
                onClick={() => void closeAll()}
              >
                {t("web.actions.close_all")}
              </button>
            ) : null
          }
        >
          <table className="data-table">
            <thead>
              <tr>
                <th>{t("web.ticker")}</th>
                <th>{t("web.qty")}</th>
                <th>{t("web.avg")}</th>
                <th>{t("web.pnl")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {positions.length ? (
                positions.map((p, i) => (
                  <tr key={`${p.ticker}-${i}`}>
                    <td>{p.ticker || "—"}</td>
                    <td>{fmtNum(p.quantity, 4)}</td>
                    <td>{fmtNum(p.avg_price ?? p.price)}</td>
                    <td
                      className={
                        Number(p.pnl) > 0
                          ? "text-[var(--success)]"
                          : Number(p.pnl) < 0
                            ? "text-[var(--danger)]"
                            : ""
                      }
                    >
                      {fmtNum(p.pnl)}
                    </td>
                    <td>
                      {p.ticker ? (
                        <button
                          type="button"
                          className="btn-ghost text-[10px] py-0.5 px-2"
                          disabled={busy}
                          title={t("tip.desk.close_one")}
                          onClick={() => void closeOne(String(p.ticker))}
                        >
                          {t("web.actions.close")}
                        </button>
                      ) : null}
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={5} className="text-[var(--muted)]">
                    {t("web.empty.flat")}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </Collapsible>

        <Collapsible
          id="desk.orders"
          title={t("web.section.orders")}
          tip={t("tip.desk.orders")}
          defaultOpen
          badge={
            <span className="text-[10px] text-[var(--muted)] font-mono">{orders.length}</span>
          }
        >
          <table className="data-table">
            <thead>
              <tr>
                <th>{t("web.ticker")}</th>
                <th>{t("web.side")}</th>
                <th>{t("web.qty")}</th>
                <th>{t("web.status")}</th>
              </tr>
            </thead>
            <tbody>
              {orders.length ? (
                orders.map((o, i) => (
                  <tr key={`${o.ticker}-${i}`}>
                    <td>{o.ticker || "—"}</td>
                    <td>
                      <ActionPill action={o.action || o.side} />
                    </td>
                    <td>{fmtNum(o.quantity, 4)}</td>
                    <td>{o.status || "—"}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={4} className="text-[var(--muted)]">
                    {t("web.empty.none")}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </Collapsible>
      </div>

      <Collapsible
        id="desk.exposures"
        title={t("web.section.exposures")}
        tip={t("tip.desk.exposures")}
        defaultOpen={false}
      >
        {exposures.length ? (
          <table className="data-table">
            <thead>
              <tr>
                <th>{t("web.key")}</th>
                <th>{t("web.value")}</th>
              </tr>
            </thead>
            <tbody>
              {exposures.map((row) => (
                <tr key={row.key}>
                  <td className="font-mono">{row.key}</td>
                  <td className="font-mono">{row.value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-sm text-[var(--muted)]">{t("web.empty.none")}</p>
        )}
      </Collapsible>

      <Collapsible
        id="desk.votes"
        title={t("web.section.votes")}
        tip={t("tip.desk.votes")}
        defaultOpen
        badge={
          <span className="text-[10px] text-[var(--muted)] font-mono">{members.length}</span>
        }
      >
        {members.length ? (
          <table className="data-table">
            <thead>
              <tr>
                <th>{t("web.strategy")}</th>
                <th>{t("web.stance")}</th>
                <th>{t("web.action")}</th>
                <th>{t("web.ticker")}</th>
                <th>{t("web.weight")}</th>
              </tr>
            </thead>
            <tbody>
              {members.map((m, i) => (
                <tr key={`${m.strategy_id}-${i}`}>
                  <td>{m.strategy_id || "—"}</td>
                  <td>{m.stance || "—"}</td>
                  <td>
                    <ActionPill action={m.action} />
                  </td>
                  <td>{m.ticker || "—"}</td>
                  <td>
                    {m.weight != null ? `${(m.weight * 100).toFixed(1)}%` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-sm text-[var(--muted)]">{t("web.empty.votes")}</p>
        )}
      </Collapsible>

      <Collapsible
        id="desk.tape"
        title={t("web.section.tape")}
        tip={t("tip.desk.tape")}
        defaultOpen={false}
      >
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("web.time")}</th>
              <th>{t("web.type")}</th>
              <th>{t("web.source")}</th>
              <th>{t("web.detail")}</th>
            </tr>
          </thead>
          <tbody>
            {tape.length ? (
              tape.slice(0, 50).map((r, i) => {
                const p = r.payload || {};
                const detail = [p.ticker, p.action, p.via, p.reason, p.message, p.close]
                  .filter(Boolean)
                  .join(" · ");
                return (
                  <tr key={`${r.t}-${r.type}-${i}`}>
                    <td>{fmtTime(r.t)}</td>
                    <td>{r.type}</td>
                    <td>{r.source || ""}</td>
                    <td>{detail || "—"}</td>
                  </tr>
                );
              })
            ) : (
              <tr>
                <td colSpan={4} className="text-[var(--muted)]">
                  {t("web.empty.quiet")}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Collapsible>
    </div>
  );
}
