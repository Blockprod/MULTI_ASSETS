"""
data_fetcher.py — Binance historical data retrieval & validation.

Extracted from MULTI_SYMBOLS.py (P3-SRP).  Groups all functions that fetch
data from the Binance API or validate raw OHLCV DataFrames.

All functions accept explicit parameters (no module-level globals).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional, Tuple

import pandas as pd
from binance.exceptions import BinanceAPIException

from bot_config import log_exceptions, retry_with_backoff
from cache_manager import (
    ensure_cache_dir, get_cache_path,
    safe_cache_read, safe_cache_write,
    update_cache_with_recent_data,
)

logger = logging.getLogger(__name__)


# ─── Exchange Info Cache (TTL 24 h) ──────────────────────────────────────────

_exchange_info_cache: Dict[str, Any] = {'data': None, 'ts': 0.0}
_EXCHANGE_INFO_TTL: float = 24.0 * 3600.0  # 24 heures


def get_cached_exchange_info(client: Any) -> Dict[str, Any]:
    """Retourne les infos d'exchange depuis le cache mémoire (TTL 24 h).

    Réduit les appels API répétitifs à chaque cycle de trading.
    Le cache est invalidé après 24 h ou lors du premier appel.

    Parameters
    ----------
    client : BinanceFinalClient
        Client Binance avec méthode ``get_exchange_info()``.

    Returns
    -------
    dict
        Réponse brute de ``/api/v3/exchangeInfo``.
    """
    if (
        _exchange_info_cache['data'] is None
        or (time.time() - _exchange_info_cache['ts']) > _EXCHANGE_INFO_TTL
    ):
        _exchange_info_cache['data'] = client.get_exchange_info()
        _exchange_info_cache['ts'] = time.time()
        logger.debug("[CACHE] exchange_info rechargé (TTL expiré ou premier appel)")
    result = _exchange_info_cache['data']
    if not isinstance(result, dict):
        raise RuntimeError("get_cached_exchange_info: données cache invalides")
    return result


# ─── Data Integrity Validation ───────────────────────────────────────────────

@log_exceptions(default_return=False)
def validate_data_integrity(df: pd.DataFrame) -> bool:
    """Valide l'intégrité des données de marché.

    Checks: non-empty, no negative OHLCV, OHLC coherence, temporal gaps.
    """
    if df.empty:
        return False
    # Vérifier les valeurs négatives
    if (df[['open', 'high', 'low', 'close', 'volume']] < 0).any().any():
        logger.warning("Valeurs negatives detectees dans les donnees")
        return False
    # Vérifier la cohérence OHLC
    invalid_ohlc = (df['high'] < df[['open', 'close']].max(axis=1)) | \
                   (df['low'] > df[['open', 'close']].min(axis=1))
    if invalid_ohlc.any():
        logger.warning("Donnees OHLC incoherentes detectees")
        return False
    # Détecter les gaps temporels (silencieux)
    time_diff = df.index.to_series().diff()
    expected_interval = time_diff.mode()[0] if not time_diff.mode().empty else None
    if expected_interval:
        gaps = time_diff > expected_interval * 1.5
        if gaps.any():
            pass  # Silence, plus de warning
    return True


# ─── Historical Data Fetch ───────────────────────────────────────────────────

@retry_with_backoff(max_retries=3, base_delay=2.0)
@log_exceptions(default_return=pd.DataFrame())
def fetch_historical_data(
    pair_symbol: str,
    time_interval: str,
    start_date: str,
    client: Any,
    *,
    force_refresh: bool = False,
    verbose_logs: bool = False,
    check_network_fn: Optional[Callable[[], bool]] = None,
    send_alert_fn: Optional[Callable[..., Any]] = None,
    data_error_template_fn: Optional[Callable[..., Tuple[str, str]]] = None,
    network_error_template_fn: Optional[Callable[..., Tuple[str, str]]] = None,
) -> pd.DataFrame:
    """Récupère les données historiques avec validation et cache thread-safe.

    Workflow : cache read → incremental update → API download → validation
    → cache write.

    Parameters
    ----------
    pair_symbol : str
        Paire de trading (ex. ``"BTCUSDC"``).
    time_interval : str
        Intervalle kline (ex. ``"1h"``).
    start_date : str
        Date de début (ex. ``"1 Jan 2024"``).
    client : BinanceFinalClient
        Client Binance.
    force_refresh : bool
        Ignorer le cache et forcer le téléchargement.
    verbose_logs : bool
        Activer les logs détaillés.
    check_network_fn : callable, optional
        Vérification connectivité réseau.
    send_alert_fn : callable, optional
        Envoi d'email d'alerte.
    data_error_template_fn : callable, optional
        Template pour erreur données.
    network_error_template_fn : callable, optional
        Template pour erreur réseau.

    Returns
    -------
    pd.DataFrame
        DataFrame OHLCV indexé par timestamp.  Vide en cas d'échec.

    Raises
    ------
    BinanceAPIException
        Re-raised après envoi d'alerte si applicable.
    """
    try:
        # Initialiser le cache via cache_manager
        ensure_cache_dir()

        # Générer des chemins sécurisés
        cache_file, lock_file = get_cache_path(pair_symbol, time_interval, start_date)

        # Lecture ultra-sécurisée du cache (sauf si force_refresh=True)
        if not force_refresh:
            cached_df = safe_cache_read(cache_file)
            if cached_df is not None and not cached_df.empty:
                # Mise à jour intelligente: ajouter les dernières bougies
                updated_df = update_cache_with_recent_data(
                    cached_df, pair_symbol, time_interval, client
                )

                # Sauvegarder le cache mis à jour
                safe_cache_write(cache_file, lock_file, updated_df)

                logger.info(
                    f"[OK] Cache used + updated: {pair_symbol} {time_interval} "
                    f"({len(updated_df)} candles)"
                )
                return updated_df

        # Récupération depuis l'API
        if not verbose_logs:
            logger.info(f"Telechargement {pair_symbol} {time_interval}...")
        else:
            logger.info(
                f"Debut telechargement pour {pair_symbol} {time_interval} "
                f"depuis {start_date}"
            )

        try:
            klines_raw = client.get_historical_klines(
                pair_symbol, time_interval, start_date
            )

            if not klines_raw:
                raise ValueError(
                    f"Aucune donnee pour {pair_symbol} et {time_interval}"
                )

            if not verbose_logs:
                first_ts = pd.Timestamp(klines_raw[0][0], unit='ms').strftime('%Y-%m-%d')
                last_ts = pd.Timestamp(klines_raw[-1][0], unit='ms').strftime('%Y-%m-%d')
                logger.info(
                    f"OK: {len(klines_raw)} candles | {first_ts} -> {last_ts}"
                )
            else:
                logger.info(
                    f"Telechargement complete: {len(klines_raw)} candles "
                    f"recuperees pour {pair_symbol}"
                )
                logger.info(
                    f"  Date premiere bougie: "
                    f"{pd.Timestamp(klines_raw[0][0], unit='ms')}"
                )
                logger.info(
                    f"  Date derniere bougie: "
                    f"{pd.Timestamp(klines_raw[-1][0], unit='ms')}"
                )

        except Exception as e:
            logger.error(f"Erreur lors du telechargement: {e}")
            raise

        all_klines = klines_raw

        # Création du DataFrame
        df = pd.DataFrame(all_klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_av', 'trades', 'tb_base_av',
            'tb_quote_av', 'ignore',
        ])
        df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]

        # Conversion sécurisée
        numeric_columns = ['open', 'high', 'low', 'close', 'volume']
        df[numeric_columns] = df[numeric_columns].astype(float)

        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)

        # Validation des données
        if not validate_data_integrity(df):
            logger.warning(f"Donnees invalides pour {pair_symbol}")

        # Sauvegarde ultra-sécurisée du cache
        safe_cache_write(cache_file, lock_file, df)

        return df

    except BinanceAPIException as e:
        logger.error(f"Erreur API Binance: {e}")
        if send_alert_fn and data_error_template_fn:
            try:
                subj, body = data_error_template_fn(
                    pair_symbol, time_interval, start_date, str(e)
                )
                send_alert_fn(subject=subj, body_main=body, client=client)
            except Exception as _exc:
                logger.debug("[data_fetcher] send_alert (data_error) a échoué: %s", _exc)
        raise
    except Exception as e:
        error_str = str(e)
        logger.error(f"Erreur recuperation donnees: {e}")

        # Détecter les erreurs de réseau
        network_keywords = [
            'nameresolutionerror', 'getaddrinfo failed',
            'max retries exceeded', 'connection',
        ]
        if any(kw in error_str.lower() for kw in network_keywords):
            logger.warning(
                "Erreur de connectivité détectée, tentative de récupération..."
            )

            _check_net = check_network_fn or (lambda: False)
            if _check_net():
                logger.info("Connexion rétablie, nouvelle tentative...")
                time.sleep(3)
                try:
                    klines_raw = client.get_historical_klines(
                        pair_symbol, time_interval, start_date
                    )
                    if klines_raw:
                        df = pd.DataFrame(klines_raw, columns=[
                            'timestamp', 'open', 'high', 'low', 'close',
                            'volume', 'close_time', 'quote_av', 'trades',
                            'tb_base_av', 'tb_quote_av', 'ignore',
                        ])
                        df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
                        numeric_columns = ['open', 'high', 'low', 'close', 'volume']
                        df[numeric_columns] = df[numeric_columns].astype(float)
                        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                        df.set_index('timestamp', inplace=True)
                        logger.info(
                            "Données récupérées après rétablissement connexion"
                        )
                        return df
                except Exception as retry_error:
                    logger.error(
                        f"echec après rétablissement: {retry_error}"
                    )

        if send_alert_fn and network_error_template_fn:
            try:
                subj, body = network_error_template_fn(pair_symbol, str(e))
                send_alert_fn(subject=subj, body_main=body, client=client)
            except Exception as _exc:
                logger.debug("[data_fetcher] send_alert (network_error) a échoué: %s", _exc)
        return pd.DataFrame()


# ─── Trading Fees ────────────────────────────────────────────────────────────

def get_binance_trading_fees(
    client: Any,
    symbol: Optional[str] = None,
    default_taker: float = 0.001,
    default_maker: float = 0.001,
) -> Tuple[float, float]:
    """Récupère les frais de trading réels depuis l'API Binance.

    Parameters
    ----------
    client : BinanceFinalClient
        Client Binance.
    symbol : str, optional
        Symbole pour la recherche de frais. Si None, utilise
        ``config.fee_reference_symbol`` (MI-02).
    default_taker, default_maker : float
        Valeurs de repli si l'appel API échoue.

    Returns
    -------
    tuple[float, float]
        ``(taker_fee, maker_fee)`` en fraction (ex. 0.001 = 0.1 %).
    """
    if symbol is None:
        from bot_config import config as _cfg_fees
        symbol = getattr(_cfg_fees, 'fee_reference_symbol', 'TRXUSDC')  # MI-02
    try:
        account_info = client.get_account()
        taker_commission = account_info.get('takerCommission', 10) / 10000
        maker_commission = account_info.get('makerCommission', 10) / 10000
        logger.info(
            f"[FEES REELS] Binance - Taker: {taker_commission*100:.4f}%, "
            f"Maker: {maker_commission*100:.4f}%"
        )
        return taker_commission, maker_commission
    except Exception as e:
        logger.warning(
            f"[FEES] Impossible de récupérer frais Binance: {e}. "
            "Utilisation valeurs par défaut."
        )
        return default_taker, default_maker
