import sys
import os
# Ajout du dossier bin/ au sys.path pour les modules Cython
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'bin')))

# Import stubs for undefined functions/variables (for Pylance)
from MULTI_SYMBOLS_NOSIGNALCLOSE_stubs import (
    sync_windows_silently,
    check_network_connectivity,
    _generate_client_order_id,
    safe_market_sell,
    compute_position_size_by_risk,
    compute_position_size_fixed_notional,
    compute_position_size_volatility_parity,
    real_trading_pair,
    init_timestamp_solution,
    cleanup_expired_cache,
    all_results,
    execute_real_trades,
    execute_scheduled_trading
)

RUN_LIVE_TRADING = False  # True pour activer le live trading, False pour n'exécuter que le backtest
class DummyErrorHandler:
    def __init__(self, email_config=None):
        self.email_config = email_config or {}
        class DummyMode:
            def __init__(self):
                self.value = "default"
        class DummyCircuitBreaker:
            def __init__(self):
                self.mode = DummyMode()
            def is_available(self):
                return True
        self.circuit_breaker = DummyCircuitBreaker()
        self.mode = 'default'         # Attribut fictif pour compatibilité
    def handle_error(self, *args, **kwargs):
        # Dummy error handler: log or ignore
        print("[DummyErrorHandler] handle_error called.")

def initialize_error_handler(email_config=None):
    """Initialise le handler d'erreur avec configuration email."""
    # Retourne un objet avec circuit_breaker et email_config
    return DummyErrorHandler(email_config)
import locale
try:
    # Forcer la console Windows en UTF-8 (code page 65001)
    if os.name == "nt":
        os.system("chcp 65001 >NUL")
        locale.setlocale(locale.LC_ALL, '')
except Exception as e:
    pass

"""
MULTI_SYMBOLS.py

Table of Contents:
1. Imports
2. Global Variables & Constants
3. Utility Decorators
4. Configuration Classes
5. Exchange Client Classes
6. Core Helpers (caching, error handling, etc.)
7. Indicator Calculation
8. Display Functions
9. Trading Logic (order placement, state management)
10. Backtest & Execution Logic
11. Main Entrypoint
"""


"""Standard, external, and project-specific imports (alphabetized, deduplicated)."""
import argparse
import ctypes
import hashlib
import hmac
import json
import logging
import numpy as np
import os
import pandas as pd
import pickle
import random
import requests
import schedule
import smtplib
import socket
import subprocess
import sys
import ta
import time
import traceback
import uuid
from binance.client import Client

# Définition centralisée de la configuration du bot
class Config:
    """Configuration centralisee du bot de trading."""
    # Champs obligatoires (sans valeur par defaut)
    api_key: str
    secret_key: str
    sender_email: str
    receiver_email: str
    smtp_password: str

    # Champs optionnels (avec valeur par defaut)
    api_timeout: int = 30
    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 587
    taker_fee: float = 0.0007
    initial_wallet: float = 10000.0
    backtest_days: int = 1825  # 5 ans
    max_workers: int = 4
    cache_dir: str = "cache"
    states_dir: str = "states"
    state_file: str = "bot_state.pkl"
    atr_period: int = 14
    atr_multiplier: float = 5.0
    atr_stop_multiplier: float = 3.0
    risk_per_trade: float = 0.01

    @classmethod
    def from_env(cls) -> 'Config':
        """Charge la configuration depuis les variables d'environnement."""
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass
        required_vars = {
            'api_key': 'BINANCE_API_KEY',
            'secret_key': 'BINANCE_SECRET_KEY',
            'sender_email': 'SENDER_EMAIL',
            'receiver_email': 'RECEIVER_EMAIL',
            'smtp_password': 'GOOGLE_MAIL_PASSWORD'
        }
        config_data = {}
        for key, env_var in required_vars.items():
            value = os.getenv(env_var)
            if not value:
                raise ValueError(f"Variable d'environnement manquante: {env_var}")
            config_data[key] = value
        # Optionnels
        config_data['taker_fee'] = float(os.getenv('TAKER_FEE', '0.0007'))
        config_data['api_timeout'] = int(os.getenv('API_TIMEOUT', '30'))
        config_data['max_workers'] = int(os.getenv('MAX_WORKERS', '4'))
        config_data['initial_wallet'] = float(os.getenv('INITIAL_WALLET', '10000.0'))
        config_data['backtest_days'] = int(os.getenv('BACKTEST_DAYS', '1825'))
        config_data['cache_dir'] = os.getenv('CACHE_DIR', 'cache')
        config_data['states_dir'] = os.getenv('STATES_DIR', 'states')
        config_data['state_file'] = os.getenv('STATE_FILE', 'bot_state.pkl')
        config_data['atr_period'] = int(os.getenv('ATR_PERIOD', '14'))
        config_data['atr_multiplier'] = float(os.getenv('ATR_MULTIPLIER', '5.0'))
        config_data['atr_stop_multiplier'] = float(os.getenv('ATR_STOP_MULTIPLIER', '3.0'))
        config_data['risk_per_trade'] = float(os.getenv('RISK_PER_TRADE', '0.01'))
        config_data['smtp_server'] = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
        config_data['smtp_port'] = int(os.getenv('SMTP_PORT', '587'))
        # Création de l'instance et assignation des attributs
        self = cls()
        for k, v in config_data.items():
            setattr(self, k, v)
        return self
    def __init__(self):
        pass

# Chargement de la configuration
config = Config.from_env()
import sys
from binance.exceptions import BinanceAPIException
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
## Toutes les fonctions utilitaires, la config, le logger, etc. sont maintenant définis directement dans ce fichier.
## Supprimer toute dépendance à core.py
from functools import lru_cache
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from rich import print
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from tqdm import tqdm
from typing import Any, Dict, List, Optional, Tuple


sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))



from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def display_buy_signal_panel(row, usdc_balance, best_params, scenario, buy_condition, console, buy_reason=None):
    """
    Affiche le panneau d'analyse des signaux d'achat avec détails des conditions et du solde USDC.
    Args:
        row: Données de la bougie analysée.
        usdc_balance: Solde USDC disponible.
        best_params: Paramètres optimaux de la stratégie.
        scenario: Scénario de stratégie utilisé.
        buy_condition: Booléen, conditions d'achat remplies ou non.
        console: Objet Rich Console pour affichage.
    """
    buy_lines = [
        f"[bold white]Analyse des conditions d'achat :[/bold white]\n\n",
        f"[bold white]EMA1 > EMA2          :[/bold white] {'[green]OK[/green]' if row['ema1'] > row['ema2'] else '[red]NOK[/red]'} [dim](EMA1={row['ema1']:.2f}, EMA2={row['ema2']:.2f})[/dim]",
        f"[bold white]StochRSI < 80%       :[/bold white] {'[green]OK[/green]' if row['stoch_rsi'] < 0.8 else '[red]NOK[/red]'} [dim]({row['stoch_rsi']*100:.1f}%)[/dim]",
        f"[bold white]Solde USDC > 0       :[/bold white] {'[green]OK[/green]' if usdc_balance > 0 else '[red]NOK[/red]'} [dim]({usdc_balance:.2f} USDC)[/dim]"
    ]
    if scenario == 'StochRSI_ADX':
        adx_threshold = best_params.get('adx_threshold', 20)
        adx_val = row.get('adx', None)
        adx_status = '[green]OK[/green]' if (adx_val is not None and adx_val > adx_threshold) else '[red]NOK[/red]'
        buy_lines.append(f"[bold white]ADX > {adx_threshold}             :[/bold white] {adx_status} [dim](ADX={adx_val if adx_val else 'N/A'})[/dim]")
    if scenario == 'StochRSI_SMA':
        sma_long_val = best_params.get('sma_long', 200)
        sma_status = '[green]OK[/green]' if row.get('close', 0) > row.get('sma_long', float('nan')) else '[red]NOK[/red]'
        buy_lines.append(f"[bold white]Prix > SMA{sma_long_val}        :[/bold white] {sma_status} [dim](Prix={row.get('close', 0):.2f}, SMA={row.get('sma_long', 0):.2f})[/dim]")
    if scenario == 'StochRSI_TRIX':
        trix_status = '[green]OK[/green]' if row.get('TRIX_HISTO', -999) > 0 else '[red]NOK[/red]'
        buy_lines.append(f"[bold white]TRIX_HISTO > 0       :[/bold white] {trix_status} [dim](TRIX={row.get('TRIX_HISTO', 0):.4f})[/dim]")
    if buy_reason:
        buy_lines.append(f"\n[dim][italic]{buy_reason}[/italic][/dim]")
    buy_content = "\n".join(buy_lines)
    panel_title = "[bold green]SIGNAL D'ACHAT - CONDITIONS REMPLIES[/bold green]" if buy_condition else "[bold yellow]SIGNAL D'ACHAT - CONDITIONS NON REMPLIES[/bold yellow]"
    panel_color = "green" if buy_condition else "yellow"
    try:
        console.print(Panel(
            buy_content,
            title=panel_title,
            border_style=panel_color,
            padding=(1, 2),
            width=120
        ))
    except UnicodeEncodeError as e:
        logger.error(f"[ENCODING ERROR] display_buy_signal_panel: {e}")

_last_sell_panel_hash = None
def display_sell_signal_panel(row, coin_balance, pair_state, sell_triggered, console, coin_symbol, sell_reason=None, trailing_activation_price=None):
    """
    Affiche le panneau d'analyse des signaux de vente avec détails des conditions, stop-loss et trailing stop.
    Args:
        row: Données de la bougie analysée.
        coin_balance: Solde de la crypto à vendre.
        best_params: Paramètres optimaux de la stratégie.
        pair_state: Dictionnaire d'état de la paire.
        current_price: Prix actuel du marché.
        sell_triggered: Booléen, conditions de vente remplies ou non.
        console: Objet Rich Console pour affichage.
        coin_symbol: Symbole de la crypto.
    """
    sell_lines = [
        f"[bold white]Analyse des conditions de vente :[/bold white]\n\n",
        f"[bold white]EMA2 > EMA1          :[/bold white] {'[green]OK[/green]' if row['ema2'] > row['ema1'] else '[red]NOK[/red]'}",
        f"[bold white]StochRSI > 20%       :[/bold white] {'[green]OK[/green]' if row['stoch_rsi'] > 0.2 else '[red]NOK[/red]'}",
        f"[bold white]Solde {coin_symbol} > 0        :[/bold white] {'[green]OK[/green]' if coin_balance > 0 else '[red]NOK[/red]'}"
    ]
    # Affichage explicite de la raison de vente si elle est fournie
    if sell_reason is not None:
        reason_map = {
            'SIGNAL': "[bold magenta]Raison de la vente : Signal de stratégie[/bold magenta]",
            'STOP-LOSS': "[bold red]Raison de la vente : Stop-Loss[/bold red]",
            'TRAILING-STOP': "[bold cyan]Raison de la vente : Trailing-Stop[/bold cyan]"
        }
        sell_lines.append("")
        sell_lines.append(reason_map.get(sell_reason, f"[bold yellow]Raison de la vente : {sell_reason}[/bold yellow]"))
    # Affichage STRICTEMENT des valeurs fixées à l'entrée (jamais recalculées)
    stop_loss_at_entry = pair_state.get('stop_loss_at_entry', None)
    trailing_activation_price_at_entry = pair_state.get('trailing_activation_price_at_entry', None)
    trailing_stop_activated = pair_state.get('trailing_stop_activated', False)
    # Harmonize current price display using ticker spot price
    # Try to get spot price from pair_state if available, else fallback to row['close']
    current_price = pair_state.get('ticker_spot_price', None)
    if current_price is None:
        current_price = row.get('close', None)

    # Ajout affichage PnL en cours
    entry_price = pair_state.get('entry_price', None)
    pnl_value = None
    if entry_price is not None and current_price is not None and coin_balance is not None:
        try:
            pnl_value = (current_price - entry_price) * coin_balance
        except Exception:
            pnl_value = None
    # Prix d'activation du trailing stop : TOUJOURS la valeur stockée à l'entrée
    if trailing_activation_price_at_entry is not None:
        trailing_activation_val = f"{trailing_activation_price_at_entry:.4f} USDC"
    else:
        trailing_activation_val = "N/A"
        logger.warning(f"[PANEL] Prix activation trailing non défini (trailing_activation_price_at_entry={trailing_activation_price_at_entry})")
    # Stop loss : TOUJOURS la valeur stockée à l'entrée tant que le trailing n'est pas activé
    if not trailing_stop_activated:
        if stop_loss_at_entry is not None:
            stop_loss_display = f"{stop_loss_at_entry:.4f} USDC (fixe à l'entrée)"
            stop_loss_nature = "[grey62](Stop-loss fixe à l'ouverture)[/grey62]"
        else:
            stop_loss_display = "N/A"
            stop_loss_nature = ""
    else:
        # Après activation du trailing, afficher la valeur dynamique
        max_price = pair_state.get('max_price', None)
        atr_multiplier = pair_state.get('atr_multiplier', 5.0)
        atr_at_entry = pair_state.get('atr_at_entry', None)
        if max_price is not None and atr_at_entry is not None:
            trailing_stop_level = max_price - atr_multiplier * atr_at_entry
            stop_loss_display = f"{trailing_stop_level:.4f} USDC (dynamique)"
            stop_loss_nature = "[cyan](Stop-loss dynamique : trailing)[/cyan]"
        else:
            stop_loss_display = "N/A"
            stop_loss_nature = ""
    # Message complémentaire si le prix d'activation du trailing stop est atteint ou dépassé
    trailing_message = ""
    if trailing_activation_price_at_entry is not None and current_price is not None:
        if current_price >= trailing_activation_price_at_entry:
            trailing_message = (
                f"[bold cyan]Trailing stop activé ! (Prix actuel : {current_price:.4f} USDC)[/bold cyan]\n"
                "[bold cyan]Le stop loss est maintenant dynamique : il est mis à jour à chaque planification à 5×ATR sous le cours de l'actif.[/bold cyan]"
            )
    # Affichage
    sell_lines.append(f"[bold white]Stop-Loss affiché       :[/bold white] {stop_loss_display}")
    if stop_loss_nature:
        sell_lines.append(stop_loss_nature)
    sell_lines.append(f"[bold white]Prix activation trailing: [/bold white] {trailing_activation_val}")
    if pnl_value is not None:
        sell_lines.append(f"[bold white]PnL en cours            :[/bold white] [bold]{pnl_value:,.2f} USDC[/bold]")
    if trailing_message:
        sell_lines.append(trailing_message)
    sell_content = "\n".join(sell_lines)
    panel_title = "[bold red]SIGNAL DE VENTE - CONDITIONS REMPLIES[/bold red]" if sell_triggered else "[bold yellow]SIGNAL DE VENTE - CONDITIONS NON REMPLIES[/bold yellow]"
    panel_color = "red" if sell_triggered else "yellow"
    try:
        console.print(Panel(
            sell_content,
            title=panel_title,
            border_style=panel_color,
            padding=(1, 2),
            width=120
        ))
    except UnicodeEncodeError as e:
        logger.error(f"[ENCODING ERROR] display_sell_signal_panel: {e}")

