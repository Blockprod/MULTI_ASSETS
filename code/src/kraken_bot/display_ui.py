"""
display_ui.py — Centralised Rich display panels for the trading bot.

Extracted from MULTI_SYMBOLS.py for separation of concerns.
All console output is routed through this module to ensure consistent
formatting, deduplication, and a single PANEL_WIDTH constant.
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Mapping, MutableMapping, Optional

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

from bot_config import config, extract_coin_from_pair
from exchange_client import get_spot_balance_usdc

logger = logging.getLogger('trading_bot')

# ─── Constants ──────────────────────────────────────────────────────────────
PANEL_WIDTH = 120

# Module-level console (shared by all functions unless caller overrides)
console = Console()


# ─── Helpers ────────────────────────────────────────────────────────────────

def _ok(condition: bool) -> str:
    """Return a Rich-formatted OK / NOK tag with visual indicator."""
    return '[bold green]\u2714 OK[/bold green]' if condition else '[bold red]\u2718 NOK[/bold red]'


def _wf_fold_label() -> str:
    folds = int(getattr(config, 'oos_min_folds', 3) or 3)
    return f"{folds}/{folds}"


def _wf_history_label(active: Mapping[str, Any], age_text: str) -> str:
    current_required = int(getattr(config, 'oos_min_folds', 3) or 3)
    try:
        completed = int(active.get('wf_folds_completed') or 0)
    except (TypeError, ValueError):
        completed = 0
    try:
        requested = int(active.get('wf_folds_requested') or current_required)
    except (TypeError, ValueError):
        requested = current_required
    parts = [
        f"{completed}/{requested} folds",
        f"requis courant {current_required}/{current_required}",
    ]
    if requested != current_required:
        parts.append("snapshot ancien")
    parts.append(age_text)
    return " | ".join(parts)


def _fmt_price(price: float) -> str:
    """Format a price with 2 decimal places (≥1) or compact notation (< 1)."""
    return f"{price:.2f}" if price >= 1.0 else f"{price:.8g}"


def _exchange_label(client: Any) -> str:
    broker = str(getattr(client, "broker", "") or os.getenv("BROKER", "")).upper()
    return "Kraken" if broker == "KRAKEN" else "Binance"


# ─── Signal Panels ──────────────────────────────────────────────────────────

def display_buy_signal_panel(
    row, usdc_balance, best_params, scenario, buy_condition,
    console: Console, pair_state: Optional[Mapping[str, Any]] = None, buy_reason=None,
    coin_symbol: Optional[str] = None,
):
    """
    Panneau d'analyse des signaux d'achat avec détails des conditions
    et solde USDC.
    """
    if pair_state is None:
        pair_state = {}

    # Devise de cotation
    try:
        if not coin_symbol:
            coin_symbol = pair_state.get('coin_symbol')
        quote_currency = pair_state.get('quote_currency')
        if not coin_symbol or not quote_currency:
            real_trading_pair = pair_state.get('real_trading_pair')
            if real_trading_pair:
                coin_symbol, quote_currency = extract_coin_from_pair(real_trading_pair)
            else:
                quote_currency, coin_symbol = 'USDC', 'COIN'
    except Exception:
        quote_currency, coin_symbol = 'USDC', 'COIN'

    # Résumé stratégie active
    _ema1_p = best_params.get('ema1_period', '?')
    _ema2_p = best_params.get('ema2_period', '?')
    _tf = best_params.get('timeframe', '?')
    _strategy_label = f"{scenario} EMA({_ema1_p}/{_ema2_p}) {_tf}"

    cond_grid = Table(
        title="[bold white]Analyse des conditions d'achat[/bold white]",
        title_justify="left",
        box=None, show_header=False, pad_edge=False, show_edge=False, padding=(0, 1),
    )
    cond_grid.add_column("condition", width=22, no_wrap=True, style="bold white")
    cond_grid.add_column("result", width=12, no_wrap=True)
    cond_grid.add_column("detail", style="dim")

    cond_grid.add_row("Stratégie active", "", f"[bold cyan]{_strategy_label}[/bold cyan]")
    cond_grid.add_row(f"Solde {quote_currency} > 0", _ok(usdc_balance > 0), f"{usdc_balance:.2f} {quote_currency}")
    def _fmt_ema(v: float) -> str:
        return f"{v:.12f}" if v < 0.01 else f"{v:.8f}"
    cond_grid.add_row("EMA1 > EMA2", _ok(row['ema1'] > row['ema2']), f"EMA{_ema1_p}={_fmt_ema(row['ema1'])}  EMA{_ema2_p}={_fmt_ema(row['ema2'])}")
    _ps_buy_max = pair_state.get('stoch_buy_max') if pair_state else None
    _buy_max = _ps_buy_max if _ps_buy_max is not None else getattr(config, 'stoch_rsi_buy_max', 0.8)
    _srsi_val = row['stoch_rsi'] * 100
    _srsi_display = f"{_srsi_val:.2f}%"
    _srsi_color = "bold green" if _srsi_val < _buy_max * 100 else "bold red"
    cond_grid.add_row(f"StochRSI < {_buy_max*100:.0f}%", _ok(row['stoch_rsi'] < _buy_max), "")
    _ps_buy_min = pair_state.get('stoch_buy_min') if pair_state else None
    _buy_min = _ps_buy_min if _ps_buy_min is not None else getattr(config, 'stoch_rsi_buy_min', 0.05)
    cond_grid.add_row(
        f"StochRSI > {_buy_min*100:.0f}%",
        _ok(row['stoch_rsi'] > _buy_min),
        "",
    )

    # Scenario-specific conditions
    if scenario == 'StochRSI_ADX':
        adx_threshold = best_params.get('adx_threshold', 25)
        adx_val = row.get('adx')
        adx_display = f"{adx_val:.2f}" if adx_val is not None else "N/A"
        cond_grid.add_row(f"ADX > {adx_threshold}", _ok(adx_val is not None and adx_val > adx_threshold), f"ADX={adx_display}")
    if scenario == 'StochRSI_SMA':
        sma_long_val = best_params.get('sma_long', 200)
        cond_grid.add_row(f"Prix > SMA{sma_long_val}", _ok(row.get('close', 0) > row.get('sma_long', float('nan'))), f"Prix={row.get('close', 0):.8g}  SMA={row.get('sma_long', 0):.8g}")
    if scenario == 'StochRSI_TRIX':
        cond_grid.add_row("TRIX_HISTO > 0", _ok(row.get('TRIX_HISTO', -999) > 0), f"TRIX={row.get('TRIX_HISTO', 0):.4f}")

    cond_grid.add_row("StochRSI actuel", "", f"[{_srsi_color}]{_srsi_display}[/{_srsi_color}]")

    if buy_reason:
        cond_grid.add_row("", "", "")
        cond_grid.add_row("", "", f"[dim italic]{buy_reason}[/dim italic]")

    # A-3 cooldown display
    import time as _time_mod
    _cd_until = pair_state.get('_stop_loss_cooldown_until', 0)
    if _cd_until > _time_mod.time():
        _cd_min = (_cd_until - _time_mod.time()) / 60
        cond_grid.add_row(
            "Cooldown A-3",
            "[bold red]\u2718 BLOQUE[/bold red]",
            f"[bold red]{_cd_min:.0f} min restantes[/bold red]",
        )

    _pair_label = f" [{coin_symbol}] " if coin_symbol and coin_symbol != 'COIN' else " "
    panel_title = (
        f"[bold green]SIGNAL D'ACHAT{_pair_label}- CONDITIONS REMPLIES[/bold green]"
        if buy_condition else
        f"[bold yellow]SIGNAL D'ACHAT{_pair_label}- CONDITIONS NON REMPLIES[/bold yellow]"
    )
    try:
        console.print(Panel(
            cond_grid,
            title=panel_title,
            border_style="green" if buy_condition else "yellow",
            padding=(1, 2),
            width=PANEL_WIDTH,
        ))
    except UnicodeEncodeError as e:
        logger.error(f"[ENCODING ERROR] display_buy_signal_panel: {e}")


def display_sell_signal_panel(
    row, coin_balance, pair_state, sell_triggered,
    console: Console, coin_symbol, sell_reason=None,
    best_params: Optional[Dict[str, Any]] = None,
):
    """
    Panneau d'analyse des signaux de vente avec stop-loss, trailing stop,
    PnL et partials.
    """
    # Coin symbol fallback
    try:
        if not coin_symbol:
            coin_symbol = pair_state.get('coin_symbol')
            if not coin_symbol:
                real_trading_pair = pair_state.get('real_trading_pair')
                if real_trading_pair:
                    coin_symbol, _ = extract_coin_from_pair(real_trading_pair)
                else:
                    coin_symbol = 'COIN'
    except Exception:
        coin_symbol = 'COIN'

    # ── Build sell conditions grid ──
    sell_grid = Table(
        title="[bold white]Analyse des conditions de vente[/bold white]",
        title_justify="left",
        box=None, show_header=False, pad_edge=False, show_edge=False, padding=(0, 1),
    )
    sell_grid.add_column("condition", width=28, no_wrap=True, style="bold white")
    sell_grid.add_column("result", width=12, no_wrap=True)
    sell_grid.add_column("detail", style="dim")

    # Stratégie active
    if best_params:
        _ema1_p = best_params.get('ema1_period', '?')
        _ema2_p = best_params.get('ema2_period', '?')
        _tf = best_params.get('timeframe', '?')
        _scenario = best_params.get('scenario', '?')
        sell_grid.add_row("Stratégie active", "", f"[bold cyan]{_scenario} EMA({_ema1_p}/{_ema2_p}) {_tf}[/bold cyan]")

    def _fmt_ema(v: float) -> str:
        return f"{v:.12f}" if v < 0.01 else f"{v:.8f}"
    sell_grid.add_row("EMA2 > EMA1", _ok(row['ema2'] > row['ema1']), f"EMA1={_fmt_ema(row['ema1'])}  EMA2={_fmt_ema(row['ema2'])}")
    _ps_sell_exit = pair_state.get('stoch_sell_exit') if pair_state else None
    _stoch_exit = _ps_sell_exit if _ps_sell_exit is not None else getattr(config, 'stoch_rsi_sell_exit', 0.4)
    sell_grid.add_row(f"StochRSI > {_stoch_exit * 100:.0f}%", _ok(row['stoch_rsi'] > _stoch_exit), f"{row['stoch_rsi']*100:.1f}%")
    sell_grid.add_row(f"Solde {coin_symbol} > 0", _ok(coin_balance > 0), "")

    # Sell reason
    if sell_reason is not None:
        reason_map = {
            'SIGNAL':        "[bold magenta]Signal de strategie[/bold magenta]",
            'STOP-LOSS':     "[bold red]Stop-Loss[/bold red]",
            'TRAILING-STOP': "[bold cyan]Trailing-Stop[/bold cyan]",
            'PARTIAL-1':     "[bold yellow]Partial 1 (50%)[/bold yellow]",
            'PARTIAL-2':     "[bold yellow]Partial 2 (30%)[/bold yellow]",
        }
        sell_grid.add_row("", "", "")
        sell_grid.add_row(
            "[bold white]Raison de la vente[/bold white]",
            reason_map.get(sell_reason, f"[bold yellow]{sell_reason}[/bold yellow]"),
            "",
        )

    # Current price
    stop_loss_at_entry = pair_state.get('stop_loss_at_entry')
    trailing_activation_price_at_entry = pair_state.get('trailing_activation_price_at_entry')
    trailing_stop_activated = pair_state.get('trailing_stop_activated', False)

    try:
        current_price = float(pair_state.get('ticker_spot_price'))
        if current_price is None or current_price <= 0:
            current_price = float(row.get('close'))
    except Exception:
        current_price = row.get('close')

    # === Partial thresholds ===
    entry_price = pair_state.get('entry_price')
    partial_taken_1 = pair_state.get('partial_taken_1', False)
    partial_taken_2 = pair_state.get('partial_taken_2', False)

    if entry_price and current_price:
        partial_1_threshold = entry_price * 1.02
        partial_2_threshold = entry_price * 1.04
        partial_1_reached = current_price >= partial_1_threshold
        partial_2_reached = current_price >= partial_2_threshold and partial_taken_1

        partial_1_status = (
            "[bold green]\u2714 OK[/bold green]"
            if partial_1_reached else
            "[bold red]\u2718 NOK[/bold red]"
        )
        partial_2_status = (
            "[bold green]\u2714 OK[/bold green]"
            if partial_2_reached else
            "[bold red]\u2718 NOK[/bold red]"
        )
        partial_1_taken = "[yellow]\u2713 DEJA PRISE[/yellow]" if partial_taken_1 else "[grey62]En attente[/grey62]"
        partial_2_taken = "[yellow]\u2713 DEJA PRISE[/yellow]" if partial_taken_2 else "[grey62]En attente[/grey62]"

        sell_grid.add_row("", "", "")
        sell_grid.add_row(f"PARTIAL-1 +2% (>={partial_1_threshold:.8g})", partial_1_status, f"{current_price:.8g} vs {partial_1_threshold:.8g}")
        sell_grid.add_row("  Statut PARTIAL-1", partial_1_taken, "")
        sell_grid.add_row(f"PARTIAL-2 +4% (>={partial_2_threshold:.8g})", partial_2_status, f"{current_price:.8g} vs {partial_2_threshold:.8g}")
        sell_grid.add_row("  Statut PARTIAL-2", partial_2_taken, "")

    # PnL
    pnl_value = None
    if entry_price is not None and current_price is not None and coin_balance is not None:
        try:
            pnl_value = (current_price - entry_price) * coin_balance
        except Exception as _e:
            logger.debug("[UI] Calcul PnL impossible: %s", _e)

    # Trailing activation display
    qc = pair_state.get('quote_currency', 'USDC')
    if trailing_activation_price_at_entry is not None:
        trailing_activation_val = f"{trailing_activation_price_at_entry:.8g} {qc}"
    else:
        trailing_activation_val = "N/A"
        logger.warning(f"[PANEL] Prix activation trailing non défini (trailing_activation_price_at_entry={trailing_activation_price_at_entry})")

    # Stop-loss display
    if not trailing_stop_activated:
        if stop_loss_at_entry is not None:
            stop_loss_display = f"{stop_loss_at_entry:.8g} {qc}"
            stop_loss_nature = "fixe a l'entree"
        else:
            stop_loss_display = "N/A"
            stop_loss_nature = ""
    else:
        max_price = pair_state.get('max_price')
        atr_multiplier = pair_state.get('atr_multiplier', config.atr_multiplier)
        atr_at_entry = pair_state.get('atr_at_entry')
        if max_price is not None and atr_at_entry is not None:
            trailing_stop_level = max_price - atr_multiplier * atr_at_entry
            stop_loss_display = f"{trailing_stop_level:.8g} {qc}"
            stop_loss_nature = "[cyan]dynamique (trailing)[/cyan]"
        else:
            stop_loss_display = "N/A"
            stop_loss_nature = ""

    # Trailing message
    trailing_active = False
    if trailing_activation_price_at_entry is not None and current_price is not None:
        if current_price >= trailing_activation_price_at_entry:
            trailing_active = True

    sell_grid.add_row("", "", "")
    sell_grid.add_row("Stop-Loss", "", f"{stop_loss_display}  {stop_loss_nature}")
    sell_grid.add_row("Activation trailing", "", trailing_activation_val)
    if pnl_value is not None:
        pnl_color = "bold bright_green" if pnl_value >= 0 else "bold red"
        sell_grid.add_row("PnL en cours", f"[{pnl_color}]{pnl_value:,.2f} {qc}[/{pnl_color}]", "")
    if trailing_active:
        sell_grid.add_row("", "", "")
        sell_grid.add_row("[bold cyan]Trailing stop[/bold cyan]", "[bold cyan]ACTIF[/bold cyan]", f"[dim]Prix: {current_price:.8g} {qc}[/dim]")
        sell_grid.add_row("", "", "[dim]SL dynamique a chaque planification[/dim]")

    _pair_label = f" [{coin_symbol}] " if coin_symbol and coin_symbol != 'COIN' else " "
    panel_title = (
        f"[bold red]SIGNAL DE VENTE{_pair_label}- CONDITIONS REMPLIES[/bold red]"
        if sell_triggered else
        f"[bold yellow]SIGNAL DE VENTE{_pair_label}- CONDITIONS NON REMPLIES[/bold yellow]"
    )
    try:
        console.print(Panel(
            sell_grid,
            title=panel_title,
            border_style="red" if sell_triggered else "yellow",
            padding=(1, 2),
            width=PANEL_WIDTH,
        ))
    except UnicodeEncodeError as e:
        logger.error(f"[ENCODING ERROR] display_sell_signal_panel: {e}")


# ─── Account Balances Panel ────────────────────────────────────────────────

_last_balance_panel_hash: Optional[int] = None


def display_account_balances_panel(
    account_info, coin_symbol, quote_currency, client,
    console: Console, pair_state: MutableMapping[str, Any],
    last_buy_price=None, atr_at_entry=None,
):
    """
    Panneau des soldes de trading (quote_currency, coin), prix de la paire
    et solde global converti dynamiquement.

    Side-effect: met à jour pair_state['quote_currency'] et pair_state['ticker_spot_price'].
    """
    global _last_balance_panel_hash

    quote_balance_obj = next((b for b in account_info['balances'] if b['asset'] == quote_currency), None)
    quote_balance = float(quote_balance_obj['free']) if quote_balance_obj else 0.0

    coin_balance_obj = next((b for b in account_info['balances'] if b['asset'] == coin_symbol), None)
    # Inclure les coins verrouillés (ordres STOP_LOSS_LIMIT en attente) dans l'affichage
    # sinon le panel montre 0.00057 SOL au lieu de 2.967 SOL quand un SL est actif.
    _coin_free = float(coin_balance_obj['free']) if coin_balance_obj else 0.0
    _coin_locked = float(coin_balance_obj.get('locked', '0') or '0') if coin_balance_obj else 0.0
    coin_balance = _coin_free + _coin_locked

    # Spot price
    try:
        pair_symbol = f"{coin_symbol}{quote_currency}"
        spot_price = float(client.get_symbol_ticker(symbol=pair_symbol)['price'])
    except Exception:
        spot_price = None

    # Side-effect: store in pair_state for other panels
    pair_state['quote_currency'] = quote_currency
    if 'ticker_spot_price' not in pair_state or pair_state['ticker_spot_price'] != spot_price:
        pair_state['ticker_spot_price'] = spot_price

    global_balance_usdc = get_spot_balance_usdc(client)

    balance_grid = Table(box=None, show_header=False, pad_edge=False, show_edge=False, padding=(0, 2))
    balance_grid.add_column("label", style="bold white", width=24, no_wrap=True)
    balance_grid.add_column("value")
    balance_grid.add_row("Crypto extraite", f"[bright_cyan]{coin_symbol}[/bright_cyan]")
    balance_grid.add_row("Monnaie de cotation", f"[bright_cyan]{quote_currency}[/bright_cyan]")
    balance_grid.add_row("", "")
    balance_grid.add_row(f"Solde {quote_currency} disponible", f"[bright_yellow]{quote_balance:.2f} {quote_currency}[/bright_yellow]")
    balance_grid.add_row(f"Solde {coin_symbol} disponible", f"[bright_yellow]{coin_balance:.8f} {coin_symbol}[/bright_yellow]")
    if last_buy_price is not None:
        balance_grid.add_row("Prix d'achat entree", f"[bright_magenta]{_fmt_price(last_buy_price)} {quote_currency}[/bright_magenta]")
    if atr_at_entry is not None:
        balance_grid.add_row("ATR utilise a l'achat", f"[bright_cyan]{atr_at_entry:.8g} {quote_currency}[/bright_cyan]")
    if spot_price is not None:
        balance_grid.add_row(f"Prix {coin_symbol}/{quote_currency} actuel", f"[bright_magenta]{_fmt_price(spot_price)} {quote_currency}[/bright_magenta]")
    balance_grid.add_row("", "")
    balance_grid.add_row(f"[bold]Solde global {_exchange_label(client)}[/bold]", f"[bold bright_green]{global_balance_usdc:.2f} USDC[/bold bright_green]")

    panel_hash = hash((coin_symbol, quote_currency, quote_balance, coin_balance, last_buy_price, atr_at_entry, spot_price, global_balance_usdc))
    if _last_balance_panel_hash != panel_hash:
        try:
            console.print(Panel(
                balance_grid,
                title="[bold green]SOLDES DE TRADING[/bold green]",
                border_style="green",
                padding=(1, 2),
                width=PANEL_WIDTH,
            ))
        except UnicodeEncodeError as e:
            logger.error(f"[ENCODING ERROR] display_account_balances_panel: {e}")
        _last_balance_panel_hash = panel_hash


# ─── Market Changes ────────────────────────────────────────────────────────

def display_market_changes(changes: Dict[str, Any], pair: str, console: Optional[Console] = None):
    """Affiche les changements détectés de façon élégante."""
    _console = console or globals()['console']
    has_changes = any([changes['ema_crosses'], changes['stoch_extremes'], changes['price_records']])

    if not has_changes:
        logger.info(f"[INFO] No significant market changes detected for {pair}")
        return

    _console.print(f"\n[bold yellow][MARKET] CHANGEMENTS DETECTES - {pair} @ {changes['execution_time']}[/bold yellow]")

    if changes['ema_crosses']:
        _console.print("\n[bold green][CROSS] EMA CROSSES:[/bold green]")
        for cross in changes['ema_crosses']:
            _console.print(f"  {cross['type']} on {cross['timeframe']} @ ${cross['price']:.2f}")

    if changes['stoch_extremes']:
        _console.print("\n[bold cyan] STOCH RSI EXTREMES:[/bold cyan]")
        for extreme in changes['stoch_extremes']:
            _console.print(f"  {extreme['type']} on {extreme['timeframe']} @ {extreme['value'] * 100:.1f}%")

    if changes['price_records']:
        _console.print("\n[bold magenta] NEW PRICE LEVELS:[/bold magenta]")
        for record in changes['price_records']:
            _console.print(f"  {record['type']} on {record['timeframe']} @ ${record['value']:.2f}")


# ─── Backtest Results ───────────────────────────────────────────────────────

MAX_TABLE_ROWS = 15  # Show top N results in backtest tables


def display_results_for_pair(backtest_pair: str, results: List[Dict], console: Optional[Console] = None, wf_config: Optional[Dict] = None, start_date_override: Optional[str] = None):
    """Affiche les résultats d'une paire de façon claire et organisée."""
    _console = console or globals()['console']

    if not results:
        _console.print(f"[red]Aucun résultat pour {backtest_pair}[/red]")
        return

    # Date range
    today = datetime.today()
    end_date_str = today.strftime("%d %B %Y")
    if start_date_override:
        start_date_str = start_date_override
    else:
        start_date_obj = today - timedelta(days=config.backtest_days)
        start_date_str = start_date_obj.strftime("%d %B %Y")

    # Sort results by profit descending
    sorted_results = sorted(results, key=lambda x: x['final_wallet'] - x['initial_wallet'], reverse=True)
    best_result = sorted_results[0]
    best_profit = best_result['final_wallet'] - best_result['initial_wallet']
    best_final_wallet_color = (
        "bold bright_green"
        if best_result['final_wallet'] >= best_result['initial_wallet']
        else "bold red"
    )

    # Elegant period header
    _console.print()
    _console.print(Rule(
        title=f"[bold bright_yellow]BACKTEST {backtest_pair}  \u2502  {start_date_str} \u2192 {end_date_str}[/bold bright_yellow]",
        style="bright_yellow",
    ))

    # Best result panel — displayed FIRST for immediate visibility
    champion_grid = Table(box=None, show_header=False, pad_edge=False, show_edge=False, padding=(0, 2))
    champion_grid.add_column("label", width=22, no_wrap=True)
    champion_grid.add_column("value")
    champion_grid.add_row("[bold green]\u2605 Scenario IS[/bold green]", f"[bold magenta]{best_result['scenario']}[/bold magenta]")
    champion_grid.add_row("[cyan]  Timeframe IS[/cyan]", best_result['timeframe'])
    champion_grid.add_row("[blue]  EMA IS[/blue]", f"{best_result['ema_periods'][0]} / {best_result['ema_periods'][1]}")
    champion_grid.add_row("[yellow]  Capital depart[/yellow]", f"[bold yellow]${best_result['initial_wallet']:,.2f}[/bold yellow]")
    champion_grid.add_row("[yellow]  Capital final[/yellow]", f"[{best_final_wallet_color}]${best_result['final_wallet']:,.2f}[/{best_final_wallet_color}]")
    champion_grid.add_row("[yellow]  Profit IS[/yellow]", f"[bold bright_green]${best_profit:,.2f}[/bold bright_green]")
    champion_grid.add_row("[red]  Max Drawdown[/red]", f"{best_result['max_drawdown']*100:.2f}%")
    champion_grid.add_row("[cyan]  Win Rate[/cyan]", f"{best_result['win_rate']:.2f}%")
    if wf_config:
        wf_ema = wf_config.get('ema_periods', ['-', '-'])
        champion_grid.add_row("[dim white]─────────────────────[/dim white]", "")
        champion_grid.add_row("[bold yellow]  \u27a4 Config active (WF/OOS)[/bold yellow]",
                              f"[bold yellow]{wf_config.get('scenario', '?')} {wf_config.get('timeframe', '?')} EMA({wf_ema[0]},{wf_ema[1]})[/bold yellow]")
        champion_grid.add_row("[cyan]  OOS Sharpe WF[/cyan]",
                              f"[bold cyan]{wf_config.get('avg_oos_sharpe', 0):.2f}[/bold cyan]")

    best_panel = Panel(
        champion_grid,
        title=(
            f"[bold bright_green]\u2605 DIAGNOSTIC BACKTEST IS \u2014 {backtest_pair} "
            "\u2605[/bold bright_green]"
        ),
        subtitle=(
            f"[dim]{len(results)} configurations testees | non tradable sans WF courant {_wf_fold_label()}"
            f"{' | config WF/OOS valide affichee ci-dessus' if wf_config else ''}[/dim]"
        ),
        border_style="bright_green",
        padding=(1, 3),
        width=PANEL_WIDTH,
    )
    _console.print(best_panel)

    # Table — sorted by profit, limited to top N
    display_results = sorted_results[:MAX_TABLE_ROWS]
    table = Table(
        title=f"[bold cyan]Top {len(display_results)} Resultats IS diagnostic — {backtest_pair}[/bold cyan]",
        title_style="bold magenta",
        show_header=True,
        header_style="bold white on dark_blue",
        border_style="bright_blue",
        row_styles=["", "dim"],
        expand=True,
        pad_edge=True,
        show_lines=False,
    )
    table.add_column("#", style="bold white", justify="center", ratio=2, no_wrap=True)
    table.add_column("TF", style="cyan", justify="center", ratio=3, no_wrap=True)
    table.add_column("EMA", style="cyan", justify="center", ratio=4, no_wrap=True)
    table.add_column("Scenario", style="magenta", justify="left", ratio=10, no_wrap=True)
    table.add_column("Profit ($)", justify="right", ratio=9, no_wrap=True)
    table.add_column("Trades", style="white", justify="center", ratio=4, no_wrap=True)
    table.add_column("DD %", justify="right", ratio=5, no_wrap=True)
    table.add_column("WR %", justify="right", ratio=5, no_wrap=True)

    for i, result in enumerate(display_results, 1):
        profit = result['final_wallet'] - result['initial_wallet']
        profit_color = "bold bright_green" if profit > 0 else "bold red"
        rank = "\u2605" if i == 1 else str(i)
        row_style = "on dark_green" if i == 1 else ""
        _is_degenerate = result.get('win_rate', 0.0) >= 100.0 and result.get('max_drawdown', 1.0) == 0.0
        table.add_row(
            f"[bold yellow]{rank}[/bold yellow]" if i == 1 else rank,
            result['timeframe'],
            f"{result['ema_periods'][0]}/{result['ema_periods'][1]}",
            f"[dim]{result['scenario']} [red][EXCLU][/red][/dim]" if _is_degenerate else result['scenario'],
            f"[{profit_color}]{profit:,.2f}[/{profit_color}]",
            f"[bold yellow]⚠[/bold yellow] {len(result['trades'])}" if len(result['trades']) < 10 else str(len(result['trades'])),
            f"[red]{result['max_drawdown']*100:.2f}%[/red]",
            (
                f"[bold red]{result['win_rate']:.2f}%[/bold red]"
                if _is_degenerate
                else (
                    f"[bold yellow]⚠[/bold yellow] [cyan]{result['win_rate']:.2f}%[/cyan]"
                    if result['win_rate'] > 95.0 and len(result['trades']) < 30
                    else f"[cyan]{result['win_rate']:.2f}%[/cyan]"
                )
            ),
            style=row_style,
        )

    _console.print(table)
    if len(results) > MAX_TABLE_ROWS:
        _console.print(f"[dim]   ... et {len(results) - MAX_TABLE_ROWS} autres configurations (triees par profit IS decroissant)[/dim]")
    _console.print(Rule(style="bright_yellow"))


