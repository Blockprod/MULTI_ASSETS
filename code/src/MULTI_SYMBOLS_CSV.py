import pandas as pd


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
        self.mode = "default"  # Attribut fictif pour compatibilité

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
        locale.setlocale(locale.LC_ALL, "")
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
    def from_env(cls) -> "Config":
        """Charge la configuration depuis les variables d'environnement."""
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass
        required_vars = {
            "api_key": "BINANCE_API_KEY",
            "secret_key": "BINANCE_SECRET_KEY",
            "sender_email": "SENDER_EMAIL",
            "receiver_email": "RECEIVER_EMAIL",
            "smtp_password": "GOOGLE_MAIL_PASSWORD",
        }
        config_data = {}
        for key, env_var in required_vars.items():
            value = os.getenv(env_var)
            if not value:
                raise ValueError(f"Variable d'environnement manquante: {env_var}")
            config_data[key] = value
        # Optionnels
        config_data["taker_fee"] = float(os.getenv("TAKER_FEE", "0.0007"))
        config_data["api_timeout"] = int(os.getenv("API_TIMEOUT", "30"))
        config_data["max_workers"] = int(os.getenv("MAX_WORKERS", "4"))
        config_data["initial_wallet"] = float(os.getenv("INITIAL_WALLET", "10000.0"))
        config_data["backtest_days"] = int(os.getenv("BACKTEST_DAYS", "1825"))
        config_data["cache_dir"] = os.getenv("CACHE_DIR", "cache")
        config_data["states_dir"] = os.getenv("STATES_DIR", "states")
        config_data["state_file"] = os.getenv("STATE_FILE", "bot_state.pkl")
        config_data["atr_period"] = int(os.getenv("ATR_PERIOD", "14"))
        config_data["atr_multiplier"] = float(os.getenv("ATR_MULTIPLIER", "5.0"))
        config_data["atr_stop_multiplier"] = float(
            os.getenv("ATR_STOP_MULTIPLIER", "3.0")
        )
        config_data["risk_per_trade"] = float(os.getenv("RISK_PER_TRADE", "0.01"))
        config_data["smtp_server"] = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        config_data["smtp_port"] = int(os.getenv("SMTP_PORT", "587"))
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
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def display_buy_signal_panel(
    row, usdc_balance, best_params, scenario, buy_condition, console, buy_reason=None
):
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
    # Récupérer la devise de cotation dynamiquement si possible
    # Récupération dynamique du coin_symbol et quote_currency
    global pair_state
    try:
        coin_symbol = pair_state.get("coin_symbol", None)
        quote_currency = pair_state.get("quote_currency", None)
        # Si non trouvés, extraire depuis le nom de la paire
        if not coin_symbol or not quote_currency:
            real_trading_pair = pair_state.get("real_trading_pair", None)
            if real_trading_pair:
                coin_symbol, quote_currency = extract_coin_from_pair(real_trading_pair)
            else:
                quote_currency = "USDC"
                coin_symbol = "COIN"
    except Exception:
        quote_currency = "USDC"
        coin_symbol = "COIN"
    buy_lines = [
        f"[bold white]Analyse des conditions d'achat :[/bold white]\n\n",
        f"[bold white]EMA1 > EMA2          :[/bold white] {'[green]OK[/green]' if row['ema1'] > row['ema2'] else '[red]NOK[/red]'} [dim](EMA1={row['ema1']:.2f}, EMA2={row['ema2']:.2f})[/dim]",
        f"[bold white]StochRSI < 80%       :[/bold white] {'[green]OK[/green]' if row['stoch_rsi'] < 0.8 else '[red]NOK[/red]'} [dim]({row['stoch_rsi']*100:.1f}%)[/dim]",
        f"[bold white]Solde {quote_currency} > 0       :[/bold white] {'[green]OK[/green]' if usdc_balance > 0 else '[red]NOK[/red]'} [dim]({usdc_balance:.2f} {quote_currency})[/dim]",
    ]
    if scenario == "StochRSI_ADX":
        adx_threshold = best_params.get("adx_threshold", 20)
        adx_val = row.get("adx", None)
        adx_status = (
            "[green]OK[/green]"
            if (adx_val is not None and adx_val > adx_threshold)
            else "[red]NOK[/red]"
        )
        buy_lines.append(
            f"[bold white]ADX > {adx_threshold}             :[/bold white] {adx_status} [dim](ADX={adx_val if adx_val else 'N/A'})[/dim]"
        )
    if scenario == "StochRSI_SMA":
        sma_long_val = best_params.get("sma_long", 200)
        sma_status = (
            "[green]OK[/green]"
            if row.get("close", 0) > row.get("sma_long", float("nan"))
            else "[red]NOK[/red]"
        )
        buy_lines.append(
            f"[bold white]Prix > SMA{sma_long_val}        :[/bold white] {sma_status} [dim](Prix={row.get('close', 0):.2f}, SMA={row.get('sma_long', 0):.2f})[/dim]"
        )
    if scenario == "StochRSI_TRIX":
        trix_status = (
            "[green]OK[/green]" if row.get("TRIX_HISTO", -999) > 0 else "[red]NOK[/red]"
        )
        buy_lines.append(
            f"[bold white]TRIX_HISTO > 0       :[/bold white] {trix_status} [dim](TRIX={row.get('TRIX_HISTO', 0):.4f})[/dim]"
        )
    if buy_reason:
        buy_lines.append(f"\n[dim][italic]{buy_reason}[/italic][/dim]")
    buy_content = "\n".join(buy_lines)
    panel_title = (
        "[bold green]SIGNAL D'ACHAT - CONDITIONS REMPLIES[/bold green]"
        if buy_condition
        else "[bold yellow]SIGNAL D'ACHAT - CONDITIONS NON REMPLIES[/bold yellow]"
    )
    panel_color = "green" if buy_condition else "yellow"
    try:
        console.print(
            Panel(
                buy_content,
                title=panel_title,
                border_style=panel_color,
                padding=(1, 2),
                width=120,
            )
        )
    except UnicodeEncodeError as e:
        logger.error(f"[ENCODING ERROR] display_buy_signal_panel: {e}")


_last_sell_panel_hash = None


def display_sell_signal_panel(
    row,
    coin_balance,
    pair_state,
    sell_triggered,
    console,
    coin_symbol,
    sell_reason=None,
    trailing_activation_price=None,
):
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
    # Récupération dynamique du coin_symbol et quote_currency si besoin
    try:
        if not coin_symbol:
            coin_symbol = pair_state.get("coin_symbol", None)
            if not coin_symbol:
                real_trading_pair = pair_state.get("real_trading_pair", None)
                if real_trading_pair:
                    coin_symbol, _ = extract_coin_from_pair(real_trading_pair)
                else:
                    coin_symbol = "COIN"
    except Exception:
        coin_symbol = "COIN"
    sell_lines = [
        f"[bold white]Analyse des conditions de vente :[/bold white]\n\n",
        f"[bold white]EMA2 > EMA1          :[/bold white] {'[green]OK[/green]' if row['ema2'] > row['ema1'] else '[red]NOK[/red]'}",
        f"[bold white]StochRSI > 20%       :[/bold white] {'[green]OK[/green]' if row['stoch_rsi'] > 0.2 else '[red]NOK[/red]'}",
        f"[bold white]Solde {coin_symbol} > 0        :[/bold white] {'[green]OK[/green]' if coin_balance > 0 else '[red]NOK[/red]'}",
    ]
    # Affichage explicite de la raison de vente si elle est fournie
    if sell_reason is not None:
        reason_map = {
            "SIGNAL": "[bold magenta]Raison de la vente : Signal de stratégie[/bold magenta]",
            "STOP-LOSS": "[bold red]Raison de la vente : Stop-Loss[/bold red]",
            "TRAILING-STOP": "[bold cyan]Raison de la vente : Trailing-Stop[/bold cyan]",
        }
        sell_lines.append("")
        sell_lines.append(
            reason_map.get(
                sell_reason,
                f"[bold yellow]Raison de la vente : {sell_reason}[/bold yellow]",
            )
        )
    # Affichage STRICTEMENT des valeurs fixées à l'entrée (jamais recalculées)
    stop_loss_at_entry = pair_state.get("stop_loss_at_entry", None)
    trailing_activation_price_at_entry = pair_state.get(
        "trailing_activation_price_at_entry", None
    )
    trailing_stop_activated = pair_state.get("trailing_stop_activated", False)
    # Toujours utiliser le prix spot de la paire courante pour cohérence PnL/trailing
    try:
        pair_symbol = f"{coin_symbol}{pair_state.get('quote_currency', 'USDC')}"
        current_price = float(pair_state.get("ticker_spot_price", None))
        # Si ticker_spot_price absent ou incohérent, on tente de le récupérer dynamiquement
        if current_price is None or current_price <= 0:
            current_price = float(row.get("close", None))
    except Exception:
        current_price = row.get("close", None)

    # Ajout affichage PnL en cours
    entry_price = pair_state.get("entry_price", None)
    pnl_value = None
    if (
        entry_price is not None
        and current_price is not None
        and coin_balance is not None
    ):
        try:
            pnl_value = (current_price - entry_price) * coin_balance
        except Exception:
            pnl_value = None
    # Prix d'activation du trailing stop : TOUJOURS la valeur stockée à l'entrée
    if trailing_activation_price_at_entry is not None:
        trailing_activation_val = f"{trailing_activation_price_at_entry:.6f} {pair_state.get('quote_currency', 'USDC')}"
    else:
        trailing_activation_val = "N/A"
        logger.warning(
            f"[PANEL] Prix activation trailing non défini (trailing_activation_price_at_entry={trailing_activation_price_at_entry})"
        )
    # Stop loss : TOUJOURS la valeur stockée à l'entrée tant que le trailing n'est pas activé
    if not trailing_stop_activated:
        if stop_loss_at_entry is not None:
            stop_loss_display = f"{stop_loss_at_entry:.6f} {pair_state.get('quote_currency', 'USDC')} (fixe à l'entrée)"
            stop_loss_nature = "[grey62](Stop-loss fixe à l'ouverture)[/grey62]"
        else:
            stop_loss_display = "N/A"
            stop_loss_nature = ""
    else:
        # Après activation du trailing, afficher la valeur dynamique
        max_price = pair_state.get("max_price", None)
        atr_multiplier = pair_state.get("atr_multiplier", 5.0)
        atr_at_entry = pair_state.get("atr_at_entry", None)
        if max_price is not None and atr_at_entry is not None:
            trailing_stop_level = max_price - atr_multiplier * atr_at_entry
            stop_loss_display = f"{trailing_stop_level:.6f} {pair_state.get('quote_currency', 'USDC')} (dynamique)"
            stop_loss_nature = "[cyan](Stop-loss dynamique : trailing)[/cyan]"
        else:
            stop_loss_display = "N/A"
            stop_loss_nature = ""
    # Message complémentaire si le prix d'activation du trailing stop est atteint ou dépassé
    trailing_message = ""
    if trailing_activation_price_at_entry is not None and current_price is not None:
        if current_price >= trailing_activation_price_at_entry:
            trailing_message = (
                f"[bold cyan]Trailing stop activé ! (Prix actuel : {current_price:.6f} {pair_state.get('quote_currency', 'USDC')})[/bold cyan]\n"
                "[bold cyan]Le stop loss est maintenant dynamique : il est mis à jour à chaque planification à 5×ATR sous le cours de l'actif.[/bold cyan]"
            )
    # Affichage
    sell_lines.append(
        f"[bold white]Stop-Loss affiché       :[/bold white] {stop_loss_display}"
    )
    if stop_loss_nature:
        sell_lines.append(stop_loss_nature)
    sell_lines.append(
        f"[bold white]Prix activation trailing: [/bold white] {trailing_activation_val}"
    )
    if pnl_value is not None:
        sell_lines.append(
            f"[bold white]PnL en cours            :[/bold white] [bold]{pnl_value:,.2f} {pair_state.get('quote_currency', 'USDC')}[/bold]"
        )
    if trailing_message:
        sell_lines.append(trailing_message)
    sell_content = "\n".join(sell_lines)
    panel_title = (
        "[bold red]SIGNAL DE VENTE - CONDITIONS REMPLIES[/bold red]"
        if sell_triggered
        else "[bold yellow]SIGNAL DE VENTE - CONDITIONS NON REMPLIES[/bold yellow]"
    )
    panel_color = "red" if sell_triggered else "yellow"
    try:
        console.print(
            Panel(
                sell_content,
                title=panel_title,
                border_style=panel_color,
                padding=(1, 2),
                width=120,
            )
        )
    except UnicodeEncodeError as e:
        logger.error(f"[ENCODING ERROR] display_sell_signal_panel: {e}")


_last_balance_panel_hash = None


def display_account_balances_panel(
    account_info,
    coin_symbol,
    quote_currency,
    client,
    console,
    last_buy_price=None,
    atr_at_entry=None,
):
    """
    Affiche le panel des soldes de trading (quote_currency, coin), le prix de la paire courante et le solde global converti dynamiquement dans la devise de cotation.
    La conversion utilise la route la plus adaptée (directe ou via intermédiaire comme BTC, ETH, BNB) selon les tickers disponibles.
    Args:
        account_info: Dictionnaire d'informations de compte Binance.
        coin_symbol: Symbole de la crypto principale.
        quote_currency: Symbole de la devise de cotation (ex: USDC, EUR, BUSD, ...).
        client: Client Binance API.
        console: Objet Rich Console pour affichage.
    """
    usdc_balance_obj = next(
        (b for b in account_info["balances"] if b["asset"] == "USDC"), None
    )
    usdc_balance = float(usdc_balance_obj["free"]) if usdc_balance_obj else 0.0

    coin_balance_obj = next(
        (b for b in account_info["balances"] if b["asset"] == coin_symbol), None
    )
    coin_balance = float(coin_balance_obj["free"]) if coin_balance_obj else 0.0

    # Récupérer dynamiquement le prix de la paire courante (ex: SOLUSDC)
    try:
        pair_symbol = f"{coin_symbol}{quote_currency}"
        spot_price = float(client.get_symbol_ticker(symbol=pair_symbol)["price"])
    except Exception:
        spot_price = None
    # Store spot price in pair_state for use in other panels
    global pair_state
    pair_state["quote_currency"] = quote_currency
    if (
        "ticker_spot_price" not in pair_state
        or pair_state["ticker_spot_price"] != spot_price
    ):
        pair_state["ticker_spot_price"] = spot_price

    tickers = get_all_tickers_cached(client)

    def convert_to_usdc(asset, amount):
        if amount < 1e-8 or asset == "":
            return 0.0
        if asset == quote_currency or asset == "BUSD":
            return amount
        symbol1 = asset + quote_currency
        symbol2 = quote_currency + asset
        if symbol1 in tickers and tickers[symbol1] > 0:
            return amount * tickers[symbol1]
        elif symbol2 in tickers and tickers[symbol2] > 0:
            return amount * (1.0 / tickers[symbol2])
        # Conversion via un intermédiaire (ex: BTC)
        # Cherche une route asset->X->quote_currency dynamiquement
        intermediates = ["BTC", "ETH", "BNB"]
        for inter in intermediates:
            if asset == inter or quote_currency == inter:
                continue
            symbol_a_inter = asset + inter
            symbol_inter_a = inter + asset
            symbol_inter_q = inter + quote_currency
            symbol_q_inter = quote_currency + inter
            via_inter = None
            if symbol_a_inter in tickers and tickers[symbol_a_inter] > 0:
                if symbol_inter_q in tickers and tickers[symbol_inter_q] > 0:
                    via_inter = tickers[symbol_a_inter] * tickers[symbol_inter_q]
                elif symbol_q_inter in tickers and tickers[symbol_q_inter] > 0:
                    via_inter = tickers[symbol_a_inter] * (
                        1.0 / tickers[symbol_q_inter]
                    )
            elif symbol_inter_a in tickers and tickers[symbol_inter_a] > 0:
                if symbol_inter_q in tickers and tickers[symbol_inter_q] > 0:
                    via_inter = (1.0 / tickers[symbol_inter_a]) * tickers[
                        symbol_inter_q
                    ]
                elif symbol_q_inter in tickers and tickers[symbol_q_inter] > 0:
                    via_inter = (1.0 / tickers[symbol_inter_a]) * (
                        1.0 / tickers[symbol_q_inter]
                    )
            if via_inter:
                return amount * via_inter
        return 0.0

    global_balance_usdc = 0.0
    for bal in account_info["balances"]:
        asset = bal["asset"]
        free = float(bal["free"])
        locked = float(bal["locked"])
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
    if spot_price is not None:
        balance_content += f"\n[bold white]Prix {coin_symbol}/{quote_currency} actuel     :[/bold white] [bright_magenta]{spot_price:.6f} {quote_currency}[/bright_magenta]"
    balance_content += f"\n[bold white]Solde global Binance     :[/bold white] [bold bright_green]{global_balance_usdc:.2f} USDC[/bold bright_green]"
    global _last_balance_panel_hash
    panel_hash = hash(balance_content)
    if _last_balance_panel_hash != panel_hash:
        try:
            console.print(
                Panel(
                    balance_content,
                    title="[bold green]SOLDES DE TRADING[/bold green]",
                    border_style="green",
                    padding=(1, 2),
                    width=120,
                )
            )
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
                        client=client,
                    )
                except Exception as mail_exc:
                    logger.error(f"[EXCEPTION] Echec envoi mail d'alerte: {mail_exc}")
                return default_return if default_return is not None else None

        return wrapper

    return decorator


@log_exceptions(default_return=False)
def send_trading_alert_email(
    subject: str, body_main: str, client, add_spot_balance: bool = True
) -> bool:
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
                    delay = base_delay * (2**attempt)
                    logger.warning(
                        f"Tentative {attempt + 1} echouee pour {func.__name__}: {e}. Retry dans {delay}s"
                    )
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
        msg["From"] = config.sender_email
        msg["To"] = config.receiver_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

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

_tickers_cache = {"data": None, "timestamp": 0}
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
        if _tickers_cache["data"] is not None and (
            now - _tickers_cache["timestamp"] < cache_ttl
        ):
            return _tickers_cache["data"]
        tickers = {t["symbol"]: float(t["price"]) for t in client.get_all_tickers()}
        _tickers_cache["data"] = tickers
        _tickers_cache["timestamp"] = now
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
                        client=client,
                    )
                except Exception as mail_exc:
                    logger.error(f"[EXCEPTION] Echec envoi mail d'alerte: {mail_exc}")
                    return default_return if default_return is not None else None

        return wrapper

    return decorator