_last_balance_panel_hash = None
def display_account_balances_panel(account_info, coin_symbol, quote_currency, client, console, last_buy_price=None, atr_at_entry=None):
    """
    Affiche le panel des soldes de trading (USDC, coin), le prix BTC/USDC et le solde global converti en USDC.
    Args:
        account_info: Dictionnaire d'informations de compte Binance.
        coin_symbol: Symbole de la crypto principale.
        quote_currency: Symbole de la devise de cotation.
        client: Client Binance API.
        console: Objet Rich Console pour affichage.
    """
    usdc_balance_obj = next((b for b in account_info['balances'] if b['asset'] == 'USDC'), None)
    usdc_balance = float(usdc_balance_obj['free']) if usdc_balance_obj else 0.0

    coin_balance_obj = next((b for b in account_info['balances'] if b['asset'] == coin_symbol), None)
    coin_balance = float(coin_balance_obj['free']) if coin_balance_obj else 0.0

    # Harmonize price retrieval for all panels
    try:
        btc_usdc_price = float(client.get_symbol_ticker(symbol="BTCUSDC")['price'])
    except Exception:
        btc_usdc_price = None
    # Store spot price in pair_state for use in other panels
    global pair_state
    if 'ticker_spot_price' not in pair_state or pair_state['ticker_spot_price'] != btc_usdc_price:
        pair_state['ticker_spot_price'] = btc_usdc_price

    tickers = get_all_tickers_cached(client)
    def convert_to_usdc(asset, amount):
        if amount < 1e-8 or asset == '':
            return 0.0
        if asset == 'USDC' or asset == 'BUSD':
            return amount
        symbol1 = asset + 'USDC'
        symbol2 = 'USDC' + asset
        if symbol1 in tickers and tickers[symbol1] > 0:
            return amount * tickers[symbol1]
        elif symbol2 in tickers and tickers[symbol2] > 0:
            return amount * (1.0 / tickers[symbol2])
        elif asset != 'BTC' and 'BTCUSDC' in tickers:
            via_btc = None
            symbol_btc = asset + 'BTC'
            if symbol_btc in tickers and tickers[symbol_btc] > 0:
                via_btc = tickers[symbol_btc] * tickers['BTCUSDC']
            elif ('BTC' + asset) in tickers and tickers['BTC' + asset] > 0:
                via_btc = (1.0 / tickers['BTC' + asset]) * tickers['BTCUSDC']
            if via_btc:
                return amount * via_btc
        return 0.0

    global_balance_usdc = 0.0
    for bal in account_info['balances']:
        asset = bal['asset']
        free = float(bal['free'])
        locked = float(bal['locked'])
        total = free + locked
        global_balance_usdc += convert_to_usdc(asset, total)

    balance_content = (
        f"[bold white]Crypto extraite       :[/bold white] [bright_cyan]{coin_symbol}[/bright_cyan]\n"
        f"[bold white]Monnaie de cotation   :[/bold white] [bright_cyan]{quote_currency}[/bright_cyan]\n\n"
        f"[bold white]Solde USDC disponible :[/bold white] [bright_yellow]{usdc_balance:.2f} USDC[/bright_yellow]\n"
        f"[bold white]Solde {coin_symbol} disponible   :[/bold white] [bright_yellow]{coin_balance:.8f} {coin_symbol}[/bright_yellow]"
    )
    if last_buy_price is not None:
        balance_content += f"\n[bold white]Prix d'achat à l'entrée   :[/bold white] [bright_magenta]{last_buy_price:.2f} USDC[/bright_magenta]"
    if atr_at_entry is not None:
        balance_content += f"\n[bold white]ATR utilisé à l'achat    :[/bold white] [bright_cyan]{atr_at_entry:.4f} USDC[/bright_cyan]"
    if btc_usdc_price is not None:
        balance_content += f"\n[bold white]Prix BTC/USDC actuel     :[/bold white] [bright_magenta]{btc_usdc_price:.2f} USDC[/bright_magenta]"
    balance_content += f"\n[bold white]Solde global Binance     :[/bold white] [bold bright_green]{global_balance_usdc:.2f} USDC[/bold bright_green]"
    global _last_balance_panel_hash
    panel_hash = hash(balance_content)
    if _last_balance_panel_hash != panel_hash:
        try:
            console.print(Panel(
                balance_content,
                title="[bold green]SOLDES DE TRADING[/bold green]",
                border_style="green",
                padding=(1, 2),
                width=120
            ))
        except UnicodeEncodeError as e:
            logger.error(f"[ENCODING ERROR] display_account_balances_panel: {e}")
        _last_balance_panel_hash = panel_hash

def log_exceptions(default_return=None):
    """Décorateur pour logguer les exceptions et retourner une valeur par défaut."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.error(f"[EXCEPTION] {func.__name__}: {e}")
                # Envoi d'une alerte mail pour toute exception
                try:
                    send_trading_alert_email(
                        subject=f"[BOT CRYPTO] EXCEPTION: {func.__name__}",
                        body_main=f"Exception dans {func.__name__}: {e}\n\nArgs: {args}\nKwargs: {kwargs}",
                        client=client
                    )
                except Exception as mail_exc:
                    logger.error(f"[EXCEPTION] Echec envoi mail d'alerte: {mail_exc}")
                return default_return if default_return is not None else None
        return wrapper
    return decorator

@log_exceptions(default_return=False)
def send_trading_alert_email(subject: str, body_main: str, client, add_spot_balance: bool = True) -> bool:
    """
    Envoie un email d'alerte de trading avec possibilité d'injecter le solde SPOT USDC.
    Args:
        subject: Sujet de l'email.
        body_main: Corps principal du message.
        client: Client Binance API.
        add_spot_balance: Ajoute le solde SPOT USDC si True.
    Returns:
        Booléen succès/échec.
    """
    body = body_main
    if add_spot_balance:
        try:
            spot_balance_usdc = get_spot_balance_usdc(client)
            body += f"\n\nSolde SPOT global : {spot_balance_usdc:.2f} USDC"
        except Exception:
            pass
    return send_email_alert(subject, body)

def retry_with_backoff(max_retries: int = 3, base_delay: float = 1.0):
    """Decorateur pour retry avec backoff exponentiel."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise e
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"Tentative {attempt + 1} echouee pour {func.__name__}: {e}. Retry dans {delay}s")
                    time.sleep(delay)
            return None
        return wrapper
    return decorator

@log_exceptions(default_return=False)
@retry_with_backoff(max_retries=3, base_delay=2.0)
def send_email_alert(subject: str, body: str) -> bool:
    """Envoie une alerte par email avec retry automatique."""
    try:
        try:
            spot_balance_usdc = get_spot_balance_usdc(client)
            body += f"\n\nSolde SPOT global : {spot_balance_usdc:.2f} USDC"
        except Exception:
            pass

        msg = MIMEMultipart()
        msg['From'] = config.sender_email
        msg['To'] = config.receiver_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        with smtplib.SMTP(config.smtp_server, config.smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(config.sender_email, config.smtp_password)
            server.sendmail(config.sender_email, config.receiver_email, msg.as_string())
        logger.info("Email d'alerte envoye avec succes")
        print("[green][SUCCESS] Email d'alerte envoye[/green]")
        return True
    except Exception as e:
        logger.error(f"Erreur envoi email: {e}")
        raise
import threading
_tickers_cache = {'data': None, 'timestamp': 0}
_tickers_lock = threading.Lock()
def get_all_tickers_cached(client, cache_ttl: int = 10) -> dict:
    """
    Récupère tous les tickers Binance avec cache local (TTL en secondes).
    Permet d'éviter les appels API redondants pour les conversions d'actifs.
    Args:
        client: Client Binance API.
        cache_ttl: Durée de vie du cache en secondes.
    Returns:
        Dictionnaire {symbol: prix}.
    """
    """Récupère tous les tickers Binance avec cache local (TTL en secondes)."""
    now = time.time()
    with _tickers_lock:
        if _tickers_cache['data'] is not None and (now - _tickers_cache['timestamp'] < cache_ttl):
            return _tickers_cache['data']
        tickers = {t['symbol']: float(t['price']) for t in client.get_all_tickers()}
        _tickers_cache['data'] = tickers
        _tickers_cache['timestamp'] = now
        return tickers
def log_exceptions(default_return=None):
    """Décorateur pour logguer les exceptions et retourner une valeur par défaut."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.error(f"[EXCEPTION] {func.__name__}: {e}")
                # Envoi d'une alerte mail pour toute exception
                try:
                    send_trading_alert_email(
                        subject=f"[BOT CRYPTO] EXCEPTION: {func.__name__}",
                        body_main=f"Exception dans {func.__name__}: {e}\n\nArgs: {args}\nKwargs: {kwargs}",
                        client=client
                    )
                except Exception as mail_exc:
                    logger.error(f"[EXCEPTION] Echec envoi mail d'alerte: {mail_exc}")
                    return default_return if default_return is not None else None
        return wrapper
    return decorator

def send_trading_alert_email(subject: str, body_main: str, client, add_spot_balance: bool = True) -> bool:
    """
    Envoie un email d'alerte de trading avec possibilité d'injecter le solde SPOT USDC.
    Args:
        subject: Sujet de l'email.
        body_main: Corps principal du message.
        client: Client Binance API.
        add_spot_balance: Ajoute le solde SPOT USDC si True.
    Returns:
        Booléen succès/échec.
    """
    """Centralized function to send trading alert emails with optional SPOT balance injection."""
    body = body_main
    if add_spot_balance:
        try:
            spot_balance_usdc = get_spot_balance_usdc(client)
            body += f"\n\nSolde SPOT global : {spot_balance_usdc:.2f} USDC"
        except Exception:
            pass
    return send_email_alert(subject, body)

@retry_with_backoff(max_retries=3, base_delay=2.0)
@log_exceptions(default_return=None)
def place_trailing_stop_order(symbol: str, quantity: float, activation_price: float, trailing_delta: int, client_id: str = None) -> dict:
    """
    Place un ordre TRAILING_STOP_MARKET sur Binance (spot).
    """
    timestamp = int(time.time() * 1000)
    params = {
        'symbol': symbol,
        'side': 'SELL',
        'type': 'TRAILING_STOP_MARKET',
        'quantity': float(quantity),
        'activationPrice': float(activation_price),
        'trailingDelta': int(trailing_delta),
        'timestamp': timestamp,
        'recvWindow': 10000
    }
    if client_id:
        params['newClientOrderId'] = client_id
    # Signature
    query_string = '&'.join([f"{k}={v}" for k, v in sorted(params.items())])
    signature = hmac.new(
        client.api_secret.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    params['signature'] = str(signature)
    url = "https://api.binance.com/api/v3/order"
    headers = {'X-MBX-APIKEY': client.api_key}
    response = requests.post(url, data=params, headers=headers, timeout=30)
    if response.status_code != 200:
        try:
            error_data = response.json()
            error_code = error_data.get('code', 'UNKNOWN')
            error_msg = error_data.get('msg', 'Unknown error')
            # Envoi d'une alerte mail en cas d'erreur d'ordre
            send_trading_alert_email(
                subject="[BOT CRYPTO] ERREUR EXECUTION TRAILING STOP",
                body_main=f"Erreur lors de l'execution de l'ordre TRAILING STOP : {error_code} - {error_msg}\n\nParams : {params}",
                client=client
            )
            raise Exception(f"Binance API error {error_code}: {error_msg}")
        except Exception:
            response.raise_for_status()
    result = response.json()
    # Envoi d'une alerte mail pour toute transaction exécutée
    send_trading_alert_email(
        subject="[BOT CRYPTO] TRAILING STOP EXECUTE",
        body_main=f"Ordre TRAILING STOP exécuté avec succès.\n\nParams : {params}\nRéponse : {result}",
        client=client
    )
    return result

@retry_with_backoff(max_retries=3, base_delay=2.0)
@log_exceptions(default_return=None)
def place_stop_loss_order(symbol: str, quantity: float, stop_price: float, client_id: str = None) -> dict:
    """
    Place un ordre STOP_LOSS sur Binance (spot).
    """
    timestamp = int(time.time() * 1000)
    params = {
        'symbol': symbol,
        'side': 'SELL',
        'type': 'STOP_LOSS',
        'quantity': float(quantity),
        'stopPrice': float(stop_price),
        'timestamp': timestamp,
        'recvWindow': 10000
    }
    if client_id:
        params['newClientOrderId'] = client_id
    # Signature
    query_string = '&'.join([f"{k}={v}" for k, v in sorted(params.items())])
    signature = hmac.new(
        client.api_secret.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    params['signature'] = str(signature)
    url = "https://api.binance.com/api/v3/order"
    headers = {'X-MBX-APIKEY': client.api_key}
    response = requests.post(url, data=params, headers=headers, timeout=30)
    if response.status_code != 200:
        try:
            error_data = response.json()
            error_code = error_data.get('code', 'UNKNOWN')
            error_msg = error_data.get('msg', 'Unknown error')
            # Envoi d'une alerte mail en cas d'erreur d'ordre
            send_trading_alert_email(
                subject="[BOT CRYPTO] ERREUR EXECUTION STOP LOSS",
                body_main=f"Erreur lors de l'execution de l'ordre STOP LOSS : {error_code} - {error_msg}\n\nParams : {params}",
                client=client
            )
            raise Exception(f"Binance API error {error_code}: {error_msg}")
        except Exception:
            response.raise_for_status()
    result = response.json()
    # Envoi d'une alerte mail pour toute transaction exécutée
    send_trading_alert_email(
        subject="[BOT CRYPTO] STOP LOSS EXECUTE",
        body_main=f"Ordre STOP LOSS exécuté avec succès.\n\nParams : {params}\nRéponse : {result}",
        client=client
    )
    return result


# Import Cython modules for optimization (en-tête du fichier)
try:
    import backtest_engine_standard as backtest_engine  # type: ignore
    CYTHON_BACKTEST_AVAILABLE = True
except ImportError as e:
    CYTHON_BACKTEST_AVAILABLE = False
    print(f"Warning: Cython backtest_engine_standard not available ({e}), using Python fallback")
    backtest_engine = None  # Set to None to avoid NameError later

logger = logging.getLogger(__name__)
console = Console()

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('trading_bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

# Paramètre pour activer/désactiver les logs détaillés (VERBOSE = False pour plus de rapidité)
VERBOSE_LOGS = False  # Mettre à True pour activer les diagnostics détaillés
logger = logging.getLogger(__name__)



# Client Binance ULTRA-ROBUSTE - Solution définitive timestamp
class BinanceFinalClient(Client):
    """Client Binance ULTRA ROBUSTE - Correction définitive du timestamp -1021"""

    def __init__(self, api_key, api_secret, **kwargs):
        self.api_key = api_key  # Correction pour accès direct
        self.api_secret = api_secret  # Correction pour accès direct
        self._server_time_offset = -2000
        self._last_sync = 0
        self._sync_interval = 180
        self._error_count = 0
        self._max_errors = 5
        kwargs['requests_params'] = {'timeout': 45}
        super().__init__(api_key, api_secret, **kwargs)
        logger.info("Client Binance ULTRA ROBUSTE initialisé")
        self._perform_ultra_robust_sync()

    def _perform_ultra_robust_sync(self):
        """Synchronisation radicale avec compensation complète."""
        try:
            local_before = int(time.time() * 1000)
            server_time = self.get_server_time()['serverTime']
            local_after = int(time.time() * 1000)
            latency = (local_after - local_before) // 2
            real_offset = server_time - (local_before + latency)
            if real_offset > -2000:
                forced_offset = -5000
            else:
                forced_offset = real_offset
            test_local = int(time.time() * 1000)
            test_timestamp = test_local + forced_offset
            test_diff = test_timestamp - server_time
            if test_diff > 800:
                forced_offset = -8000
            self._server_time_offset = forced_offset
            self._last_sync = time.time()
            self._error_count = 0
            logger.info(f"SYNCHRO OK: offset={self._server_time_offset}ms")
        except Exception as e:
            logger.error(f"Echec synchronisation: {e}")
            self._server_time_offset = -10000

    def _get_ultra_safe_timestamp(self):
        """Génère un timestamp garanti correct."""
        current_time = time.time()
        if (current_time - self._last_sync > 60 or self._error_count > 0):
            self._perform_ultra_robust_sync()
        local_ts = int(current_time * 1000)
        safe_ts = local_ts + self._server_time_offset
        try:
            server_check = self.get_server_time()['serverTime']
            diff = safe_ts - server_check
            if diff > 500:
                correction = diff + 1000
                self._server_time_offset -= correction
                safe_ts = int(time.time() * 1000) + self._server_time_offset
        except Exception:
            pass
        return safe_ts

    def _request(self, method, uri, signed, force_params=False, **kwargs):
        """Override MINIMAL - only handle timestamp sync, let parent handle params.

        The parent Client._request() already correctly:
        - Adds timestamp + signature for signed requests
        - Adds recvWindow with sane defaults
        - Converts params/data to query string appropriately

        We ONLY need to handle timestamp synchronization, nothing else!
        """
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Sanitize any recvWindow provided by callers to avoid duplicate-parameter errors
                try:
                    # Remove recvWindow if passed as top-level kwarg
                    if 'recvWindow' in kwargs:
                        logger.debug("_request: Removing caller-provided recvWindow from kwargs to avoid duplicate parameter")
                        kwargs.pop('recvWindow', None)

                    # If params or data dict contains recvWindow, remove it as well
                    if 'params' in kwargs and isinstance(kwargs['params'], dict) and 'recvWindow' in kwargs['params']:
                        logger.debug("_request: Removing recvWindow from kwargs['params'] to avoid duplicate parameter")
                        kwargs['params'].pop('recvWindow', None)
                    if 'data' in kwargs and isinstance(kwargs['data'], dict) and 'recvWindow' in kwargs['data']:
                        logger.debug("_request: Removing recvWindow from kwargs['data'] to avoid duplicate parameter")
                        kwargs['data'].pop('recvWindow', None)
                except Exception as _sanitize_ex:
                    logger.debug(f"_request: recvWindow sanitation failed: {_sanitize_ex}")

                # Call parent with sanitized kwargs. Parent will add timestamp/signature and its own recvWindow.
                result = super()._request(method, uri, signed, force_params=force_params, **kwargs)
                self._error_count = max(0, self._error_count - 1)
                return result
                
            except BinanceAPIException as e:
                # Handle timestamp -1021 specifically by resyncing
                if getattr(e, 'code', None) == -1021:
                    self._error_count += 1
                    logger.warning(f"Erreur -1021 détectée (tentative {attempt+1}), resync obligatoire")
                    self._perform_ultra_robust_sync()
                    if attempt < max_retries - 1:
                        time.sleep(2)
                        continue
                    raise
                else:
                    # For other errors, just log and raise
                    if getattr(e, 'code', None) == -1101:
                        logger.error(f"BinanceAPIException -1101 (Duplicate recvWindow): {e}")
                        logger.error(f"  Request kwargs après sanitation: {kwargs}")
                        logger.error("  CETTE ERREUR NE DEVRAIT PLUS SE PRODUIRE - Vérifier le code d'appel")
                    else:
                        logger.error(f"BinanceAPIException: {e}")
                    if 'duplicate' in str(e).lower():
                        logger.error("DUPLICATE PARAMETER DETECTED - This should NOT happen with clean code")
                    raise
                    
            except Exception as e:
                logger.error(f"Erreur inattendue dans _request: {e}")
                if attempt < max_retries - 1:
                    # Affichage du temps restant avant la prochaine exécution
                    now = datetime.now()
                    next_run = schedule.next_run()
                    if next_run:
                        delta = next_run - now
                        minutes_left = max(0, int(delta.total_seconds() // 60))
                        print(f"[TIME] {now.strftime('%H:%M:%S')} - Bot actif (RUNNING) | Prochaine execution dans {minutes_left} min ({next_run.strftime('%H:%M:%S')})")
                    else:
                        print(f"[TIME] {now.strftime('%H:%M:%S')} - Bot actif (RUNNING) | Prochaine execution non planifiée")
                    time.sleep(300)  # Mise à jour toutes les 5 minutes
                    continue
                raise
        
        # Fallback
        return None

    def _sync_server_time(self):
        self._perform_ultra_robust_sync()

    def _sync_server_time_robust(self):
        self._perform_ultra_robust_sync()

    def _get_synchronized_timestamp(self):
        return self._get_ultra_safe_timestamp()


# Initialisation du client
client = BinanceFinalClient(
    config.api_key, 
    config.secret_key,
    requests_params={'timeout': config.api_timeout}
)

# Timeframes
timeframes = [
    Client.KLINE_INTERVAL_1HOUR,
    Client.KLINE_INTERVAL_4HOUR,
    Client.KLINE_INTERVAL_1DAY
]

# Calcul de la date de debut
today = datetime.today()
start_date = (today - timedelta(days=config.backtest_days)).strftime("%d %B %Y")

# Cache pour les indicateurs
indicators_cache: Dict[str, pd.DataFrame] = {}

# etat du bot
bot_state: Dict[str, Dict] = {}

# Variable globale pour le répertoire cache (pour éviter les créations multiples)
_cache_dir_initialized = False

# Variable globale pour stocker la paire courante lors des backtests
_current_backtest_pair = None

###############################################################
#                                                             #
#              *** UTILITAIRES ET HELPERS ***                 #
#                                                             #
###############################################################

def full_timestamp_resync():
    try:
        sync_windows_silently()
        time.sleep(1)  
        client._sync_server_time()
        logger.info("Synchronisation complète (Windows + Binance) effectuée avant envoi d’ordre.")
    except Exception as e:
        logger.error(f"Echec de la resynchronisation horaire: {e}")

def retry_with_backoff(max_retries: int = 3, base_delay: float = 1.0):
    """Decorateur pour retry avec backoff exponentiel."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise e
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"Tentative {attempt + 1} echouee pour {func.__name__}: {e}. Retry dans {delay}s")
                    time.sleep(delay)
            return None
        return wrapper
    return decorator

