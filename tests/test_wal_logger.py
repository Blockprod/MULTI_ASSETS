"""tests/test_wal_logger.py — Tests unitaires du Write-Ahead Log (B-05).

Scénarios couverts :
  1. wal_write()  — écriture d'un record, lecture vérifiée
  2. wal_replay() — WAL absent → []
  3. wal_replay() — intent sans confirmation → paire retournée
  4. wal_replay() — intent + confirmed → []
  5. wal_replay() — BUY_CONFIRMED sans SL_PLACED → paire retournée
  6. wal_replay() — BUY_CONFIRMED + SL_PLACED → []
  7. wal_replay() — ligne corrompue ignorée, reste traité
  8. wal_clear(pair) — supprime uniquement la paire demandée
  9. wal_clear(None) — vide entièrement le WAL
 10. wal_write() — exception I/O silencieuse (ne crashe pas)
 11. Thread-safety — concurrent writes ne corrompent pas le fichier
"""
from __future__ import annotations

import json
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'code', 'src'))

import wal_logger as wal_mod
from wal_logger import wal_write, wal_replay, wal_clear
from wal_logger import OP_BUY_INTENT, OP_BUY_CONFIRMED, OP_SL_PLACED


# ---------------------------------------------------------------------------
# Fixture : redirecte le WAL vers un fichier temporaire
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _tmp_wal(tmp_path, monkeypatch):
    """Redirige _WAL_FILE et _WAL_DIR vers un répertoire temporaire."""
    wal_file = str(tmp_path / 'wal.jsonl')
    monkeypatch.setattr(wal_mod, '_WAL_FILE', wal_file)
    monkeypatch.setattr(wal_mod, '_WAL_DIR', str(tmp_path))
    yield wal_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_records(wal_file: str) -> list:
    if not os.path.exists(wal_file):
        return []
    with open(wal_file, encoding='utf-8') as fh:
        return [json.loads(line) for line in fh if line.strip()]


# ---------------------------------------------------------------------------
# Tests wal_write
# ---------------------------------------------------------------------------

class TestWalWrite:
    def test_creates_file_with_record(self, _tmp_wal):
        wal_write(OP_BUY_INTENT, 'BTCUSDC', qty_str='0.001', entry_price=94500.0)
        records = _read_records(_tmp_wal)
        assert len(records) == 1
        assert records[0]['op'] == OP_BUY_INTENT
        assert records[0]['pair'] == 'BTCUSDC'
        assert records[0]['qty_str'] == '0.001'
        assert records[0]['entry_price'] == 94500.0
        assert 'ts' in records[0]

    def test_appends_multiple_records(self, _tmp_wal):
        wal_write(OP_BUY_INTENT, 'BTCUSDC')
        wal_write(OP_BUY_CONFIRMED, 'BTCUSDC', order_id='abc123')
        wal_write(OP_SL_PLACED, 'BTCUSDC', sl_order_id='def456')
        records = _read_records(_tmp_wal)
        assert len(records) == 3
        assert [r['op'] for r in records] == [OP_BUY_INTENT, OP_BUY_CONFIRMED, OP_SL_PLACED]

    def test_silent_on_io_error(self, monkeypatch, _tmp_wal):
        """wal_write ne lève jamais même si le fichier est inaccessible."""
        monkeypatch.setattr(wal_mod, '_WAL_FILE', '/nonexistent_dir/wal.jsonl')
        monkeypatch.setattr(wal_mod, '_WAL_DIR', '/nonexistent_dir')
        wal_write(OP_BUY_INTENT, 'BTCUSDC')  # ne doit pas crasher


# ---------------------------------------------------------------------------
# Tests wal_replay
# ---------------------------------------------------------------------------

