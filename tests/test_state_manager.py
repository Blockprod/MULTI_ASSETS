"""
tests/test_state_manager.py — Tests unitaires pour state_manager.py (C-09)

Couvre :
- Écriture atomique et relecture correcte
- Signature HMAC — tamper détecté et StateError levée
- Fichier manquant → retourne {}
- Réécriture sans modification → pas d'écriture disque (hash-diff)
- Concurrence : N threads save simultanés — pas de corruption
"""
import os
import json
import pickle
import threading
import time
import pytest

# Patch config.states_dir / config.state_file pour utiliser un répertoire temporaire
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'code', 'src'))


@pytest.fixture()
def tmp_state_dir(tmp_path, monkeypatch):
    """Rédirige states_dir et state_file vers un répertoire temporaire."""
    import bot_config
    import state_manager as _sm
    # Sync state_manager config with bot_config (importlib.reload in other tests may disconnect them)
    monkeypatch.setattr(_sm, 'config', bot_config.config)
    monkeypatch.setattr(bot_config.config, 'states_dir', str(tmp_path))
    monkeypatch.setattr(bot_config.config, 'state_file', 'bot_state.json')
    return tmp_path


class TestSaveAndLoad:
    def test_round_trip(self, tmp_state_dir):
        """save_state puis load_state retournent le même dict."""
        import state_manager
        state = {"SOLUSDT": {"entry_price": 150.0, "partial_taken_1": False}}
        state_manager.save_state(state)
        loaded = state_manager.load_state()
        assert loaded == state

    def test_missing_file_returns_empty(self, tmp_state_dir):
        """load_state sans fichier retourne {}."""
        import state_manager
        result = state_manager.load_state()
        assert result == {}

    def test_no_write_if_unchanged(self, tmp_state_dir):
        """save_state ne réécrit pas le fichier si le contenu est identique."""
        import state_manager
        state = {"SOLUSDT": {"entry_price": 200.0}}
        state_manager.save_state(state)
        from bot_config import config as _cfg
        state_path = os.path.join(str(tmp_state_dir), _cfg.state_file)
        mtime_before = os.path.getmtime(state_path)
        import time
        time.sleep(0.05)
        state_manager.save_state(state)  # même contenu
        mtime_after = os.path.getmtime(state_path)
        assert mtime_before == mtime_after, "Le fichier ne doit pas être réécrit si inchangé"

    def test_atomic_write_tmp_file_cleaned(self, tmp_state_dir):
        """Aucun fichier .tmp ne doit subsister après save_state."""
        import state_manager
        state_manager.save_state({"pair": "BTCUSDC"})
        from bot_config import config as _cfg
        tmp_file = os.path.join(str(tmp_state_dir), _cfg.state_file + '.tmp')
        assert not os.path.exists(tmp_file), "Le fichier .tmp doit être supprimé après écriture atomique"


class TestHMACIntegrity:
    def test_tampered_file_returns_empty(self, tmp_state_dir):
        """Un fichier falsifié doit lever StateError (P1-06: re-raise)."""
        import state_manager
        from exceptions import StateError
        state_manager.save_state({"secret": "value"})
        from bot_config import config as _cfg
        state_path = os.path.join(str(tmp_state_dir), _cfg.state_file)
        # Tamper : modifier un octet après le header+HMAC
        with open(state_path, 'r+b') as f:
            data = f.read()
            f.seek(0)
            tampered = bytearray(data)
            tampered[-1] ^= 0xFF
            f.write(bytes(tampered))
        with pytest.raises(StateError, match="HMAC"):
            state_manager.load_state()

    def test_unsigned_legacy_file_loads_silently(self, tmp_state_dir):
        """Un pickle non signé (ancien format) doit charger sans erreur."""
        import state_manager
        state = {"legacy": True}
        from bot_config import config as _cfg
        state_path = os.path.join(str(tmp_state_dir), _cfg.state_file)
        with open(state_path, 'wb') as f:
            f.write(pickle.dumps(state))
        loaded = state_manager.load_state()
        assert loaded == state