def send_trading_alert_email(
    subject: str, body_main: str, client, add_spot_balance: bool = True
) -> bool:
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
@retry_with_backoff(max_retries=3, base_delay=2.0)
@log_exceptions(default_return=None)
def place_trailing_stop_order(
    symbol: str,
    quantity: float,
    activation_price: float,
    trailing_delta: int,
    client_id: str = None,
) -> dict:
    """
    Place un ordre TRAILING_STOP_MARKET sur Binance (spot).
    """
    timestamp = int(time.time() * 1000)
    params = {
        "symbol": symbol,
        "side": "SELL",
        "type": "TRAILING_STOP_MARKET",
        "quantity": float(quantity),
        "activationPrice": float(activation_price),
        "trailingDelta": int(trailing_delta),
        "timestamp": timestamp,
        "recvWindow": 10000,
    }
    if client_id:
        params["newClientOrderId"] = client_id
    # Signature
    query_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
    signature = hmac.new(
        client.api_secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    params["signature"] = str(signature)
    url = "https://api.binance.com/api/v3/order"
    headers = {"X-MBX-APIKEY": client.api_key}
    response = requests.post(url, data=params, headers=headers, timeout=30)
    if response.status_code != 200:
        try:
            error_data = response.json()
            error_code = error_data.get("code", "UNKNOWN")
            error_msg = error_data.get("msg", "Unknown error")
            # Envoi d'une alerte mail en cas d'erreur d'ordre
            send_trading_alert_email(
                subject="[BOT CRYPTO] ERREUR EXECUTION TRAILING STOP",
                body_main=f"Erreur lors de l'execution de l'ordre TRAILING STOP : {error_code} - {error_msg}\n\nParams : {params}",
                client=client,
            )
            raise Exception(f"Binance API error {error_code}: {error_msg}")
        except Exception:
            response.raise_for_status()
    result = response.json()
    # Envoi d'une alerte mail pour toute transaction exécutée
    send_trading_alert_email(
        subject="[BOT CRYPTO] TRAILING STOP EXECUTE",
        body_main=f"Ordre TRAILING STOP exécuté avec succès.\n\nParams : {params}\nRéponse : {result}",
        client=client,
    )
    return result


@retry_with_backoff(max_retries=3, base_delay=2.0)
@log_exceptions(default_return=None)
def place_stop_loss_order(
    symbol: str, quantity: float, stop_price: float, client_id: str = None
) -> dict:
    """
    Place un ordre STOP_LOSS sur Binance (spot).
    """
    timestamp = int(time.time() * 1000)
    params = {
        "symbol": symbol,
        "side": "SELL",
        "type": "STOP_LOSS",
        "quantity": float(quantity),
        "stopPrice": float(stop_price),
        "timestamp": timestamp,
        "recvWindow": 10000,
    }
    if client_id:
        params["newClientOrderId"] = client_id
    # Signature
    query_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
    signature = hmac.new(
        client.api_secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    params["signature"] = str(signature)
    url = "https://api.binance.com/api/v3/order"
    headers = {"X-MBX-APIKEY": client.api_key}
    response = requests.post(url, data=params, headers=headers, timeout=30)
    if response.status_code != 200:
        try:
            error_data = response.json()
            error_code = error_data.get("code", "UNKNOWN")
            error_msg = error_data.get("msg", "Unknown error")
            # Envoi d'une alerte mail en cas d'erreur d'ordre
            send_trading_alert_email(
                subject="[BOT CRYPTO] ERREUR EXECUTION STOP LOSS",
                body_main=f"Erreur lors de l'execution de l'ordre STOP LOSS : {error_code} - {error_msg}\n\nParams : {params}",
                client=client,
            )
            raise Exception(f"Binance API error {error_code}: {error_msg}")
        except Exception:
            response.raise_for_status()
    result = response.json()
    # Envoi d'une alerte mail pour toute transaction exécutée
    send_trading_alert_email(
        subject="[BOT CRYPTO] STOP LOSS EXECUTE",
        body_main=f"Ordre STOP LOSS exécuté avec succès.\n\nParams : {params}\nRéponse : {result}",
        client=client,
    )
    return result


# Import Cython modules for optimization (en-tête du fichier)
try:
    import backtest_engine_standard as backtest_engine  # type: ignore

    CYTHON_BACKTEST_AVAILABLE = True
except ImportError as e:
    CYTHON_BACKTEST_AVAILABLE = False
    print(
        f"Warning: Cython backtest_engine_standard not available ({e}), using Python fallback"
    )
    backtest_engine = None  # Set to None to avoid NameError later

logger = logging.getLogger(__name__)
console = Console()

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("trading_bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
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
        kwargs["requests_params"] = {"timeout": 45}
        super().__init__(api_key, api_secret, **kwargs)
        logger.info("Client Binance ULTRA ROBUSTE initialisé")
        self._perform_ultra_robust_sync()

    def _perform_ultra_robust_sync(self):
        """Synchronisation radicale avec compensation complète."""
        try:
            local_before = int(time.time() * 1000)
            server_time = self.get_server_time()["serverTime"]
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
        if current_time - self._last_sync > 60 or self._error_count > 0:
            self._perform_ultra_robust_sync()
        local_ts = int(current_time * 1000)
        safe_ts = local_ts + self._server_time_offset
        try:
            server_check = self.get_server_time()["serverTime"]
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
                    if "recvWindow" in kwargs:
                        logger.debug(
                            "_request: Removing caller-provided recvWindow from kwargs to avoid duplicate parameter"
                        )
                        kwargs.pop("recvWindow", None)

                    # If params or data dict contains recvWindow, remove it as well
                    if (
                        "params" in kwargs
                        and isinstance(kwargs["params"], dict)
                        and "recvWindow" in kwargs["params"]
                    ):
                        logger.debug(
                            "_request: Removing recvWindow from kwargs['params'] to avoid duplicate parameter"
                        )
                        kwargs["params"].pop("recvWindow", None)
                    if (
                        "data" in kwargs
                        and isinstance(kwargs["data"], dict)
                        and "recvWindow" in kwargs["data"]
                    ):
                        logger.debug(
                            "_request: Removing recvWindow from kwargs['data'] to avoid duplicate parameter"
                        )
                        kwargs["data"].pop("recvWindow", None)
                except Exception as _sanitize_ex:
                    logger.debug(
                        f"_request: recvWindow sanitation failed: {_sanitize_ex}"
                    )

                # Call parent with sanitized kwargs. Parent will add timestamp/signature and its own recvWindow.
                result = super()._request(
                    method, uri, signed, force_params=force_params, **kwargs
                )
                self._error_count = max(0, self._error_count - 1)
                return result

            except BinanceAPIException as e:
                # Handle timestamp -1021 specifically by resyncing
                if getattr(e, "code", None) == -1021:
                    self._error_count += 1
                    logger.warning(
                        f"Erreur -1021 détectée (tentative {attempt+1}), resync obligatoire"
                    )
                    self._perform_ultra_robust_sync()
                    if attempt < max_retries - 1:
                        time.sleep(2)
                        continue
                    raise
                else:
                    # For other errors, just log and raise
                    if getattr(e, "code", None) == -1101:
                        logger.error(
                            f"BinanceAPIException -1101 (Duplicate recvWindow): {e}"
                        )
                        logger.error(f"  Request kwargs après sanitation: {kwargs}")
                        logger.error(
                            "  CETTE ERREUR NE DEVRAIT PLUS SE PRODUIRE - Vérifier le code d'appel"
                        )
                    else:
                        logger.error(f"BinanceAPIException: {e}")
                    if "duplicate" in str(e).lower():
                        logger.error(
                            "DUPLICATE PARAMETER DETECTED - This should NOT happen with clean code"
                        )
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
                        print(
                            f"[TIME] {now.strftime('%H:%M:%S')} - Bot actif (RUNNING) | Prochaine execution dans {minutes_left} min ({next_run.strftime('%H:%M:%S')})"
                        )
                    else:
                        print(
                            f"[TIME] {now.strftime('%H:%M:%S')} - Bot actif (RUNNING) | Prochaine execution non planifiée"
                        )
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
    config.api_key, config.secret_key, requests_params={"timeout": config.api_timeout}
)

# Timeframes
timeframes = [
    Client.KLINE_INTERVAL_1HOUR,
    Client.KLINE_INTERVAL_4HOUR,
    Client.KLINE_INTERVAL_1DAY,
]

# Calcul de la date de debut
today = datetime.today()
start_date = (today - timedelta(days=config.backtest_days)).strftime("%d %B %Y")

# Cache pour les indicateurs
indicators_cache: dict = {}

# etat du bot
bot_state: dict = {}

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
        logger.info(
            "Synchronisation complète (Windows + Binance) effectuée avant envoi d’ordre."
        )
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
                    delay = base_delay * (2**attempt)
                    logger.warning(
                        f"Tentative {attempt + 1} echouee pour {func.__name__}: {e}. Retry dans {delay}s"
                    )
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
                for bal in account_info["balances"]:
                    asset = bal["asset"]
                    free = float(bal["free"])
                    locked = float(bal["locked"])
                    total = free + locked
                    if total < 1e-8:
                        continue
                    if asset == "USDC":
                        spot_balance_usdc += total
                    else:
                        symbol1 = asset + "USDC"
                        symbol2 = "USDC" + asset
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
                client=client,
            )
        except:
            pass
        return False


@log_exceptions(default_return=False)
def validate_data_integrity(df) -> bool:
    """Valide l'integrite des donnees de marche."""
    if df.empty:
        return False
    # Verifier les valeurs negatives
    if (df[["open", "high", "low", "close", "volume"]] < 0).any().any():
        logger.warning("Valeurs negatives detectees dans les donnees")
        return False
    # Verifier la coherence OHLC
    invalid_ohlc = (df["high"] < df[["open", "close"]].max(axis=1)) | (
        df["low"] > df[["open", "close"]].min(axis=1)
    )
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


# Version lru_cache pour usage sur des paramètres immuables (ex: tickers)
# Already provided by core.py, so no need to redefine here.


# Initialize error handler with email config


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
            with open(state_path, "rb") as f:
                old_hash = hash(f.read())
        new_hash = hash(state_bytes)
        if old_hash != new_hash:
            with open(state_path, "wb") as f:
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
            with open(state_path, "rb") as f:
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
        for bal in account_info["balances"]:
            asset = bal["asset"]
            free = float(bal["free"])
            locked = float(bal["locked"])
            total = free + locked
            if total < 1e-8:
                continue
            if asset == "USDC":
                spot_balance_usdc += total
            else:
                symbol1 = asset + "USDC"
                symbol2 = "USDC" + asset
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
        msg["From"] = config.sender_email
        msg["To"] = config.receiver_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

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


@log_exceptions(
    default_return={"min_qty": Decimal("0.001"), "step_size": Decimal("0.000001")}
)
def get_symbol_filters(symbol: str) -> Dict:
    """Recupere les filters min_qty et step_size pour un symbole."""
    info = client.get_symbol_info(symbol)
    if not info:
        raise ValueError(f"Aucune information trouvee pour le symbole {symbol}")
    for f in info["filters"]:
        if f["filterType"] == "LOT_SIZE":
            min_qty = Decimal(f["minQty"])
            step_size = Decimal(f["stepSize"])
            logger.info(
                f"Filters pour {symbol}: min_qty={min_qty}, step_size={step_size}"
            )
            return {"min_qty": min_qty, "step_size": step_size}
    raise ValueError(f"LOT_SIZE filter not found pour {symbol}")


###############################################################
#                                                             #
#              *** RECUPERATION DES DONNEES ***               #
#                                                             #
###############################################################


def get_cache_path(
    pair_symbol: str, time_interval: str, start_date: str
) -> Tuple[str, str]:
    """Génère des chemins de cache sécurisés et uniques."""
    # Normaliser le format de date pour éviter les conflits
    normalized_date = start_date.replace(" ", "_").replace(":", "-").replace(",", "")
    safe_name = f"{pair_symbol}_{time_interval}_{normalized_date}"

    # Vérifier si un cache existant avec un format légèrement différent existe
    cache_dir = config.cache_dir
    if os.path.exists(cache_dir):
        for existing_file in os.listdir(cache_dir):
            if (
                existing_file.startswith(f"{pair_symbol}_{time_interval}_")
                and existing_file.endswith(".pkl")
                and pair_symbol in existing_file
                and time_interval in existing_file
            ):
                # Utiliser le fichier existant
                cache_file = os.path.join(cache_dir, existing_file)
                lock_file = os.path.join(
                    cache_dir, existing_file.replace(".pkl", ".lock")
                )
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
def safe_cache_read(cache_file: str):
    """Lecture ultra-sécurisée du cache avec validation complète et expiration mensuelle."""
    if not os.path.exists(cache_file):
        return None

    try:
        # Vérifier l'expiration mensuelle
        if is_cache_expired(cache_file, max_age_days=30):
            logger.info(
                f"Cache expiré (>30 jours): {os.path.basename(cache_file)} - Mise à jour nécessaire"
            )
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
        with open(cache_file, "rb") as f:
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
def safe_cache_write(cache_file: str, lock_file: str, df) -> bool:
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
            with open(lock_file, "w") as lock:
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
                with open(cache_file, "rb") as f:
                    old_hash = hash(f.read())
            new_hash = hash(new_bytes)
            if old_hash != new_hash:
                with open(temp_file, "wb") as f:
                    f.write(new_bytes)
                # Renommage atomique
                if os.path.exists(cache_file):
                    try:
                        os.remove(cache_file)
                    except:
                        pass
                os.rename(temp_file, cache_file)
                logger.debug(
                    f"Cache sauvegardé: {os.path.basename(cache_file)} (modifié)"
                )
                return True
            else:
                logger.debug(
                    f"Cache inchangé, pas de sauvegarde: {os.path.basename(cache_file)}"
                )
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
def update_cache_with_recent_data(
    cached_df, pair_symbol: str, time_interval: str
) -> any:
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
            Client.KLINE_INTERVAL_1DAY: "7 days ago",
        }
        lookback = lookback_map.get(time_interval, "3 hours ago")

        # Récupérer UNIQUEMENT les dernières bougies
        recent_klines = client.get_historical_klines(
            pair_symbol, time_interval, lookback
        )

        if not recent_klines:
            return cached_df

        # Convertir en DataFrame
        df_recent = pd.DataFrame(
            recent_klines,
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "close_time",
                "quote_av",
                "trades",
                "tb_base_av",
                "tb_quote_av",
                "ignore",
            ],
        )
        df_recent = df_recent[["timestamp", "open", "high", "low", "close", "volume"]]
        df_recent[["open", "high", "low", "close", "volume"]] = df_recent[
            ["open", "high", "low", "close", "volume"]
        ].astype(float)
        df_recent["timestamp"] = pd.to_datetime(df_recent["timestamp"], unit="ms")
        df_recent.set_index("timestamp", inplace=True)

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
        df_merged = df_merged[
            ~df_merged.index.duplicated(keep="last")
        ]  # Retirer les doublons en gardant la dernière
        df_merged = df_merged.sort_index()

        new_candles_count = len(df_recent)
        logger.info(
            f"[CACHE] Cache updated: {pair_symbol} {time_interval} (+{new_candles_count} new candles)"
        )

        return df_merged

    except Exception as e:
        logger.debug(f"Mise à jour du cache échouée pour {pair_symbol}: {e}")
        return cached_df


@retry_with_backoff(max_retries=3, base_delay=2.0)
@log_exceptions(default_return=pd.DataFrame())
def fetch_historical_data(
    pair_symbol: str, time_interval: str, start_date: str, force_refresh: bool = False
) -> any:
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
                updated_df = update_cache_with_recent_data(
                    cached_df, pair_symbol, time_interval
                )

                # Sauvegarder le cache mis à jour
                safe_cache_write(cache_file, lock_file, updated_df)

                logger.info(
                    f"[OK] Cache used + updated: {pair_symbol} {time_interval} ({len(updated_df)} candles)"
                )
                return updated_df

        # Recuperation depuis l'API - Récupérer TOUTES les données depuis start_date
        if not VERBOSE_LOGS:
            logger.info(f"Telechargement {pair_symbol} {time_interval}...")
        else:
            logger.info(
                f"Debut telechargement pour {pair_symbol} {time_interval} depuis {start_date}"
            )

        try:
            # Récupérer TOUTES les données depuis start_date (pagination automatique)
            klinesT = client.get_historical_klines(
                pair_symbol, time_interval, start_date
            )

            if not klinesT:
                raise ValueError(f"Aucune donnee pour {pair_symbol} et {time_interval}")

            if not VERBOSE_LOGS:
                logger.info(
                    f"OK: {len(klinesT)} candles | {pd.Timestamp(klinesT[0][0], unit='ms').strftime('%Y-%m-%d')} -> {pd.Timestamp(klinesT[-1][0], unit='ms').strftime('%Y-%m-%d')}"
                )
            else:
                logger.info(
                    f"Telechargement complete: {len(klinesT)} candles recuperees pour {pair_symbol}"
                )
                logger.info(
                    f"  Date premiere bougie: {pd.Timestamp(klinesT[0][0], unit='ms')}"
                )
                logger.info(
                    f"  Date derniere bougie: {pd.Timestamp(klinesT[-1][0], unit='ms')}"
                )

        except Exception as e:
            logger.error(f"Erreur lors du telechargement: {e}")
            raise

        all_klines = klinesT

        # Creation du DataFrame
        df = pd.DataFrame(
            all_klines,
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "close_time",
                "quote_av",
                "trades",
                "tb_base_av",
                "tb_quote_av",
                "ignore",
            ],
        )
        df = df[["timestamp", "open", "high", "low", "close", "volume"]]

        # Conversion securisee
        numeric_columns = ["open", "high", "low", "close", "volume"]
        df[numeric_columns] = df[numeric_columns].astype(float)

        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)

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
                for bal in account_info["balances"]:
                    asset = bal["asset"]
                    free = float(bal["free"])
                    locked = float(bal["locked"])
                    total = free + locked
                    if total < 1e-8:
                        continue
                    if asset == "USDC":
                        global_balance_usdc += total
                    else:
                        symbol1 = asset + "USDC"
                        symbol2 = "USDC" + asset
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
                client=client,
            )
        except:
            pass
        raise
    except Exception as e:
        error_str = str(e)
        logger.error(f"Erreur recuperation donnees: {e}")

        # Détecter les erreurs de réseau
        if any(
            keyword in error_str.lower()
            for keyword in [
                "nameresolutionerror",
                "getaddrinfo failed",
                "max retries exceeded",
                "connection",
            ]
        ):
            logger.warning(
                "Erreur de connectivité détectée, tentative de récupération..."
            )

            if check_network_connectivity():
                logger.info("Connexion rétablie, nouvelle tentative...")
                time.sleep(3)
                # Une seule tentative supplémentaire
                try:
                    klinesT = client.get_historical_klines(
                        pair_symbol, time_interval, start_date
                    )
                    if klinesT:
                        df = pd.DataFrame(
                            klinesT,
                            columns=[
                                "timestamp",
                                "open",
                                "high",
                                "low",
                                "close",
                                "volume",
                                "close_time",
                                "quote_av",
                                "trades",
                                "tb_base_av",
                                "tb_quote_av",
                                "ignore",
                            ],
                        )
                        df = df[["timestamp", "open", "high", "low", "close", "volume"]]
                        numeric_columns = ["open", "high", "low", "close", "volume"]
                        df[numeric_columns] = df[numeric_columns].astype(float)
                        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
                        df.set_index("timestamp", inplace=True)
                        logger.info(
                            f"Données récupérées après rétablissement connexion"
                        )
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
                client=client,
            )
        except:
            pass
        return pd.DataFrame()


###############################################################
#                                                             #
#                *** CALCUL DES INDICATEURS ***               #
#                                                             #
###############################################################


def get_cache_key(prefix: str, df, params) -> str:
    """Génère une clé de cache unique basée sur les données et les paramètres."""
    if df.empty or "close" not in df.columns:
        return f"{prefix}_empty"

    close_vals = df["close"].values
    # Échantillon représentatif : 5 premières + 5 dernières valeurs
    sample_size = min(5, len(close_vals))
    sample = np.concatenate([close_vals[:sample_size], close_vals[-sample_size:]])
    data_hash = hashlib.md5(sample.tobytes()).hexdigest()[:16]

    # Paramètres triés et filtrés (ignorer None)
    param_items = sorted((k, v) for k, v in params.items() if v is not None)
    param_str = "_".join(f"{k}{v}" for k, v in param_items)

    return f"{prefix}_{len(df)}_{data_hash}_{param_str}"


def universal_calculate_indicators(
    df,
    ema1_period: int,
    ema2_period: int,
    stoch_period: int = 14,
    sma_long: Optional[int] = None,
    adx_period=None,
    trix_length=None,
    trix_signal=None,
) -> any:
    """
    Universal indicator calculation for live trading and backtest.
    Uses Cython-accelerated calculation if available, else falls back to Python.
    Mirrors the logic of backtest_from_dataframe for indicator calculation.
    """
    try:
        if df.empty or len(df) < 10:
            return pd.DataFrame()

        # Use Cython if available
        if CYTHON_BACKTEST_AVAILABLE and hasattr(
            backtest_engine, "calculate_indicators_fast"
        ):
            try:
                # Prepare all required columns
                df_work = df.copy()
                df_work["ema1"] = (
                    df_work["close"].ewm(span=ema1_period, adjust=False).mean()
                )
                df_work["ema2"] = (
                    df_work["close"].ewm(span=ema2_period, adjust=False).mean()
                )
                if "rsi" not in df_work.columns:
                    df_work["rsi"] = ta.momentum.RSIIndicator(
                        df_work["close"], window=14
                    ).rsi()

                def compute_stochrsi(rsi_series, period: int = 14):
                    min_rsi = rsi_series.rolling(
                        window=period, min_periods=period
                    ).min()
                    max_rsi = rsi_series.rolling(
                        window=period, min_periods=period
                    ).max()
                    stochrsi = (rsi_series - min_rsi) / (max_rsi - min_rsi)
                    stochrsi = stochrsi.clip(lower=0, upper=1)
                    stochrsi = stochrsi.fillna(0)
                    return stochrsi

                df_work["stoch_rsi"] = compute_stochrsi(
                    df_work["rsi"], period=stoch_period
                )
                df_work["atr"] = ta.volatility.AverageTrueRange(
                    high=df_work.get("high", df_work["close"]),
                    low=df_work.get("low", df_work["close"]),
                    close=df_work["close"],
                    window=14,
                ).average_true_range()
                if sma_long:
                    df_work["sma_long"] = (
                        df_work["close"].rolling(window=sma_long).mean()
                    )
                if adx_period and len(df_work) >= adx_period + 2:
                    try:
                        df_work["adx"] = ta.trend.ADXIndicator(
                            high=df_work["high"],
                            low=df_work["low"],
                            close=df_work["close"],
                            window=adx_period,
                        ).adx()
                    except Exception:
                        df_work["adx"] = np.nan
                if trix_length and trix_signal:
                    ema1 = df_work["close"].ewm(span=trix_length, adjust=False).mean()
                    ema2 = ema1.ewm(span=trix_length, adjust=False).mean()
                    ema3 = ema2.ewm(span=trix_length, adjust=False).mean()
                    df_work["TRIX_PCT"] = ema3.pct_change() * 100
                    df_work["TRIX_SIGNAL"] = (
                        df_work["TRIX_PCT"].rolling(window=trix_signal).mean()
                    )
                    df_work["TRIX_HISTO"] = df_work["TRIX_PCT"] - df_work["TRIX_SIGNAL"]
                # Call Cython-accelerated calculation (returns DataFrame)
                result = backtest_engine.calculate_indicators_fast(
                    df_work["close"].values.astype(np.float64),
                    df_work["high"].values.astype(np.float64),
                    df_work["low"].values.astype(np.float64),
                    ema1_period,
                    ema2_period,
                    stoch_period,
                    sma_long if sma_long else 0,
                    adx_period if adx_period else 0,
                    trix_length if trix_length else 0,
                    trix_signal if trix_signal else 0,
                )
                # The Cython function should return a DataFrame or dict of arrays; adapt as needed
                # If not a DataFrame, reconstruct it
                if isinstance(result, pd.DataFrame):
                    return result
                elif isinstance(result, dict):
                    # Assume dict of arrays, reconstruct DataFrame with same index as df_work
                    return pd.DataFrame(result, index=df_work.index)
                else:
                    logger.warning(
                        "Cython calculate_indicators_fast returned unexpected type, using Python fallback."
                    )
            except Exception as e:
                logger.warning(
                    f"Cython indicator calculation failed, using Python fallback: {e}"
                )
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
            trix_signal=trix_signal,
        )
    except Exception as e:
        logger.error(f"Erreur universal_calculate_indicators: {e}", exc_info=True)
        return pd.DataFrame()


