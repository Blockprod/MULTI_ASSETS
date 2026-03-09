"""
test_cache_manager.py — Tests pour cache_manager.py.

Couvre :
  - get_cache_key : unicité, déterminisme
  - get_cache_path : chemins sécurisés, réutilisation fichier existant
  - is_cache_expired : fraîcheur, fichier manquant
  - safe_cache_read : lecture, expiration, fichier corrompu, vide, trop gros
  - safe_cache_write : écriture atomique, verrou, skip si identique
  - cleanup_expired_cache : suppression des expirés
  - ensure_cache_dir : création, fallback temp
"""

import os
import sys
import time
import pickle
import pytest

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'code', 'src'))

from cache_manager import (
    get_cache_key, get_cache_path,
    is_cache_expired, safe_cache_read, safe_cache_write,
    cleanup_expired_cache, ensure_cache_dir,
)
import cache_manager as cm
from bot_config import Config


# ---------------------------------------------------------------------------
#  Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_cache_dir(monkeypatch, tmp_path):
    """Crée un répertoire cache temporaire et configure le module."""
    cache_dir = str(tmp_path / "test_cache")
    os.makedirs(cache_dir, exist_ok=True)

    cfg = Config()
    cfg.cache_dir = cache_dir
    cfg.states_dir = str(tmp_path / "states")
    cfg.state_file = "bot_state.json"
    for attr in ('api_key', 'secret_key', 'sender_email', 'receiver_email', 'smtp_password'):
        setattr(cfg, attr, 'test')
    for attr in ('taker_fee', 'maker_fee', 'backtest_taker_fee', 'backtest_maker_fee'):
        setattr(cfg, attr, 0.0007)
    cfg.slippage_buy = cfg.slippage_sell = 0.0001
    cfg.initial_wallet = 10000.0
    cfg.api_timeout = 30

    monkeypatch.setattr(cm, 'config', cfg)
    monkeypatch.setattr(cm, '_cache_dir_initialized', False)
    return cache_dir


def _sample_df(n=50):
    """Crée un DataFrame de test."""
    idx = pd.date_range('2024-01-01', periods=n, freq='h')
    return pd.DataFrame({
        'open': np.random.uniform(90, 110, n),
        'high': np.random.uniform(100, 115, n),
        'low': np.random.uniform(85, 100, n),
        'close': np.random.uniform(90, 110, n),
        'volume': np.random.uniform(1000, 5000, n),
    }, index=idx)


# ---------------------------------------------------------------------------
#  Tests: get_cache_key
# ---------------------------------------------------------------------------

class TestGetCacheKey:
    def test_deterministic(self):
        """Mêmes inputs → même clé."""
        key1 = get_cache_key('BTCUSDC', '1h', {'ema1': 26, 'ema2': 50})
        key2 = get_cache_key('BTCUSDC', '1h', {'ema1': 26, 'ema2': 50})
        assert key1 == key2

    def test_different_pair(self):
        """Paires différentes → clés différentes."""
        key1 = get_cache_key('BTCUSDC', '1h', {'ema1': 26})
        key2 = get_cache_key('ETHUSDC', '1h', {'ema1': 26})
        assert key1 != key2

    def test_different_params(self):
        """Params différents → clés différentes."""
        key1 = get_cache_key('BTCUSDC', '1h', {'ema1': 26})
        key2 = get_cache_key('BTCUSDC', '1h', {'ema1': 50})
        assert key1 != key2

    def test_param_order_invariant(self):
        """L'ordre des params ne change pas la clé (json sort_keys=True)."""
        key1 = get_cache_key('BTCUSDC', '1h', {'ema1': 26, 'ema2': 50})
        key2 = get_cache_key('BTCUSDC', '1h', {'ema2': 50, 'ema1': 26})
        assert key1 == key2


# ---------------------------------------------------------------------------
#  Tests: get_cache_path
# ---------------------------------------------------------------------------

class TestGetCachePath:
    def test_returns_paths(self, tmp_cache_dir):
        """Retourne un tuple (cache_file, lock_file)."""
        cache_file, lock_file = get_cache_path('BTCUSDC', '1h', '2024-01-01 00:00:00')
        assert cache_file.endswith('.pkl')
        assert lock_file.endswith('.lock')

    def test_reuses_existing_file(self, tmp_cache_dir):
        """Si un fichier cache existant matche, il est réutilisé."""
        # Créer un fichier existant
        existing = os.path.join(tmp_cache_dir, 'BTCUSDC_1h_2024-01-01_00-00-00.pkl')
        with open(existing, 'wb') as f:
            pickle.dump(pd.DataFrame(), f)

        cache_file, _ = get_cache_path('BTCUSDC', '1h', '2024-01-15 00:00:00')
        assert cache_file == existing

    def test_sanitizes_date(self, tmp_cache_dir):
        """Les caractères spéciaux dans la date sont remplacés."""
        cache_file, _ = get_cache_path('BTCUSDC', '1h', '2024-01-01 12:30:00')
        assert ' ' not in os.path.basename(cache_file)
        assert ':' not in os.path.basename(cache_file)


