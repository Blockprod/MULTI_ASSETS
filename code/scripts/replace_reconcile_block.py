"""C-03: Replace reconcile block in MULTI_SYMBOLS.py with thin wrappers."""
import sys
import os

src_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'MULTI_SYMBOLS.py')

with open(src_path, encoding='utf-8') as f:
    content = f.read()

# --- Locate the block ---
start_marker = '@dataclass\nclass _PairStatus:'
end_marker = '    logger.info("[RECONCILE] V\u00e9rification termin\u00e9e.")'

start_idx = content.find(start_marker)
end_idx = content.find(end_marker)

if start_idx == -1:
    print("ERROR: start_marker not found", file=sys.stderr)
    sys.exit(1)
if end_idx == -1:
    print("ERROR: end_marker not found", file=sys.stderr)
    sys.exit(1)

end_of_block = content.find('\n', end_idx) + 1  # include the trailing newline
print(f"Block found: char {start_idx} to {end_of_block}")
print(f"Lines {content[:start_idx].count(chr(10))+1} to {content[:end_of_block].count(chr(10))}")

# --- Replacement wrappers ---
replacement = '''from position_reconciler import (
    _ReconcileDeps,
    _PairStatus,
    _check_pair_vs_exchange as _check_pair_impl,
    _handle_pair_discrepancy as _handle_pair_impl,
    reconcile_positions_with_exchange as _reconcile_impl,
)


def _make_reconcile_deps() -> _ReconcileDeps:
    """C-03: Injecte les globaux du module dans les fonctions de position_reconciler."""
    return _ReconcileDeps(
        client=client,
        bot_state=bot_state,
        bot_state_lock=_bot_state_lock,
        save_fn=save_bot_state,
        send_alert_fn=send_trading_alert_email,
        place_sl_fn=place_exchange_stop_loss_order,
        get_exchange_info_fn=get_cached_exchange_info,
    )


def _check_pair_vs_exchange(pair_info: Dict[str, Any]) -> 'Optional[_PairStatus]':
    """C-03 wrapper \u2014 d\u00e9l\u00e8gue \u00e0 position_reconciler avec les globaux inject\u00e9s."""
    return _check_pair_impl(pair_info, _make_reconcile_deps())


def _handle_pair_discrepancy(status: '_PairStatus') -> None:
    """C-03 wrapper \u2014 d\u00e9l\u00e8gue \u00e0 position_reconciler avec les globaux inject\u00e9s."""
    return _handle_pair_impl(status, _make_reconcile_deps())


def reconcile_positions_with_exchange(crypto_pairs_list: List[Dict[str, Any]]) -> None:
    """V\u00e9rifie la coh\u00e9rence entre bot_state et les positions r\u00e9elles sur Binance.

    C\u00f4t\u00e9 MULTI_SYMBOLS: wrapper qui injecte les globaux dans position_reconciler.
    """
    return _reconcile_impl(crypto_pairs_list, _make_reconcile_deps())

'''

new_content = content[:start_idx] + replacement + content[end_of_block:]

with open(src_path, 'w', encoding='utf-8') as f:
    f.write(new_content)

old_lines = content.count('\n')
new_lines = new_content.count('\n')
print(f"Done. Lines: {old_lines} -> {new_lines} (delta: {new_lines - old_lines})")
