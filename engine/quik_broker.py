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
            # The request may have reached QUIK before the failure (timeout,
            # dropped socket, gateway restart). Reporting REJECTED told the
            # caller "no position", which is how a live order ends up
            # unnoticed while new risk is opened on top of it.
            return OrderResult(
                client_id=intent.client_id,
                status=OrderStatus.UNKNOWN,
                reason=f"transport_error_unknown_state:{exc}",
                raw={"exception": type(exc).__name__, "message": str(exc), "tx": tx},
            )
        if not isinstance(reply, dict):
            return OrderResult(
                client_id=intent.client_id,
                status=OrderStatus.UNKNOWN,
                reason=f"unparseable_reply:{type(reply).__name__}",
                raw={"data": repr(reply)[:512]},
            )
        raw_status = (
            reply.get("status") or reply.get("STATUS") or reply.get("result")
        )
        if raw_status is None or str(raw_status).strip() == "":
            # An absent status field is not evidence of acceptance. The previous
            # code defaulted this to "ok" and returned ACCEPTED, so a reply that
            # carried no verdict at all was reported as a live order.
            return OrderResult(
                client_id=intent.client_id,
                status=OrderStatus.UNKNOWN,
                reason="reply_without_status_field",
                raw=reply,
            )
        status_raw = str(raw_status).lower()
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
            raw=reply,
        )