def validate_api_connection() -> bool:
    """Valide la connexion a l'API Binance."""
    try:
        # Test de connexion basique
        client.ping()
        logger.info(f"Connexion API validée")
        return True
    except Exception as e:
        logger.error(f"Echec de validation API: {e}")
        try:
            email_body = f"""
=== ECHEC DE CONNEXION API BINANCE ===

Le bot n'a pas pu etablir une connexion avec l'API Binance.

DETAILS DE L'INCIDENT:
----------------------
Horodatage          : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Erreur rencontree   : {str(e)[:150]}{'...' if len(str(e)) > 150 else ''}
Type d'erreur       : Connexion API

IMPACT SUR LE BOT:
------------------
Le bot ne peut pas fonctionner sans connexion API.
Toutes les operations de trading sont suspendues.

ACTIONS RECOMMANDEES:
---------------------
1. Verifier la connexion internet
2. Controler les cles API Binance
3. Verifier le statut de l'API Binance
4. Redemarrer le bot apres resolution

--- Message automatique du Bot de Trading Crypto ---
            """
            # Ajout du solde global Binance à l'email
            try:
                account_info = client.get_account()
                tickers = get_all_tickers_cached(client)
                spot_balance_usdc = 0.0
                for bal in account_info['balances']:
                    asset = bal['asset']
                    free = float(bal['free'])
                    locked = float(bal['locked'])
                    total = free + locked
                    if total < 1e-8:
                        continue
                    if asset == 'USDC':
                        spot_balance_usdc += total
                    else:
                        symbol1 = asset + 'USDC'
                        symbol2 = 'USDC' + asset
                        price = None
                        if symbol1 in tickers:
                            price = tickers[symbol1]
                            spot_balance_usdc += total * price
                        elif symbol2 in tickers and tickers[symbol2] > 0:
                            price = 1.0 / tickers[symbol2]
                            spot_balance_usdc += total * price
                # SPOT balance will be injected by send_trading_alert_email
            except Exception:
                pass
            send_trading_alert_email(
                subject="[BOT CRYPTO] ERREUR Connexion API",
                body_main=email_body,
                client=client
            )
        except:
            pass
        return False

@log_exceptions(default_return=False)
def validate_data_integrity(df: pd.DataFrame) -> bool:
    """Valide l'integrite des donnees de marche."""
    if df.empty:
        return False
    # Verifier les valeurs negatives
    if (df[['open', 'high', 'low', 'close', 'volume']] < 0).any().any():
        logger.warning("Valeurs negatives detectees dans les donnees")
        return False
    # Verifier la coherence OHLC
    invalid_ohlc = (df['high'] < df[['open', 'close']].max(axis=1)) | \
                   (df['low'] > df[['open', 'close']].min(axis=1))
    if invalid_ohlc.any():
        logger.warning("Donnees OHLC incoherentes detectees")
        return False
    # Detecter les gaps temporels (sans warning)
    time_diff = df.index.to_series().diff()
    expected_interval = time_diff.mode()[0] if not time_diff.mode().empty else None
    if expected_interval:
        gaps = time_diff > expected_interval * 1.5
        if gaps.any():
            pass  # Silence, plus de warning
    return True


def get_cache_key(pair: str, interval: str, params: Dict) -> str:
    """Genere une cle de cache unique pour les indicateurs."""
    # Utilisation d'un cache mémoire pour accélérer les accès redondants
    key_data = f"{pair}_{interval}_{json.dumps(params, sort_keys=True)}"
    return hashlib.md5(key_data.encode()).hexdigest()

@log_exceptions(default_return=None)
def save_bot_state():
    """Sauvegarde l'etat du bot."""
    try:
        os.makedirs(config.states_dir, exist_ok=True)
        state_path = os.path.join(config.states_dir, config.state_file)
        # Only write if state changed (by hash)
        state_bytes = pickle.dumps(bot_state)
        old_hash = None
        if os.path.exists(state_path):
            with open(state_path, 'rb') as f:
                old_hash = hash(f.read())
        new_hash = hash(state_bytes)
        if old_hash != new_hash:
            with open(state_path, 'wb') as f:
                f.write(state_bytes)
            logger.debug("etat du bot sauvegarde (modifié)")
        else:
            logger.debug("etat du bot inchangé, pas de sauvegarde")
    except Exception as e:
        logger.error(f"Erreur lors de la sauvegarde: {e}")

@log_exceptions(default_return=None)
def load_bot_state():
    """Charge l'etat du bot."""
    global bot_state
    try:
        state_path = os.path.join(config.states_dir, config.state_file)
        if os.path.exists(state_path):
            with open(state_path, 'rb') as f:
                bot_state = pickle.load(f)
            logger.info("etat du bot charge")
    except Exception as e:
        logger.error(f"Erreur lors du chargement: {e}")


# --- Centralisation de la logique de solde SPOT USDC ---
@log_exceptions(default_return=0.0)
def get_spot_balance_usdc(client) -> float:
    """Retourne le solde SPOT global converti en USDC (y compris coins)."""
    try:
        account_info = client.get_account()
        tickers = get_all_tickers_cached(client)
        spot_balance_usdc = 0.0
        for bal in account_info['balances']:
            asset = bal['asset']
            free = float(bal['free'])
            locked = float(bal['locked'])
            total = free + locked
            if total < 1e-8:
                continue
            if asset == 'USDC':
                spot_balance_usdc += total
            else:
                symbol1 = asset + 'USDC'
                symbol2 = 'USDC' + asset
                price = None
                if symbol1 in tickers:
                    price = tickers[symbol1]
                    spot_balance_usdc += total * price
                elif symbol2 in tickers and tickers[symbol2] > 0:
                    price = 1.0 / tickers[symbol2]
                    spot_balance_usdc += total * price
        return spot_balance_usdc
    except Exception:
        return 0.0