# ---------------------------------------------------------------------------
#  Tests: is_cache_expired
# ---------------------------------------------------------------------------

class TestIsCacheExpired:
    def test_missing_file(self):
        """Fichier inexistant → expiré."""
        assert is_cache_expired('/nonexistent/file.pkl') is True

    def test_fresh_file(self, tmp_cache_dir):
        """Fichier récent → pas expiré."""
        fpath = os.path.join(tmp_cache_dir, 'test.pkl')
        with open(fpath, 'w') as f:
            f.write('data')
        assert is_cache_expired(fpath, max_age_days=30) is False

    def test_old_file(self, tmp_cache_dir):
        """Fichier > max_age_days → expiré."""
        fpath = os.path.join(tmp_cache_dir, 'old.pkl')
        with open(fpath, 'w') as f:
            f.write('data')
        # Simuler un fichier vieux de 31 jours
        old_time = time.time() - (31 * 24 * 3600)
        os.utime(fpath, (old_time, old_time))
        assert is_cache_expired(fpath, max_age_days=30) is True


# ---------------------------------------------------------------------------
#  Tests: safe_cache_read
# ---------------------------------------------------------------------------

class TestSafeCacheRead:
    def test_reads_valid_cache(self, tmp_cache_dir):
        """Lecture d'un cache valide."""
        df = _sample_df()
        fpath = os.path.join(tmp_cache_dir, 'valid.pkl')
        with open(fpath, 'wb') as f:
            pickle.dump(df, f)

        result = safe_cache_read(fpath)
        assert result is not None
        assert len(result) == len(df)

    def test_returns_none_missing(self):
        """Fichier inexistant → None."""
        result = safe_cache_read('/nofile.pkl')
        assert result is None

    def test_returns_none_expired(self, tmp_cache_dir):
        """Cache expiré → None (et fichier supprimé)."""
        df = _sample_df()
        fpath = os.path.join(tmp_cache_dir, 'expired.pkl')
        with open(fpath, 'wb') as f:
            pickle.dump(df, f)

        old_time = time.time() - (31 * 24 * 3600)
        os.utime(fpath, (old_time, old_time))

        result = safe_cache_read(fpath)
        assert result is None

    def test_returns_none_empty_file(self, tmp_cache_dir):
        """Fichier vide (0 bytes) → None."""
        fpath = os.path.join(tmp_cache_dir, 'empty.pkl')
        open(fpath, 'w').close()

        result = safe_cache_read(fpath)
        assert result is None

    def test_returns_none_corrupted(self, tmp_cache_dir):
        """Fichier corrompu → None."""
        fpath = os.path.join(tmp_cache_dir, 'corrupt.pkl')
        with open(fpath, 'wb') as f:
            f.write(b'not a valid pickle')

        result = safe_cache_read(fpath)
        assert result is None

    def test_returns_none_small_df(self, tmp_cache_dir):
        """DataFrame < 10 lignes → None (trop petit)."""
        df = _sample_df(n=5)
        fpath = os.path.join(tmp_cache_dir, 'small.pkl')
        with open(fpath, 'wb') as f:
            pickle.dump(df, f)

        result = safe_cache_read(fpath)
        assert result is None


# ---------------------------------------------------------------------------
#  Tests: safe_cache_write
# ---------------------------------------------------------------------------

class TestSafeCacheWrite:
    def test_writes_valid(self, tmp_cache_dir):
        """Écriture réussie retourne True."""
        df = _sample_df()
        fpath = os.path.join(tmp_cache_dir, 'write.pkl')
        lpath = os.path.join(tmp_cache_dir, 'write.lock')

        result = safe_cache_write(fpath, lpath, df)
        assert result is True
        assert os.path.exists(fpath)

        # Relecture pour vérifier
        with open(fpath, 'rb') as f:
            loaded = pickle.load(f)
        assert len(loaded) == len(df)

    def test_empty_df_skipped(self, tmp_cache_dir):
        """DataFrame vide → False, pas d'écriture."""
        fpath = os.path.join(tmp_cache_dir, 'empty_write.pkl')
        lpath = os.path.join(tmp_cache_dir, 'empty_write.lock')

        result = safe_cache_write(fpath, lpath, pd.DataFrame())
        assert result is False
        assert not os.path.exists(fpath)

    def test_lock_cleaned_up(self, tmp_cache_dir):
        """Le fichier lock est supprimé après écriture."""
        df = _sample_df()
        fpath = os.path.join(tmp_cache_dir, 'locked.pkl')
        lpath = os.path.join(tmp_cache_dir, 'locked.lock')

        safe_cache_write(fpath, lpath, df)
        assert not os.path.exists(lpath)

    def test_skip_identical_content(self, tmp_cache_dir):
        """Si le contenu est identique, pas de réécriture (hash match)."""
        df = _sample_df()
        fpath = os.path.join(tmp_cache_dir, 'ident.pkl')
        lpath = os.path.join(tmp_cache_dir, 'ident.lock')

        safe_cache_write(fpath, lpath, df)
        mtime1 = os.path.getmtime(fpath)

        # Petite pause pour que le mtime diffère
        time.sleep(0.05)
        safe_cache_write(fpath, lpath, df)
        mtime2 = os.path.getmtime(fpath)

        assert mtime1 == mtime2  # pas réécrit

    def test_stale_lock_removed(self, tmp_cache_dir):
        """Lock d'un processus mort → supprimé automatiquement."""
        df = _sample_df()
        fpath = os.path.join(tmp_cache_dir, 'stale.pkl')
        lpath = os.path.join(tmp_cache_dir, 'stale.lock')

        # Créer un lock avec un PID inexistant
        with open(lpath, 'w') as f:
            f.write(f"99999999_{int(time.time())}")

        result = safe_cache_write(fpath, lpath, df)
        assert result is True


