"""
Engine layer — торговые модули ядра (консилиум).

Отделено от UI. Реализуется поверх TradingBotCore / EventBus.
См. docs/architecture/adaptive_ensemble_trading_bot.md
"""

from .market import (
    MarketDataPort,
    Candle,
    BrokerExecutionPort,
    candle_to_market_payload,
    timeframe_to_interval_min,
    NoopBrokerExecution,
)
from .quik_transport import (
    QuikTransport,
    QuikCandleRow,
    NullQuikTransport,
    UnavailableQuikTransport,
)
from .quik_broker import QuikBrokerExecution
from .quik_transports import build_quik_transport
from .adapters import (
    MockMarketDataAdapter,
    CcxtMarketDataAdapter,
    QuikMarketDataAdapter,
)
from .risk import RiskLimits, RiskDecision, RiskManager
from .order import OrderIntent, OrderManager, OrderSide, OrderStatus, OrderResult, Position
from .ensemble import StrategySignal, EnsembleManager, WeightedDecision
from .candle_store import CandleStore, StoredBar
from .runtime import TradingRuntime, get_runtime
from .strategy_plugin import StrategyPlugin, StrategyDecision, StrategyHealth
# AI tools are intentionally loaded by the AI plugin after host bootstrap.
# Importing them here poisons ``sys.modules`` when optional Code_base packages
# are absent (research workers and broker utilities import ``engine`` directly).
TRADING_AI_TOOL_CLASSES: list[type] = []
from .indicators import sma, ema, FormulaSeries, equity_pnl, max_drawdown
from .universe import (
    Instrument,
    UniverseConfig,
    UniverseLimits,
    UniverseManager,
    UniverseSnapshot,
    ScreenPolicy,
    BUILTIN_PROFILES,
    instrument_from_dict,
    normalize_universe_mode,
    resolve_screen_policy,
)
from .listings import fetch_moex_board_listings, board_meta

__all__ = [
    "MarketDataPort",
    "Candle",
    "BrokerExecutionPort",
    "candle_to_market_payload",
    "timeframe_to_interval_min",
    "NoopBrokerExecution",
    "QuikTransport",
    "QuikCandleRow",
    "NullQuikTransport",
    "UnavailableQuikTransport",
    "QuikBrokerExecution",
    "build_quik_transport",
    "MockMarketDataAdapter",
    "CcxtMarketDataAdapter",
    "QuikMarketDataAdapter",
    "RiskLimits",
    "RiskDecision",
    "RiskManager",
    "OrderIntent",
    "OrderManager",
    "OrderSide",
    "OrderStatus",
    "OrderResult",
    "Position",
    "StrategySignal",
    "EnsembleManager",
    "WeightedDecision",
    "CandleStore",
    "StoredBar",
    "TradingRuntime",
    "get_runtime",
    "StrategyPlugin",
    "StrategyDecision",
    "StrategyHealth",
    "Instrument",
    "UniverseConfig",
    "UniverseLimits",
    "UniverseManager",
    "UniverseSnapshot",
    "ScreenPolicy",
    "BUILTIN_PROFILES",
    "instrument_from_dict",
    "normalize_universe_mode",
    "resolve_screen_policy",
    "fetch_moex_board_listings",
    "board_meta",
    "TRADING_AI_TOOL_CLASSES",
    "sma",
    "ema",
    "FormulaSeries",
    "equity_pnl",
    "max_drawdown",
]
