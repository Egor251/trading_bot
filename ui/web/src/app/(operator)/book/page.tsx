"use client";

import { useEffect, useMemo, useState } from "react";
import { Collapsible } from "@/components/collapsible";
import { FieldLabel, WithHelp } from "@/components/help-tip";
import { EmptyState, PageHeader } from "@/components/ui";
import { useStatus } from "@/hooks/useStatus";
import { useT } from "@/lib/i18n";
import { useSnapshot } from "@/lib/snapshot";

type CatalogItem = {
  key?: string;
  ticker?: string;
  exchange?: string;
  symbol?: string;
  [k: string]: unknown;
};

function itemKey(it: CatalogItem): string {
  return String(it.key || it.symbol || it.ticker || "").trim();
}

export default function BookPage() {
  const { snap } = useSnapshot();
  const { status, busy, run } = useStatus();
  const t = useT();
  const catalog = useMemo(() => {
    const c = snap?.catalog;
    if (Array.isArray(c)) return c as CatalogItem[];
    if (c && typeof c === "object" && Array.isArray((c as { items?: unknown }).items)) {
      return (c as { items: CatalogItem[] }).items;
    }
    return [] as CatalogItem[];
  }, [snap]);

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [mode, setMode] = useState("manual");
  const [profile, setProfile] = useState("");
  const [manualKey, setManualKey] = useState("");

  useEffect(() => {
    setSelected(new Set(snap?.active_keys || []));
  }, [snap?.active_keys]);

  useEffect(() => {
    const u = snap?.universe;
    if (u?.mode) {
      const m = String(u.mode).toLowerCase();
      // Normalize legacy aliases if snapshot still has them
      if (m === "static") setMode("manual");
      else if (m === "screen") setMode("screener");
      else setMode(m);
    }
    if (u?.profile != null) setProfile(String(u.profile));
  }, [snap?.universe?.mode, snap?.universe?.profile]);

  if (!snap || snap.empty) {
    return (
      <div className="space-y-6">
        <PageHeader title={t("nav.book")} tip={t("tip.nav.book")} />
        <EmptyState title={t("web.waiting")} hint={t("web.engine_hint")} />
      </div>
    );
  }

  function toggle(key: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function addManual() {
    const k = manualKey.trim().toUpperCase();
    if (!k) return;
    setSelected((prev) => new Set(prev).add(k));
    setManualKey("");
  }

  async function apply() {
    await run("SET_UNIVERSE", {
      static_active: Array.from(selected),
      mode,
      profile: profile || undefined,
      persist: true,
    });
  }

  return (
    <div className="space-y-4 fade-in">
      <PageHeader
        title={t("nav.book")}
        description={t("book.subtitle")}
        tip={t("tip.nav.book")}
        actions={
          <WithHelp tip={t("tip.book.apply")}>
            <button
              type="button"
              className="btn-primary"
              disabled={busy}
              title={t("tip.book.apply")}
              onClick={() => void apply()}
            >
              {t("book.apply")}
            </button>
          </WithHelp>
        }
      />
      <p className="text-sm text-[var(--muted)] max-w-3xl">{t("book.howto")}</p>
      {status ? <p className="text-sm text-[var(--muted)]">{status}</p> : null}

      <Collapsible
        id="book.controls"
        title={t("book.mode")}
        tip={t("tip.book.mode")}
        defaultOpen
      >
        <div className="grid gap-3 sm:grid-cols-3 max-w-3xl">
          <label className="space-y-1 text-sm block">
            <FieldLabel tip={t("tip.book.mode_select")}>{t("book.mode")}</FieldLabel>
            <select
              className="input"
              value={mode}
              title={t("tip.book.mode_select")}
              onChange={(e) => setMode(e.target.value)}
            >
              <option value="manual">{t("book.mode.static")}</option>
              <option value="screener">{t("book.mode.screen")}</option>
              <option value="hybrid">{t("book.mode.hybrid")}</option>
            </select>
          </label>
          <label className="space-y-1 text-sm block">
            <FieldLabel tip={t("tip.book.profile")}>{t("book.profile")}</FieldLabel>
            <input
              className="input"
              value={profile}
              onChange={(e) => setProfile(e.target.value)}
              placeholder={t("book.profile_ph")}
              title={t("tip.book.profile")}
            />
          </label>
          <div className="space-y-1 text-sm">
            <FieldLabel tip={t("tip.book.add_key")}>{t("book.add_key")}</FieldLabel>
            <div className="flex gap-2">
              <input
                className="input"
                value={manualKey}
                onChange={(e) => setManualKey(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") addManual();
                }}
                placeholder={t("book.add_key_ph")}
                title={t("tip.book.add_key")}
              />
              <button
                type="button"
                className="btn-ghost"
                title={t("tip.book.add_key")}
                onClick={addManual}
              >
                {t("book.add")}
              </button>
            </div>
          </div>
        </div>
        <p className="text-xs text-[var(--muted)] mt-3">{t("book.mode_help")}</p>
      </Collapsible>

      <Collapsible
        id="book.catalog"
        title={`${t("book.catalog")} · ${selected.size}`}
        tip={t("tip.book.catalog")}
        defaultOpen
      >
        <p className="text-xs text-[var(--muted)] mb-3">
          {t("book.selected_fmt", { n: selected.size, catalog: catalog.length })}
          {snap.universe?.reason ? ` · ${snap.universe.reason}` : ""}
        </p>
        {catalog.length ? (
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {catalog.map((it) => {
              const k = itemKey(it);
              if (!k) return null;
              const on = selected.has(k);
              return (
                <label
                  key={k}
                  className={`flex items-center gap-2 border rounded-lg px-3 py-2 cursor-pointer ${
                    on
                      ? "border-[var(--accent)]/50 bg-[var(--accent)]/10"
                      : "border-[var(--panel-border)]"
                  }`}
                  title={t("tip.book.toggle")}
                >
                  <input type="checkbox" checked={on} onChange={() => toggle(k)} />
                  <span className="font-mono text-sm">{k}</span>
                  {it.exchange ? (
                    <span className="text-[10px] text-[var(--muted)] ml-auto">
                      {String(it.exchange)}
                    </span>
                  ) : null}
                </label>
              );
            })}
          </div>
        ) : (
          <div className="space-y-2">
            <p className="text-sm text-[var(--muted)]">{t("book.empty_catalog")}</p>
            <div className="flex flex-wrap gap-2">
              {Array.from(selected).map((k) => (
                <button
                  key={k}
                  type="button"
                  className="btn-ghost font-mono text-xs"
                  title={t("tip.book.toggle")}
                  onClick={() => toggle(k)}
                >
                  {k} ×
                </button>
              ))}
            </div>
          </div>
        )}
      </Collapsible>
    </div>
  );
}
