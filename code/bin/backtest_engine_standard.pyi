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
    initial_wallet: float = 10000.0,
    scenario: str = "StochRSI",
    use_sma: bool = False,
    use_adx: bool = False,
    use_trix: bool = False,
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
