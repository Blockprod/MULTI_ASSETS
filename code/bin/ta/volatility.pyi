"""Type stubs for ta.volatility — covers MULTI_ASSETS usage only."""

import pandas as pd


class AverageTrueRange:
    def __init__(
        self,
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        window: int = 14,
        fillna: bool = False,
    ) -> None: ...
    def average_true_range(self) -> pd.Series: ...