def display_backtest_table(
    backtest_pair: str, results: List[Dict], console: Console,
):
    """
    Table de résultats backtest utilisée dans backtest_and_display_results.
    Variante étendue avec Initial Wallet. Triée par profit décroissant.
    """
    # Sort by profit descending
    sorted_results = sorted(results, key=lambda x: x['final_wallet'] - x['initial_wallet'], reverse=True)
    best_result = sorted_results[0]
    best_profit = best_result['final_wallet'] - best_result['initial_wallet']
    best_final_wallet_color = (
        "bold bright_green"
        if best_result['final_wallet'] >= best_result['initial_wallet']
        else "bold red"
    )

    # Champion panel
    champion_grid = Table(box=None, show_header=False, pad_edge=False, show_edge=False, padding=(0, 2))
    champion_grid.add_column("label", width=18, no_wrap=True)
    champion_grid.add_column("value")
    champion_grid.add_row("[bold green]\u2605 Scenario IS[/bold green]", f"[bold magenta]{best_result['scenario']}[/bold magenta]")
    champion_grid.add_row("[cyan]  Timeframe IS[/cyan]", f"[cyan bold]{best_result['timeframe']}[/cyan bold]")
    champion_grid.add_row("[blue]  EMA IS[/blue]", f"[cyan bold]{best_result['ema_periods'][0]} / {best_result['ema_periods'][1]}[/cyan bold]")
    champion_grid.add_row("[yellow]  Capital depart[/yellow]", f"[bold yellow]${best_result['initial_wallet']:,.2f}[/bold yellow]")
    champion_grid.add_row("[yellow]  Capital final[/yellow]", f"[{best_final_wallet_color}]${best_result['final_wallet']:,.2f}[/{best_final_wallet_color}]")
    champion_grid.add_row("[yellow]  Profit IS[/yellow]", f"[bold bright_green]${best_profit:,.2f}[/bold bright_green]")
    champion_grid.add_row("[red]  Max Drawdown[/red]", f"[bold red]{best_result['max_drawdown']*100:.2f}%[/bold red]")
    champion_grid.add_row("[cyan]  Win Rate[/cyan]", f"[bold cyan]{best_result['win_rate']:.2f}%[/bold cyan]")

    console.print(Panel(
        champion_grid,
        title=(
            f"[bold bright_green]\u2605 DIAGNOSTIC BACKTEST IS \u2014 {backtest_pair} "
            "\u2605[/bold bright_green]"
        ),
        subtitle=f"[dim]{len(results)} configurations testees | non tradable sans WF courant {_wf_fold_label()}[/dim]",
        border_style="bright_green",
        padding=(1, 3),
        expand=False,
    ))

    # Results table — top N only
    display_results = sorted_results[:MAX_TABLE_ROWS]
    table = Table(
        title=f"[bold cyan]Top {len(display_results)} Backtest IS diagnostic[/bold cyan]",
        title_style="bold magenta",
        show_header=True,
        header_style="bold white on dark_blue",
        border_style="bright_blue",
        row_styles=["", "dim"],
        width=PANEL_WIDTH,
        pad_edge=True,
    )
    table.add_column("#", style="bold white", justify="center", ratio=2, no_wrap=True)
    table.add_column("TF", style="cyan", justify="center", ratio=3, no_wrap=True)
    table.add_column("EMA", style="cyan", justify="center", ratio=4, no_wrap=True)
    table.add_column("Scenario", style="magenta", justify="left", ratio=8, no_wrap=True)
    table.add_column("Init ($)", style="white", justify="right", ratio=7, no_wrap=True)
    table.add_column("Final ($)", justify="right", ratio=7, no_wrap=True)
    table.add_column("Profit ($)", justify="right", ratio=8, no_wrap=True)
    table.add_column("Trades", style="white", justify="center", ratio=4, no_wrap=True)
    table.add_column("DD %", justify="right", ratio=5, no_wrap=True)
    table.add_column("WR %", justify="right", ratio=5, no_wrap=True)

    for i, result in enumerate(display_results, 1):
        profit = result['final_wallet'] - result['initial_wallet']
        profit_color = "bold bright_green" if profit > 0 else "bold red"
        final_wallet_color = "bold green" if result['final_wallet'] > result['initial_wallet'] else "bold red"
        rank = "\u2605" if i == 1 else str(i)
        row_style = "on dark_green" if i == 1 else ""

        table.add_row(
            f"[bold yellow]{rank}[/bold yellow]" if i == 1 else rank,
            result['timeframe'],
            f"{result['ema_periods'][0]}/{result['ema_periods'][1]}",
            result['scenario'],
            f"${result['initial_wallet']:,.2f}",
            f"[{final_wallet_color}]${result['final_wallet']:,.2f}[/{final_wallet_color}]",
            f"[{profit_color}]${profit:,.2f}[/{profit_color}]",
            f"[bold yellow]⚠[/bold yellow] {len(result['trades'])}" if len(result['trades']) < 10 else str(len(result['trades'])),
            f"[red]{result['max_drawdown']*100:.2f}%[/red]",
            f"[cyan]{result['win_rate']:.2f}%[/cyan]",
            style=row_style,
        )

    console.print(table)
    if len(results) > MAX_TABLE_ROWS:
        console.print(f"[dim]   ... et {len(results) - MAX_TABLE_ROWS} autres configurations omises[/dim]")