@retry_with_backoff(max_retries=3, base_delay=2.0)
def send_email_alert(subject: str, body: str) -> bool:
    """Envoie une alerte par email avec retry automatique."""
    try:
        # Ajout automatique du solde SPOT global à la fin de chaque email
        try:
            spot_balance_usdc = get_spot_balance_usdc(client)
            body += f"\n\nSolde SPOT global : {spot_balance_usdc:.2f} USDC"
        except Exception:
            pass

        msg = MIMEMultipart()
        msg['From'] = config.sender_email
        msg['To'] = config.receiver_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        with smtplib.SMTP(config.smtp_server, config.smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(config.sender_email, config.smtp_password)
            server.sendmail(config.sender_email, config.receiver_email, msg.as_string())
        logger.info("Email d'alerte envoye avec succes")
        print("[green][SUCCESS] Email d'alerte envoye[/green]")
        return True
    except Exception as e:
        logger.error(f"Erreur envoi email: {e}")
        raise

@log_exceptions(default_return={'min_qty': Decimal('0.001'), 'step_size': Decimal('0.000001')})
def get_symbol_filters(symbol: str) -> Dict:
    """Recupere les filters min_qty et step_size pour un symbole."""
    info = client.get_symbol_info(symbol)
    if not info:
        raise ValueError(f"Aucune information trouvee pour le symbole {symbol}")
    for f in info['filters']:
        if f['filterType'] == 'LOT_SIZE':
            min_qty = Decimal(f['minQty'])
            step_size = Decimal(f['stepSize'])
            logger.info(f"Filters pour {symbol}: min_qty={min_qty}, step_size={step_size}")
            return {
                'min_qty': min_qty,
                'step_size': step_size
            }
    raise ValueError(f"LOT_SIZE filter not found pour {symbol}")

###############################################################
#                                                             #
#              *** RECUPERATION DES DONNEES ***               #
#                                                             #
###############################################################

def get_cache_path(pair_symbol: str, time_interval: str, start_date: str) -> Tuple[str, str]:
    """Génère des chemins de cache sécurisés et uniques."""
    # Normaliser le format de date pour éviter les conflits
    normalized_date = start_date.replace(" ", "_").replace(":", "-").replace(",", "")
    safe_name = f"{pair_symbol}_{time_interval}_{normalized_date}"
    
    # Vérifier si un cache existant avec un format légèrement différent existe
    cache_dir = config.cache_dir
    if os.path.exists(cache_dir):
        for existing_file in os.listdir(cache_dir):
            if (existing_file.startswith(f"{pair_symbol}_{time_interval}_") and 
                existing_file.endswith(".pkl") and
                pair_symbol in existing_file and time_interval in existing_file):
                # Utiliser le fichier existant
                cache_file = os.path.join(cache_dir, existing_file)
                lock_file = os.path.join(cache_dir, existing_file.replace(".pkl", ".lock"))
                return cache_file, lock_file
    
    # Créer un nouveau nom si aucun fichier existant trouvé
    cache_file = os.path.join(cache_dir, f"{safe_name}.pkl")
    lock_file = os.path.join(cache_dir, f"{safe_name}.lock")
    return cache_file, lock_file

def is_cache_expired(cache_file: str, max_age_days: int = 30) -> bool:
    """Vérifie si le cache a expiré (par défaut 30 jours)."""
    if not os.path.exists(cache_file):
        return True
    
    try:
        cache_age = time.time() - os.path.getmtime(cache_file)
        max_age_seconds = max_age_days * 24 * 3600
        return cache_age > max_age_seconds
    except Exception:
        return True

@log_exceptions(default_return=None)
def safe_cache_read(cache_file: str) -> Optional[pd.DataFrame]:
    """Lecture ultra-sécurisée du cache avec validation complète et expiration mensuelle."""
    if not os.path.exists(cache_file):
        return None
    
    try:
        # Vérifier l'expiration mensuelle
        if is_cache_expired(cache_file, max_age_days=30):
            logger.info(f"Cache expiré (>30 jours): {os.path.basename(cache_file)} - Mise à jour nécessaire")
            try:
                os.remove(cache_file)
            except:
                pass
            return None
        
        # Vérifications de sécurité
        file_size = os.path.getsize(cache_file)
        if file_size == 0:
            try:
                os.remove(cache_file)
            except:
                pass
            return None
        
        if file_size > 100 * 1024 * 1024:  # 100MB max
            try:
                os.remove(cache_file)
            except:
                pass
            return None
        
        # Lecture sécurisée
        with open(cache_file, 'rb') as f:
            df = pickle.load(f)
        
        # Validation des données
        if df.empty or len(df) < 10:
            try:
                os.remove(cache_file)
            except:
                pass
            return None
        
        logger.debug(f"Cache lu avec succès: {os.path.basename(cache_file)}")
        return df
        
    except (FileNotFoundError, PermissionError, EOFError, pickle.UnpicklingError):
        # Erreurs normales de cache - pas de log
        return None
    except Exception:
        # Autres erreurs - nettoyage silencieux
        try:
            os.remove(cache_file)
        except:
            pass
        return None

@log_exceptions(default_return=False)
def safe_cache_write(cache_file: str, lock_file: str, df: pd.DataFrame) -> bool:
    """Écriture ultra-sécurisée du cache avec protection complète."""
    if df.empty:
        return False
    
    try:
        # Attendre que le verrou soit libéré (max 10 secondes pour éviter les blocages)
        lock_timeout = 10
        lock_start = time.time()
        
        while os.path.exists(lock_file):
            if (time.time() - lock_start) > lock_timeout:
                logger.debug(f"Timeout verrou cache, abandon: {lock_file}")
                return False  # Abandonner plutôt que forcer
            time.sleep(0.1)
        
        # Créer le verrou
        try:
            with open(lock_file, 'w') as lock:
                lock.write(f"{os.getpid()}_{int(time.time())}")
        except Exception:
            return False  # Abandonner silencieusement
        
        try:
            # Écriture atomique simplifiée
            temp_file = cache_file + f".tmp_{os.getpid()}_{int(time.time())}"
            # Only write if cache changed (by hash)
            new_bytes = pickle.dumps(df)
            old_hash = None
            if os.path.exists(cache_file):
                with open(cache_file, 'rb') as f:
                    old_hash = hash(f.read())
            new_hash = hash(new_bytes)
            if old_hash != new_hash:
                with open(temp_file, 'wb') as f:
                    f.write(new_bytes)
                # Renommage atomique
                if os.path.exists(cache_file):
                    try:
                        os.remove(cache_file)
                    except:
                        pass
                os.rename(temp_file, cache_file)
                logger.debug(f"Cache sauvegardé: {os.path.basename(cache_file)} (modifié)")
                return True
            else:
                logger.debug(f"Cache inchangé, pas de sauvegarde: {os.path.basename(cache_file)}")
                return True
        except Exception:
            # Nettoyer silencieusement
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except:
                pass
            return False
            
        finally:
            # Supprimer le verrou
            try:
                os.remove(lock_file)
            except:
                pass
                
    except Exception:
        return False

@retry_with_backoff(max_retries=5, base_delay=2.0)
def update_cache_with_recent_data(cached_df: pd.DataFrame, pair_symbol: str, time_interval: str) -> pd.DataFrame:
    """
    Mise à jour INTELLIGENTE du cache avec les dernières bougies.
    
    Au lieu de retélécharger 5 ans de données, récupère uniquement les 2-3 dernières heures
    et les ajoute au cache existant. C'est 100x plus rapide et économe en API calls.
    """
    try:
        if cached_df.empty:
            return cached_df
        
        # Déterminer combien de bougies récentes chercher basé sur le timeframe
        lookback_map = {
            Client.KLINE_INTERVAL_1HOUR: "3 hours ago",
            Client.KLINE_INTERVAL_4HOUR: "12 hours ago",
            Client.KLINE_INTERVAL_1DAY: "7 days ago"
        }
        lookback = lookback_map.get(time_interval, "3 hours ago")
        
        # Récupérer UNIQUEMENT les dernières bougies
        recent_klines = client.get_historical_klines(pair_symbol, time_interval, lookback)
        
        if not recent_klines:
            return cached_df
        
        # Convertir en DataFrame
        df_recent = pd.DataFrame(recent_klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_av', 'trades', 'tb_base_av', 'tb_quote_av', 'ignore'
        ])
        df_recent = df_recent[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
        df_recent[['open', 'high', 'low', 'close', 'volume']] = df_recent[['open', 'high', 'low', 'close', 'volume']].astype(float)
        df_recent['timestamp'] = pd.to_datetime(df_recent['timestamp'], unit='ms')
        df_recent.set_index('timestamp', inplace=True)
        
        # Combiner intelligemment: retirer les doublons et ajouter les nouvelles bougies
        # Les timestamps du cache et des récentes doivent être alignés
        last_cache_time = cached_df.index[-1]
        
        # Garder uniquement les bougies récentes qui sont APRÈS le cache
        df_recent = df_recent[df_recent.index > last_cache_time]
        
        if df_recent.empty:
            logger.debug(f"Aucune nouvelle bougie pour {pair_symbol} {time_interval}")
            return cached_df
        
        # Fusionner: cache existant + nouvelles bougies
        df_merged = pd.concat([cached_df, df_recent])
        df_merged = df_merged[~df_merged.index.duplicated(keep='last')]  # Retirer les doublons en gardant la dernière
        df_merged = df_merged.sort_index()
        
        new_candles_count = len(df_recent)
        logger.info(f"[CACHE] Cache updated: {pair_symbol} {time_interval} (+{new_candles_count} new candles)")
        
        return df_merged
        
    except Exception as e:
        logger.debug(f"Mise à jour du cache échouée pour {pair_symbol}: {e}")
        return cached_df


@retry_with_backoff(max_retries=3, base_delay=2.0)
@log_exceptions(default_return=pd.DataFrame())
def fetch_historical_data(pair_symbol: str, time_interval: str, start_date: str, force_refresh: bool = False) -> pd.DataFrame:
    """Recupere les donnees historiques avec validation et cache thread-safe.
    IMPORTANT: force_refresh=True pour bypasser le cache et forcer un telechargement frais.
    Utilise la pagination pour recuperer TOUTES les donnees depuis start_date."""
    try:
        # Initialiser le cache une seule fois
        global _cache_dir_initialized
        if not _cache_dir_initialized:
            try:
                os.makedirs(config.cache_dir, exist_ok=True)
                _cache_dir_initialized = True
                logger.debug(f"Répertoire cache initialisé: {config.cache_dir}")
            except Exception:
                # Fallback silencieux vers cache temporaire
                import tempfile
                config.cache_dir = tempfile.mkdtemp(prefix="crypto_cache_")
                _cache_dir_initialized = True
                logger.debug(f"Cache temporaire créé: {config.cache_dir}")
        
        # Générer des chemins sécurisés
        cache_file, lock_file = get_cache_path(pair_symbol, time_interval, start_date)
        
        # Lecture ultra-sécurisée du cache (sauf si force_refresh=True)
        if not force_refresh:
            cached_df = safe_cache_read(cache_file)
            if cached_df is not None and not cached_df.empty:
                #  MISE À JOUR INTELLIGENTE: Ajouter les dernières bougies sans retélécharger 5 ans
                updated_df = update_cache_with_recent_data(cached_df, pair_symbol, time_interval)
                
                # Sauvegarder le cache mis à jour
                safe_cache_write(cache_file, lock_file, updated_df)
                
                logger.info(f"[OK] Cache used + updated: {pair_symbol} {time_interval} ({len(updated_df)} candles)")
                return updated_df
        
        # Recuperation depuis l'API - Récupérer TOUTES les données depuis start_date
        if not VERBOSE_LOGS:
            logger.info(f"Telechargement {pair_symbol} {time_interval}...")
        else:
            logger.info(f"Debut telechargement pour {pair_symbol} {time_interval} depuis {start_date}")
        
        try:
            # Récupérer TOUTES les données depuis start_date (pagination automatique)
            klinesT = client.get_historical_klines(pair_symbol, time_interval, start_date)
            
            if not klinesT:
                raise ValueError(f"Aucune donnee pour {pair_symbol} et {time_interval}")
            
            if not VERBOSE_LOGS:
                logger.info(f"OK: {len(klinesT)} candles | {pd.Timestamp(klinesT[0][0], unit='ms').strftime('%Y-%m-%d')} -> {pd.Timestamp(klinesT[-1][0], unit='ms').strftime('%Y-%m-%d')}")
            else:
                logger.info(f"Telechargement complete: {len(klinesT)} candles recuperees pour {pair_symbol}")
                logger.info(f"  Date premiere bougie: {pd.Timestamp(klinesT[0][0], unit='ms')}")
                logger.info(f"  Date derniere bougie: {pd.Timestamp(klinesT[-1][0], unit='ms')}")
            
        except Exception as e:
            logger.error(f"Erreur lors du telechargement: {e}")
            raise
        
        all_klines = klinesT
        
        # Creation du DataFrame
        df = pd.DataFrame(all_klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume', 
            'close_time', 'quote_av', 'trades', 'tb_base_av', 'tb_quote_av', 'ignore'
        ])
        df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
        
        # Conversion securisee
        numeric_columns = ['open', 'high', 'low', 'close', 'volume']
        df[numeric_columns] = df[numeric_columns].astype(float)
        
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        
        # Validation des donnees
        if not validate_data_integrity(df):
            logger.warning(f"Donnees invalides pour {pair_symbol}")
            
        # Sauvegarde ultra-sécurisée du cache
        safe_cache_write(cache_file, lock_file, df)
        
        return df
        
    except BinanceAPIException as e:
        logger.error(f"Erreur API Binance: {e}")
        try:
            email_body = f"""
=== ERREUR DE RECUPERATION DES DONNEES ===

Echec lors du telechargement des donnees historiques depuis Binance.

DETAILS DE L'INCIDENT:
----------------------
Paire concernee     : {pair_symbol}
Intervalle          : {time_interval}
Periode demandee    : {start_date}
Horodatage          : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Erreur rencontree   : {str(e)[:150]}{'...' if len(str(e)) > 150 else ''}

IMPACT SUR LE BOT:
------------------
Impossible de calculer les indicateurs techniques.
Les backtests et le trading sont affectes.

ACTIONS RECOMMANDEES:
---------------------
1. Verifier la connexion internet
2. Controler la validite de la paire {pair_symbol}
3. Verifier le statut de l'API Binance
4. Attendre et relancer automatiquement

--- Message automatique du Bot de Trading Crypto ---
            """
            # Ajout du solde global Binance à l'email
            try:
                account_info = client.get_account()
                tickers = get_all_tickers_cached(client)
                global_balance_usdc = 0.0
                for bal in account_info['balances']:
                    asset = bal['asset']
                    free = float(bal['free'])
                    locked = float(bal['locked'])
                    total = free + locked
                    if total < 1e-8:
                        continue
                    if asset == 'USDC':
                        global_balance_usdc += total
                    else:
                        symbol1 = asset + 'USDC'
                        symbol2 = 'USDC' + asset
                        price = None
                        if symbol1 in tickers:
                            price = tickers[symbol1]
                            global_balance_usdc += total * price
                        elif symbol2 in tickers and tickers[symbol2] > 0:
                            price = 1.0 / tickers[symbol2]
                            global_balance_usdc += total * price
                # SPOT balance will be injected by send_trading_alert_email
            except Exception:
                pass
            send_trading_alert_email(
                subject=f"[BOT CRYPTO] ERREUR Donnees - {pair_symbol}",
                body_main=email_body,
                client=client
            )
        except:
            pass
        raise
    except Exception as e:
        error_str = str(e)
        logger.error(f"Erreur recuperation donnees: {e}")
        
        # Détecter les erreurs de réseau
        if any(keyword in error_str.lower() for keyword in ['nameresolutionerror', 'getaddrinfo failed', 'max retries exceeded', 'connection']):
            logger.warning("Erreur de connectivité détectée, tentative de récupération...")
            
            if check_network_connectivity():
                logger.info("Connexion rétablie, nouvelle tentative...")
                time.sleep(3)
                # Une seule tentative supplémentaire
                try:
                    klinesT = client.get_historical_klines(pair_symbol, time_interval, start_date)
                    if klinesT:
                        df = pd.DataFrame(klinesT, columns=[
                            'timestamp', 'open', 'high', 'low', 'close', 'volume', 
                            'close_time', 'quote_av', 'trades', 'tb_base_av', 'tb_quote_av', 'ignore'
                        ])
                        df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
                        numeric_columns = ['open', 'high', 'low', 'close', 'volume']
                        df[numeric_columns] = df[numeric_columns].astype(float)
                        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                        df.set_index('timestamp', inplace=True)
                        logger.info(f"Données récupérées après rétablissement connexion")
                        return df
                except Exception as retry_error:
                    logger.error(f"echec après rétablissement: {retry_error}")
        
        try:
            email_body = f"""
=== PERTE DE CONNEXION RESEAU DETECTEE ===

Une erreur de connectivite reseau a ete detectee lors du telechargement.

DETAILS DE L'INCIDENT:
----------------------
Paire concernee     : {pair_symbol}
Intervalle          : {time_interval}
Horodatage          : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Erreur rencontree   : {str(e)[:150]}{'...' if len(str(e)) > 150 else ''}

ACTION AUTOMATIQUE:
-------------------
Le bot tentera de retablir la connexion automatiquement.
Plusieurs tentatives seront effectuees.

SURVEILLANCE REQUISE:
---------------------
1. Verifier la stabilite de la connexion internet
2. Controler les parametres reseau
3. Surveiller les prochains emails du bot
4. Redemarrer manuellement si necessaire

--- Message automatique du Bot de Trading Crypto ---
            """
            send_trading_alert_email(
                subject=f"[BOT CRYPTO] ERREUR Reseau - {pair_symbol}",
                body_main=email_body,
                client=client
            )
        except:
            pass
        return pd.DataFrame()

###############################################################
#                                                             #
#                *** CALCUL DES INDICATEURS ***               #
#                                                             #
###############################################################

def get_cache_key(prefix: str, df: pd.DataFrame, params: Dict[str, Any]) -> str:
    """Génère une clé de cache unique basée sur les données et les paramètres."""
    if df.empty or 'close' not in df.columns:
        return f"{prefix}_empty"
    
    close_vals = df['close'].values
    # Échantillon représentatif : 5 premières + 5 dernières valeurs
    sample_size = min(5, len(close_vals))
    sample = np.concatenate([close_vals[:sample_size], close_vals[-sample_size:]])
    data_hash = hashlib.md5(sample.tobytes()).hexdigest()[:16]
    
    # Paramètres triés et filtrés (ignorer None)
    param_items = sorted((k, v) for k, v in params.items() if v is not None)
    param_str = "_".join(f"{k}{v}" for k, v in param_items)
    
    return f"{prefix}_{len(df)}_{data_hash}_{param_str}"

