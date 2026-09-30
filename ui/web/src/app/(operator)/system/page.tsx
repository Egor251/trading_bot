"use client";

import { useEffect, useState, type ReactNode } from "react";
import { HelpTip } from "@/components/help-tip";
import { EmptyState, PageHeader, StatusBadge } from "@/components/ui";
import { useStatus } from "@/hooks/useStatus";
import { useT } from "@/lib/i18n";
import { useSnapshot } from "@/lib/snapshot";
import type { PluginRow, StorageSnap } from "@/lib/types";

type Tab = "market" | "keys" | "risk" | "ai" | "storage" | "extensions";

function pctToFrac(v: string): number {
  return Math.max(0, (Number(v) || 0) / 100);
}
function optPct(v: string): number | null {
  const n = Number(v) || 0;
  return n <= 0 ? null : n / 100;
}
function optAbs(v: string): number | null {
  const n = Number(v) || 0;
  return n <= 0 ? null : n;
}

export default function SystemPage() {
  const { snap } = useSnapshot();
  const { status, setStatus, busy, run } = useStatus();
  const t = useT();
  const [tab, setTab] = useState<Tab>("market");

  const cfg = (snap?.config || {}) as Record<string, unknown>;
  const trading = (cfg.trading || {}) as Record<string, unknown>;
  const services = (cfg.services || trading.services || {}) as Record<string, Record<string, unknown>>;
  const ai = (snap?.ai || {}) as Record<string, unknown>;
  const storage = (snap?.storage || {}) as StorageSnap;
  const plugins = (snap?.plugins || []) as PluginRow[];
  const extensions = plugins.filter((p) => !p.is_strategy);
  const riskSrc = ((snap?.risk as Record<string, unknown>) ||
    (trading.risk as Record<string, unknown>) ||
    {}) as Record<string, unknown>;
  const limits = ((riskSrc.limits as Record<string, unknown>) || riskSrc) as Record<
    string,
    unknown
  >;

  // Market
  const [mode, setMode] = useState("paper");
  const [equity, setEquity] = useState("100000");
  const [logLevel, setLogLevel] = useState("INFO");
  const [mock, setMock] = useState(false);
  const [alpaca, setAlpaca] = useState(false);
  const [ccxt, setCcxt] = useState(false);
  const [ccxtEx, setCcxtEx] = useState("bitget");
  const [quik, setQuik] = useState(false);

  // Risk (% UI)
  const [mddPct, setMddPct] = useState("10");
  const [sddPct, setSddPct] = useState("15");
  const [mddAbs, setMddAbs] = useState("0");
  const [sddAbs, setSddAbs] = useState("0");
  const [posPct, setPosPct] = useState("10");
  const [lev, setLev] = useState("1");
  const [dailyLoss, setDailyLoss] = useState("0");
  const [turnover, setTurnover] = useState("0");
  const [ordersMin, setOrdersMin] = useState("0");
  const [concentration, setConcentration] = useState("0");
  const [margin, setMargin] = useState("0");
  const [dataAge, setDataAge] = useState("0");

  // Keys (Bitget / Alpaca) — blank = keep existing
  const [bitgetKey, setBitgetKey] = useState("");
  const [bitgetSecret, setBitgetSecret] = useState("");
  const [bitgetPass, setBitgetPass] = useState("");
  const [alpacaKey, setAlpacaKey] = useState("");
  const [alpacaSecret, setAlpacaSecret] = useState("");

  // AI
  const [aiEnabled, setAiEnabled] = useState(true);
  const [aiProvider, setAiProvider] = useState("mock");
  const [aiIters, setAiIters] = useState("5");

  // Storage
  const [defaultBackend, setDefaultBackend] = useState("sqlite");
  const [backendId, setBackendId] = useState("sqlite");
  const [backendEnabled, setBackendEnabled] = useState(true);
  const [backendPath, setBackendPath] = useState("");
  const [backendUrl, setBackendUrl] = useState("");

  useEffect(() => {
    if (trading.mode) setMode(String(trading.mode));
    else if (snap?.mode) setMode(String(snap.mode));
    if (trading.equity != null) setEquity(String(trading.equity));
    else if (snap?.equity != null) setEquity(String(snap.equity));
    const ll = (cfg.core as Record<string, unknown> | undefined)?.log_level;
    if (ll) setLogLevel(String(ll));
    setMock(!!(services.mock_market_service || {}).enabled);
    setAlpaca(!!(services.alpaca_market_service || {}).enabled);
    setCcxt(!!(services.ccxt_market_service || {}).enabled);
    setCcxtEx(String((services.ccxt_market_service || {}).exchange || "bitget"));
    setQuik(!!(services.quik_market_service || {}).enabled);
  }, [trading.mode, trading.equity, snap?.mode, snap?.equity, cfg.core, services]);

  useEffect(() => {
    const frac = (k: string, d: number) =>
      String(((Number(limits[k]) || d) * 100).toFixed(2));
    if (Object.keys(limits).length) {
      setMddPct(frac("max_portfolio_drawdown", 0.1));
      setSddPct(frac("max_strategy_drawdown", 0.15));
      setMddAbs(String(limits.max_portfolio_drawdown_abs ?? 0));
      setSddAbs(String(limits.max_strategy_drawdown_abs ?? 0));
      setPosPct(frac("max_position_pct", 0.1));
      setLev(String(limits.max_leverage ?? 1));
      setDailyLoss(
        limits.max_daily_loss_pct != null
          ? String((Number(limits.max_daily_loss_pct) * 100).toFixed(2))
          : "0",
      );
      setTurnover(
        limits.max_daily_turnover_pct != null
          ? String((Number(limits.max_daily_turnover_pct) * 100).toFixed(2))
          : "0",
      );
      setOrdersMin(String(limits.max_orders_per_minute ?? 0));
      setConcentration(
        limits.max_concentration_pct != null
          ? String((Number(limits.max_concentration_pct) * 100).toFixed(2))
          : "0",
      );
      setMargin(
        limits.max_margin_utilization != null
          ? String((Number(limits.max_margin_utilization) * 100).toFixed(2))
          : "0",
      );
      setDataAge(String(limits.max_data_age_seconds ?? 0));
    }
  }, [limits]);

  useEffect(() => {
    const ready = ai.ready;
    if (ready != null) setAiEnabled(!!ready || !!ai.enabled);
    const prov = String(ai.default_provider || ai.provider || "mock");
    setAiProvider(prov);
    if (ai.max_tool_iterations != null) setAiIters(String(ai.max_tool_iterations));
  }, [ai]);

  useEffect(() => {
    if (storage.default_backend) setDefaultBackend(String(storage.default_backend));
    const ids = storage.backend_ids || ["sqlite"];
    if (!ids.includes(backendId) && ids[0]) setBackendId(ids[0]);
    const b = (storage.backends || {})[backendId];
    if (b) {
      setBackendEnabled(b.enabled !== false);
      setBackendPath(String(b.path || ""));
      setBackendUrl(String(b.url || ""));
    }
  }, [storage, backendId]);

  if (!snap || snap.empty) {
    return (
      <div className="space-y-6">
        <PageHeader title={t("nav.system")} />
        <EmptyState title="Waiting for config snapshot" />
      </div>
    );
  }

  async function applyMarket() {
    const live = mode === "live" || mode === "quik";
    if (live && !confirm(`Confirm ${mode.toUpperCase()} mode?`)) return;
    await run("SET_TRADING", {
      mode,
      equity: Number(equity),
      log_level: logLevel,
      services: {
        mock_market_service: { enabled: mock },
        alpaca_market_service: { enabled: alpaca },
        ccxt_market_service: { enabled: ccxt, exchange: ccxtEx },
        quik_market_service: { enabled: quik },
      },
      persist: true,
      ...(live ? { confirm_live: true } : {}),
    });
  }

  async function applyKeys() {
    const secrets: Record<string, string> = {};
    if (bitgetKey.trim()) secrets.BITGET_API_KEY = bitgetKey.trim();
    if (bitgetSecret.trim()) secrets.BITGET_SECRET = bitgetSecret.trim();
    if (bitgetPass.trim()) secrets.BITGET_PASSWORD = bitgetPass.trim();
    if (alpacaKey.trim()) secrets.ALPACA_API_KEY = alpacaKey.trim();
    if (alpacaSecret.trim()) secrets.ALPACA_SECRET_KEY = alpacaSecret.trim();
    if (!Object.keys(secrets).length) {
      setStatus(t("keys.hint_paste"));
      return;
    }
    await run("SET_SECRETS", { secrets });
    setBitgetKey("");
    setBitgetSecret("");
    setBitgetPass("");
    setAlpacaKey("");
    setAlpacaSecret("");
  }

  async function applyRisk() {
    const orders = Number(ordersMin) || 0;
    const age = Number(dataAge) || 0;
    await run("SET_TRADING", {
      risk: {
        max_portfolio_drawdown: pctToFrac(mddPct),
        max_strategy_drawdown: pctToFrac(sddPct),
        max_portfolio_drawdown_abs: optAbs(mddAbs),
        max_strategy_drawdown_abs: optAbs(sddAbs),
        max_position_pct: pctToFrac(posPct),
        max_leverage: Number(lev) || 1,
        max_daily_loss_pct: optPct(dailyLoss),
        max_daily_turnover_pct: optPct(turnover),
        max_orders_per_minute: orders <= 0 ? null : orders,
        max_concentration_pct: optPct(concentration),
        max_margin_utilization: optPct(margin),
        max_data_age_seconds: age <= 0 ? null : age,
      },
      persist: true,
    });
  }

  async function applyAi() {
    await run("SET_AI", {
      enabled: aiEnabled,
      default_provider: aiProvider,
      max_tool_iterations: Number(aiIters) || 5,
      persist: true,
    });
  }

  async function applyStorageRouting() {
    await run("SET_STORAGE", {
      action: "reroute",
      default_backend: defaultBackend,
      persist: true,
    });
  }

  async function applyStorageBackend() {
    await run("SET_STORAGE", {
      action: "backend",
      backend_id: backendId,
      enabled: backendEnabled,
      path: backendPath || undefined,
      url: backendUrl || undefined,
      persist: true,
    });
  }

  const secretsPresent = ((snap?.secrets as { present?: Record<string, boolean> } | undefined)
    ?.present || {}) as Record<string, boolean>;
  const flag = (name: string) => (secretsPresent[name] ? "set" : "missing");

  const tabs: { id: Tab; label: string }[] = [
    { id: "market", label: t("sys.tab.market") },
    { id: "keys", label: t("sys.tab.keys") },
    { id: "risk", label: t("sys.tab.risk") },
    { id: "ai", label: t("sys.tab.ai") },
    { id: "storage", label: t("sys.tab.storage") },
    { id: "extensions", label: t("sys.tab.extensions") },
  ];

  const providers = (Array.isArray(ai.providers)
    ? ai.providers
    : Object.keys((ai.profiles as Record<string, unknown>) || {})) as string[];
  const backendIds = storage.backend_ids || ["sqlite", "postgres", "memory"];

  return (
    <div className="space-y-6 fade-in">
      <PageHeader
        title={t("nav.system")}
        description={t("sys.page_desc")}
        actions={
          <button
            type="button"
            className="btn-ghost"
            disabled={busy}
            onClick={() => void run("SAVE_CONFIG")}
          >
            SAVE_CONFIG
          </button>
        }
      />
      <div className="flex flex-wrap gap-2">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            className={tab === t.id ? "btn-primary" : "btn-ghost"}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      {status ? <p className="text-sm text-[var(--muted)]">{status}</p> : null}

      {tab === "market" ? (
        <section className="panel p-5 space-y-4">
          {(mode === "live" || mode === "quik") && (
            <p className="text-sm text-[var(--danger)]">
              LIVE warn: applying {mode.toUpperCase()} requires confirmation.
            </p>
          )}
          <div className="grid gap-3 sm:grid-cols-2 max-w-2xl">
            <Field label="Mode">
              <select className="input" value={mode} onChange={(e) => setMode(e.target.value)}>
                <option value="paper">paper</option>
                <option value="quik">quik</option>
                <option value="live">live</option>
              </select>
            </Field>
            <Field label="Equity">
              <input className="input" value={equity} onChange={(e) => setEquity(e.target.value)} />
            </Field>
            <Field label="Log level">
              <select
                className="input"
                value={logLevel}
                onChange={(e) => setLogLevel(e.target.value)}
              >
                {["DEBUG", "INFO", "WARNING", "ERROR"].map((l) => (
                  <option key={l} value={l}>
                    {l}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="CCXT exchange">
              <select
                className="input"
                value={ccxtEx}
                onChange={(e) => setCcxtEx(e.target.value)}
              >
                <option value="bitget">bitget</option>
                <option value="binance">binance</option>
                <option value="bybit">bybit</option>
                <option value="okx">okx</option>
                <option value="kraken">kraken</option>
              </select>
            </Field>
          </div>
          <div className="flex flex-wrap gap-4 text-sm">
            <Check label="mock" checked={mock} onChange={setMock} />
            <Check label="alpaca" checked={alpaca} onChange={setAlpaca} />
            <Check label="ccxt" checked={ccxt} onChange={setCcxt} />
            <Check label="quik" checked={quik} onChange={setQuik} />
          </div>
          <button
            type="button"
            className="btn-primary"
            disabled={busy}
            onClick={() => void applyMarket()}
          >
            Apply SET_TRADING
          </button>
          <p className="text-xs text-[var(--muted)]">{t("sys.market_keys_hint")}</p>
        </section>
      ) : null}

      {tab === "keys" ? (
        <section className="panel p-5 space-y-6 max-w-xl">
          <p className="text-sm text-[var(--muted)]">{t("keys.intro")}</p>

          <div className="space-y-3">
            <h3 className="text-sm font-medium">{t("keys.bitget_section")}</h3>
            <p className="text-xs text-[var(--muted)]">{t("keys.bitget_help")}</p>
            <div className="flex flex-wrap gap-2 text-xs">
              <StatusBadge status={`API ${flag("BITGET_API_KEY")}`} />
              <StatusBadge status={`Secret ${flag("BITGET_SECRET")}`} />
              <StatusBadge status={`Passphrase ${flag("BITGET_PASSWORD")}`} />
            </div>
            <Field label={`BITGET_API_KEY (${flag("BITGET_API_KEY")})`}>
              <input
                className="input font-mono"
                type="password"
                autoComplete="off"
                placeholder={t("keys.hint_paste")}
                value={bitgetKey}
                onChange={(e) => setBitgetKey(e.target.value)}
              />
            </Field>
            <Field label={`BITGET_SECRET (${flag("BITGET_SECRET")})`}>
              <input
                className="input font-mono"
                type="password"
                autoComplete="off"
                placeholder={t("keys.hint_paste")}
                value={bitgetSecret}
                onChange={(e) => setBitgetSecret(e.target.value)}
              />
            </Field>
            <Field label={`BITGET_PASSWORD / passphrase (${flag("BITGET_PASSWORD")})`}>
              <input
                className="input font-mono"
                type="password"
                autoComplete="off"
                placeholder={t("keys.hint_paste")}
                value={bitgetPass}
                onChange={(e) => setBitgetPass(e.target.value)}
              />
            </Field>
          </div>

          <hr className="border-[var(--panel-border)]" />

          <div className="space-y-3">
            <h3 className="text-sm font-medium">{t("keys.alpaca_section")}</h3>
            <p className="text-xs text-[var(--muted)]">{t("keys.alpaca_help")}</p>
            <div className="flex flex-wrap gap-2 text-xs">
              <StatusBadge status={`Key ${flag("ALPACA_API_KEY")}`} />
              <StatusBadge status={`Secret ${flag("ALPACA_SECRET_KEY")}`} />
            </div>
            <Field label={`ALPACA_API_KEY (${flag("ALPACA_API_KEY")})`}>
              <input
                className="input font-mono"
                type="password"
                autoComplete="off"
                placeholder={t("keys.hint_paste")}
                value={alpacaKey}
                onChange={(e) => setAlpacaKey(e.target.value)}
              />
            </Field>
            <Field label={`ALPACA_SECRET_KEY (${flag("ALPACA_SECRET_KEY")})`}>
              <input
                className="input font-mono"
                type="password"
                autoComplete="off"
                placeholder={t("keys.hint_paste")}
                value={alpacaSecret}
                onChange={(e) => setAlpacaSecret(e.target.value)}
              />
            </Field>
          </div>

          <button
            type="button"
            className="btn-primary"
            disabled={
              busy ||
              !(
                bitgetKey.trim() ||
                bitgetSecret.trim() ||
                bitgetPass.trim() ||
                alpacaKey.trim() ||
                alpacaSecret.trim()
              )
            }
            onClick={() => void applyKeys()}
          >
            {t("keys.apply")}
          </button>
          <p className="text-xs text-[var(--muted)]">{t("keys.persist_hint")}</p>
        </section>
      ) : null}

      {tab === "risk" ? (
        <section className="panel p-5 space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 max-w-4xl">
            <Field label="Max portfolio DD %">
              <input className="input" value={mddPct} onChange={(e) => setMddPct(e.target.value)} />
            </Field>
            <Field label="Max strategy DD %">
              <input className="input" value={sddPct} onChange={(e) => setSddPct(e.target.value)} />
            </Field>
            <Field label="Max pos %">
              <input className="input" value={posPct} onChange={(e) => setPosPct(e.target.value)} />
            </Field>
            <Field label="Max leverage">
              <input className="input" value={lev} onChange={(e) => setLev(e.target.value)} />
            </Field>
            <Field label="Portfolio DD abs (0=off)">
              <input className="input" value={mddAbs} onChange={(e) => setMddAbs(e.target.value)} />
            </Field>
            <Field label="Strategy DD abs (0=off)">
              <input className="input" value={sddAbs} onChange={(e) => setSddAbs(e.target.value)} />
            </Field>
            <Field label="Daily loss % (0=off)">
              <input
                className="input"
                value={dailyLoss}
                onChange={(e) => setDailyLoss(e.target.value)}
              />
            </Field>
            <Field label="Daily turnover % (0=off)">
              <input
                className="input"
                value={turnover}
                onChange={(e) => setTurnover(e.target.value)}
              />
            </Field>
            <Field label="Orders / min (0=off)">
              <input
                className="input"
                value={ordersMin}
                onChange={(e) => setOrdersMin(e.target.value)}
              />
            </Field>
            <Field label="Concentration % (0=off)">
              <input
                className="input"
                value={concentration}
                onChange={(e) => setConcentration(e.target.value)}
              />
            </Field>
            <Field label="Margin util % (0=off)">
              <input className="input" value={margin} onChange={(e) => setMargin(e.target.value)} />
            </Field>
            <Field label="Max data age sec (0=off)">
              <input
                className="input"
                value={dataAge}
                onChange={(e) => setDataAge(e.target.value)}
              />
            </Field>
          </div>
          <button
            type="button"
            className="btn-primary"
            disabled={busy}
            onClick={() => void applyRisk()}
          >
            Apply risk
          </button>
        </section>
      ) : null}

      {tab === "ai" ? (
        <section className="panel p-5 space-y-4 max-w-xl">
          <div className="flex gap-3 items-center">
            <span className="text-[var(--muted)] text-sm">ready</span>
            <StatusBadge status={ai.ready ? "ready" : "not ready"} />
          </div>
          <Check label="enabled" checked={aiEnabled} onChange={setAiEnabled} />
          <Field label="Default provider">
            <select
              className="input"
              value={aiProvider}
              onChange={(e) => setAiProvider(e.target.value)}
            >
              {(providers.length ? providers : ["mock"]).map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Max tool iterations">
            <input className="input" value={aiIters} onChange={(e) => setAiIters(e.target.value)} />
          </Field>
          <button
            type="button"
            className="btn-primary"
            disabled={busy}
            onClick={() => void applyAi()}
          >
            Apply SET_AI
          </button>
        </section>
      ) : null}

      {tab === "storage" ? (
        <section className="panel p-5 space-y-4 max-w-2xl">
          <Field label="Default backend">
            <select
              className="input"
              value={defaultBackend}
              onChange={(e) => setDefaultBackend(e.target.value)}
            >
              {backendIds.map((b) => (
                <option key={b} value={b}>
                  {b}
                </option>
              ))}
            </select>
          </Field>
          <button
            type="button"
            className="btn-primary"
            disabled={busy}
            onClick={() => void applyStorageRouting()}
          >
            Apply routing
          </button>
          <hr className="border-[var(--panel-border)]" />
          <Field label="Configure backend">
            <select
              className="input"
              value={backendId}
              onChange={(e) => setBackendId(e.target.value)}
            >
              {backendIds.map((b) => (
                <option key={b} value={b}>
                  {b}
                </option>
              ))}
            </select>
          </Field>
          <Check label="enabled" checked={backendEnabled} onChange={setBackendEnabled} />
          <Field label="Path">
            <input
              className="input"
              value={backendPath}
              onChange={(e) => setBackendPath(e.target.value)}
            />
          </Field>
          <Field label="URL">
            <input
              className="input"
              value={backendUrl}
              onChange={(e) => setBackendUrl(e.target.value)}
            />
          </Field>
          <div className="flex gap-2">
            <button
              type="button"
              className="btn-primary"
              disabled={busy}
              onClick={() => void applyStorageBackend()}
            >
              Apply backend
            </button>
            <button
              type="button"
              className="btn-ghost"
              disabled={busy}
              onClick={() => void run("SET_STORAGE", { action: "reload" })}
            >
              Reload
            </button>
          </div>
        </section>
      ) : null}

      {tab === "extensions" ? (
        <section className="panel p-5">
          {extensions.length ? (
            <table className="data-table">
              <thead>
                <tr>
                  <th>id</th>
                  <th>kind</th>
                  <th>enabled</th>
                  <th>running</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {extensions.map((p) => {
                  const id = String(p.id || "");
                  return (
                    <tr key={id}>
                      <td className="font-mono">{id}</td>
                      <td>{p.kind || "—"}</td>
                      <td>
                        <StatusBadge status={p.enabled ? "enabled" : "disabled"} />
                      </td>
                      <td>
                        <StatusBadge status={p.running ? "running" : "stopped"} />
                      </td>
                      <td className="space-x-2">
                        <button
                          type="button"
                          className="btn-ghost text-xs"
                          disabled={busy}
                          onClick={() =>
                            void run("SET_PLUGIN", {
                              plugin_id: id,
                              enabled: true,
                              action: "start",
                              persist: true,
                            })
                          }
                        >
                          Start
                        </button>
                        <button
                          type="button"
                          className="btn-ghost text-xs"
                          disabled={busy}
                          onClick={() =>
                            void run("SET_PLUGIN", {
                              plugin_id: id,
                              enabled: false,
                              action: "stop",
                              persist: true,
                            })
                          }
                        >
                          Stop
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : (
            <p className="text-sm text-[var(--muted)]">no non-strategy plugins</p>
          )}
        </section>
      ) : null}
    </div>
  );
}

function Field({
  label,
  tip,
  children,
}: {
  label: string;
  tip?: string;
  children: ReactNode;
}) {
  return (
    <label className="space-y-1 text-sm block">
      <span className="inline-flex items-center gap-1.5">
        <span className="text-[var(--muted)]">{label}</span>
        {tip ? <HelpTip text={tip} /> : null}
      </span>
      {children}
    </label>
  );
}

function Check({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="inline-flex items-center gap-2 text-sm cursor-pointer">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      {label}
    </label>
  );
}