# ─── Trading Panel (deduplicated) ──────────────────────────────────────────

def display_trading_panel(real_trading_pair: str, best_params: Dict[str, Any], console: Console):
    """
    Panneau principal «TRADING ALGORITHMIQUE EN TEMPS RÉEL».
    Remplace les 2 constructions inline identiques (execute_scheduled_trading
    et backtest_and_display_results).
    """
    trading_grid = Table(
        title="[dim]Execution automatisee des ordres de trading[/dim]",
        title_justify="left",
        box=None, show_header=False, pad_edge=False, show_edge=False, padding=(0, 2),
    )
    trading_grid.add_column("label", style="bold white", width=24, no_wrap=True)
    trading_grid.add_column("value")
    trading_grid.add_row("Paire de trading", f"[bright_yellow]{real_trading_pair}[/bright_yellow]")
    trading_grid.add_row("Intervalle temporel", f"[bright_cyan]{best_params['timeframe']}[/bright_cyan]")
    trading_grid.add_row("Configuration EMA", f"[bright_green]{best_params['ema1_period']}[/bright_green] / [bright_green]{best_params['ema2_period']}[/bright_green]")
    trading_grid.add_row("Indicateur principal", f"[bright_magenta]{best_params['scenario']}[/bright_magenta]")

    console.print(Panel(
        trading_grid,
        title="[bold bright_cyan]TRADING ALGORITHMIQUE EN TEMPS REEL[/bold bright_cyan]",
        title_align="center",
        border_style="bright_cyan",
        padding=(1, 3),
        width=PANEL_WIDTH,
    ))


