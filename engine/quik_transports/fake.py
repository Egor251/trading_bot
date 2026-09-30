"""Deterministic QUIK transport for tests / paper without a terminal."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from engine.quik_transport import QuikCandleRow


class FakeQuikTransport:
    """
    In-process stand-in for QUIK.

    Produces synthetic candles and records transactions — enough to exercise
    ``QuikMarketDataAdapter`` + ``QuikBrokerExecution`` without a terminal.
    Also implements the snapshot methods used by ``QuikExecutionAdapter`` /
    reconciliation so FakeQuik does not emit permanent BROKER_ERROR incidents.
    """

    def __init__(
        self,
        *,
        base_prices: dict[str, float] | None = None,
        default_price: float = 100.0,
    ) -> None:
        self._base_prices = dict(base_prices or {"SBER": 285.0, "GAZP": 165.0})
        self._default_price = float(default_price)
        self._connected = False
        self._tick = 0
        self.transactions: list[dict[str, Any]] = []
        self.orders: list[dict[str, Any]] = []
        self.positions: list[dict[str, Any]] = []
        self.balances: list[dict[str, Any]] = [
            {"currency": "RUB", "currentbal": 1_000_000.0, "available": 1_000_000.0}
        ]
        self.trades: list[dict[str, Any]] = []

    async def connect(self) -> None:
        self._connected = True

    async def close(self) -> None:
        self._connected = False

    async def is_connected(self) -> bool:
        return self._connected

    def _price(self, sec_code: str) -> float:
        return float(self._base_prices.get(sec_code, self._default_price))

    async def get_candles(
        self,
        class_code: str,
        sec_code: str,
        *,
        interval: int = 1,
        count: int = 1,
    ) -> list[QuikCandleRow]:
        if not self._connected:
            raise RuntimeError("fake_quik_not_connected")
        n = max(1, int(count))
        base = self._price(sec_code)
        now = datetime.now(UTC)
        out: list[QuikCandleRow] = []
        for i in range(n):
            self._tick += 1
            drift = (self._tick % 17) * 0.01
            close = base + drift
            open_ = close - 0.05
            high = max(open_, close) + 0.1
            low = min(open_, close) - 0.1
            # older bars first
            minute = max(0, now.minute - (n - 1 - i))
            out.append(
                QuikCandleRow(
                    class_code=class_code,
                    sec_code=sec_code,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=1000.0 + self._tick,
                    datetime={
                        "year": now.year,
                        "month": now.month,
                        "day": now.day,
                        "hour": now.hour,
                        "min": minute % 60,
                    },
                    raw={"interval": interval, "fake": True},
                )
            )
        return out

    async def send_transaction(self, transaction: dict[str, Any]) -> dict[str, Any]:
        if not self._connected:
            raise RuntimeError("fake_quik_not_connected")
        self.transactions.append(dict(transaction))
        qty = float(transaction.get("QUANTITY") or 0)
        price = float(transaction.get("PRICE") or 0) or self._default_price
        order = {
            "order_num": str(len(self.transactions)),
            "sec_code": transaction.get("SECCODE") or transaction.get("sec_code"),
            "class_code": transaction.get("CLASSCODE") or transaction.get("class_code"),
            "qty": qty,
            "price": price,
            "status": "filled",
            "flags": transaction.get("OPERATION") or transaction.get("operation") or "B",
        }
        self.orders.append(order)
        self.trades.append(
            {
                "trade_num": f"t-{len(self.trades) + 1}",
                "order_num": order["order_num"],
                "sec_code": order["sec_code"],
                "qty": qty,
                "price": price,
                "flags": order["flags"],
                "fee": 0.0,
            }
        )
        return {
            "status": "ok",
            "trans_id": str(len(self.transactions)),
            "fill_qty": qty,
            "fill_price": price or None,
            "echo": dict(transaction),
        }

    # --- reconciliation / QuikExecutionAdapter snapshot API ---

    async def get_orders(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self.orders)

    async def get_positions(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self.positions)

    async def get_balances(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self.balances)

    async def get_trades(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self.trades)
