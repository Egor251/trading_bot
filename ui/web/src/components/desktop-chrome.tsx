"use client";

import { useEffect, useRef } from "react";
import { isDesktop, isTauriRuntime } from "@/lib/platform";
import type { Snapshot } from "@/lib/types";

type Props = {
  snap: Snapshot | null;
  halted: boolean;
};

/**
 * Desktop-only chrome: watch snapshot for halt/resume and fire OS notifications
 * via Tauri plugin when running inside the webview.
 */
export function DesktopChrome({ snap, halted }: Props) {
  const prevHalted = useRef<boolean | null>(null);
  const seen = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (!isDesktop() || !isTauriRuntime()) return;

    let cancelled = false;
    (async () => {
      try {
        const { getCurrentWindow } = await import("@tauri-apps/api/window");
        const win = getCurrentWindow();
        await win.setTitle("trading_bot Operator");
      } catch {
        /* not in Tauri */
      }
    })();

    return () => {
      cancelled = true;
      void cancelled;
    };
  }, []);

  useEffect(() => {
    if (!isDesktop() || !isTauriRuntime()) return;
    if (prevHalted.current === null) {
      prevHalted.current = halted;
      return;
    }
    if (prevHalted.current === halted) return;
    prevHalted.current = halted;
    void notify(
      halted ? "Trading halted" : "Trading resumed",
      halted ? "Kill switch is active" : "Engine accepted RESUME",
    );
  }, [halted]);

  useEffect(() => {
    if (!isDesktop() || !isTauriRuntime() || !snap) return;
    const items = (snap as { recent_notify?: unknown }).recent_notify;
    if (!Array.isArray(items)) return;
    for (const raw of items.slice(-8)) {
      if (!raw || typeof raw !== "object") continue;
      const item = raw as Record<string, unknown>;
      const key = `${item.t}|${item.type}|${JSON.stringify(item.payload || {})}`;
      if (seen.current.has(key)) continue;
      seen.current.add(key);
      if (seen.current.size > 200) {
        seen.current = new Set(Array.from(seen.current).slice(-100));
      }
      const et = String(item.type || "");
      if (et === "execution.result" || et === "risk.reject" || et === "system.halted") {
        const payload =
          item.payload && typeof item.payload === "object"
            ? (item.payload as Record<string, unknown>)
            : {};
        const title = et === "system.halted" ? "System halted" : et;
        const body = String(
          payload.ticker || payload.reason || payload.action || et,
        );
        void notify(title, body);
      }
    }
  }, [snap]);

  return null;
}

async function notify(title: string, body: string): Promise<void> {
  try {
    const { isPermissionGranted, requestPermission, sendNotification } =
      await import("@tauri-apps/plugin-notification");
    let granted = await isPermissionGranted();
    if (!granted) {
      const perm = await requestPermission();
      granted = perm === "granted";
    }
    if (granted) {
      sendNotification({ title, body });
    }
  } catch {
    /* plugin unavailable */
  }
}
