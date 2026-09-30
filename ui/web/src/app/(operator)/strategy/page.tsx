"use client";

import { useEffect, useMemo, useState } from "react";
import { EmptyState, PageHeader, StatusBadge } from "@/components/ui";
import { Collapsible } from "@/components/collapsible";
import { useStatus } from "@/hooks/useStatus";
import { useT } from "@/lib/i18n";
import { useSnapshot } from "@/lib/snapshot";
import type { PluginRow } from "@/lib/types";

type Role = "live" | "observe" | "off";

export default function StrategyPage() {
  const { snap } = useSnapshot();
  const { status, busy, run } = useStatus();
  const t = useT();
  const plugins = (snap?.plugins || []) as PluginRow[];
  const strategies = plugins.filter(
    (p) => p.is_strategy || String(p.kind || "").includes("strategy"),
  );
  const rows = strategies.length ? strategies : plugins;

  const [roles, setRoles] = useState<Record<string, Role>>({});
  const [paramId, setParamId] = useState("");
  const [paramJson, setParamJson] = useState('{\n  "fast": 10,\n  "slow": 30\n}');

  const liveSet = useMemo(
    () => new Set(snap?.ensemble?.strategies || []),
    [snap?.ensemble?.strategies],
  );
  const observeSet = useMemo(
    () => new Set(snap?.ensemble?.observe || []),
    [snap?.ensemble?.observe],
  );

  useEffect(() => {
    const list = (snap?.plugins || []) as PluginRow[];
    const strat = list.filter(
      (p) => p.is_strategy || String(p.kind || "").includes("strategy"),
    );
    const src = strat.length ? strat : list;
    const next: Record<string, Role> = {};
    for (const p of src) {
      const id = String(p.id || "");
      if (!id) continue;
      if (liveSet.has(id)) next[id] = "live";
      else if (observeSet.has(id)) next[id] = "observe";
      else next[id] = "off";
    }
    setRoles(next);
    setParamId((cur) => cur || String(src[0]?.id || ""));
  }, [snap?.ensemble?.strategies, snap?.ensemble?.observe, snap?.plugins, liveSet, observeSet]);

  if (!snap || snap.empty) {
    return (
      <div className="space-y-6">
        <PageHeader title={t("nav.strategy")} />
        <EmptyState title={t("web.waiting")} />
      </div>
    );
  }

  async function applyLineup() {
    const live: string[] = [];
    const observe: string[] = [];
    const off: string[] = [];
    for (const [id, role] of Object.entries(roles)) {
      if (role === "live") live.push(id);
      else if (role === "observe") observe.push(id);
      else off.push(id);
    }
    await run("SET_ENSEMBLE", {
      strategies: live,
      observe,
      off,
      persist: true,
    });
  }

  async function setPlugin(id: string, enabled: boolean) {
    await run("SET_PLUGIN", {
      plugin_id: id,
      enabled,
      action: enabled ? "start" : "stop",
      persist: true,
    });
  }

  async function applyParams() {
    let params: Record<string, unknown>;
    try {
      params = JSON.parse(paramJson) as Record<string, unknown>;
    } catch (e) {
      return;
    }
    if (!paramId) return;
    await run("SET_STRATEGY_PARAMS", {
      plugin_id: paramId,
      params,
      persist: true,
    });
  }

  return (
    <div className="space-y-6 fade-in">
      <PageHeader
        title={t("nav.strategy")}
        description="Lineup (live / observe / off) · plugin lifecycle · params"
        actions={
          <button
            type="button"
            className="btn-primary"
            disabled={busy}
            onClick={() => void applyLineup()}
          >
            Apply lineup
          </button>
        }
      />
      {status ? <p className="text-sm text-[var(--muted)]">{status}</p> : null}

      <Collapsible id="strat.lineup" title="Lineup" defaultOpen>
        {rows.length ? (
          <table className="data-table">
            <thead>
              <tr>
                <th>id</th>
                <th>kind</th>
                <th>enabled</th>
                <th>running</th>
                <th>lineup</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => {
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
                    <td>
                      <select
                        className="input w-auto text-xs"
                        value={roles[id] || "off"}
                        onChange={(e) =>
                          setRoles((prev) => ({
                            ...prev,
                            [id]: e.target.value as Role,
                          }))
                        }
                      >
                        <option value="live">live</option>
                        <option value="observe">observe</option>
                        <option value="off">off</option>
                      </select>
                    </td>
                    <td className="space-x-2">
                      <button
                        type="button"
                        className="btn-ghost text-xs"
                        disabled={busy}
                        onClick={() => void setPlugin(id, true)}
                      >
                        On
                      </button>
                      <button
                        type="button"
                        className="btn-ghost text-xs"
                        disabled={busy}
                        onClick={() => void setPlugin(id, false)}
                      >
                        Off
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          <p className="text-sm text-[var(--muted)]">no plugins</p>
        )}
      </Collapsible>

      <Collapsible id="strat.params" title="SET_STRATEGY_PARAMS" defaultOpen={false}>
        <h3 className="text-xs uppercase tracking-wide text-[var(--muted)] mb-3">
          Params JSON
        </h3>
        <label className="block max-w-md space-y-1 text-sm">
          <span className="text-[var(--muted)]">Plugin</span>
          <select
            className="input"
            value={paramId}
            onChange={(e) => setParamId(e.target.value)}
          >
            {rows.map((p) => (
              <option key={String(p.id)} value={String(p.id)}>
                {String(p.id)}
              </option>
            ))}
          </select>
        </label>
        <textarea
          className="input font-mono text-xs min-h-36"
          value={paramJson}
          onChange={(e) => setParamJson(e.target.value)}
        />
        <button
          type="button"
          className="btn-primary"
          disabled={busy}
          onClick={() => void applyParams()}
        >
          Apply params
        </button>
      </Collapsible>
    </div>
  );
}
