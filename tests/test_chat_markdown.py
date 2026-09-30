"""Chat markdown flattening for Operator AI drawer."""

from __future__ import annotations

from ui.chat_markdown import inline_plain, parse_markdown_blocks


def test_inline_strips_bold_and_code() -> None:
    assert inline_plain("**Инструмент исполнения.**") == "Инструмент исполнения."
    assert inline_plain("use `halt` now") == "use halt now"
    assert inline_plain("[docs](https://x.test)") == "docs"


def test_inline_keeps_snake_case() -> None:
    s = (
        "exit_pending phantom_pnl get_strategy_stats "
        "read_cost_schedule max_leverage=1.0 reconciliation_healthy=false"
    )
    assert inline_plain(s) == s


def test_inline_asterisk_italic_still_works() -> None:
    assert inline_plain("это *важно* сейчас") == "это важно сейчас"


def test_parse_heading_list_paragraph() -> None:
    md = "# Title\n\n**Bold intro**\n\n- one\n- two\n\nTail."
    blocks = parse_markdown_blocks(md)
    kinds = [b.kind for b in blocks]
    assert kinds == ["heading", "paragraph", "list", "list", "paragraph"]
    assert blocks[0].text == "Title"
    assert blocks[1].text == "Bold intro"
    assert blocks[2].text == "one"
    assert blocks[-1].text == "Tail."


def test_parse_code_fence_and_table() -> None:
    md = "```\nprint(1)\n```\n\n| A | B |\n|---|---|\n| 1 | 2 |\n"
    blocks = parse_markdown_blocks(md)
    assert any(b.kind == "code" and "print(1)" in b.text for b in blocks)
    table = next(b for b in blocks if b.kind == "table")
    assert "A" in table.text and "2" in table.text


def test_space_aligned_table_not_heading() -> None:
    md = (
        "Что улучшить\n\n"
        "#  Пробел                  Предложение\n"
        "1  Леверидж 130x           Проверить max_leverage\n"
        "2  exit_pending            Добавить таймаут\n"
    )
    blocks = parse_markdown_blocks(md)
    assert not any(b.kind == "heading" for b in blocks)
    table = next(b for b in blocks if b.kind == "table")
    assert "Пробел" in table.text
    assert "exit_pending" in table.text
    assert "max_leverage" in table.text
