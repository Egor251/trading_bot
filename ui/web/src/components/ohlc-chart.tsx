"use client";

import { useEffect, useRef } from "react";
import type { OhlcBar } from "@/lib/types";

export function OhlcChart({
  bars,
  height = 360,
}: {
  bars: OhlcBar[];
  height?: number;
}) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let chart: { remove: () => void } | null = null;
    let cancelled = false;

    async function mount() {
      if (!ref.current || !bars.length) return;
      const mod = await import("lightweight-charts");
      if (cancelled || !ref.current) return;

      const createChart = mod.createChart;
      const c = createChart(ref.current, {
        height,
        layout: {
          background: { color: "transparent" },
          textColor: "#8a97a8",
        },
        grid: {
          vertLines: { color: "#24304055" },
          horzLines: { color: "#24304055" },
        },
        rightPriceScale: { borderColor: "#243040" },
        timeScale: { borderColor: "#243040" },
      });

      const opts = {
        upColor: "#3dba8b",
        downColor: "#e23d3d",
        borderVisible: false,
        wickUpColor: "#3dba8b",
        wickDownColor: "#e23d3d",
      };

      // v5: addSeries(CandlestickSeries); v4: addCandlestickSeries()
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const anyChart = c as any;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const CandlestickSeries = (mod as any).CandlestickSeries;
      const series = CandlestickSeries
        ? anyChart.addSeries(CandlestickSeries, opts)
        : anyChart.addCandlestickSeries(opts);

      const data = bars
        .filter((b) => b.t && b.o != null && b.h != null && b.l != null && b.c != null)
        .map((b) => ({
          time: Math.floor(Number(b.t)),
          open: Number(b.o),
          high: Number(b.h),
          low: Number(b.l),
          close: Number(b.c),
        }));
      series.setData(data);
      c.timeScale().fitContent();
      chart = c;
    }

    void mount();
    return () => {
      cancelled = true;
      chart?.remove();
    };
  }, [bars, height]);

  if (!bars.length) {
    return <p className="text-sm text-[var(--muted)]">no OHLC for symbol</p>;
  }
  return <div ref={ref} className="w-full" />;
}
