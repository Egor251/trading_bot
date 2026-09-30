"""Position sizing for phantom / paper / live (TradingView-style modes + risk/ATR/Kelly)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from enum import Enum
from typing import Any, Mapping, Sequence


class SizingMode(str, Enum):
    FIXED = "fixed"
    CASH = "cash"
    PERCENT_OF_EQUITY = "percent_of_equity"
    RISK_PERCENT = "risk_percent"
    ATR_RISK = "atr_risk"
    KELLY = "kelly"


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def atr_from_bars(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    *,
    period: int = 14,
) -> float | None:
    """Trailing-window ATR ending at the last bar (needs period+1 closes)."""
    n = min(len(highs), len(lows), len(closes))
    if n <= 0:
        return None
    series = atr_series_from_bars(highs, lows, closes, period=period)
    if not series:
        return None
    return series[-1]


def atr_series_from_bars(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    *,
    period: int = 14,
) -> list[float | None]:
    """Causal ATR at each bar: uses only OHLC up to that index (no lookahead)."""
    n = min(len(highs), len(lows), len(closes))
    p = max(1, int(period))
    out: list[float | None] = [None] * n
    if n < p + 1:
        return out
    trs: list[float] = []
    for i in range(n):
        h = float(highs[i])
        l = float(lows[i])
        prev_c = float(closes[i - 1]) if i > 0 else float(closes[i])
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(max(tr, 1e-12))
        if i + 1 >= p + 1:
            window = trs[i - p + 1 : i + 1]
            out[i] = sum(window) / len(window)
    return out


def kelly_fraction(*, win_rate: float, reward_risk: float) -> float:
    """Classic Kelly f* = W - (1-W)/R. Clamped to [0, 1]."""
    w = _clamp(float(win_rate), 0.0, 1.0)
    r = max(float(reward_risk), 1e-9)
    f = w - (1.0 - w) / r
    return _clamp(f, 0.0, 1.0)


@dataclass(slots=True)
class SizingConfig:
    mode: SizingMode | str = SizingMode.PERCENT_OF_EQUITY
    value: float = 10.0  # fixed qty | cash | % equity | risk %
    stop_pct: float = 0.02  # for risk_percent (fraction, 0.02 = 2%)
    atr_period: int = 14
    atr_mult: float = 2.0
    win_rate: float = 0.55
    reward_risk: float = 1.5
    kelly_fraction: float = 0.5  # half-Kelly
    max_position_pct: float = 0.25
    scale_by_strength: bool = True
    strength_floor: float = 0.25
    min_qty: float = 1e-8

    def __post_init__(self) -> None:
        if not isinstance(self.mode, SizingMode):
            raw = str(self.mode or SizingMode.PERCENT_OF_EQUITY.value).strip().lower()
            try:
                object.__setattr__(self, "mode", SizingMode(raw))
            except ValueError:
                object.__setattr__(self, "mode", SizingMode.PERCENT_OF_EQUITY)
        object.__setattr__(self, "value", float(self.value))
        object.__setattr__(self, "stop_pct", max(1e-6, float(self.stop_pct)))
        object.__setattr__(self, "atr_period", max(1, int(self.atr_period)))
        object.__setattr__(self, "atr_mult", max(1e-6, float(self.atr_mult)))
        object.__setattr__(self, "win_rate", _clamp(float(self.win_rate), 0.0, 1.0))
        object.__setattr__(self, "reward_risk", max(1e-6, float(self.reward_risk)))
        object.__setattr__(
            self, "kelly_fraction", _clamp(float(self.kelly_fraction), 0.0, 1.0)
        )
        object.__setattr__(
            self, "max_position_pct", _clamp(float(self.max_position_pct), 0.0, 10.0)
        )
        object.__setattr__(self, "scale_by_strength", bool(self.scale_by_strength))
        object.__setattr__(
            self, "strength_floor", _clamp(float(self.strength_floor), 0.0, 1.0)
        )
        object.__setattr__(self, "min_qty", max(0.0, float(self.min_qty)))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "SizingConfig":
        if not data:
            return cls()
        known = {f.name for f in fields(cls)}
        raw = {k: v for k, v in dict(data).items() if k in known}
        return cls(**raw)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["mode"] = (
            self.mode.value if isinstance(self.mode, SizingMode) else str(self.mode)
        )
        return out

    def strength_factor(self, strength: float | None) -> float:
        """Scale factor for qty, or ``0`` when strength is below ``strength_floor``.

        Semantics (Operator UI: "min strength"):
        - ``scale_by_strength`` off → always ``1.0``
        - missing / NaN strength → ``1.0`` (manual / unsized paths)
        - ``strength < strength_floor`` → ``0.0`` (reject; do **not** boost weak signals)
        - otherwise → clamp strength to ``[0, 1]``
        """
        if not self.scale_by_strength:
            return 1.0
        if strength is None:
            return 1.0
        try:
            s = float(strength)
        except (TypeError, ValueError):
            return 1.0
        if s != s:  # NaN
            return 1.0
        s = _clamp(s, 0.0, 1.0)
        floor = float(self.strength_floor)
        if floor > 0.0 and s < floor:
            return 0.0
        return s

    def rejects_strength(self, strength: float | None) -> bool:
        """True when ``scale_by_strength`` would zero the order for this strength."""
        return self.strength_factor(strength) <= 0.0 and self.scale_by_strength


def size_quantity(
    *,
    equity: float,
    price: float,
    config: SizingConfig | Mapping[str, Any] | None = None,
    strength: float | None = None,
    atr: float | None = None,
    fixed_qty: float | None = None,
    contract_multiplier: float = 1.0,
) -> float:
    """Compute order quantity from equity/price and sizing config.

    ``contract_multiplier`` scales notional the same way as ``Position.notional``
    (qty * price * multiplier), so futures cannot silently bypass max position %.
    """
    cfg = config if isinstance(config, SizingConfig) else SizingConfig.from_dict(config)
    eq = max(float(equity or 0), 0.0)
    px = float(price or 0)
    mult = max(float(contract_multiplier or 1.0), 1e-12)
    if px <= 0 or eq <= 0:
        return 0.0
    unit_notional = px * mult

    mode = cfg.mode if isinstance(cfg.mode, SizingMode) else SizingMode(str(cfg.mode))
    qty = 0.0

    if mode is SizingMode.FIXED:
        qty = float(fixed_qty if fixed_qty is not None else cfg.value)
    elif mode is SizingMode.CASH:
        qty = float(cfg.value) / unit_notional
    elif mode is SizingMode.PERCENT_OF_EQUITY:
        pct = float(cfg.value) / 100.0
        qty = (eq * pct) / unit_notional
    elif mode is SizingMode.RISK_PERCENT:
        risk_pct = float(cfg.value) / 100.0
        stop = max(float(cfg.stop_pct), 1e-9)
        risk_cash = eq * risk_pct
        stop_dist = unit_notional * stop
        qty = risk_cash / max(stop_dist, 1e-12)
    elif mode is SizingMode.ATR_RISK:
        risk_pct = float(cfg.value) / 100.0
        atr_v = float(atr or 0)
        if atr_v <= 0:
            return 0.0
        stop_dist = atr_v * float(cfg.atr_mult) * mult
        qty = (eq * risk_pct) / max(stop_dist, 1e-12)
    elif mode is SizingMode.KELLY:
        f_star = kelly_fraction(win_rate=cfg.win_rate, reward_risk=cfg.reward_risk)
        f = f_star * float(cfg.kelly_fraction)
        # value can optionally scale Kelly further (100 = full computed fraction)
        scale = float(cfg.value) / 100.0 if cfg.value > 0 else 1.0
        qty = (eq * f * scale) / unit_notional
    else:
        pct = float(cfg.value) / 100.0
        qty = (eq * pct) / unit_notional

    qty = abs(float(qty)) * cfg.strength_factor(strength)

    max_notional = eq * float(cfg.max_position_pct)
    if max_notional > 0 and qty * unit_notional > max_notional:
        qty = max_notional / unit_notional

    if qty < cfg.min_qty:
        return 0.0
    return float(qty)


SIZING_MODE_IDS: tuple[str, ...] = tuple(m.value for m in SizingMode)
