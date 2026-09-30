"""MOEX ISS board listings → ``Instrument`` rows for universe catalog."""

from __future__ import annotations

from typing import Any

from engine.universe import Instrument

# board → (asset_class, market, engine)
_BOARD_META: dict[str, tuple[str, str, str]] = {
    "TQBR": ("equity", "shares", "stock"),
    "TQTF": ("etf", "shares", "stock"),
    "TQOB": ("other", "bonds", "stock"),
    "SPBFUT": ("future", "forts", "futures"),
}


def board_meta(board: str) -> tuple[str, str, str]:
    return _BOARD_META.get(board.upper(), ("equity", "shares", "stock"))


async def fetch_moex_board_listings(
    *,
    boards: list[str] | tuple[str, ...] = ("TQBR",),
    include_marketdata: bool = True,
) -> list[Instrument]:
    """
    Pull tradable securities (+ optional session marketdata) from MOEX ISS.

    Requires ``aiomoex`` + ``aiohttp``. Does not need QUIK.
    """
    try:
        import aiomoex
        import aiohttp
    except ImportError as exc:
        raise ImportError(
            "moex listings require aiomoex+aiohttp — pip install aiomoex aiohttp"
        ) from exc

    out: list[Instrument] = []
    async with aiohttp.ClientSession() as session:
        for board in boards:
            board_u = str(board).upper()
            asset_class, market, engine = board_meta(board_u)
            secs = await aiomoex.get_board_securities(
                session,
                table="securities",
                columns=None,
                board=board_u,
                market=market,
                engine=engine,
            )
            md_by_sec: dict[str, dict[str, Any]] = {}
            if include_marketdata:
                try:
                    md_rows = await aiomoex.get_board_securities(
                        session,
                        table="marketdata",
                        columns=None,
                        board=board_u,
                        market=market,
                        engine=engine,
                    )
                    for row in md_rows or []:
                        secid = str(row.get("SECID") or "")
                        if secid:
                            md_by_sec[secid] = dict(row)
                except Exception:
                    md_by_sec = {}

            for row in secs or []:
                secid = str(row.get("SECID") or "").strip()
                if not secid:
                    continue
                md = md_by_sec.get(secid) or {}
                price = _f(md.get("LAST") or md.get("LCLOSE") or row.get("PREVPRICE"))
                volume = _f(md.get("VOLTODAY") or md.get("VALTODAY_USD"))
                # VALTODAY is RUB turnover on MOEX equities — use as dollar_volume proxy
                dollar_volume = _f(md.get("VALTODAY") or (price * volume if price and volume else 0))
                shortname = str(row.get("SHORTNAME") or row.get("SECNAME") or "")
                lotsize = _f(row.get("LOTSIZE") or 1) or 1.0
                out.append(
                    Instrument(
                        symbol=secid,
                        class_code=board_u,
                        asset_class=asset_class,
                        exchange="MOEX",
                        price=price,
                        volume=volume,
                        dollar_volume=dollar_volume,
                        meta={
                            "shortname": shortname,
                            "lotsize": lotsize,
                            "source": "moex_iss",
                            "board": board_u,
                        },
                    )
                )
    return out


def _f(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0
