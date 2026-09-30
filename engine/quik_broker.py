"""BrokerExecutionPort over QuikTransport.send_transaction."""

from __future__ import annotations

from typing import Any

from engine.order import OrderIntent, OrderResult, OrderSide, OrderStatus
from engine.quik_transport import QuikTransport


class QuikBrokerExecution:
    """Maps ``OrderIntent`` → QUIK TRANSACTION table via transport."""

    def __init__(
        self,
        transport: QuikTransport,
        *,
        account: str = "",
        class_code: str = "TQBR",
        client_code: str = "",
    ) -> None:
        self.transport = transport
        self.account = account
        self.class_code = class_code
        self.client_code = client_code

    async def place_order(self, intent: OrderIntent) -> OrderResult:
        side = "B" if intent.side == OrderSide.BUY else "S"
        price = float(intent.limit_price or 0)
        board = intent.class_code or self.class_code or "TQBR"
        tx: dict[str, Any] = {
            "ACTION": "NEW_ORDER",
            "CLASSCODE": board,
            "SECCODE": intent.symbol,
            "OPERATION": side,
            "QUANTITY": str(int(abs(intent.quantity))),
            "PRICE": str(price),
            "ACCOUNT": self.account,
            "CLIENT_CODE": self.client_code or intent.client_id or "",
            "TYPE": "L" if intent.limit_price and intent.order_type != "market" else "M",
        }
        try:
            reply = await self.transport.send_transaction(tx)
        except Exception as exc:  # noqa: BLE001
            return OrderResult(
                client_id=intent.client_id,
                status=OrderStatus.REJECTED,
                reason=str(exc),
            )
        status_raw = str(
            reply.get("status") or reply.get("STATUS") or reply.get("result") or "ok"
        ).lower()
        if status_raw in {"ok", "sent", "0", "success", "accepted"}:
            fill_px = reply.get("fill_price") or reply.get("price") or price or None
            fill_qty = float(reply.get("fill_qty") or 0.0)
            requested = abs(float(intent.quantity))
            if fill_qty >= requested - 1e-12 and fill_qty > 0:
                status = OrderStatus.FILLED
            elif fill_qty > 0:
                status = OrderStatus.PARTIAL
            else:
                status = OrderStatus.ACCEPTED
            return OrderResult(
                client_id=intent.client_id,
                status=status,
                filled_qty=fill_qty,
                avg_price=float(fill_px) if fill_px not in (None, "") else None,
                reason="quik_fill" if fill_qty > 0 else "quik_accepted",
                raw=reply if isinstance(reply, dict) else {"data": reply},
            )
        return OrderResult(
            client_id=intent.client_id,
            status=OrderStatus.REJECTED,
            reason=str(reply.get("message") or reply.get("reason") or status_raw),
            raw=reply if isinstance(reply, dict) else {"data": reply},
        )
