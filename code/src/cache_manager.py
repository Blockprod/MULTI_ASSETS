"""
cache_manager.py — Gestion du cache disque pour les données historiques.

Contient:
- get_cache_key / get_cache_path
- is_cache_expired
- safe_cache_read / safe_cache_write
- update_cache_with_recent_data
- cleanup_expired_cache
"""
import os
import time
import json
import pickle
import hashlib
import logging
from typing import Dict, Optional, Tuple

import pandas as pd

from bot_config import config, log_exceptions, retry_with_backoff
from email_utils import send_email_alert

logger = logging.getLogger('trading_bot')

# Flag pour initialisation unique du répertoire cache
_cache_dir_initialized = False


def get_cache_key(pair: str, interval: str, params: Dict) -> str:
    """Génère une clé de cache unique pour les indicateurs."""
    key_data = f"{pair}_{interval}_{json.dumps(params, sort_keys=True)}"
    return hashlib.md5(key_data.encode()).hexdigest()


def get_cache_path(pair_symbol: str, time_interval: str, start_date: str) -> Tuple[str, str]:
    """Génère des chemins de cache sécurisés et uniques."""
    normalized_date = start_date.replace(" ", "_").replace(":", "-").replace(",", "")
    safe_name = f"{pair_symbol}_{time_interval}_{normalized_date}"

    cache_dir = config.cache_dir
    if os.path.exists(cache_dir):
        for existing_file in os.listdir(cache_dir):
            if (
                existing_file.startswith(f"{pair_symbol}_{time_interval}_")
                and existing_file.endswith(".pkl")
                and pair_symbol in existing_file
                and time_interval in existing_file
            ):
                cache_file = os.path.join(cache_dir, existing_file)
                lock_file = os.path.join(cache_dir, existing_file.replace(".pkl", ".lock"))
                return cache_file, lock_file

    cache_file = os.path.join(cache_dir, f"{safe_name}.pkl")
    lock_file = os.path.join(cache_dir, f"{safe_name}.lock")
    return cache_file, lock_file


def is_cache_expired(cache_file: str, max_age_days: int = 30) -> bool:
    """Vérifie si le cache a expiré."""
    if not os.path.exists(cache_file):
        return True
    try:
        cache_age = time.time() - os.path.getmtime(cache_file)
        return cache_age > max_age_days * 24 * 3600
    except Exception:
        return True


@log_exceptions(default_return=None)
def safe_cache_read(cache_file: str) -> Optional[pd.DataFrame]:
    """Lecture ultra-sécurisée du cache avec validation et expiration."""
    if not os.path.exists(cache_file):
        return None
    try:
        if is_cache_expired(cache_file, max_age_days=30):
            logger.info(f"Cache expiré (>30 jours): {os.path.basename(cache_file)}")
            try:
                os.remove(cache_file)
            except Exception:
                pass
            return None

        file_size = os.path.getsize(cache_file)
        if file_size == 0 or file_size > 100 * 1024 * 1024:
            try:
                os.remove(cache_file)
            except Exception:
                pass
            return None

        with open(cache_file, 'rb') as f:
            df = pickle.load(f)

        if df.empty or len(df) < 10:
            try:
                os.remove(cache_file)
            except Exception:
                pass
            return None

        logger.debug(f"Cache lu avec succès: {os.path.basename(cache_file)}")
        return df

    except (FileNotFoundError, PermissionError, EOFError, pickle.UnpicklingError):
        return None
    except Exception:
        try:
            os.remove(cache_file)
        except Exception as _e:
            logger.debug("[CACHE] Suppression cache corrompu impossible: %s", _e)
        return None


@log_exceptions(default_return=False)
def safe_cache_write(cache_file: str, lock_file: str, df: pd.DataFrame) -> bool:
    """Écriture ultra-sécurisée du cache avec verrou."""
    if df.empty:
        return False
    try:
        lock_timeout = 10
        lock_start = time.time()
        while os.path.exists(lock_file):
            # Détecte et supprime les locks périmés (processus mort)
            try:
                with open(lock_file) as _lf:
                    _pid_str = _lf.read().strip().split('_')[0]
                _dead = False
                try:
                    os.kill(int(_pid_str), 0)
                except OSError:
                    _dead = True  # processus introuvable (mort ou disparu)
                if _dead:
                    try:
                        os.remove(lock_file)
                    except Exception:
                        pass
                    break
            except Exception:
                pass
            if (time.time() - lock_start) > lock_timeout:
                logger.debug(f"Timeout verrou cache, abandon: {lock_file}")
                return False
            time.sleep(0.1)

        try:
            with open(lock_file, 'w') as lock:
                lock.write(f"{os.getpid()}_{int(time.time())}")
        except Exception:
            return False

        temp_file = cache_file + f".tmp_{os.getpid()}_{int(time.time())}"
        try:
            new_bytes = pickle.dumps(df)
            old_hash = None
            if os.path.exists(cache_file):
                with open(cache_file, 'rb') as f:
                    old_hash = hash(f.read())
            new_hash = hash(new_bytes)
            if old_hash != new_hash:
                with open(temp_file, 'wb') as f:
                    f.write(new_bytes)
                os.replace(temp_file, cache_file)  # atomique, écrase la destination (Windows-safe)
                logger.debug(f"Cache sauvegardé: {os.path.basename(cache_file)} (modifié)")
            else:
                logger.debug(f"Cache inchangé: {os.path.basename(cache_file)}")
            return True
        except Exception:
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except Exception:
                pass
            return False
        finally:
            try:
                os.remove(lock_file)
            except Exception:
                pass
    except Exception:
        return False


