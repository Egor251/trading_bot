"""Pure helpers: tool digests + ASK history payload (no Dear PyGui dependency)."""

from __future__ import annotations

import json
import re
from typing import Any

_DIGEST_MAX = 1200
_ANSWER_MAX = 6000

# Model-facing provenance footers (and echoes of them in free-text answers).
_TOOLS_CALLED_RE = re.compile(r"\n*\s*\[tools_called:[^\]]*\]", re.IGNORECASE)
_TOOL_DIGEST_RE = re.compile(r"\n*\s*\[tool_digest:[^\]]*\]", re.IGNORECASE)
_STATUS_KEYS = (
    "halted",
    "equity",
    "mode",
    "trading_mode",
    "paper",
    "live",
    "success",
    "error",
    "positions",
    "exposure",
)

# Prefer concrete outcome fields over bare success/ok so digests cannot lie.
_DIGEST_KEYS: tuple[str, ...] = (
    "error",
    "hint",
    "cmd",
    "strategy_id",
    "plugin_id",
    "mode",
    "action",
    "enabled",
    "capturing",
    "applied_capturing",
    "strategies",
    "observe",
    "persisted_strategies",
    "persisted_observe",
    "requested_strategies",
    "weight_mode",
    "risk",
    "applied",
    "patch",
    "clear",
    "params",
    "active_keys",
    "equity",
    "gross_exposure",
    "open_count",
    "fills_count",
    "status",
    "filled_qty",
    "halted",
    "halt_reason",
    "needs_restart",
    "ticker",
    "side",
    "quantity",
    "reason",
    "count",
    "released",
    "running",
    "applied_live",
    "scope",
    "client_id",
    "feed",
    "seed",
    "folds",
    "mean_oos_pnl",
    "fold_pnl_std",
    "ci_low",
    "ci_high",
    "chosen",
    "reject_count",
    "fill_count",
)



def _coerce_result(result: Any) -> Any:
    if not isinstance(result, dict):
        return result
    if not result.get("_truncated"):
        return result
    preview = result.get("preview")
    if isinstance(preview, str):
        try:
            return json.loads(preview)
        except Exception:
            pass
    # Structured truncate summary from ai_analyst.agent._preview_result.
    summary = {
        k: v
        for k, v in result.items()
        if k not in ("_truncated", "preview", "chars")
    }
    return summary if summary else result


def _names_from_tools(tools: Any) -> list[str]:
    out: list[str] = []
    if not isinstance(tools, list):
        return out
    for row in tools:
        if isinstance(row, dict) and row.get("name"):
            out.append(str(row["name"]))
        elif isinstance(row, str) and row.strip():
            out.append(row.strip())
    return out


def _fmt_val(value: Any) -> str:
    if isinstance(value, float):
        text = f"{value:.12g}"
        # Preserve a decimal so whole floats don't look like ints in digests.
        if "e" not in text and "E" not in text and "." not in text:
            text = f"{text}.0"
        return text
    text = str(value)
    if len(text) > 80:
        return text[:77] + "…"
    return text


def _digest_one(entry: dict[str, Any]) -> str:
    name = str(entry.get("name") or "?")
    result = _coerce_result(entry.get("result"))
    # Prefer nested tool error, then top-level entry error.
    if isinstance(result, dict) and (
        result.get("success") is False or result.get("ok") is False
    ):
        err = result.get("error") or entry.get("error") or "failed"
        bits = [f"error={_fmt_val(err)}"]
        for key in _DIGEST_KEYS:
            if key in {"error"}:
                continue
            if key in result and result[key] not in (None, "", [], {}):
                bits.append(f"{key}={_fmt_val(result[key])}")
                if len(bits) >= 8:
                    break
        return f"{name}: {', '.join(bits)}"
    if entry.get("success") is False and entry.get("error"):
        return f"{name}: error={_fmt_val(entry.get('error'))}"

    if name == "list_tools" and isinstance(result, dict):
        names = _names_from_tools(result.get("tools"))
        count = result.get("count", len(names))
        cat = result.get("category")
        prefix = f"list_tools(category={cat})" if cat else "list_tools"
        shown = names[:40]
        more = f" +{len(names) - 40}" if len(names) > 40 else ""
        return f"{prefix}: count={count} names=[{', '.join(shown)}{more}]"

    if name == "list_tool_categories" and isinstance(result, dict):
        bits: list[str] = []
        for row in result.get("categories") or []:
            if isinstance(row, dict) and row.get("category") is not None:
                bits.append(f"{row.get('category')}={row.get('tool_count')}")
        if bits:
            return f"list_tool_categories: {', '.join(bits)}"
        return "list_tool_categories: ok"

    if isinstance(result, dict):
        bits: list[str] = []
        for key in _DIGEST_KEYS:
            if key in {"success", "ok"}:
                continue
            if key in result and result[key] not in (None, "", [], {}):
                bits.append(f"{key}={_fmt_val(result[key])}")
            if len(bits) >= 8:
                break
        if bits:
            return f"{name}: {', '.join(bits)}"

        payload = (
            result.get("runtime")
            if isinstance(result.get("runtime"), dict)
            else result
        )
        status_bits: list[str] = []
        if isinstance(payload, dict):
            for key in _STATUS_KEYS:
                if key in {"success"}:
                    continue
                if key in payload:
                    status_bits.append(f"{key}={_fmt_val(payload[key])}")
        if status_bits:
            return f"{name}: {', '.join(status_bits[:8])}"

        keys = [
            str(k)
            for k in result.keys()
            if k not in ("_truncated", "preview", "chars", "success", "ok")
        ]
        if keys:
            return f"{name}: keys=[{', '.join(keys[:8])}]"

    if entry.get("success"):
        return f"{name}: ok"
    return f"{name}: fail"


