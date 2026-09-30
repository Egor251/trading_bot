import Link from "next/link";
import type { ReactNode } from "react";
import { HelpTip } from "@/components/help-tip";

export function PageHeader({
  title,
  description,
  tip,
  actions,
}: {
  title: string;
  description?: string;
  tip?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight inline-flex items-center gap-2">
          {title}
          {tip ? <HelpTip text={tip} /> : null}
        </h2>
        {description ? (
          <p className="text-sm text-[var(--muted)] mt-1 max-w-2xl">{description}</p>
        ) : null}
      </div>
      {actions ? <div className="flex flex-wrap gap-2 items-center">{actions}</div> : null}
    </header>
  );
}

export function StatCard({
  title,
  value,
  hint,
  tip,
  tone = "default",
}: {
  title: string;
  value: string;
  hint?: string;
  tip?: string;
  tone?: "default" | "ok" | "warn" | "bad";
}) {
  const toneClass =
    tone === "ok"
      ? "text-[var(--success)]"
      : tone === "warn"
        ? "text-[var(--warn)]"
        : tone === "bad"
          ? "text-[var(--danger)]"
          : "";
  return (
    <div className="panel p-5 fade-in" title={tip || hint || undefined}>
      <p className="text-xs uppercase tracking-wide text-[var(--muted)] inline-flex items-center gap-1.5">
        {title}
        {tip ? <HelpTip text={tip} /> : null}
      </p>
      <p className={`text-2xl font-semibold mt-2 ${toneClass}`}>{value}</p>
      {hint ? <p className="text-xs text-[var(--muted)] mt-2">{hint}</p> : null}
    </div>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const n = status.toLowerCase();
  const cls =
    n === "live" ||
    n === "ok" ||
    n === "running" ||
    n === "ready" ||
    n === "on" ||
    n === "enabled" ||
    n === "trading" ||
    n.includes("set")
      ? "badge-ok"
      : n === "halted" ||
          n === "paused" ||
          n === "error" ||
          n === "down" ||
          n === "off" ||
          n === "disabled" ||
          n.includes("missing")
        ? "badge-bad"
        : n === "warn" || n === "degraded" || n === "reconnecting"
          ? "badge-warn"
          : "badge-idle";
  return (
    <span className={`inline-flex items-center text-xs px-2.5 py-1 rounded-full font-medium ${cls}`}>
      {status}
    </span>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="panel p-8 text-center fade-in">
      <p className="font-medium">{title}</p>
      {hint ? <p className="text-sm text-[var(--muted)] mt-2">{hint}</p> : null}
    </div>
  );
}

export function NavSection({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-1">
      <p className="px-3 pt-3 pb-1 text-[10px] uppercase tracking-[0.18em] text-[var(--muted)]">
        {label}
      </p>
      {children}
    </div>
  );
}

export function NavLink({
  href,
  active,
  tip,
  children,
}: {
  href: string;
  active?: boolean;
  tip?: string;
  children: ReactNode;
}) {
  return (
    <Link
      href={href}
      className={`nav-link ${active ? "active" : ""}`}
      title={tip || undefined}
    >
      <span className="inline-flex items-center gap-1.5 w-full">
        <span className="truncate flex-1">{children}</span>
        {tip ? <HelpTip text={tip} /> : null}
      </span>
    </Link>
  );
}

export function ActionPill({
  action,
}: {
  action?: string;
}) {
  const a = (action || "").toUpperCase();
  const cls =
    a === "BUY"
      ? "text-[var(--success)] border-[var(--success)]/40"
      : a === "SELL"
        ? "text-[var(--danger)] border-[var(--danger)]/40"
        : "text-[var(--muted)] border-[var(--panel-border)]";
  return (
    <span className={`inline-block px-2 py-0.5 rounded-full border text-[11px] ${cls}`}>
      {a || "—"}
    </span>
  );
}
