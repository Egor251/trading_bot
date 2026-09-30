"use client";

import { useMemo, useState } from "react";
import { Collapsible } from "@/components/collapsible";
import { EquityChart } from "@/components/equity-chart";
import { OhlcChart } from "@/components/ohlc-chart";
import { EmptyState, PageHeader } from "@/components/ui";
import { useT } from "@/lib/i18n";
import { ohlcMapFromSnap } from "@/lib/ohlc";
import { useSnapshot } from "@/lib/snapshot";

export default function ChartsPage() {
  const { snap } = useSnapshot();
  const t = useT();
  const ohlcMap = useMemo(
    () => ohlcMapFromSnap(snap?.candles as { ohlc?: Record<string, unknown> }),
    [snap?.candles],
  );
  const symbols = useMemo(() => {
    const fromOhlc = Object.keys(ohlcMap);
    const fromBook = snap?.active_keys || [];
    return Array.from(new Set([...fromOhlc, ...fromBook])).sort();
  }, [ohlcMap, snap?.active_keys]);
  const [symbol, setSymbol] = useState("");
  const [manual, setManual] = useState("");
  const active = symbol || symbols[0] || "";
  const bars = active ? ohlcMap[active] || [] : [];
  const equity = Array.isArray(snap?.equity_curve)
    ? (snap!.equity_curve as number[])
    : [];
  const barCount =
    bars.length ||
    Number(
      (snap?.candles as { bar_counts?: Record<string, number> } | undefined)
        ?.bar_counts?.[active] || 0,
    );

  if (!snap || snap.empty) {
    return (
      <div className="space-y-6">
        <PageHeader title={t("nav.charts")} />
        <EmptyState title={t("web.waiting")} />
      </div>
    );
  }

  return (
    <div className="space-y-4 fade-in">
      <PageHeader
        title={t("nav.charts")}
        description={t("chart.title")}
        actions={
          <div className="flex flex-wrap gap-2 items-center">
            <select
              className="input w-auto"
              value={active}
              onChange={(e) => setSymbol(e.target.value)}
            >
              {symbols.length ? (
                symbols.map((s) => {
                  const n =
                    ohlcMap[s]?.length ||
                    Number(
                      (
                        snap?.candles as
                          | { bar_counts?: Record<string, number> }
                          | undefined
                      )?.bar_counts?.[s] || 0,
                    );
                  return (
                    <option key={s} value={s}>
                      {s}
                      {n ? ` (${n})` : ""}
                    </option>
                  );
                })
              ) : (
                <option value="">no symbols</option>
              )}
            </select>
            <input
              className="input w-36"
              placeholder="symbol…"
              value={manual}
              onChange={(e) => setManual(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && manual.trim()) {
                  setSymbol(manual.trim());
                  setManual("");
                }
              }}
            />
          </div>
        }
      />
      <Collapsible
        id="charts.ohlc"
        title={`${t("win.charts_candles")} · ${active || "—"}`}
        defaultOpen
      >
        {bars.length ? (
          <OhlcChart bars={bars} />
        ) : (
          <p className="text-sm text-[var(--muted)] py-16 text-center">
            {barCount
              ? t("chart.no_bars")
              : `no bars for ${active || "symbol"}`}
          </p>
        )}
      </Collapsible>
      <Collapsible id="charts.equity" title={t("win.charts_equity")} defaultOpen={false}>
        <EquityChart values={equity} />
      </Collapsible>
    </div>
  );
}
