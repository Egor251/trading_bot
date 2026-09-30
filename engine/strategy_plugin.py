"""Base class for strategy plugins (public Strategy SDK)."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from microkernel_core import BasePlugin, KernelEvent, PluginKind, PluginRunMode

from host.events import normalize_strategy_signal, publish_strategy_signal
from engine.protection import StrategyRiskProfile


@dataclass(slots=True)
class StrategyDecision:
    """Causal decision for one bar (or HOLD). Quantity/sizing stays outside."""

    action: str  # BUY | SELL | HOLD
    strength: float = 0.0
    reason: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    intent: str = "flat"  # long | short | flat | reduce

    def as_signal_fields(self) -> dict[str, Any]:
        return {
            "action": str(self.action or "HOLD").upper(),
            "strength": float(self.strength or 0),
            "reason": self.reason,
            "params_snapshot": dict(self.metrics),
            "intent": self.intent,
        }


@dataclass(slots=True)
class StrategyHealth:
    ok: bool
    warmup_ready: bool
    bars_seen: int
    version: str
    last_error: str | None = None
    research: bool = False


class StrategyPlugin(BasePlugin):
    """
    Shared strategy surface (product entity: strategy — not an "extension plugin").

    Contract:
    - CONFIG_SCHEMA / warmup_bars / required_data describe needs.
    - evaluate_at is the pure causal core (no EventBus, no future bars).
    - Live path: market.candle -> evaluate_at -> emit_signal.
    - Research path: begin_research isolates config/state and gates by run_id.
    - Sizing stays outside: Risk/Order own size.
    """

    PLUGIN_KIND = PluginKind.BUSINESS
    RUN_MODE = PluginRunMode.CONTINUOUS
    STRATEGY_VERSION = "1.0.0"
    CONFIG_SCHEMA: dict[str, dict[str, Any]] = {}
    RISK_PROFILE: dict[str, Any] = {"archetype": "trend"}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._operator_view: dict[str, Any] = {}
        self._bars_seen = 0
        self._last_error: str | None = None
        self._last_decision: StrategyDecision | None = None
        self._research: dict[str, Any] | None = None

    def config_schema(self) -> dict[str, dict[str, Any]]:
        return dict(self.CONFIG_SCHEMA)

    def default_params(self) -> dict[str, Any]:
        return {
            key: meta.get("default")
            for key, meta in self.CONFIG_SCHEMA.items()
            if isinstance(meta, dict) and "default" in meta
        }

    def warmup_bars(self) -> int:
        return 1

    def required_data(self) -> tuple[str, ...]:
        return ("close",)

    def risk_profile(self) -> StrategyRiskProfile:
        """Protection metadata; config may tighten but not disable max-loss."""
        values = dict(self.RISK_PROFILE)
        overrides = self.config.get("risk_profile")
        if isinstance(overrides, dict):
            values.update(overrides)
        return StrategyRiskProfile.from_dict(values)

    def health(self) -> StrategyHealth:
        return StrategyHealth(
            ok=self._last_error is None,
            warmup_ready=self._bars_seen >= self.warmup_bars(),
            bars_seen=self._bars_seen,
            version=str(getattr(self, "STRATEGY_VERSION", "0")),
            last_error=self._last_error,
            research=self._research is not None,
        )

    def position_intent(self) -> str:
        if self._last_decision is None:
            return "flat"
        return str(self._last_decision.intent or "flat")

    def book_view(self, symbol: str) -> dict[str, Any]:
        """Own slice + net exposure for ``symbol`` (todo2 §3)."""
        runtime = getattr(getattr(self, "core", None), "trading", None)
        if runtime is None or not hasattr(runtime, "exposure_view"):
            return {
                "symbol": symbol,
                "strategy_id": self.component_id,
                "strategy_qty": "0",
                "net_qty": "0",
                "slices": [],
            }
        return runtime.exposure_view(self.component_id, symbol)

    def reset_state(self) -> None:
        self._bars_seen = 0
        self._last_error = None
        self._last_decision = None
        self._operator_view = {}

    def snapshot_state(self) -> dict[str, Any]:
        return {
            "bars_seen": self._bars_seen,
            "last_error": self._last_error,
            "last_decision": self._last_decision,
            "operator_view": dict(self._operator_view),
            "extra": self._snapshot_extra_state(),
        }

    def restore_state(self, snap: dict[str, Any]) -> None:
        self._bars_seen = int(snap.get("bars_seen") or 0)
        self._last_error = snap.get("last_error")
        self._last_decision = snap.get("last_decision")
        self._operator_view = dict(snap.get("operator_view") or {})
        self._restore_extra_state(dict(snap.get("extra") or {}))

    def _snapshot_extra_state(self) -> dict[str, Any]:
        return {}

    def _restore_extra_state(self, extra: dict[str, Any]) -> None:
        return None

    def isolated_config(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        cfg = {**self.default_params(), **dict(self.config or {})}
        if overrides:
            schema = getattr(self, "CONFIG_SCHEMA", None) or {}
            if isinstance(schema, dict) and schema:
                # Drop alien keys (e.g. MA fast/slow leaked onto ADX grid)
                # so research variants cannot silently no-op.
                allowed = set(schema) | set(self.default_params())
                cleaned = {k: v for k, v in dict(overrides).items() if k in allowed}
            else:
                cleaned = dict(overrides)
            cfg.update(cleaned)
        return cfg

    def begin_research(self, *, run_id: str, config: dict[str, Any]) -> None:
        if self._research is not None:
            self.end_research()
        self._research = {
            "run_id": str(run_id),
            "orig_config": dict(self.config or {}),
            "orig_state": self.snapshot_state(),
        }
        self.config.clear()
        self.config.update(config)
        self.reset_state()

    def end_research(self) -> None:
        if self._research is None:
            return
        orig_cfg = dict(self._research.get("orig_config") or {})
        orig_state = dict(self._research.get("orig_state") or {})
        self._research = None
        self.config.clear()
        self.config.update(orig_cfg)
        self.restore_state(orig_state)

    @contextmanager
    def research_session(
        self, *, run_id: str, config: dict[str, Any]
    ) -> Iterator[StrategyPlugin]:
        self.begin_research(run_id=run_id, config=config)
        try:
            yield self
        finally:
            self.end_research()

    def candle_allowed(self, event: KernelEvent) -> bool:
        if self._research is None:
            return True
        rid = str((event.payload or {}).get("run_id") or "")
        return rid == str(self._research.get("run_id") or "")

    def operator_view(self) -> dict[str, Any]:
        return dict(self._operator_view)

    def update_operator_view(
        self,
        *,
        action: str,
        ticker: str = "",
        strength: float = 0.0,
        metrics: dict[str, Any] | None = None,
        reason: str = "",
    ) -> None:
        import time

        self._operator_view = {
            "action": str(action or "HOLD").upper(),
            "ticker": str(ticker or ""),
            "strength": float(strength or 0),
            "metrics": dict(metrics or {}),
            "reason": reason,
            "ts": time.time(),
        }

    def allowed_tickers(self) -> set[str] | None:
        raw = self.config.get("tickers")
        if not raw:
            return None
        return {str(t) for t in raw}

    def ticker_allowed(self, ticker: str | None) -> bool:
        allow = self.allowed_tickers()
        if allow is None:
            return True
        return bool(ticker) and str(ticker) in allow

    def is_observe_only(self) -> bool:
        """True when strategy runs for audit/shadow but is not in Consilium lineup."""
        if bool(self.config.get("observe_only")):
            return True
        try:
            ens = self.core.service_registry.get("ensemble_service")
            obs = list((getattr(ens, "_ensemble_cfg", {}) or {}).get("observe") or [])
            return self.component_id in {str(x) for x in obs}
        except Exception:
            return False

    async def emit_signal(self, payload: dict[str, Any]) -> None:
        body = dict(payload)
        body.setdefault("strategy_id", self.component_id)
        if self.is_observe_only():
            body["observe_only"] = True
        normalized = normalize_strategy_signal(body)
        instance_id = str(
            await self.core.config.get("instance.id", "local") or "local"
        )
        await publish_strategy_signal(
            self.core.event_bus,
            source_id=self.component_id,
            payload=normalized,
            instance_id=instance_id,
        )

    def closes(self, event: KernelEvent) -> list[float]:
        return list(self.bar_series(event).get("close") or [])

    def bar_series(self, event: KernelEvent) -> dict[str, list[float]]:
        # Research/phantom runs must read the shadow book, not live trading history
        # (otherwise signals fire on a hybrid series and plot outside pattern candles).
        runtime = None
        if self._research is not None:
            runtime = getattr(self.core, "phantom", None)
        if runtime is None:
            runtime = getattr(self.core, "trading", None)
        payload = event.payload or {}
        ticker = str(payload.get("ticker") or "")
        class_code = str(payload.get("class_code") or "")
        key = f"{class_code}:{ticker}" if class_code else ticker
        bars = []
        if runtime is not None:
            bars = runtime.candles.bars(key) or runtime.candles.bars(ticker) or []

        series: dict[str, list[float]] = {
            "open": [float(b.open) for b in bars],
            "high": [float(b.high) for b in bars],
            "low": [float(b.low) for b in bars],
            "close": [float(b.close) for b in bars],
            "volume": [float(b.volume) for b in bars],
        }
        close = payload.get("close")
        if close not in (None, ""):
            c = float(close)
            if not series["close"] or series["close"][-1] != c:
                series["open"].append(float(payload.get("open") or c))
                series["high"].append(float(payload.get("high") or c))
                series["low"].append(float(payload.get("low") or c))
                series["close"].append(c)
                series["volume"].append(float(payload.get("volume") or 0))
        return series

    def evaluate_at(
        self,
        series: dict[str, list[float]],
        index: int,
        *,
        config: dict[str, Any] | None = None,
    ) -> StrategyDecision | None:
        raise NotImplementedError(f"{type(self).__name__}.evaluate_at")

    def evaluate_bars(
        self,
        series: dict[str, list[float]],
        *,
        config: dict[str, Any] | None = None,
    ) -> list[StrategyDecision | None]:
        # Explicit research config = schema defaults + overrides only (no live
        # operator knobs leaking into walk-forward variants).
        if config is not None:
            cfg = {**self.default_params()}
            schema = getattr(self, "CONFIG_SCHEMA", None) or {}
            if isinstance(schema, dict) and schema:
                allowed = set(schema) | set(self.default_params())
                cfg.update({k: v for k, v in dict(config).items() if k in allowed})
            else:
                cfg.update(dict(config))
        else:
            cfg = self.isolated_config()
        n = len(series.get("close") or [])
        out: list[StrategyDecision | None] = []
        saved = self.snapshot_state()
        try:
            self.reset_state()
            for i in range(n):
                window = {k: list(v[: i + 1]) for k, v in series.items()}
                try:
                    decision = self.evaluate_at(window, i, config=cfg)
                except Exception as exc:
                    self._last_error = str(exc)
                    decision = None
                if decision is not None:
                    self._last_decision = decision
                    action = str(decision.action or "").upper()
                    intent = str(decision.intent or "").lower()
                    # Keep side memory for stateful exits (turtle, etc.).
                    if action in {"BUY", "SELL"}:
                        self._last_side = action
                    elif action == "HOLD" and intent == "flat":
                        self._last_side = None
                self._bars_seen = i + 1
                out.append(decision)
        finally:
            self.restore_state(saved)
        return out

    async def handle_candle(self, event: KernelEvent) -> StrategyDecision | None:
        if not self.candle_allowed(event):
            return None
        ticker = (event.payload or {}).get("ticker")
        if not self.ticker_allowed(ticker):
            return None
        series = self.bar_series(event)
        index = max(0, len(series.get("close") or []) - 1)
        self._bars_seen = index + 1
        try:
            decision = self.evaluate_at(series, index, config=self.isolated_config())
            self._last_error = None
        except Exception as exc:
            self._last_error = str(exc)
            return None
        if decision is None:
            return None
        self._last_decision = decision
        self.update_operator_view(
            action=decision.action,
            ticker=str(ticker or ""),
            strength=decision.strength,
            metrics=decision.metrics,
            reason=decision.reason,
        )
        return decision