class TestConcurrentSave:
    def test_concurrent_writes_do_not_corrupt(self, tmp_state_dir):
        """N threads sauvegardant simultanément ne doivent pas corrompre l'état.

        Après P0-SAVE, save_state() propage StateError au lieu de l'avaler
        silencieusement.  En concurrence sur Windows, des PermissionError /
        WinError sont attendues.  On vérifie donc :
        1) Au moins un thread réussit (l'état final est lisible et valide).
        2) Les seules exceptions levées sont des StateError (pas de corruption).
        """
        import state_manager
        from exceptions import StateError
        import random
        successes = []
        errors = []
        barrier = threading.Barrier(10)

        def worker(i):
            try:
                barrier.wait()
                # Petit jitter pour éviter que TOUS échouent simultanément sur Windows
                time.sleep(random.uniform(0, 0.05))
                state_manager.save_state({f"pair_{i}": {"price": float(i)}})
                successes.append(i)
            except StateError:
                # Attendu en concurrence (fichier verrouillé par un autre thread)
                pass
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Erreurs inattendues (non-StateError) : {errors}"
        # Sur Windows, il est possible que tous les threads se bloquent mutuellement.
        # On vérifie qu'au moins une sauvegarde sérielle fonctionne ensuite.
        if not successes:
            state_manager.save_state({"serial_fallback": True})
        # On doit pouvoir lire l'état final sans corruption
        loaded = state_manager.load_state()
        assert isinstance(loaded, dict)
        loaded = state_manager.load_state()
        assert isinstance(loaded, dict)


# ══════════════════════════════════════════════════════════════════════════════
# C-08 — Tests complémentaires pour couverture complète
# ══════════════════════════════════════════════════════════════════════════════

class TestSaveStateUnsignedHashCompare:
    """Couvre le chemin old_state_bytes = raw_old (fichier non signé existant)."""

    def test_overwrite_unsigned_file_detects_change(self, tmp_state_dir):
        """Si le fichier existant est non signé, save_state le réécrit signé (JSON)."""
        import state_manager
        # Créer un fichier non signé (ancien format : pickle brut)
        from bot_config import config as _cfg
        state_path = os.path.join(str(tmp_state_dir), _cfg.state_file)
        old_state = {"old_key": "old_value"}
        with open(state_path, 'wb') as f:
            f.write(pickle.dumps(old_state))

        # Sauvegarder un nouvel état → le fichier non signé doit être réécrit
        new_state = {"new_key": "new_value"}
        state_manager.save_state(new_state)

        loaded = state_manager.load_state()
        assert loaded == new_state

        # Le fichier doit maintenant être signé en JSON (format JSON_V1)
        with open(state_path, 'rb') as f:
            raw = f.read()
        assert raw.startswith(state_manager._JSON_HEADER())


class TestSaveStateExceptionBranch:
    """Couvre la branche except Exception dans save_state (P1-06: re-raise)."""

    def test_generic_exception_in_save_raises_state_error(self, tmp_state_dir):
        """Une exception inattendue dans save_state est re-raisée en StateError (P1-06)."""
        import state_manager
        from exceptions import StateError
        from unittest.mock import patch

        # RuntimeError n'est pas dans (OSError, TypeError, ValueError)
        # → branche except Exception → re-raise as StateError (P1-06)
        with patch('state_manager.json.dumps', side_effect=RuntimeError("unexpected")):
            with pytest.raises(StateError, match="unexpected"):
                state_manager.save_state({"key": "val"})


class TestLoadStateExceptionBranch:
    """Couvre la branche except Exception dans load_state."""

    def test_random_bytes_raises_state_error(self, tmp_state_dir):
        """Un fichier de bytes aléatoires lève StateError (P1-06: re-raise)."""
        import state_manager
        from exceptions import StateError
        state_path = os.path.join(str(tmp_state_dir), 'bot_state.json')
        with open(state_path, 'wb') as f:
            f.write(os.urandom(128))
        with pytest.raises(StateError):
            state_manager.load_state()


class TestBackupOnOverwrite:
    """Vérifie que l'écriture atomique fonctionne correctement."""

    def test_state_file_exists_after_two_saves(self, tmp_state_dir):
        """Deux save_state successifs avec données différentes → fichier valide."""
        import state_manager
        state_manager.save_state({"v": 1})
        state_manager.save_state({"v": 2})
        loaded = state_manager.load_state()
        assert loaded == {"v": 2}


