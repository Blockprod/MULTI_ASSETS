"""C-03 Phase 2: Extract order_manager.py from MULTI_SYMBOLS.py.

Extrait les fonctions du moteur de trading (1450L) dans un module séparé
avec injection de dépendances via _TradingDeps pour compatibilité tests.
"""
from __future__ import annotations

import ast
import re
import sys
import os

ROOT = os.path.join(os.path.dirname(__file__), '..', '..')
SRC = os.path.join(ROOT, 'code', 'src')

ms_path = os.path.join(SRC, 'MULTI_SYMBOLS.py')
om_path = os.path.join(SRC, 'order_manager.py')

with open(ms_path, encoding='utf-8') as f:
    src = f.read()

tree = ast.parse(src)

# Build mapping function_name -> (start_line, end_line) for top-level nodes
func_ranges: dict[str, tuple[int, int]] = {}
for node in ast.walk(tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        if node.col_offset == 0:
            func_ranges[node.name] = (node.lineno, node.end_lineno)

src_lines = src.splitlines(keepends=True)

# Functions to extract (in order):
# Block 1: _cancel_exchange_sl (currently in MULTI_SYMBOLS.py, not with trading block)
# Block 2: _TradeCtx through _execute_buy (contiguous)

cancel_start, cancel_end = func_ranges['_cancel_exchange_sl']
trade_ctx_start = func_ranges['_TradeCtx'][0]
execute_buy_end = func_ranges['_execute_buy'][1]

print(f"_cancel_exchange_sl: L{cancel_start}-{cancel_end} ({cancel_end - cancel_start + 1}L)")
print(f"Trading block: L{trade_ctx_start}-{execute_buy_end} ({execute_buy_end - trade_ctx_start + 1}L)")

block1 = ''.join(src_lines[cancel_start - 1:cancel_end])
block2 = ''.join(src_lines[trade_ctx_start - 1:execute_buy_end])

raw_extracted = block1 + '\n\n' + block2


# ─── Substitutions: module globals → deps.* ──────────────────────────────────

def apply_deps_substitutions(code: str) -> str:
    """Remplace les références aux globaux MULTI_SYMBOLS par deps.*."""
    subs = [
        # client.* method calls (but not inside 'deps.client' already)
        (r'\bclient\.', 'deps.client.'),
        # client= keyword argument
        (r'\bclient=client\b', 'client=deps.client'),
        # bot_state (module-level dict)
        (r'\bbot_state\b', 'deps.bot_state'),
        # _bot_state_lock
        (r'\b_bot_state_lock\b', 'deps.bot_state_lock'),
        # save_bot_state(
        (r'\bsave_bot_state\(', 'deps.save_fn('),
        # send_trading_alert_email(
        (r'\bsend_trading_alert_email\(', 'deps.send_alert_fn('),
        # place_exchange_stop_loss_order(
        (r'\bplace_exchange_stop_loss_order\(', 'deps.place_sl_fn('),
        # safe_market_sell(
        (r'\bsafe_market_sell\(', 'deps.market_sell_fn('),
        # safe_market_buy(
        (r'\bsafe_market_buy\(', 'deps.market_buy_fn('),
        # _update_daily_pnl(
        (r'\b_update_daily_pnl\(', 'deps.update_daily_pnl_fn('),
        # _is_daily_loss_limit_reached()
        (r'\b_is_daily_loss_limit_reached\(\)', 'deps.is_loss_limit_fn()'),
        # generate_buy_condition_checker(
        (r'\bgenerate_buy_condition_checker\(', 'deps.gen_buy_checker_fn('),
        # generate_sell_condition_checker(
        (r'\bgenerate_sell_condition_checker\(', 'deps.gen_sell_checker_fn('),
        # check_if_order_executed(
        (r'\bcheck_if_order_executed\(', 'deps.check_order_executed_fn('),
        # get_usdc_from_all_sells_since_last_buy(
        (r'\bget_usdc_from_all_sells_since_last_buy\(', 'deps.get_usdc_sells_fn('),
        # get_sniper_entry_price(
        (r'\bget_sniper_entry_price\(', 'deps.get_sniper_entry_fn('),
        # check_partial_exits_from_history(
        (r'\bcheck_partial_exits_from_history\(', 'deps.check_partial_exits_fn('),
        # console=console keyword arg (must come BEFORE the standalone console sub)
        (r'\bconsole=console\b', 'console=deps.console'),
        # console standalone (not followed by = i.e. not a keyword arg name)
        (r'\bconsole\b(?!=)', 'deps.console'),
    ]
    for pattern, replacement in subs:
        code = re.sub(pattern, replacement, code)
    return code


transformed = apply_deps_substitutions(raw_extracted)

# ─── Fix function signatures (add deps parameter) ────────────────────────────

sig_fixes = [
    # _cancel_exchange_sl
    (
        "def _cancel_exchange_sl(ctx: '_TradeCtx') -> None:",
        "def _cancel_exchange_sl(ctx: '_TradeCtx', deps: '_TradingDeps') -> None:",
    ),
    # _sync_entry_state
    (
        "def _sync_entry_state(ctx: '_TradeCtx', last_side: Optional[str]) -> None:",
        "def _sync_entry_state(ctx: '_TradeCtx', last_side: Optional[str], deps: '_TradingDeps') -> None:",
    ),
    # _update_trailing_stop
    (
        "def _update_trailing_stop(ctx: '_TradeCtx') -> None:",
        "def _update_trailing_stop(ctx: '_TradeCtx', deps: '_TradingDeps') -> None:",
    ),
    # _execute_partial_sells
    (
        "def _execute_partial_sells(ctx: '_TradeCtx') -> None:",
        "def _execute_partial_sells(ctx: '_TradeCtx', deps: '_TradingDeps') -> None:",
    ),
    # _execute_one_partial (deps before the * keyword-only section)
    (
        "def _execute_one_partial(ctx: '_TradeCtx', *, partial_number: int, sell_pct: float, profit_pct: float) -> None:",
        "def _execute_one_partial(ctx: '_TradeCtx', deps: '_TradingDeps', *, partial_number: int, sell_pct: float, profit_pct: float) -> None:",
    ),
    # _check_and_execute_stop_loss
    (
        "def _check_and_execute_stop_loss(ctx: '_TradeCtx') -> bool:",
        "def _check_and_execute_stop_loss(ctx: '_TradeCtx', deps: '_TradingDeps') -> bool:",
    ),
    # _handle_dust_cleanup
    (
        "def _handle_dust_cleanup(ctx: '_TradeCtx') -> bool:",
        "def _handle_dust_cleanup(ctx: '_TradeCtx', deps: '_TradingDeps') -> bool:",
    ),
    # _execute_signal_sell
    (
        "def _execute_signal_sell(ctx: '_TradeCtx') -> None:",
        "def _execute_signal_sell(ctx: '_TradeCtx', deps: '_TradingDeps') -> None:",
    ),
    # _execute_buy
    (
        "def _execute_buy(ctx: '_TradeCtx') -> None:",
        "def _execute_buy(ctx: '_TradeCtx', deps: '_TradingDeps') -> None:",
    ),
]

for old, new in sig_fixes:
    if old not in transformed:
        print(f"WARNING: signature not found: {old[:60]}", file=sys.stderr)
    transformed = transformed.replace(old, new)

# ─── Fix inter-function calls within extracted code ──────────────────────────

call_fixes = [
    # _cancel_exchange_sl(ctx) → _cancel_exchange_sl(ctx, deps)
    ('_cancel_exchange_sl(ctx)', '_cancel_exchange_sl(ctx, deps)'),
    # _execute_one_partial(ctx, partial_number= → _execute_one_partial(ctx, deps, partial_number=
    ('_execute_one_partial(ctx, partial_number=', '_execute_one_partial(ctx, deps, partial_number='),
]

for old, new in call_fixes:
    if old not in transformed:
        print(f"WARNING: call not found: {old}", file=sys.stderr)
    transformed = transformed.replace(old, new)

# ─── Fix _TradeCtx: pair_state: PairState → pair_state: Dict[str, Any] ──────
# (PairState is defined in MULTI_SYMBOLS.py — would create circular import)
transformed = transformed.replace(
    '    pair_state: PairState\n',
    '    pair_state: Dict[str, Any]  # PairState TypedDict from MULTI_SYMBOLS\n',
)

# ─── Build order_manager.py content ──────────────────────────────────────────

header = '''\
# pylint: disable=trailing-whitespace
"""C-03 Phase 2: Moteur de trading — extrait de MULTI_SYMBOLS.py (God-Object split).

Toutes les fonctions reçoivent leurs dépendances via _TradingDeps pour
- éviter les imports circulaires
- garantir la compatibilité avec les tests (patches ms.*)

Signature type: fn(ctx: _TradeCtx, deps: _TradingDeps, ...) -> ...
"""
from __future__ import annotations

import math
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

from bot_config import config
from display_ui import display_closure_panel, display_sell_signal_panel, display_buy_signal_panel
from email_templates import buy_executed_email, sell_executed_email
from exchange_client import _get_coin_balance, is_valid_stop_loss_order, can_execute_partial_safely
from exceptions import SizingError
from position_sizing import (
    compute_position_size_by_risk,
    compute_position_size_fixed_notional,
    compute_position_size_volatility_parity,
)
from trade_journal import log_trade

logger = __import__('logging').getLogger(__name__)


@dataclass
class _TradingDeps:
    """Injecte les dépendances runtime du moteur de trading (évite les imports circulaires).

    _make_trading_deps() dans MULTI_SYMBOLS.py lit les globaux au moment de l\'appel,
    ce qui garantit que les patches de tests (monkeypatch.setattr(ms, ...)) sont
    effectifs dans les fonctions extraites.
    """
    # Runtime state
    client: Any
    bot_state: Dict[str, Any]
    bot_state_lock: Any              # _bot_state_lock (RLock)
    # Callables (patched in tests at ms.* level)
    save_fn: Callable                      # save_bot_state
    send_alert_fn: Callable                # send_trading_alert_email
    place_sl_fn: Callable                  # place_exchange_stop_loss_order
    market_sell_fn: Callable               # safe_market_sell
    market_buy_fn: Callable                # safe_market_buy
    update_daily_pnl_fn: Callable          # _update_daily_pnl
    is_loss_limit_fn: Callable             # _is_daily_loss_limit_reached
    gen_buy_checker_fn: Callable           # generate_buy_condition_checker
    gen_sell_checker_fn: Callable          # generate_sell_condition_checker
    check_order_executed_fn: Callable      # check_if_order_executed
    get_usdc_sells_fn: Callable            # get_usdc_from_all_sells_since_last_buy
    get_sniper_entry_fn: Callable          # get_sniper_entry_price
    check_partial_exits_fn: Callable       # check_partial_exits_from_history
    console: Any                           # Rich console


'''

om_content = header + transformed + '\n'

# Verify AST before writing
try:
    ast.parse(om_content)
    print("order_manager.py AST: OK")
except SyntaxError as e:
    print(f"order_manager.py AST ERROR: {e}", file=sys.stderr)
    sys.exit(1)

with open(om_path, 'w', encoding='utf-8') as f:
    f.write(om_content)
print(f"Written: {om_path} ({om_content.count(chr(10))} lines)")

# ─── Modify MULTI_SYMBOLS.py: remove extracted blocks ────────────────────────

new_src = src

# 1. Remove _cancel_exchange_sl block (L389~431) — find exact text
cancel_block_text = block1
# We want to remove the blank line before the function too
# Find the "\ndef _cancel_exchange_sl" in the file
cancel_marker_start = '\ndef _cancel_exchange_sl'
cancel_idx = new_src.find(cancel_marker_start)
if cancel_idx == -1:
    print("ERROR: cannot find _cancel_exchange_sl in MULTI_SYMBOLS.py", file=sys.stderr)
    sys.exit(1)
# Find where the block ends (end of function)
# Use block_end from func_ranges
cancel_block_end_in_src = new_src.find('\n', new_src.find('\ndef _cancel_exchange_sl'))
# Actually, use line-based approach:
lines_new = new_src.splitlines(keepends=True)
# Remove lines cancel_start to cancel_end (1-indexed)
# Also remove a comment line before it if present (e.g. "# --- Utility Functions...")
# Actually just remove the function lines
del lines_new[cancel_start - 1:cancel_end]
new_src = ''.join(lines_new)
print(f"Removed _cancel_exchange_sl: lines {cancel_start}-{cancel_end}")

# 2. Remove trading block (_TradeCtx through _execute_buy)
# Re-parse to get updated line numbers
tree2 = ast.parse(new_src)
func_ranges2 = {}
for node in ast.walk(tree2):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        if node.col_offset == 0:
            func_ranges2[node.name] = (node.lineno, node.end_lineno)

trade_start2 = func_ranges2['_TradeCtx'][0]
buy_end2 = func_ranges2['_execute_buy'][1]
print(f"Trading block (after cancel removal): L{trade_start2}-{buy_end2} ({buy_end2 - trade_start2 + 1}L)")

lines_new2 = new_src.splitlines(keepends=True)
del lines_new2[trade_start2 - 1:buy_end2]
new_src = ''.join(lines_new2)
print("Removed trading block")

# 3. Add imports from order_manager to MULTI_SYMBOLS.py
# Add after "from position_reconciler import ..." block
om_import = (
    'from order_manager import (\n'
    '    _TradingDeps,\n'
    '    _TradeCtx,\n'
    '    _cancel_exchange_sl,\n'
    '    _sync_entry_state,\n'
    '    _update_trailing_stop,\n'
    '    _execute_partial_sells,\n'
    '    _execute_one_partial,\n'
    '    _check_and_execute_stop_loss,\n'
    '    _handle_dust_cleanup,\n'
    '    _execute_signal_sell,\n'
    '    _compute_buy_quantity,\n'
    '    _execute_buy,\n'
    ')\n'
)

# Insert after "from position_reconciler import (...)\n"
pr_end_marker = '    reconcile_positions_with_exchange as _reconcile_impl,\n)\n'
pr_end_idx = new_src.find(pr_end_marker)
if pr_end_idx == -1:
    print("ERROR: cannot find position_reconciler import block end", file=sys.stderr)
    sys.exit(1)
insert_pos = pr_end_idx + len(pr_end_marker)
new_src = new_src[:insert_pos] + om_import + new_src[insert_pos:]
print("Added order_manager import block")

# 4. Add _make_trading_deps() factory (after _make_reconcile_deps)
make_trading_deps_fn = '''

def _make_trading_deps() -> _TradingDeps:
    """C-03: Injecte les globaux du module dans les fonctions de order_manager."""
    return _TradingDeps(
        client=client,
        bot_state=bot_state,
        bot_state_lock=_bot_state_lock,
        save_fn=save_bot_state,
        send_alert_fn=send_trading_alert_email,
        place_sl_fn=place_exchange_stop_loss_order,
        market_sell_fn=safe_market_sell,
        market_buy_fn=safe_market_buy,
        update_daily_pnl_fn=_update_daily_pnl,
        is_loss_limit_fn=_is_daily_loss_limit_reached,
        gen_buy_checker_fn=generate_buy_condition_checker,
        gen_sell_checker_fn=generate_sell_condition_checker,
        check_order_executed_fn=check_if_order_executed,
        get_usdc_sells_fn=get_usdc_from_all_sells_since_last_buy,
        get_sniper_entry_fn=get_sniper_entry_price,
        check_partial_exits_fn=check_partial_exits_from_history,
        console=console,
    )
'''

# Insert after _make_reconcile_deps function (find its end)
tree3 = ast.parse(new_src)
func_ranges3 = {}
for node in ast.walk(tree3):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        if node.col_offset == 0:
            func_ranges3[node.name] = (node.lineno, node.end_lineno)

if '_make_reconcile_deps' not in func_ranges3:
    print("ERROR: _make_reconcile_deps not found in updated src", file=sys.stderr)
    sys.exit(1)

mrd_end_line = func_ranges3['_make_reconcile_deps'][1]
lines_new3 = new_src.splitlines(keepends=True)
# Insert after the end of _make_reconcile_deps
lines_new3.insert(mrd_end_line, make_trading_deps_fn)
new_src = ''.join(lines_new3)
print("Added _make_trading_deps() factory")

# 5. Final AST check
try:
    ast.parse(new_src)
    print("MULTI_SYMBOLS.py AST: OK")
except SyntaxError as e:
    print(f"MULTI_SYMBOLS.py AST ERROR: {e}", file=sys.stderr)
    sys.exit(1)

with open(ms_path, 'w', encoding='utf-8') as f:
    f.write(new_src)

old_lines = src.count('\n')
new_lines = new_src.count('\n')
print(f"\nDone. MULTI_SYMBOLS.py: {old_lines} → {new_lines} lines (delta: {new_lines - old_lines})")
print(f"order_manager.py: {om_content.count(chr(10))} lines")
