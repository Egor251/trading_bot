"""Mock market service — synthetic candles on active universe."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from engine.adapters import MockMarketDataAdapter
from plugins.market.market_adapter_base import MarketAdapterServiceBase

if TYPE_CHECKING:
    from microkernel_core import MicrokernelCore


class MockMarketService(MarketAdapterServiceBase):
    """Publishes synthetic ``market.candle`` via ``MockMarketDataAdapter``."""

    def __init__(self, core: MicrokernelCore, component_id: str) -> None:
        super().__init__(core, component_id)
        self._mock: MockMarketDataAdapter | None = None

    def default_config(self) -> dict[str, Any]:
        cfg = super().default_config()
        cfg.update(
            {
                "enabled": True,
                "auto_start": True,
                "interval_sec": 2,
                "class_code": "TQBR",
                "tickers": ["SBER"],
                "follow_universe": True,
                "base_prices": {"SBER": 285.0, "GAZP": 165.0, "LKOH": 7200.0},
                "seed_bars": 120,
                "interval_sec": 1.5,
            }
        )
        return cfg

    async def on_initialize(self) -> None:
        await super().on_initialize()
        prices = dict(self.config.get("base_prices") or {})
        for _, ticker in self._active:
            prices.setdefault(ticker, 100.0)
        self._mock = MockMarketDataAdapter(base_prices=prices, exchange="mock")
        self._adapter = self._mock

    def _apply_active(self, rows: list[Any]) -> None:
        super()._apply_active(rows)
        if self._mock is None:
            return
        for _cc, ticker in self._active:
            self._mock.ensure_symbol(ticker)
