"""Tests pour metrics.py (P2-04) — observabilité du bot."""
from __future__ import annotations

import json
import os
import sys
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'code', 'src'))


def _import_metrics():
    """Importe write_metrics et read_metrics depuis metrics.py."""
    with patch.dict(os.environ, {
        'BINANCE_API_KEY': 'k', 'BINANCE_SECRET_KEY': 's',
        'SENDER_EMAIL': 'a@b.c', 'RECEIVER_EMAIL': 'a@b.c',
        'GOOGLE_MAIL_PASSWORD': 'p',
    }):
        try:
            from metrics import write_metrics, read_metrics
            return write_metrics, read_metrics
        except Exception:
            pytest.skip("Cannot import metrics")


# ---------------------------------------------------------------------------
# Fixture : runtime mock
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_runtime():
    r = MagicMock()
    r.save_failure_count = 0
    r.taker_fee = 0.0007
    r.maker_fee = 0.0002
    return r


@pytest.fixture
def sample_bot_state():
    return {
        'emergency_halt': False,
        'emergency_halt_reason': None,
        'BTCUSDC': {
            'last_order_side': 'BUY',
            'entry_price': 62000.0,
            'oos_blocked': False,
            'drawdown_halted': False,
            'sl_exchange_placed': True,
            'last_execution': '2026-04-06T10:25:00Z',
            'execution_count': 42,
        },
        'ETHUSDC': {
            'last_order_side': None,
            'entry_price': None,
            'oos_blocked': True,
            'drawdown_halted': False,
            'sl_exchange_placed': False,
            'last_execution': '2026-04-06T09:00:00Z',
            'execution_count': 15,
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestWriteMetrics:
    """P2-04: write_metrics écrit un JSON valide dans metrics/metrics.json."""

    def test_write_creates_json_file(self, mock_runtime, sample_bot_state, tmp_path):
        """write_metrics doit créer un fichier metrics.json."""
        write_metrics, _ = _import_metrics()
        import metrics as _m
        original_file = _m._METRICS_FILE

        metrics_file = str(tmp_path / 'metrics.json')
        _m._METRICS_FILE = metrics_file
        _m._METRICS_DIR = str(tmp_path)
        try:
            result = write_metrics(
                bot_state=sample_bot_state,
                runtime=mock_runtime,
                pairs=['BTCUSDC', 'ETHUSDC'],
            )
            assert result is True, "write_metrics doit retourner True en cas de succès"
            assert os.path.exists(metrics_file), "Le fichier metrics.json doit avoir été créé"
        finally:
            _m._METRICS_FILE = original_file

    def test_write_returns_valid_json(self, mock_runtime, sample_bot_state, tmp_path):
        """Le fichier metrics.json doit être un JSON valide avec les clés attendues."""
        write_metrics, _ = _import_metrics()
        import metrics as _m
        original_file = _m._METRICS_FILE
        original_dir = _m._METRICS_DIR

        metrics_file = str(tmp_path / 'metrics.json')
        _m._METRICS_FILE = metrics_file
        _m._METRICS_DIR = str(tmp_path)
        try:
            write_metrics(
                bot_state=sample_bot_state,
                runtime=mock_runtime,
                pairs=['BTCUSDC', 'ETHUSDC'],
            )
            with open(metrics_file, encoding='utf-8') as fh:
                data = json.load(fh)

            assert 'timestamp_utc' in data
            assert 'emergency_halt' in data
            assert 'save_failure_count' in data
            assert 'taker_fee' in data
            assert 'pairs' in data
        finally:
            _m._METRICS_FILE = original_file
            _m._METRICS_DIR = original_dir

    def test_write_captures_pair_state(self, mock_runtime, sample_bot_state, tmp_path):
        """Les données par paire doivent être correctement extraites."""
        write_metrics, _ = _import_metrics()
        import metrics as _m
        original_file = _m._METRICS_FILE
        original_dir = _m._METRICS_DIR

        metrics_file = str(tmp_path / 'metrics.json')
        _m._METRICS_FILE = metrics_file
        _m._METRICS_DIR = str(tmp_path)
        try:
            write_metrics(
                bot_state=sample_bot_state,
                runtime=mock_runtime,
                pairs=['BTCUSDC', 'ETHUSDC'],
            )
            with open(metrics_file, encoding='utf-8') as fh:
                data = json.load(fh)

            btc = data['pairs']['BTCUSDC']
            assert btc['in_position'] is True
            assert btc['entry_price'] == 62000.0
            assert btc['sl_placed'] is True
            assert btc['execution_count'] == 42

            eth = data['pairs']['ETHUSDC']
            assert eth['in_position'] is False
            assert eth['oos_blocked'] is True
        finally:
            _m._METRICS_FILE = original_file
            _m._METRICS_DIR = original_dir

    def test_write_captures_emergency_halt(self, mock_runtime, tmp_path):
        """emergency_halt=True doit être reflété dans le JSON."""
        write_metrics, _ = _import_metrics()
        import metrics as _m
        original_file = _m._METRICS_FILE
        original_dir = _m._METRICS_DIR

        metrics_file = str(tmp_path / 'metrics.json')
        _m._METRICS_FILE = metrics_file
        _m._METRICS_DIR = str(tmp_path)
        try:
            write_metrics(
                bot_state={'emergency_halt': True, 'emergency_halt_reason': 'test'},
                runtime=mock_runtime,
                pairs=[],
            )
            with open(metrics_file, encoding='utf-8') as fh:
                data = json.load(fh)
            assert data['emergency_halt'] is True
        finally:
            _m._METRICS_FILE = original_file
            _m._METRICS_DIR = original_dir

    def test_write_with_circuit_breaker(self, mock_runtime, sample_bot_state, tmp_path):
        """circuit_breaker.is_available() doit être capturé si fourni."""
        write_metrics, _ = _import_metrics()
        import metrics as _m
        original_file = _m._METRICS_FILE
        original_dir = _m._METRICS_DIR

        cb = MagicMock()
        cb.is_available.return_value = True

        metrics_file = str(tmp_path / 'metrics.json')
        _m._METRICS_FILE = metrics_file
        _m._METRICS_DIR = str(tmp_path)
        try:
            write_metrics(
                bot_state=sample_bot_state,
                runtime=mock_runtime,
                circuit_breaker=cb,
                pairs=['BTCUSDC'],
            )
            with open(metrics_file, encoding='utf-8') as fh:
                data = json.load(fh)
            assert data['circuit_breaker_available'] is True
        finally:
            _m._METRICS_FILE = original_file
            _m._METRICS_DIR = original_dir

    def test_write_returns_false_on_error(self, mock_runtime, sample_bot_state, tmp_path):
        """write_metrics doit retourner False si l'écriture échoue."""
        write_metrics, _ = _import_metrics()
        import metrics as _m
        import builtins

        original_file = _m._METRICS_FILE
        original_dir = _m._METRICS_DIR
        metrics_file = str(tmp_path / 'metrics.json')
        _m._METRICS_FILE = metrics_file
        _m._METRICS_DIR = str(tmp_path)

        original_open = builtins.open

        def _failing_open(path, *a, **kw):
            if str(path).endswith('.tmp'):
                raise OSError("Simulated write error")
            return original_open(path, *a, **kw)

        try:
            with patch('builtins.open', side_effect=_failing_open):
                result = write_metrics(
                    bot_state=sample_bot_state,
                    runtime=mock_runtime,
                    pairs=[],
                )
            assert result is False, "write_metrics doit retourner False en cas d'erreur"
        finally:
            _m._METRICS_FILE = original_file
            _m._METRICS_DIR = original_dir


class TestReadMetrics:
    """P2-04: read_metrics lit le snapshot JSON."""

    def test_read_returns_none_if_file_absent(self, tmp_path):
        """read_metrics doit retourner None si le fichier n'existe pas."""
        _, read_metrics = _import_metrics()
        import metrics as _m
        original_file = _m._METRICS_FILE

        _m._METRICS_FILE = str(tmp_path / 'nonexistent.json')
        try:
            result = read_metrics()
            assert result is None
        finally:
            _m._METRICS_FILE = original_file

    def test_read_after_write_returns_same_data(self, mock_runtime, sample_bot_state, tmp_path):
        """read_metrics après write_metrics doit retrouver les données écrites."""
        write_metrics, read_metrics = _import_metrics()
        import metrics as _m
        original_file = _m._METRICS_FILE
        original_dir = _m._METRICS_DIR

        metrics_file = str(tmp_path / 'metrics.json')
        _m._METRICS_FILE = metrics_file
        _m._METRICS_DIR = str(tmp_path)
        try:
            write_metrics(
                bot_state=sample_bot_state,
                runtime=mock_runtime,
                pairs=['BTCUSDC'],
            )
            data = read_metrics()
            assert data is not None
            assert data['taker_fee'] == 0.0007
            assert 'BTCUSDC' in data['pairs']
        finally:
            _m._METRICS_FILE = original_file
            _m._METRICS_DIR = original_dir