# ---------------------------------------------------------------------------
#  Tests: cleanup_expired_cache
# ---------------------------------------------------------------------------

class TestCleanupExpiredCache:
    def test_removes_expired(self, tmp_cache_dir, monkeypatch):
        """Supprime les fichiers expirés."""
        # Neutraliser l'envoi d'email
        monkeypatch.setattr('cache_manager.send_email_alert', lambda *a, **kw: None, raising=False)

        fpath = os.path.join(tmp_cache_dir, 'old_cache.pkl')
        with open(fpath, 'wb') as f:
            pickle.dump(_sample_df(), f)

        old_time = time.time() - (31 * 24 * 3600)
        os.utime(fpath, (old_time, old_time))

        cleanup_expired_cache()
        assert not os.path.exists(fpath)

    def test_keeps_fresh(self, tmp_cache_dir, monkeypatch):
        """Garde les fichiers récents."""
        monkeypatch.setattr('cache_manager.send_email_alert', lambda *a, **kw: None, raising=False)

        fpath = os.path.join(tmp_cache_dir, 'fresh_cache.pkl')
        with open(fpath, 'wb') as f:
            pickle.dump(_sample_df(), f)

        cleanup_expired_cache()
        assert os.path.exists(fpath)

    def test_ignores_non_pkl(self, tmp_cache_dir, monkeypatch):
        """Ne touche pas aux fichiers non-.pkl."""
        monkeypatch.setattr('cache_manager.send_email_alert', lambda *a, **kw: None, raising=False)

        fpath = os.path.join(tmp_cache_dir, 'data.json')
        with open(fpath, 'w') as f:
            f.write('{}')

        old_time = time.time() - (31 * 24 * 3600)
        os.utime(fpath, (old_time, old_time))

        cleanup_expired_cache()
        assert os.path.exists(fpath)


# ---------------------------------------------------------------------------
#  Tests: ensure_cache_dir
# ---------------------------------------------------------------------------

class TestEnsureCacheDir:
    def test_creates_dir(self, tmp_cache_dir, monkeypatch):
        """Crée le répertoire cache si inexistant."""
        new_dir = os.path.join(tmp_cache_dir, 'new_subdir')
        cm.config.cache_dir = new_dir
        monkeypatch.setattr(cm, '_cache_dir_initialized', False)

        ensure_cache_dir()
        assert os.path.isdir(new_dir)

    def test_idempotent(self, tmp_cache_dir, monkeypatch):
        """Appels multiples ne lèvent pas d'erreur."""
        monkeypatch.setattr(cm, '_cache_dir_initialized', False)
        ensure_cache_dir()
        ensure_cache_dir()  # deuxième appel = noop
        assert cm._cache_dir_initialized is True

    def test_fallback_temp(self, monkeypatch, tmp_path):
        """Si la création échoue, bascule sur un tempdir."""
        invalid_dir = str(tmp_path / 'NUL' / 'impossible')  # pas créable sous Windows

        cfg = Config()
        cfg.cache_dir = invalid_dir
        for attr in ('api_key', 'secret_key', 'sender_email', 'receiver_email', 'smtp_password'):
            setattr(cfg, attr, 'test')

        monkeypatch.setattr(cm, 'config', cfg)
        monkeypatch.setattr(cm, '_cache_dir_initialized', False)

        # Force une erreur dans os.makedirs
        original_makedirs = os.makedirs
        def failing_makedirs(path, **kw):
            if path == invalid_dir:
                raise PermissionError("Cannot create directory")
            return original_makedirs(path, **kw)
        monkeypatch.setattr(os, 'makedirs', failing_makedirs)

        ensure_cache_dir()
        assert cm._cache_dir_initialized is True
        assert cfg.cache_dir != invalid_dir  # basculé sur tempdir
