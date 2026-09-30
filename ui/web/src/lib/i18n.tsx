"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import en from "@/locales/en.json";
import ru from "@/locales/ru.json";
import { loadPref, savePref } from "@/lib/prefs";

export type Lang = "en" | "ru";

const CATALOGS: Record<Lang, Record<string, string>> = {
  en: en as Record<string, string>,
  ru: ru as Record<string, string>,
};

type I18nCtx = {
  lang: Lang;
  setLang: (code: Lang) => void;
  t: (key: string, vars?: Record<string, string | number>) => string;
};

const Ctx = createContext<I18nCtx>({
  lang: "en",
  setLang: () => undefined,
  t: (k) => k,
});

function detectLang(): Lang {
  const saved = loadPref<string>("lang", "");
  if (saved === "ru" || saved === "en") return saved;
  if (typeof navigator !== "undefined" && navigator.language?.toLowerCase().startsWith("ru")) {
    return "ru";
  }
  return "en";
}

function format(template: string, vars?: Record<string, string | number>): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (_, k: string) =>
    vars[k] != null ? String(vars[k]) : `{${k}}`,
  );
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(() =>
    typeof window === "undefined" ? "en" : detectLang(),
  );

  const setLang = useCallback((code: Lang) => {
    setLangState(code);
    savePref("lang", code);
  }, []);

  const t = useCallback(
    (key: string, vars?: Record<string, string | number>) => {
      const catalog = CATALOGS[lang] || CATALOGS.en;
      const text = catalog[key] || CATALOGS.en[key] || key;
      return format(text, vars);
    },
    [lang],
  );

  const value = useMemo(() => ({ lang, setLang, t }), [lang, setLang, t]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useI18n() {
  return useContext(Ctx);
}

export function useT() {
  return useContext(Ctx).t;
}