@retry_with_backoff(max_retries=5, base_delay=2.0)
def update_cache_with_recent_data(
    cached_df: pd.DataFrame, pair_symbol: str, time_interval: str, client
) -> pd.DataFrame:
    """Mise à jour intelligente du cache avec les dernières bougies."""
    from binance.client import Client

    try:
        if cached_df.empty:
            return cached_df

        lookback_map = {
            Client.KLINE_INTERVAL_1HOUR: "3 hours ago",
            Client.KLINE_INTERVAL_4HOUR: "12 hours ago",
            Client.KLINE_INTERVAL_1DAY: "7 days ago",
        }
        lookback = lookback_map.get(time_interval, "3 hours ago")
        recent_klines = client.get_historical_klines(pair_symbol, time_interval, lookback)

        if not recent_klines:
            return cached_df

        df_recent = pd.DataFrame(
            recent_klines,
            columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_av', 'trades', 'tb_base_av', 'tb_quote_av', 'ignore',
            ],
        )
        df_recent = df_recent[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
        df_recent[['open', 'high', 'low', 'close', 'volume']] = df_recent[
            ['open', 'high', 'low', 'close', 'volume']
        ].astype(float)
        df_recent['timestamp'] = pd.to_datetime(df_recent['timestamp'], unit='ms')
        df_recent.set_index('timestamp', inplace=True)

        last_cache_time = cached_df.index[-1]
        df_recent = df_recent[df_recent.index > last_cache_time]

        if df_recent.empty:
            logger.debug(f"Aucune nouvelle bougie pour {pair_symbol} {time_interval}")
            return cached_df

        df_merged = pd.concat([cached_df, df_recent])
        df_merged = df_merged[~df_merged.index.duplicated(keep='last')]
        df_merged = df_merged.sort_index()

        logger.info(
            f"[CACHE] Cache updated: {pair_symbol} {time_interval} (+{len(df_recent)} new candles)"
        )
        return df_merged

    except Exception as e:
        logger.debug(f"Mise à jour du cache échouée pour {pair_symbol}: {e}")
        return cached_df


def cleanup_expired_cache():
    """Nettoie les fichiers de cache expirés (>30 jours)."""
    try:
        cache_dir = config.cache_dir
        if not os.path.exists(cache_dir):
            return

        cleaned = 0
        for fname in os.listdir(cache_dir):
            if not fname.endswith('.pkl'):
                continue
            fpath = os.path.join(cache_dir, fname)
            if is_cache_expired(fpath, max_age_days=30):
                try:
                    os.remove(fpath)
                    cleaned += 1
                except Exception:
                    pass

        if cleaned > 0:
            logger.info(f"[CACHE] {cleaned} fichiers de cache expirés supprimés")
            try:
                send_email_alert(
                    "[BOT CRYPTO] Cache nettoyé",
                    f"{cleaned} fichiers de cache expirés ont été supprimés.",
                )
            except Exception:
                pass
    except Exception as e:
        logger.error(f"Erreur nettoyage cache: {e}")


def ensure_cache_dir():
    """S'assure que le répertoire cache existe. Appeler une fois au démarrage."""
    global _cache_dir_initialized
    if _cache_dir_initialized:
        return
    try:
        os.makedirs(config.cache_dir, exist_ok=True)
        _cache_dir_initialized = True
        logger.debug(f"Répertoire cache initialisé: {config.cache_dir}")
    except Exception:
        import tempfile
        config.cache_dir = tempfile.mkdtemp(prefix="crypto_cache_")
        _cache_dir_initialized = True
        logger.debug(f"Cache temporaire créé: {config.cache_dir}")