def universal_calculate_indicators(
    df: pd.DataFrame,
    ema1_period: int,
    ema2_period: int,
    stoch_period: int = 14,
    sma_long: Optional[int] = None,
    adx_period: Optional[int] = None,
    trix_length: Optional[int] = None,
    trix_signal: Optional[int] = None
) -> pd.DataFrame:
    """
    Universal indicator calculation for live trading and backtest.
    Uses Cython-accelerated calculation if available, else falls back to Python.
    Mirrors the logic of backtest_from_dataframe for indicator calculation.
    """
    try:
        if df.empty or len(df) < 10:
            return pd.DataFrame()

        # Use Cython if available
        if CYTHON_BACKTEST_AVAILABLE and hasattr(backtest_engine, 'calculate_indicators_fast'):
            try:
                # Prepare all required columns
                df_work = df.copy()
                df_work['ema1'] = df_work['close'].ewm(span=ema1_period, adjust=False).mean()
                df_work['ema2'] = df_work['close'].ewm(span=ema2_period, adjust=False).mean()
                if 'rsi' not in df_work.columns:
                    df_work['rsi'] = ta.momentum.RSIIndicator(df_work['close'], window=14).rsi()
                def compute_stochrsi(rsi_series: pd.Series, period: int = 14) -> pd.Series:
                    min_rsi = rsi_series.rolling(window=period, min_periods=period).min()
                    max_rsi = rsi_series.rolling(window=period, min_periods=period).max()
                    stochrsi = (rsi_series - min_rsi) / (max_rsi - min_rsi)
                    stochrsi = stochrsi.clip(lower=0, upper=1)
                    stochrsi = stochrsi.fillna(0)
                    return stochrsi
                df_work['stoch_rsi'] = compute_stochrsi(df_work['rsi'], period=stoch_period)
                df_work['atr'] = ta.volatility.AverageTrueRange(
                    high=df_work.get('high', df_work['close']),
                    low=df_work.get('low', df_work['close']),
                    close=df_work['close'],
                    window=14
                ).average_true_range()
                if sma_long:
                    df_work['sma_long'] = df_work['close'].rolling(window=sma_long).mean()
                if adx_period and len(df_work) >= adx_period + 2:
                    try:
                        df_work['adx'] = ta.trend.ADXIndicator(high=df_work['high'], low=df_work['low'], close=df_work['close'], window=adx_period).adx()
                    except Exception:
                        df_work['adx'] = np.nan
                if trix_length and trix_signal:
                    ema1 = df_work['close'].ewm(span=trix_length, adjust=False).mean()
                    ema2 = ema1.ewm(span=trix_length, adjust=False).mean()
                    ema3 = ema2.ewm(span=trix_length, adjust=False).mean()
                    df_work['TRIX_PCT'] = ema3.pct_change() * 100
                    df_work['TRIX_SIGNAL'] = df_work['TRIX_PCT'].rolling(window=trix_signal).mean()
                    df_work['TRIX_HISTO'] = df_work['TRIX_PCT'] - df_work['TRIX_SIGNAL']
                # Call Cython-accelerated calculation (returns DataFrame)
                result = backtest_engine.calculate_indicators_fast(
                    df_work['close'].values.astype(np.float64),
                    df_work['high'].values.astype(np.float64),
                    df_work['low'].values.astype(np.float64),
                    ema1_period,
                    ema2_period,
                    stoch_period,
                    sma_long if sma_long else 0,
                    adx_period if adx_period else 0,
                    trix_length if trix_length else 0,
                    trix_signal if trix_signal else 0
                )
                # The Cython function should return a DataFrame or dict of arrays; adapt as needed
                # If not a DataFrame, reconstruct it
                if isinstance(result, pd.DataFrame):
                    return result
                elif isinstance(result, dict):
                    # Assume dict of arrays, reconstruct DataFrame with same index as df_work
                    return pd.DataFrame(result, index=df_work.index)
                else:
                    logger.warning("Cython calculate_indicators_fast returned unexpected type, using Python fallback.")
            except Exception as e:
                logger.warning(f"Cython indicator calculation failed, using Python fallback: {e}")
                # Fallback to Python below

        # Fallback: use the robust Python implementation
        return calculate_indicators(
            df,
            ema1_period,
            ema2_period,
            stoch_period=stoch_period,
            sma_long=sma_long,
            adx_period=adx_period,
            trix_length=trix_length,
            trix_signal=trix_signal
        )
    except Exception as e:
        logger.error(f"Erreur universal_calculate_indicators: {e}", exc_info=True)
        return pd.DataFrame()


def calculate_indicators(
    df: pd.DataFrame, 
    ema1_period: int, 
    ema2_period: int, 
    stoch_period: int = 14,
    sma_long: Optional[int] = None, 
    adx_period: Optional[int] = None, 
    trix_length: Optional[int] = None, 
    trix_signal: Optional[int] = None
) -> pd.DataFrame:
    """
    Calcule les indicateurs techniques avec cache robuste et optimisation mémoire.
    Toutes les opérations sont vectorisées (pandas/numpy), aucune boucle Python.
    """
    try:
        if df.empty or 'close' not in df.columns:
            raise KeyError("DataFrame vide ou colonne 'close' absente")
        
        # Préparer les paramètres pour le cache
        params = {
            'ema1': ema1_period, 
            'ema2': ema2_period, 
            'stoch': stoch_period,
            'sma': sma_long, 
            'adx': adx_period, 
            'trix_len': trix_length, 
            'trix_sig': trix_signal
        }
        cache_key = get_cache_key("indicators", df, params)
        
        # Lecture thread-safe du cache
        try:
            if cache_key in indicators_cache:
                cached_df = indicators_cache[cache_key]
                if len(cached_df) == len(df.dropna(subset=['close'])):  # ou juste len(df) si tu veux matcher l'entrée brute
                    logger.debug("Indicateurs chargés depuis le cache mémoire")
                    return cached_df.copy()
        except (KeyError, AttributeError, TypeError):
            pass  # Cache corrompu ou indisponible -> recalcul

        # Copie de travail
        df_work = df.copy()
        
        # Nettoyage minimal des NaN sur 'close'
        df_work['close'] = df_work['close'].ffill().bfill()
        if df_work['close'].isna().any():
            logger.warning("Données 'close' entièrement NaN après nettoyage")
            return pd.DataFrame()

        # --- RSI (seulement si absent) ---
        if 'rsi' not in df_work.columns:
            df_work['rsi'] = ta.momentum.RSIIndicator(df_work['close'], window=14).rsi()

        # --- EMA ---
        # IMPORTANT: utiliser adjust=False pour correspondre aux calculs Binance (methode recursive/online)
        df_work['ema1'] = df_work['close'].ewm(span=ema1_period, adjust=False).mean()
        df_work['ema2'] = df_work['close'].ewm(span=ema2_period, adjust=False).mean()

        # --- Stochastic RSI (vectorized robust calculation) ---
        def compute_stochrsi(rsi_series: pd.Series, period: int) -> pd.Series:
            min_rsi = rsi_series.rolling(window=period, min_periods=period).min()
            max_rsi = rsi_series.rolling(window=period, min_periods=period).max()
            stochrsi = (rsi_series - min_rsi) / (max_rsi - min_rsi)
            stochrsi = stochrsi.clip(lower=0, upper=1)
            stochrsi = stochrsi.fillna(0)
            return stochrsi
        if 'rsi' in df_work.columns:
            df_work['stoch_rsi'] = compute_stochrsi(df_work['rsi'], period=stoch_period)

        # --- ATR ---
        df_work['atr'] = ta.volatility.AverageTrueRange(
            high=df_work.get('high', df_work['close']),
            low=df_work.get('low', df_work['close']),
            close=df_work['close'],
            window=config.atr_period
        ).average_true_range()

        # --- SMA long ---
        if sma_long:
            df_work['sma_long'] = df_work['close'].rolling(window=sma_long).mean()

        # --- ADX (utiliser l'implémentation standard Wilder via ta.trend.ADXIndicator) ---
        if adx_period and len(df_work) >= adx_period + 2:
            try:
                df_work['adx'] = ta.trend.ADXIndicator(high=df_work['high'], low=df_work['low'], close=df_work['close'], window=adx_period).adx()
            except Exception:
                df_work['adx'] = np.nan

        # --- TRIX ---
        if trix_length and trix_signal:
            ema1 = df_work['close'].ewm(span=trix_length, adjust=False).mean()
            ema2 = ema1.ewm(span=trix_length, adjust=False).mean()
            ema3 = ema2.ewm(span=trix_length, adjust=False).mean()
            df_work['TRIX_PCT'] = ema3.pct_change() * 100
            df_work['TRIX_SIGNAL'] = df_work['TRIX_PCT'].rolling(window=trix_signal).mean()
            df_work['TRIX_HISTO'] = df_work['TRIX_PCT'] - df_work['TRIX_SIGNAL']

        # --- Nettoyage final ---
        # Supprimer NaN uniquement des colonnes essentielles (pas stoch_rsi qui a des 0 valides)
        df_work.dropna(subset=['close', 'rsi', 'atr'], inplace=True)

        # --- Mise en cache (taille limitée à 30 pour meilleure couverture) ---
        try:
            if len(indicators_cache) < 30:
                indicators_cache[cache_key] = df_work.copy()
                logger.debug(f"Indicateurs mis en cache: {cache_key[:30]}...")
        except (MemoryError, KeyError) as e:
            logger.debug(f"Erreur mise en cache: {e}")

        logger.debug(f"Indicateurs calculés: {len(df_work)} lignes")
        return df_work

    except Exception as e:
        logger.error(f"Erreur calcul indicateurs: {e}", exc_info=True)
        try:
            email_body = f"""
=== ERREUR DE CALCUL DES INDICATEURS ===

Echec lors du calcul des indicateurs techniques necessaires au trading.

DETAILS DE L'INCIDENT:
----------------------
Horodatage          : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Erreur rencontree   : {str(e)[:150]}{'...' if len(str(e)) > 150 else ''}
Module affecte      : Calcul des indicateurs

IMPACT CRITIQUE:
----------------
Le bot ne peut pas analyser les signaux de trading.
Toutes les decisions d'achat/vente sont compromises.
Le trading automatique est suspendu.

ACTIONS URGENTES:
-----------------
1. Verifier l'integrite des donnees de marche
2. Controler les parametres des indicateurs
3. Examiner les logs detailles du bot
4. Redemarrer le bot si necessaire

--- Message automatique du Bot de Trading Crypto ---
            """
            # Ajout du solde global Binance à l'email
            try:
                account_info = client.get_account()
                tickers = get_all_tickers_cached(client)
                global_balance_usdc = 0.0
                for bal in account_info['balances']:
                    asset = bal['asset']
                    free = float(bal['free'])
                    locked = float(bal['locked'])
                    total = free + locked
                    if total < 1e-8:
                        continue
                    if asset == 'USDC':
                        global_balance_usdc += total
                    else:
                        symbol1 = asset + 'USDC'
                        symbol2 = 'USDC' + asset
                        price = None
                        if symbol1 in tickers:
                            price = tickers[symbol1]
                            global_balance_usdc += total * price
                        elif symbol2 in tickers and tickers[symbol2] > 0:
                            price = 1.0 / tickers[symbol2]
                            global_balance_usdc += total * price
                # SPOT balance will be injected by send_trading_alert_email
            except Exception:
                pass
            send_trading_alert_email(
                subject="[BOT CRYPTO] ERREUR CRITIQUE Indicateurs",
                body_main=email_body,
                client=client
            )
        except:
            pass
        return pd.DataFrame()

###############################################################
#                                                             #
#              *** BACKTEST DE LA STRATEGIE ***               #
#                                                             #
###############################################################


def prepare_base_dataframe(pair: str, timeframe: str, start_date: str, stoch_period: int = 14) -> Optional[pd.DataFrame]:
    """Prépare un DataFrame avec tous les indicateurs de base partagés par tous les scénarios."""
    df = fetch_historical_data(pair, timeframe, start_date)
    if df.empty:
        return None

    # Calculer TOUS les EMA possibles (adjust=False pour correspondre Binance - methode recursive/online)
    for period in [14, 25, 26, 45, 50]:
        df[f'ema_{period}'] = df['close'].ewm(span=period, adjust=False).mean()

    # Indicateurs communs à tous les scénarios
    df['rsi'] = ta.momentum.RSIIndicator(df['close'], window=14).rsi()

    df['atr'] = ta.volatility.AverageTrueRange(
        high=df['high'], low=df['low'], close=df['close'], window=config.atr_period
    ).average_true_range()
    # Stochastic RSI (Raw calculation - no smoothing)
    def compute_stochrsi(rsi_series: pd.Series, period: int = 14) -> pd.Series:
        # Méthode optimisée et robuste pour StochRSI
        rsi_np = rsi_series.to_numpy()
        min_rsi = pd.Series(rsi_np).rolling(window=period, min_periods=period).min().to_numpy()
        max_rsi = pd.Series(rsi_np).rolling(window=period, min_periods=period).max().to_numpy()
        # Calcul vectorisé, évite les divisions par zéro
        denom = max_rsi - min_rsi
        with np.errstate(divide='ignore', invalid='ignore'):
            stochrsi = np.where(denom != 0, (rsi_np - min_rsi) / denom, 0)
        stochrsi = np.clip(stochrsi, 0, 1)
        stochrsi = np.nan_to_num(stochrsi, nan=0)
        return pd.Series(stochrsi, index=rsi_series.index)
    df['stoch_rsi'] = compute_stochrsi(df['rsi'], period=stoch_period)

    # Supprimer NaN uniquement des colonnes essentielles
    df.dropna(subset=['close', 'rsi', 'atr'], inplace=True)
    return df

