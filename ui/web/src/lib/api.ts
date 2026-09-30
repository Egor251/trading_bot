import { isDesktop } from "@/lib/platform";

function normalizeBase(url: string): string {
  return url.replace(/\/$/, "").replace("://localhost", "://127.0.0.1");
}

/** BFF origin for desktop static builds (no Next rewrites). */
export function bffOrigin(): string {
  return normalizeBase(
    process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8787",
  );
}

/**
 * Resolve a BFF path.
 * - desktop: absolute URL to BFF
 * - web: `/backend/...` rewrite (Next) unless NEXT_PUBLIC_API_URL forces absolute
 */
export function apiUrl(path: string): string {
  const p = path.startsWith("/") ? path : `/${path}`;
  if (isDesktop()) {
    return `${bffOrigin()}${p}`;
  }
  const env = process.env.NEXT_PUBLIC_API_URL;
  if (env && process.env.NEXT_PUBLIC_USE_ABSOLUTE_API === "1") {
    return `${normalizeBase(env)}${p}`;
  }
  return `/backend${p}`;
}

export function wsUrl(): string {
  const env = process.env.NEXT_PUBLIC_WS_URL;
  if (env) return normalizeBase(env.replace(/^http/, "ws"));
  if (isDesktop()) {
    return bffOrigin().replace(/^http/, "ws") + "/ws";
  }
  const api = process.env.NEXT_PUBLIC_API_URL;
  if (api) {
    return normalizeBase(api).replace(/^http/, "ws") + "/ws";
  }
  if (typeof window !== "undefined") {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    return `${proto}://${window.location.host}/backend/ws`;
  }
  return "ws://127.0.0.1:8787/ws";
}

export async function apiFetch<T = unknown>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(apiUrl(path), {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

export type CmdResult = {
  ok?: boolean;
  cmd?: string;
  error?: string;
  [key: string]: unknown;
};

export async function sendCmd(
  cmd: string,
  payload: Record<string, unknown> = {},
  timeout = 30,
): Promise<CmdResult> {
  return apiFetch<CmdResult>("/api/cmd", {
    method: "POST",
    body: JSON.stringify({ cmd, payload, timeout }),
  });
}

export type Health = {
  ok: boolean;
  engine_connected: boolean;
  engine_error?: string;
  browser_clients?: number;
};

export async function fetchHealth(): Promise<Health> {
  return apiFetch<Health>("/api/health");
}
