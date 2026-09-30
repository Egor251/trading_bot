"use client";

import { useState } from "react";
import { EmptyState, PageHeader } from "@/components/ui";
import { useStatus } from "@/hooks/useStatus";
import { useT } from "@/lib/i18n";
import { useSnapshot } from "@/lib/snapshot";

const SAMPLE_DRAFT = `{
  "version": "draft-1",
  "rationale": "operator draft",
  "proposed_by": "web_ui",
  "rules": [
    {
      "rule_id": "default_bps",
      "priority": 0,
      "match": { "symbol": "*" },
      "components": [{ "kind": "bps", "rate": "5", "name": "commission" }],
      "source_spans": [
        {
          "source_hash": "REPLACE_WITH_SOURCE_SHA256",
          "start": 0,
          "end": 10,
          "quote": "commission",
          "confidence": "1"
        }
      ]
    }
  ]
}`;

export default function CostsPage() {
  const { snap } = useSnapshot();
  const { status, busy, run, setStatus } = useStatus();
  const t = useT();
  const costs = (snap?.costs || {}) as Record<string, unknown>;

  const [docId, setDocId] = useState("broker-fee-sheet");
  const [title, setTitle] = useState("Broker fee schedule");
  const [text, setText] = useState(
    "Commission 5 bps per trade. Minimum fee 1 RUB. Slippage allowance 2 bps.",
  );
  const [draftJson, setDraftJson] = useState(SAMPLE_DRAFT);
  const [source, setSource] = useState<Record<string, unknown> | null>(null);
  const [expectedHash, setExpectedHash] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [lastReport, setLastReport] = useState<unknown>(null);

  if (!snap || snap.empty) {
    return (
      <div className="space-y-6">
        <PageHeader title={t("nav.costs")} />
        <EmptyState title="Waiting for costs snapshot" />
      </div>
    );
  }

  function sourcePayload() {
    return source || { document_id: docId.trim(), title: title.trim(), text };
  }

  function parseDraft(): Record<string, unknown> | null {
    try {
      return JSON.parse(draftJson) as Record<string, unknown>;
    } catch (e) {
      setStatus(`Invalid draft JSON: ${e instanceof Error ? e.message : e}`);
      return null;
    }
  }

  async function extract() {
    const res = await run(
      "COST_EXTRACT",
      { source: { document_id: docId.trim(), title: title.trim(), text } },
      60,
    );
    if (res.ok !== false && res.source && typeof res.source === "object") {
      const src = res.source as Record<string, unknown>;
      setSource(src);
      const hash = String(src.sha256 || "");
      if (hash) {
        setDraftJson((prev) =>
          prev.replace(/REPLACE_WITH_SOURCE_SHA256/g, hash),
        );
      }
      setLastReport(src);
    }
  }

  async function validate() {
    const draft = parseDraft();
    if (!draft) return;
    const res = await run(
      "COST_VALIDATE",
      { source: sourcePayload(), draft },
      60,
    );
    if (res.ok !== false) setLastReport(res.validation || res);
  }

  async function saveDraft() {
    const draft = parseDraft();
    if (!draft) return;
    const res = await run(
      "COST_SAVE_DRAFT",
      {
        source: sourcePayload(),
        draft,
        operator_id: "web_operator",
      },
      60,
    );
    if (res.ok !== false) {
      const h = String(res.draft_hash || "");
      if (h) setExpectedHash(h);
      setLastReport(res);
    }
  }

  async function activate() {
    if (!confirm) {
      setStatus("confirm_cost_schedule required");
      return;
    }
    if (expectedHash.length !== 64) {
      setStatus("exact 64-char expected_hash required (save draft first)");
      return;
    }
    const res = await run(
      "COST_ACTIVATE",
      {
        expected_hash: expectedHash,
        confirm_cost_schedule: true,
        operator_id: "web_operator",
      },
      60,
    );
    if (res.ok !== false) setLastReport(res.activation || res);
  }

  return (
    <div className="space-y-6 fade-in">
      <PageHeader title={t("nav.costs")} description="Source → extract → draft → validate → activate" />
      {status ? <p className="text-sm text-[var(--muted)]">{status}</p> : null}

      <section className="panel p-5 space-y-3">
        <h3 className="text-xs uppercase tracking-wide text-[var(--muted)]">
          Source document
        </h3>
        <div className="grid gap-3 sm:grid-cols-2 max-w-3xl">
          <label className="space-y-1 text-sm">
            <span className="text-[var(--muted)]">document_id</span>
            <input className="input" value={docId} onChange={(e) => setDocId(e.target.value)} />
          </label>
          <label className="space-y-1 text-sm">
            <span className="text-[var(--muted)]">title</span>
            <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} />
          </label>
        </div>
        <textarea
          className="input font-mono text-xs min-h-28"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        {source?.sha256 ? (
          <p className="text-xs font-mono text-[var(--muted)]">
            source sha256: {String(source.sha256)}
          </p>
        ) : null}
      </section>

      <section className="panel p-5 space-y-3">
        <h3 className="text-xs uppercase tracking-wide text-[var(--muted)]">
          Draft JSON
        </h3>
        <textarea
          className="input font-mono text-xs min-h-64"
          value={draftJson}
          onChange={(e) => setDraftJson(e.target.value)}
        />
        <label className="space-y-1 text-sm block max-w-xl">
          <span className="text-[var(--muted)]">expected_hash (from SAVE_DRAFT)</span>
          <input
            className="input font-mono text-xs"
            value={expectedHash}
            onChange={(e) => setExpectedHash(e.target.value)}
          />
        </label>
        <label className="inline-flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={confirm}
            onChange={(e) => setConfirm(e.target.checked)}
          />
          confirm_cost_schedule
        </label>
      </section>

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          className="btn-primary"
          disabled={busy}
          onClick={() => void extract()}
        >
          Extract
        </button>
        <button
          type="button"
          className="btn-ghost"
          disabled={busy}
          onClick={() => void validate()}
        >
          Validate
        </button>
        <button
          type="button"
          className="btn-ghost"
          disabled={busy}
          onClick={() => void saveDraft()}
        >
          Save draft
        </button>
        <button
          type="button"
          className="btn-kill"
          disabled={busy}
          onClick={() => void activate()}
        >
          Activate
        </button>
      </div>

      <section className="panel p-5">
        <h3 className="text-xs uppercase tracking-wide text-[var(--muted)] mb-3">
          Last report / snapshot
        </h3>
        <pre className="text-xs font-mono whitespace-pre-wrap overflow-auto max-h-[420px]">
          {JSON.stringify(lastReport ?? costs, null, 2)}
        </pre>
      </section>
    </div>
  );
}