# ─── Tracking Panel (deduplicated) ─────────────────────────────────────────

def _tracking_pair_label(pair_state: Mapping[str, Any]) -> str:
    primary = (
        pair_state.get('pair_symbol')
        or pair_state.get('backtest_pair')
        or pair_state.get('pair_key')
        or pair_state.get('real_trading_pair')
        or pair_state.get('symbol')
    )
    broker_symbol = pair_state.get('broker_symbol')
    if primary and broker_symbol and str(primary) != str(broker_symbol):
        return f"{primary} -> {broker_symbol}"
    if primary:
        return str(primary)
    for key in (
        'broker_symbol',
    ):
        value = pair_state.get(key)
        if value:
            return str(value)
    active = pair_state.get('active_strategy')
    if isinstance(active, Mapping) and active.get('pair'):
        return str(active['pair'])
    entry = pair_state.get('entry_strategy')
    if isinstance(entry, Mapping) and entry.get('pair'):
        return str(entry['pair'])
    return "PAIRE NON IDENTIFIEE"


def build_tracking_panel(pair_state: Mapping[str, Any], current_run_time: str) -> Panel:
    """
    Construit le panel «SUIVI D'EXÉCUTION & PLANIFICATION AUTOMATIQUE».
    Retourne un Panel Rich prêt à être imprimé.
    Remplace 3 constructions inline identiques.
    """
    tracking_grid = Table(box=None, show_header=False, pad_edge=False, show_edge=False, padding=(0, 2))
    tracking_grid.add_column("label", style="bold white", width=42, no_wrap=True)
    tracking_grid.add_column("value")
    pair_label = _tracking_pair_label(pair_state)
    tracking_grid.add_row("Paire", f"[bold bright_yellow]{pair_label}[/bold bright_yellow]")
    tracking_grid.add_row("", "")

    if pair_state.get('last_run_time'):
        try:
            last_time_obj = datetime.strptime(pair_state['last_run_time'], "%Y-%m-%d %H:%M:%S")
            current_time_obj = datetime.strptime(current_run_time, "%Y-%m-%d %H:%M:%S")
            time_elapsed = current_time_obj - last_time_obj
            tracking_grid.add_row("Derniere execution", f"[bright_cyan]{pair_state['last_run_time']}[/bright_cyan]")
            tracking_grid.add_row("Temps ecoule", f"[bright_yellow]{time_elapsed}[/bright_yellow]")
        except Exception:
            tracking_grid.add_row("Derniere execution", f"[bright_cyan]{pair_state['last_run_time']}[/bright_cyan]")
    else:
        tracking_grid.add_row("[bold bright_green]Premiere execution[/bold bright_green]", "[bold bright_green]Systeme de trading automatise[/bold bright_green]")

    tracking_grid.add_row("", "")
    tracking_grid.add_row("Mode de planification", "[dim]Live: 2 min | Backtest+WF: 1 heure[/dim]")
    next_live = pair_state.get('display_next_live') or pair_state.get('next_live_run_at')
    next_wf = pair_state.get('display_next_wf') or pair_state.get('next_hourly_run_at')
    tracking_grid.add_row(
        "Prochaine surveillance",
        f"[bright_green]{next_live}[/bright_green]" if next_live else "[dim]Initialisation scheduler[/dim]",
    )
    tracking_grid.add_row(
        "Prochain Backtest+WF",
        f"[bright_yellow]{next_wf}[/bright_yellow]" if next_wf else "[dim]Initialisation scheduler[/dim]",
    )

    # I-3: alerte visuelle si aucune config OOS validée au démarrage
    wf_status = str(pair_state.get('wf_status') or 'pending_revalidation')
    entries_ready = bool(pair_state.get('entries_ready'))
    wf_reason = str(pair_state.get('wf_block_reason') or '')
    backtest_status = str(pair_state.get('backtest_display_status') or 'en attente')
    wf_session_status = str(pair_state.get('wf_session_status') or ('validated' if entries_ready else 'not_validated'))
    live_execution_mode = str(pair_state.get('live_execution_mode') or ('TRADABLE' if entries_ready else 'PROTECTION_ONLY'))
    tracking_grid.add_row("", "")
    tracking_grid.add_row("Backtest IS", f"[bright_cyan]{backtest_status}[/bright_cyan]")
    tracking_grid.add_row(
        "WF session",
        f"[bold green]valide {_wf_fold_label()}[/bold green]" if wf_session_status == 'validated' else "[bold yellow]non valide[/bold yellow]",
    )
    tracking_grid.add_row(
        "Trading Live",
        "[bold green]TRADABLE[/bold green]" if live_execution_mode == 'TRADABLE' else "[bold yellow]PROTECTION_ONLY[/bold yellow]",
    )
    if pair_state.get('wf_fallback') or not entries_ready:
        tracking_grid.add_row("", "")
        if wf_status == 'protection_only':
            wf_mode = "[bold yellow]Protection seule - revalidation WF requise[/bold yellow]"
        else:
            wf_mode = "[bold yellow]WF FALLBACK - aucune config OOS valide[/bold yellow]"
        tracking_grid.add_row("[bold yellow]\u26a0 Mode WF[/bold yellow]", wf_mode)
        tracking_grid.add_row("Mode position", "[yellow]Protection seule[/yellow]")
        tracking_grid.add_row("Tradable", "[bold red]NON[/bold red]")
        tracking_grid.add_row("Statut WF", f"[yellow]{wf_status}[/yellow]")
        tracking_grid.add_row("Validation session", "[bold red]NON[/bold red]")
        if wf_reason:
            tracking_grid.add_row("Raison", f"[yellow]{wf_reason}[/yellow]")
    else:
        tracking_grid.add_row("Tradable", "[bold green]OUI[/bold green]")
        tracking_grid.add_row("Validation session", "[bold green]OUI[/bold green]")

    active = pair_state.get('active_strategy')
    history_status = pair_state.get('history_status')
    preferred_tf = None
    if isinstance(active, Mapping):
        preferred_tf = str(active.get('timeframe') or '') or None
        _validated_at = str(active.get('validated_at', ''))
        _age_text = 'age N/A'
        try:
            _validated_dt = datetime.fromisoformat(_validated_at.replace('Z', '+00:00'))
            _age = datetime.now(_validated_dt.tzinfo) - _validated_dt
            _age_text = f"age {_age.total_seconds() / 3600:.1f}h"
        except (TypeError, ValueError):
            pass
        snapshot_label = (
            "Snapshot actif tradable"
            if entries_ready and wf_status == 'validated'
            else "Dernier snapshot en protection seule"
        )
        tracking_grid.add_row("", "")
        tracking_grid.add_row(snapshot_label, f"[bright_cyan]{active.get('snapshot_id', 'N/A')}[/bright_cyan]")
        tracking_grid.add_row(
            "Seuils StochRSI",
            f"{float(active.get('stoch_buy_min', 0))*100:.0f}% / "
            f"{float(active.get('stoch_buy_max', 0))*100:.0f}% / "
            f"{float(active.get('stoch_sell_exit', 0))*100:.0f}%",
        )
        tracking_grid.add_row(
            "Validation historique",
            _wf_history_label(active, _age_text),
        )

    if isinstance(history_status, Mapping) and history_status:
        selected_history = None
        if preferred_tf and isinstance(history_status.get(preferred_tf), Mapping):
            selected_history = history_status.get(preferred_tf)
        if selected_history is None:
            selected_history = next((v for v in history_status.values() if isinstance(v, Mapping)), None)
        if isinstance(selected_history, Mapping):
            oldest = str(selected_history.get('oldest_available') or 'N/A')[:10]
            newest = str(selected_history.get('newest_available') or 'N/A')[:10]
            bars = selected_history.get('bars_available', '?')
            required = selected_history.get('bars_required', '?')
            source = selected_history.get('source', '?')
            tracking_grid.add_row("Plage donnees", f"[bright_cyan]{oldest} -> {newest}[/bright_cyan]")
            tracking_grid.add_row("Bougies WF", f"[yellow]{bars}/{required} ({source})[/yellow]")

    fee_taker = pair_state.get('effective_backtest_fee_taker')
    live_taker = pair_state.get('effective_live_fee_taker')
    if live_taker is None:
        live_taker = fee_taker
    if fee_taker is not None and live_taker is not None:
        try:
            maker = pair_state.get('effective_backtest_fee_maker')
            fee_taker_pct = float(fee_taker) * 100.0
            live_taker_pct = float(live_taker) * 100.0
            fee_source = str(pair_state.get('kraken_fee_source') or '').upper()
            tracking_grid.add_row(
                "Frais backtest/live",
                f"{fee_taker_pct:.4f}% / {live_taker_pct:.4f}%"
                + (f" maker {float(maker)*100:.4f}%" if maker is not None else ""),
            )
            if fee_source:
                tracking_grid.add_row("Source frais", fee_source)
        except (TypeError, ValueError):
            pass

    return Panel(
        tracking_grid,
        title=(
            "[bold bright_blue]SUIVI D'EXECUTION & PLANIFICATION AUTOMATIQUE"
            f" — {pair_label}[/bold bright_blue]"
        ),
        title_align="center",
        border_style="bright_blue",
        padding=(1, 3),
        width=PANEL_WIDTH,
    )


