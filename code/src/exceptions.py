"""
Trading Bot Exception Hierarchy
================================
Structured exception classes for clean error handling and recovery.

Hierarchy
---------
TradingBotError (base)
├── ConfigError           — Invalid or missing configuration
├── ExchangeError         — Exchange connectivity / API issues
│   ├── RateLimitError    — Rate limit exceeded (back off & retry)
│   ├── CircuitOpenError  — Circuit breaker open (repeated network failures)
│   ├── InsufficientFundsError — Not enough balance
│   └── OrderError        — Order placement/cancellation failure
├── DataError             — Market data fetching / processing
│   ├── StaleDataError    — Data older than expected
│   └── InsufficientDataError — Not enough bars for indicators
├── StrategyError         — Indicator / signal calculation errors
├── StateError            — State persistence (save/load/corrupt)
└── CapitalProtectionError — Kill-switch / daily loss limit triggered
"""


class TradingBotError(Exception):
    """Base exception for all trading bot errors."""
    pass


# ── Config ──────────────────────────────────────────────────
class ConfigError(TradingBotError):
    """Invalid or missing configuration."""
    pass


# ── Exchange ────────────────────────────────────────────────
class ExchangeError(TradingBotError):
    """Exchange connectivity or API errors."""
    pass


class RateLimitError(ExchangeError):
    """Exchange rate limit exceeded — caller should back off and retry."""
    pass


class CircuitOpenError(ExchangeError):  # TS-P2-01
    """Circuit breaker ouvert — API Binance en quarantaine après des échecs réseau répétés.

    Levée par _request() dans exchange_client.py lorsque le nombre d'échecs consécutifs
    atteint `circuit_breaker_threshold`. L'API reste bloquée pendant `circuit_breaker_reset_seconds`.
    Les appelants ne doivent pas intercepter cette exception sans la propager.
    """
    pass


class InsufficientFundsError(ExchangeError):
    """Account balance insufficient for the requested operation."""
    pass


class BalanceUnavailableError(ExchangeError):
    """Account balance could not be retrieved from the exchange (P0-02).

    Raised by get_spot_balance_usdc when the API call fails. Callers should
    skip the current trading cycle rather than operating on a zero balance.
    """
    pass


class OrderError(ExchangeError):
    """Order placement, modification, or cancellation failure."""

    def __init__(self, message: str, order_id: str | None = None, symbol: str | None = None):
        super().__init__(message)
        self.order_id = order_id
        self.symbol = symbol


# ── Data ────────────────────────────────────────────────────
class DataError(TradingBotError):
    """Market data fetching or processing error."""
    pass


class StaleDataError(DataError):
    """Market data is older than the expected freshness threshold."""

    def __init__(self, message: str, age_seconds: float | None = None):
        super().__init__(message)
        self.age_seconds = age_seconds


class InsufficientDataError(DataError):
    """Not enough data bars to compute required indicators."""

    def __init__(self, message: str, required: int = 0, available: int = 0):
        super().__init__(message)
        self.required = required
        self.available = available


# ── Strategy ────────────────────────────────────────────────
class StrategyError(TradingBotError):
    """Indicator or signal calculation error."""
    pass


class SizingError(StrategyError):
    """Position sizing calculation failure (P0-05).

    Raised when compute_position_size_* encounters an unexpected error.
    Callers should skip the trade rather than silently using qty=0.
    """
    pass


# ── State ───────────────────────────────────────────────────
class StateError(TradingBotError):
    """State persistence errors (save / load / corruption)."""
    pass


# ── Capital Protection ──────────────────────────────────────
class CapitalProtectionError(TradingBotError):
    """Kill-switch or daily loss limit triggered — trading should halt."""

    def __init__(self, message: str, drawdown_pct: float | None = None):
        super().__init__(message)
        self.drawdown_pct = drawdown_pct
