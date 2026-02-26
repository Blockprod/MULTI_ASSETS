import os
from src.trading_bot.config import Config

cfg = Config.from_env()
print("[CONFIG] Active values:")
print(f"TAKER_FEE={cfg.taker_fee}")
print(f"ATR_MULTIPLIER={cfg.atr_multiplier}")
print(f"ATR_STOP_MULTIPLIER={cfg.atr_stop_multiplier}")
print(f"INITIAL_WALLET={cfg.initial_wallet}")
print(f"BACKTEST_DAYS={cfg.backtest_days}")

# Also show raw envs if present
for k in [
    "TAKER_FEE",
    "ATR_MULTIPLIER",
    "ATR_STOP_MULTIPLIER",
    "INITIAL_WALLET",
    "BACKTEST_DAYS",
]:
    print(f"ENV {k}={os.getenv(k)}")
