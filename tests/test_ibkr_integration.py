"""tests/test_ibkr_integration.py — Tests d'intégration scénarios risque IBKR.

Couvre :
  - WAL replay : détection opération incomplète BUY_CONFIRMED sans SL_PLACED
  - WAL replay : opération complète ignorée
  - WAL clear : nettoyage entrées par paire
  - IBKR state P0-1 : JSON non signé → IBKRStateError (regression fail-open)
  - IBKR state P0-2 : déduplication octets (regression hash() PYTHONHASHSEED)
  - IBKR state : HMAC valide → chargement correct
  - IBKR state : HMAC invalide → IBKRStateError
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

# ─── Path setup ───────────────────────────────────────────────────────────────
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
_SRC_DIR = os.path.join(_ROOT_DIR, "code", "src")
_IBKR_DIR = os.path.join(_SRC_DIR, "ibkr")

for _p in (_SRC_DIR, _IBKR_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("BINANCE_API_KEY", "TEST_PLACEHOLDER")
os.environ.setdefault("BINANCE_SECRET_KEY", "TEST_PLACEHOLDER")
os.environ.setdefault("IBKR_SECRET", "TEST_IBKR_SECRET_32BYTES_PADDING!")


# ─── WAL replay ───────────────────────────────────────────────────────────────

class TestIBKRWalReplay:
    """Tests du WAL ibkr_wal — lecture et replay au démarrage."""

    def _write_wal(self, path: str, entries: list) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            for e in entries:
                fh.write(json.dumps(e) + "\n")

    def test_replay_detects_buy_without_sl(self, tmp_path):
        """FX_BUY_CONFIRMED sans FX_SL_PLACED → opération incomplète retournée."""
        from ibkr_wal import ibkr_wal_replay, _WAL_FILE, OP_FX_BUY_CONFIRMED
        import ibkr_wal as wal_module

        wal_path = tmp_path / "ibkr_wal.jsonl"
        self._write_wal(str(wal_path), [
            {"op": "FX_BUY_INTENT", "pair": "EURUSD", "ts": 1.0},
            {"op": "FX_BUY_CONFIRMED", "pair": "EURUSD", "ts": 2.0, "qty": 20000},
        ])
        original = wal_module._WAL_FILE
        wal_module._WAL_FILE = wal_path
        try:
            result = ibkr_wal_replay()
            assert len(result) == 1
            assert result[0]["pair"] == "EURUSD"
            assert result[0]["op"] == OP_FX_BUY_CONFIRMED
        finally:
            wal_module._WAL_FILE = original

    def test_replay_ignores_complete_operation(self, tmp_path):
        """FX_BUY_CONFIRMED + FX_SL_PLACED → opération complète, pas retournée."""
        import ibkr_wal as wal_module
        from ibkr_wal import ibkr_wal_replay

        wal_path = tmp_path / "ibkr_wal.jsonl"
        self._write_wal(str(wal_path), [
            {"op": "FX_BUY_CONFIRMED", "pair": "EURUSD", "ts": 1.0},
            {"op": "FX_SL_PLACED", "pair": "EURUSD", "ts": 2.0},
        ])
        original = wal_module._WAL_FILE
        wal_module._WAL_FILE = wal_path
        try:
            result = ibkr_wal_replay()
            assert result == []
        finally:
            wal_module._WAL_FILE = original

    def test_replay_empty_wal(self, tmp_path):
        """WAL absent → retourne liste vide sans erreur."""
        import ibkr_wal as wal_module
        from ibkr_wal import ibkr_wal_replay

        wal_path = tmp_path / "non_existent_wal.jsonl"
        original = wal_module._WAL_FILE
        wal_module._WAL_FILE = wal_path
        try:
            result = ibkr_wal_replay()
            assert result == []
        finally:
            wal_module._WAL_FILE = original

    def test_replay_multiple_pairs_partial(self, tmp_path):
        """Plusieurs paires : seule GBPUSD incomplète est retournée."""
        import ibkr_wal as wal_module
        from ibkr_wal import ibkr_wal_replay

        wal_path = tmp_path / "ibkr_wal.jsonl"
        self._write_wal(str(wal_path), [
            # EURUSD : complet
            {"op": "FX_BUY_CONFIRMED", "pair": "EURUSD", "ts": 1.0},
            {"op": "FX_SL_PLACED", "pair": "EURUSD", "ts": 1.5},
            # GBPUSD : incomplet
            {"op": "FX_BUY_CONFIRMED", "pair": "GBPUSD", "ts": 2.0},
        ])
        original = wal_module._WAL_FILE
        wal_module._WAL_FILE = wal_path
        try:
            result = ibkr_wal_replay()
            assert len(result) == 1
            assert result[0]["pair"] == "GBPUSD"
        finally:
            wal_module._WAL_FILE = original

    def test_wal_clear_removes_pair(self, tmp_path):
        """ibkr_wal_clear supprime les entrées d'une paire, conserve les autres."""
        import ibkr_wal as wal_module
        from ibkr_wal import ibkr_wal_clear

        wal_path = tmp_path / "ibkr_wal.jsonl"
        self._write_wal(str(wal_path), [
            {"op": "FX_BUY_CONFIRMED", "pair": "EURUSD", "ts": 1.0},
            {"op": "FX_BUY_CONFIRMED", "pair": "GBPUSD", "ts": 2.0},
        ])
        original = wal_module._WAL_FILE
        wal_module._WAL_FILE = wal_path
        try:
            ibkr_wal_clear("EURUSD")
            lines = [
                json.loads(ln)
                for ln in wal_path.read_text(encoding="utf-8").splitlines()
                if ln.strip()
            ]
            assert all(e["pair"] != "EURUSD" for e in lines)
            assert any(e["pair"] == "GBPUSD" for e in lines)
        finally:
            wal_module._WAL_FILE = original