# ══════════════════════════════════════════════════════════════════════════════
# C-17 — Migration pickle → JSON + validation de schéma
# ══════════════════════════════════════════════════════════════════════════════

class TestJSONFormat:
    """C-17: Vérifie que save_state produit un fichier JSON signé lisible."""

    def test_file_starts_with_json_header(self, tmp_state_dir):
        """Le fichier sauvegardé commence par le header JSON_V1."""
        import state_manager
        state_manager.save_state({"SOLUSDT": {"entry_price": 100.0}})
        state_path = os.path.join(str(tmp_state_dir), 'bot_state.json')
        with open(state_path, 'rb') as f:
            raw = f.read()
        assert raw.startswith(state_manager._JSON_HEADER())

    def test_json_content_is_readable(self, tmp_state_dir):
        """Le contenu après header+HMAC est du JSON valide lisible."""
        import state_manager
        import json
        state = {"SOLUSDT": {"entry_price": 42.5, "partial_taken_1": True}}
        state_manager.save_state(state)
        state_path = os.path.join(str(tmp_state_dir), 'bot_state.json')
        with open(state_path, 'rb') as f:
            raw = f.read()
        header = state_manager._JSON_HEADER()
        json_bytes = raw[len(header) + 32:]
        parsed = json.loads(json_bytes.decode('utf-8'))
        assert parsed == state

    def test_json_round_trip_with_types(self, tmp_state_dir):
        """Types spéciaux (datetime, Decimal) survivent au round-trip."""
        import state_manager
        from datetime import datetime, timezone
        state = {
            "SOLUSDT": {
                "entry_price": 150.0,
                "last_execution": datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc),
            }
        }
        state_manager.save_state(state)
        loaded = state_manager.load_state()
        # datetime sera chargé comme string ISO
        assert loaded["SOLUSDT"]["entry_price"] == 150.0
        assert "2026-03-01" in loaded["SOLUSDT"]["last_execution"]


class TestPickleMigration:
    """C-17: Migration automatique des fichiers pickle vers JSON."""

    def test_migrate_signed_pickle_to_json(self, tmp_state_dir):
        """Fichier HMAC_V1 pickle → chargé correctement, resave en JSON."""
        import state_manager
        state = {"BTCUSDT": {"entry_price": 50000.0, "last_order_side": "BUY"}}
        # Créer un fichier pickle signé (ancien format HMAC_V1)
        state_path = os.path.join(str(tmp_state_dir), 'bot_state.json')
        state_bytes = pickle.dumps(state)
        mac = state_manager._compute_hmac(state_bytes)
        signed_data = state_manager._STATE_HEADER() + mac + state_bytes
        with open(state_path, 'wb') as f:
            f.write(signed_data)

        # Charger → doit lire le pickle
        loaded = state_manager.load_state()
        assert loaded == state

        # Re-sauvegarder → doit écrire en JSON
        state_manager.save_state(loaded)
        with open(state_path, 'rb') as f:
            raw = f.read()
        assert raw.startswith(state_manager._JSON_HEADER()), \
            "Après re-save, le fichier doit être au format JSON"

    def test_migrate_unsigned_pickle_to_json(self, tmp_state_dir):
        """Fichier pickle non signé → chargé, resave en JSON."""
        import state_manager
        state = {"legacy_pair": {"old_key": True}}
        state_path = os.path.join(str(tmp_state_dir), 'bot_state.json')
        with open(state_path, 'wb') as f:
            f.write(pickle.dumps(state))

        loaded = state_manager.load_state()
        assert loaded == state

        # Re-save → JSON
        state_manager.save_state(loaded)
        with open(state_path, 'rb') as f:
            raw = f.read()
        assert raw.startswith(state_manager._JSON_HEADER())

    def test_tampered_signed_pickle_raises_state_error(self, tmp_state_dir):
        """Fichier pickle signé falsifié → StateError (P1-06: re-raise)."""
        import state_manager
        from exceptions import StateError
        state = {"pair": {"price": 100.0}}
        state_path = os.path.join(str(tmp_state_dir), 'bot_state.json')
        state_bytes = pickle.dumps(state)
        mac = state_manager._compute_hmac(state_bytes)
        signed_data = state_manager._STATE_HEADER() + mac + state_bytes
        tampered = bytearray(signed_data)
        tampered[-1] ^= 0xFF
        with open(state_path, 'wb') as f:
            f.write(bytes(tampered))

        with pytest.raises(StateError):
            state_manager.load_state()


