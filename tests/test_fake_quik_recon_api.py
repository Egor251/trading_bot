"""FakeQuik must expose snapshot methods used by reconciliation."""

from __future__ import annotations

import asyncio

from host.bootstrap import ensure_code_base_path

ensure_code_base_path()

from engine.quik_transports.fake import FakeQuikTransport


def test_fake_quik_has_recon_snapshot_methods() -> None:
    async def _run() -> None:
        t = FakeQuikTransport()
        await t.connect()
        assert await t.get_orders() == []
        assert await t.get_positions() == []
        assert await t.get_balances()
        assert await t.get_trades() == []
        await t.send_transaction(
            {"SECCODE": "SBER", "CLASSCODE": "TQBR", "QUANTITY": 1, "PRICE": 280, "OPERATION": "B"}
        )
        assert len(await t.get_orders()) == 1
        assert len(await t.get_trades()) == 1

    asyncio.run(_run())