# ─── IBKR State Manager ───────────────────────────────────────────────────────

_SECRET = "TEST_IBKR_SECRET_32BYTES_PADDING!"


class TestIBKRStateManager:
    """Tests unitaires ibkr_state_manager — intégrité HMAC + déduplication."""

    def _make_state_file(self, tmp_path, state: dict, secret: str = _SECRET):
        """Crée un fichier d'état signé HMAC-SHA256."""
        from ibkr_state_manager import save_ibkr_state
        save_ibkr_state(
            state,
            states_dir=str(tmp_path),
            state_file="ibkr_state.json",
            ibkr_secret=secret,
            force=True,
        )
        return tmp_path / "ibkr_state.json"

    # P0-1 regression ──────────────────────────────────────────────────────────

    def test_unsigned_json_raises_state_error(self, tmp_path):
        """P0-1 : JSON sans header JSON_V1 → IBKRStateError (plus de fail-open)."""
        from ibkr_state_manager import load_ibkr_state, IBKRStateError

        state_file = tmp_path / "ibkr_state.json"
        state_file.write_text(
            json.dumps({"pair": "EURUSD", "last_order_side": "BUY"}),
            encoding="utf-8",
        )
        with pytest.raises(IBKRStateError, match="sans signature HMAC"):
            load_ibkr_state(
                states_dir=str(tmp_path),
                state_file="ibkr_state.json",
                ibkr_secret=_SECRET,
            )

    def test_empty_file_raises_state_error(self, tmp_path):
        """Fichier vide → IBKRStateError."""
        from ibkr_state_manager import load_ibkr_state, IBKRStateError

        (tmp_path / "ibkr_state.json").write_bytes(b"")
        with pytest.raises(IBKRStateError):
            load_ibkr_state(
                states_dir=str(tmp_path),
                state_file="ibkr_state.json",
                ibkr_secret=_SECRET,
            )

    # P0-2 regression ──────────────────────────────────────────────────────────

    def test_save_dedup_does_not_skip_modified_state(self, tmp_path):
        """P0-2 : deduplication par comparaison octets — un état modifié est ré-écrit."""
        from ibkr_state_manager import save_ibkr_state, load_ibkr_state

        state_v1 = {"pair": "EURUSD", "qty": 20000}
        state_v2 = {"pair": "EURUSD", "qty": 10000}  # valeur modifiée

        save_ibkr_state(
            state_v1,
            states_dir=str(tmp_path),
            state_file="ibkr_state.json",
            ibkr_secret=_SECRET,
            force=True,
        )
        import time
        time.sleep(0.01)
        save_ibkr_state(
            state_v2,
            states_dir=str(tmp_path),
            state_file="ibkr_state.json",
            ibkr_secret=_SECRET,
            force=True,
        )
        loaded = load_ibkr_state(
            states_dir=str(tmp_path),
            state_file="ibkr_state.json",
            ibkr_secret=_SECRET,
        )
        assert loaded["pair"] == "EURUSD"
        assert loaded["qty"] == 10000  # pas le v1

    def test_save_dedup_skips_unchanged_state(self, tmp_path):
        """P0-2 : état identique → dedup, le fichier n'est pas ré-écrit inutilement."""
        from ibkr_state_manager import save_ibkr_state

        state = {"pair": "EURUSD", "qty": 20000}
        save_ibkr_state(
            state,
            states_dir=str(tmp_path),
            state_file="ibkr_state.json",
            ibkr_secret=_SECRET,
            force=True,
        )
        path = tmp_path / "ibkr_state.json"
        mtime_before = path.stat().st_mtime

        import time
        time.sleep(0.05)

        save_ibkr_state(
            state,
            states_dir=str(tmp_path),
            state_file="ibkr_state.json",
            ibkr_secret=_SECRET,
            force=True,
        )
        mtime_after = path.stat().st_mtime
        # L'état étant identique, le fichier ne doit pas être retouché
        assert mtime_after == mtime_before

    # Validité HMAC ─────────────────────────────────────────────────────────────

    def test_valid_state_roundtrip(self, tmp_path):
        """État signé → chargement correct."""
        from ibkr_state_manager import load_ibkr_state

        original = {"pair": "GBPUSD", "last_order_side": "SHORT", "qty": 15000}
        self._make_state_file(tmp_path, original)
        loaded = load_ibkr_state(
            states_dir=str(tmp_path),
            state_file="ibkr_state.json",
            ibkr_secret=_SECRET,
        )
        assert loaded["pair"] == "GBPUSD"
        assert loaded["last_order_side"] == "SHORT"

    def test_tampered_state_raises_state_error(self, tmp_path):
        """Fichier HMAC modifié après signature → IBKRStateError."""
        from ibkr_state_manager import load_ibkr_state, IBKRStateError, _JSON_HEADER

        path = self._make_state_file(tmp_path, {"balance": 9999.0})
        raw = path.read_bytes()
        # Corrompre un octet dans le payload (après header + 32 octets HMAC)
        offset = len(_JSON_HEADER) + 32 + 5
        tampered = bytearray(raw)
        tampered[offset] ^= 0xFF
        path.write_bytes(bytes(tampered))

        with pytest.raises(IBKRStateError, match="HMAC invalide"):
            load_ibkr_state(
                states_dir=str(tmp_path),
                state_file="ibkr_state.json",
                ibkr_secret=_SECRET,
            )

    def test_missing_file_returns_empty_dict(self, tmp_path):
        """Fichier absent → retourne {} sans erreur."""
        from ibkr_state_manager import load_ibkr_state

        result = load_ibkr_state(
            states_dir=str(tmp_path),
            state_file="ibkr_state.json",
            ibkr_secret=_SECRET,
        )
        assert result == {}