def backtest_from_dataframe(
    df: pd.DataFrame,
    ema1_period: int,
    ema2_period: int,
    sma_long: Optional[int] = None,
    adx_period: Optional[int] = None,
    trix_length: Optional[int] = None,
    trix_signal: Optional[int] = None,
    sizing_mode: str = 'baseline'  # 'baseline' (100% capital) or 'risk' (risk-based sizing)
) -> Dict[str, Any]:
    """Backtest à partir d'un DataFrame préparé.
    
    Utilise l'implémentation Cython accélérée si disponible (30-50x plus rapide),
    sinon utilise la version Python comme fallback.
    """
    try:
        if df.empty or len(df) < 50:
            return {'final_wallet': 0.0, 'trades': pd.DataFrame(), 'max_drawdown': 0.0, 'win_rate': 0.0}

        # === OPTIMISATION CYTHON : Appeler le moteur optimisé si disponible ===
        if CYTHON_BACKTEST_AVAILABLE:
            try:
                # Préparer les données pour le backtest Cython
                df_work = df.copy()
                df_work['ema1'] = df_work['close'].ewm(span=ema1_period, adjust=False).mean()
                df_work['ema2'] = df_work['close'].ewm(span=ema2_period, adjust=False).mean()
                # StochRSI déjà calculé dans prepare_base_dataframe
                # Pas besoin de recalculer ici
                # Ajouter indicateurs spécifiques
                if sma_long:
                    df_work['sma_long'] = df_work['close'].rolling(window=sma_long).mean()
                
                if adx_period:
                    if 'adx' in df.columns:
                        df_work['adx'] = df['adx'].values
                    else:
                        # Utiliser ta.trend (optimisé et testé)
                        df_work['adx'] = ta.trend.ADXIndicator(
                            high=df_work['high'],
                            low=df_work['low'],
                            close=df_work['close'],
                            window=adx_period
                        ).adx()
                
                if trix_length and trix_signal:
                    ema1 = df_work['close'].ewm(span=trix_length, adjust=False).mean()
                    ema2 = ema1.ewm(span=trix_length, adjust=False).mean()
                    ema3 = ema2.ewm(span=trix_length, adjust=False).mean()
                    df_work['TRIX_PCT'] = ema3.pct_change() * 100
                    df_work['TRIX_SIGNAL'] = df_work['TRIX_PCT'].rolling(window=trix_signal).mean()
                    df_work['TRIX_HISTO'] = df_work['TRIX_PCT'] - df_work['TRIX_SIGNAL']
                
                # Appeler le moteur Cython optimisé
                result = backtest_engine.backtest_from_dataframe_fast(
                    df_work['close'].values.astype(np.float64),
                    df_work['high'].values.astype(np.float64),
                    df_work['low'].values.astype(np.float64),
                    df_work['ema1'].values.astype(np.float64),
                    df_work['ema2'].values.astype(np.float64),
                    df_work['stoch_rsi'].values.astype(np.float64),
                    df_work['atr'].values.astype(np.float64),
                    df_work['sma_long'].values.astype(np.float64) if sma_long and 'sma_long' in df_work.columns else None,
                    df_work['adx'].values.astype(np.float64) if adx_period and 'adx' in df_work.columns else None,
                    df_work['TRIX_HISTO'].values.astype(np.float64) if trix_length and 'TRIX_HISTO' in df_work.columns else None,
                    config.initial_wallet,  # initial_wallet
                    'StochRSI',  # scenario
                    sma_long is not None,  # use_sma
                    adx_period is not None,  # use_adx
                    trix_length is not None  # use_trix
                )
                
                # Convertir le résultat Cython en format compatible
                return {
                    'final_wallet': result['final_wallet'],
                    'trades': pd.DataFrame(result['trades']) if result['trades'] else pd.DataFrame(),
                    'max_drawdown': result['max_drawdown'],
                    'win_rate': result['win_rate']
                }
            
            except Exception as e:
                logger.warning(f" Cython backtest failed, using Python fallback: {e}")
                import traceback
                traceback.print_exc()
                # Continuer avec la version Python comme fallback
        
        # === VERSION PYTHON (fallback ou si Cython n'est pas disponible) ===
        df_work = df.copy()
        df_work['ema1'] = df_work[f'ema_{ema1_period}']
        df_work['ema2'] = df_work[f'ema_{ema2_period}']
        
        # StochRSI déjà calculé dans prepare_base_dataframe avec manual loop (méthode optimale +$895)
        # Pas besoin de recalculer ici

        # Ajouter indicateurs spécifiques
        if sma_long:
            df_work['sma_long'] = df_work['close'].rolling(window=sma_long).mean()
        
        if adx_period and 'adx' in df.columns:
            df_work['adx'] = df['adx'].values
        elif adx_period:
            close = df_work['close'].values
            high = df_work['high'].values
            low = df_work['low'].values
            high_low = high - low
            high_close = np.abs(high[1:] - close[:-1])
            low_close = np.abs(low[1:] - close[:-1])
            tr = np.empty_like(high)
            tr[0] = high_low[0]
            tr[1:] = np.maximum(high_low[1:], np.maximum(high_close, low_close))
            plus_dm = np.zeros_like(high)
            minus_dm = np.zeros_like(high)
            high_diff = np.diff(high)
            low_diff = -np.diff(low)
            plus_dm[1:] = np.where((high_diff > low_diff) & (high_diff > 0), high_diff, 0)
            minus_dm[1:] = np.where((low_diff > high_diff) & (low_diff > 0), low_diff, 0)
            kernel = np.ones(adx_period) / adx_period
            tr_smooth = np.convolve(tr, kernel, mode='same')
            plus_dm_smooth = np.convolve(plus_dm, kernel, mode='same')
            minus_dm_smooth = np.convolve(minus_dm, kernel, mode='same')
            plus_di = np.divide(plus_dm_smooth, tr_smooth, out=np.zeros_like(tr_smooth), where=tr_smooth != 0) * 100
            minus_di = np.divide(minus_dm_smooth, tr_smooth, out=np.zeros_like(tr_smooth), where=tr_smooth != 0) * 100
            di_sum = plus_di + minus_di
            dx = np.divide(np.abs(plus_di - minus_di), di_sum, out=np.zeros_like(di_sum), where=di_sum != 0) * 100
            adx = np.convolve(dx, kernel, mode='same')
            df_work['adx'] = adx

        if trix_length and trix_signal:
            ema1 = df_work['close'].ewm(span=trix_length, adjust=False).mean()
            ema2 = ema1.ewm(span=trix_length, adjust=False).mean()
            ema3 = ema2.ewm(span=trix_length, adjust=False).mean()
            df_work['TRIX_PCT'] = ema3.pct_change() * 100
            df_work['TRIX_SIGNAL'] = df_work['TRIX_PCT'].rolling(window=trix_signal).mean()
            df_work['TRIX_HISTO'] = df_work['TRIX_PCT'] - df_work['TRIX_SIGNAL']

        # Logique de backtest (version Python)
        # Note: stoch_period déjà utilisé dans prepare_base_dataframe pour calculer StochRSI
        usd = config.initial_wallet
        coin = 0.0
        trades_history = []
        in_position = False
        entry_price = 0.0  # Tracked for position analysis (logged at entry/exit)
        entry_usd_invested = 0.0  # Track USD invested at entry
        max_price = 0.0
        trailing_stop = 0.0
        trailing_stop_activated = False
        stop_loss = 0.0
        max_drawdown = 0.0
        peak_wallet = config.initial_wallet
        winning_trades = 0
        total_trades = 0

        # OPTIMISATION: Pré-extraire numpy arrays UNIQUEMENT pour fallback Python
        ema_cross_up = df_work['ema1'] > df_work['ema2']
        ema_cross_down = df_work['ema2'] > df_work['ema1']
        stoch_low = (df_work['stoch_rsi'] < 0.8) & (df_work['stoch_rsi'] > 0.05)
        stoch_high = df_work['stoch_rsi'] > 0.2

        ema_cross_up_vals = ema_cross_up.values
        ema_cross_down_vals = ema_cross_down.values
        stoch_low_vals = stoch_low.values
        stoch_high_vals = stoch_high.values
        close_vals = df_work['close'].values
        atr_vals = df_work['atr'].values
        indices = df_work.index.values

        # Correction: utiliser la dernière bougie fermée pour le signal d'achat
        for i in range(len(df_work)):
            # Pour le signal d'achat, on utilise la dernière bougie fermée (i-1)
            idx_signal = i-1 if i > 0 else i
            row_close = close_vals[i]
            row_atr = atr_vals[i]
            index = indices[i]
            symbol = config.symbol if hasattr(config, 'symbol') else None
            # Diagnostic: afficher la valeur StochRSI utilisée pour le signal d'achat
            if i == len(df_work) - 1:
                stochrsi_used = df_work['stoch_rsi'].values[idx_signal]
                print(f"[DIAGNOSTIC] StochRSI utilisé pour le signal d'achat (bougie fermée): {stochrsi_used:.4f}")
            if in_position:
                # Utiliser uniquement les valeurs fixées à l'entrée tant que le trailing n'est pas activé
                if not trailing_stop_activated:
                    # stop_loss et trailing_activation_price restent fixes
                    stop_loss = stop_loss_at_entry
                    # trailing_activation_price doit toujours être stocké dans pair_state pour cohérence affichage/logique
                    trailing_activation_price = trailing_activation_price_at_entry
                    # On ne garde pas de variable locale inutile : la valeur d'affichage et de logique doit toujours venir de pair_state['trailing_activation_price']
                    trailing_distance = config.atr_multiplier * atr_at_entry
                else:
                    # Après activation, trailing_distance devient dynamique
                    trailing_distance = config.atr_multiplier * row_atr
                    # Activer le trailing stop uniquement si le prix a progressé d'au moins la distance (calculé à l'entrée)
                # Activation du trailing stop (une seule fois)
                if not trailing_stop_activated and row_close >= trailing_activation_price_at_entry:
                    trailing_stop_activated = True
                    max_price = row_close
                    trailing_stop = max_price - trailing_distance
                    stop_loss = trailing_stop
                    # Envoi d'une alerte mail une seule fois lors de l'activation du trailing stop
                    try:
                        send_trading_alert_email(
                            subject=f"[BOT CRYPTO] Trailing Stop ACTIVÉ sur {symbol}",
                            body_main=f"Le trailing stop vient d'être activé sur {symbol} à {row_close:.4f} USDC. Distance: {trailing_distance:.4f} USDC.",
                            client=client
                        )
                    except Exception as mail_exc:
                        logger.error(f"[EXCEPTION] Echec envoi mail activation trailing stop: {mail_exc}")
                # Mettre à jour le trailing stop uniquement si activé et nouveau plus haut
                if trailing_stop_activated and row_close > max_price:
                    max_price = row_close
                    trailing_stop = max_price - trailing_distance
                # Sécurité : le stop_loss ne doit jamais être supérieur au trailing_stop si ce dernier existe
                if trailing_stop_activated and trailing_stop is not None:
                    stop_loss = min(stop_loss, trailing_stop)

                # Calculate current wallet value and drawdown
                current_wallet = usd + (coin * row_close)

                # Update peak BEFORE calculating drawdown
                if current_wallet > peak_wallet:
                    peak_wallet = current_wallet

                # Calculate drawdown from peak
                drawdown = (peak_wallet - current_wallet) / peak_wallet if peak_wallet > 0 else 0.0
                max_drawdown = max(max_drawdown, drawdown)

                # Check exit conditions
                exit_trade = False
                motif_sortie = None
                # Harmonisation : sortie si <= stop_loss ou (<= trailing_stop et trailing activé) ou signal stratégie
                # Harmonisation : sortie uniquement si <= stop_loss ou (<= trailing_stop et trailing activé)
                if row_close <= stop_loss:
                            exit_trade = True
                            motif_sortie = 'STOP_LOSS'
                elif trailing_stop_activated and row_close <= trailing_stop:
                            exit_trade = True
                            motif_sortie = 'TRAILING_STOP'

                if exit_trade:
                    # Exécution de l'ordre selon le motif
                    client_id = _generate_client_order_id('sell')
                    if motif_sortie == 'STOP_LOSS':
                        place_stop_loss_order(symbol=symbol, quantity=float(coin), stop_price=float(stop_loss), client_id=client_id)
                    elif motif_sortie == 'TRAILING_STOP':
                        trailing_delta = int(row_atr * 100)
                        place_trailing_stop_order(symbol=symbol, quantity=float(coin), activation_price=float(row_close), trailing_delta=trailing_delta, client_id=client_id)
                    else:
                        safe_market_sell(symbol=symbol, quantity=float(coin))
                    # Envoi d'une alerte mail à chaque clôture de position
                    try:
                        send_trading_alert_email(
                            subject=f"[BOT CRYPTO] Clôture exécutée sur {symbol}",
                            body_main=f"Clôture exécutée sur {symbol} pour {coin} à {row_close:.4f} USDC. Motif: {motif_sortie}",
                            client=client
                        )
                    except Exception as mail_exc:
                        logger.error(f"[EXCEPTION] Echec envoi mail clôture: {mail_exc}")

                    # Calcul proceeds local (pour le backtest)
                    gross_proceeds = coin * row_close
                    fee = gross_proceeds * config.taker_fee
                    usd = gross_proceeds - fee
                    coin = 0.0
                    
                    # CORRECT profit calculation: final USD - initial USD invested
                    trade_profit = usd - entry_usd_invested
                    
                    if trade_profit > 0:
                        winning_trades += 1
                    total_trades += 1
                    
                    trades_history.append({
                        'date': index, 
                        'type': 'sell', 
                        'price': row_close, 
                        'profit': trade_profit,
                        'motif': motif_sortie,
                        'stop_loss': f'{stop_loss:.4f} USDC',
                        'trailing_stop': f'{trailing_stop:.4f} USDC'
                    })
                    
                    in_position = False
                    entry_price = 0.0  # Reset après vente
                    logger.debug(f"Position fermée à {row_close:.4f}, profit: {trade_profit:.2f}, motif: {motif_sortie}")
                    entry_usd_invested = 0.0
                    max_price = 0.0
                    trailing_stop = 0.0
                    stop_loss = 0.0
                    trailing_stop_activated = False
                    continue


            # ...diagnostic RSI/StochRSI retiré...
            # Condition d'achat: suffisant de capital
            buy_condition = ema_cross_up_vals[idx_signal] and stoch_low_vals[idx_signal] and usd > 0
            if sma_long and 'sma_long' in df_work.columns:
                buy_condition &= (row_close > df_work['sma_long'].values[idx_signal])
            if adx_period and 'adx' in df_work.columns:
                # FILTRE ADX: 25 (standard)
                buy_condition &= (df_work['adx'].values[idx_signal] > 25)
            if trix_length and 'TRIX_HISTO' in df_work.columns:
                buy_condition &= (df_work['TRIX_HISTO'].values[idx_signal] > 0)

            # --- PATCH: Fix ATR at entry for stop loss and trailing activation ---
            # These variables will be set at order open and used throughout the position
            atr_at_entry = None
            stop_loss_at_entry = None
            trailing_activation_price_at_entry = None

            if buy_condition and not in_position:
                # OPTIMISATION: Pas de sniper en backtest Python (trop lent, déjà dans Cython)
                optimized_price = row_close
                # Position sizing: support for 3 methods
                if sizing_mode == 'baseline':
                    # 100% du capital par trade (ancien comportement)
                    gross_coin = usd / optimized_price if optimized_price > 0 else 0.0
                elif sizing_mode == 'risk':
                    # ATR-based risk sizing
                    try:
                        equity = usd  # Use current USD balance as equity for backtest
                        qty_by_risk = compute_position_size_by_risk(equity=equity, atr_value=row_atr, entry_price=optimized_price)
                        max_affordable = usd / optimized_price if optimized_price > 0 else 0.0
                        gross_coin = min(max_affordable, qty_by_risk)
                    except Exception:
                        gross_coin = 0.0
                elif sizing_mode == 'fixed_notional':
                    # Fixed notional (fixed USD per trade)
                    try:
                        notional_per_trade = getattr(config, 'notional_per_trade_usd', None) or (config.initial_wallet * 0.10)
                        qty_fixed = compute_position_size_fixed_notional(equity=usd, notional_per_trade_usd=notional_per_trade, entry_price=optimized_price)
                        max_affordable = usd / optimized_price if optimized_price > 0 else 0.0
                        gross_coin = min(max_affordable, qty_fixed)
                    except Exception:
                        gross_coin = 0.0
                elif sizing_mode == 'volatility_parity':
                    # Volatility parity sizing
                    try:
                        target_vol = getattr(config, 'target_volatility_pct', 0.02)
                        qty_vol = compute_position_size_volatility_parity(equity=usd, atr_value=row_atr, entry_price=optimized_price, target_volatility_pct=target_vol)
                        max_affordable = usd / optimized_price if optimized_price > 0 else 0.0
                        gross_coin = min(max_affordable, qty_vol)
                    except Exception:
                        gross_coin = 0.0
                else:
                    # Default to baseline
                    gross_coin = usd / optimized_price if optimized_price > 0 else 0.0

                if gross_coin and gross_coin > 0:
                    # Calculate entry with fees
                    fee_in_coin = gross_coin * config.taker_fee
                    coin = gross_coin - fee_in_coin

                    # Track USD invested (before fees)
                    entry_usd_invested = usd
                    usd = 0.0

                    # Set entry parameters
                    entry_price = optimized_price
                    logger.debug(f"Position ouverte: entry={entry_price:.4f}, stop_loss={optimized_price - (3 * row_atr):.4f}")
                    max_price = optimized_price
                    # --- PATCH: Fix ATR at entry for stop loss and trailing activation ---
                    atr_at_entry = row_atr
                    stop_loss_at_entry = optimized_price - (3 * atr_at_entry)
                    trailing_activation_price_at_entry = optimized_price + (config.atr_multiplier * atr_at_entry)
                    stop_loss = stop_loss_at_entry
                    trailing_stop = optimized_price - (config.atr_multiplier * atr_at_entry)
                    # Initialiser le prix d'activation du trailing stop (fixe, pour affichage et logique)
                    if 'pair_state' in locals():
                        pair_state['trailing_activation_price'] = trailing_activation_price_at_entry
                    trailing_stop_activated = False
                    trades_history.append({'date': index, 'type': 'buy', 'price': optimized_price})
                    in_position = True
                    # Envoi d'une alerte mail lors du placement du stop loss à l'ouverture
                    try:
                        send_trading_alert_email(
                            subject=f"[BOT CRYPTO] STOP LOSS placé à l'ouverture sur {symbol}",
                            body_main=f"Un ordre STOP LOSS a été placé à l'ouverture sur {symbol} pour {coin} à {stop_loss:.4f} USDC.",
                            client=client
                        )
                    except Exception as mail_exc:
                        logger.error(f"[EXCEPTION] Echec envoi mail STOP LOSS ouverture: {mail_exc}")
                else:
                    # Impossible d'ouvrir une position (pas de taille calculable)
                    pass

        # Final wallet calculation
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
        final_wallet = usd + (coin * df_work['close'].iloc[-1]) if in_position else usd

        return {
            'final_wallet': final_wallet,
            'trades': pd.DataFrame(trades_history),
            'max_drawdown': max_drawdown,
            'win_rate': win_rate
        }

    except Exception as e:
        logger.error(f"Erreur dans backtest_from_dataframe: {e}", exc_info=True)
        return {'final_wallet': 0.0, 'trades': pd.DataFrame(), 'max_drawdown': 0.0, 'win_rate': 0.0}

