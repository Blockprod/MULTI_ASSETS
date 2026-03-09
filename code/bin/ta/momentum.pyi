"""Type stubs for ta.momentum — covers MULTI_ASSETS usage only."""

import pandas as pd


class RSIIndicator:
    def __init__(self, close: pd.Series, window: int = 14, fillna: bool = False) -> None: ...
    def rsi(self) -> pd.Series: ...
