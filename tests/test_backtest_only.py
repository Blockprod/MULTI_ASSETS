#!/usr/bin/env python3
"""Quick backtest-only script to test with fixed window and legacy mode."""
import sys
sys.path.insert(0, r'c:\Users\averr\BIBOT\MULTI_ASSETS_BOT\code')

# Run only the backtest part
import argparse
from MULTI_SYMBOLS import (
    config, crypto_pairs, timeframes, 
    run_parallel_backtests, logger, console,
    VERBOSE_LOGS, BACKTEST_MODE, LEGACY_MODE
)
from datetime import datetime, timedelta
from rich.panel import Panel

# Setup
parser = argparse.ArgumentParser(description='Run backtests only with fixed window')
parser.add_argument('--fixed-window', type=str, help='Fixed date window START:END')
parser.add_argument('--legacy-mode', action='store_true', help='Use legacy mode')
args, unknown = parser.parse_known_args()

# Apply fixed window
if args.fixed_window:
    start_str, end_str = args.fixed_window.split(':', 1)
    config.fixed_window_start = start_str.strip()
    config.fixed_window_end = end_str.strip()
    start_date = config.fixed_window_start
    logger.info(f"Using fixed window: {config.fixed_window_start} -> {config.fixed_window_end}")
else:
    today = datetime.today()
    start_date = (today - timedelta(days=config.backtest_days)).strftime("%Y-%m-%d")

# Set legacy mode
if hasattr(sys.modules['__main__'], 'LEGACY_MODE'):
    sys.modules['__main__'].LEGACY_MODE = bool(args.legacy_mode)
else:
    import __main__
    __main__.LEGACY_MODE = bool(args.legacy_mode)

logger.info(f"LEGACY_MODE = {bool(args.legacy_mode)}")

# Run backtests
logger.info("Running backtests...")
all_results = run_parallel_backtests(crypto_pairs, start_date, timeframes, sizing_mode='baseline')

# Display results
for backtest_pair, data in all_results.items():
    if not data['results']:
        console.print(f"[red]Aucun résultat pour {backtest_pair}[/red]")
        continue

    # Find best result
    best_result = max(data['results'], key=lambda x: x['final_wallet'] - x['initial_wallet'])
    best_profit = best_result['final_wallet'] - best_result['initial_wallet']
    
    # Display in panel
    console.print(Panel(
        f"[bold cyan]Backtest Period:[/bold cyan] ({best_result['timeframe']} {best_result['ema_periods'][0]}/{best_result['ema_periods'][1]})\n"
        f"[bold cyan]Scenario:[/bold cyan] {best_result['scenario']}\n"
        f"[bold green]Profit:[/bold green] ${best_profit:,.2f}\n"
        f"[bold yellow]Final Wallet:[/bold yellow] ${best_result['final_wallet']:,.2f}\n"
        f"[bold red]Max Drawdown:[/bold red] {best_result['max_drawdown']*100:.2f}%\n"
        f"[bold cyan]Win Rate:[/bold cyan] {best_result['win_rate']:.2f}%",
        title=f"*** BEST RESULT — {backtest_pair} ***",
        expand=False
    ))
