"""Type stubs for Cython-compiled backtest_engine module (MULTI_ASSETS).

Covers only the public ``def`` functions exposed by backtest_engine.pyx.
cdef/cpdef internals are not accessible from Python and are omitted.
"""

from typing import Optional

import numpy as np
import numpy.typing as npt


def backtest_from_dataframe_fast(
    close_prices: npt.NDArray[np.float64],
    open_prices: npt.NDArray[np.float64],
    high_prices: npt.NDArray[np.float64],
    low_prices: npt.NDArray[np.float64],
    ema1_values: npt.NDArray[np.float64],
    ema2_values: npt.NDArray[np.float64],
    stoch_rsi_values: npt.NDArray[np.float64],
    atr_values: npt.NDArray[np.float64],
    hv_values: Optional[npt.NDArray[np.float64]] = ...,
    sma_long_values: Optional[npt.NDArray[np.float64]] = ...,
    adx_values: Optional[npt.NDArray[np.float64]] = ...,
    trix_histo_values: Optional[npt.NDArray[np.float64]] = ...,
    initial_wallet: float = ...,
    scenario: str = ...,
    use_sma: bool = ...,
    use_adx: bool = ...,
    use_trix: bool = ...,
    atr_filter_multiplier: float = ...,
) -> dict[str, object]: ...


def vectorized_ema(
    prices: npt.NDArray[np.float64],
    period: int,
) -> npt.NDArray[np.float64]: ...


def vectorized_rsi(
    prices: npt.NDArray[np.float64],
) -> npt.NDArray[np.float64]: ...


def vectorized_atr(
    high: npt.NDArray[np.float64],
    low: npt.NDArray[np.float64],
    close: npt.NDArray[np.float64],
    period: int = ...,
) -> npt.NDArray[np.float64]: ...


def vectorized_stoch_rsi(
    rsi: npt.NDArray[np.float64],
    period: int = ...,
) -> npt.NDArray[np.float64]: ...