def calculate_indicators(
    df,
    ema1_period: int,
    ema2_period: int,
    stoch_period: int = 14,
    sma_long=None,
    adx_period=None,
    trix_length=None,
    trix_signal=None,
) -> any:
    """
    Calcule les indicateurs techniques avec cache robuste et optimisation mémoire.
    Toutes les opérations sont vectorisées (pandas/numpy), aucune boucle Python.
    """
    try:
        if df.empty or "close" not in df.columns:
            raise KeyError("DataFrame vide ou colonne 'close' absente")

        # Préparer les paramètres pour le cache
        params = {
            "ema1": ema1_period,
            "ema2": ema2_period,
            "stoch": stoch_period,
            "sma": sma_long,
            "adx": adx_period,
            "trix_len": trix_length,
            "trix_sig": trix_signal,
        }
        cache_key = get_cache_key("indicators", df, params)

        # Lecture thread-safe du cache
        try:
            if cache_key in indicators_cache:
                cached_df = indicators_cache[cache_key]
                if len(cached_df) == len(
                    df.dropna(subset=["close"])
                ):  # ou juste len(df) si tu veux matcher l'entrée brute
                    logger.debug("Indicateurs chargés depuis le cache mémoire")
                    return cached_df.copy()
        except (KeyError, AttributeError, TypeError):
            pass  # Cache corrompu ou indisponible -> recalcul

        # Copie de travail
        df_work = df.copy()

        # Nettoyage minimal des NaN sur 'close'
        df_work["close"] = df_work["close"].ffill().bfill()
        if df_work["close"].isna().any():
            logger.warning("Données 'close' entièrement NaN après nettoyage")
            return pd.DataFrame()

        # --- RSI (seulement si absent) ---
        if "rsi" not in df_work.columns:
            df_work["rsi"] = ta.momentum.RSIIndicator(df_work["close"], window=14).rsi()

        # --- EMA ---
        # IMPORTANT: utiliser adjust=False pour correspondre aux calculs Binance (methode recursive/online)
        df_work["ema1"] = df_work["close"].ewm(span=ema1_period, adjust=False).mean()
        df_work["ema2"] = df_work["close"].ewm(span=ema2_period, adjust=False).mean()

        # --- Stochastic RSI (vectorized robust calculation) ---
        def compute_stochrsi(rsi_series, period: int):
            min_rsi = rsi_series.rolling(window=period, min_periods=period).min()
            max_rsi = rsi_series.rolling(window=period, min_periods=period).max()
            stochrsi = (rsi_series - min_rsi) / (max_rsi - min_rsi)
            stochrsi = stochrsi.clip(lower=0, upper=1)
            stochrsi = stochrsi.fillna(0)
            return stochrsi

        if "rsi" in df_work.columns:
            df_work["stoch_rsi"] = compute_stochrsi(df_work["rsi"], period=stoch_period)

        # --- ATR ---
        df_work["atr"] = ta.volatility.AverageTrueRange(
            high=df_work.get("high", df_work["close"]),
            low=df_work.get("low", df_work["close"]),
            close=df_work["close"],
            window=config.atr_period,
        ).average_true_range()

        # --- SMA long ---
        if sma_long:
            df_work["sma_long"] = df_work["close"].rolling(window=sma_long).mean()

        # --- ADX (utiliser l'implémentation standard Wilder via ta.trend.ADXIndicator) ---
        if adx_period and len(df_work) >= adx_period + 2:
            try:
                df_work["adx"] = ta.trend.ADXIndicator(
                    high=df_work["high"],
                    low=df_work["low"],
                    close=df_work["close"],
                    window=adx_period,
                ).adx()
            except Exception:
                df_work["adx"] = np.nan

        # --- TRIX ---
        if trix_length and trix_signal:
            ema1 = df_work["close"].ewm(span=trix_length, adjust=False).mean()
            ema2 = ema1.ewm(span=trix_length, adjust=False).mean()
            ema3 = ema2.ewm(span=trix_length, adjust=False).mean()
            df_work["TRIX_PCT"] = ema3.pct_change() * 100
            df_work["TRIX_SIGNAL"] = (
                df_work["TRIX_PCT"].rolling(window=trix_signal).mean()
            )
            df_work["TRIX_HISTO"] = df_work["TRIX_PCT"] - df_work["TRIX_SIGNAL"]

        # --- Nettoyage final ---
        # Supprimer NaN uniquement des colonnes essentielles (pas stoch_rsi qui a des 0 valides)
        df_work.dropna(subset=["close", "rsi", "atr"], inplace=True)

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
                for bal in account_info["balances"]:
                    asset = bal["asset"]
                    free = float(bal["free"])
                    locked = float(bal["locked"])
                    total = free + locked
                    if total < 1e-8:
                        continue
                    if asset == "USDC":
                        global_balance_usdc += total
                    else:
                        symbol1 = asset + "USDC"
                        symbol2 = "USDC" + asset
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
                client=client,
            )
        except:
            pass
        return pd.DataFrame()


###############################################################
#                                                             #
#              *** BACKTEST DE LA STRATEGIE ***               #
#                                                             #
###############################################################


def prepare_base_dataframe(
    pair: str, timeframe: str, start_date: str, stoch_period: int = 14
) -> any:
    """Prépare un DataFrame avec tous les indicateurs de base partagés par tous les scénarios."""
    df = fetch_historical_data(pair, timeframe, start_date)
    if df.empty:
        return None

    # Calculer TOUS les EMA possibles (adjust=False pour correspondre Binance - methode recursive/online)
    for period in [14, 25, 26, 45, 50]:
        df[f"ema_{period}"] = df["close"].ewm(span=period, adjust=False).mean()

    # Indicateurs communs à tous les scénarios
    df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=14).rsi()

    df["atr"] = ta.volatility.AverageTrueRange(
        high=df["high"], low=df["low"], close=df["close"], window=config.atr_period
    ).average_true_range()

    # Stochastic RSI (Raw calculation - no smoothing)
    def compute_stochrsi(rsi_series, period: int = 14):
        # Méthode optimisée et robuste pour StochRSI
        rsi_np = rsi_series.to_numpy()
        min_rsi = (
            pd.Series(rsi_np)
            .rolling(window=period, min_periods=period)
            .min()
            .to_numpy()
        )
        max_rsi = (
            pd.Series(rsi_np)
            .rolling(window=period, min_periods=period)
            .max()
            .to_numpy()
        )
        # Calcul vectorisé, évite les divisions par zéro
        denom = max_rsi - min_rsi
        with np.errstate(divide="ignore", invalid="ignore"):
            stochrsi = np.where(denom != 0, (rsi_np - min_rsi) / denom, 0)
        stochrsi = np.clip(stochrsi, 0, 1)
        stochrsi = np.nan_to_num(stochrsi, nan=0)
        return pd.Series(stochrsi, index=rsi_series.index)

    df["stoch_rsi"] = compute_stochrsi(df["rsi"], period=stoch_period)

    # Supprimer NaN uniquement des colonnes essentielles
    df.dropna(subset=["close", "rsi", "atr"], inplace=True)
    return df


def backtest_from_dataframe(
    df,
    ema1_period: int,
    ema2_period: int,
    sma_long: Optional[int] = None,
    adx_period: Optional[int] = None,
    trix_length: Optional[int] = None,
    trix_signal: Optional[int] = None,
    sizing_mode: str = "baseline",  # 'baseline' (100% capital) or 'risk' (risk-based sizing)
) -> dict:
    """Backtest à partir d'un DataFrame préparé.
    import pandas as pd  # Pour multiprocessing et scope local (needed for multiprocessing/fallback)
    trailing_activation_price_at_entry = None  # Initialisation robuste

    Utilise l'implémentation Cython accélérée si disponible (30-50x plus rapide),
    sinon utilise la version Python comme fallback.
    """
    try:
        if df.empty or len(df) < 50:
            return {
                "final_wallet": 0.0,
                "trades": pd.DataFrame(),
                "max_drawdown": 0.0,
                "win_rate": 0.0,
            }

        # === OPTIMISATION CYTHON : Appeler le moteur optimisé si disponible ===
        if CYTHON_BACKTEST_AVAILABLE:
            try:
                # Préparer les données pour le backtest Cython
                df_work = df.copy()
                df_work["ema1"] = df_work[f"ema_{ema1_period}"]
                df_work["ema2"] = df_work[f"ema_{ema2_period}"]
                # StochRSI déjà calculé dans prepare_base_dataframe
                # Pas besoin de recalculer ici
                # Ajouter indicateurs spécifiques
                if sma_long:
                    df_work["sma_long"] = (
                        df_work["close"].rolling(window=sma_long).mean()
                    )

                if adx_period:
                    if "adx" in df.columns:
                        df_work["adx"] = df["adx"].values
                    else:
                        # Utiliser ta.trend (optimisé et testé)
                        df_work["adx"] = ta.trend.ADXIndicator(
                            high=df_work["high"],
                            low=df_work["low"],
                            close=df_work["close"],
                            window=adx_period,
                        ).adx()

                if trix_length and trix_signal:
                    ema1 = df_work["close"].ewm(span=trix_length, adjust=False).mean()
                    ema2 = ema1.ewm(span=trix_length, adjust=False).mean()
                    ema3 = ema2.ewm(span=trix_length, adjust=False).mean()
                    df_work["TRIX_PCT"] = ema3.pct_change() * 100
                    df_work["TRIX_SIGNAL"] = (
                        df_work["TRIX_PCT"].rolling(window=trix_signal).mean()
                    )
                    df_work["TRIX_HISTO"] = df_work["TRIX_PCT"] - df_work["TRIX_SIGNAL"]

                # Appeler le moteur Cython optimisé
                result = backtest_engine.backtest_from_dataframe_fast(
                    df_work["close"].values.astype(np.float64),
                    df_work["high"].values.astype(np.float64),
                    df_work["low"].values.astype(np.float64),
                    df_work["ema1"].values.astype(np.float64),
                    df_work["ema2"].values.astype(np.float64),
                    df_work["stoch_rsi"].values.astype(np.float64),
                    df_work["atr"].values.astype(np.float64),
                    (
                        df_work["sma_long"].values.astype(np.float64)
                        if sma_long and "sma_long" in df_work.columns
                        else None
                    ),
                    (
                        df_work["adx"].values.astype(np.float64)
                        if adx_period and "adx" in df_work.columns
                        else None
                    ),
                    (
                        df_work["TRIX_HISTO"].values.astype(np.float64)
                        if trix_length and "TRIX_HISTO" in df_work.columns
                        else None
                    ),
                    config.initial_wallet,  # initial_wallet
                    "StochRSI",  # scenario
                    sma_long is not None,  # use_sma
                    adx_period is not None,  # use_adx
                    trix_length is not None,  # use_trix
                )

                # Convertir le résultat Cython en format compatible
                # Ensure pandas is imported in this scope (for multiprocessing fallback)
                import pandas as pd

                return {
                    "final_wallet": result["final_wallet"],
                    "trades": (
                        pd.DataFrame(result["trades"])
                        if result["trades"]
                        else pd.DataFrame()
                    ),
                    "max_drawdown": result["max_drawdown"],
                    "win_rate": result["win_rate"],
                }

            except Exception as e:
                logger.warning(f" Cython backtest failed, using Python fallback: {e}")
                import traceback

                traceback.print_exc()
                # Continuer avec la version Python comme fallback

        # === VERSION PYTHON (fallback ou si Cython n'est pas disponible) ===
        df_work = df.copy()
        df_work["ema1"] = df_work[f"ema_{ema1_period}"]
        df_work["ema2"] = df_work[f"ema_{ema2_period}"]

        # StochRSI déjà calculé dans prepare_base_dataframe avec manual loop (méthode optimale +$895)
        # Pas besoin de recalculer ici

        # Ajouter indicateurs spécifiques
        if sma_long:
            df_work["sma_long"] = df_work["close"].rolling(window=sma_long).mean()

        if adx_period and "adx" in df.columns:
            df_work["adx"] = df["adx"].values
        elif adx_period:
            close = df_work["close"].values
            high = df_work["high"].values
            low = df_work["low"].values
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
            plus_dm[1:] = np.where(
                (high_diff > low_diff) & (high_diff > 0), high_diff, 0
            )
            minus_dm[1:] = np.where(
                (low_diff > high_diff) & (low_diff > 0), low_diff, 0
            )
            kernel = np.ones(adx_period) / adx_period
            tr_smooth = np.convolve(tr, kernel, mode="same")
            plus_dm_smooth = np.convolve(plus_dm, kernel, mode="same")
            minus_dm_smooth = np.convolve(minus_dm, kernel, mode="same")
            plus_di = (
                np.divide(
                    plus_dm_smooth,
                    tr_smooth,
                    out=np.zeros_like(tr_smooth),
                    where=tr_smooth != 0,
                )
                * 100
            )
            minus_di = (
                np.divide(
                    minus_dm_smooth,
                    tr_smooth,
                    out=np.zeros_like(tr_smooth),
                    where=tr_smooth != 0,
                )
                * 100
            )
            di_sum = plus_di + minus_di
            dx = (
                np.divide(
                    np.abs(plus_di - minus_di),
                    di_sum,
                    out=np.zeros_like(di_sum),
                    where=di_sum != 0,
                )
                * 100
            )
            adx = np.convolve(dx, kernel, mode="same")
            df_work["adx"] = adx

        if trix_length and trix_signal:
            ema1 = df_work["close"].ewm(span=trix_length, adjust=False).mean()
            ema2 = ema1.ewm(span=trix_length, adjust=False).mean()
            ema3 = ema2.ewm(span=trix_length, adjust=False).mean()
            df_work["TRIX_PCT"] = ema3.pct_change() * 100
            df_work["TRIX_SIGNAL"] = (
                df_work["TRIX_PCT"].rolling(window=trix_signal).mean()
            )
            df_work["TRIX_HISTO"] = df_work["TRIX_PCT"] - df_work["TRIX_SIGNAL"]

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
        ema_cross_up = df_work["ema1"] > df_work["ema2"]
        ema_cross_down = df_work["ema2"] > df_work["ema1"]
        stoch_low = (df_work["stoch_rsi"] < 0.8) & (df_work["stoch_rsi"] > 0.05)
        stoch_high = df_work["stoch_rsi"] > 0.2

        ema_cross_up_vals = ema_cross_up.values
        ema_cross_down_vals = ema_cross_down.values
        stoch_low_vals = stoch_low.values
        stoch_high_vals = stoch_high.values
        close_vals = df_work["close"].values
        atr_vals = df_work["atr"].values
        indices = df_work.index.values

        # Correction: utiliser la dernière bougie fermée pour le signal d'achat
        for i in range(len(df_work)):
            # Pour le signal d'achat, on utilise la dernière bougie fermée (i-1)
            idx_signal = i - 1 if i > 0 else i
            row_close = close_vals[i]
            row_atr = atr_vals[i]
            index = indices[i]
            symbol = config.symbol if hasattr(config, "symbol") else None
            # Diagnostic: afficher la valeur StochRSI utilisée pour le signal d'achat
            if i == len(df_work) - 1:
                stochrsi_used = df_work["stoch_rsi"].values[idx_signal]
                print(
                    f"[DIAGNOSTIC] StochRSI utilisé pour le signal d'achat (bougie fermée): {stochrsi_used:.4f}"
                )
            if in_position:
                # Utiliser uniquement les valeurs fixées à l'entrée tant que le trailing n'est pas activé
                if not trailing_stop_activated:
                    # stop_loss et trailing_activation_price restent fixes
                    stop_loss = stop_loss_at_entry
                    # trailing_activation_price doit toujours être stocké dans pair_state pour cohérence affichage/logique
                    if "pair_state" in locals():
                        pair_state["trailing_activation_price"] = (
                            trailing_activation_price_at_entry
                        )
                # Correction robustesse : éviter le calcul si atr_at_entry est None
                atr_at_entry = row_atr if row_atr is not None else 0.0
                # On ne garde pas de variable locale inutile : la valeur d'affichage et de logique doit toujours venir de pair_state['trailing_activation_price']
                trailing_distance = config.atr_multiplier * atr_at_entry
            else:
                # Après activation, trailing_distance devient dynamique
                trailing_distance = config.atr_multiplier * row_atr
                # Activer le trailing stop uniquement si le prix a progressé d'au moins la distance (calculé à l'entrée)
                # Activation du trailing stop (une seule fois)
                if (
                    not trailing_stop_activated
                    and trailing_activation_price_at_entry is not None
                    and row_close >= trailing_activation_price_at_entry
                ):
                    trailing_stop_activated = True
                    max_price = row_close
                    trailing_stop = row_close - trailing_distance
                    stop_loss = trailing_stop
                    # Envoi d'une alerte mail une seule fois lors de l'activation du trailing stop
                    try:
                        send_trading_alert_email(
                            subject=f"[BOT CRYPTO] Trailing Stop ACTIVÉ sur {symbol}",
                            body_main=f"Le trailing stop vient d'être activé sur {symbol} à {row_close:.4f} USDC. Distance: {trailing_distance:.4f} USDC.",
                            client=client,
                        )
                    except Exception as mail_exc:
                        logger.error(
                            f"[EXCEPTION] Echec envoi mail activation trailing stop: {mail_exc}"
                        )
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
                drawdown = (
                    (peak_wallet - current_wallet) / peak_wallet
                    if peak_wallet > 0
                    else 0.0
                )
                max_drawdown = max(max_drawdown, drawdown)

                # Check exit conditions
                exit_trade = False
                motif_sortie = None
                # Harmonisation : sortie si <= stop_loss ou (<= trailing_stop et trailing activé) ou signal stratégie
                if row_close <= stop_loss:
                    exit_trade = True
                    motif_sortie = "STOP_LOSS"
                elif trailing_stop_activated and row_close <= trailing_stop:
                    exit_trade = True
                    motif_sortie = "TRAILING_STOP"
                elif ema_cross_down_vals[i] and stoch_high_vals[i]:
                    exit_trade = True
                    motif_sortie = "SIGNAL"

                if exit_trade:
                    # Exécution de l'ordre selon le motif
                    client_id = _generate_client_order_id("sell")
                    if motif_sortie == "STOP_LOSS":
                        place_stop_loss_order(
                            symbol=symbol,
                            quantity=float(coin),
                            stop_price=float(stop_loss),
                            client_id=client_id,
                        )
                    elif motif_sortie == "TRAILING_STOP":
                        trailing_delta = int(row_atr * 100)
                        place_trailing_stop_order(
                            symbol=symbol,
                            quantity=float(coin),
                            activation_price=float(row_close),
                            trailing_delta=trailing_delta,
                            client_id=client_id,
                        )
                    else:
                        safe_market_sell(symbol=symbol, quantity=float(coin))
                    # Envoi d'une alerte mail à chaque clôture de position
                    try:
                        send_trading_alert_email(
                            subject=f"[BOT CRYPTO] Clôture exécutée sur {symbol}",
                            body_main=f"Clôture exécutée sur {symbol} pour {coin} à {row_close:.4f} USDC. Motif: {motif_sortie}",
                            client=client,
                        )
                    except Exception as mail_exc:
                        logger.error(
                            f"[EXCEPTION] Echec envoi mail clôture: {mail_exc}"
                        )

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

                    trades_history.append(
                        {
                            "date": index,
                            "type": "sell",
                            "price": row_close,
                            "profit": trade_profit,
                            "motif": motif_sortie,
                            "stop_loss": f"{stop_loss:.4f} USDC",
                            "trailing_stop": f"{trailing_stop:.4f} USDC",
                        }
                    )

                    in_position = False
                    entry_price = 0.0  # Reset après vente
                    logger.debug(
                        f"Position fermée à {row_close:.4f}, profit: {trade_profit:.2f}, motif: {motif_sortie}"
                    )
                    entry_usd_invested = 0.0
                    max_price = 0.0
                    trailing_stop = 0.0
                    stop_loss = 0.0
                    trailing_stop_activated = False
                    continue

            # ...diagnostic RSI/StochRSI retiré...
            # Condition d'achat: suffisant de capital
            buy_condition = (
                ema_cross_up_vals[idx_signal] and stoch_low_vals[idx_signal] and usd > 0
            )
            if sma_long and "sma_long" in df_work.columns:
                buy_condition &= row_close > df_work["sma_long"].values[idx_signal]
            if adx_period and "adx" in df_work.columns:
                # FILTRE ADX: 25 (standard)
                buy_condition &= df_work["adx"].values[idx_signal] > 25
            if trix_length and "TRIX_HISTO" in df_work.columns:
                buy_condition &= df_work["TRIX_HISTO"].values[idx_signal] > 0

            # --- PATCH: Fix ATR at entry for stop loss and trailing activation ---
            # These variables will be set at order open and used throughout the position
            atr_at_entry = None
            stop_loss_at_entry = None
            trailing_activation_price_at_entry = None

            if buy_condition and not in_position:
                # OPTIMISATION: Pas de sniper en backtest Python (trop lent, déjà dans Cython)
                optimized_price = row_close
                # Position sizing: support for 3 methods
                if sizing_mode == "baseline":
                    # 100% du capital par trade (ancien comportement)
                    gross_coin = usd / optimized_price if optimized_price > 0 else 0.0
                elif sizing_mode == "risk":
                    # ATR-based risk sizing
                    try:
                        equity = usd  # Use current USD balance as equity for backtest
                        qty_by_risk = compute_position_size_by_risk(
                            equity=equity,
                            atr_value=row_atr,
                            entry_price=optimized_price,
                        )
                        max_affordable = (
                            usd / optimized_price if optimized_price > 0 else 0.0
                        )
                        gross_coin = min(max_affordable, qty_by_risk)
                    except Exception:
                        gross_coin = 0.0
                elif sizing_mode == "fixed_notional":
                    # Fixed notional (fixed USD per trade)
                    try:
                        notional_per_trade = getattr(
                            config, "notional_per_trade_usd", None
                        ) or (config.initial_wallet * 0.10)
                        qty_fixed = compute_position_size_fixed_notional(
                            equity=usd,
                            notional_per_trade_usd=notional_per_trade,
                            entry_price=optimized_price,
                        )
                        max_affordable = (
                            usd / optimized_price if optimized_price > 0 else 0.0
                        )
                        gross_coin = min(max_affordable, qty_fixed)
                    except Exception:
                        gross_coin = 0.0
                elif sizing_mode == "volatility_parity":
                    # Volatility parity sizing
                    try:
                        target_vol = getattr(config, "target_volatility_pct", 0.02)
                        qty_vol = compute_position_size_volatility_parity(
                            equity=usd,
                            atr_value=row_atr,
                            entry_price=optimized_price,
                            target_volatility_pct=target_vol,
                        )
                        max_affordable = (
                            usd / optimized_price if optimized_price > 0 else 0.0
                        )
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
                    logger.debug(
                        f"Position ouverte: entry={entry_price:.4f}, stop_loss={optimized_price - (3 * row_atr):.4f}"
                    )
                    max_price = optimized_price
                    # --- PATCH: Fix ATR at entry for stop loss and trailing activation ---
                    atr_at_entry = row_atr
                    stop_loss_at_entry = optimized_price - (3 * atr_at_entry)
                    trailing_activation_price_at_entry = optimized_price + (
                        config.atr_multiplier * atr_at_entry
                    )
                    stop_loss = stop_loss_at_entry
                    trailing_stop = optimized_price - (
                        config.atr_multiplier * atr_at_entry
                    )
                    # Initialiser le prix d'activation du trailing stop (fixe, pour affichage et logique)
                    if "pair_state" in locals():
                        pair_state["trailing_activation_price"] = (
                            trailing_activation_price_at_entry
                        )
                    trailing_stop_activated = False
                    trades_history.append(
                        {"date": index, "type": "buy", "price": optimized_price}
                    )
                    in_position = True
                    # Envoi d'une alerte mail lors du placement du stop loss à l'ouverture
                    try:
                        send_trading_alert_email(
                            subject=f"[BOT CRYPTO] STOP LOSS placé à l'ouverture sur {symbol}",
                            body_main=f"Un ordre STOP LOSS a été placé à l'ouverture sur {symbol} pour {coin} à {stop_loss:.4f} USDC.",
                            client=client,
                        )
                    except Exception as mail_exc:
                        logger.error(
                            f"[EXCEPTION] Echec envoi mail STOP LOSS ouverture: {mail_exc}"
                        )
                else:
                    # Impossible d'ouvrir une position (pas de taille calculable)
                    pass

        # Final wallet calculation
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
        final_wallet = usd + (coin * df_work["close"].iloc[-1]) if in_position else usd

        # Export CSV des trades au format standard dans trades_export.csv
        import os

        columns = [
            "type",
            "price",
            "profit",
            "pair",
            "scenario",
            "timeframe",
            "ema1",
            "ema2",
        ]
        # Récupérer les infos de contexte (pair, scenario, timeframe, ema1, ema2)
        pair = config.symbol if hasattr(config, "symbol") else ""
        scenario = locals().get("scenario", "")
        timeframe = locals().get("timeframe", "")
        ema1 = locals().get("ema1_period", None)
        ema2 = locals().get("ema2_period", None)
        trades_export = []
        for t in trades_history:
            if t.get("type") == "buy":
                trades_export.append(
                    [
                        "BUY",
                        t.get("price", ""),
                        "",
                        pair,
                        scenario,
                        timeframe,
                        str(ema1) if ema1 is not None else "",
                        str(ema2) if ema2 is not None else "",
                    ]
                )
            elif t.get("type") == "sell":
                profit = t.get("profit", "")
                if profit is None or (isinstance(profit, float) and (profit != profit)):
                    profit = ""
                trades_export.append(
                    [
                        "SELL",
                        t.get("price", ""),
                        profit,
                        pair,
                        scenario,
                        timeframe,
                        str(ema1) if ema1 is not None else "",
                        str(ema2) if ema2 is not None else "",
                    ]
                )
        import pandas as pd

        trades_df = pd.DataFrame(trades_export, columns=columns)
        export_path = os.path.abspath("trades_export.csv")
        try:
            trades_df.to_csv(
                export_path, index=False, encoding="utf-8", float_format="%.10g"
            )
            print(
                f"[EXPORT] {len(trades_df)} trades exportés dans {export_path}",
                flush=True,
            )
            # Write metadata JSON next to CSV for traceability
            try:
                import json
                from datetime import datetime as _dt

                meta = {
                    "timestamp": _dt.now().isoformat(timespec="seconds"),
                    "source": "csv_script",
                    "taker_fee": getattr(config, "taker_fee", None),
                    "atr_multiplier": getattr(config, "atr_multiplier", None),
                    "atr_stop_multiplier": getattr(config, "atr_stop_multiplier", None),
                    "initial_wallet": getattr(config, "initial_wallet", None),
                    "backtest_days": getattr(config, "backtest_days", None),
                    "context": {
                        "pair": pair,
                        "scenario": scenario,
                        "timeframe": timeframe,
                        "ema1": ema1,
                        "ema2": ema2,
                    },
                }
                meta_path = os.path.abspath("trades_export.meta.json")
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(meta, f, ensure_ascii=False, indent=2)
            except Exception as _meta_exc:
                print(
                    f"[EXPORT WARN] Impossible d'écrire le metadata JSON: {_meta_exc}",
                    flush=True,
                )
        except Exception as export_exc:
            print(
                f"[EXPORT ERROR] Impossible d'exporter l'historique des trades: {export_exc}",
                flush=True,
            )

        return {
            "final_wallet": final_wallet,
            "trades": trades_df,
            "max_drawdown": max_drawdown,
            "win_rate": win_rate,
        }

    except Exception as e:
        logger.error(f"Erreur dans backtest_from_dataframe: {e}", exc_info=True)
        return {
            "final_wallet": 0.0,
            "trades": pd.DataFrame(),
            "max_drawdown": 0.0,
            "win_rate": 0.0,
        }