def empty_result_dict(timeframe: str, ema1: int, ema2: int, scenario_name: str) -> Dict[str, Any]:
    """Retourne un résultat de backtest vide en cas d'erreur."""
    return {
        'timeframe': timeframe,
        'ema_periods': (ema1, ema2),
        'scenario': scenario_name,
        'initial_wallet': config.initial_wallet,
        'final_wallet': 0.0,
        'trades': pd.DataFrame(),
        'max_drawdown': 0.0,
        'win_rate': 0.0
    }

def run_single_backtest_optimized(args: Tuple) -> Dict[str, Any]:
    """Execute un backtest unique à partir d'un DataFrame préparé.

    args tuple layout: (timeframe, ema1, ema2, scenario, base_df, pair_symbol, sizing_mode)
    """
    # Backwards-compatible unpacking (older tasks may not include sizing_mode)
    if len(args) == 6:
        (timeframe, ema1, ema2, scenario, base_df, pair_symbol) = args
        sizing_mode = 'baseline'
    else:
        (timeframe, ema1, ema2, scenario, base_df, pair_symbol, sizing_mode) = args
    try:
        # Stocker temporairement le nom de la paire pour l'optimisation sniper
        global _current_backtest_pair
        _current_backtest_pair = pair_symbol
        
        result = backtest_from_dataframe(
            df=base_df,
            ema1_period=ema1,
            ema2_period=ema2,
            sma_long=scenario['params'].get('sma_long'),
            adx_period=scenario['params'].get('adx_period'),
            trix_length=scenario['params'].get('trix_length'),
            trix_signal=scenario['params'].get('trix_signal'),
            sizing_mode=sizing_mode
        )
        return {
            'timeframe': timeframe,
            'ema_periods': (ema1, ema2),
            'scenario': scenario['name'],
            'initial_wallet': config.initial_wallet,
            'final_wallet': result['final_wallet'],
            'trades': result['trades'],
            'max_drawdown': result['max_drawdown'],
            'win_rate': result['win_rate']
        }
    except Exception as e:
        logger.error(f"Erreur backtest parallele: {e}")
        return {
            'timeframe': timeframe,
            'ema_periods': (ema1, ema2),
            'scenario': scenario['name'],
            'initial_wallet': config.initial_wallet,
            'final_wallet': 0.0,
            'trades': pd.DataFrame(),
            'max_drawdown': 0.0,
            'win_rate': 0.0
        }
    finally:
        # Nettoyer la variable globale
        _current_backtest_pair = None

def run_all_backtests(backtest_pair: str, start_date: str, timeframes: List[str], sizing_mode: str = 'baseline') -> List[Dict[str, Any]]:
    """Execute tous les backtests en parallele (version optimisée)."""
    results = []

    # Préparer 1 DataFrame par timeframe
    base_dataframes = {}
    # Utilise la valeur par défaut 14 pour stoch_period (ou personnaliser selon le contexte)
    for tf in timeframes:
        df = prepare_base_dataframe(backtest_pair, tf, start_date, 14)
        base_dataframes[tf] = df if df is not None and not df.empty else pd.DataFrame()

    ema_periods = [(14, 26), (25, 45), (26, 50)]
    scenarios = [
        {'name': 'StochRSI', 'params': {'stoch_period': 14}},
        {'name': 'StochRSI_SMA', 'params': {'stoch_period': 14, 'sma_long': 200}},
        {'name': 'StochRSI_ADX', 'params': {'stoch_period': 14, 'adx_period': 14}},
        {'name': 'StochRSI_TRIX', 'params': {'stoch_period': 14, 'trix_length': 7, 'trix_signal': 15}},
    ]

    tasks = []
    for ema1, ema2 in ema_periods:
        for scenario in scenarios:
            for timeframe in timeframes:
                base_df = base_dataframes[timeframe]
                if base_df.empty:
                    continue
                # include sizing_mode in task tuple so workers can use risk-based sizing when requested
                tasks.append((timeframe, ema1, ema2, scenario, base_df, backtest_pair, sizing_mode))

    with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
        future_to_task = {executor.submit(run_single_backtest_optimized, task): task for task in tasks}
        with tqdm(
            total=len(tasks), 
            desc="[BACKTESTS]", 
            colour="green",
            bar_format="{desc}: {percentage:3.0f}%|█{bar:30}█| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]"
        ) as pbar:
            for future in as_completed(future_to_task):
                try:
                    result = future.result()
                    results.append(result)
                    pbar.update(1)
                except Exception as e:
                    logger.error(f"Erreur future: {e}")
                    pbar.update(1)

    return results

