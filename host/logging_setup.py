"""Wire Code_base ``prod_logging`` for engine and Operator UI.

Also attaches a **sync** Loguru file sink per process. The AsyncCoordinatedFileSink
from Code_base often stays empty when Loguru ``enqueue=True`` writes from a
background thread (create_task never runs) — hence empty engine.log / operator.log
while app.log still receives early init lines.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Optional

from host.bootstrap import ensure_code_base_path

ROOT = Path(__file__).resolve().parent.parent
_READY = False
_SYNC_SINK_IDS: dict[str, int] = {}


class _BraceStdLogger:
    """Make stdlib fallback honor the project's Loguru-style ``{}`` messages."""

    def __init__(self, logger: Any) -> None:
        self._logger = logger

    @staticmethod
    def _format(message: object, args: tuple[Any, ...]) -> str:
        text = str(message)
        if not args:
            return text
        try:
            return text.format(*args)
        except Exception:
            return f"{text} args={args!r}"

    def debug(self, message: object, *args: Any, **kwargs: Any) -> None:
        self._logger.debug(self._format(message, args), **kwargs)

    def info(self, message: object, *args: Any, **kwargs: Any) -> None:
        self._logger.info(self._format(message, args), **kwargs)

    def warning(self, message: object, *args: Any, **kwargs: Any) -> None:
        self._logger.warning(self._format(message, args), **kwargs)

    def error(self, message: object, *args: Any, **kwargs: Any) -> None:
        self._logger.error(self._format(message, args), **kwargs)

    def exception(self, message: object, *args: Any, **kwargs: Any) -> None:
        self._logger.exception(self._format(message, args), **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._logger, name)


def _default_level() -> str:
    return (os.environ.get("LOG_LEVEL") or "INFO").upper()


def _attach_sync_file_sink(*, process: str, path: Path, level: str) -> None:
    """Reliable per-process file (sync write, absolute path)."""
    from loguru import logger

    try:
        from utils.prod_logging._formatter import SafeLogFormatter

        fmt = SafeLogFormatter.format_file
    except Exception:
        fmt = (
            "{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | "
            "{extra[process]:<14} | {extra[component]:<14} | {message}\n"
        )

    # Drop previous sink for this process (re-init / tests)
    old = _SYNC_SINK_IDS.pop(process, None)
    if old is not None:
        try:
            logger.remove(old)
        except Exception:
            pass

    path.parent.mkdir(parents=True, exist_ok=True)

    def _filter(record: dict) -> bool:
        return str(record["extra"].get("process") or "") == process

    sink_id = logger.add(
        str(path),
        format=fmt,
        level=level,
        filter=_filter,
        rotation="20 MB",
        retention="14 days",
        compression="gz",
        enqueue=False,
        catch=True,
    )
    _SYNC_SINK_IDS[process] = sink_id


async def setup_prod_logging(
    *,
    log_level: Optional[str] = None,
    process: str = "trading_bot",
    file_name: str = "app.log",
) -> Any:
    """Initialize prod_logging once (async). Safe to call repeatedly."""
    global _READY
    ensure_code_base_path()
    # Keep CWD-relative app.log under repo even if launched elsewhere
    os.chdir(ROOT)

    from utils.prod_logging import get_logger, setup_logging

    level = (log_level or _default_level()).upper()
    prod = await setup_logging(log_level=level)
    log_dir = ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / file_name

    # Best-effort Code_base coordinated sink (may stay empty — see module doc)
    try:
        await prod.add_file_sink(
            str(path),
            process,
            rotation="20 MB",
            retention="14 days",
            compression="gz",
        )
    except Exception:
        pass

    _attach_sync_file_sink(process=process, path=path, level=level)
    _READY = True
    log = get_logger(process, "System")
    log.info("prod_logging ready level={} file={}", level, path)
    return prod


def setup_prod_logging_sync(
    *,
    log_level: Optional[str] = None,
    process: str = "trading_bot",
    file_name: str = "app.log",
) -> None:
    """Sync entry for Dear PyGui / non-async processes."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(
            setup_prod_logging(
                log_level=log_level, process=process, file_name=file_name
            )
        )
        return
    # Already inside a loop (unusual for UI) — schedule and hope
    asyncio.create_task(
        setup_prod_logging(log_level=log_level, process=process, file_name=file_name)
    )


def get_log(process: str = "trading_bot", component: str = "System"):
    """Return prod logger; falls back to stdlib if not initialized yet."""
    ensure_code_base_path()
    try:
        from utils.prod_logging import get_logger

        return get_logger(process, component)
    except Exception:
        import logging

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        )
        return _BraceStdLogger(logging.getLogger(f"{process}.{component}"))


def logging_ready() -> bool:
    return bool(_READY)
