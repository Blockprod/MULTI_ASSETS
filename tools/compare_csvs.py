import pandas as pd
import numpy as np
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
p1 = root / "all_backtest_trades_export.csv"
p2 = root / "trades_export.csv"
p1_meta = root / "all_backtest_trades_export.meta.json"
p2_meta = root / "trades_export.meta.json"

print(f"[INFO] Loading: {p1}")
print(f"[INFO] Loading: {p2}")

# Load metadata if available
import json

meta1, meta2 = None, None
try:
    if p1_meta.exists():
        with open(p1_meta, "r", encoding="utf-8") as f:
            meta1 = json.load(f)
        print(f"[INFO] Metadata bot: {meta1}")
except Exception as e:
    print(f"[WARN] Cannot load {p1_meta}: {e}")
try:
    if p2_meta.exists():
        with open(p2_meta, "r", encoding="utf-8") as f:
            meta2 = json.load(f)
        print(f"[INFO] Metadata csv: {meta2}")
except Exception as e:
    print(f"[WARN] Cannot load {p2_meta}: {e}")

df1 = pd.read_csv(p1)
df2 = pd.read_csv(p2)

for d in [df1, df2]:
    if "profit" in d.columns:
        d["profit"] = pd.to_numeric(d["profit"], errors="coerce")

# Parse df2 ema_periods into ints


def parse_tuple(s):
    try:
        s = str(s).strip()
        if s.startswith("(") and s.endswith(")"):
            a, b = s[1:-1].split(",")
            return int(a.strip()), int(b.strip())
    except Exception:
        return (np.nan, np.nan)
    return (np.nan, np.nan)


if "ema_periods" in df2.columns:
    ema_parsed = df2["ema_periods"].apply(parse_tuple)
    df2["ema1"] = [t[0] for t in ema_parsed]
    df2["ema2"] = [t[1] for t in ema_parsed]

# Aggregates
agg1 = (
    df1.groupby(["pair", "timeframe", "scenario", "ema1", "ema2"], dropna=False)[
        "profit"
    ]
    .sum(min_count=1)
    .reset_index()
)
agg2 = (
    df2.groupby(
        ["backtest_pair", "timeframe", "scenario", "ema1", "ema2"], dropna=False
    )["profit"]
    .sum(min_count=1)
    .reset_index()
)

# Common combos
common = agg2.merge(
    agg1,
    left_on=["backtest_pair", "timeframe", "scenario", "ema1", "ema2"],
    right_on=["pair", "timeframe", "scenario", "ema1", "ema2"],
    how="inner",
    suffixes=("_csv", "_bot"),
)

print("\n[RESULT] Common combos count:", len(common))
print(common.head(20).to_string(index=False))

# Compare trade counts per combo (SELL rows only)
df1_sells = df1[df1.get("type") == "SELL"]
df2_sells = df2[df2.get("type") == "SELL"]
cnt1 = (
    df1_sells.groupby(["pair", "timeframe", "scenario", "ema1", "ema2"])
    .size()
    .reset_index(name="sell_count_bot")
)
cnt2 = (
    df2_sells.groupby(["backtest_pair", "timeframe", "scenario", "ema1", "ema2"])
    .size()
    .reset_index(name="sell_count_csv")
)
counts = cnt2.merge(
    cnt1,
    left_on=["backtest_pair", "timeframe", "scenario", "ema1", "ema2"],
    right_on=["pair", "timeframe", "scenario", "ema1", "ema2"],
    how="inner",
)
print("\n[RESULT] Sell trade counts (first 20):")
print(counts.head(20).to_string(index=False))

# Profit deltas for common combos
common["delta"] = common["profit_csv"] - common["profit_bot"]
print("\n[RESULT] Top 20 profit deltas:")
print(common.sort_values("delta", ascending=False).head(20).to_string(index=False))

# Average profit per SELL trade per combo
avg1 = (
    df1_sells.groupby(["pair", "timeframe", "scenario", "ema1", "ema2"])["profit"]
    .mean()
    .reset_index()
    .rename(columns={"profit": "avg_profit_bot"})
)
avg2 = (
    df2_sells.groupby(["backtest_pair", "timeframe", "scenario", "ema1", "ema2"])[
        "profit"
    ]
    .mean()
    .reset_index()
    .rename(columns={"profit": "avg_profit_csv"})
)
avg_common = avg2.merge(
    avg1,
    left_on=["backtest_pair", "timeframe", "scenario", "ema1", "ema2"],
    right_on=["pair", "timeframe", "scenario", "ema1", "ema2"],
    how="inner",
)
avg_common["avg_delta"] = avg_common["avg_profit_csv"] - avg_common["avg_profit_bot"]
print("\n[RESULT] Top 20 average profit deltas per SELL:")
print(
    avg_common.sort_values("avg_delta", ascending=False).head(20).to_string(index=False)
)

# Write a detailed summary CSV for further analysis
summary_out = Path(__file__).resolve().parent / "csv_comparison_summary.csv"
full = common.merge(
    counts,
    left_on=["backtest_pair", "timeframe", "scenario", "ema1", "ema2"],
    right_on=["backtest_pair", "timeframe", "scenario", "ema1", "ema2"],
    how="left",
)
full = full.merge(
    avg_common,
    left_on=["backtest_pair", "timeframe", "scenario", "ema1", "ema2"],
    right_on=["backtest_pair", "timeframe", "scenario", "ema1", "ema2"],
    how="left",
)
cols = [
    "backtest_pair",
    "timeframe",
    "scenario",
    "ema1",
    "ema2",
    "profit_csv",
    "profit_bot",
    "delta",
    "sell_count_csv",
    "sell_count_bot",
    "avg_profit_csv",
    "avg_profit_bot",
    "avg_delta",
]
full_to_write = full[cols].copy()
full_to_write.sort_values(["timeframe", "scenario", "ema1", "ema2"], inplace=True)
full_to_write.to_csv(summary_out, index=False)
print(f"\n[INFO] Wrote detailed summary to: {summary_out}")

pairs1 = sorted(set(zip(df1["ema1"].dropna(), df1["ema2"].dropna())))
pairs2 = sorted(set(zip(df2["ema1"].dropna(), df2["ema2"].dropna())))
print("\n[RESULT] EMA pairs in bot-export:", pairs1)
print("[RESULT] EMA pairs in csv-export:", pairs2)

# Summaries for quick comparison
sum1 = (
    agg1.groupby(["pair", "timeframe", "scenario"])["profit"]
    .sum(min_count=1)
    .reset_index()
    .rename(columns={"profit": "pnl_bot"})
)
sum2 = (
    agg2.groupby(["backtest_pair", "timeframe", "scenario"])["profit"]
    .sum(min_count=1)
    .reset_index()
    .rename(columns={"profit": "pnl_csv"})
)
summary = sum2.merge(
    sum1,
    left_on=["backtest_pair", "timeframe", "scenario"],
    right_on=["pair", "timeframe", "scenario"],
    how="outer",
)
print("\n[SUMMARY] PnL by pair/timeframe/scenario (not split by EMA):")
print(summary.head(20).to_string(index=False))
