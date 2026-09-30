"""QUIK market service — active-book feed via QuikTransport (not QuikPy)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from engine.adapters.quik_adapter import QuikMarketDataAdapter
from engine.quik_broker import QuikBrokerExecution
from engine.quik_transports import build_quik_transport
from engine.runtime import resolve_quik_broker
from plugins.market.market_adapter_base import MarketAdapterServiceBase

if TYPE_CHECKING:
    from microkernel_core import MicrokernelCore


class QuikMarketService(MarketAdapterServiceBase):
    """
    Streams candles for ``universe.active`` through a swappable QUIK transport.

    ``enabled: false`` by default. Use ``transport: fake`` for dry-run without
    a terminal, or ``quik_python`` / ``qlua_rpc`` when Lua RPC is running.
    Legacy QuikPy stays in ``Drivers/`` only.
    """

    def __init__(self, core: MicrokernelCore, component_id: str) -> None:
        super().__init__(core, component_id)
        self._quik: QuikMarketDataAdapter | None = None
        self._broker: QuikBrokerExecution | None = None
        self._last_error: str | None = None

    def default_config(self) -> dict[str, Any]:
        cfg = super().default_config()
        cfg.update(
            {
                "enabled": False,
                "auto_start": False,
                "interval_sec": 5,
                "timeframe": "1m",
                "class_code": "TQBR",
                "tickers": ["SBER"],
                "follow_universe": True,
                # null | fake | quik_python | qlua_rpc
                "transport": "null",
                "transport_options": {},
                "account": "",
                "client_code": "",
            }
        )
        return cfg

    def _build_transport_stack(self) -> None:
        kind = self.config.get("transport", "null")
        opts = dict(self.config.get("transport_options") or {})
        transport = build_quik_transport(kind, **opts)
        self._quik = QuikMarketDataAdapter(transport=transport)
        self._adapter = self._quik
        self._broker = QuikBrokerExecution(
            transport,
            account=str(self.config.get("account") or ""),
            class_code=str(self.config.get("class_code") or "TQBR"),
            client_code=str(self.config.get("client_code") or ""),
        )

    async def on_initialize(self) -> None:
        await super().on_initialize()
        self._build_transport_stack()

    async def start(self) -> None:
        if not self.config.get("enabled", False):
            return
        kind = str(self.config.get("transport") or "null").lower()
        if kind in {"", "null", "none", "unavailable"}:
            self._last_error = "transport_not_configured"
            return
        # Rebuild in case config mutated after initialize (tests / REPL)
        self._build_transport_stack()
        try:
            await super().start()
            self._last_error = None
            runtime = getattr(self.core, "trading", None)
            if runtime is not None:
                broker = resolve_quik_broker(self.core)
                if broker is not None:
                    runtime.broker = broker
        except Exception as exc:
            self.is_running = False
            self._last_error = str(exc)

    async def get_status(self) -> dict[str, Any]:
        status = await super().get_status()
        connected = False
        if self._quik is not None:
            try:
                connected = await self._quik.transport.is_connected()
            except Exception:
                connected = False
        status.update(
            {
                "connected": connected,
                "transport": self.config.get("transport", "null"),
                "broker": type(self._broker).__name__ if self._broker else None,
                "last_error": self._last_error,
                "note": (
                    "preferred live: quik_python; alternate: qlua_rpc; "
                    "dev: fake; QuikPy legacy-only"
                ),
            }
        )
        return status

    @property
    def broker(self) -> QuikBrokerExecution | None:
        return self._broker
