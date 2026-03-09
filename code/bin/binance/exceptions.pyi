"""Minimal type stub for binance.exceptions (python-binance 1.0.35).

Covers only BinanceAPIException, which is:
  - caught as ``except BinanceAPIException as e`` in exchange_client.py
  - its .code (int) and .message (str) attributes accessed directly
  - raised manually as BinanceAPIException(response, error_code, error_msg)
"""

from typing import Any


class BinanceAPIException(Exception):
    code: int
    message: str

    def __init__(
        self,
        response: Any,
        status_code: int | str,
        error_msg: str,
    ) -> None: ...
