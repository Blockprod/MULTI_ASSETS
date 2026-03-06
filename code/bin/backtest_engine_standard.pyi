"""Type stubs for Cython-compiled backtest_engine_standard module."""

from typing import Any, Dict, List, Optional

import numpy as np
import numpy.typing as npt


def backtest_from_dataframe_fast(
    close_prices: npt.NDArray[np.float64],
    high_prices: npt.NDArray[np.float64],
    low_prices: npt.NDArray[np.float64],
    ema1_values: npt.NDArray[np.float64],
    ema2_values: npt.NDArray[np.float64],
    stoch_rsi_values: npt.NDArray[np.float64],
    atr_values: npt.NDArray[np.float64],
    sma_long_values: Optional[npt.NDArray[np.float64]] = None,
    adx_values: Optional[npt.NDArray[np.float64]] = None,
    trix_histo_values: Optional[npt.NDArray[np.float64]] = None,
    open_prices: Optional[npt.NDArray[np.float64]] = None,
    volume_values: Optional[npt.NDArray[np.float64]] = None,
    vol_sma_values: Optional[npt.NDArray[np.float64]] = None,
    initial_wallet: float = 10000.0,
    scenario: str = "StochRSI",
    use_sma: bool = False,
    use_adx: bool = False,
    use_trix: bool = False,
    use_vol_filter: bool = False,
    taker_fee: float = 0.0007,
    slippage_buy: float = 0.0001,
    slippage_sell: float = 0.0001,
    atr_multiplier: float = 8.0,
    atr_stop_multiplier: float = 3.0,
    stoch_threshold_buy: float = 0.8,
    stoch_threshold_sell: float = 0.2,
    adx_threshold: float = 25.0,
    sizing_mode: str = "baseline",
    risk_per_trade: float = 0.05,
    partial_enabled: bool = False,
    partial_threshold_1: float = 0.02,
    partial_threshold_2: float = 0.04,
    partial_pct_1: float = 0.50,
    partial_pct_2: float = 0.30,
    min_notional: float = 5.0,
    stoch_threshold_buy_min: float = 0.05,
    breakeven_enabled: bool = True,
    breakeven_trigger_pct: float = 0.015,
    cooldown_candles: int = 0,
    mtf_bullish: Optional[npt.NDArray[np.float64]] = None,
    use_mtf_filter: bool = False,
) -> Dict[str, Any]: ...


def calculate_indicators_fast(
    close_prices: npt.NDArray[np.float64],
    high_prices: npt.NDArray[np.float64],
    low_prices: npt.NDArray[np.float64],
    ema1_period: int,
    ema2_period: int,
    stoch_period: int,
    sma_long: int = 0,
    adx_period: int = 0,
    trix_length: int = 0,
    trix_signal: int = 0,
) -> Any: ...