def empty_result_dict(
    timeframe: str, ema1: int, ema2: int, scenario_name: str
) -> Dict[str, Any]:
    """Retourne un résultat de backtest vide en cas d'erreur."""
    return {
        "timeframe": timeframe,
        "ema_periods": (ema1, ema2),
        "scenario": scenario_name,
        "initial_wallet": config.initial_wallet,
        "final_wallet": 0.0,
        "trades": pd.DataFrame(),
        "max_drawdown": 0.0,
        "win_rate": 0.0,
    }


def run_single_backtest_optimized(args: Tuple) -> Dict[str, Any]:
    """Execute un backtest unique à partir d'un DataFrame préparé.

    args tuple layout: (timeframe, ema1, ema2, scenario, base_df, pair_symbol, sizing_mode)
    """
    # Backwards-compatible unpacking (older tasks may not include sizing_mode)
    if len(args) == 6:
        (timeframe, ema1, ema2, scenario, base_df, pair_symbol) = args
        sizing_mode = "baseline"
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
            sma_long=scenario["params"].get("sma_long"),
            adx_period=scenario["params"].get("adx_period"),
            trix_length=scenario["params"].get("trix_length"),
            trix_signal=scenario["params"].get("trix_signal"),
            sizing_mode=sizing_mode,
        )
        return {
            "timeframe": timeframe,
            "ema_periods": (ema1, ema2),
            "scenario": scenario["name"],
            "initial_wallet": config.initial_wallet,
            "final_wallet": result["final_wallet"],
            "trades": result["trades"],
            "max_drawdown": result["max_drawdown"],
            "win_rate": result["win_rate"],
        }
    except Exception as e:
        logger.error(f"Erreur backtest parallele: {e}")
        return {
            "timeframe": timeframe,
            "ema_periods": (ema1, ema2),
            "scenario": scenario["name"],
            "initial_wallet": config.initial_wallet,
            "final_wallet": 0.0,
            "trades": pd.DataFrame(),
            "max_drawdown": 0.0,
            "win_rate": 0.0,
        }
    finally:
        # Nettoyer la variable globale
        _current_backtest_pair = None