def run_parallel_backtests(crypto_pairs: List[Dict], start_date: str, timeframes: List[str], sizing_mode: str = 'baseline') -> List[Dict]:
    """Exécute les backtests en parallèle et retourne les résultats bruts."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    max_workers = min(len(crypto_pairs), 5)

    results_by_pair = {}
    
    # Calculer le nombre total de tâches pour la barre de progression globale
    ema_periods = [(14, 26), (25, 45), (26, 50)]
    scenarios = [
        {'name': 'StochRSI', 'params': {'stoch_period': 14}},
        {'name': 'StochRSI_SMA', 'params': {'stoch_period': 14, 'sma_long': 200}},
        {'name': 'StochRSI_ADX', 'params': {'stoch_period': 14, 'adx_period': 14}},
        {'name': 'StochRSI_TRIX', 'params': {'stoch_period': 14, 'trix_length': 7, 'trix_signal': 15}},
    ]
    total_tasks = len(crypto_pairs) * len(ema_periods) * len(scenarios) * len(timeframes)
    
    # Affichage unique et propre
    console.print(f"\n[bold cyan]Lancement de {total_tasks} backtests avec optimisation sniper...[/bold cyan]")

    def run_single_pair(pair_info):
        try:
            # Exécuter le backtest SANS affichage
            results = run_all_backtests(
                pair_info["backtest_pair"], 
                start_date, 
                timeframes,
                sizing_mode=sizing_mode
            )
            return pair_info["backtest_pair"], pair_info["real_pair"], results
        except Exception as e:
            logger.error(f"Erreur backtest {pair_info['backtest_pair']}: {e}")
            return pair_info["backtest_pair"], pair_info["real_pair"], []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_pair = {executor.submit(run_single_pair, pair): pair for pair in crypto_pairs}
        for future in as_completed(future_to_pair):
            try:
                backtest_pair, real_pair, results = future.result()
                results_by_pair[backtest_pair] = {
                    'real_pair': real_pair,
                    'results': results
                }
            except Exception as e:
                logger.error(f"Erreur future: {e}")

    return results_by_pair  # ← retourne un dict structuré

def display_results_for_pair(backtest_pair: str, real_pair: str, results: List[Dict]):
    """Affiche les résultats d'une paire de façon claire et organisée."""
    if not results:
        console.print(f"[red]Aucun résultat pour {backtest_pair}[/red]")
        return

    # Calculer les dates dynamiquement pour affichage
    today = datetime.today()
    start_date_obj = today - timedelta(days=config.backtest_days)
    end_date_str = today.strftime("%d %B %Y")
    start_date_str = start_date_obj.strftime("%d %B %Y")

    # Trouver le meilleur résultat
    best_result = max(results, key=lambda x: x['final_wallet'] - x['initial_wallet'])
    best_profit = best_result['final_wallet'] - best_result['initial_wallet']

    # Créer le tableau
    table = Table(
        title=f"[bold cyan]Résultats du Backtest — {backtest_pair}[/bold cyan]",
        title_style="bold magenta",
        show_header=True,
        header_style="bold green",
        border_style="bright_yellow",
        expand=False,
        width=120
    )
    table.add_column("Timeframe", style="dim", justify="center", width=10)
    table.add_column("EMA", style="cyan", justify="center", width=15)
    table.add_column("Scénario", style="magenta", justify="left", width=20)
    table.add_column("Profit ($)", style="bold green", justify="right", width=15)
    table.add_column("Final Wallet ($)", style="bold yellow", justify="right", width=18)
    table.add_column("Trades", style="white", justify="right", width=8)
    table.add_column("Max Drawdown (%)", style="bold red", justify="right", width=18)
    table.add_column("Win Rate (%)", style="bold cyan", justify="right", width=15)

    for result in results:
        profit = result['final_wallet'] - result['initial_wallet']
        profit_color = "bold green" if profit > 0 else "bold red"
        final_wallet_color = "bold green" if result['final_wallet'] > result['initial_wallet'] else "bold red"

        table.add_row(
            f"[cyan]{result['timeframe']}[/cyan]",
            f"[blue]{result['ema_periods'][0]} / {result['ema_periods'][1]}[/blue]",
            f"[magenta]{result['scenario']}[/magenta]",
            f"[bold white]${result['initial_wallet']:.2f}[/bold white]",
            f"[{final_wallet_color}]${result['final_wallet']:.2f}[/{final_wallet_color}]",
            f"[{profit_color}]${profit:.2f}[/{profit_color}]",
            f"[bold white]{len(result['trades'])}[/bold white]",
            f"[bold red]{result['max_drawdown']*100:.2f}%[/bold red]",
            f"[bold cyan]{result['win_rate']:.2f}%[/bold cyan]"
        )

    best_title = (
        f"[bold green]Best Backtest Configuration {backtest_pair}:[/]\n\n"
        f"Timeframe: [cyan bold]{best_result['timeframe']}[/cyan bold]\n"
        f"EMA: [cyan bold]{best_result['ema_periods'][0]} / {best_result['ema_periods'][1]}[/cyan bold]\n"
        f"Scenario: [magenta]{best_result['scenario']}[/magenta]\n"
        f"Profit: [bold green]${best_profit:.2f}[/bold green]\n"
        f"Final Wallet: [bold yellow]${best_result['final_wallet']:.2f}[/bold yellow]\n"
        f"Max Drawdown: [bold red]{best_result['max_drawdown']*100:.2f}%[/bold red]\n"
        f"Win Rate: [bold cyan]{best_result['win_rate']:.2f}%[/bold cyan]"
    )

    console.print(Panel(best_title, title="[bold yellow]Best Result[/bold yellow]", title_align="center", expand=False))
    console.print(table)

    # Execution des ordres reels avec les meilleurs parametres
    scenario_params = {
        'StochRSI': {'stoch_period': 14},
        'StochRSI_SMA': {'stoch_period': 14, 'sma_long': 200},
        'StochRSI_ADX': {'stoch_period': 14, 'adx_period': 14},
        'StochRSI_TRIX': {'stoch_period': 14, 'trix_length': 7, 'trix_signal': 15}
    }
    
    best_params = {
        'timeframe': best_result['timeframe'],
        'ema1_period': best_result['ema_periods'][0],
        'ema2_period': best_result['ema_periods'][1],
        'scenario': best_result['scenario'],
    }
    best_params.update(scenario_params.get(best_result['scenario'], {}))

    # Mise a jour de l'etat du bot
    pair_state['last_best_params'] = best_params
    pair_state['execution_count'] += 1
    save_bot_state()

    # Affichage soigne pour la section trading en temps reel
    console.print("\n")
    
    # Panel principal avec style elegant
    trading_content = (
        f"[dim]Execution automatisee des ordres de trading[/dim]\n\n"
        f"[bold white]Paire de trading    :[/bold white] [bright_yellow]{real_trading_pair}[/bright_yellow]\n"
        f"[bold white]Intervalle temporel :[/bold white] [bright_cyan]{best_params['timeframe']}[/bright_cyan]\n"
        f"[bold white]Configuration EMA   :[/bold white] [bright_green]{best_params['ema1_period']}[/bright_green] / [bright_green]{best_params['ema2_period']}[/bright_green]\n"
        f"[bold white]Indicateur principal:[/bold white] [bright_magenta]{best_params['scenario']}[/bright_magenta]"
    )
    
    console.print(Panel(
        trading_content,
        title="[bold bright_cyan]TRADING ALGORITHMIQUE EN TEMPS REEL[/bold bright_cyan]",
        title_align="center",
        border_style="bright_cyan",
        padding=(1, 3),
        width=120
    ))

    # Partie live trading (exécution des ordres et planification) uniquement si RUN_LIVE_TRADING
    if globals().get('RUN_LIVE_TRADING', False):
        try:
            # SOLUTION ULTRA-ROBUSTE TIMESTAMP
            logger.info("=== INITIALISATION TIMESTAMP ULTRA-ROBUSTE ===")
            if not init_timestamp_solution():
                logger.error("IMPOSSIBLE D'INITIALISER LA SYNCHRONISATION")
            # Re-synchroniser avant chaque session de trading
            client._sync_server_time()
            logger.info("Synchronisation timestamp pre-trading terminee")
            # Validation de la connexion API au demarrage
            if not validate_api_connection():
                logger.error("Impossible de valider la connexion API. Arret du programme.")
                exit(1)
            # Chargement de l'etat du bot
            load_bot_state()
            logger.info("Script demarre. Planification initiale en cours...")
            # Purge préventive: supprimer toute planification résiduelle
            try:
                schedule.clear()
                logger.info("Planification nettoyee au demarrage (schedule.clear())")
            except Exception as _clear_ex:
                logger.debug(f"Echec nettoyage planification au demarrage: {_clear_ex}")
            # Vérification qu'aucun job 30-min n'est déjà présent
            for job in list(schedule.jobs):
                if job.interval == 30 and job.unit == 'minutes':
                    schedule.cancel_job(job)
                    logger.info("Job 30-min existant supprimé avant nouvelle planification.")
            # Planification du nettoyage du cache tous les 30 jours
            schedule.every(30).days.do(cleanup_expired_cache)
            logger.info("Nettoyage automatique du cache planifié: tous les 30 jours")
            # --- Suite du code live trading (affichage, exécution, planification, boucle) ---
            # ...existing code (affichage résultats, exécution trading, planification, boucle principale)...
        except KeyboardInterrupt:
            logger.info("Execution interrompue par l'utilisateur. Arret du script.")
            save_bot_state()
        except Exception as e:
            error_msg = f"Erreur inattendue au démarrage : {e}"
            logger.error(error_msg)
            # Alerter par email en cas d'erreur critique au démarrage
            try:
                email_body = f"Erreur critique au démarrage du bot:\n\n{str(e)}\n\nTraceback:\n{traceback.format_exc()[:500]}"
                send_email_alert(
                    subject="[BOT CRYPTO] ARRET CRITIQUE",
                    body=email_body
                )
            except:
                pass
            save_bot_state()

        # === AFFICHAGE PROPRE, SANS CHEVAUCHEMENT ===
        from rich.table import Table
        from rich.panel import Panel

        for backtest_pair, data in all_results.items():
            if not data['results']:
                console.print(f"[red]Aucun résultat pour {backtest_pair}[/red]")
                continue

            # Trouver le meilleur résultat
            best_result = max(data['results'], key=lambda x: x['final_wallet'] - x['initial_wallet'])
            best_profit = best_result['final_wallet'] - best_result['initial_wallet']

            # Créer le tableau
            table = Table(
                title=f"[bold cyan]Résultats du Backtest — {backtest_pair}[/bold cyan]",
                title_style="bold magenta",
                show_header=True,
                header_style="bold green",
                border_style="bright_yellow",
                expand=False,
                width=120
            )
            table.add_column("Timeframe", style="dim", justify="center", width=10)
            table.add_column("EMA", style="cyan", justify="center", width=15)
            table.add_column("Scénario", style="magenta", justify="left", width=20)
            table.add_column("Profit ($)", style="bold green", justify="right", width=15)
            table.add_column("Final Wallet ($)", style="bold yellow", justify="right", width=18)
            table.add_column("Trades", style="white", justify="right", width=8)
            table.add_column("Max Drawdown (%)", style="bold red", justify="right", width=18)
            table.add_column("Win Rate (%)", style="bold cyan", justify="right", width=15)

            for result in data['results']:
                profit = result['final_wallet'] - result['initial_wallet']
                profit_color = "bold green" if profit > 0 else "bold red"
                table.add_row(
                    f"[cyan]{result['timeframe']}[/cyan]",
                    f"[blue]{result['ema_periods'][0]} / {result['ema_periods'][1]}[/blue]",
                    f"[magenta]{result['scenario']}[/magenta]",
                    f"[bold white]${result['initial_wallet']:.2f}[/bold white]",
                    f"[{final_wallet_color}]${result['final_wallet']:.2f}[/{final_wallet_color}]",
                    f"[{profit_color}]${profit:.2f}[/{profit_color}]",
                    f"[bold white]{len(result['trades'])}[/bold white]",
                    f"[bold red]{result['max_drawdown']*100:.2f}%[/bold red]",
                    f"[bold cyan]{result['win_rate']:.2f}%[/bold cyan]"
                )

            # Calculer les dates dynamiquement pour affichage
            today = datetime.today()
            start_date_obj = today - timedelta(days=config.backtest_days)
            end_date_str = today.strftime("%d %b %Y")
            start_date_str = start_date_obj.strftime("%d %b %Y")

            # Résumé du meilleur
            best_panel = Panel(
                f"[bold cyan]Backtest Period[/bold cyan]: 5 years ({start_date_str} - {end_date_str})\n"
                f"[bold green]Meilleur scénario[/bold green]: [magenta]{best_result['scenario']}[/magenta]\n"
                f"[bold cyan]Timeframe[/bold cyan]: {best_result['timeframe']}\n"
                f"[bold blue]EMA[/bold blue]: {best_result['ema_periods'][0]} / {best_result['ema_periods'][1]}[/bold blue]\n"
                f"[bold yellow]Profit[/bold yellow]: ${best_profit:,.2f}\n"
                f"[bold red]Max Drawdown[/bold red]: {best_result['max_drawdown']*100:.2f}%\n"
                f"[bold cyan]Win Rate[/bold cyan]: {best_result['win_rate']:.2f}%",
                title=f"[bold green]*** MEILLEUR RESULTAT — {backtest_pair} ***[/bold green]",
                border_style="green",
                padding=(1, 2)
            )

            console.print("\n")
            console.print(best_panel)
            console.print(table)
            console.print("\n" + "="*120 + "\n")
            
            # Afficher le panel de trading en temps réel
            trading_content = (
                f"[dim]Execution automatisee des ordres de trading[/dim]\n\n"
                f"[bold white]Paire de trading    :[/bold white] [bright_yellow]{data['real_pair']}[/bright_yellow]\n"
                f"[bold white]Intervalle temporel :[/bold white] [bright_cyan]{best_result['timeframe']}[/bright_cyan]\n"
                f"[bold white]Configuration EMA   :[/bold white] [bright_green]{best_result['ema_periods'][0]}[/bright_green] / [bright_green]{best_result['ema_periods'][1]}[/bright_green]\n"
                f"[bold white]Indicateur principal:[/bold white] [bright_magenta]{best_result['scenario']}[/bright_magenta]"
            )
            
            console.print(Panel(
                trading_content,
                title="[bold bright_cyan]TRADING ALGORITHMIQUE EN TEMPS REEL[/bold bright_cyan]",
                title_align="center",
                border_style="bright_cyan",
                padding=(1, 3),
                width=120
            ))

            # === Exécuter le trading réel avec les meilleurs paramètres ===
            scenario_params = {
                'StochRSI': {'stoch_period': 14},
                'StochRSI_SMA': {'stoch_period': 14, 'sma_long': 200},
                'StochRSI_ADX': {'stoch_period': 14, 'adx_period': 14},
                'StochRSI_TRIX': {'stoch_period': 14, 'trix_length': 7, 'trix_signal': 15}
            }
            
            best_params = {
                'timeframe': best_result['timeframe'],
                'ema1_period': best_result['ema_periods'][0],
                'ema2_period': best_result['ema_periods'][1],
                'scenario': best_result['scenario'],
            }
            best_params.update(scenario_params.get(best_result['scenario'], {}))
            
            # Initialiser l'état du bot pour cette paire
            if backtest_pair not in bot_state:
                bot_state[backtest_pair] = {
                    'last_run_time': None,
                    'last_best_params': None,
                    'execution_count': 0,
                    'entry_price': None,
                    'max_price': None,
                    'trailing_stop': None,
                    'stop_loss': None,
                    'last_execution': None
                }
            
            pair_state = bot_state[backtest_pair]
            
            try:
                execute_real_trades(data['real_pair'], best_result['timeframe'], best_params, backtest_pair)
            except Exception as e:
                logger.error(f"Erreur trading réel {backtest_pair}: {e}")
                email_body = f"""
=== ERREUR D'EXECUTION DU TRADING ===

Une erreur s'est produite lors de l'execution du trading en temps reel.

DETAILS DE L'INCIDENT:
----------------------
Paire concernee     : {backtest_pair}
Horodatage          : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Erreur rencontree   : {str(e)[:150]}{'...' if len(str(e)) > 150 else ''}

IMPACT SUR LE BOT:
------------------
L'execution automatique du trading est interrompue.
La surveillance manuelle est recommandee.

ACTIONS RECOMMANDEES:
---------------------
1. Examiner les logs detailles du bot
2. Verifier la connexion API Binance
3. Controler les soldes disponibles
4. Redemarrer le module de trading si necessaire

TRACE COMPLETE:
---------------
{traceback.format_exc()[:500]}{'...' if len(traceback.format_exc()) > 500 else ''}

--- Message automatique du Bot de Trading Crypto ---
                """
                send_email_alert(
                    subject=f"[BOT CRYPTO] ERREUR Trading - {backtest_pair}",
                    body=email_body
                )
            
            # === AFFICHAGE DATE/HEURE ET PLANIFICATION ===
            current_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # Historique d'exécution
            if pair_state.get('last_run_time'):
                try:
                    last_time_obj = datetime.strptime(pair_state['last_run_time'], "%Y-%m-%d %H:%M:%S")
                    current_time_obj = datetime.strptime(current_run_time, "%Y-%m-%d %H:%M:%S")
                    time_elapsed = current_time_obj - last_time_obj
                    
                    history_section = (
                        f"[bold white]Derniere execution  :[/bold white] [bright_cyan]{pair_state['last_run_time']}[/bright_cyan]\n"
                        f"[bold white]Temps ecoule        :[/bold white] [bright_yellow]{time_elapsed}[/bright_yellow]"
                    )
                except Exception:
                    history_section = f"[bold white]Derniere execution  :[/bold white] [bright_cyan]{pair_state['last_run_time']}[/bright_cyan]"
            else:
                history_section = "[bold bright_green]Premiere execution du systeme de trading automatise[/bold bright_green]"
            
            pair_state['last_run_time'] = current_run_time
            pair_state['last_best_params'] = best_params
            pair_state['execution_count'] = pair_state.get('execution_count', 0) + 1
            
            # Planification UNIQUE homogène toutes les 2 minutes (indépendant du timeframe)
            # CRITICAL: Capture best_params with default argument to avoid late-binding closure issue
            # IMPORTANT: start_date will be calculated dynamically in execute_scheduled_trading
            schedule.every(2).minutes.do(
                lambda bp=backtest_pair, rp=data['real_pair'], tf=best_result['timeframe'], params=dict(best_params): execute_scheduled_trading(rp, tf, params, bp)
            )
            next_exec = "toutes les 2 minutes (720 exécutions/jour)"
            logger.info(f"Planification homogène 30-min activée pour {backtest_pair} - Réactivité optimale")
            
            schedule_section = (
                f"[bold white]Mode de planification:[/bold white] [dim]Homogène et réactif - 2 minutes[/dim]\n"
                f"[bold white]Prochaine exécution :[/bold white] [bright_green]{next_exec}[/bright_green]"
            )
            
            # Afficher le panel de suivi
            tracking_panel = Panel(
                f"{history_section}\n\n{schedule_section}",
                title="[bold bright_blue]SUIVI D'EXECUTION & PLANIFICATION AUTOMATIQUE[/bold bright_blue]",
                border_style="bright_blue",
                padding=(1, 3),
                width=120
            )
            
            console.print(tracking_panel)
            console.print("\n")
            
            save_bot_state()

        logger.info(f"Tâches planifiées actives: {len(schedule.jobs)}")
        
        # === BOUCLE PRINCIPALE ===
        console.print("\n" + "="*120)
        console.print(f"[bold green][OK] BOT ACTIF - Surveillance 24/7 demarree a {datetime.now().strftime('%H:%M:%S')}[/bold green]")
        console.print(f"[bold cyan][INFO] {len(schedule.jobs)} tache(s) planifiee(s) - Prochaine execution: {schedule.next_run().strftime('%H:%M:%S') if schedule.next_run() else 'N/A'}[/bold cyan]")
        console.print(f"[dim]Le bot execute les verifications de trading toutes les 2 minutes automatiquement[/dim]")
        console.print("="*120 + "\n")
        
        logger.info("Bot actif - Surveillance des signaux de trading...")
        logger.info("Initialisation du gestionnaire d'erreurs...")
        error_handler = initialize_error_handler({
            'smtp_server': config.smtp_server,
            'smtp_port': str(config.smtp_port),
            'sender_email': config.sender_email,
            'sender_password': config.smtp_password,
            'recipient_email': config.receiver_email
        })
        # Correction : accès aux attributs fictifs
        logger.info(f"Gestionnaire d'erreurs actif - Mode: {getattr(error_handler, 'mode', 'default')}")
        
        # Initialisation du compteur de vérification réseau
        network_check_counter = 0
        try:
            running_counter = 0
            while True:
                try:
                    # Check circuit breaker status
                    if not error_handler.circuit_breaker.is_available():
                        logger.critical(f"[CIRCUIT] Bot en mode pause - Circuit ouvert. Prochaine tentative: {error_handler.circuit_breaker.timeout_seconds}s")
                        time.sleep(10)
                        continue

                    # Execute scheduled tasks with error handling
                    try:
                        schedule.run_pending()
                    except Exception as e:
                        should_continue, _ = error_handler.handle_error(
                            error=e,
                            context="schedule.run_pending()",
                            critical=False
                        )
                        if not should_continue:
                            logger.warning("[CIRCUIT] Skipping task execution due to circuit breaker")
                            time.sleep(10)
                            continue

                    # Vérification réseau toutes les 5 minutes
                    network_check_counter += 1
                    if network_check_counter >= 5:  # 5 cycles = 5 minutes
                        if not check_network_connectivity():
                            logger.warning("Connectivité réseau perdue...")
                            time.sleep(30)
                            continue
                        network_check_counter = 0

                    # Affichage du temps restant avant la prochaine exécution
                    now = datetime.now()
                    next_run = schedule.next_run()
                    if next_run:
                        delta = next_run - now
                        minutes_left = max(0, int(delta.total_seconds() // 60))
                        print(f"[TIME] {now.strftime('%H:%M:%S')} - Bot actif (RUNNING) | Prochaine execution dans {minutes_left} min ({next_run.strftime('%H:%M:%S')})")
                    else:
                        print(f"[TIME] {now.strftime('%H:%M:%S')} - Bot actif (RUNNING) | Prochaine execution non planifiée")

                    running_counter += 1
                    if running_counter % 1 == 0:  # Toutes les 10 minutes (600s sleep)
                        print(f"[RUNNING] {now.strftime('%H:%M:%S')} - running en cours")

                    time.sleep(120)

                except Exception as e:
                    # Use error handler to manage main loop exceptions
                    should_continue, _ = error_handler.handle_error(
                        error=e,
                        context="main_loop",
                        critical=True
                    )

                    if not should_continue:
                        logger.critical(f"[CIRCUIT] Main loop paused due to circuit breaker. Waiting {error_handler.circuit_breaker.timeout_seconds}s before retry")
                        time.sleep(error_handler.circuit_breaker.timeout_seconds)
                    else:
                        logger.warning(f"[MAIN_LOOP] Continuing despite error - circuit still available")
                        time.sleep(30)
        except KeyboardInterrupt:
            logger.info("Execution interrompue par l'utilisateur. Arret du script.")
            save_bot_state()
        except Exception as e:
            error_msg = f"Erreur inattendue au démarrage : {e}"
            logger.error(error_msg)
            # Alerter par email en cas d'erreur critique au démarrage
            try:
                email_body = f"Erreur critique au démarrage du bot:\n\n{str(e)}\n\nTraceback:\n{traceback.format_exc()[:500]}"
                send_email_alert(
                    subject="[BOT CRYPTO] ARRET CRITIQUE",
                    body=email_body
                )
            except:
                pass
            save_bot_state()

        except KeyboardInterrupt:
            logger.info("Execution interrompue par l'utilisateur. Arret du script.")
            save_bot_state()
        except Exception as e:
            error_msg = f"Erreur inattendue au démarrage : {e}"
            logger.error(error_msg)
        # Alerter par email en cas d'erreur critique au démarrage
        try:
            email_body = f"Erreur critique au démarrage du bot:\n\n{str(e)}\n\nTraceback:\n{traceback.format_exc()[:500]}"
            send_email_alert(
                subject="[BOT CRYPTO] ARRET CRITIQUE",
                body=email_body
            )
        except:
            pass
        save_bot_state()