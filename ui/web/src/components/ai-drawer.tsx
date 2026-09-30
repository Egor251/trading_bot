"use client";

import { useMemo, useState } from "react";
import { sendCmd } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useSnapshot } from "@/lib/snapshot";

type Turn = { role: "user" | "assistant"; content: string };

export function AiDrawer() {
  const { snap } = useSnapshot();
  const t = useT();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [history, setHistory] = useState<Turn[]>([]);
  const [useTools, setUseTools] = useState(true);
  const [providerId, setProviderId] = useState("");

  const ai = (snap?.ai || {}) as Record<string, unknown>;
  const providers = useMemo(() => {
    const chat = ai.chat_profiles;
    if (Array.isArray(chat) && chat.length) {
      return chat
        .filter((r) => r && typeof r === "object" && (r as { enabled?: boolean }).enabled !== false)
        .map((r) => String((r as { id?: string }).id || ""))
        .filter(Boolean);
    }
    if (Array.isArray(ai.providers)) return ai.providers.map(String);
    if (Array.isArray(ai.profiles)) return ai.profiles.map(String);
    const profiles = ai.profiles;
    if (profiles && typeof profiles === "object") return Object.keys(profiles as object);
    return [] as string[];
  }, [ai]);

  const defaultProvider = String(ai.default_provider || ai.provider || "");
  const activeProvider = providerId || defaultProvider;

  async function ask() {
    const text = q.trim();
    if (!text || busy) return;
    setBusy(true);
    setErr("");
    try {
      const res = await sendCmd(
        "ASK",
        {
          text,
          use_tools: useTools,
          provider_id: activeProvider || null,
          caller_role: "analyst",
          history: history.slice(-12),
        },
        180,
      );
      if (res.ok === false) {
        setErr(String(res.error || "ask failed"));
        return;
      }
      const reply =
        (res.answer as string) ||
        (res.reply as string) ||
        (res.text as string) ||
        JSON.stringify(res, null, 2);
      setHistory((h) => [
        ...h,
        { role: "user", content: text },
        { role: "assistant", content: reply },
      ]);
      setQ("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        type="button"
        className="btn-ghost fixed bottom-5 right-5 z-40 shadow-lg bg-[var(--panel)]"
        onClick={() => setOpen((v) => !v)}
      >
        {t("web.ai.open")}
      </button>
      {open ? (
        <aside className="fixed top-0 right-0 z-50 h-full w-full max-w-md border-l border-[var(--panel-border)] bg-[var(--panel)] p-4 flex flex-col gap-3 fade-in">
          <div className="flex items-center justify-between">
            <div>
              <p className="font-semibold">{t("nav.ai_panel")}</p>
              <p className="text-xs text-[var(--muted)]">
                provider {activeProvider || "—"} · ready {String(ai.ready ?? "—")}
              </p>
            </div>
            <button type="button" className="btn-ghost" onClick={() => setOpen(false)}>
              {t("web.ai.close")}
            </button>
          </div>

          <div className="flex flex-wrap gap-2 items-center text-sm">
            <select
              className="input w-auto text-xs"
              value={activeProvider}
              onChange={(e) => setProviderId(e.target.value)}
            >
              {(providers.length ? providers : [defaultProvider || "default"]).map(
                (p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ),
              )}
            </select>
            <label className="inline-flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                checked={useTools}
                onChange={(e) => setUseTools(e.target.checked)}
              />
              use tools
            </label>
            <button
              type="button"
              className="btn-ghost text-xs"
              onClick={() => setHistory([])}
            >
              Clear
            </button>
          </div>

          <div className="flex-1 overflow-auto space-y-3 panel p-3 min-h-0">
            {history.length ? (
              history.map((t, i) => (
                <div key={i} className="text-sm">
                  <p className="text-[10px] uppercase tracking-wide text-[var(--muted)]">
                    {t.role}
                  </p>
                  <pre className="whitespace-pre-wrap font-sans text-xs mt-1">
                    {t.content}
                  </pre>
                </div>
              ))
            ) : (
              <p className="text-xs text-[var(--muted)]">
                Ask about risk, fills, consilium…
              </p>
            )}
          </div>

          <textarea
            className="input min-h-24 resize-y"
            placeholder="Ask…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void ask();
            }}
          />
          <button type="button" className="btn-primary" disabled={busy} onClick={() => void ask()}>
            {busy ? "Asking…" : "ASK"}
          </button>
          {err ? <p className="text-sm text-[var(--danger)]">{err}</p> : null}
        </aside>
      ) : null}
    </>
  );
}