def run_all_backtests(
    backtest_pair: str,
    start_date: str,
    timeframes: List[str],
    sizing_mode: str = "baseline",
) -> List[Dict[str, Any]]:
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
        {"name": "StochRSI", "params": {"stoch_period": 14}},
        {"name": "StochRSI_SMA", "params": {"stoch_period": 14, "sma_long": 200}},
        {"name": "StochRSI_ADX", "params": {"stoch_period": 14, "adx_period": 14}},
        {
            "name": "StochRSI_TRIX",
            "params": {"stoch_period": 14, "trix_length": 7, "trix_signal": 15},
        },
    ]

    tasks = []
    for ema1, ema2 in ema_periods:
        for scenario in scenarios:
            for timeframe in timeframes:
                base_df = base_dataframes[timeframe]
                if base_df.empty:
                    continue
                # include sizing_mode in task tuple so workers can use risk-based sizing when requested
                tasks.append(
                    (
                        timeframe,
                        ema1,
                        ema2,
                        scenario,
                        base_df,
                        backtest_pair,
                        sizing_mode,
                    )
                )

    with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
        future_to_task = {
            executor.submit(run_single_backtest_optimized, task): task for task in tasks
        }
        with tqdm(
            total=len(tasks),
            desc="[BACKTESTS]",
            colour="green",
            bar_format="{desc}: {percentage:3.0f}%|█{bar:30}█| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
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


def run_parallel_backtests(
    crypto_pairs: List[Dict],
    start_date: str,
    timeframes: List[str],
    sizing_mode: str = "baseline",
) -> List[Dict]:
    """Exécute les backtests en parallèle et retourne les résultats bruts."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    max_workers = min(len(crypto_pairs), 5)

    results_by_pair = {}

    # Calculer le nombre total de tâches pour la barre de progression globale
    ema_periods = [(14, 26), (25, 45), (26, 50)]
    scenarios = [
        {"name": "StochRSI", "params": {"stoch_period": 14}},
        {"name": "StochRSI_SMA", "params": {"stoch_period": 14, "sma_long": 200}},
        {"name": "StochRSI_ADX", "params": {"stoch_period": 14, "adx_period": 14}},
        {
            "name": "StochRSI_TRIX",
            "params": {"stoch_period": 14, "trix_length": 7, "trix_signal": 15},
        },
    ]
    total_tasks = (
        len(crypto_pairs) * len(ema_periods) * len(scenarios) * len(timeframes)
    )

    # Affichage unique et propre
    console.print(
        f"\n[bold cyan]Lancement de {total_tasks} backtests avec optimisation sniper...[/bold cyan]"
    )

    def run_single_pair(pair_info):
        try:
            # Exécuter le backtest SANS affichage
            results = run_all_backtests(
                pair_info["backtest_pair"],
                start_date,
                timeframes,
                sizing_mode=sizing_mode,
            )
            return pair_info["backtest_pair"], pair_info["real_pair"], results
        except Exception as e:
            logger.error(f"Erreur backtest {pair_info['backtest_pair']}: {e}")
            return pair_info["backtest_pair"], pair_info["real_pair"], []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_pair = {
            executor.submit(run_single_pair, pair): pair for pair in crypto_pairs
        }
        for future in as_completed(future_to_pair):
            try:
                backtest_pair, real_pair, results = future.result()
                results_by_pair[backtest_pair] = {
                    "real_pair": real_pair,
                    "results": results,
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
    best_result = max(results, key=lambda x: x["final_wallet"] - x["initial_wallet"])
    best_profit = best_result["final_wallet"] - best_result["initial_wallet"]

    # Créer le tableau
    table = Table(
        title=f"[bold cyan]Résultats du Backtest — {backtest_pair}[/bold cyan]",
        title_style="bold magenta",
        show_header=True,
        header_style="bold green",
        border_style="bright_yellow",
        expand=False,
        width=120,
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
        profit = result["final_wallet"] - result["initial_wallet"]
        profit_color = "bold green" if profit > 0 else "bold red"
        table.add_row(
            f"[cyan]{result['timeframe']}[/cyan]",
            f"[blue]{result['ema_periods'][0]} / {result['ema_periods'][1]}[/blue]",
            f"[magenta]{result['scenario']}[/magenta]",
            f"[{profit_color}]{profit:,.2f}[/{profit_color}]",
            f"[yellow]{result['final_wallet']:,.2f}[/yellow]",
            f"{len(result['trades'])}",
            f"[red]{result['max_drawdown']*100:.2f}%[/red]",
            f"[cyan]{result['win_rate']:.2f}%[/cyan]",
        )

    # Afficher clairement la periode du backtest AVANT le panel
    period_info = f"[bold yellow]{'='*230}[/bold yellow]\n[bold yellow]BACKTEST PERIOD: 5 YEARS ({start_date_str} - {end_date_str})[/bold yellow]\n[bold yellow]{'='*230}[/bold yellow]"
    console.print("\n")
    console.print(period_info)

    # Résumé du meilleur
    best_panel = Panel(
        f"[bold cyan]Backtest Period[/bold cyan]: 5 years ({start_date_str} - {end_date_str})\n"
        f"[bold green]Meilleur scénario[/bold green]: [magenta]{best_result['scenario']}[/magenta]\n"
        f"[bold cyan]Timeframe[/bold cyan]: {best_result['timeframe']}\n"
        f"[bold blue]EMA[/bold blue]: {best_result['ema_periods'][0]} / {best_result['ema_periods'][1]}\n"
        f"[bold yellow]Profit[/bold yellow]: ${best_profit:,.2f}\n"
        f"[bold red]Max Drawdown[/bold red]: {best_result['max_drawdown']*100:.2f}%\n"
        f"[bold cyan]Win Rate[/bold cyan]: {best_result['win_rate']:.2f}%",
        title=f"[bold green]*** MEILLEUR RESULTAT — {backtest_pair} ***[/bold green]",
        border_style="green",
        padding=(1, 2),
    )

    console.print("\n")
    console.print(best_panel)
    console.print(table)
    console.print("\n" + "=" * 120 + "\n")


###############################################################
#                                                             #
#              *** STRATEGIE EN MARCHE REEL ***               #
#                                                             #
###############################################################


def generate_buy_condition_checker(best_params):
    """
    Génère une fonction de vérification des conditions d'achat
    qui reflète EXACTEMENT le backtest gagnant.
    """

    def check_buy_signal(row, usdc_balance: float):
        # Affichage diagnostic RSI/StochRSI supprimé (aucun print en temps réel)
        """
        Vérifie si les conditions d'achat sont remplies.
        Retourne (is_buy_signal, detailed_reason)
        """
        if usdc_balance <= 0:
            return False, "Solde USDC insuffisant"

        # Condition de base : EMA1 > EMA2 + StochRSI < 0.8
        ema_condition = row["ema1"] > row["ema2"]
        stoch_condition = row["stoch_rsi"] < 0.8

        if not ema_condition:
            return False, f"EMA1 ({row['ema1']:.2f}) <= EMA2 ({row['ema2']:.2f})"
        if not stoch_condition:
            return False, f"StochRSI ({row['stoch_rsi']:.4f}) >= 0.8"

        # Conditions additionnelles selon le scénario
        scenario = best_params.get("scenario", "StochRSI")

        if scenario == "StochRSI_SMA" and "sma_long" in row:
            sma_long = best_params.get("sma_long", 200)
            if row["close"] <= row["sma_long"]:
                return (
                    False,
                    f"Prix ({row['close']:.4f}) <= SMA{sma_long} ({row['sma_long']:.4f})",
                )

        if scenario == "StochRSI_ADX" and "adx" in row:
            adx_threshold = 25
            if row["adx"] <= adx_threshold:
                return False, f"ADX ({row['adx']:.2f}) <= {adx_threshold}"

        if scenario == "StochRSI_TRIX" and "TRIX_HISTO" in row:
            if row["TRIX_HISTO"] <= 0:
                return False, f"TRIX_HISTO ({row['TRIX_HISTO']:.6f}) <= 0"

        # Toutes les conditions sont remplies
        return True, "[OK] Signal d'achat valide"

    return check_buy_signal


def generate_sell_condition_checker(best_params):
    """
    Génère une fonction de vérification des conditions de vente
    qui reflète EXACTEMENT le backtest gagnant.
    """

    def check_sell_signal(
        row,
        coin_balance: float,
        entry_price,
        current_price: float,
        atr_value: float,
    ):
        """
        Vérifie si les conditions de vente sont remplies.
        Retourne (is_sell_signal, sell_reason)
        - sell_reason peut être : 'SIGNAL', 'STOP-LOSS', 'TRAILING-STOP', None
        """
        if coin_balance <= 0 or entry_price is None:
            return False, None

        atr_multiplier = 5.0  # config.atr_multiplier

        # Sécurisation des entrées
        if entry_price is None or current_price is None or atr_value is None:
            return False, None  # Impossible de calculer les stops

        # Calcul du stop-loss
        stop_loss = entry_price - 3 * atr_value

        # Correction trailing stop : max_price doit mémoriser le plus haut atteint depuis l'entrée
        if (
            "max_price" not in pair_state
            or pair_state["max_price"] is None
            or pair_state["max_price"] < entry_price
        ):
            pair_state["max_price"] = entry_price
        if current_price > pair_state["max_price"]:
            pair_state["max_price"] = current_price
        max_price = pair_state["max_price"]
        trailing_stop = max_price - (atr_multiplier * atr_value)

        # Vérification des stops
        if current_price < stop_loss:
            return True, "STOP-LOSS"

        if current_price < trailing_stop:
            return True, "TRAILING-STOP"

        # Condition de signal de vente : EMA2 > EMA1 + StochRSI > 0.2 (base commune)
        ema_condition = row["ema2"] > row["ema1"]
        stoch_condition = row["stoch_rsi"] > 0.2

        if not (ema_condition and stoch_condition):
            return False, None

        # Conditions additionnelles selon le scénario (filtres supplémentaires pour vente)
        scenario = best_params.get("scenario", "StochRSI")

        # Pour tous les scénarios : si signal de base OK + conditions additionnelles spécifiques
        if scenario == "StochRSI_SMA" and "sma_long" in row:
            # Vendre seulement si prix < SMA (confirmation supplémentaire)
            # Sinon garder la position
            pass  # Laisser la logique de base : juste EMA + Stoch

        if scenario == "StochRSI_ADX" and "adx" in row:
            # Vendre si ADX < 20 (force faible, meilleur moment pour vendre)
            # Ou garder si ADX > 20
            pass  # Laisser la logique de base pour cohérence backtest

        if scenario == "StochRSI_TRIX" and "TRIX_HISTO" in row:
            # Vendre si TRIX_HISTO <= 0 (croisement négatif)
            if row["TRIX_HISTO"] <= 0:
                return True, "SIGNAL"  # Signal amélioré avec TRIX

        # Signal de vente confirmé
        return True, "SIGNAL"

    return check_sell_signal


def sync_windows_silently():
    """Synchronise Windows silencieusement si privilèges admin disponibles."""

    try:
        # Vérifier les privilèges admin
        if not check_admin_privileges():
            logger.debug("Pas de privilèges admin - synchronisation ignorée")
            return False

        logger.info("Synchronisation Windows silencieuse en cours...")

        # Commandes de synchronisation directes (sans popup)
        commands = [
            ["net", "stop", "w32time"],
            ["net", "start", "w32time"],
            [
                "w32tm",
                "/config",
                "/manualpeerlist:time.windows.com,0x1",
                "/syncfromflags:manual",
                "/reliable:yes",
            ],
            ["w32tm", "/config", "/update"],
            ["w32tm", "/resync", "/force"],
        ]

        success_count = 0
        for cmd in commands:
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=15,
                    text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if result.returncode == 0:
                    success_count += 1
                    logger.debug(f"Commande réussie: {' '.join(cmd)}")
            except Exception as e:
                logger.debug(f"Erreur commande {' '.join(cmd)}: {e}")
                continue

        if success_count >= 3:
            logger.info("Synchronisation Windows silencieuse: REUSSIE")
            return True
        else:
            logger.warning(f"Synchronisation partielle: {success_count}/5")
            return False

    except Exception as e:
        logger.debug(f"Erreur synchronisation silencieuse: {e}")
        return False


def init_timestamp_solution():
    """Initialisation ULTRA ROBUSTE de la synchronisation."""
    try:
        logger.info("=== INITIALISATION SYNCHRO ULTRA ROBUSTE ===")

        # Utiliser la BONNE méthode (compatibilité assurée)
        if hasattr(client, "_perform_ultra_robust_sync"):
            client._perform_ultra_robust_sync()
        elif hasattr(client, "_sync_server_time_robust"):
            client._sync_server_time_robust()
        else:
            client._sync_server_time()

        # Test de validation
        if hasattr(client, "_get_ultra_safe_timestamp"):
            test_ts = client._get_ultra_safe_timestamp()
        else:
            test_ts = client._get_synchronized_timestamp()

        server_ts = client.get_server_time()["serverTime"]
        diff = test_ts - server_ts

        logger.info(f"VALIDATION: diff={diff}ms")

        if abs(diff) < 1000:
            logger.info("SYNCHRONISATION PARFAITE - Prêt pour le trading")
        else:
            logger.info(f"SYNCHRONISATION STABLE: {diff}ms")

        return True

    except Exception as e:
        logger.error(f"Échec initialisation: {e}")
        logger.info("Fallback robuste activé")
        return True  # Continuer même en cas d'erreur


def check_network_connectivity() -> bool:
    """Vérifie la connectivité réseau et tente de la rétablir."""

    try:
        # Test de connectivité basique
        socket.create_connection(("8.8.8.8", 53), timeout=5)
        return True
    except (socket.error, OSError):
        logger.warning("Perte de connectivité réseau détectée")

        try:
            # Tenter de réactiver la connexion réseau
            logger.info("Tentative de réactivation de la connexion réseau...")

            # Flush DNS
            subprocess.run(["ipconfig", "/flushdns"], capture_output=True, timeout=10)

            # Renouveler l'IP
            subprocess.run(["ipconfig", "/release"], capture_output=True, timeout=10)
            time.sleep(2)
            subprocess.run(["ipconfig", "/renew"], capture_output=True, timeout=15)

            # Attendre un peu
            time.sleep(5)

            # Re-tester
            socket.create_connection(("8.8.8.8", 53), timeout=10)
            logger.info("Connexion réseau rétablie")
            return True

        except Exception as e:
            logger.error(f"Impossible de rétablir la connexion: {e}")
            return False


def _generate_client_order_id(prefix: str = "bot") -> str:
    """Generate a reasonably unique client order id to allow idempotent checks across retries."""
    return f"{prefix}-{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}"


def compute_position_size_by_risk(
    equity: float,
    atr_value: float,
    entry_price: float,
    risk_pct: float = None,
    stop_atr_multiplier: float = None,
) -> float:
    """Calcule la taille de position (en unité coin) basée sur un risque fixe en $.

    - equity: montant total disponible en quote currency (ex: USDC)
    - atr_value: valeur ATR (en quote currency)
    - entry_price: prix d'entrée prévu (en quote currency par coin)
    - risk_pct: fraction du capital à risquer (ex: 0.01 pour 1%). Si None utilise config.risk_per_trade
    - stop_atr_multiplier: multiple d'ATR pour placer le stop (si None utilise config.atr_stop_multiplier)

    Retourne la quantité de coin à acheter (float). Ne dépasse pas l'équivalent en cash (le caller doit vérifier la disponibilité).
    """
    try:
        if risk_pct is None:
            risk_pct = config.risk_per_trade
        if stop_atr_multiplier is None:
            stop_atr_multiplier = config.atr_stop_multiplier

        if (
            atr_value is None
            or atr_value <= 0
            or entry_price is None
            or entry_price <= 0
        ):
            return 0.0

        stop_distance = stop_atr_multiplier * float(atr_value)
        if stop_distance <= 0:
            return 0.0

        risk_amount = float(equity) * float(risk_pct)
        if risk_amount <= 0:
            return 0.0

        # Quantité de coin correspondant au risque demandé
        qty_coin = risk_amount / stop_distance
        if qty_coin <= 0:
            return 0.0
        return float(qty_coin)
    except Exception:
        return 0.0


def compute_position_size_fixed_notional(
    equity: float, notional_per_trade_usd: float = None, entry_price: float = None
) -> float:
    """Calcule la taille de position avec une allocation fixe en USD par trade.

    - equity: montant total disponible en quote currency (ex: USDC) - non utilisé directement pour le sizing
    - notional_per_trade_usd: montant USD fixe à investir par trade (ex: 1000 USD). Si None, utilise 10% de equity.
    - entry_price: prix d'entrée prévu (en quote currency par coin)

    Retourne la quantité de coin à acheter (float).
    """
    try:
        if entry_price is None or entry_price <= 0:
            return 0.0

        if notional_per_trade_usd is None:
            # Par défaut: 10% de l'equity
            notional_per_trade_usd = max(100.0, equity * 0.1)

        if notional_per_trade_usd <= 0:
            return 0.0

        # Quantité simple: montant / prix
        qty_coin = float(notional_per_trade_usd) / float(entry_price)
        if qty_coin <= 0:
            return 0.0
        return float(qty_coin)
    except Exception:
        return 0.0


def compute_position_size_volatility_parity(
    equity: float,
    atr_value: float,
    entry_price: float,
    target_volatility_pct: float = 0.02,
) -> float:
    """Calcule la taille de position pour maintenir une volatilité fixe du P&L par trade.

    - equity: montant total disponible en quote currency
    - atr_value: valeur ATR (mesure de volatilité)
    - entry_price: prix d'entrée prévu
    - target_volatility_pct: volatilité cible en fraction (ex: 0.02 = 2% de profit/loss potentiel par trade)

    Idée: si ATR augmente, réduire la taille pour garder la volatilité de P&L stable.
    Taille = (equity * target_volatility_pct) / (ATR * entry_price)

    Retourne la quantité de coin à acheter (float).
    """
    try:
        if (
            atr_value is None
            or atr_value <= 0
            or entry_price is None
            or entry_price <= 0
        ):
            return 0.0

        if target_volatility_pct is None or target_volatility_pct <= 0:
            target_volatility_pct = 0.02  # 2% par défaut

        # P&L volatility = position_size * atr_value * entry_price
        # On veut: position_size * atr_value * entry_price = equity * target_volatility_pct
        # Donc: position_size = (equity * target_volatility_pct) / (atr_value * entry_price)

        volatility_amount = float(equity) * float(target_volatility_pct)
        qty_coin = volatility_amount / (float(atr_value) * float(entry_price))

        if qty_coin <= 0:
            return 0.0
        return float(qty_coin)
    except Exception:
        return 0.0


def _direct_market_order(
    symbol: str,
    side: str,
    quoteOrderQty: float = None,
    quantity: float = None,
    client_id: str = None,
) -> Dict[str, Any]:
    """Appel API REST direct pour éviter le bug 'Duplicate recvWindow' du wrapper Binance.

    Cette fonction contourne le problème du wrapper python-binance qui peut passer
    recvWindow plusieurs fois, causant l'erreur -1101.
    """
    # 1. Construction manuelle de la chaîne de requête dans l'ordre exact requis par Binance (paramètres minimaux)
    # Already imported at the top: requests, hmac, hashlib, time
    timestamp = int(time.time() * 1000)
    params_order = [
        ("symbol", symbol),
        ("side", side),
        ("type", "MARKET"),
        ("quantity", f"{quantity}" if quantity is not None else None),
        (
            "quoteOrderQty",
            f"{float(quoteOrderQty):.2f}" if quoteOrderQty is not None else None,
        ),
        ("timestamp", int(timestamp)),
    ]
    # Retirer les None
    params_order = [(k, v) for k, v in params_order if v is not None]
    # Construction manuelle de la query string dans l'ordre
    query_string = "&".join([f"{k}={v}" for k, v in params_order])
    # 2. Génération de la signature
    signature = hmac.new(
        client.api_secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    # 3. Ajout de la signature à la query string
    query_string_with_sig = query_string + f"&signature={signature}"
    # 4. Construction des headers
    headers = {
        "X-MBX-APIKEY": client.api_key,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    # 5. Vérification du timestamp serveur (log l'écart)
    try:
        server_time_resp = requests.get(
            "https://api.binance.com/api/v3/time", timeout=5
        )
        server_time = server_time_resp.json().get("serverTime")
        timestamp_diff = int(timestamp) - int(server_time)
        logger.error(
            f"[DEBUG TIMESTAMP] Différence timestamp local-serveur: {timestamp_diff} ms (local={timestamp}, serveur={server_time})"
        )
    except Exception as e:
        logger.error(
            f"[DEBUG TIMESTAMP] Impossible de récupérer le timestamp serveur: {e}"
        )
    # 6. Envoi de la requête POST avec uniquement les paramètres minimaux et dans l'ordre
    url = "https://api.binance.com/api/v3/order"
    logger.error(f"[DEBUG ORDER] Query string envoyée: {query_string_with_sig}")
    logger.error(f"[DEBUG ORDER] Headers envoyés: {headers}")
    logger.error(f"[DEBUG ORDER] URL appelée: {url}")
    try:
        response = requests.post(
            url, data=query_string_with_sig, headers=headers, timeout=10
        )
        logger.error(f"[DEBUG ORDER] Status code: {response.status_code}")
        logger.error(f"[DEBUG ORDER] Response text: {response.text}")
        if response.status_code != 200:
            try:
                error_data = response.json()
                error_code = error_data.get("code", "UNKNOWN")
                error_msg = error_data.get("msg", "Unknown error")
                logger.error(
                    f"[DEBUG ORDER] Erreur API Binance: code={error_code}, msg={error_msg}"
                )
                # Envoi d'une alerte mail en cas d'erreur d'ordre
                send_trading_alert_email(
                    subject=f"[BOT CRYPTO] ERREUR EXECUTION {side.upper()} ORDER",
                    body_main=f"Erreur lors de l'execution de l'ordre {side.upper()} : {error_code} - {error_msg}\n\nParams : {params_order}",
                    client=client,
                )
                raise BinanceAPIException(response, error_code, error_msg)
            except (ValueError, KeyError):
                logger.error(f"[DEBUG ORDER] Erreur HTTP non-JSON: {response.text}")
                response.raise_for_status()
        result = response.json()
        # Envoi d'une alerte mail pour toute transaction exécutée
        send_trading_alert_email(
            subject=f"[BOT CRYPTO] {side.upper()} ORDER EXECUTE",
            body_main=f"Ordre {side.upper()} exécuté avec succès.\n\nParams : {params_order}\nRéponse : {result}",
            client=client,
        )
        return result
    except Exception as e:
        logger.error(f"[DEBUG ORDER] Exception Python lors de l'appel API: {e}")
        # Envoi d'une alerte mail pour l'exception
        try:
            send_trading_alert_email(
                subject=f"[BOT CRYPTO] EXCEPTION {side.upper()} ORDER",
                body_main=f"Exception lors de l'appel API {side.upper()} : {e}\n\nParams : {params_order}",
                client=client,
            )
        except Exception as mail_exc:
            logger.error(f"[EXCEPTION] Echec envoi mail d'alerte: {mail_exc}")
        raise


def safe_market_buy(
    symbol: str, quoteOrderQty: float, max_retries: int = 4
) -> Dict[str, Any]:
    """Place a market BUY by quote amount with idempotency/retry and safety checks.

    - Uses `newClientOrderId` for idempotency and duplicate detection
    - Retries on network errors, timestamp errors (-1021), rate limits
    - After an exception, queries the order by `origClientOrderId` to confirm placement
    - Uses direct REST API call to avoid 'Duplicate recvWindow' bug
    """
    client_id = _generate_client_order_id("buy")
    attempt = 0
    delay = 1.0
    last_exc = None

    while attempt < max_retries:
        try:
            # CRITICAL FIX: Use direct REST API call instead of wrapper to avoid duplicate recvWindow
            res = _direct_market_order(
                symbol=symbol,
                side="BUY",
                quoteOrderQty=quoteOrderQty,
                client_id=client_id,
            )
            logger.info(
                f"Market buy placed: {symbol} quote={quoteOrderQty} clientId={client_id}"
            )
            return res
        except Exception as e:
            last_exc = e
            msg = str(e)
            logger.warning(
                f"safe_market_buy attempt {attempt+1} failed for {symbol}: {msg}"
            )

            # Check whether order actually exists by origClientOrderId
            try:
                order = client.get_order(symbol=symbol, origClientOrderId=client_id)
                if order and order.get("status"):
                    logger.info(
                        f"Order found after exception by clientId={client_id}: status={order.get('status')}"
                    )
                    return order
            except Exception as e2:
                logger.debug(f"safe_market_buy: get_order by clientId failed: {e2}")

            # Handle specific error types
            lowered = msg.lower()
            if "duplicate values for parameter" in lowered or "duplicate" in lowered:
                logger.warning(
                    "Detected duplicate parameter error - attempting resilient recovery"
                )
                # First, try to confirm whether the order was actually placed by querying by origClientOrderId
                for attempt_check in range(3):
                    try:
                        order = client.get_order(
                            symbol=symbol, origClientOrderId=client_id
                        )
                        if order and order.get("status"):
                            logger.info(
                                f"Order found after duplicate-param error by clientId={client_id}: status={order.get('status')}"
                            )
                            return order
                    except Exception as _get_ex:
                        logger.debug(
                            f"get_order attempt {attempt_check+1} after duplicate error failed: {_get_ex}"
                        )
                    time.sleep(0.5 * (attempt_check + 1))

                # Si l'ordre n'existe pas, forcer une resync complète et attendre avant retry
                logger.warning(
                    "Forcing complete resync after duplicate parameter error"
                )
                try:
                    if hasattr(client, "_perform_ultra_robust_sync"):
                        client._perform_ultra_robust_sync()
                    elif hasattr(client, "_sync_server_time_robust"):
                        client._sync_server_time_robust()
                    else:
                        client._sync_server_time()
                except Exception:
                    pass
                time.sleep(2.0)  # Attendre 2 secondes avant retry
                attempt += 1
                delay *= 2
                continue

            # Timestamp / -1021 handling: resync and retry
            if "-1021" in lowered or "timestamp" in lowered or "server time" in lowered:
                logger.info(
                    "Timestamp-related error detected, performing full sync and retrying"
                )
                try:
                    client._perform_ultra_robust_sync()
                except Exception:
                    pass
                time.sleep(delay + random.random())
                attempt += 1
                delay *= 2
                continue

            # Rate limit / 429 handling
            if "rate limit" in lowered or "429" in lowered or "too many" in lowered:
                logger.warning("Rate limit detected, backing off before retry")
                time.sleep(delay + random.random())
                attempt += 1
                delay *= 2
                continue

            # Network/connectivity errors: backoff
            if any(
                x in lowered
                for x in [
                    "connection",
                    "timed out",
                    "timeout",
                    "nameresolutionerror",
                    "getaddrinfo",
                ]
            ):
                logger.warning("Network error detected, backing off and retrying")
                time.sleep(delay + random.random())
                attempt += 1
                delay *= 2
                continue

            # Other errors: break after a short backoff
            time.sleep(delay + random.random())
            attempt += 1
            delay *= 2

    # Final check before giving up
    try:
        order = client.get_order(symbol=symbol, origClientOrderId=client_id)
        if order and order.get("status"):
            return order
    except Exception:
        pass

    logger.error(f"safe_market_buy failed after {max_retries} attempts for {symbol}")
    raise last_exc


def safe_market_sell(
    symbol: str, quantity: float, max_retries: int = 4
) -> Dict[str, Any]:
    """Place a market SELL with idempotent retries and safety checks (same approach as buy).

    Uses parent's order_market_sell() which handles parameters correctly.
    """
    client_id = _generate_client_order_id("sell")
    attempt = 0
    delay = 1.0
    last_exc = None

    while attempt < max_retries:
        try:
            # CRITICAL FIX: Use direct REST API call instead of wrapper to avoid duplicate recvWindow
            res = _direct_market_order(
                symbol=symbol, side="SELL", quantity=quantity, client_id=client_id
            )
            logger.info(
                f"Market sell placed: {symbol} qty={quantity} clientId={client_id}"
            )
            return res
        except Exception as e:
            last_exc = e
            msg = str(e)
            logger.warning(
                f"safe_market_sell attempt {attempt+1} failed for {symbol}: {msg}"
            )

            try:
                order = client.get_order(symbol=symbol, origClientOrderId=client_id)
                if order and order.get("status"):
                    logger.info(
                        f"Order found after exception by clientId={client_id}: status={order.get('status')}"
                    )
                    return order
            except Exception as e2:
                logger.debug(f"safe_market_sell: get_order by clientId failed: {e2}")

            lowered = msg.lower()
            if "duplicate values for parameter" in lowered or "duplicate" in lowered:
                logger.warning(
                    "Detected duplicate parameter error - attempting resilient recovery"
                )
                # First, try to confirm whether the order was actually placed by querying by origClientOrderId
                for attempt_check in range(3):
                    try:
                        order = client.get_order(
                            symbol=symbol, origClientOrderId=client_id
                        )
                        if order and order.get("status"):
                            logger.info(
                                f"Order found after duplicate-param error by clientId={client_id}: status={order.get('status')}"
                            )
                            return order
                    except Exception as _get_ex:
                        logger.debug(
                            f"get_order attempt {attempt_check+1} after duplicate error failed: {_get_ex}"
                        )
                    time.sleep(0.5 * (attempt_check + 1))

                # Si l'ordre n'existe pas, forcer une resync complète et attendre avant retry
                logger.warning(
                    "Forcing complete resync after duplicate parameter error"
                )
                try:
                    if hasattr(client, "_perform_ultra_robust_sync"):
                        client._perform_ultra_robust_sync()
                    elif hasattr(client, "_sync_server_time_robust"):
                        client._sync_server_time_robust()
                    else:
                        client._sync_server_time()
                except Exception:
                    pass
                time.sleep(2.0)  # Attendre 2 secondes avant retry
                attempt += 1
                delay *= 2
                continue

            if "-1021" in lowered or "timestamp" in lowered or "server time" in lowered:
                try:
                    client._perform_ultra_robust_sync()
                except Exception:
                    pass
                time.sleep(delay + random.random())
                attempt += 1
                delay *= 2
                continue

            if "rate limit" in lowered or "429" in lowered or "too many" in lowered:
                time.sleep(delay + random.random())
                attempt += 1
                delay *= 2
                continue

            if any(
                x in lowered
                for x in [
                    "connection",
                    "timed out",
                    "timeout",
                    "nameresolutionerror",
                    "getaddrinfo",
                ]
            ):
                time.sleep(delay + random.random())
                attempt += 1
                delay *= 2
                continue

            time.sleep(delay + random.random())
            attempt += 1
            delay *= 2

    try:
        order = client.get_order(symbol=symbol, origClientOrderId=client_id)
        if order and order.get("status"):
            return order
    except Exception:
        pass

    logger.error(f"safe_market_sell failed after {max_retries} attempts for {symbol}")
    raise last_exc


def get_sniper_entry_price(
    pair_symbol: str, signal_price: float, max_wait_candles: int = 4
) -> float:
    """Optimise le prix d'entrée en utilisant la timeframe 15min pour des entrées 'sniper'."""
    try:
        # Récupérer les données 15min récentes (24h pour avoir assez de données)
        df_15m = fetch_historical_data(
            pair_symbol, Client.KLINE_INTERVAL_15MINUTE, "1 day ago"
        )
        if df_15m.empty or len(df_15m) < 20:
            return signal_price

        # Prendre les dernières bougies 15min
        recent_candles = df_15m.tail(max_wait_candles)

        # Logique simple : chercher le prix le plus bas dans la fenêtre récente
        # qui reste proche du signal (max 2% d'écart)
        best_price = signal_price

        for _, candle in recent_candles.iterrows():
            candle_price = candle["low"]  # Utiliser le plus bas de la bougie

            # Vérifier que le prix reste dans une fourchette acceptable (max 2% d'écart)
            price_diff_pct = abs(candle_price - signal_price) / signal_price * 100

            if price_diff_pct <= 2.0 and candle_price < best_price:
                best_price = candle_price

        # Calculer l'amélioration (logs désactivés pendant backtest)
        if best_price < signal_price:
            improvement = (signal_price - best_price) / signal_price * 100
            logger.debug(
                f"Optimisation sniper: amélioration de {improvement:.2f}% (prix: {best_price:.8f})"
            )

        return best_price

    except Exception as e:
        logger.debug(f"Erreur optimisation entree sniper: {e}")
        return signal_price


def extract_coin_from_pair(real_trading_pair: str) -> Tuple[str, str]:
    """Extrait le symbole de la crypto et la devise de cotation (USDC par defaut)."""
    quote_currencies = ["USDC"]  # Ajoutez d'autres devises de cotation si necessaire
    for quote in quote_currencies:
        if real_trading_pair.endswith(quote):
            coin_symbol = real_trading_pair[
                : -len(quote)
            ]  # Retire la quote_currency pour obtenir le coin
            return coin_symbol, quote
    raise ValueError(
        f"Impossible de determiner le coin ou la monnaie de cotation pour {real_trading_pair}."
    )


@retry_with_backoff(max_retries=3, base_delay=1.0)
def get_last_sell_trade_usdc(
    real_trading_pair: str,
) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    try:
        coin_symbol, quote_currency = extract_coin_from_pair(real_trading_pair)
        trades = client.get_my_trades(
            symbol=real_trading_pair, limit=100
        )  # Augmenter la limite
        logger.debug(
            f"Trades recuperes pour {coin_symbol}/{quote_currency} : {len(trades) if trades else 0} trades"
        )
        if not trades:
            logger.warning("Aucun trade trouve pour cette paire.")
            return None, None, None

        aggregated_sells = {}
        aggregated_commissions = {}
        commission_assets = {}
        for trade in trades:
            if (
                "isBuyer" not in trade
                or "quoteQty" not in trade
                or "orderId" not in trade
            ):
                logger.error(f"Trade mal forme : {trade}")
                continue
            if (
                not trade["isBuyer"] and float(trade["quoteQty"]) > 0
            ):  # Ignorer quoteQty=0
                order_id = trade["orderId"]
                quote_qty = float(trade["quoteQty"])
                commission = float(trade.get("commission", 0))
                commission_asset = trade.get("commissionAsset", "UNKNOWN")
                if order_id not in aggregated_sells:
                    aggregated_sells[order_id] = 0.0
                    aggregated_commissions[order_id] = 0.0
                    commission_assets[order_id] = commission_asset
                aggregated_sells[order_id] += quote_qty
                aggregated_commissions[order_id] += commission

        for trade in reversed(trades):
            oid = trade["orderId"]
            if oid in aggregated_sells:
                total_amount = aggregated_sells[oid]
                total_fee = aggregated_commissions[oid]
                fee_asset = commission_assets[oid]
                logger.debug(
                    f"Derniere vente : Order ID={oid}, Montant={total_amount:.8f} {quote_currency}, Frais={total_fee:.8f} {fee_asset}"
                )
                return total_amount, total_fee, fee_asset
        logger.info("Aucune vente valide trouvee.")
        return None, None, None
    except Exception as e:
        logger.error(f"Erreur recuperation derniere vente : {e}")
        return None, None, None


def check_if_order_executed(orders: List[Dict], order_type: str) -> bool:
    """Verifie si un ordre donne (achat ou vente) a deja ete execute."""
    if orders:
        last_order = orders[-1]  # Dernier ordre execute
        last_order_type = last_order["side"]  # "BUY" ou "SELL"
        last_order_status = last_order["status"]  # Statut de l'ordre (ex. 'FILLED')

        if last_order_status == "FILLED" and last_order_type == order_type:
            return True  # Un ordre a deja ete execute

    return False


def execute_scheduled_trading(
    real_trading_pair: str,
    time_interval: str,
    best_params: Dict[str, Any],
    backtest_pair: str,
):
    """Wrapper pour les exécutions planifiées avec affichage complet (identique au démarrage)."""
    try:
        # === MESSAGE VISUEL DE DEMARRAGE ===
        logger.info(
            f"[SCHEDULED] DEBUT execution planifiee - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        console.print("\n" + "=" * 120)
        console.print(
            f"[bold cyan][RUN] EXECUTION PLANIFIEE - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/bold cyan]"
        )
        console.print(
            f"[bold yellow]Paire: {backtest_pair} -> {real_trading_pair} | Timeframe: {time_interval}[/bold yellow]"
        )
        console.print("=" * 120 + "\n")

        # Force flush de la console
        import sys

        sys.stdout.flush()
        logger.info(f"[SCHEDULED] Header affiché, debut des backtests...")

        # Re-faire le backtest pour obtenir les paramètres les plus à jour
        logger.info(
            f"[SCHEDULED] Re-backtest de {backtest_pair} pour obtenir les paramètres les plus à jour..."
        )

        # Calculer les dates dynamiquement
        today = datetime.today()
        dynamic_start_date = (today - timedelta(days=config.backtest_days)).strftime(
            "%d %B %Y"
        )
        logger.info(
            f"[SCHEDULED] Backtest dates: {dynamic_start_date} -> {today.strftime('%d %B %Y')}"
        )

        # Re-exécuter le backtest et AFFICHER les résultats
        logger.info(f"[SCHEDULED] Lancement des backtests...")
        try:
            # Utiliser la liste de timeframes déjà définie en global (pas d'attribut config.timeframes)
            backtest_results = run_all_backtests(
                backtest_pair,
                dynamic_start_date,
                timeframes,
                sizing_mode=getattr(config, "sizing_mode", "baseline"),
            )
        except Exception as backtest_err:
            logger.error(f"[SCHEDULED] ERREUR backtest {backtest_pair}: {backtest_err}")
            logger.error(f"[SCHEDULED] Traceback backtest: {traceback.format_exc()}")
            console.print(
                f"[red][SCHEDULED] Erreur backtest {backtest_pair} : {backtest_err}[/red]"
            )
            return

        if backtest_results:
            logger.info(
                f"[SCHEDULED] {len(backtest_results)} resultats de backtest recus"
            )

            # Identifier le meilleur résultat basé sur le profit
            best_result = max(
                backtest_results, key=lambda x: x["final_wallet"] - x["initial_wallet"]
            )
            best_profit = best_result["final_wallet"] - best_result["initial_wallet"]

            logger.info(
                f"[SCHEDULED] Meilleur resultat: {best_result['scenario']} sur {best_result['timeframe']} | Profit: ${best_profit:,.2f}"
            )

            # === EXPORT CSV DES TRADES ===

            try:
                import csv, sys

                print("[DEBUG EXPORT] Début export CSV des trades...")
                trades = best_result.get("trades", [])
                if hasattr(trades, "to_dict"):
                    trades_list = trades.to_dict(orient="records")
                else:
                    trades_list = trades if isinstance(trades, list) else []
                print(
                    f"[DEBUG EXPORT] Nombre de trades à exporter : {len(trades_list)}"
                )
                if trades_list and len(trades_list) > 0:
                    csv_file = f"trades_{backtest_pair}_{best_result['timeframe']}.csv"
                    csv_path = os.path.abspath(csv_file)
                    print(f"[DEBUG EXPORT] Chemin du fichier CSV : {csv_path}")
                    with open(csv_path, mode="w", newline="", encoding="utf-8") as f:
                        writer = csv.DictWriter(f, fieldnames=trades_list[0].keys())
                        writer.writeheader()
                        writer.writerows(trades_list)
                    logger.info(f"Export CSV des trades effectué : {csv_path}")
                    console.print(f"[green]Export CSV des trades : {csv_path}[/green]")
                    print(f"[EXPORT] Fichier CSV généré : {csv_path}", flush=True)
                else:
                    logger.warning("Aucun trade à exporter pour ce backtest.")
                    console.print(
                        "[yellow]Aucun trade à exporter pour ce backtest.[/yellow]"
                    )
                    print(
                        "[EXPORT] Aucun trade à exporter pour ce backtest.", flush=True
                    )
                sys.stdout.flush()
            except Exception as export_err:
                logger.error(f"Erreur export CSV trades: {export_err}")
                console.print(f"[red]Erreur export CSV trades: {export_err}[/red]")
                print(f"[EXPORT ERROR] {export_err}", flush=True)

            # === AFFICHAGE PANEL - MEILLEUR RESULTAT ===
            try:
                start_date_obj = today - timedelta(days=config.backtest_days)
                end_date_str = today.strftime("%d %b %Y")
                start_date_str = start_date_obj.strftime("%d %b %Y")

                best_panel = Panel(
                    f"[bold cyan]Backtest Period[/bold cyan]: 5 years ({start_date_str} - {end_date_str})\n"
                    f"[bold green]Meilleur scénario[/bold green]: [magenta]{best_result['scenario']}[/magenta]\n"
                    f"[bold cyan]Timeframe[/bold cyan]: {best_result['timeframe']}\n"
                    f"[bold blue]EMA[/bold blue]: {best_result['ema_periods'][0]} / {best_result['ema_periods'][1]}\n"
                    f"[bold yellow]Profit[/bold yellow]: ${best_profit:,.2f}\n"
                    f"[bold red]Max Drawdown[/bold red]: {best_result['max_drawdown']*100:.2f}%\n"
                    f"[bold cyan]Win Rate[/bold cyan]: {best_result['win_rate']:.2f}%",
                    title=f"[bold green]*** MEILLEUR RESULTAT — {backtest_pair} ***[/bold green]",
                    border_style="green",
                    padding=(1, 2),
                    width=120,
                )
                console.print(best_panel)
                logger.info(f"[SCHEDULED] Panel meilleur resultat affiché")
            except Exception as panel_err:
                logger.error(
                    f"[SCHEDULED] Erreur affichage best_panel: {str(panel_err)}"
                )

            # === AFFICHAGE TABLEAU DES RESULTATS ===
            try:
                logger.info(
                    f"[SCHEDULED] Creation du tableau des {len(backtest_results)} resultats..."
                )
                table = Table(
                    title=f"[bold cyan]Résultats du Backtest — {backtest_pair}[/bold cyan]",
                    title_style="bold magenta",
                    show_header=True,
                    header_style="bold green",
                    border_style="bright_yellow",
                    expand=False,
                    width=120,
                )
                table.add_column("Timeframe", style="dim", justify="center", width=10)
                table.add_column("EMA", style="cyan", justify="center", width=15)
                table.add_column("Scénario", style="magenta", justify="left", width=20)
                table.add_column(
                    "Profit ($)", style="bold green", justify="right", width=15
                )
                table.add_column(
                    "Final Wallet ($)", style="bold yellow", justify="right", width=18
                )
                table.add_column("Trades", style="white", justify="right", width=8)
                table.add_column(
                    "Max Drawdown (%)", style="bold red", justify="right", width=18
                )
                table.add_column(
                    "Win Rate (%)", style="bold cyan", justify="right", width=15
                )

                for result in backtest_results:
                    profit = result["final_wallet"] - result["initial_wallet"]
                    profit_color = "bold green" if profit > 0 else "bold red"
                    table.add_row(
                        f"[cyan]{result['timeframe']}[/cyan]",
                        f"[blue]{result['ema_periods'][0]} / {result['ema_periods'][1]}[/blue]",
                        f"[magenta]{result['scenario']}[/magenta]",
                        f"[{profit_color}]{profit:,.2f}[/{profit_color}]",
                        f"[yellow]{result['final_wallet']:,.2f}[/yellow]",
                        f"{len(result['trades'])}",
                        f"[red]{result['max_drawdown']*100:.2f}%[/red]",
                        f"[cyan]{result['win_rate']:.2f}%[/cyan]",
                    )

                console.print(table)
                logger.info(
                    f"[SCHEDULED] Tableau affiche - {len(backtest_results)} lignes"
                )
                console.print("\n" + "=" * 120 + "\n")
                sys.stdout.flush()
            except Exception as table_err:
                logger.error(f"[SCHEDULED] Erreur affichage table: {str(table_err)}")

            # Mettre à jour best_params avec les derniers résultats du backtest
            scenario_params = {
                "StochRSI": {"stoch_period": 14},
                "StochRSI_SMA": {"stoch_period": 14, "sma_long": 200},
                "StochRSI_ADX": {"stoch_period": 14, "adx_period": 14},
                "StochRSI_TRIX": {
                    "stoch_period": 14,
                    "trix_length": 7,
                    "trix_signal": 15,
                },
            }

            updated_best_params = {
                "timeframe": best_result["timeframe"],
                "ema1_period": best_result["ema_periods"][0],
                "ema2_period": best_result["ema_periods"][1],
                "scenario": best_result["scenario"],
            }
            updated_best_params.update(scenario_params.get(best_result["scenario"], {}))

            # Vérifier si les paramètres ont changé
            if updated_best_params != best_params:
                logger.info(
                    f"[SCHEDULED] CHANGEMENT DETECTE - Anciens params: {best_params}"
                )
                logger.info(f"[SCHEDULED] Nouveaux params: {updated_best_params}")
                best_params = updated_best_params
            else:
                logger.info(f"[SCHEDULED] Parametres inchanges pour {backtest_pair}")
        else:
            logger.warning(
                f"[SCHEDULED] Aucun resultat de backtest pour {backtest_pair}, utilisation des anciens parametres"
            )
            console.print(
                f"[yellow][SCHEDULED] Aucun résultat de backtest pour {backtest_pair} – affichage sauté[/yellow]"
            )

        # === AFFICHAGE PANEL - TRADING EN TEMPS REEL ===
        try:
            logger.info(f"[SCHEDULED] Affichage panel trading...")
            trading_content = (
                f"[dim]Execution automatisee des ordres de trading[/dim]\n\n"
                f"[bold white]Paire de trading    :[/bold white] [bright_yellow]{real_trading_pair}[/bright_yellow]\n"
                f"[bold white]Intervalle temporel :[/bold white] [bright_cyan]{best_params['timeframe']}[/bright_cyan]\n"
                f"[bold white]Configuration EMA   :[/bold white] [bright_green]{best_params['ema1_period']}[/bright_green] / [bright_green]{best_params['ema2_period']}[/bright_green]\n"
                f"[bold white]Indicateur principal:[/bold white] [bright_magenta]{best_params['scenario']}[/bright_magenta]"
            )
            console.print(
                Panel(
                    trading_content,
                    title="[bold bright_cyan]TRADING ALGORITHMIQUE EN TEMPS REEL[/bold bright_cyan]",
                    title_align="center",
                    border_style="bright_cyan",
                    padding=(1, 3),
                    width=120,
                )
            )
            logger.info(
                f"[SCHEDULED] Panel trading affiché, appel execute_real_trades..."
            )
            sys.stdout.flush()
        except Exception as panel_trading_err:
            logger.error(
                f"[SCHEDULED] Erreur affichage panel trading: {str(panel_trading_err)}"
            )

        # Exécuter le trading avec les paramètres mis à jour
        try:
            logger.info(
                f"[SCHEDULED] Appel execute_real_trades avec {best_params['scenario']} sur {best_params['timeframe']}..."
            )
            execute_real_trades(
                real_trading_pair, best_params["timeframe"], best_params, backtest_pair
            )
            logger.info(f"[SCHEDULED] execute_real_trades complété avec succès")
        except Exception as trade_error:
            logger.error(
                f"[SCHEDULED] Erreur dans execute_real_trades: {str(trade_error)}"
            )
            logger.error(f"[SCHEDULED] Traceback: {traceback.format_exc()}")

        # === AFFICHAGE PANEL - SUIVI & PLANIFICATION ===
        logger.info(f"[SCHEDULED] Affichage des informations de suivi...")

        # Assurer l'initialisation par defaut de l'etat de la paire
        pair_state = bot_state.setdefault(backtest_pair, {})
        pair_state.setdefault("last_order_side", None)
        current_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Historique d'exécution
        if pair_state.get("last_run_time"):
            try:
                last_time_obj = datetime.strptime(
                    pair_state["last_run_time"], "%Y-%m-%d %H:%M:%S"
                )
                current_time_obj = datetime.strptime(
                    current_run_time, "%Y-%m-%d %H:%M:%S"
                )
                time_elapsed = current_time_obj - last_time_obj

                history_section = (
                    f"[bold white]Derniere execution  :[/bold white] [bright_cyan]{pair_state['last_run_time']}[/bright_cyan]\n"
                    f"[bold white]Temps ecoule        :[/bold white] [bright_yellow]{time_elapsed}[/bright_yellow]"
                )
            except Exception as time_err:
                logger.debug(f"[SCHEDULED] Erreur parsing temps: {time_err}")
                history_section = f"[bold white]Derniere execution  :[/bold white] [bright_cyan]{pair_state['last_run_time']}[/bright_cyan]"
        else:
            history_section = "[bold bright_green]Premiere execution du systeme de trading automatise[/bold bright_green]"

        # Planification (homogène 2 minutes)
        next_exec = "toutes les 2 minutes (720 exécutions/jour)"
        schedule_section = (
            f"[bold white]Mode de planification:[/bold white] [dim]Homogène et réactif - 2 minutes[/dim]\n"
            f"[bold white]Prochaine execution :[/bold white] [bright_green]{next_exec}[/bright_green]"
        )

        # Mettre à jour l'état
        pair_state["last_run_time"] = current_run_time
        save_bot_state()

        # Afficher le panel de suivi
        logger.info(f"[SCHEDULED] Création et affichage du panel de suivi...")
        try:
            tracking_panel = Panel(
                f"{history_section}\n\n{schedule_section}",
                title="[bold bright_blue]SUIVI D'EXECUTION & PLANIFICATION AUTOMATIQUE[/bold bright_blue]",
                border_style="bright_blue",
                padding=(1, 3),
                width=120,
            )
            console.print(tracking_panel)
            console.print("\n")
            sys.stdout.flush()
            logger.info(
                f"[SCHEDULED] Exécution planifiée COMPLETEE pour {backtest_pair}"
            )
        except Exception as tracking_err:
            logger.error(
                f"[SCHEDULED] Erreur affichage tracking panel: {str(tracking_err)}"
            )

    except Exception as e:
        logger.error(
            f"[SCHEDULED] Erreur GLOBALE execution planifiee {backtest_pair}: {str(e)}"
        )
        logger.error(f"[SCHEDULED] Traceback complet: {traceback.format_exc()}")


from decimal import Decimal, ROUND_DOWN
from datetime import datetime, timezone
import uuid
import json
import time
import logging

logger = logging.getLogger(__name__)


@retry_with_backoff(max_retries=3, base_delay=2.0)
def execute_real_trades(
    real_trading_pair: str,
    time_interval: str,
    best_params: Dict[str, Any],
    backtest_pair: str,
):
    """
    Exécution complète des trades réels avec gestion totale du cycle achat/vente,
    stop-loss, trailing-stop, sniper entry, envoi d'emails d'alerte et affichage console.
    Stratégie d'origine préservée intégralement.
    """
    pair_state = bot_state.setdefault(backtest_pair, {})
    pair_state.setdefault("last_order_side", None)

    # Paramètres stratégiques
    ema1_period = best_params.get("ema1_period")
    ema2_period = best_params.get("ema2_period")
    atr_multiplier = best_params.get(
        "atr_multiplier", getattr(config, "atr_multiplier", 5.0)
    )
    atr_stop_multiplier = getattr(config, "atr_stop_multiplier", 3.0)
    scenario = best_params.get("scenario", "StochRSI")

    try:
        # === COMPTES & SOLDES ===
        account_info = client.get_account()
        coin_symbol, quote_currency = extract_coin_from_pair(real_trading_pair)

        usdc_balance_obj = next(
            (b for b in account_info["balances"] if b["asset"] == "USDC"), None
        )
        usdc_balance = float(usdc_balance_obj["free"]) if usdc_balance_obj else 0.0

        coin_balance_obj = next(
            (b for b in account_info["balances"] if b["asset"] == coin_symbol), None
        )
        coin_balance = float(coin_balance_obj["free"]) if coin_balance_obj else 0.0

        if coin_balance_obj is None:
            return

        # Afficher le panel des soldes :
        # - Si on vérifie les conditions d'achat (pas de position ouverte), on ne passe PAS les infos d'achat
        # - Si on vérifie les conditions de vente (position ouverte), on passe les infos d'achat
        if pair_state.get("last_order_side") == "BUY":
            last_buy_price = pair_state.get("entry_price")
            atr_at_entry = pair_state.get("atr_at_entry")
            display_account_balances_panel(
                account_info,
                coin_symbol,
                quote_currency,
                client,
                console,
                last_buy_price=last_buy_price,
                atr_at_entry=atr_at_entry,
            )
        else:
            display_account_balances_panel(
                account_info,
                coin_symbol,
                quote_currency,
                client,
                console,
                last_buy_price=None,
                atr_at_entry=None,
            )

        # === DONNÉES & INDICATEURS ===
        df = fetch_historical_data(
            real_trading_pair, time_interval, start_date, force_refresh=True
        )
        df = universal_calculate_indicators(
            df,
            ema1_period,
            ema2_period,
            stoch_period=best_params.get("stoch_period", 14),
            sma_long=best_params.get("sma_long"),
            adx_period=best_params.get("adx_period"),
            trix_length=best_params.get("trix_length"),
            trix_signal=best_params.get("trix_signal"),
        )

        row = df.iloc[-2]
        # Harmonize current_price for all panels in this cycle
        current_price = float(
            client.get_symbol_ticker(symbol=real_trading_pair)["price"]
        )
        global_current_price = current_price

        # === HISTORIQUE ORDRES ===
        orders = client.get_all_orders(symbol=real_trading_pair, limit=20)
        filled_orders = [o for o in reversed(orders) if o["status"] == "FILLED"]
        last_filled_order = filled_orders[0] if filled_orders else None
        last_side = last_filled_order["side"] if last_filled_order else None

        if last_side and pair_state["last_order_side"] != last_side:
            pair_state["last_order_side"] = last_side
            save_bot_state()

        # === NET USDC APRÈS DERNIÈRE VENTE ===
        net_usdc = None
        if last_side == "SELL":
            result = get_last_sell_trade_usdc(real_trading_pair)
            if result is None:
                USDC_amount, fee, fee_asset = None, None, None
            else:
                USDC_amount, fee, fee_asset = result
            if USDC_amount is not None and fee is not None:
                if fee_asset == "USDC":
                    net_usdc = USDC_amount - fee
                else:
                    try:
                        ticker = client.get_symbol_ticker(symbol=f"{fee_asset}USDC")
                        fee_in_usdc = fee * float(ticker["price"])
                        net_usdc = USDC_amount - fee_in_usdc
                    except Exception:
                        net_usdc = None
            # On n'affiche PAS ici le panel d'achat, il sera affiché plus bas une seule fois

        # === FILTRES PAIRE (une seule récupération) ===
        exchange_info = client.get_exchange_info()
        symbol_info = next(
            (s for s in exchange_info["symbols"] if s["symbol"] == real_trading_pair),
            None,
        )
        if not symbol_info:
            console.print(
                f"[ERREUR] Informations symbole introuvables pour {real_trading_pair}."
            )
            return

        lot_filter = next(
            (f for f in symbol_info["filters"] if f["filterType"] == "LOT_SIZE"), None
        )
        if not lot_filter:
            console.print("[ERREUR] Filtre LOT_SIZE non trouvé.")
            return

        min_qty = float(lot_filter["minQty"])
        max_qty = float(lot_filter["maxQty"])
        step_size = float(lot_filter["stepSize"])

        min_qty_dec = Decimal(str(min_qty))
        max_qty_dec = Decimal(str(max_qty))
        step_size_dec = Decimal(str(step_size))
        step_decimals = abs(step_size_dec.as_tuple().exponent)

        # === GESTION POSITION APRÈS BUY ===
        if last_side == "BUY":
            last_buy_order = next(
                (
                    o
                    for o in reversed(orders)
                    if o["status"] == "FILLED" and o["side"] == "BUY"
                ),
                None,
            )
            if last_buy_order:
                executed_qty = float(last_buy_order.get("executedQty", 0))
                price = float(last_buy_order.get("price", 0))
                if price == 0.0 and executed_qty > 0:
                    price = (
                        float(last_buy_order.get("cummulativeQuoteQty", 0))
                        / executed_qty
                    )

                atr_value = row.get("atr")
                # Set entry variables ONLY if not already set (never update after entry)
                if atr_value is not None and price > 0:
                    if pair_state.get("atr_at_entry") is None:
                        pair_state["atr_at_entry"] = atr_value
                    if pair_state.get("entry_price") is None:
                        pair_state["entry_price"] = price
                    if pair_state.get("stop_loss_at_entry") is None:
                        pair_state["stop_loss_at_entry"] = (
                            price - atr_stop_multiplier * atr_value
                        )
                        pair_state["stop_loss"] = pair_state["stop_loss_at_entry"]
                    if pair_state.get("trailing_activation_price_at_entry") is None:
                        pair_state["trailing_activation_price_at_entry"] = (
                            price + atr_multiplier * atr_value
                        )
                        pair_state["trailing_activation_price"] = pair_state[
                            "trailing_activation_price_at_entry"
                        ]
                    save_bot_state()

            # (Affichage du panneau de vente supprimé ici pour éviter le doublon)

        # === LOGIQUE CENTRALISÉE STOP-LOSS / TRAILING-STOP ===
        # stop_loss_hit supprimé (jamais utilisé)

        if pair_state.get("last_order_side") == "BUY" and coin_balance > 0:
            entry_price = pair_state.get("entry_price")
            atr_at_entry = pair_state.get("atr_at_entry")
            trailing_activation_price = pair_state.get(
                "trailing_activation_price_at_entry"
            )
            trailing_activated = pair_state.get("trailing_stop_activated", False)
            max_price = pair_state.get("max_price", entry_price or global_current_price)
            current_atr = row.get("atr")

            trailing_distance = (
                atr_multiplier * current_atr
                if trailing_activated and current_atr
                else atr_multiplier * atr_at_entry if atr_at_entry else None
            )

            if (
                not trailing_activated
                and trailing_activation_price
                and global_current_price >= trailing_activation_price
            ):
                trailing_activated = True
                max_price = global_current_price
                logger.info(f"[TRAILING] Activation à {global_current_price:.4f}")

            if trailing_activated and global_current_price > max_price:
                max_price = global_current_price
                logger.info(f"[TRAILING] Nouveau haut : {max_price:.4f}")

            if trailing_activated and trailing_distance is not None:
                trailing_stop_val = max_price - trailing_distance
                pair_state["stop_loss"] = min(
                    pair_state.get("stop_loss", float("inf")), trailing_stop_val
                )

            pair_state.update(
                {
                    "trailing_stop_activated": trailing_activated,
                    "max_price": max_price,
                }
            )
            save_bot_state()

            stop_loss = pair_state.get("stop_loss")
            stop_loss_at_entry = pair_state.get("stop_loss_at_entry")
            trailing_activated = pair_state.get("trailing_stop_activated", False)
            if stop_loss and global_current_price <= stop_loss:
                # Exécution vente immédiate sur stop-loss
                quantity_decimal = Decimal(str(coin_balance))
                quantity_rounded = (quantity_decimal // step_size_dec) * step_size_dec
                if quantity_rounded < min_qty_dec:
                    quantity_rounded = quantity_decimal
                if quantity_rounded > max_qty_dec:
                    quantity_rounded = max_qty_dec

                if quantity_rounded >= min_qty_dec:
                    qty_str = f"{quantity_rounded:.{step_decimals}f}"
                    safe_market_sell(symbol=real_trading_pair, quantity=qty_str)
                    logger.info(f"[STOP-LOSS] Vente exécutée : {qty_str} {coin_symbol}")

                    # Reset entry variables after closure
                    pair_state.update(
                        {
                            "entry_price": None,
                            "max_price": None,
                            "stop_loss": None,
                            "trailing_stop": None,
                            "trailing_stop_activated": False,
                            "atr_at_entry": None,
                            "stop_loss_at_entry": None,
                            "trailing_activation_price_at_entry": None,
                            "last_order_side": "SELL",
                        }
                    )
                    save_bot_state()

                # Affichage explicite de la nature du stop-loss utilisé
                if trailing_activated:
                    stop_loss_info = f"{stop_loss:.4f} USDC (dynamique : trailing)"
                else:
                    stop_loss_info = f"{stop_loss:.4f} USDC (fixe à l'entrée)"

                closure_lines = [
                    f"[bold white]Stop-Loss utilisé :[/bold white] {stop_loss_info}",
                    f"[bold white]Prix actuel :[/bold white] {global_current_price:.4f} USDC",
                    f"[bold white]Solde {coin_symbol} :[/bold white] {coin_balance:.8f}",
                    f"[bold white]Raison fermeture :[/bold white] [red]STOP-LOSS touché[/red]",
                ]
                console.print(
                    Panel(
                        "\n".join(closure_lines),
                        title="[bold red]FERMETURE POSITION - STOP TOUCHÉ[/bold red]",
                        border_style="red",
                        padding=(1, 2),
                        width=120,
                    )
                )
                pair_state["last_execution"] = datetime.now(timezone.utc)
                save_bot_state()
                return

        # === CONDITIONS ACHAT / AFFICHAGE ===
        check_buy_signal = generate_buy_condition_checker(best_params)
        buy_condition, buy_reason = check_buy_signal(row, usdc_balance)

        # Affichage du panel d'achat UNIQUEMENT si aucune position n'est ouverte (après une vente ou à l'init)
        if pair_state.get("last_order_side") != "BUY":
            display_buy_signal_panel(
                row=row,
                usdc_balance=usdc_balance,
                best_params=best_params,
                scenario=scenario,
                buy_condition=buy_condition,
                console=console,
                buy_reason=buy_reason,
            )

        # === CONDITIONS VENTE STRATÉGIE ===
        entry_price_for_panel = pair_state.get("entry_price") or row.get("close")
        pair_state.setdefault(
            "stop_loss_at_entry",
            (
                entry_price_for_panel - 3 * row.get("atr")
                if entry_price_for_panel and row.get("atr")
                else None
            ),
        )
        pair_state.setdefault("atr_at_entry", row.get("atr"))
        pair_state.setdefault("max_price", entry_price_for_panel)

        check_sell_signal = generate_sell_condition_checker(best_params)
        sell_triggered, sell_reason = check_sell_signal(
            row, coin_balance, entry_price_for_panel, current_price, row.get("atr")
        )

        # Affichage du panel de vente uniquement si une position est ouverte
        if pair_state.get("last_order_side") == "BUY":
            display_sell_signal_panel(
                row=row,
                coin_balance=coin_balance,
                pair_state=pair_state,
                sell_triggered=sell_triggered,
                console=console,
                coin_symbol=coin_symbol,
                sell_reason=sell_reason,
            )

        # === EXÉCUTION ACHAT ===
        if buy_condition and pair_state.get("last_order_side") != "BUY":
            if scenario != best_params.get("scenario", "UNKNOWN"):
                logger.error("[CRITICAL] SCENARIO MISMATCH DETECTED! ABORTING ORDER")
                console.print(
                    Panel(
                        f"[bold red]ERREUR CRITIQUE:[/bold red]\n\n"
                        f"Le scénario affiché ([cyan]{scenario}[/cyan]) ne correspond PAS\n"
                        f"au scénario dans best_params ([yellow]{best_params.get('scenario', 'UNKNOWN')}[/yellow]).\n\n"
                        f"[bold red]ORDRE ANNULÉ pour éviter une exécution avec la mauvaise stratégie.[/bold red]",
                        title="[bold red]SCENARIO MISMATCH - ORDER ABORTED[/bold red]",
                        border_style="red",
                        padding=(1, 2),
                        width=120,
                    )
                )
                return

            if net_usdc is None:
                console.print(
                    Panel(
                        "[bold red]Erreur : Montant net USDC non defini\nImpossible de calculer la quantite a acheter[/bold red]",
                        title="[bold red]ERREUR SIGNAL D'ACHAT[/bold red]",
                        border_style="red",
                        padding=(1, 2),
                        width=120,
                    )
                )
                return

            # Sniper entry
            sniper_price = get_sniper_entry_price(real_trading_pair, current_price)
            if sniper_price != current_price:
                improvement = (current_price - sniper_price) / current_price * 100
                logger.info(
                    f"Entree sniper optimisee: {improvement:.2f}% d'amelioration"
                )
                console.print(
                    Panel(
                        f"[bold cyan]Optimisation entree sniper activee[/bold cyan]\n\n"
                        f"[bold white]Prix signal initial :[/bold white] [bright_yellow]{current_price:.4f} USDC[/bright_yellow]\n"
                        f"[bold white]Prix entree optimise :[/bold white] [bright_green]{sniper_price:.4f} USDC[/bright_green]\n"
                        f"[bold white]Amelioration :[/bold white] [bright_cyan]{improvement:.2f}%[/bright_cyan]",
                        title="[bold cyan]ENTREE SNIPER 15MIN[/bold cyan]",
                        border_style="cyan",
                        padding=(1, 2),
                        width=120,
                    )
                )
                current_price = sniper_price

            # LIVE TRADING SPOT : Utiliser 99.5% du solde USDC disponible pour l'achat (sécurité frais/arrondis)
            quote_amount = float(net_usdc) * 0.995

            quantity_to_buy = (
                Decimal(str(quote_amount)) / Decimal(str(current_price))
            ).quantize(step_size_dec, rounding=ROUND_DOWN)

            if min_qty_dec <= quantity_to_buy <= max_qty_dec:
                run_id = f"RUN-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
                strategy_snapshot = json.dumps(best_params, sort_keys=True)

                logger.info(
                    f"[BUY Attempt] {real_trading_pair} | scenario={scenario} | timeframe={time_interval} | "
                    f"EMA={ema1_period}/{ema2_period} | runId={run_id}"
                )

                order_success = False
                last_error = None

                for retry_count in range(3):
                    try:
                        full_timestamp_resync()
                        # On achète avec 99.5% du solde USDC disponible (sécurité)
                        quote_amount_for_order = float(net_usdc) * 0.995
                        buy_order = safe_market_buy(
                            symbol=real_trading_pair,
                            quoteOrderQty=quote_amount_for_order,
                        )

                        fill_price = float(buy_order["fills"][0]["price"])

                        account_info_updated = client.get_account()
                        spot_balance_usdc = next(
                            (
                                float(b["free"])
                                for b in account_info_updated["balances"]
                                if b["asset"] == "USDC"
                            ),
                            None,
                        )

                        pair_state.update(
                            {
                                "entry_price": fill_price,
                                "max_price": fill_price,
                                "stop_loss": fill_price
                                - atr_stop_multiplier * row.get("atr"),
                                "last_order_side": "BUY",
                            }
                        )
                        save_bot_state()

                        console.print(
                            Panel(
                                "Un ordre d'achat vient d'etre execute !",
                                title="[bold green]SIGNAL D'ACHAT - EXeCUTe[/bold green]",
                                border_style="green",
                                padding=(1, 2),
                                width=120,
                            )
                        )

                        # Ne pas afficher le panneau de vente juste après achat
                        # Il sera affiché à la prochaine planification automatique

                        # === EMAIL ACHAT RÉUSSI ===
                        email_body = f"""
=== ORDRE D'ACHAT EXECUTE AVEC SUCCES ===
Un nouvel ordre d'achat a ete realise avec succes sur Binance.
DETAILS DE L'OPERATION:
------------------------
Paire de trading : {real_trading_pair}
Quantite achetee : {quantity_to_buy:.8f} {coin_symbol}
Prix d'execution : {fill_price:.4f} USDC
Valeur totale : {float(quantity_to_buy) * fill_price:.2f} USDC
Horodatage : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
SOLDE APRES TRANSACTION:
------------------------
USDC disponible : {spot_balance_usdc:.2f} USDC
STRATEGIE UTILISEE:
-------------------
Timeframe : {time_interval}
Configuration EMA : {ema1_period or 'N/A'} / {ema2_period or 'N/A'}
Scenario : {scenario}
--- Message automatique du Bot de Trading Crypto ---
                        """
                        try:
                            send_trading_alert_email(
                                subject=f"[BOT CRYPTO] Achat execute - {real_trading_pair}",
                                body_main=email_body,
                                client=client,
                            )
                            console.print(
                                "[INFO] E-mail d'alerte envoye pour l'ordre d'achat."
                            )
                        except Exception as e:
                            console.print(
                                f"[ERREUR] L'envoi de l'e-mail a echoue : {e}"
                            )

                        order_success = True
                        break

                    except Exception as e:
                        last_error = e
                        console.print(
                            f"[ERREUR] Impossible d'executer l'ordre d'achat : {e}"
                        )
                        if retry_count < 2:
                            console.print(
                                f"[INFO] Nouvelle tentative dans 1 minute (essai {retry_count + 1}/3)..."
                            )
                            time.sleep(60)

                if not order_success and last_error:
                    # === EMAIL ÉCHEC ACHAT ===
                    conditions_details = f"""
CONDITIONS D'ACHAT EVALUEES:
----------------------------
EMA1 > EMA2 : {'OK' if row['ema1'] > row['ema2'] else 'NOK'} (EMA1={row['ema1']:.4f}, EMA2={row['ema2']:.4f})
StochRSI < 80% : {'OK' if row['stoch_rsi'] < 0.8 else 'NOK'} (StochRSI={row['stoch_rsi']*100:.2f}%)
Solde USDC > 0 : {'OK' if usdc_balance > 0 else 'NOK'} (Solde={usdc_balance:.2f} USDC)
"""
                    if scenario == "StochRSI_ADX":
                        adx_threshold = best_params.get("adx_threshold", 20)
                        conditions_details += f"ADX > {adx_threshold} : {'OK' if row.get('adx', 0) > adx_threshold else 'NOK'} (ADX={row.get('adx', 'N/A')})\n"
                    elif scenario == "StochRSI_SMA":
                        sma_long_val = best_params.get("sma_long", 200)
                        conditions_details += f"Prix > SMA{sma_long_val} : {'OK' if row.get('close', 0) > row.get('sma_long', float('nan')) else 'NOK'}\n"
                    elif scenario == "StochRSI_TRIX":
                        conditions_details += f"TRIX_HISTO > 0 : {'OK' if row.get('TRIX_HISTO', -999) > 0 else 'NOK'} (TRIX_HISTO={row.get('TRIX_HISTO', -999):.6f})\n"

                    email_body_failure = f"""
=== ECHEC D'EXECUTION D'ORDRE D'ACHAT ===
Le bot n'a pas pu executer l'ordre d'achat apres plusieurs tentatives.
DETAILS DE L'INCIDENT:
----------------------
Paire de trading : {real_trading_pair}
Quantite cible : {quantity_to_buy:.8f} {coin_symbol}
Prix unitaire : {current_price:.4f} USDC
Horodatage : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Erreur rencontree : {str(last_error)[:150]}{'...' if len(str(last_error)) > 150 else ''}
{conditions_details}
ACTIONS RECOMMANDEES:
---------------------
1. Verifier la connectivite a l'API Binance
2. Controler les fonds USDC disponibles
3. Examiner les logs detailles du bot
4. Verifier les parametres de la paire de trading
STRATEGIE UTILISEE:
-------------------
Timeframe : {time_interval}
Configuration EMA : {ema1_period or 'N/A'} / {ema2_period or 'N/A'}
Scenario : {scenario}
 Run Id : {run_id}
 Strategie snapshot : {strategy_snapshot}
--- Message automatique du Bot de Trading Crypto ---
                    """
                    try:
                        send_trading_alert_email(
                            subject=f"[BOT CRYPTO] ECHEC Achat - {real_trading_pair}",
                            body_main=email_body_failure,
                            client=client,
                        )
                        console.print(
                            "[INFO] E-mail d'alerte envoye pour l'echec d'achat."
                        )
                    except Exception as email_error:
                        console.print(
                            f"[ERREUR] echec d'envoi de l'alerte : {email_error}"
                        )

        # === EXÉCUTION VENTE (SIGNAL STRATÉGIE) ===
        if (
            sell_triggered
            and pair_state.get("last_order_side") == "BUY"
            and coin_balance > 0
        ):
            reason_vente = f"Signal de stratégie : {sell_reason}"

            display_sell_signal_panel(
                row, coin_balance, pair_state, True, console, coin_symbol, sell_reason
            )

            # LIVE TRADING SPOT : vendre 100% du solde coin disponible
            quantity_decimal = Decimal(str(coin_balance))
            quantity_rounded = (quantity_decimal // step_size_dec) * step_size_dec
            if quantity_rounded < min_qty_dec:
                quantity_rounded = quantity_decimal
            if quantity_rounded > max_qty_dec:
                quantity_rounded = max_qty_dec

            if quantity_rounded > 0:
                order_success = False
                last_error = None

                for retry_count in range(3):
                    try:
                        full_timestamp_resync()
                        sell_order = safe_market_sell(
                            symbol=real_trading_pair,
                            quantity=f"{quantity_rounded:.{step_decimals}f}",
                        )

                        total_sell_value = float(quantity_rounded) * current_price
                        account_info_updated = client.get_account()
                        spot_balance_usdc = next(
                            (
                                float(b["free"])
                                for b in account_info_updated["balances"]
                                if b["asset"] == "USDC"
                            ),
                            None,
                        )

                        pair_state.update(
                            {
                                "entry_price": None,
                                "max_price": None,
                                "stop_loss": None,
                                "trailing_stop": None,
                                "last_order_side": "SELL",
                            }
                        )
                        save_bot_state()

                        console.print(
                            Panel(
                                "Un ordre de vente vient d'etre execute !",
                                title="[bold red]SIGNAL DE VENTE - EXeCUTe[/bold red]",
                                border_style="red",
                                padding=(1, 2),
                                width=120,
                            )
                        )

                        # === EMAIL VENTE RÉUSSIE ===
                        email_body = f"""
=== ORDRE DE VENTE EXECUTE AVEC SUCCES ===
Un ordre de vente a ete execute avec succes sur Binance.
Raison de la vente: {reason_vente}
DETAILS DE L'OPERATION:
------------------------
Paire de trading : {real_trading_pair}
Quantite vendue : {quantity_rounded:.8f} {coin_symbol}
Prix d'execution : {current_price:.4f} USDC
Valeur totale : {total_sell_value:.2f} USDC
Horodatage : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
SOLDE APRES TRANSACTION:
------------------------
USDC disponible : {spot_balance_usdc:.2f} USDC
STRATEGIE UTILISEE:
-------------------
Timeframe : {time_interval}
Configuration EMA : {ema1_period or 'N/A'} / {ema2_period or 'N/A'}
Scenario : {scenario}
--- Message automatique du Bot de Trading Crypto ---
                        """
                        try:
                            send_trading_alert_email(  # ou send_email_alert selon votre implémentation
                                subject=f"[BOT CRYPTO] Vente executee ({sell_reason}) - {real_trading_pair}",
                                body_main=email_body,
                                client=client,
                            )
                            console.print(
                                f"[INFO] E-mail d'alerte envoye pour l'ordre de vente ({sell_reason})."
                            )
                        except Exception as e:
                            console.print(
                                f"[ERREUR] L'envoi de l'e-mail a echoue : {e}"
                            )

                        order_success = True
                        break

                    except Exception as e:
                        last_error = e
                        console.print(
                            f"[ERREUR] Impossible d'executer l'ordre de vente ({sell_reason}) : {e}"
                        )
                        if retry_count < 2:
                            console.print(
                                f"[INFO] Nouvelle tentative dans 1 minute (essai {retry_count + 1}/3)..."
                            )
                            time.sleep(60)

                if not order_success and last_error:
                    # === EMAIL ÉCHEC VENTE ===
                    conditions_details = f"""
CONDITIONS DE VENTE EVALUEES:
-----------------------------
EMA2 > EMA1 : {'OK' if row['ema2'] > row['ema1'] else 'NOK'} (EMA1={row['ema1']:.4f}, EMA2={row['ema2']:.4f})
StochRSI > 20% : {'OK' if row['stoch_rsi'] > 0.2 else 'NOK'} (StochRSI={row['stoch_rsi']*100:.2f}%)
Solde {coin_symbol} > 0 : {'OK' if coin_balance > 0 else 'NOK'} (Solde={coin_balance:.8f} {coin_symbol})
Raison de la vente : {sell_reason}
"""
                    email_body_failure = f"""
=== ECHEC D'EXECUTION D'ORDRE DE VENTE ===
Le bot n'a pas pu executer l'ordre de vente apres plusieurs tentatives.
Raison de la vente: {reason_vente}
DETAILS DE L'INCIDENT:
----------------------
Paire de trading : {real_trading_pair}
Quantite cible : {quantity_rounded:.8f} {coin_symbol}
Prix unitaire : {current_price:.4f} USDC
Horodatage : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Erreur rencontree : {str(last_error)[:150]}{'...' if len(str(last_error)) > 150 else ''}
{conditions_details}
ACTIONS RECOMMANDEES:
---------------------
1. Verifier la connectivite a l'API Binance
2. Controler le solde {coin_symbol} disponible
3. Examiner les logs detailles du bot
4. Verifier les parametres de la paire de trading
STRATEGIE UTILISEE:
-------------------
Timeframe : {time_interval}
Configuration EMA : {ema1_period or 'N/A'} / {ema2_period or 'N/A'}
Scenario : {scenario}
--- Message automatique du Bot de Trading Crypto ---
                    """
                    try:
                        send_trading_alert_email(
                            subject=f"[BOT CRYPTO] ECHEC Vente ({sell_reason}) - {real_trading_pair}",
                            body_main=email_body_failure,
                            client=client,
                        )
                        console.print(
                            f"[INFO] E-mail d'alerte envoye pour l'echec de vente ({sell_reason})."
                        )
                    except Exception as email_error:
                        console.print(
                            f"[ERREUR] echec d'envoi de l'alerte : {email_error}"
                        )

        # === FIN DU CYCLE ===
        pair_state["last_execution"] = datetime.now(timezone.utc)
        save_bot_state()

    except Exception as e:
        logger.error(f"Erreur inattendue dans execute_real_trades : {e}")
        console.print(f"Erreur lors de l'execution de l'ordre : {e}")


###############################################################
#                                                             #
#              *** FONCTION PRINCIPALE ***                    #
#                                                             #
###############################################################


def detect_market_changes(
    pair: str, timeframes: List[str], start_date: str
) -> Dict[str, Any]:
    """
    Détecte intelligemment les changements IMPORTANTS du marché.

    Retourne:
    - EMA crosses (bullish/bearish)
    - StochRSI extremes
    - TRIX histogram changes
    - Prix nouveaux (highs/lows)
    """
    changes = {
        "ema_crosses": [],
        "stoch_extremes": [],
        "trix_changes": [],
        "price_records": [],
        "execution_time": datetime.now().strftime("%H:%M:%S"),
    }

    try:
        for tf in timeframes:
            try:
                df = prepare_base_dataframe(pair, tf, start_date, 14)
                if df.empty or len(df) < 50:
                    continue

                # Récupérer les 2 derniers candles
                if len(df) >= 2:
                    prev_candle = df.iloc[-2]
                    curr_candle = df.iloc[-1]

                    # Détection: EMA Cross (bullish/bearish)
                    ema1_prev = prev_candle.get("ema_26", None)
                    ema2_prev = prev_candle.get("ema_50", None)
                    ema1_curr = curr_candle.get("ema_26", None)
                    ema2_curr = curr_candle.get("ema_50", None)

                    if (
                        ema1_prev is not None
                        and ema2_prev is not None
                        and ema1_curr is not None
                        and ema2_curr is not None
                    ):

                        if ema1_prev <= ema2_prev and ema1_curr > ema2_curr:
                            changes["ema_crosses"].append(
                                {
                                    "timeframe": tf,
                                    "type": " BULLISH CROSS",
                                    "ema1": ema1_curr,
                                    "ema2": ema2_curr,
                                    "price": curr_candle["close"],
                                }
                            )
                        elif ema1_prev >= ema2_prev and ema1_curr < ema2_curr:
                            changes["ema_crosses"].append(
                                {
                                    "timeframe": tf,
                                    "type": " BEARISH CROSS",
                                    "ema1": ema1_curr,
                                    "ema2": ema2_curr,
                                    "price": curr_candle["close"],
                                }
                            )

                    # Détection: StochRSI extremes
                    stoch_curr = curr_candle.get("stoch_rsi", None)
                    if stoch_curr is not None:
                        if stoch_curr < 0.2:
                            changes["stoch_extremes"].append(
                                {
                                    "timeframe": tf,
                                    "type": " OVERSOLD",
                                    "value": stoch_curr,
                                    "price": curr_candle["close"],
                                }
                            )
                        elif stoch_curr > 0.8:
                            changes["stoch_extremes"].append(
                                {
                                    "timeframe": tf,
                                    "type": " OVERBOUGHT",
                                    "value": stoch_curr,
                                    "price": curr_candle["close"],
                                }
                            )

                    # Détection: Prix record
                    high_price = curr_candle["high"]
                    low_price = curr_candle["low"]

                    if len(df) >= 20:
                        recent_high = df["high"].iloc[-20:].max()
                        recent_low = df["low"].iloc[-20:].min()

                        if high_price >= recent_high:
                            changes["price_records"].append(
                                {
                                    "timeframe": tf,
                                    "type": "🆕 NEW 20-CANDLE HIGH",
                                    "value": high_price,
                                    "previous_high": recent_high,
                                }
                            )

                        if low_price <= recent_low:
                            changes["price_records"].append(
                                {
                                    "timeframe": tf,
                                    "type": "🆕 NEW 20-CANDLE LOW",
                                    "value": low_price,
                                    "previous_low": recent_low,
                                }
                            )

            except Exception as e:
                logger.debug(f"Erreur détection changements {pair} {tf}: {e}")
                continue

    except Exception as e:
        logger.debug(f"Erreur globale détection changements: {e}")

    return changes


def display_market_changes(changes: Dict[str, Any], pair: str):
    """Affiche les changements détectés de façon élégante"""
    has_changes = any(
        [changes["ema_crosses"], changes["stoch_extremes"], changes["price_records"]]
    )

    if not has_changes:
        logger.info(f"[INFO] No significant market changes detected for {pair}")
        return

    console.print(
        f"\n[bold yellow][MARKET] CHANGEMENTS DETECTES - {pair} @ {changes['execution_time']}[/bold yellow]"
    )

    if changes["ema_crosses"]:
        console.print("\n[bold green][CROSS] EMA CROSSES:[/bold green]")
        for cross in changes["ema_crosses"]:
            status = cross["type"]
            tf = cross["timeframe"]
            price = cross["price"]
            console.print(f"  {status} on {tf} @ ${price:.2f}")

    if changes["stoch_extremes"]:
        console.print("\n[bold cyan] STOCH RSI EXTREMES:[/bold cyan]")
        for extreme in changes["stoch_extremes"]:
            status = extreme["type"]
            tf = extreme["timeframe"]
            value = extreme["value"] * 100
            console.print(f"  {status} on {tf} @ {value:.1f}%")

    if changes["price_records"]:
        console.print("\n[bold magenta] NEW PRICE LEVELS:[/bold magenta]")
        for record in changes["price_records"]:
            status = record["type"]
            tf = record["timeframe"]
            price = record["value"]
            console.print(f"  {status} on {tf} @ ${price:.2f}")


def backtest_and_display_results(
    backtest_pair: str, real_trading_pair: str, start_date: str, timeframes: List[str]
):
    """
    Effectue les backtests pour differents timeframes, affiche les resultats,
    et identifie les meilleurs parametres pour le trading en temps reel.

    IMPORTANT: start_date sera recalcule dynamiquement a chaque appel pour toujours
    utiliser une fenetre glissante de 5 ans depuis aujourd'hui.
    """
    # Recalculer start_date dynamiquement a chaque execution (fenetre glissante 5 ans)
    dynamic_start_date = (
        datetime.today() - timedelta(days=config.backtest_days)
    ).strftime("%d %B %Y")

    # DETECTION INTELLIGENTE DES CHANGEMENTS DE MARCHE
    console.print(
        f"\n[bold cyan][ANALYZE] Analyse des changements du marche...[/bold cyan]"
    )
    market_changes = detect_market_changes(
        backtest_pair, timeframes, dynamic_start_date
    )
    display_market_changes(market_changes, backtest_pair)

    logger.info(
        f"Backtest period: 5 years from today | Start date: {dynamic_start_date}"
    )

    # COMPENSATION BINANCE ULTRA-ROBUSTE A CHAQUE BACKTEST
    logger.info("Compensation timestamp Binance ultra-robuste active")

    logger.info("Debut des backtests...")

    if backtest_pair not in bot_state:
        bot_state[backtest_pair] = {
            "last_run_time": None,
            "last_best_params": None,
            "execution_count": 0,
            "entry_price": None,
            "max_price": None,
            "trailing_stop": None,
            "stop_loss": None,
            "last_execution": None,
        }

    pair_state = bot_state[backtest_pair]

    try:
        results = run_all_backtests(backtest_pair, dynamic_start_date, timeframes)
    except Exception as e:
        logger.error(f"Une erreur est survenue pendant les backtests : {e}")
        return

    if not results:
        logger.error("Aucune donnee de backtest n'a ete generee")
        return

    # Identifier le meilleur resultat
    best_result = max(results, key=lambda x: x["final_wallet"] - x["initial_wallet"])
    best_profit = best_result["final_wallet"] - best_result["initial_wallet"]

    # Afficher les resultats
    console = Console()
    table = Table(
        title="[bold cyan]Backtest Results[/bold cyan]",
        title_style="bold magenta",
        show_header=True,
        header_style="bold green",
        border_style="bright_yellow",
    )
    table.add_column("Timeframe", style="dim", justify="center", width=15)
    table.add_column("EMA Periods", style="cyan", justify="center", width=25)
    table.add_column("Scenario", style="magenta", justify="left", width=25)
    table.add_column(
        "Initial Wallet ($)", style="bold white", justify="center", width=20
    )
    table.add_column("Final Wallet ($)", style="bold yellow", justify="right", width=20)
    table.add_column("Profit ($)", style="bold green", justify="right", width=20)
    table.add_column("Trades", style="bold white", justify="right", width=15)
    table.add_column("Max Drawdown (%)", style="bold red", justify="right", width=15)
    table.add_column("Win Rate (%)", style="bold cyan", justify="right", width=15)

    for result in results:
        profit = result["final_wallet"] - result["initial_wallet"]
        profit_color = "bold green" if profit > 0 else "bold red"
        final_wallet_color = (
            "bold green"
            if result["final_wallet"] > result["initial_wallet"]
            else "bold red"
        )

        table.add_row(
            f"[cyan]{result['timeframe']}[/cyan]",
            f"EMA [blue]{result['ema_periods'][0]}[/] / [blue]{result['ema_periods'][1]}[/]",
            f"[magenta]{result['scenario']}[/magenta]",
            f"[bold white]${result['initial_wallet']:.2f}[/bold white]",
            f"[{final_wallet_color}]${result['final_wallet']:.2f}[/{final_wallet_color}]",
            f"[{profit_color}]${profit:.2f}[/{profit_color}]",
            f"[bold white]{len(result['trades'])}[/bold white]",
            f"[bold red]{result['max_drawdown']*100:.2f}%[/bold red]",
            f"[bold cyan]{result['win_rate']:.2f}%[/bold cyan]",
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

    console.print(
        Panel(
            best_title,
            title="[bold yellow]Best Result[/bold yellow]",
            title_align="center",
            expand=False,
        )
    )
    console.print(table)

    # Execution des ordres reels avec les meilleurs parametres
    scenario_params = {
        "StochRSI": {"stoch_period": 14},
        "StochRSI_SMA": {"stoch_period": 14, "sma_long": 200},
        "StochRSI_ADX": {"stoch_period": 14, "adx_period": 14},
        "StochRSI_TRIX": {"stoch_period": 14, "trix_length": 7, "trix_signal": 15},
    }

    best_params = {
        "timeframe": best_result["timeframe"],
        "ema1_period": best_result["ema_periods"][0],
        "ema2_period": best_result["ema_periods"][1],
        "scenario": best_result["scenario"],
    }
    best_params.update(scenario_params.get(best_result["scenario"], {}))

    # Mise a jour de l'etat du bot
    pair_state["last_best_params"] = best_params
    pair_state["execution_count"] += 1
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

    console.print(
        Panel(
            trading_content,
            title="[bold bright_cyan]TRADING ALGORITHMIQUE EN TEMPS ReEL[/bold bright_cyan]",
            title_align="center",
            border_style="bright_cyan",
            padding=(1, 3),
            width=120,
        )
    )

    try:
        execute_real_trades(
            real_trading_pair, best_params["timeframe"], best_params, backtest_pair
        )
    except Exception as e:
        logger.error(
            f"Une erreur est survenue lors de l'execution des ordres en reel: {e}"
        )
        send_email_alert(
            subject="[ALERTE BOT CRYPTO] Erreur execution trading",
            body=f"Erreur lors de l'execution des ordres:\n\n{traceback.format_exc()}",
        )

    # Gestion de l'historique d'execution
    current_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Panel pour l'historique et la planification
    if pair_state.get("last_run_time"):
        last_time_obj = datetime.strptime(
            pair_state["last_run_time"], "%Y-%m-%d %H:%M:%S"
        )
        current_time_obj = datetime.strptime(current_run_time, "%Y-%m-%d %H:%M:%S")
        time_elapsed = current_time_obj - last_time_obj

        history_section = (
            f"[bold white]Derniere execution  :[/bold white] [bright_cyan]{pair_state['last_run_time']}[/bright_cyan]\n"
            f"[bold white]Temps ecoule        :[/bold white] [bright_yellow]{time_elapsed}[/bright_yellow]"
        )
    else:
        history_section = "[bold bright_green]Premiere execution du systeme de trading automatise[/bold bright_green]"

    pair_state["last_run_time"] = current_run_time

    # SOLUTION DEFINITIVE - Éviter les planifications multiples
    # Vérifier si une tâche existe déjà pour cette paire
    existing_job = None
    for job in schedule.jobs:
        try:
            if (
                hasattr(job.job_func, "args")
                and len(job.job_func.args) >= 2
                and job.job_func.args[0] == backtest_pair
            ):
                existing_job = job
                break
        except Exception:
            continue

    # Si une tâche existe déjà, la supprimer
    if existing_job:
        schedule.cancel_job(existing_job)
        logger.info(f"Ancienne planification supprimée pour {backtest_pair}")

    # Programmer une tâche UNIQUE et HOMOGENE toutes les 2 minutes (indépendant du timeframe)
    # IMPORTANT: recalculer start_date à CHAQUE exécution pour conserver une fenêtre glissante de 5 ans
    schedule.every(2).minutes.do(
        lambda bp=backtest_pair, rp=real_trading_pair, tfs=timeframes: backtest_and_display_results(
            bp,
            rp,
            (datetime.today() - timedelta(days=config.backtest_days)).strftime(
                "%d %B %Y"
            ),
            tfs,
        )
    )
    next_exec = "toutes les 2 minutes (720 exécutions/jour)"
    logger.info(
        f"Planification homogène 30-min activée pour {backtest_pair} - Réactivité optimale"
    )

    schedule_section = (
        f"[bold white]Mode de planification:[/bold white] [dim]Homogène et réactif - 2 minutes[/dim]\n"
        f"[bold white]Prochaine execution :[/bold white] [bright_green]{next_exec}[/bright_green]"
    )

    console.print(
        Panel(
            f"{history_section}\n\n{schedule_section}",
            title="[bold bright_blue]SUIVI D'EXeCUTION & PLANIFICATION AUTOMATIQUE[/bold bright_blue]",
            title_align="center",
            border_style="bright_blue",
            padding=(1, 3),
            width=120,
        )
    )
    console.print("\n")


###############################################################
#                                                             #
#              *** POINT D'ENTREE PRINCIPAL ***               #
#                                                             #
###############################################################


def cleanup_expired_cache():
    """Nettoie automatiquement les caches expirés (>30 jours)."""
    try:
        if not os.path.exists(config.cache_dir):
            return

        cleaned_count = 0
        total_size_freed = 0

        for filename in os.listdir(config.cache_dir):
            if filename.endswith(".pkl"):
                filepath = os.path.join(config.cache_dir, filename)

                if is_cache_expired(filepath, max_age_days=30):
                    try:
                        file_size = os.path.getsize(filepath)
                        os.remove(filepath)
                        cleaned_count += 1
                        total_size_freed += file_size
                        logger.debug(f"Cache expiré supprimé: {filename}")
                    except Exception as e:
                        logger.debug(f"Erreur suppression cache {filename}: {e}")

        if cleaned_count > 0:
            size_mb = total_size_freed / (1024 * 1024)
            logger.info(
                f"Nettoyage mensuel terminé: {cleaned_count} fichiers supprimés ({size_mb:.1f} MB libérés)"
            )

            # Notification email du nettoyage
            try:
                email_body = f"""
=== NETTOYAGE MENSUEL DU CACHE EFFECTUE ===

Le nettoyage automatique du cache a ete realise avec succes.

DETAILS DE L'OPERATION:
-----------------------
Horodatage          : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Fichiers supprimes  : {cleaned_count}
Espace libere       : {size_mb:.1f} MB
Type d'operation    : Nettoyage automatique

IMPACT SUR LE BOT:
------------------
Amelioration des performances du systeme.
Liberation d'espace disque.
Optimisation des temps de reponse.

FONCTIONNEMENT NORMAL:
----------------------
Les donnees seront re-telechargees automatiquement.
Aucune intervention manuelle requise.
Le bot continue de fonctionner normalement.

--- Message automatique du Bot de Trading Crypto ---
                """
                send_email_alert(
                    subject="[BOT CRYPTO] INFO Nettoyage Cache", body=email_body
                )
            except Exception:
                pass
        else:
            logger.debug("Nettoyage mensuel: aucun cache expiré trouvé")

    except Exception as e:
        logger.error(f"Erreur lors du nettoyage du cache: {e}")


def check_admin_privileges():
    """Vérifie les privilèges admin sans élévation."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


if __name__ == "__main__":
    full_timestamp_resync()
    logger.info("Synchronisation complète exécutée au démarrage.")

    # MODE ULTRA-ROBUSTE SANS POPUP - COMPENSATION BINANCE PURE
    logger.info("Bot crypto H24/7 - Mode ultra-robuste avec privileges admin")

    crypto_pairs = [
        {"backtest_pair": "SOLUSDT", "real_pair": "SOLUSDC"},
    ]
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
        # Nettoyage renforcé de la planification
        try:
            schedule.clear()
            logger.info("Planification nettoyee au demarrage (schedule.clear())")
        except Exception as _clear_ex:
            logger.debug(f"Echec nettoyage planification au demarrage: {_clear_ex}")
        # Vérification qu'aucun job 30-min n'est déjà présent
        for job in list(schedule.jobs):
            if job.interval == 30 and job.unit == "minutes":
                schedule.cancel_job(job)
                logger.info(
                    "Job 30-min existant supprimé avant nouvelle planification."
                )

        # Planification du nettoyage du cache tous les 30 jours
        schedule.every(30).days.do(cleanup_expired_cache)
        logger.info("Nettoyage automatique du cache planifié: tous les 30 jours")

        # === NOUVELLE LOGIQUE : backtests optimisés + affichage propre ===
        parser = argparse.ArgumentParser(
            description="Run backtests and optional sizing mode"
        )
        parser.add_argument(
            "--sizing-mode",
            choices=["baseline", "risk"],
            default="baseline",
            help="Position sizing mode to use for backtests",
        )
        args, unknown = parser.parse_known_args()

        # Exécuter les backtests avec affichage propre (passer sizing_mode)
        all_results = run_parallel_backtests(
            crypto_pairs, start_date, timeframes, sizing_mode=args.sizing_mode
        )

        # === EXPORT TRADES TO CSV ===
        import csv
        import os

        all_trades = []
        skipped_trades = 0
        debug_printed = False
        for backtest_pair, data in all_results.items():
            for result in data["results"]:
                # DEBUG: Affiche la structure de result['trades'] pour le premier résultat
                if not debug_printed:
                    print(
                        "[DEBUG][EXPORT] Aperçu de result['trades'] pour le premier résultat:"
                    )
                    print(repr(result.get("trades")))
                    debug_printed = True
                trades_obj = result["trades"]
                # Si c'est un DataFrame, convertir en liste de dicts
                try:
                    import pandas as pd
                except ImportError:
                    pd = None
                if pd and isinstance(trades_obj, pd.DataFrame):
                    trades_list = trades_obj.to_dict(orient="records")
                else:
                    trades_list = trades_obj
                for trade in trades_list:
                    trade_copy = None
                    if isinstance(trade, dict):
                        trade_copy = dict(trade)
                    elif hasattr(trade, "_asdict"):
                        trade_copy = trade._asdict()
                    elif (
                        isinstance(trade, (list, tuple))
                        and len(trade) == 2
                        and isinstance(trade[0], str)
                    ):
                        trade_copy = {trade[0]: trade[1]}
                    else:
                        skipped_trades += 1
                        print(f"[EXPORT][WARN] Skipped malformed trade: {repr(trade)}")
                        continue
                    trade_copy["backtest_pair"] = backtest_pair
                    trade_copy["scenario"] = result.get("scenario", "")
                    trade_copy["timeframe"] = result.get("timeframe", "")
                    trade_copy["ema_periods"] = result.get("ema_periods", "")
                    all_trades.append(trade_copy)

        if all_trades:
            csv_path = os.path.join(os.path.dirname(__file__), "trades_export.csv")
            # Collect all unique fieldnames
            fieldnames = set()
            for t in all_trades:
                fieldnames.update(t.keys())
            fieldnames = list(fieldnames)
            with open(csv_path, mode="w", newline="", encoding="utf-8") as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(all_trades)
            print(f"[EXPORT] {len(all_trades)} trades exported to {csv_path}")
            if skipped_trades:
                print(
                    f"[EXPORT][WARN] {skipped_trades} trades were skipped due to format issues."
                )
        else:
            print("[EXPORT] No trades to export.")

        # === AFFICHAGE PROPRE, SANS CHEVAUCHEMENT ===
        from rich.table import Table
        from rich.panel import Panel

        for backtest_pair, data in all_results.items():
            if not data["results"]:
                console.print(f"[red]Aucun résultat pour {backtest_pair}[/red]")
                continue

            # Trouver le meilleur résultat
            best_result = max(
                data["results"], key=lambda x: x["final_wallet"] - x["initial_wallet"]
            )
            best_profit = best_result["final_wallet"] - best_result["initial_wallet"]

            # Créer le tableau
            table = Table(
                title=f"[bold cyan]Résultats du Backtest — {backtest_pair}[/bold cyan]",
                title_style="bold magenta",
                show_header=True,
                header_style="bold green",
                border_style="bright_yellow",
                expand=False,
                width=120,
            )
            table.add_column("Timeframe", style="dim", justify="center", width=10)
            table.add_column("EMA", style="cyan", justify="center", width=15)
            table.add_column("Scénario", style="magenta", justify="left", width=20)
            table.add_column(
                "Profit ($)", style="bold green", justify="right", width=15
            )
            table.add_column(
                "Final Wallet ($)", style="bold yellow", justify="right", width=18
            )
            table.add_column("Trades", style="white", justify="right", width=8)
            table.add_column(
                "Max Drawdown (%)", style="bold red", justify="right", width=18
            )
            table.add_column(
                "Win Rate (%)", style="bold cyan", justify="right", width=15
            )

            for result in data["results"]:
                profit = result["final_wallet"] - result["initial_wallet"]
                profit_color = "bold green" if profit > 0 else "bold red"
                table.add_row(
                    f"[cyan]{result['timeframe']}[/cyan]",
                    f"[blue]{result['ema_periods'][0]} / {result['ema_periods'][1]}[/blue]",
                    f"[magenta]{result['scenario']}[/magenta]",
                    f"[{profit_color}]{profit:,.2f}[/{profit_color}]",
                    f"[yellow]{result['final_wallet']:,.2f}[/yellow]",
                    f"{len(result['trades'])}",
                    f"[red]{result['max_drawdown']*100:.2f}%[/red]",
                    f"[cyan]{result['win_rate']:.2f}%[/cyan]",
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
                f"[bold blue]EMA[/bold blue]: {best_result['ema_periods'][0]} / {best_result['ema_periods'][1]}\n"
                f"[bold yellow]Profit[/bold yellow]: ${best_profit:,.2f}\n"
                f"[bold red]Max Drawdown[/bold red]: {best_result['max_drawdown']*100:.2f}%\n"
                f"[bold cyan]Win Rate[/bold cyan]: {best_result['win_rate']:.2f}%",
                title=f"[bold green]*** MEILLEUR RESULTAT — {backtest_pair} ***[/bold green]",
                border_style="green",
                padding=(1, 2),
            )

            console.print("\n")
            console.print(best_panel)
            console.print(table)
            console.print("\n" + "=" * 120 + "\n")

            # Afficher le panel de trading en temps réel
            trading_content = (
                f"[dim]Execution automatisee des ordres de trading[/dim]\n\n"
                f"[bold white]Paire de trading    :[/bold white] [bright_yellow]{data['real_pair']}[/bright_yellow]\n"
                f"[bold white]Intervalle temporel :[/bold white] [bright_cyan]{best_result['timeframe']}[/bright_cyan]\n"
                f"[bold white]Configuration EMA   :[/bold white] [bright_green]{best_result['ema_periods'][0]}[/bright_green] / [bright_green]{best_result['ema_periods'][1]}[/bright_green]\n"
                f"[bold white]Indicateur principal:[/bold white] [bright_magenta]{best_result['scenario']}[/bright_magenta]"
            )

            console.print(
                Panel(
                    trading_content,
                    title="[bold bright_cyan]TRADING ALGORITHMIQUE EN TEMPS REEL[/bold bright_cyan]",
                    title_align="center",
                    border_style="bright_cyan",
                    padding=(1, 3),
                    width=120,
                )
            )

            # === Exécuter le trading réel avec les meilleurs paramètres ===
            scenario_params = {
                "StochRSI": {"stoch_period": 14},
                "StochRSI_SMA": {"stoch_period": 14, "sma_long": 200},
                "StochRSI_ADX": {"stoch_period": 14, "adx_period": 14},
                "StochRSI_TRIX": {
                    "stoch_period": 14,
                    "trix_length": 7,
                    "trix_signal": 15,
                },
            }

            best_params = {
                "timeframe": best_result["timeframe"],
                "ema1_period": best_result["ema_periods"][0],
                "ema2_period": best_result["ema_periods"][1],
                "scenario": best_result["scenario"],
            }
            best_params.update(scenario_params.get(best_result["scenario"], {}))

            # Initialiser l'état du bot pour cette paire
            if backtest_pair not in bot_state:
                bot_state[backtest_pair] = {
                    "last_run_time": None,
                    "last_best_params": None,
                    "execution_count": 0,
                    "entry_price": None,
                    "max_price": None,
                    "trailing_stop": None,
                    "stop_loss": None,
                    "last_execution": None,
                }

            pair_state = bot_state[backtest_pair]

            try:
                execute_real_trades(
                    data["real_pair"],
                    best_result["timeframe"],
                    best_params,
                    backtest_pair,
                )
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
                    body=email_body,
                )

            # === AFFICHAGE DATE/HEURE ET PLANIFICATION ===
            current_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Historique d'exécution
            if pair_state.get("last_run_time"):
                try:
                    last_time_obj = datetime.strptime(
                        pair_state["last_run_time"], "%Y-%m-%d %H:%M:%S"
                    )
                    current_time_obj = datetime.strptime(
                        current_run_time, "%Y-%m-%d %H:%M:%S"
                    )
                    time_elapsed = current_time_obj - last_time_obj

                    history_section = (
                        f"[bold white]Derniere execution  :[/bold white] [bright_cyan]{pair_state['last_run_time']}[/bright_cyan]\n"
                        f"[bold white]Temps ecoule        :[/bold white] [bright_yellow]{time_elapsed}[/bright_yellow]"
                    )
                except Exception:
                    history_section = f"[bold white]Derniere execution  :[/bold white] [bright_cyan]{pair_state['last_run_time']}[/bright_cyan]"
            else:
                history_section = "[bold bright_green]Premiere execution du systeme de trading automatise[/bold bright_green]"

            pair_state["last_run_time"] = current_run_time
            pair_state["last_best_params"] = best_params
            pair_state["execution_count"] = pair_state.get("execution_count", 0) + 1

            # Planification UNIQUE homogène toutes les 2 minutes (indépendant du timeframe)
            # CRITICAL: Capture best_params with default argument to avoid late-binding closure issue
            # IMPORTANT: start_date will be calculated dynamically in execute_scheduled_trading
            schedule.every(2).minutes.do(
                lambda bp=backtest_pair, rp=data["real_pair"], tf=best_result[
                    "timeframe"
                ], params=dict(best_params): execute_scheduled_trading(
                    rp, tf, params, bp
                )
            )
            next_exec = "toutes les 2 minutes (720 exécutions/jour)"
            logger.info(
                f"Planification homogène 30-min activée pour {backtest_pair} - Réactivité optimale"
            )

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
                width=120,
            )

            console.print(tracking_panel)
            console.print("\n")

            save_bot_state()

        logger.info(f"Tâches planifiées actives: {len(schedule.jobs)}")

        # === BOUCLE PRINCIPALE ===
        console.print("\n" + "=" * 120)
        console.print(
            f"[bold green][OK] BOT ACTIF - Surveillance 24/7 demarree a {datetime.now().strftime('%H:%M:%S')}[/bold green]"
        )
        console.print(
            f"[bold cyan][INFO] {len(schedule.jobs)} tache(s) planifiee(s) - Prochaine execution: {schedule.next_run().strftime('%H:%M:%S') if schedule.next_run() else 'N/A'}[/bold cyan]"
        )
        console.print(
            f"[dim]Le bot execute les verifications de trading toutes les 2 minutes automatiquement[/dim]"
        )
        console.print("=" * 120 + "\n")

        logger.info("Bot actif - Surveillance des signaux de trading...")
        logger.info("Initialisation du gestionnaire d'erreurs...")
        error_handler = initialize_error_handler(
            {
                "smtp_server": config.smtp_server,
                "smtp_port": str(config.smtp_port),
                "sender_email": config.sender_email,
                "sender_password": config.smtp_password,
                "recipient_email": config.receiver_email,
            }
        )
        # Correction : accès aux attributs fictifs
        logger.info(
            f"Gestionnaire d'erreurs actif - Mode: {getattr(error_handler, 'mode', 'default')}"
        )

        # Initialisation du compteur de vérification réseau
        network_check_counter = 0
        try:
            running_counter = 0
            while True:
                try:
                    # Check circuit breaker status
                    if not error_handler.circuit_breaker.is_available():
                        logger.critical(
                            f"[CIRCUIT] Bot en mode pause - Circuit ouvert. Prochaine tentative: {error_handler.circuit_breaker.timeout_seconds}s"
                        )
                        time.sleep(10)
                        continue

                    # Execute scheduled tasks with error handling
                    try:
                        schedule.run_pending()
                    except Exception as e:
                        should_continue, _ = error_handler.handle_error(
                            error=e, context="schedule.run_pending()", critical=False
                        )
                        if not should_continue:
                            logger.warning(
                                "[CIRCUIT] Skipping task execution due to circuit breaker"
                            )
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
                        print(
                            f"[TIME] {now.strftime('%H:%M:%S')} - Bot actif (RUNNING) | Prochaine execution dans {minutes_left} min ({next_run.strftime('%H:%M:%S')})"
                        )
                    else:
                        print(
                            f"[TIME] {now.strftime('%H:%M:%S')} - Bot actif (RUNNING) | Prochaine execution non planifiée"
                        )

                    running_counter += 1
                    if running_counter % 1 == 0:  # Toutes les 10 minutes (600s sleep)
                        print(
                            f"[RUNNING] {now.strftime('%H:%M:%S')} - running en cours"
                        )

                    time.sleep(120)

                except Exception as e:
                    # Use error handler to manage main loop exceptions
                    should_continue, _ = error_handler.handle_error(
                        error=e, context="main_loop", critical=True
                    )

                    if not should_continue:
                        logger.critical(
                            f"[CIRCUIT] Main loop paused due to circuit breaker. Waiting {error_handler.circuit_breaker.timeout_seconds}s before retry"
                        )
                        time.sleep(error_handler.circuit_breaker.timeout_seconds)
                    else:
                        logger.warning(
                            f"[MAIN_LOOP] Continuing despite error - circuit still available"
                        )
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
                send_email_alert(subject="[BOT CRYPTO] ARRET CRITIQUE", body=email_body)
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
            send_email_alert(subject="[BOT CRYPTO] ARRET CRITIQUE", body=email_body)
        except:
            pass
        save_bot_state()
