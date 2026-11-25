"use client";

import { useEffect, useState, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { AiDrawer } from "@/components/ai-drawer";
import { DesktopChrome } from "@/components/desktop-chrome";
import { NavLink, NavSection, StatusBadge } from "@/components/ui";
import { sendCmd } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { isDesktop } from "@/lib/platform";
import { loadPref, savePref } from "@/lib/prefs";
import { useSnapshot } from "@/lib/snapshot";
import { fmtNum } from "@/lib/format";

const NAV = [
  { href: "/desk", key: "nav.desk", tip: "tip.nav.desk" },
  { href: "/charts", key: "nav.charts", tip: "tip.nav.charts" },
  { href: "/book", key: "nav.book", tip: "tip.nav.book" },
  { href: "/consilium", key: "nav.consilium", tip: "tip.nav.consilium" },
  { href: "/strategy", key: "nav.strategy", tip: "tip.nav.strategy" },
  { href: "/research", key: "nav.research", tip: "tip.nav.research" },
  { href: "/phantom", key: "nav.phantom", tip: "tip.nav.phantom" },
  { href: "/costs", key: "nav.costs", tip: "tip.nav.costs" },
  { href: "/system", key: "nav.system", tip: "tip.nav.system" },
] as const;

export function OperatorShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { snap, health, wsConnected } = useSnapshot();
  const { t, lang, setLang } = useI18n();
  const halted = !!snap?.halted;
  const engineOk = !!health?.engine_connected;
  const connLabel = !engineOk
    ? "engine down"
    : wsConnected
      ? "live"
      : "reconnecting";
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    setCollapsed(loadPref("sidebar.collapsed", false));
  }, []);

  function toggleSidebar() {
    setCollapsed((v) => {
      const next = !v;
      savePref("sidebar.collapsed", next);
      return next;
    });
  }

  const ensOn = !!snap?.ensemble?.enabled;
  const mode = String(snap?.mode || "").toLowerCase();

  return (
    <div className="min-h-screen flex">
      <DesktopChrome snap={snap} halted={halted} />
      <aside
        className={`shrink-0 border-r border-[var(--panel-border)] bg-black/20 flex flex-col gap-3 transition-[width] duration-200 ${
          collapsed ? "w-[64px] p-2" : "w-[240px] p-4"
        }`}
      >
        <div className="flex items-start justify-between gap-2">
          {!collapsed ? (
            <div>
              <div className="brand-mark">
                OPERATOR <span>· TB</span>
              </div>
              <p className="text-[11px] text-[var(--muted)] mt-2">
                {isDesktop() ? t("web.platform.desktop") : t("web.platform.web")}
              </p>
            </div>
          ) : (
            <div className="brand-mark text-sm mx-auto">
              <span>TB</span>
            </div>
          )}
        </div>

        <button
          type="button"
          className="btn-ghost text-[11px] w-full"
          onClick={toggleSidebar}
          title={collapsed ? t("web.sidebar.expand") : t("web.sidebar.collapse")}
        >
          {collapsed ? "»" : "«"}
        </button>

        {!collapsed ? (
          <>
            <NavSection label={t("menu.trading")}>
              {NAV.map((item) => (
                <NavLink
                  key={item.href}
                  href={item.href}
                  tip={t(item.tip)}
                  active={
                    pathname === item.href || pathname.startsWith(item.href + "/")
                  }
                >
                  {t(item.key)}
                </NavLink>
              ))}
            </NavSection>

            <div className="panel p-3 text-xs space-y-2" title={t("tip.shell.status")}>
              <div className="flex items-center justify-between gap-2">
                <span className="text-[var(--muted)]" title={t("tip.shell.link")}>
                  {t("web.link")}
                </span>
                <StatusBadge status={connLabel} />
              </div>
              <div className="flex items-center justify-between gap-2">
                <span className="text-[var(--muted)]" title={t("tip.desk.state")}>
                  {t("web.state")}
                </span>
                <StatusBadge status={halted ? "HALTED" : "LIVE"} />
              </div>
              <div className="flex items-center justify-between gap-2">
                <span className="text-[var(--muted)]" title={t("tip.desk.mode")}>
                  {t("web.mode")}
                </span>
                <span className="font-mono">{(snap?.mode || "—").toString().toUpperCase()}</span>
              </div>
              <div className="flex items-center justify-between gap-2">
                <span className="text-[var(--muted)]" title={t("tip.desk.equity")}>
                  {t("web.equity")}
                </span>
                <span className="font-mono">{fmtNum(snap?.equity)}</span>
              </div>
              <div className="flex items-center justify-between gap-2">
                <span className="text-[var(--muted)]" title={t("tip.desk.autotrade")}>
                  {t("web.autotrade")}
                </span>
                <StatusBadge status={ensOn ? "ON" : "OFF"} />
              </div>
              <p className="text-[10px] text-[var(--muted)] leading-snug">
                {mode === "paper" || mode === "sim" || mode === "simulation"
                  ? t("web.mode.paper_hint")
                  : mode
                    ? t("web.mode.live_hint")
                    : "—"}
              </p>
            </div>

            <div className="flex gap-1">
              <button
                type="button"
                className={`btn-ghost flex-1 text-[11px] ${lang === "en" ? "border-[var(--accent)] text-[var(--foreground)]" : ""}`}
                onClick={() => setLang("en")}
              >
                EN
              </button>
              <button
                type="button"
                className={`btn-ghost flex-1 text-[11px] ${lang === "ru" ? "border-[var(--accent)] text-[var(--foreground)]" : ""}`}
                onClick={() => setLang("ru")}
              >
                RU
              </button>
            </div>

            <div className="mt-auto space-y-2">
              <button
                type="button"
                className="btn-kill w-full"
                title={t("tip.desk.kill")}
                onClick={() => void sendCmd("KILL")}
              >
                {t("nav.kill")}
              </button>
              <button
                type="button"
                className="btn-ghost w-full"
                title={t("tip.desk.resume")}
                onClick={() => void sendCmd("RESUME")}
              >
                {t("desk.resume")}
              </button>
            </div>
          </>
        ) : (
          <div className="mt-auto space-y-2">
            <button
              type="button"
              className="btn-kill w-full text-[10px] px-1"
              onClick={() => void sendCmd("KILL")}
              title={t("nav.kill")}
            >
              K
            </button>
            <button
              type="button"
              className="btn-ghost w-full text-[10px] px-1"
              onClick={() => void sendCmd("RESUME")}
              title={t("desk.resume")}
            >
              R
            </button>
          </div>
        )}
      </aside>
      <main className="flex-1 min-w-0 p-5 md:p-6 overflow-auto">{children}</main>
      <AiDrawer />
    </div>
  );
}
