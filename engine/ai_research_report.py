"""Persist detailed AI research reports (markdown + JSON) under a sandboxed root."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from engine.paths import data_path

DEFAULT_REPORT_ROOT = data_path("ai_reports")
_MAX_BODY_CHARS = 400_000
_MAX_TITLE_CHARS = 160
_MAX_ATTACH_BYTES = 8_000_000
_SLUG_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def default_report_root(core: Any | None = None) -> Path:
    """Resolve report root relative to project / config when available."""
    if core is not None:
        cfg = getattr(core, "config", None)
        raw = None
        getter = getattr(cfg, "get", None)
        if callable(getter):
            try:
                raw = getter("ai_reports_dir")
            except Exception:
                raw = None
        if not raw and isinstance(cfg, Mapping):
            raw = cfg.get("ai_reports_dir")
        if isinstance(raw, str) and raw.strip():
            return Path(raw.strip())
        path = getattr(cfg, "path", None)
        if path is not None:
            # boot YAML usually lives in config/ → project root is parent
            base = Path(path).resolve().parent
            if base.name.lower() == "config":
                base = base.parent
            return base / "data" / "ai_reports"
    return DEFAULT_REPORT_ROOT


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def slugify(text: str, *, fallback: str = "report") -> str:
    s = _SLUG_RE.sub("_", str(text or "").strip())[:80].strip("._-")
    return s or fallback


def _ensure_under(root: Path, target: Path) -> Path:
    root_r = root.resolve()
    tgt_r = target.resolve()
    if tgt_r != root_r and root_r not in tgt_r.parents:
        raise ValueError(f"path_outside_report_root: {target}")
    return tgt_r


def _safe_name(name: str, *, suffix: str = "") -> str:
    base = slugify(Path(str(name)).name, fallback="file")
    if suffix and not base.endswith(suffix):
        if "." in base:
            base = base.rsplit(".", 1)[0]
        base = f"{base}{suffix}"
    return base


def write_research_report(
    *,
    title: str,
    body_markdown: str,
    root: Path | str | None = None,
    kind: str = "research",
    summary: str | None = None,
    tags: Sequence[str] | None = None,
    strategy_ids: Sequence[str] | None = None,
    tickers: Sequence[str] | None = None,
    sections: Mapping[str, str] | None = None,
    metrics: Mapping[str, Any] | None = None,
    attachments: Mapping[str, Any] | None = None,
    conclusions: Sequence[str] | None = None,
    next_steps: Sequence[str] | None = None,
    source_tool_results: Sequence[Mapping[str, Any]] | None = None,
    author: str = "ai_analyst",
) -> dict[str, Any]:
    """Write a detailed report folder: report.md + meta.json + attachments/*.json."""
    report_root = Path(root) if root is not None else DEFAULT_REPORT_ROOT
    report_root.mkdir(parents=True, exist_ok=True)

    title_s = str(title or "").strip()[:_MAX_TITLE_CHARS] or "Untitled research"
    body = str(body_markdown or "")
    if len(body) > _MAX_BODY_CHARS:
        raise ValueError(f"body_too_large:{len(body)}>{_MAX_BODY_CHARS}")

    stamp = _utc_stamp()
    folder_name = f"{stamp}_{slugify(title_s)}"
    folder = report_root / folder_name
    # collision: append counter
    n = 1
    while folder.exists():
        folder = report_root / f"{folder_name}_{n}"
        n += 1
    folder.mkdir(parents=True, exist_ok=False)
    attach_dir = folder / "attachments"
    attach_dir.mkdir(parents=True, exist_ok=True)

    tag_list = [str(t).strip() for t in (tags or []) if str(t).strip()]
    sid_list = [str(s).strip() for s in (strategy_ids or []) if str(s).strip()]
    tick_list = [str(t).strip() for t in (tickers or []) if str(t).strip()]
    concl = [str(c).strip() for c in (conclusions or []) if str(c).strip()]
    steps = [str(s).strip() for s in (next_steps or []) if str(s).strip()]
    section_map = {
        str(k): str(v)
        for k, v in dict(sections or {}).items()
        if str(k).strip() and str(v).strip()
    }

    attach_paths: dict[str, str] = {}
    if attachments:
        for key, payload in attachments.items():
            name = _safe_name(str(key), suffix=".json")
            path = attach_dir / name
            raw = json.dumps(payload, indent=2, default=str, ensure_ascii=False)
            if len(raw.encode("utf-8")) > _MAX_ATTACH_BYTES:
                raise ValueError(f"attachment_too_large:{name}")
            path.write_text(raw, encoding="utf-8")
            attach_paths[str(key)] = str(path.as_posix())

    if source_tool_results:
        path = attach_dir / "source_tool_results.json"
        raw = json.dumps(list(source_tool_results), indent=2, default=str, ensure_ascii=False)
        if len(raw.encode("utf-8")) > _MAX_ATTACH_BYTES:
            # keep truncated index instead of failing the whole report
            slim = [
                {
                    "tool": r.get("tool") or r.get("cmd") or r.get("name"),
                    "ok": r.get("ok", r.get("success")),
                    "error": r.get("error"),
                    "keys": sorted(str(k) for k in r.keys())[:40]
                    if isinstance(r, Mapping)
                    else [],
                }
                for r in source_tool_results
                if isinstance(r, Mapping)
            ]
            path.write_text(
                json.dumps(slim, indent=2, default=str, ensure_ascii=False),
                encoding="utf-8",
            )
        else:
            path.write_text(raw, encoding="utf-8")
        attach_paths["source_tool_results"] = str(path.as_posix())

    md_parts: list[str] = [f"# {title_s}", ""]
    if summary:
        md_parts.extend([str(summary).strip(), ""])
    meta_line_bits = [
        f"**kind**: `{kind}`",
        f"**generated_at**: `{stamp}`",
        f"**author**: `{author}`",
    ]
    if sid_list:
        meta_line_bits.append("**strategies**: " + ", ".join(f"`{s}`" for s in sid_list))
    if tick_list:
        meta_line_bits.append("**tickers**: " + ", ".join(f"`{t}`" for t in tick_list))
    if tag_list:
        meta_line_bits.append("**tags**: " + ", ".join(f"`{t}`" for t in tag_list))
    md_parts.extend([" | ".join(meta_line_bits), ""])

    if metrics:
        md_parts.append("## Metrics")
        md_parts.append("")
        md_parts.append("| key | value |")
        md_parts.append("|---|---|")
        for k, v in metrics.items():
            val = json.dumps(v, default=str, ensure_ascii=False) if not isinstance(v, str) else v
            md_parts.append(f"| `{k}` | {val} |")
        md_parts.append("")

    if section_map:
        for name, text in section_map.items():
            md_parts.append(f"## {name}")
            md_parts.append("")
            md_parts.append(text.rstrip())
            md_parts.append("")

    md_parts.append("## Narrative")
    md_parts.append("")
    md_parts.append(body.rstrip() if body.strip() else "_(empty)_")
    md_parts.append("")

    if concl:
        md_parts.append("## Conclusions")
        md_parts.append("")
        for c in concl:
            md_parts.append(f"- {c}")
        md_parts.append("")

    if steps:
        md_parts.append("## Next steps")
        md_parts.append("")
        for s in steps:
            md_parts.append(f"- {s}")
        md_parts.append("")

    if attach_paths:
        md_parts.append("## Attachments")
        md_parts.append("")
        for k, p in attach_paths.items():
            md_parts.append(f"- `{k}` → `{p}`")
        md_parts.append("")

    report_md = "\n".join(md_parts)
    md_path = folder / "report.md"
    md_path.write_text(report_md, encoding="utf-8")

    meta = {
        "ok": True,
        "id": folder.name,
        "title": title_s,
        "kind": str(kind or "research"),
        "summary": str(summary or "").strip() or None,
        "tags": tag_list,
        "strategy_ids": sid_list,
        "tickers": tick_list,
        "metrics": dict(metrics or {}),
        "conclusions": concl,
        "next_steps": steps,
        "sections": list(section_map.keys()),
        "attachments": attach_paths,
        "author": author,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "folder": str(folder.as_posix()),
        "report_md": str(md_path.as_posix()),
        "bytes_md": len(report_md.encode("utf-8")),
    }
    meta_path = folder / "meta.json"
    meta_path.write_text(
        json.dumps(meta, indent=2, default=str, ensure_ascii=False), encoding="utf-8"
    )
    return meta


def _report_folder(report_id: str, root: Path) -> Path:
    rid = str(report_id or "").strip()
    if not rid:
        raise ValueError("report_id_required")
    candidate = Path(rid)
    if candidate.is_absolute() or "/" in rid or "\\" in rid:
        folder = candidate if candidate.is_dir() else candidate.parent
    else:
        folder = root / rid
    return _ensure_under(root, folder)


def _load_meta(folder: Path) -> dict[str, Any]:
    meta_path = folder / "meta.json"
    if not meta_path.is_file():
        return {"ok": True, "id": folder.name, "attachments": {}}
    return json.loads(meta_path.read_text(encoding="utf-8"))


def _save_meta(folder: Path, meta: dict[str, Any]) -> None:
    meta_path = folder / "meta.json"
    meta_path.write_text(
        json.dumps(meta, indent=2, default=str, ensure_ascii=False), encoding="utf-8"
    )


def begin_research_report(
    *,
    title: str,
    root: Path | str | None = None,
    kind: str = "research",
    summary: str | None = None,
    tags: Sequence[str] | None = None,
    strategy_ids: Sequence[str] | None = None,
    tickers: Sequence[str] | None = None,
    metrics: Mapping[str, Any] | None = None,
    author: str = "ai_analyst",
) -> dict[str, Any]:
    """Create a draft report folder; fill via append_research_report + finalize."""
    meta = write_research_report(
        title=title,
        body_markdown="_(draft — append sections next)_",
        root=root,
        kind=kind,
        summary=summary,
        tags=tags,
        strategy_ids=strategy_ids,
        tickers=tickers,
        metrics=metrics,
        author=author,
    )
    meta["status"] = "draft"
    _save_meta(Path(str(meta["folder"])), meta)
    return meta


def append_research_report(
    report_id: str,
    markdown_chunk: str,
    *,
    root: Path | str | None = None,
    section: str | None = None,
) -> dict[str, Any]:
    """Append a markdown chunk (optional ## section) to an existing report."""
    report_root = Path(root) if root is not None else DEFAULT_REPORT_ROOT
    folder = _report_folder(report_id, report_root)
    if not folder.is_dir():
        raise FileNotFoundError(f"report_not_found:{report_id}")
    chunk = str(markdown_chunk or "")
    if not chunk.strip():
        raise ValueError("markdown_chunk_required")
    if len(chunk) > 40_000:
        raise ValueError(f"chunk_too_large:{len(chunk)}>40000")
    md_path = folder / "report.md"
    existing = md_path.read_text(encoding="utf-8") if md_path.is_file() else ""
    # Drop placeholder draft line once real content arrives
    if "_(draft — append sections next)_" in existing:
        existing = existing.replace("_(draft — append sections next)_", "").rstrip() + "\n"
    parts = [existing.rstrip(), ""]
    if section and str(section).strip():
        parts.append(f"## {str(section).strip()}")
        parts.append("")
    parts.append(chunk.rstrip())
    parts.append("")
    new_body = "\n".join(parts)
    if len(new_body) > _MAX_BODY_CHARS:
        raise ValueError(f"body_too_large:{len(new_body)}>{_MAX_BODY_CHARS}")
    md_path.write_text(new_body, encoding="utf-8")
    meta = _load_meta(folder)
    meta["bytes_md"] = len(new_body.encode("utf-8"))
    meta["status"] = "draft"
    if section and str(section).strip():
        secs = list(meta.get("sections") or [])
        name = str(section).strip()
        if name not in secs:
            secs.append(name)
        meta["sections"] = secs
    _save_meta(folder, meta)
    return {
        "ok": True,
        "id": meta.get("id") or folder.name,
        "folder": str(folder.as_posix()),
        "bytes_md": meta["bytes_md"],
        "status": "draft",
        "appended_chars": len(chunk),
    }


def finalize_research_report(
    report_id: str,
    *,
    root: Path | str | None = None,
    conclusions: Sequence[str] | None = None,
    next_steps: Sequence[str] | None = None,
    attachments: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Mark report final; optionally append conclusions/next steps + JSON attachments."""
    report_root = Path(root) if root is not None else DEFAULT_REPORT_ROOT
    folder = _report_folder(report_id, report_root)
    if not folder.is_dir():
        raise FileNotFoundError(f"report_not_found:{report_id}")
    meta = _load_meta(folder)
    md_path = folder / "report.md"
    body = md_path.read_text(encoding="utf-8") if md_path.is_file() else ""
    concl = [str(c).strip() for c in (conclusions or []) if str(c).strip()]
    steps = [str(s).strip() for s in (next_steps or []) if str(s).strip()]
    extra_parts: list[str] = []
    if concl:
        extra_parts.append("## Conclusions")
        extra_parts.append("")
        for c in concl:
            extra_parts.append(f"- {c}")
        extra_parts.append("")
        meta["conclusions"] = list(meta.get("conclusions") or []) + concl
    if steps:
        extra_parts.append("## Next steps")
        extra_parts.append("")
        for s in steps:
            extra_parts.append(f"- {s}")
        extra_parts.append("")
        meta["next_steps"] = list(meta.get("next_steps") or []) + steps

    attach_dir = folder / "attachments"
    attach_dir.mkdir(parents=True, exist_ok=True)
    attach_paths = dict(meta.get("attachments") or {})
    if attachments:
        for key, payload in attachments.items():
            name = _safe_name(str(key), suffix=".json")
            path = attach_dir / name
            raw = json.dumps(payload, indent=2, default=str, ensure_ascii=False)
            if len(raw.encode("utf-8")) > _MAX_ATTACH_BYTES:
                raise ValueError(f"attachment_too_large:{name}")
            path.write_text(raw, encoding="utf-8")
            attach_paths[str(key)] = str(path.as_posix())
        if attach_paths:
            if "## Attachments" not in body:
                extra_parts.append("## Attachments")
                extra_parts.append("")
                for k, p in attach_paths.items():
                    extra_parts.append(f"- `{k}` → `{p}`")
                extra_parts.append("")
    if extra_parts:
        body = body.rstrip() + "\n\n" + "\n".join(extra_parts)
        md_path.write_text(body, encoding="utf-8")
    meta["attachments"] = attach_paths
    meta["bytes_md"] = len(body.encode("utf-8"))
    meta["status"] = "final"
    meta["finalized_at"] = datetime.now(timezone.utc).isoformat()
    _save_meta(folder, meta)
    return {
        "ok": True,
        "id": meta.get("id") or folder.name,
        "folder": str(folder.as_posix()),
        "report_md": str(md_path.as_posix()),
        "bytes_md": meta["bytes_md"],
        "status": "final",
        "attachments": attach_paths,
    }


def list_research_reports(
    *,
    root: Path | str | None = None,
    limit: int = 30,
    kind: str | None = None,
    strategy_id: str | None = None,
    tag: str | None = None,
) -> dict[str, Any]:
    report_root = Path(root) if root is not None else DEFAULT_REPORT_ROOT
    if not report_root.exists():
        return {"ok": True, "root": str(report_root.as_posix()), "reports": [], "count": 0}
    rows: list[dict[str, Any]] = []
    for child in report_root.iterdir():
        if not child.is_dir():
            continue
        meta_path = child / "meta.json"
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                meta = {"ok": False, "id": child.name, "error": str(exc)}
        else:
            md = child / "report.md"
            meta = {
                "ok": md.is_file(),
                "id": child.name,
                "title": child.name,
                "report_md": str(md.as_posix()) if md.is_file() else None,
                "generated_at": None,
            }
        if kind and str(meta.get("kind") or "") != str(kind):
            continue
        if strategy_id:
            sids = [str(x) for x in (meta.get("strategy_ids") or [])]
            if strategy_id not in sids and strategy_id not in str(meta.get("title") or ""):
                continue
        if tag:
            tags = [str(x) for x in (meta.get("tags") or [])]
            if tag not in tags:
                continue
        rows.append(
            {
                "id": meta.get("id") or child.name,
                "title": meta.get("title"),
                "kind": meta.get("kind"),
                "summary": meta.get("summary"),
                "strategy_ids": meta.get("strategy_ids") or [],
                "tickers": meta.get("tickers") or [],
                "tags": meta.get("tags") or [],
                "generated_at": meta.get("generated_at"),
                "folder": str(child.as_posix()),
                "report_md": meta.get("report_md") or str((child / "report.md").as_posix()),
            }
        )
    rows.sort(key=lambda r: str(r.get("generated_at") or r.get("id") or ""), reverse=True)
    lim = max(1, min(int(limit or 30), 200))
    clipped = rows[:lim]
    return {
        "ok": True,
        "root": str(report_root.as_posix()),
        "count": len(clipped),
        "total_matched": len(rows),
        "reports": clipped,
    }


def read_research_report(
    report_id: str,
    *,
    root: Path | str | None = None,
    include_body: bool = True,
    include_attachments: bool = False,
    max_body_chars: int = 120_000,
) -> dict[str, Any]:
    report_root = Path(root) if root is not None else DEFAULT_REPORT_ROOT
    rid = str(report_id or "").strip()
    if not rid:
        return {"ok": False, "error": "report_id_required"}
    # Accept bare id, folder name, or absolute/relative path under root
    candidate = Path(rid)
    if candidate.is_absolute() or "/" in rid or "\\" in rid:
        folder = candidate if candidate.is_dir() else candidate.parent
    else:
        folder = report_root / rid
    try:
        folder = _ensure_under(report_root, folder)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if not folder.is_dir():
        return {"ok": False, "error": "report_not_found", "id": rid}

    meta_path = folder / "meta.json"
    meta: dict[str, Any]
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            meta = {"ok": False, "error": f"meta_read_failed:{exc}"}
    else:
        meta = {"ok": True, "id": folder.name}

    out: dict[str, Any] = {
        "ok": True,
        "id": meta.get("id") or folder.name,
        "folder": str(folder.as_posix()),
        "meta": meta,
    }
    md_path = folder / "report.md"
    if include_body and md_path.is_file():
        body = md_path.read_text(encoding="utf-8")
        limit = max(1000, min(int(max_body_chars or 120_000), _MAX_BODY_CHARS))
        out["report_md"] = body[:limit]
        out["report_md_truncated"] = len(body) > limit
        out["report_md_chars"] = len(body)
    if include_attachments:
        attach_dir = folder / "attachments"
        loaded: dict[str, Any] = {}
        if attach_dir.is_dir():
            for path in sorted(attach_dir.glob("*.json")):
                try:
                    raw = path.read_text(encoding="utf-8")
                    if len(raw.encode("utf-8")) > _MAX_ATTACH_BYTES:
                        loaded[path.name] = {"error": "too_large", "bytes": len(raw)}
                    else:
                        loaded[path.name] = json.loads(raw)
                except Exception as exc:  # noqa: BLE001
                    loaded[path.name] = {"error": str(exc)}
        out["attachments"] = loaded
    return out


def collect_runtime_attachments(
    core: Any,
    *,
    include_last_walk_forward: bool = False,
    include_last_batch: bool = False,
    include_phantom_status: bool = False,
    include_ensemble_status: bool = False,
    include_clearance: bool = False,
    wf_report_paths: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Pull optional snapshots from live services for report attachments."""
    out: dict[str, Any] = {}
    if core is None:
        return out
    ph = None
    try:
        ph = core.service_registry.get("phantom_service")
    except Exception:
        ph = None

    if include_last_walk_forward and ph is not None:
        last = getattr(ph, "_last_walk_forward", None)
        if isinstance(last, Mapping):
            out["last_walk_forward"] = dict(last)

    if include_last_batch and ph is not None:
        last_run = getattr(ph, "_last_run", None)
        if isinstance(last_run, Mapping) and last_run.get("kind") == "walk_forward_batch":
            out["last_batch"] = {
                "tickers": last_run.get("tickers"),
                "holdout_bars": last_run.get("holdout_bars"),
                "variant_mode": last_run.get("variant_mode"),
                "rows": last_run.get("rows"),
                "errors": last_run.get("errors"),
            }
        elif getattr(ph, "_last_batch", None) is not None:
            out["last_batch_rows"] = list(getattr(ph, "_last_batch") or [])

    if include_phantom_status and ph is not None and hasattr(ph, "status"):
        try:
            st = ph.status()
            out["phantom_status"] = st if isinstance(st, Mapping) else {"raw": st}
        except Exception as exc:  # noqa: BLE001
            out["phantom_status"] = {"error": str(exc)}

    if include_ensemble_status:
        ens = None
        try:
            ens = core.service_registry.get("ensemble_service")
        except Exception:
            ens = None
        if ens is not None and hasattr(ens, "status"):
            try:
                st = ens.status()
                out["ensemble_status"] = st if isinstance(st, Mapping) else {"raw": st}
            except Exception as exc:  # noqa: BLE001
                out["ensemble_status"] = {"error": str(exc)}

    if include_clearance:
        try:
            from engine.research_gate import load_clearance

            out["research_clearance"] = load_clearance()
        except Exception as exc:  # noqa: BLE001
            out["research_clearance"] = {"error": str(exc)}

    if wf_report_paths:
        bundled: dict[str, Any] = {}
        for raw_path in wf_report_paths:
            p = Path(str(raw_path))
            if not p.is_file():
                bundled[str(raw_path)] = {"error": "not_found"}
                continue
            # Only allow under data/phantom or data/ai_reports
            try:
                resolved = p.resolve()
                ok_roots = [
                    (Path("data") / "phantom").resolve(),
                    (Path("data") / "ai_reports").resolve(),
                ]
                if not any(r == resolved or r in resolved.parents for r in ok_roots):
                    bundled[p.name] = {"error": "path_not_allowed"}
                    continue
                text = p.read_text(encoding="utf-8")
                if len(text.encode("utf-8")) > _MAX_ATTACH_BYTES:
                    bundled[p.name] = {"error": "too_large"}
                else:
                    bundled[p.name] = json.loads(text)
            except Exception as exc:  # noqa: BLE001
                bundled[p.name] = {"error": str(exc)}
        if bundled:
            out["wf_reports"] = bundled
    return out
