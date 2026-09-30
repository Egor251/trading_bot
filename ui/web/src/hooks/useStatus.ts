"use client";

import { useCallback, useState } from "react";
import { sendCmd, type CmdResult } from "@/lib/api";

export function useStatus() {
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);

  const run = useCallback(
    async (
      cmd: string,
      payload: Record<string, unknown> = {},
      timeout = 30,
    ): Promise<CmdResult> => {
      setBusy(true);
      setStatus(`${cmd}…`);
      try {
        const res = await sendCmd(cmd, payload, timeout);
        if (res.ok === false) {
          setStatus(String(res.error || `${cmd} failed`));
        } else {
          setStatus(`${cmd} ok`);
        }
        return res;
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        setStatus(msg);
        return { ok: false, error: msg, cmd };
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  return { status, setStatus, busy, run };
}
