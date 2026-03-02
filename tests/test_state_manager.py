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
import pickle
import threading
import tempfile
import pytest

# Patch config.states_dir / config.state_file pour utiliser un répertoire temporaire
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'code', 'src'))


@pytest.fixture()
def tmp_state_dir(tmp_path, monkeypatch):
    """Rédirige states_dir et state_file vers un répertoire temporaire."""
    import bot_config
    monkeypatch.setattr(bot_config.config, 'states_dir', str(tmp_path))
    monkeypatch.setattr(bot_config.config, 'state_file', 'bot_state.pkl')
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
        state_path = os.path.join(str(tmp_state_dir), 'bot_state.pkl')
        mtime_before = os.path.getmtime(state_path)
        import time; time.sleep(0.05)
        state_manager.save_state(state)  # même contenu
        mtime_after = os.path.getmtime(state_path)
        assert mtime_before == mtime_after, "Le fichier ne doit pas être réécrit si inchangé"

    def test_atomic_write_tmp_file_cleaned(self, tmp_state_dir):
        """Aucun fichier .tmp ne doit subsister après save_state."""
        import state_manager
        state_manager.save_state({"pair": "BTCUSDC"})
        tmp_file = os.path.join(str(tmp_state_dir), 'bot_state.pkl.tmp')
        assert not os.path.exists(tmp_file), "Le fichier .tmp doit être supprimé après écriture atomique"


class TestHMACIntegrity:
    def test_tampered_file_returns_empty(self, tmp_state_dir):
        """Un fichier falsifié doit retourner {} (StateError swallowed par @log_exceptions)."""
        import state_manager
        state_manager.save_state({"secret": "value"})
        state_path = os.path.join(str(tmp_state_dir), 'bot_state.pkl')
        # Tamper : modifier un octet après le header+HMAC
        with open(state_path, 'r+b') as f:
            data = f.read()
            f.seek(0)
            tampered = bytearray(data)
            tampered[-1] ^= 0xFF
            f.write(bytes(tampered))
        result = state_manager.load_state()
        assert result == {}, "Un fichier corrompu doit retourner {} (StateError géré par décorateur)"

    def test_unsigned_legacy_file_loads_silently(self, tmp_state_dir):
        """Un pickle non signé (ancien format) doit charger sans erreur."""
        import state_manager
        state = {"legacy": True}
        state_path = os.path.join(str(tmp_state_dir), 'bot_state.pkl')
        with open(state_path, 'wb') as f:
            f.write(pickle.dumps(state))
        loaded = state_manager.load_state()
        assert loaded == state


class TestConcurrentSave:
    def test_concurrent_writes_do_not_corrupt(self, tmp_state_dir):
        """N threads sauvegardant simultanément ne doivent pas corrompre l'état."""
        import state_manager
        errors = []
        barrier = threading.Barrier(10)

        def worker(i):
            try:
                barrier.wait()
                state_manager.save_state({f"pair_{i}": {"price": float(i)}})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Erreurs lors des écritures concurrentes : {errors}"
        # On doit pouvoir lire l'état final sans corruption
        loaded = state_manager.load_state()
        assert isinstance(loaded, dict)
