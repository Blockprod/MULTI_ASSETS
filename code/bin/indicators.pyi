"""Type stubs for Cython-compiled indicators module (MULTI_ASSETS)."""

import pandas as pd


def calculate_indicators(
    df: pd.DataFrame,
    ema1_period: int,
    ema2_period: int,
    stoch_period: int = ...,
    sma_long: int = ...,
    adx_period: int = ...,
    trix_length: int = ...,
    trix_signal: int = ...,
) -> pd.DataFrame: ...
