"use client";

import { useEffect, useState, type ReactNode } from "react";
import { HelpTip } from "@/components/help-tip";
import { loadPref, savePref } from "@/lib/prefs";

type Props = {
  id: string;
  title: string;
  children: ReactNode;
  /** Hover help next to the section title */
  tip?: string;
  /** Default open when no preference saved */
  defaultOpen?: boolean;
  /** Right-side header actions (always visible) */
  actions?: ReactNode;
  className?: string;
  badge?: ReactNode;
};

export function Collapsible({
  id,
  title,
  tip,
  children,
  defaultOpen = true,
  actions,
  className = "",
  badge,
}: Props) {
  const [open, setOpen] = useState(defaultOpen);

  useEffect(() => {
    setOpen(loadPref(`collapse.${id}`, defaultOpen));
  }, [id, defaultOpen]);

  function toggle() {
    setOpen((prev) => {
      const next = !prev;
      savePref(`collapse.${id}`, next);
      return next;
    });
  }

  return (
    <section className={`panel overflow-hidden fade-in ${className}`}>
      <header className="flex items-center gap-2 px-4 py-3 border-b border-[var(--panel-border)]/70">
        <button
          type="button"
          className="flex flex-1 items-center gap-2 min-w-0 text-left"
          onClick={toggle}
          aria-expanded={open}
          title={tip || undefined}
        >
          <span
            className={`inline-flex h-5 w-5 shrink-0 items-center justify-center rounded border border-[var(--panel-border)] text-[10px] text-[var(--muted)] transition-transform ${
              open ? "rotate-90" : ""
            }`}
          >
            ▸
          </span>
          <h3 className="text-xs uppercase tracking-wide text-[var(--muted)] truncate">
            {title}
          </h3>
          {tip ? (
            <span
              onClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => e.stopPropagation()}
            >
              <HelpTip text={tip} />
            </span>
          ) : null}
          {badge}
        </button>
        {actions ? (
          <div className="flex shrink-0 items-center gap-2" onClick={(e) => e.stopPropagation()}>
            {actions}
          </div>
        ) : null}
      </header>
      {open ? <div className="p-4">{children}</div> : null}
    </section>
  );
}