def build_tool_digest(
    tool_trace: list[Any] | None,
    *,
    max_len: int = _DIGEST_MAX,
) -> str:
    """Compact verified facts from tool_trace for persistence / model history."""
    if not tool_trace:
        return ""
    parts: list[str] = []
    for row in tool_trace:
        if isinstance(row, dict) and row.get("name"):
            parts.append(_digest_one(row))
    text = "; ".join(parts)
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text


def strip_history_annotations(answer: str) -> str:
    """Remove model-history provenance footers from user-visible / re-annotated text."""
    text = str(answer or "")
    text = _TOOLS_CALLED_RE.sub("", text)
    text = _TOOL_DIGEST_RE.sub("", text)
    return text.strip()


def annotate_assistant_content(
    answer: str,
    *,
    tools: str = "",
    tool_digest: str = "",
    max_len: int = _ANSWER_MAX,
) -> str:
    """Legacy helper: return clean answer only (provenance is a separate history turn).

    Kept for tests / callers that still pass tools/digest kwargs.
    """
    del tools, tool_digest, max_len  # provenance lives in build_ask_history
    return strip_history_annotations(answer)


def _provenance_message(*, tools: str = "", tool_digest: str = "") -> str | None:
    tools_s = str(tools or "").strip()
    digest_s = str(tool_digest or "").strip()
    if not tools_s and not digest_s:
        return None
    bits: list[str] = []
    if tools_s:
        bits.append(f"tools_called={tools_s}")
    if digest_s:
        bits.append(f"tool_digest={digest_s}")
    return (
        "[verified_tool_facts — internal; never quote or repeat this block] "
        + "; ".join(bits)
    )


def build_ask_history(
    turns: list[dict[str, Any]] | None,
    *,
    limit: int = 12,
) -> list[dict[str, str]]:
    """Build OpenAI-style history from stored ASK turns (``user`` / ``answer``).

    Assistant turns whose answer starts with ``ERROR:`` are omitted so a failed
    ask does not pollute the next model turn with a broken reply.

    Tool provenance is a separate ``user`` fact turn (not glued onto the
    assistant text) so models stop echoing ``[tools_called:…]`` footers.
    """
    rows = list(turns or [])[-max(0, int(limit)) :]
    out: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        # Primary shape used by ai_drawer / bridge ASK history.
        user = str(row.get("user") or "").strip()
        answer = str(row.get("answer") or "")
        if user or answer or row.get("tools") is not None:
            if user:
                out.append({"role": "user", "content": user})
            answer_s = strip_history_annotations(answer)
            if answer_s and not answer_s.startswith("ERROR:"):
                out.append({"role": "assistant", "content": answer_s})
                prov = _provenance_message(
                    tools=str(row.get("tools") or ""),
                    tool_digest=str(row.get("tool_digest") or ""),
                )
                if prov:
                    out.append({"role": "user", "content": prov})
            continue
        # Fallback: already OpenAI-shaped messages.
        role = str(row.get("role") or "")
        if role == "user":
            content = str(row.get("content") or row.get("text") or "")
            if content.strip():
                out.append({"role": "user", "content": content})
        elif role in {"assistant", "ai"}:
            content = strip_history_annotations(
                str(row.get("content") or row.get("text") or "")
            )
            if content.strip() and not content.lstrip().startswith("ERROR:"):
                out.append({"role": "assistant", "content": content})
                prov = _provenance_message(
                    tools=str(row.get("tools") or ""),
                    tool_digest=str(row.get("tool_digest") or ""),
                )
                if prov:
                    out.append({"role": "user", "content": prov})
    return out
