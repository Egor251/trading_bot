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

/**
 * WebSocket URL, with the operator token as a query parameter.
 *
 * The browser cannot set custom headers on a WebSocket handshake, so the token
 * travels in the query string. The BFF still enforces an exact `Origin`
 * allow-list, and the URL is not logged server-side.
 */
export async function wsUrl(): Promise<string> {
  const base = await resolveWsBase();
  const token = await loadToken();
  if (!token) return base;
  const sep = base.includes("?") ? "&" : "?";
  return `${base}${sep}operator_token=${encodeURIComponent(token)}`;
}

function resolveWsBase(): string {
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

/**
 * BFF session token.
 *
 * The BFF authenticates mutating calls with `X-Operator-Token`. The token is
 * fetched once from `/api/session`, which the BFF gates on an exact
 * `Origin` allow-list — so a hostile page cannot bootstrap one, while the
 * Operator app bootstraps itself. A custom header also makes every call
 * non-simple, so a cross-site form post is refused by CORS preflight.
 */
let cachedToken: string | null = null;
let tokenPromise: Promise<string> | null = null;

async function loadToken(): Promise<string> {
  if (cachedToken) return cachedToken;
  if (!tokenPromise) {
    tokenPromise = fetch(apiUrl("/api/session"), {
      headers: { "Content-Type": "application/json" },
    })
      .then(async (res) => {
        if (!res.ok) throw new Error(await res.text().catch(() => `HTTP ${res.status}`));
        const body = (await res.json()) as { token?: string };
        cachedToken = body.token || "";
        return cachedToken;
      })
      .finally(() => {
        tokenPromise = null;
      });
  }
  return tokenPromise;
}

export function resetToken(): void {
  cachedToken = null;
  tokenPromise = null;
}

export async function apiFetch<T = unknown>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const token = await loadToken();
  const res = await fetch(apiUrl(path), {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { "X-Operator-Token": token } : {}),
      ...(init?.headers || {}),
    },
  });
  if (res.status === 401) {
    // Token rotated (engine restart) — drop it so the next call re-bootstraps.
    resetToken();
  }
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
