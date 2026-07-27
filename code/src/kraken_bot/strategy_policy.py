"""Pure Binance strategy policy shared by backtest and live trading."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Tuple

import pandas as pd


@dataclass(frozen=True)
class EntryLevels:
    """Protection levels fixed when a position is opened."""

    atr_stop_multiplier: float
    stop_loss: float
    trailing_activation: float


@dataclass(frozen=True)
class StrategySnapshot:
    """Complete, immutable strategy contract shared by WF, backtest and live."""

    pair: str
    timeframe: str
    ema1_period: int
    ema2_period: int
    scenario: str
    stoch_buy_min: float
    stoch_buy_max: float
    stoch_sell_exit: float
    stoch_period: int = 14
    sma_long: Optional[int] = None
    adx_period: Optional[int] = None
    trix_length: Optional[int] = None
    trix_signal: Optional[int] = None
    wf_method: str = "legacy"
    wf_validated: bool = False
    wf_folds_completed: int = 0
    wf_folds_requested: int = 4
    avg_oos_sharpe: float = 0.0
    avg_oos_win_rate: float = 0.0
    oos_is_decay: float = 0.0
    oos_total_trades: int = 0
    validated_at: str = ""
    data_end_time: str = ""
    schema_version: int = 2
    snapshot_id: str = ""

    def __post_init__(self) -> None:
        if not self.pair or not self.timeframe or not self.scenario:
            raise ValueError("StrategySnapshot pair/timeframe/scenario are required")
        if self.ema1_period <= 0 or self.ema2_period <= self.ema1_period:
            raise ValueError("StrategySnapshot requires 0 < ema1_period < ema2_period")
        if not 0.0 <= self.stoch_buy_min < self.stoch_buy_max <= 1.0:
            raise ValueError("Invalid StochRSI buy thresholds")
        if not 0.0 <= self.stoch_sell_exit <= 1.0:
            raise ValueError("Invalid StochRSI sell threshold")
        if self.wf_validated and self.wf_folds_completed != self.wf_folds_requested:
            raise ValueError("A WF-validated snapshot requires every requested fold")
        if not self.validated_at:
            object.__setattr__(
                self,
                "validated_at",
                datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            )
        expected_id = self._compute_id()
        if self.snapshot_id and self.snapshot_id != expected_id:
            raise ValueError("StrategySnapshot identifier does not match its payload")
        object.__setattr__(self, "snapshot_id", expected_id)

    def _payload(self) -> Dict[str, Any]:
        return {
            key: value
            for key, value in self.__dict__.items()
            if key != "snapshot_id"
        }

    def _compute_id(self) -> str:
        encoded = json.dumps(
            self._payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
        return hashlib.sha256(encoded).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        return {**self._payload(), "snapshot_id": self.snapshot_id}

    def as_best_params(self) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "timeframe": self.timeframe,
            "ema1_period": self.ema1_period,
            "ema2_period": self.ema2_period,
            "scenario": self.scenario,
            "stoch_period": self.stoch_period,
            "stoch_buy_min": self.stoch_buy_min,
            "stoch_buy_max": self.stoch_buy_max,
            "stoch_sell_exit": self.stoch_sell_exit,
            "snapshot_id": self.snapshot_id,
            "wf_validated": self.wf_validated,
            "wf_folds_completed": self.wf_folds_completed,
            "wf_folds_requested": self.wf_folds_requested,
        }
        for key in ("sma_long", "adx_period", "trix_length", "trix_signal"):
            value = getattr(self, key)
            if value is not None:
                params[key] = value
        return params

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "StrategySnapshot":
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: payload[key] for key in allowed if key in payload})

    @classmethod
    def from_wf_config(
        cls,
        pair: str,
        config: Mapping[str, Any],
        *,
        data_end_time: str = "",
    ) -> "StrategySnapshot":
        ema_periods: Any = config.get("ema_periods")
        if not isinstance(ema_periods, (list, tuple)) or len(ema_periods) < 2:
            raise ValueError("WF config requires two EMA periods")
        return cls(
            pair=pair,
            timeframe=str(config["timeframe"]),
            ema1_period=int(ema_periods[0]),
            ema2_period=int(ema_periods[1]),
            scenario=str(config["scenario"]),
            stoch_buy_min=float(config["stoch_buy_min"]),
            stoch_buy_max=float(config["stoch_buy_max"]),
            stoch_sell_exit=float(config["stoch_sell_exit"]),
            stoch_period=int(config.get("stoch_period", 14)),
            sma_long=config.get("sma_long"),
            adx_period=config.get("adx_period"),
            trix_length=config.get("trix_length"),
            trix_signal=config.get("trix_signal"),
            wf_method=str(config.get("method", "optuna")),
            wf_validated=bool(config.get("passed_oos_gates", False)),
            wf_folds_completed=int(config.get("wf_folds_completed", len(config.get("folds", [])))),
            wf_folds_requested=int(config.get("wf_folds_requested", 4)),
            avg_oos_sharpe=float(config.get("avg_oos_sharpe", 0.0)),
            avg_oos_win_rate=float(config.get("avg_oos_win_rate", 0.0)),
            oos_is_decay=float(config.get("oos_is_decay", 0.0)),
            oos_total_trades=int(config.get("oos_total_trades", 0)),
            data_end_time=data_end_time,
        )


def _get(row: Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:
    getter = getattr(row, "get", None)
    if callable(getter):
        return getter(key, default)
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        return default


def _finite_float(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def atr_median_window(periods_per_year: int) -> int:
    """Return the exact rolling window used for the 30-day ATR median."""
    candles_per_day = max(1.0, float(periods_per_year) / 365.0)
    return max(10, int(candles_per_day * 30.0))


def periods_per_year_for_timeframe(timeframe: str) -> int:
    mapping = {
        "1m": 525960,
        "5m": 105192,
        "15m": 35064,
        "30m": 17532,
        "1h": 8766,
        "4h": 2191,
        "1d": 365,
    }
    return mapping.get(timeframe, 8766)


def compute_mtf_bullish(
    df: pd.DataFrame,
    ema_fast: int,
    ema_slow: int,
) -> pd.Series:
    """Return the completed-4h bullish regime aligned to the source index."""
    if not isinstance(df.index, pd.DatetimeIndex):
        return pd.Series(index=df.index, data=float("nan"), dtype=float)
    close_4h = df["close"].resample("4h").last().dropna()
    fast = close_4h.ewm(span=ema_fast, adjust=False).mean()
    slow = close_4h.ewm(span=ema_slow, adjust=False).mean()
    completed = (fast > slow).astype(float).shift(1).fillna(0.0)
    return completed.reindex(df.index, method="ffill").fillna(0.0)


def adaptive_atr_multiplier(
    base_multiplier: float,
    atr_value: Any,
    atr_median_30d: Any,
    *,
    minimum: float = 1.5,
    maximum: float = 5.0,
) -> float:
    """Scale the stop multiplier with volatility and clamp it deterministically."""
    atr = _finite_float(atr_value)
    median = _finite_float(atr_median_30d)
    base = float(base_multiplier)
    if atr is None or median is None or atr <= 0 or median <= 0:
        return base
    scaled = base * math.sqrt(atr / median)
    return max(minimum, min(maximum, scaled))


def entry_levels(
    entry_price: float,
    atr_value: float,
    atr_median_30d: Any,
    *,
    atr_stop_multiplier: float,
    atr_multiplier: float,
) -> EntryLevels:
    """Compute the immutable stop and trailing activation levels at entry."""
    effective_stop_multiplier = adaptive_atr_multiplier(
        atr_stop_multiplier,
        atr_value,
        atr_median_30d,
    )
    return EntryLevels(
        atr_stop_multiplier=effective_stop_multiplier,
        stop_loss=entry_price - effective_stop_multiplier * atr_value,
        trailing_activation=trailing_activation_level(
            entry_price, atr_value, atr_multiplier
        ),
    )


def trailing_activation_level(
    entry_price: float,
    atr_at_entry: float,
    atr_multiplier: float,
) -> float:
    return float(entry_price) + float(atr_multiplier) * float(atr_at_entry)


def evaluate_buy_signal(
    row: Mapping[str, Any] | Any,
    quote_balance: float,
    best_params: Mapping[str, Any],
    config: Any,
    *,
    stoch_buy_min: Optional[float] = None,
    stoch_buy_max: Optional[float] = None,
) -> Tuple[bool, str]:
    """Evaluate the shared long-entry signal without side effects."""
    if quote_balance <= 0:
        return False, "Solde quote insuffisant"

    ema1 = _finite_float(_get(row, "ema1"))
    ema2 = _finite_float(_get(row, "ema2"))
    stoch = _finite_float(_get(row, "stoch_rsi"))
    if ema1 is None or ema2 is None or stoch is None:
        return False, "Indicateurs EMA/StochRSI invalides"

    buy_min = (
        float(stoch_buy_min)
        if stoch_buy_min is not None
        else float(getattr(config, "stoch_rsi_buy_min", 0.05))
    )
    buy_max = (
        float(stoch_buy_max)
        if stoch_buy_max is not None
        else float(getattr(config, "stoch_rsi_buy_max", 0.8))
    )
    if ema1 <= ema2:
        return False, f"EMA1 ({ema1:.8g}) <= EMA2 ({ema2:.8g})"
    if stoch >= buy_max:
        return False, f"StochRSI ({stoch * 100:.2f}%) >= {buy_max * 100:.1f}%"
    if stoch <= buy_min:
        return False, f"StochRSI ({stoch * 100:.2f}%) <= {buy_min * 100:.1f}% (trop bas)"

    scenario = str(best_params.get("scenario", "StochRSI"))
    close = _finite_float(_get(row, "close"))
    if scenario == "StochRSI_SMA":
        sma = _finite_float(_get(row, "sma_long"))
        if close is not None and sma is not None and close <= sma:
            return False, f"Prix ({close:.8g}) <= SMA ({sma:.8g})"

    if scenario == "StochRSI_ADX":
        adx = _finite_float(_get(row, "adx"))
        threshold = float(getattr(config, "adx_threshold", 25.0))
        if adx is not None and adx <= threshold:
            return False, f"ADX ({adx:.2f}) <= {threshold}"

    if scenario == "StochRSI_TRIX":
        trix = _finite_float(_get(row, "TRIX_HISTO"))
        if trix is not None and trix <= 0:
            return False, f"TRIX_HISTO ({trix:.6f}) <= 0"

    if getattr(config, "volume_filter_enabled", False):
        volume = _finite_float(_get(row, "volume"))
        volume_sma = _finite_float(_get(row, "vol_sma"))
        if volume is not None and volume_sma is not None and volume_sma > 0:
            if volume <= volume_sma:
                return False, f"Volume ({volume:,.0f}) <= SMA_vol ({volume_sma:,.0f})"

    if getattr(config, "mtf_filter_enabled", False):
        mtf_bullish = _finite_float(_get(row, "mtf_bullish"))
        if mtf_bullish is not None and mtf_bullish < 0.5:
            return False, "Tendance 4h non haussiere"

    return True, "[OK] Signal d'achat valide"


def evaluate_signal_exit(
    row: Mapping[str, Any] | Any,
    best_params: Mapping[str, Any],
    config: Any,
    *,
    stoch_sell_exit: Optional[float] = None,
) -> Tuple[bool, Optional[str]]:
    """Evaluate only the EMA/StochRSI strategy exit, excluding protective stops."""
    del best_params  # Scenarios only affect entry filters in the current Binance policy.
    ema1 = _finite_float(_get(row, "ema1"))
    ema2 = _finite_float(_get(row, "ema2"))
    stoch = _finite_float(_get(row, "stoch_rsi"))
    if ema1 is None or ema2 is None or stoch is None:
        return False, None
    sell_exit = (
        float(stoch_sell_exit)
        if stoch_sell_exit is not None
        else float(getattr(config, "stoch_rsi_sell_exit", 0.4))
    )
    if ema2 > ema1 and stoch > sell_exit:
        return True, "SIGNAL"
    return False, None


def next_max_price(current_price: float, max_price: Optional[float]) -> float:
    """Update a long-position high-water mark."""
    return max(float(current_price), float(max_price or current_price))


def trailing_level(max_price: float, atr_at_entry: float, atr_multiplier: float) -> float:
    return float(max_price) - float(atr_multiplier) * float(atr_at_entry)


def trailing_should_activate(
    current_price: float,
    activation_price: Optional[float],
    already_activated: bool,
) -> bool:
    return (
        not already_activated
        and activation_price is not None
        and float(current_price) >= float(activation_price)
    )


def breakeven_should_activate(
    current_price: float,
    entry_price: float,
    already_triggered: bool,
    trigger_pct: float,
) -> bool:
    if already_triggered or entry_price <= 0:
        return False
    return (current_price - entry_price) / entry_price >= trigger_pct


def breakeven_level(entry_price: float, slippage_buy: float) -> float:
    return float(entry_price) * (1.0 + float(slippage_buy))


def partial_stage(
    profit_pct: float,
    partial_taken_1: bool,
    partial_taken_2: bool,
    threshold_1: float,
    threshold_2: float,
) -> Optional[int]:
    """Return the next partial stage using the same priority in both engines."""
    if not partial_taken_1 and profit_pct >= threshold_1:
        return 1
    if not partial_taken_2 and profit_pct >= threshold_2:
        return 2
    return None


def classify_long_exit(
    current_price: float,
    active_stop: Optional[float],
    trailing_activated: bool,
    trailing_stop: Optional[float],
    signal_exit: bool,
) -> Optional[str]:
    """Classify exits with a single priority contract."""
    if (
        trailing_activated
        and trailing_stop is not None
        and (active_stop is None or trailing_stop > active_stop)
        and current_price <= trailing_stop
    ):
        return "TRAILING_STOP"
    if active_stop is not None and current_price <= active_stop:
        return "STOP_LOSS"
    if signal_exit:
        return "SIGNAL"
    return None
