"""Alex-Shur ``quik_python`` / ``quik-python`` backend (optional dependency)."""

from __future__ import annotations

from typing import Any

from engine.quik_transport import QuikCandleRow


def _import_quik_python() -> Any:
    try:
        from quik_python import Quik  # type: ignore[import-untyped]

        return Quik
    except ImportError as exc:
        raise ImportError(
            "quik_python not installed — pip install quik-python "
            "(Alex-Shur/quik_python). QuikPy (cia76) is not supported here."
        ) from exc


def _interval_enum(QuikMod: Any, interval_min: int) -> Any:
    """Map minutes → CandleInterval if the package exposes it."""
    try:
        from quik_python import CandleInterval  # type: ignore[import-untyped]
    except ImportError:
        CandleInterval = getattr(QuikMod, "CandleInterval", None)
    if CandleInterval is None:
        return interval_min
    mapping = {
        1: getattr(CandleInterval, "M1", None),
        2: getattr(CandleInterval, "M2", None),
        3: getattr(CandleInterval, "M3", None),
        5: getattr(CandleInterval, "M5", None),
        10: getattr(CandleInterval, "M10", None),
        15: getattr(CandleInterval, "M15", None),
        30: getattr(CandleInterval, "M30", None),
        60: getattr(CandleInterval, "H1", None) or getattr(CandleInterval, "M60", None),
        240: getattr(CandleInterval, "H4", None),
        1440: getattr(CandleInterval, "D1", None) or getattr(CandleInterval, "DAY", None),
    }
    return mapping.get(int(interval_min)) or getattr(CandleInterval, "M1", interval_min)


def _row_from_obj(class_code: str, sec_code: str, c: Any) -> QuikCandleRow:
    dt = getattr(c, "datetime", None) or getattr(c, "dt", None)
    dt_dict: dict[str, Any] | None = None
    if isinstance(dt, dict):
        dt_dict = dt
    elif dt is not None and hasattr(dt, "year"):
        dt_dict = {
            "year": int(getattr(dt, "year")),
            "month": int(getattr(dt, "month")),
            "day": int(getattr(dt, "day")),
            "hour": int(getattr(dt, "hour", 0) or 0),
            "min": int(getattr(dt, "minute", getattr(dt, "min", 0)) or 0),
        }
    raw = c if isinstance(c, dict) else getattr(c, "__dict__", None)
    if isinstance(c, dict):
        return QuikCandleRow(
            class_code=class_code,
            sec_code=sec_code,
            open=float(c.get("open", 0)),
            high=float(c.get("high", 0)),
            low=float(c.get("low", 0)),
            close=float(c.get("close", 0)),
            volume=float(c.get("volume", c.get("vol", 0)) or 0),
            datetime=dt_dict or c.get("datetime"),
            raw=c,
        )
    return QuikCandleRow(
        class_code=class_code,
        sec_code=sec_code,
        open=float(getattr(c, "open", 0)),
        high=float(getattr(c, "high", 0)),
        low=float(getattr(c, "low", 0)),
        close=float(getattr(c, "close", 0)),
        volume=float(getattr(c, "volume", getattr(c, "vol", 0)) or 0),
        datetime=dt_dict,
        raw=dict(raw) if isinstance(raw, dict) else None,
    )


class QuikPythonTransport:
    """
    Preferred live QUIK backend for the spike.

    Requires terminal + Lua scripts from Alex-Shur/quik_python (not QuikPy).
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        requests_port: int = 34130,
        callbacks_port: int = 34131,
    ) -> None:
        self.host = host
        self.requests_port = requests_port
        self.callbacks_port = callbacks_port
        self._quik: Any = None
        self._connected = False
        self._import_error: str | None = None

    @property
    def available(self) -> bool:
        try:
            _import_quik_python()
            return True
        except ImportError as exc:
            self._import_error = str(exc)
            return False

    async def connect(self) -> None:
        Quik = _import_quik_python()
        # Constructor signatures differ across forks — try common forms
        try:
            self._quik = Quik(
                host=self.host,
                requests_port=self.requests_port,
                callbacks_port=self.callbacks_port,
            )
        except TypeError:
            try:
                self._quik = Quik(self.host)
            except TypeError:
                self._quik = Quik()
        if hasattr(self._quik, "connect"):
            maybe = self._quik.connect()
            if hasattr(maybe, "__await__"):
                await maybe
        self._connected = True

    async def close(self) -> None:
        if self._quik is not None:
            for name in ("close", "disconnect", "stop"):
                fn = getattr(self._quik, name, None)
                if callable(fn):
                    maybe = fn()
                    if hasattr(maybe, "__await__"):
                        await maybe
                    break
        self._quik = None
        self._connected = False

    async def is_connected(self) -> bool:
        if not self._connected or self._quik is None:
            return False
        svc = getattr(self._quik, "service", None)
        if svc is not None and hasattr(svc, "is_connected"):
            maybe = svc.is_connected()
            if hasattr(maybe, "__await__"):
                return bool(await maybe)
            return bool(maybe)
        return self._connected

    async def get_candles(
        self,
        class_code: str,
        sec_code: str,
        *,
        interval: int = 1,
        count: int = 1,
    ) -> list[QuikCandleRow]:
        if self._quik is None:
            raise RuntimeError("quik_python_not_connected")
        candles_api = getattr(self._quik, "candles", None)
        if candles_api is None:
            raise RuntimeError("quik_python client has no candles API")
        iv = _interval_enum(type(self._quik), interval)
        raw_list: Any
        if hasattr(candles_api, "get_last_candles"):
            maybe = candles_api.get_last_candles(class_code, sec_code, iv, int(count))
            raw_list = await maybe if hasattr(maybe, "__await__") else maybe
        elif hasattr(candles_api, "GetCandlesFromDataSource"):
            maybe = candles_api.GetCandlesFromDataSource(
                class_code, sec_code, interval, int(count)
            )
            raw_list = await maybe if hasattr(maybe, "__await__") else maybe
            if isinstance(raw_list, dict):
                raw_list = raw_list.get("data", raw_list)
        else:
            raise RuntimeError("unsupported quik_python candles surface")
        return [
            _row_from_obj(class_code, sec_code, c) for c in (raw_list or [])
        ]

    async def send_transaction(self, transaction: dict[str, Any]) -> dict[str, Any]:
        if self._quik is None:
            raise RuntimeError("quik_python_not_connected")
        orders = getattr(self._quik, "orders", None) or getattr(
            self._quik, "transactions", None
        )
        if orders is not None and hasattr(orders, "send_transaction"):
            maybe = orders.send_transaction(transaction)
            result = await maybe if hasattr(maybe, "__await__") else maybe
            return result if isinstance(result, dict) else {"data": result}
        if hasattr(self._quik, "send_transaction"):
            maybe = self._quik.send_transaction(transaction)
            result = await maybe if hasattr(maybe, "__await__") else maybe
            return result if isinstance(result, dict) else {"data": result}
        raise RuntimeError("quik_python client has no send_transaction")
