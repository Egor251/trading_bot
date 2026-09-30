"use client";

import type { ReactNode } from "react";
import { I18nProvider } from "@/lib/i18n";
import { SnapshotProvider } from "@/lib/snapshot";

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <I18nProvider>
      <SnapshotProvider>{children}</SnapshotProvider>
    </I18nProvider>
  );
}
