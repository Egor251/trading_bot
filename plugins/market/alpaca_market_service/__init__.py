"""Alpaca market service — real US equity/crypto bars on active universe."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from engine.adapters.alpaca_adapter import AlpacaMarketDataAdapter
from plugins.market.market_adapter_base import MarketAdapterServiceBase

if TYPE_CHECKING:
    from microkernel_core import MicrokernelCore


class AlpacaMarketService(MarketAdapterServiceBase):
    """
    Polls OHLCV via **alpaca-py** for ``universe.active``.

    Replaces mock drawings. Requires::

        pip install alpaca-py
        set ALPACA_API_KEY=...
        set ALPACA_SECRET_KEY=...

    (Paper keys are fine for market data.)
    The legacy package ``alpaca-trade-api`` is deprecated by Alpaca —
    see https://github.com/alpacahq/alpaca-trade-api-python
    """

    def __init__(self, core: MicrokernelCore, component_id: str) -> None:
        super().__init__(core, component_id)
        self._alpaca: AlpacaMarketDataAdapter | None = None

    def default_config(self) -> dict[str, Any]:
        cfg = super().default_config()
        cfg.update(
            {
                "enabled": False,
                "auto_start": False,
                "interval_sec": 15,
                "timeframe": "1m",
                "class_code": "US",
                "tickers": ["AAPL", "MSFT", "SPY"],
                "follow_universe": True,
                "seed_bars": 120,
                "paper": True,
                "feed": "iex",  # iex | sip (sip needs paid plan)
                "asset_class": "stock",  # stock | crypto
                "api_key_env": "ALPACA_API_KEY",
                "secret_key_env": "ALPACA_SECRET_KEY",
            }
        )
        return cfg

    async def on_initialize(self) -> None:
        await super().on_initialize()
        key_env = str(self.config.get("api_key_env") or "ALPACA_API_KEY")
        secret_env = str(self.config.get("secret_key_env") or "ALPACA_SECRET_KEY")
        # Also accept ALPACA_API_SECRET_KEY alias
        secret = os.environ.get(secret_env, "") or os.environ.get(
            "ALPACA_API_SECRET_KEY", ""
        )
        self._alpaca = AlpacaMarketDataAdapter(
            api_key=os.environ.get(key_env, "") or os.environ.get("APCA_API_KEY_ID", ""),
            secret_key=secret or os.environ.get("APCA_API_SECRET_KEY", ""),
            paper=bool(self.config.get("paper", True)),
            feed=str(self.config.get("feed") or "iex"),
            asset_class=str(self.config.get("asset_class") or "stock"),
            exchange="alpaca",
        )
        self._adapter = self._alpaca
        if not self.config.get("class_code"):
            self._default_class_code = "US"

    async def start(self) -> None:
        if self._alpaca is None:
            self._last_error = "adapter_not_configured"
            return
        if not self._alpaca.available:
            self._last_error = "alpaca_py_not_installed"
            return
        if not self._alpaca.configured:
            self._last_error = "alpaca_keys_missing"
            return
        await super().start()

    async def get_status(self) -> dict[str, Any]:
        status = await super().get_status()
        status["alpaca_available"] = bool(self._alpaca and self._alpaca.available)
        status["alpaca_configured"] = bool(self._alpaca and self._alpaca.configured)
        status["feed"] = self.config.get("feed")
        status["asset_class"] = self.config.get("asset_class")
        status["last_error"] = self._last_error or (
            self._alpaca._last_error if self._alpaca else None
        )
        return status