# ─── Closure Panel ─────────────────────────────────────────────────────────

def display_closure_panel(
    stop_loss_info: str, current_price: float,
    coin_symbol: str, coin_balance: float, console: Console,
    fill_price: Optional[float] = None,
):
    """Panneau de fermeture de position — STOP-LOSS touché."""
    closure_grid = Table(box=None, show_header=False, pad_edge=False, show_edge=False, padding=(0, 2))
    closure_grid.add_column("label", style="bold white", width=24, no_wrap=True)
    closure_grid.add_column("value")
    closure_grid.add_row("Stop-Loss utilise", stop_loss_info)
    if fill_price is not None:
        closure_grid.add_row("Prix fill exchange", f"{fill_price:.8g} USDC")
    closure_grid.add_row("Prix actuel", f"{current_price:.8g} USDC")
    closure_grid.add_row(f"Solde {coin_symbol}", f"{coin_balance:.8f}")
    closure_grid.add_row("Raison fermeture", "[bold red]STOP-LOSS touche[/bold red]")

    console.print(Panel(
        closure_grid,
        title="[bold red]FERMETURE POSITION - STOP TOUCHE[/bold red]",
        border_style="red",
        padding=(1, 2),
        width=PANEL_WIDTH,
    ))


# ─── Execution Header ──────────────────────────────────────────────────────

