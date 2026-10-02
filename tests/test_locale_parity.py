"""Locale catalogues must not drift apart silently.

There are two hand-edited catalogues (`ui/i18n/locales` for the Python side and
`ui/web/src/locales` for Next.js) and no test compared them. Three failure modes
were live at the time of writing:

* **en/ru drift.** `tr()` falls back to English and then to the raw key, so a
  missing translation surfaces as a wall of English or a literal `desk.title` in
  the UI, and nothing noticed.
* **Broken interpolation.** If English has `{n}` and Russian only `{count}`, the
  Russian UI silently renders a stray placeholder or drops the value.
* **Contradiction on shared keys.** Seven keys had diverged, and the desktop copy
  was the wrong one: it told the operator that Apply "writes into Engine config"
  — i.e. that credentials land in YAML — after secrets moved to an encrypted
  SQLite store, and it named Binance where the configured venue is Bitget. An
  operator relying on that text would form a materially wrong belief about where
  their credentials are stored.

The placeholders were checked and are already consistent; these tests keep them
that way and keep the two catalogues from disagreeing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PLACEHOLDER = re.compile(r"\{([a-zA-Z0-9_]+)\}")

CATALOGUES = {
    "desktop": (
        REPO / "ui" / "i18n" / "locales" / "en.json",
        REPO / "ui" / "i18n" / "locales" / "ru.json",
    ),
    "web": (
        REPO / "ui" / "web" / "src" / "locales" / "en.json",
        REPO / "ui" / "web" / "src" / "locales" / "ru.json",
    ),
}

#: Keys allowed to differ between the two catalogues because they describe
#: different surfaces (the web app has features the Python shell does not, and
#: vice versa). Anything not listed must be identical or be reported.
ALLOWED_DIFFERENCES: frozenset[str] = frozenset()


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(CATALOGUES))
def test_catalogues_are_valid_json_objects(name: str) -> None:
    en_p, ru_p = CATALOGUES[name]
    for path in (en_p, ru_p):
        data = _load(path)
        assert isinstance(data, dict), f"{path.name} must be a flat object"
        assert data, f"{path} is empty"
        assert all(isinstance(k, str) and isinstance(v, str) for k, v in data.items())


@pytest.mark.parametrize("name", sorted(CATALOGUES))
def test_english_and_russian_have_identical_key_sets(name: str) -> None:
    """A key present in one language and missing in the other shows up raw."""
    en_p, ru_p = CATALOGUES[name]
    en, ru = _load(en_p), _load(ru_p)
    assert set(en) == set(ru), (
        f"{name}: en-only={sorted(set(en) - set(ru))} ru-only={sorted(set(ru) - set(en))}"
    )


@pytest.mark.parametrize("name", sorted(CATALOGUES))
def test_no_placeholder_only_in_one_language(name: str) -> None:
    """`{n}` in English and only `{count}` in Russian silently breaks formatting."""
    en_p, ru_p = CATALOGUES[name]
    en, ru = _load(en_p), _load(ru_p)
    mismatches = []
    for key in sorted(set(en) & set(ru)):
        a = set(PLACEHOLDER.findall(en[key]))
        b = set(PLACEHOLDER.findall(ru[key]))
        if a != b:
            mismatches.append(f"{key}: en={sorted(a)} ru={sorted(b)}")
    assert not mismatches, f"{name} placeholder mismatches:\n" + "\n".join(mismatches)


def test_shared_keys_agree_between_desktop_and_web() -> None:
    """The two catalogues describe one product and must not contradict.

    `keys.intro` is the reason this test exists: the desktop text claimed
    Apply writes secrets into the engine YAML config, which stopped being true
    when secrets moved to an encrypted SQLite store.
    """
    desktop_en = _load(CATALOGUES["desktop"][0])
    web_en = _load(CATALOGUES["web"][0])
    disagreements = {
        key: (desktop_en[key], web_en[key])
        for key in sorted(set(desktop_en) & set(web_en))
        if desktop_en[key] != web_en[key] and key not in ALLOWED_DIFFERENCES
    }
    assert not disagreements, "desktop and web disagree on shared keys:\n" + "\n".join(
        f"  {k}\n    desktop: {a!r}\n    web:     {b!r}"
        for k, (a, b) in disagreements.items()
    )


def test_desktop_and_web_share_placeholders_on_common_keys() -> None:
    desktop_en = _load(CATALOGUES["desktop"][0])
    web_en = _load(CATALOGUES["web"][0])
    mismatches = []
    for key in sorted(set(desktop_en) & set(web_en)):
        a = set(PLACEHOLDER.findall(desktop_en[key]))
        b = set(PLACEHOLDER.findall(web_en[key]))
        if a != b:
            mismatches.append(f"{key}: desktop={sorted(a)} web={sorted(b)}")
    assert not mismatches, "\n".join(mismatches)


def test_secret_storage_wording_matches_the_implementation() -> None:
    """Guards the specific fact that was wrong and is security-relevant.

    Credentials are encrypted at rest (`host/secret_store.py`: DPAPI on Windows,
    AES-256-GCM elsewhere) and stored in the SQLite `operator_secrets` table.
    Any UI copy claiming they are written to YAML is misleading.
    """
    for name, (en_p, _ru_p) in CATALOGUES.items():
        text = _load(en_p).get("keys.intro", "")
        assert "YAML" not in text or "not in YAML" in text, (
            f"{name}: keys.intro still claims secrets land in YAML: {text!r}"
        )
        assert "operator_secrets" in text, (
            f"{name}: keys.intro should name the real store, got {text!r}"
        )


def test_venue_named_in_help_matches_the_configured_exchange() -> None:
    """`keys.alpaca_help` said Binance; the configured CCXT venue is Bitget."""
    config = (REPO / "config" / "trading_bot.yaml").read_text(encoding="utf-8")
    venue = "BITGET" if "BITGET" in config.upper() else None
    assert venue, "expected Bitget to be the configured venue"
    for name, (en_p, _ru_p) in CATALOGUES.items():
        help_text = _load(en_p).get("keys.alpaca_help", "")
        assert "Binance" not in help_text, (
            f"{name}: keys.alpaca_help still names a venue that is not configured"
        )
        assert "Bitget" in help_text


def test_python_ui_tr_keys_all_exist() -> None:
    """`tr()` returns the raw key on a miss, so a typo renders as `desk.title`."""
    import re as _re

    catalogue = set(_load(CATALOGUES["desktop"][0]))
    call = _re.compile(r"""\btr\(\s*['"]([a-zA-Z0-9_.]+)['"]""")
    used: dict[str, set[str]] = {}
    for path in (REPO / "ui").rglob("*.py"):
        if "node_modules" in str(path):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in call.finditer(text):
            used.setdefault(match.group(1), set()).add(str(path.relative_to(REPO)))
    missing = {k: v for k, v in used.items() if k not in catalogue}
    assert not missing, f"keys missing from the desktop catalogue: {missing}"


def test_identical_en_ru_values_are_only_untranslatable_tokens() -> None:
    """Guards the equal-value keys: they must stay a short, explainable list.

    API field names, indicator abbreviations, units and provider ids are
    legitimately identical in both languages. Anything else equal is a string
    that simply was never translated, so the allow-list is explicit rather than
    "whichever happens to match today".
    """
    from ui.i18n import _load_json  # noqa: PLC0415

    allowed = {
        # API / config field names
        "ai.api_key", "ai.base_url", "ai.max_tokens", "ai.temperature",
        "sys.storage.backend_path", "sys.storage.backend_pick",
        "sys.storage.backend_url", "sys.storage.health_fmt",
        "sys.storage.sink_fmt", "sys.notify.tg_chat", "sys.notify.tg_token",
        # indicator abbreviations and units
        "chart.ind.bottom.macd", "chart.ind.bottom.rsi", "chart.ind.ema",
        "chart.ind.sma", "strat.metric.z",
        # language menu labels
        "menu.lang.en", "menu.lang.ru",
        # providers / venues
        "ph.provider.moex", "ph.provider.yahoo",
        # column headers and abbreviations
        "desk.ord.id", "desk.pos.pnl", "panel.equity", "sys.col.id",
        "desk.state.off", "chart.follow",
        # long-form strings that carry no prose to translate
        "ai.drawer.ai", "ai.drawer.rail", "ai.drawer.tools",
        "ai.drawer.tools_cb", "ai.drawer.status_fmt",
        "strat.close_line", "strat.marker.buy", "strat.marker.sell",
        "strat.mode.observe", "strat.status.observe",
        "strat.overlay.band_lo", "strat.overlay.band_up",
        "ph.chart_variant", "ph.kelly_fraction", "ph.reward_risk",
        "ph.win_rate", "ph.state.pnl", "ph.batch.result", "ph.wf.result",
        "ph.wf.seed", "ph.wf.top_k", "ph.wf_title",
        # "Batch WF" / "Walk-forward" — jargon kept verbatim in both languages
        "ph.run_batch",
        "ph.symbol_search_hint", "picker.count",
        "sys.notify.channel_telegram", "sys.notify.toast.ops",
        "sys.notify.toast.phantom", "sys.notify.toast.recon",
        "sys.tab.ai",
    }
    en = _load_json("en")
    ru = _load_json("ru")
    assert isinstance(en, dict) and isinstance(ru, dict)
    identical = {k for k in set(en) & set(ru) if en[k] == ru[k]}
    unexpected = identical - allowed
    assert not unexpected, (
        f"new untranslated string(s); either translate them or add to the "
        f"allow-list with a reason: {sorted(unexpected)}"
    )