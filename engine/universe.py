"""Universe selection — catalog → candidates → active book.

Liquidity ranking is only ONE screen profile. Vol / futures / options need
different rank keys and sometimes a different instrument model (option chains).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Iterable, Sequence


def normalize_universe_mode(mode: str | None) -> str:
    """Map Operator UI / legacy aliases to engine modes.

    Engine modes: ``manual`` | ``screener`` | ``hybrid``.
    UI historically sent ``static`` / ``screen``.
    """
    raw = str(mode or "manual").strip().lower()
    aliases = {
        "static": "manual",
        "manual": "manual",
        "screen": "screener",
        "screener": "screener",
        "hybrid": "hybrid",
    }
    return aliases.get(raw, "manual" if raw not in {"manual", "screener", "hybrid"} else raw)


# Built-in screen profiles (YAML can override fields).
# rank_by = what to maximize after coarse floors pass.
BUILTIN_PROFILES: dict[str, dict[str, Any]] = {
    # Classic QC/Zipline liquid universe
    "liquid": {
        "rank_by": "dollar_volume",
        "min_dollar_volume": 1_000_000.0,
        "min_price": 1.0,
        "asset_classes": (),
    },
    # Vol play: need SOME tradability floor, then rank by realized/ATR vol
    "volatility": {
        "rank_by": "atr_pct",
        "min_dollar_volume": 500_000.0,  # floor, not the objective
        "min_atr_pct": 0.01,
        "min_price": 0.0,
        "asset_classes": (),
    },
    # Speculative directional: prefer futures (costs/leverage) over cash equity
    "futures_speculation": {
        "rank_by": "dollar_volume",
        "min_dollar_volume": 1_000_000.0,
        "asset_classes": ("future",),
    },
    # Crypto liquid book
    "crypto_liquid": {
        "rank_by": "dollar_volume",
        "min_dollar_volume": 5_000_000.0,
        "asset_classes": ("crypto",),
    },
    # Options: screen underlyings first; chain selection is a later stage
    "options_underlying": {
        "rank_by": "iv_rank",
        "min_dollar_volume": 1_000_000.0,
        "min_option_oi": 1000.0,
        "asset_classes": ("equity", "etf", "future", "index"),
    },
}


@dataclass(slots=True)
class Instrument:
    """Normalized instrument row for screening.

    Options contracts may use asset_class=\"option\" with underlying in meta.
    """

    symbol: str
    class_code: str = ""
    asset_class: str = "equity"  # equity | future | crypto | etf | option | index | other
    exchange: str = ""
    price: float = 0.0
    dollar_volume: float = 0.0
    volume: float = 0.0
    # Strategy-relevant metrics (filled by catalog feed / history job)
    atr_pct: float = 0.0  # ATR / price
    realized_vol: float = 0.0  # e.g. 20d annualized
    iv_rank: float = 0.0  # 0..100 for options underlyings
    option_open_interest: float = 0.0
    bid_ask_pct: float = 0.0  # relative spread; lower is better when filtering
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        if self.class_code:
            return f"{self.class_code}:{self.symbol}"
        return self.symbol

    def metric(self, name: str) -> float:
        if name == "dollar_volume":
            return self.dollar_volume
        if name == "atr_pct":
            return self.atr_pct
        if name == "realized_vol":
            return self.realized_vol
        if name == "iv_rank":
            return self.iv_rank
        if name == "option_open_interest":
            return self.option_open_interest
        if name == "volume":
            return self.volume
        if name == "price":
            return self.price
        # prefer higher = negate spread when used as rank (see rank_score)
        if name == "neg_bid_ask_pct":
            return -self.bid_ask_pct
        return float(self.meta.get(name, 0) or 0)


@dataclass(slots=True)
class UniverseSnapshot:
    """Result of one selection pass."""

    catalog: list[Instrument]
    candidates: list[Instrument]
    active: list[Instrument]
    mode: str
    reason: str = ""
    profile: str = ""
    rank_by: str = ""
    books: dict[str, list[str]] = field(default_factory=dict)
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))

    def active_keys(self) -> list[str]:
        return [i.key for i in self.active]

    def active_symbols(self) -> list[str]:
        return [i.symbol for i in self.active]

    def to_payload(self) -> dict[str, Any]:
        def _row(i: Instrument) -> dict[str, Any]:
            return {
                "symbol": i.symbol,
                "class_code": i.class_code,
                "asset_class": i.asset_class,
                "exchange": i.exchange,
                "price": i.price,
                "dollar_volume": i.dollar_volume,
                "volume": i.volume,
                "atr_pct": i.atr_pct,
                "realized_vol": i.realized_vol,
                "iv_rank": i.iv_rank,
                "option_open_interest": i.option_open_interest,
                "bid_ask_pct": i.bid_ask_pct,
                "key": i.key,
            }

        return {
            "mode": self.mode,
            "profile": self.profile,
            "rank_by": self.rank_by,
            "reason": self.reason,
            "ts": self.ts.isoformat(),
            "counts": {
                "catalog": len(self.catalog),
                "candidates": len(self.candidates),
                "active": len(self.active),
            },
            "active": [_row(i) for i in self.active],
            "candidates": [_row(i) for i in self.candidates],
            "active_keys": self.active_keys(),
            "books": dict(self.books),
        }


@dataclass(slots=True)
class ScreenPolicy:
    """
    How to turn catalog into candidates/active.

    Floors (min_*) are tradability constraints; rank_by is the objective.
    Vol strategies still keep a liquidity floor — they do not rank pure illiquids.
    """

    name: str = "liquid"
    rank_by: str = "dollar_volume"
    max_candidates: int = 100
    max_active: int = 20
    min_price: float = 0.0
    min_dollar_volume: float = 0.0
    min_atr_pct: float = 0.0
    min_realized_vol: float = 0.0
    min_iv_rank: float = 0.0
    min_option_oi: float = 0.0
    max_bid_ask_pct: float = 0.0  # 0 = disabled
    asset_classes: tuple[str, ...] = ()
    prefer_previous_active: bool = True


@dataclass(slots=True)
class UniverseConfig:
    mode: str = "manual"  # manual | screener
    screen: ScreenPolicy = field(default_factory=ScreenPolicy)
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    static_active: tuple[str, ...] = ()
    # Optional: named profiles for multi-strategy union (see merge_active_books)
    profiles: dict[str, ScreenPolicy] = field(default_factory=dict)


def _norm_key(raw: str) -> str:
    return raw.strip().upper()


def _matches(inst: Instrument, token: str) -> bool:
    t = _norm_key(token)
    return t in {_norm_key(inst.key), _norm_key(inst.symbol)}


def instrument_from_dict(row: dict[str, Any]) -> Instrument:
    meta = dict(row.get("meta") or {})
    return Instrument(
        symbol=str(row["symbol"]),
        class_code=str(row.get("class_code", "") or ""),
        asset_class=str(row.get("asset_class", "equity") or "equity"),
        exchange=str(row.get("exchange", "") or ""),
        price=float(row.get("price", 0) or 0),
        dollar_volume=float(row.get("dollar_volume", 0) or 0),
        volume=float(row.get("volume", 0) or 0),
        atr_pct=float(row.get("atr_pct", meta.get("atr_pct", 0)) or 0),
        realized_vol=float(row.get("realized_vol", meta.get("realized_vol", 0)) or 0),
        iv_rank=float(row.get("iv_rank", meta.get("iv_rank", 0)) or 0),
        option_open_interest=float(
            row.get("option_open_interest", meta.get("option_open_interest", 0)) or 0
        ),
        bid_ask_pct=float(row.get("bid_ask_pct", meta.get("bid_ask_pct", 0)) or 0),
        meta=meta,
    )


def resolve_screen_policy(raw: dict[str, Any] | None) -> ScreenPolicy:
    """Merge builtin profile + YAML `screen` / legacy `limits` block."""
    raw = raw or {}
    profile_name = str(raw.get("profile") or raw.get("screen_profile") or "liquid")
    base = dict(BUILTIN_PROFILES.get(profile_name, BUILTIN_PROFILES["liquid"]))
    screen_raw = dict(raw.get("screen") or {})
    # Legacy: limits: { min_price, max_active, ... }
    lim_raw = dict(raw.get("limits") or {})
    merged = {**base, **lim_raw, **screen_raw}
    if "profile" in raw or "screen_profile" in raw:
        merged["name"] = profile_name
    elif "name" in screen_raw:
        merged["name"] = str(screen_raw["name"])
    else:
        merged["name"] = profile_name

    asset_classes = merged.get("asset_classes") or ()
    if isinstance(asset_classes, str):
        asset_classes = [asset_classes]

    return ScreenPolicy(
        name=str(merged.get("name", profile_name)),
        rank_by=str(merged.get("rank_by", "dollar_volume")),
        max_candidates=int(merged.get("max_candidates", 100)),
        max_active=int(merged.get("max_active", 20)),
        min_price=float(merged.get("min_price", 0) or 0),
        min_dollar_volume=float(merged.get("min_dollar_volume", 0) or 0),
        min_atr_pct=float(merged.get("min_atr_pct", 0) or 0),
        min_realized_vol=float(merged.get("min_realized_vol", 0) or 0),
        min_iv_rank=float(merged.get("min_iv_rank", 0) or 0),
        min_option_oi=float(merged.get("min_option_oi", 0) or 0),
        max_bid_ask_pct=float(merged.get("max_bid_ask_pct", 0) or 0),
        asset_classes=tuple(str(x) for x in asset_classes),
        prefer_previous_active=bool(merged.get("prefer_previous_active", True)),
    )


class UniverseManager:
    """
    Pure selection logic (no I/O, no EventBus).

    Screener: coarse floors → rank by screen.rank_by → top max_active.
    """

    def __init__(self, config: UniverseConfig | None = None) -> None:
        self.config = config or UniverseConfig()
        self._previous_active_keys: set[str] = set()
        self._last: UniverseSnapshot | None = None

    @property
    def last(self) -> UniverseSnapshot | None:
        return self._last

    def select(self, catalog: Sequence[Instrument]) -> UniverseSnapshot:
        cfg = self.config
        catalog_list = list(catalog)
        pool = [i for i in catalog_list if not self._is_excluded(i)]
        mode = normalize_universe_mode(cfg.mode)
        # hybrid = screener + static_active treated as pinned includes
        include_tokens = list(cfg.include)
        if mode == "hybrid":
            include_tokens = list(
                dict.fromkeys([*list(cfg.static_active), *include_tokens])
            )

        if mode == "manual":
            active = self._resolve_manual(pool)
            snap = UniverseSnapshot(
                catalog=catalog_list,
                candidates=list(active),
                active=active,
                mode="manual",
                reason="static_or_include",
                profile="manual",
                rank_by="",
            )
            self._commit(snap)
            return snap

        # Per-strategy books → union for market subscriptions.
        if cfg.profiles:
            books: list[UniverseSnapshot] = []
            book_keys: dict[str, list[str]] = {}
            for name, policy in cfg.profiles.items():
                book = self._screen_book(pool, policy)
                books.append(book)
                book_keys[str(name)] = book.active_keys()
            candidates = self._dedupe([c for book in books for c in book.candidates])
            active = self.merge_active_books(*books)
            active = self._merge_include_tokens(active, catalog_list, include_tokens)
            union_cap = ScreenPolicy(
                name="union",
                max_active=sum(max(1, p.max_active) for p in cfg.profiles.values())
                or max(1, cfg.screen.max_active),
            )
            active = self._finalize_active(active, union_cap)
            names = "+".join(str(n) for n in cfg.profiles.keys())
            rank_by = ",".join(sorted({p.rank_by for p in cfg.profiles.values()}))
            snap = UniverseSnapshot(
                catalog=catalog_list,
                candidates=candidates,
                active=active,
                mode="screener" if mode != "hybrid" else "hybrid",
                reason=f"union:{names}",
                profile="union",
                rank_by=rank_by,
                books=book_keys,
            )
            self._commit(snap)
            return snap

        snap = self._screen_book(pool, cfg.screen)
        active = self._merge_include_tokens(
            list(snap.active), catalog_list, include_tokens
        )
        active = self._finalize_active(active, cfg.screen)
        snap = UniverseSnapshot(
            catalog=catalog_list,
            candidates=snap.candidates,
            active=active,
            mode="hybrid" if mode == "hybrid" else "screener",
            reason=snap.reason if mode != "hybrid" else f"hybrid:{snap.reason}",
            profile=snap.profile,
            rank_by=snap.rank_by,
        )
        self._commit(snap)
        return snap

    def _screen_book(
        self, pool: Sequence[Instrument], screen: ScreenPolicy
    ) -> UniverseSnapshot:
        """One screener pass without include/pin merge (used by single + union)."""
        filtered = [i for i in pool if self._passes_coarse(i, screen)]
        filtered.sort(key=lambda i: i.metric(screen.rank_by), reverse=True)
        max_cand = max(1, screen.max_candidates)
        candidates = filtered[:max_cand]
        active = self._pick_active(candidates, screen)
        return UniverseSnapshot(
            catalog=[],
            candidates=candidates,
            active=active,
            mode="screener",
            reason=f"profile:{screen.name}:rank:{screen.rank_by}",
            profile=screen.name,
            rank_by=screen.rank_by,
        )

    def select_with_policy(
        self, catalog: Sequence[Instrument], screen: ScreenPolicy
    ) -> UniverseSnapshot:
        """Run screener with an explicit policy (per-strategy book)."""
        catalog_list = list(catalog)
        pool = [i for i in catalog_list if not self._is_excluded(i)]
        book = self._screen_book(pool, screen)
        active = self._merge_include(list(book.active), catalog_list)
        active = self._finalize_active(active, screen)
        snap = UniverseSnapshot(
            catalog=catalog_list,
            candidates=book.candidates,
            active=active,
            mode="screener",
            reason=book.reason,
            profile=book.profile,
            rank_by=book.rank_by,
        )
        self._commit(snap)
        return snap

    @staticmethod
    def merge_active_books(*snapshots: UniverseSnapshot) -> list[Instrument]:
        """Union of strategy-specific actives → global market subscriptions."""
        seen: set[str] = set()
        out: list[Instrument] = []
        for snap in snapshots:
            for inst in snap.active:
                if inst.key in seen:
                    continue
                seen.add(inst.key)
                out.append(inst)
        return out

    def _commit(self, snap: UniverseSnapshot) -> None:
        self._last = snap
        self._previous_active_keys = {i.key for i in snap.active}

    def _is_excluded(self, inst: Instrument) -> bool:
        return any(_matches(inst, t) for t in self.config.exclude)

    def _passes_coarse(self, inst: Instrument, screen: ScreenPolicy) -> bool:
        if screen.asset_classes and inst.asset_class not in screen.asset_classes:
            return False
        if inst.price < screen.min_price:
            return False
        if inst.dollar_volume < screen.min_dollar_volume:
            return False
        if inst.atr_pct < screen.min_atr_pct:
            return False
        if inst.realized_vol < screen.min_realized_vol:
            return False
        if inst.iv_rank < screen.min_iv_rank:
            return False
        if inst.option_open_interest < screen.min_option_oi:
            return False
        if screen.max_bid_ask_pct > 0 and inst.bid_ask_pct > screen.max_bid_ask_pct:
            return False
        return True

    def _resolve_manual(self, pool: Sequence[Instrument]) -> list[Instrument]:
        tokens: list[str] = list(self.config.static_active) + list(self.config.include)
        if not tokens:
            return []
        found: list[Instrument] = []
        for token in tokens:
            match = next((i for i in pool if _matches(i, token)), None)
            if match is not None:
                found.append(match)
            else:
                if ":" in token:
                    cc, sym = token.split(":", 1)
                else:
                    cc, sym = "", token
                found.append(
                    Instrument(symbol=sym.upper(), class_code=cc.upper() if cc else "")
                )
        return self._dedupe(found)[: max(1, self.config.screen.max_active)]

    def _pick_active(
        self, candidates: Sequence[Instrument], screen: ScreenPolicy
    ) -> list[Instrument]:
        max_active = max(1, screen.max_active)
        if not screen.prefer_previous_active or not self._previous_active_keys:
            return list(candidates[:max_active])
        kept = [i for i in candidates if i.key in self._previous_active_keys]
        rest = [i for i in candidates if i.key not in self._previous_active_keys]
        return (kept + rest)[:max_active]

    def _merge_include(
        self, active: list[Instrument], catalog: Sequence[Instrument]
    ) -> list[Instrument]:
        return self._merge_include_tokens(
            active, catalog, list(self.config.include)
        )

    def _merge_include_tokens(
        self,
        active: list[Instrument],
        catalog: Sequence[Instrument],
        tokens: Sequence[str],
    ) -> list[Instrument]:
        out = list(active)
        for token in tokens:
            if any(_matches(i, token) for i in out):
                continue
            match = next((i for i in catalog if _matches(i, token)), None)
            if match is not None:
                out.insert(0, match)
            else:
                if ":" in token:
                    cc, sym = token.split(":", 1)
                else:
                    cc, sym = "", token
                out.insert(
                    0,
                    Instrument(symbol=sym.upper(), class_code=cc.upper() if cc else ""),
                )
        return out

    def _finalize_active(
        self, active: list[Instrument], screen: ScreenPolicy
    ) -> list[Instrument]:
        """Keep every include/pin; fill remaining slots up to max_active."""
        active = self._dedupe(active)
        max_active = max(1, screen.max_active)
        include = list(self.config.include)
        if normalize_universe_mode(self.config.mode) == "hybrid":
            include = list(
                dict.fromkeys([*list(self.config.static_active), *include])
            )
        if not include:
            return active[:max_active]
        pinned: list[Instrument] = []
        rest: list[Instrument] = []
        for inst in active:
            if any(_matches(inst, t) for t in include):
                pinned.append(inst)
            else:
                rest.append(inst)
        room = max(0, max_active - len(pinned))
        return pinned + rest[:room]

    @staticmethod
    def _dedupe(items: Iterable[Instrument]) -> list[Instrument]:
        seen: set[str] = set()
        out: list[Instrument] = []
        for i in items:
            if i.key in seen:
                continue
            seen.add(i.key)
            out.append(i)
        return out

    @classmethod
    def from_mapping(cls, raw: dict[str, Any] | None) -> UniverseManager:
        raw = raw or {}
        screen = resolve_screen_policy(raw)
        profiles_raw = raw.get("profiles") or {}
        profiles: dict[str, ScreenPolicy] = {}
        known = {"profile", "screen_profile", "screen", "limits"}
        for name, prow in profiles_raw.items():
            if not isinstance(prow, dict):
                continue
            row = dict(prow)
            extras = {k: row.pop(k) for k in list(row) if k not in known}
            screen_block = dict(row.get("screen") or {})
            screen_block.update(extras)
            row["screen"] = screen_block
            if "profile" not in row and "screen_profile" not in row:
                row["profile"] = str(name)
            profiles[str(name)] = resolve_screen_policy(row)
        cfg = UniverseConfig(
            mode=normalize_universe_mode(str(raw.get("mode", "manual"))),
            screen=screen,
            include=tuple(str(x) for x in (raw.get("include") or [])),
            exclude=tuple(str(x) for x in (raw.get("exclude") or [])),
            static_active=tuple(str(x) for x in (raw.get("static_active") or [])),
            profiles=profiles,
        )
        return cls(cfg)


# Back-compat alias used by older imports/docs
UniverseLimits = ScreenPolicy
