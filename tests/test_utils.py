import sys, os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
import unittest
import pandas as pd
from trading_bot.MULTI_SYMBOLS import get_all_tickers_cached


class DummyClient:
    def get_all_tickers(self):
        return [
            {"symbol": "BTCUSDT", "price": "50000"},
            {"symbol": "ETHUSDT", "price": "4000"},
        ]


class TestUtils(unittest.TestCase):
    def test_get_all_tickers_cached(self):
        client = DummyClient()
        tickers = get_all_tickers_cached(client, cache_ttl=1)
        self.assertIn("BTCUSDT", tickers)
        self.assertEqual(tickers["BTCUSDT"], 50000.0)
        self.assertIn("ETHUSDT", tickers)
        self.assertEqual(tickers["ETHUSDT"], 4000.0)


if __name__ == "__main__":
    unittest.main()
