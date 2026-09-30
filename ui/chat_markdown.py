"""Lightweight markdown → chat blocks (plain text structure).

Emphasis markers are stripped to plain content; structure (headings / lists /
code / tables) is preserved as separate wrapped text rows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
# Asterisk italic only — underscore form destroys snake_case (exit_pending → exitpending).
_ITALIC = re.compile(r"(?<!\*)\*(?!\*)([^*\n]+?)(?<!\*)\*(?!\*)")
_CODE_INLINE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_STRIKE = re.compile(r"~~(.+?)~~")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_UL = re.compile(r"^(\s*)([-*+])\s+(.*)$")
_OL = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_HR = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_FENCE = re.compile(r"^```")
# Space-aligned columns: "ColA          ColB" or "#  Пробел     Предложение"
_SPACE_COLS = re.compile(r"\S+\s{2,}\S+")


@dataclass(frozen=True)
class MdBlock:
    kind: str  # heading | paragraph | list | code | quote | hr | table
    text: str
    level: int = 0


def inline_plain(text: str) -> str:
    """Strip common inline markdown markers, keep readable content."""
    s = text or ""
    s = _LINK.sub(r"\1", s)
    s = _BOLD.sub(lambda m: m.group(1) or m.group(2) or "", s)
    s = _ITALIC.sub(r"\1", s)
    s = _CODE_INLINE.sub(r"\1", s)
    s = _STRIKE.sub(r"\1", s)
    return s.replace("\u00a0", " ").strip() if s else ""


def _split_space_cols(line: str) -> list[str]:
    return [inline_plain(c) for c in re.split(r"\s{2,}", line.strip()) if c.strip()]


def _format_cells(parsed: list[list[str]]) -> str:
    if not parsed:
        return ""
    width = max(len(r) for r in parsed)
    for r in parsed:
        while len(r) < width:
            r.append("")
    cols = [max(len(r[i]) for r in parsed) for i in range(width)]
    lines = []
    for r in parsed:
        lines.append("  ".join(r[i].ljust(cols[i]) for i in range(width)).rstrip())
    return "\n".join(lines)


def _format_pipe_table(rows: list[str]) -> str:
    parsed: list[list[str]] = []
    for row in rows:
        cells = [inline_plain(c.strip()) for c in row.strip().strip("|").split("|")]
        if cells and all(re.fullmatch(r":?-{3,}:?", c or "") for c in cells):
            continue  # alignment row
        parsed.append(cells)
    return _format_cells(parsed)


def _format_space_table(rows: list[str]) -> str:
    return _format_cells([_split_space_cols(r) for r in rows if r.strip()])


def _looks_like_space_table_row(line: str) -> bool:
    s = line.rstrip()
    if not s.strip():
        return False
    return bool(_SPACE_COLS.search(s))


def _atx_heading_match(line: str) -> re.Match[str] | None:
    hm = _HEADING.match(line)
    if not hm:
        return None
    # "#  ColA        ColB" is a space-table header, not a markdown heading.
    if _SPACE_COLS.search(hm.group(2) or ""):
        return None
    return hm


def parse_markdown_blocks(text: str) -> list[MdBlock]:
    """Split markdown into structural blocks for the chat renderer."""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not raw.strip():
        return []

    blocks: list[MdBlock] = []
    lines = raw.split("\n")
    i = 0
    para: list[str] = []
    pipe_table: list[str] = []
    space_table: list[str] = []

    def flush_para() -> None:
        nonlocal para
        if not para:
            return
        body = inline_plain(" ".join(p.strip() for p in para if p.strip()))
        if body:
            blocks.append(MdBlock("paragraph", body))
        para = []

    def flush_pipe_table() -> None:
        nonlocal pipe_table
        if not pipe_table:
            return
        formatted = _format_pipe_table(pipe_table)
        if formatted:
            blocks.append(MdBlock("table", formatted))
        pipe_table = []

    def flush_space_table() -> None:
        nonlocal space_table
        if not space_table:
            return
        formatted = _format_space_table(space_table)
        if formatted:
            blocks.append(MdBlock("table", formatted))
        space_table = []

    while i < len(lines):
        line = lines[i]

        if _FENCE.match(line.strip()):
            flush_para()
            flush_pipe_table()
            flush_space_table()
            i += 1
            buf: list[str] = []
            while i < len(lines) and not _FENCE.match(lines[i].strip()):
                buf.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1
            code = "\n".join(buf).rstrip()
            if code:
                blocks.append(MdBlock("code", code))
            continue

        if "|" in line and line.strip().startswith("|"):
            flush_para()
            flush_space_table()
            pipe_table.append(line)
            i += 1
            continue
        if pipe_table:
            flush_pipe_table()

        if _looks_like_space_table_row(line):
            flush_para()
            space_table.append(line)
            i += 1
            continue
        if space_table:
            flush_space_table()

        if _HR.match(line):
            flush_para()
            blocks.append(MdBlock("hr", ""))
            i += 1
            continue

        hm = _atx_heading_match(line)
        if hm:
            flush_para()
            level = len(hm.group(1))
            title = inline_plain(hm.group(2))
            if title:
                blocks.append(MdBlock("heading", title, level=level))
            i += 1
            continue

        um = _UL.match(line)
        if um:
            flush_para()
            indent = len(um.group(1) or "") // 2
            item = inline_plain(um.group(3))
            if item:
                blocks.append(MdBlock("list", item, level=indent))
            i += 1
            continue

        om = _OL.match(line)
        if om:
            flush_para()
            indent = len(om.group(1) or "") // 2
            item = f"{om.group(2)}. {inline_plain(om.group(3))}"
            if item.strip():
                blocks.append(MdBlock("list", item, level=indent))
            i += 1
            continue

        if line.startswith(">"):
            flush_para()
            quote = inline_plain(line.lstrip("> ").strip())
            if quote:
                blocks.append(MdBlock("quote", quote))
            i += 1
            continue

        if not line.strip():
            flush_para()
            i += 1
            continue

        para.append(line)
        i += 1

    flush_para()
    flush_pipe_table()
    flush_space_table()
    return blocks