def display_execution_header(backtest_pair: str, real_trading_pair: str, time_interval: str, console: Console):
    """Bannière d'en-tête pour les exécutions planifiées."""
    console.print()
    console.print(Rule(
        title=f"[bold bright_cyan]EXECUTION PLANIFIEE  \u2502  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/bold bright_cyan]",
        style="bright_cyan",
    ))
    console.print(
        f"  [bold white]Paire[/bold white] [bright_yellow]{backtest_pair}[/bright_yellow] "
        f"[dim]\u2192[/dim] [bright_yellow]{real_trading_pair}[/bright_yellow]  "
        f"[dim]\u2502[/dim]  [bold white]Timeframe[/bold white] [bright_cyan]{time_interval}[/bright_cyan]"
    )
    console.print(Rule(style="dim"))
    console.print()


# ─── Bot Active Banner ─────────────────────────────────────────────────────

def display_bot_active_banner(
    num_jobs: int,
    next_live_run,
    console: Console,
    next_hourly_run=None,
    schedule_status: Optional[Mapping[str, Any]] = None,
):
    """Bannière affichée après planification, avant la boucle principale."""
    if schedule_status:
        next_live_str = str(schedule_status.get('display_next_live') or 'Initialisation scheduler')
        next_hourly_str = str(schedule_status.get('display_next_wf') or 'Initialisation scheduler')
        system_status = str(schedule_status.get('system_status') or '')
        system_status_detail = str(schedule_status.get('system_status_detail') or '')
    else:
        next_live_str = next_live_run.strftime('%H:%M:%S') if next_live_run else 'N/A'
        next_hourly_str = next_hourly_run.strftime('%H:%M:%S') if next_hourly_run else 'N/A'
        system_status = ''
        system_status_detail = ''
    bot_grid = Table(box=None, show_header=False, pad_edge=False, show_edge=False, padding=(0, 2))
    bot_grid.add_column("label", style="bold white", width=24, no_wrap=True)
    bot_grid.add_column("value")
    bot_grid.add_row("[bold bright_green]\u2714 Surveillance 24/7[/bold bright_green]", f"[bold bright_green]demarree a {datetime.now().strftime('%H:%M:%S')}[/bold bright_green]")
    bot_grid.add_row("", "")
    bot_grid.add_row("Taches planifiees", f"[bright_cyan]{num_jobs}[/bright_cyan]")
    bot_grid.add_row("Prochaine surveillance", f"[bright_yellow]{next_live_str}[/bright_yellow]")
    bot_grid.add_row("Prochain Backtest+WF", f"[bright_yellow]{next_hourly_str}[/bright_yellow]")
    if system_status:
        if "EMERGENCY_HALT" in system_status or "KO" in system_status or "DEGRADE" in system_status:
            style = "bold red"
        elif (
            "NO-BUY" in system_status
            or "PROTECTION_ONLY" in system_status
            or "PUBLIC DATA" in system_status
            or "PARTIAL_TRADABLE" in system_status
        ):
            style = "bold yellow"
        else:
            style = "bold green"
        detail = f" — {system_status_detail}" if system_status_detail else ""
        bot_grid.add_row("Etat runtime", f"[{style}]{system_status}{detail}[/{style}]")
    bot_grid.add_row("Frequence", "[dim]Live: 2 min (720/jour) | Backtest+WF: 1 heure[/dim]")

    console.print()
    console.print(Panel(
        bot_grid,
        title="[bold bright_green]BOT ACTIF[/bold bright_green]",
        title_align="center",
        border_style="bright_green",
        padding=(1, 3),
        width=PANEL_WIDTH,
    ))
    console.print()
