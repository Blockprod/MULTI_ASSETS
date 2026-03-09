"""Type stubs for ta.trend — covers MULTI_ASSETS usage only."""

import pandas as pd


class ADXIndicator:
    def __init__(
        self,
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        window: int = 14,
        fillna: bool = False,
    ) -> None: ...
    def adx(self) -> pd.Series: ...


class MACD:
    def __init__(
        self,
        close: pd.Series,
        window_slow: int = 26,
        window_fast: int = 12,
        window_sign: int = 9,
        fillna: bool = False,
    ) -> None: ...
    def macd(self) -> pd.Series: ...
    def macd_signal(self) -> pd.Series: ...
    def macd_diff(self) -> pd.Series: ...