class TestWalReplay:
    def test_returns_empty_when_no_wal_file(self, _tmp_wal):
        result = wal_replay()
        assert result == []

    def test_returns_empty_when_wal_is_empty(self, _tmp_wal):
        open(_tmp_wal, 'w').close()
        result = wal_replay()
        assert result == []

    def test_intent_without_confirmed_returns_pair(self, _tmp_wal):
        wal_write(OP_BUY_INTENT, 'BTCUSDC', qty_str='0.001')
        result = wal_replay()
        assert 'BTCUSDC' in result

    def test_intent_with_confirmed_returns_empty(self, _tmp_wal):
        wal_write(OP_BUY_INTENT, 'BTCUSDC')
        wal_write(OP_BUY_CONFIRMED, 'BTCUSDC', order_id='x')
        wal_write(OP_SL_PLACED, 'BTCUSDC', sl_order_id='y')
        result = wal_replay()
        assert result == []

    def test_confirmed_without_sl_returns_pair(self, _tmp_wal):
        """BUY confirmé mais SL non placé → crash entre les deux → réconciliation requise."""
        wal_write(OP_BUY_INTENT, 'ETHUSDC')
        wal_write(OP_BUY_CONFIRMED, 'ETHUSDC', order_id='ord1')
        result = wal_replay()
        assert 'ETHUSDC' in result

    def test_confirmed_with_sl_returns_empty(self, _tmp_wal):
        wal_write(OP_BUY_INTENT, 'ETHUSDC')
        wal_write(OP_BUY_CONFIRMED, 'ETHUSDC', order_id='ord1')
        wal_write(OP_SL_PLACED, 'ETHUSDC', sl_order_id='sl1')
        result = wal_replay()
        assert result == []

    def test_corrupted_line_ignored(self, _tmp_wal):
        """Une ligne JSON invalide est ignorée, les lignes suivantes sont traitées."""
        with open(_tmp_wal, 'w') as fh:
            fh.write('{"op":"BUY_INTENT","pair":"BTCUSDC","ts":1}\n')
            fh.write('NOT_JSON_AT_ALL\n')  # corrompue
            fh.write('{"op":"BUY_CONFIRMED","pair":"BTCUSDC","ts":2,"order_id":"x"}\n')
            fh.write('{"op":"SL_PLACED","pair":"BTCUSDC","ts":3,"sl_order_id":"y"}\n')
        # BUY_INTENT + BUY_CONFIRMED + SL_PLACED → doit retourner []
        result = wal_replay()
        assert result == []

    def test_multiple_pairs_independent(self, _tmp_wal):
        """Deux paires indépendantes : une confirmée, l'autre non."""
        wal_write(OP_BUY_INTENT, 'BTCUSDC')
        wal_write(OP_BUY_CONFIRMED, 'BTCUSDC', order_id='o1')
        wal_write(OP_SL_PLACED, 'BTCUSDC', sl_order_id='s1')
        wal_write(OP_BUY_INTENT, 'SOLUSDC')  # pas de confirmation
        result = wal_replay()
        assert 'BTCUSDC' not in result
        assert 'SOLUSDC' in result


# ---------------------------------------------------------------------------
# Tests wal_clear
# ---------------------------------------------------------------------------

class TestWalClear:
    def test_clear_all_empties_file(self, _tmp_wal):
        wal_write(OP_BUY_INTENT, 'BTCUSDC')
        wal_write(OP_BUY_INTENT, 'ETHUSDC')
        wal_clear(None)
        records = _read_records(_tmp_wal)
        assert records == []

    def test_clear_pair_removes_only_that_pair(self, _tmp_wal):
        wal_write(OP_BUY_INTENT, 'BTCUSDC')
        wal_write(OP_BUY_CONFIRMED, 'BTCUSDC', order_id='o1')
        wal_write(OP_BUY_INTENT, 'ETHUSDC')
        wal_clear('BTCUSDC')
        records = _read_records(_tmp_wal)
        assert all(r['pair'] == 'ETHUSDC' for r in records)
        assert len(records) == 1

    def test_clear_nonexistent_file_is_noop(self, _tmp_wal):
        """wal_clear sans fichier WAL ne crashe pas."""
        wal_clear('BTCUSDC')  # fichier absent → noop

    def test_clear_noop_when_pair_absent(self, _tmp_wal):
        wal_write(OP_BUY_INTENT, 'ETHUSDC')
        wal_clear('BTCUSDC')  # BTCUSDC n'est pas dans le WAL
        records = _read_records(_tmp_wal)
        assert len(records) == 1  # ETHUSDC intact


# ---------------------------------------------------------------------------
# Test thread-safety
# ---------------------------------------------------------------------------

class TestThreadSafety:
    def test_concurrent_writes_do_not_corrupt(self, _tmp_wal):
        """100 threads écrivent simultanément → 100 records JSON valides."""
        errors: list = []

        def _write(i: int) -> None:
            try:
                wal_write(OP_BUY_INTENT, f'PAIR{i:03d}', idx=i)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_write, args=(i,)) for i in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Erreurs dans les threads: {errors}"
        records = _read_records(_tmp_wal)
        assert len(records) == 100
        # Toutes les lignes doivent être du JSON valide (déjà vérifié par _read_records)
