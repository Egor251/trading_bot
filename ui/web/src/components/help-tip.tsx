"use client";

import type { ReactNode } from "react";

/** Small «?» with hover / focus tooltip. */
export function HelpTip({
  text,
  label = "?",
}: {
  text: string;
  label?: string;
}) {
  if (!text) return null;
  return (
    <span className="help-tip" tabIndex={0} aria-label={text}>
      <span className="help-tip__mark" aria-hidden>
        {label}
      </span>
      <span className="help-tip__bubble" role="tooltip">
        {text}
      </span>
    </span>
  );
}

/** Inline label + optional help tip. */
export function FieldLabel({
  children,
  tip,
  className = "",
}: {
  children: ReactNode;
  tip?: string;
  className?: string;
}) {
  return (
    <span className={`inline-flex items-center gap-1.5 ${className}`}>
      <span className="text-[var(--muted)]">{children}</span>
      {tip ? <HelpTip text={tip} /> : null}
    </span>
  );
}

/** Button (or control) with native title + optional ? tip beside it. */
export function WithHelp({
  tip,
  children,
  className = "",
}: {
  tip?: string;
  children: ReactNode;
  className?: string;
}) {
  if (!tip) return <>{children}</>;
  return (
    <span className={`inline-flex items-center gap-1.5 ${className}`} title={tip}>
      {children}
      <HelpTip text={tip} />
    </span>
  );
}
