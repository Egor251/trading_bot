"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { fetchHealth, wsUrl, type Health } from "@/lib/api";
import type { Snapshot } from "@/lib/types";

type SnapshotCtx = {
  snap: Snapshot | null;
  health: Health | null;
  wsConnected: boolean;
  lastError: string;
};

const Ctx = createContext<SnapshotCtx>({
  snap: null,
  health: null,
  wsConnected: false,
  lastError: "",
});

export function SnapshotProvider({ children }: { children: ReactNode }) {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [wsConnected, setWsConnected] = useState(false);
  const [lastError, setLastError] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const connect = useCallback(() => {
    const url = wsUrl();
    if (!url) return;
    let closed = false;
    const ws = new WebSocket(url);
    ws.onopen = () => {
      setWsConnected(true);
      setLastError("");
    };
    ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data) as Snapshot;
        setSnap(data);
      } catch {
        /* ignore */
      }
    };
    ws.onerror = () => setLastError("ws error");
    ws.onclose = () => {
      setWsConnected(false);
      if (!closed) {
        timer.current = setTimeout(connect, 1200);
      }
    };
    return () => {
      closed = true;
      ws.close();
    };
  }, []);

  useEffect(() => {
    const cleanup = connect();
    return () => {
      if (timer.current) clearTimeout(timer.current);
      cleanup?.();
    };
  }, [connect]);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const h = await fetchHealth();
        if (alive) setHealth(h);
      } catch {
        if (alive) {
          setHealth({ ok: false, engine_connected: false, engine_error: "bff down" });
        }
      }
    };
    tick();
    const id = setInterval(tick, 2000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const value = useMemo(
    () => ({ snap, health, wsConnected, lastError }),
    [snap, health, wsConnected, lastError],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSnapshot() {
  return useContext(Ctx);
}