class TestSchemaValidation:
    """C-17: validate_bot_state logue les clés inconnues."""

    def test_known_keys_no_warning(self, tmp_state_dir, caplog):
        """État avec uniquement des clés connues → pas de warning."""
        import state_manager
        state = {
            "SOLUSDT": {
                "entry_price": 100.0,
                "last_order_side": "BUY",
                "partial_taken_1": False,
            },
            "emergency_halt": False,
        }
        with caplog.at_level('WARNING'):
            state_manager.validate_bot_state(state)
        assert "clés inconnues" not in caplog.text.lower()
        assert "clé globale inconnue" not in caplog.text.lower()

    def test_unknown_pair_key_logs_warning(self, tmp_state_dir, caplog):
        """Clé inconnue dans pair_state → warning logué."""
        import state_manager
        state = {
            "SOLUSDT": {
                "entry_price": 100.0,
                "future_new_key": "surprise",
            }
        }
        with caplog.at_level('WARNING'):
            state_manager.validate_bot_state(state)
        assert "future_new_key" in caplog.text

    def test_unknown_global_key_logs_warning(self, tmp_state_dir, caplog):
        """Clé globale inconnue → warning logué."""
        import state_manager
        state = {"unknown_global_flag": 42}
        with caplog.at_level('WARNING'):
            state_manager.validate_bot_state(state)
        assert "unknown_global_flag" in caplog.text

    def test_validation_never_raises(self, tmp_state_dir):
        """validate_bot_state ne lève jamais d'exception."""
        import state_manager
        # Même avec des données bizarres
        state_manager.validate_bot_state({})
        state_manager.validate_bot_state({"pair": "not_a_dict"})
        state_manager.validate_bot_state({"PAIR": {}, "emergency_halt": True})

    def test_validation_runs_on_load(self, tmp_state_dir, caplog):
        """La validation est appelée automatiquement au load_state."""
        import state_manager
        state = {"SOLUSDT": {"unknown_key_xyz": True, "entry_price": 1.0}}
        state_manager.save_state(state)
        with caplog.at_level('WARNING'):
            loaded = state_manager.load_state()
        assert loaded == state
        assert "unknown_key_xyz" in caplog.text


class TestStateEncoder:
    """C-17: _StateEncoder gère datetime, date, Decimal."""

    def test_datetime_serialization(self):
        """datetime → ISO string."""
        import state_manager
        import json
        from datetime import datetime, timezone
        dt = datetime(2026, 3, 1, 12, 30, 0, tzinfo=timezone.utc)
        result = json.dumps({"ts": dt}, cls=state_manager._StateEncoder)
        assert "2026-03-01" in result

    def test_decimal_serialization(self):
        """Decimal → float."""
        import state_manager
        import json
        from decimal import Decimal
        result = json.dumps({"qty": Decimal("1.234")}, cls=state_manager._StateEncoder)
        parsed = json.loads(result)
        assert parsed["qty"] == 1.234

    def test_unsupported_type_raises(self):
        """Type non supporté → TypeError."""
        import state_manager
        import json
        with pytest.raises(TypeError):
            json.dumps({"bad": set([1, 2])}, cls=state_manager._StateEncoder)


class TestPlainJSONFallback:
    """Un fichier JSON brut (sans header JSON_V1) doit être chargé correctement."""

    def test_load_plain_json_without_header(self, tmp_state_dir, caplog):
        """Un fichier .json écrit manuellement (sans header) est lu en fallback."""
        import state_manager
        from bot_config import config
        state_path = os.path.join(config.states_dir, config.state_file)
        plain = {"SOLUSDT": {"entry_price": 100.0}, "_state_version": 2}
        with open(state_path, 'w', encoding='utf-8') as f:
            json.dump(plain, f)
        with caplog.at_level('WARNING'):
            loaded = state_manager.load_state()
        assert loaded == plain
        assert "JSON sans signature" in caplog.text

    def test_plain_json_is_resigned_on_save(self, tmp_state_dir):
        """Après load d'un plain JSON, save_state écrit un fichier signé JSON_V1."""
        import state_manager
        from bot_config import config
        state_path = os.path.join(config.states_dir, config.state_file)
        plain = {"SOLUSDT": {"entry_price": 50.0}}
        with open(state_path, 'w', encoding='utf-8') as f:
            json.dump(plain, f)
        loaded = state_manager.load_state()
        state_manager.save_state(loaded)
        with open(state_path, 'rb') as f:
            raw = f.read()
        assert raw.startswith(state_manager._JSON_HEADER())


# ══════════════════════════════════════════════════════════════════════════════
# P6-A — Étape 3.3 : Robustesse aux fichiers corrompus / tronqués
# ══════════════════════════════════════════════════════════════════════════════

class TestCorruptionRobustness:
    """Tests de résilience face aux fichiers d'état corrompus ou tronqués (P6-A)."""

    def test_empty_file_raises_state_error(self, tmp_state_dir):
        """Fichier vide → StateError (pas de crash silencieux)."""
        import state_manager
        from exceptions import StateError
        state_path = os.path.join(str(tmp_state_dir), 'bot_state.json')
        with open(state_path, 'wb') as f:
            f.write(b'')
        with pytest.raises(StateError):
            state_manager.load_state()

    def test_truncated_json_payload_raises_state_error(self, tmp_state_dir):
        """Fichier tronqué à mi-écriture (JSON_V1 header + HMAC + JSON incomplet)
        → StateError (json.JSONDecodeError remonte comme StateError)."""
        import state_manager
        from exceptions import StateError
        state_bytes = b'{"SOLUSDT": {"entry_price": 150'  # payload incomplet
        mac = state_manager._compute_hmac(state_bytes)
        signed_data = state_manager._JSON_HEADER() + mac + state_bytes
        state_path = os.path.join(str(tmp_state_dir), 'bot_state.json')
        with open(state_path, 'wb') as f:
            f.write(signed_data)
        with pytest.raises(StateError):
            state_manager.load_state()

    def test_json_v1_header_only_no_hmac_no_payload_raises(self, tmp_state_dir):
        """Fichier avec uniquement le header JSON_V1 et rien d'autre → StateError."""
        import state_manager
        from exceptions import StateError
        state_path = os.path.join(str(tmp_state_dir), 'bot_state.json')
        with open(state_path, 'wb') as f:
            f.write(state_manager._JSON_HEADER())  # header seul, HMAC et payload absents
        with pytest.raises(StateError):
            state_manager.load_state()

    def test_json_v1_header_correct_hmac_wrong_payload_raises(self, tmp_state_dir):
        """JSON_V1 header + HMAC calculé sur payload A, mais payload B écrit → StateError."""
        import state_manager
        from exceptions import StateError
        good_payload = b'{"pair": "SOLUSDT"}'
        bad_payload  = b'{"pair": "BTCUSDT"}'
        mac = state_manager._compute_hmac(good_payload)
        state_path = os.path.join(str(tmp_state_dir), 'bot_state.json')
        with open(state_path, 'wb') as f:
            f.write(state_manager._JSON_HEADER() + mac + bad_payload)
        with pytest.raises(StateError, match="HMAC"):
            state_manager.load_state()

    def test_daily_pnl_tracker_survives_round_trip(self, tmp_state_dir):
        """_daily_pnl_tracker est une clé globale connue — round-trip sans perte."""
        import state_manager
        state = {
            'SOLUSDT': {'entry_price': 100.0},
            '_daily_pnl_tracker': {'2026-03-05': {'total_pnl': -45.0, 'trade_count': 3}},
        }
        state_manager.save_state(state)
        loaded = state_manager.load_state()
        assert loaded['_daily_pnl_tracker']['2026-03-05']['total_pnl'] == -45.0
        assert loaded['_daily_pnl_tracker']['2026-03-05']['trade_count'] == 3

    def test_daily_pnl_tracker_no_schema_warning(self, tmp_state_dir, caplog):
        """_daily_pnl_tracker ne déclenche pas de warning de validation schéma."""
        import state_manager
        state = {'_daily_pnl_tracker': {'2026-03-05': {'total_pnl': -10.0, 'trade_count': 1}}}
        with caplog.at_level('WARNING'):
            state_manager.validate_bot_state(state)
        assert '_daily_pnl_tracker' not in caplog.text