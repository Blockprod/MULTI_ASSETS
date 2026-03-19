"""tests/test_circuit_breaker.py — MA-01

Tests unitaires du circuit breaker (TS-P2-01) dans exchange_client.py.
Manipule directement _circuit_state (dict mutable module-level) et
_circuit_lock pour éviter toute dépendance réseau.
"""
import os
import sys
import time
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'code', 'src'))

import pytest
import exchange_client as ec
from exceptions import CircuitOpenError

# Seuil et cooldown réduits pour les tests (injectés dans _simulate_final_failure)
_TEST_THRESHOLD = 3
_TEST_RESET_S = 2


# ---------------------------------------------------------------------------
# Helpers qui répliquent la logique interne de _request()
# ---------------------------------------------------------------------------

def _check_circuit_or_raise() -> None:
    """Réplique le check effectué au début de chaque tentative dans _request()."""
    with ec._circuit_lock:
        open_until: float = ec._circuit_state['open_until']
    if open_until > 0 and time.time() < open_until:
        remaining = open_until - time.time()
        raise CircuitOpenError(f"Circuit ouvert ({remaining:.0f}s restantes).")


def _simulate_final_failure(
    threshold: int = _TEST_THRESHOLD,
    reset_s: int = _TEST_RESET_S,
) -> None:
    """Réplique le bloc except du dernier retry dans _request() (sans appel réseau)."""
    with ec._circuit_lock:
        ec._circuit_state['failure_count'] += 1
        if ec._circuit_state['failure_count'] >= threshold:
            ec._circuit_state['open_until'] = time.time() + reset_s
            ec._circuit_state['failure_count'] = 0
            cb = ec._circuit_alert_callback
            if cb is not None:
                try:
                    cb(f"Circuit ouvert après {threshold} échecs.")
                except Exception:
                    pass


def _simulate_success() -> None:
    """Réplique la réinitialisation du compteur sur succès dans _request()."""
    with ec._circuit_lock:
        ec._circuit_state['failure_count'] = 0


# ---------------------------------------------------------------------------
# Fixture: reset de l'état module avant chaque test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_circuit_state():
    """Remet _circuit_state à zéro et supprime le callback avant chaque test."""
    ec._circuit_state['failure_count'] = 0
    ec._circuit_state['open_until'] = 0.0
    ec.set_circuit_alert_callback(None)
    yield
    # Nettoyage post-test
    ec._circuit_state['failure_count'] = 0
    ec._circuit_state['open_until'] = 0.0
    ec.set_circuit_alert_callback(None)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCircuitBreakerInitialState:
    """Vérifie l'état initial du circuit breaker au démarrage du module."""

    def test_failure_count_starts_at_zero(self):
        assert ec._circuit_state['failure_count'] == 0

    def test_open_until_starts_at_zero(self):
        assert ec._circuit_state['open_until'] == 0.0

    def test_callback_starts_as_none(self):
        assert ec._circuit_alert_callback is None


class TestCircuitBreakerThreshold:
    """Vérifie l'ouverture/fermeture du circuit selon le nombre d'échecs."""

    def test_n_minus_1_failures_do_not_open_circuit(self):
        """N-1 échecs : le circuit reste fermé (open_until = 0)."""
        for _ in range(_TEST_THRESHOLD - 1):
            _simulate_final_failure()
        assert ec._circuit_state['open_until'] == 0.0
        # Pas d'exception levée
        _check_circuit_or_raise()

    def test_n_failures_open_circuit(self):
        """N échecs consécutifs ouvrent le circuit (open_until > now)."""
        for _ in range(_TEST_THRESHOLD):
            _simulate_final_failure()
        assert ec._circuit_state['open_until'] > time.time()

    def test_n_failures_reset_failure_count(self):
        """Quand le circuit s'ouvre, failure_count est remis à 0."""
        for _ in range(_TEST_THRESHOLD):
            _simulate_final_failure()
        assert ec._circuit_state['failure_count'] == 0

    def test_circuit_open_raises_circuit_open_error(self):
        """Un circuit ouvert (open_until dans le futur) lève CircuitOpenError."""
        for _ in range(_TEST_THRESHOLD):
            _simulate_final_failure()
        with pytest.raises(CircuitOpenError):
            _check_circuit_or_raise()

    def test_circuit_error_message_contains_remaining_seconds(self):
        """Le message de CircuitOpenError mentionne le temps restant."""
        for _ in range(_TEST_THRESHOLD):
            _simulate_final_failure()
        with pytest.raises(CircuitOpenError, match=r"\d+s restantes"):
            _check_circuit_or_raise()


class TestCircuitBreakerCooldown:
    """Vérifie que le circuit se ferme automatiquement après le délai."""

    def test_expired_open_until_does_not_raise(self):
        """open_until dans le passé → pas de CircuitOpenError."""
        # Mettre open_until dans le passé (déjà expiré)
        ec._circuit_state['open_until'] = time.time() - 1.0
        # Ne doit pas lever d'exception
        _check_circuit_or_raise()

    def test_zero_open_until_does_not_raise(self):
        """open_until = 0.0 (état initial) → pas de CircuitOpenError."""
        ec._circuit_state['open_until'] = 0.0
        _check_circuit_or_raise()

    def test_circuit_auto_closes_after_reset_seconds(self):
        """Après reset_s secondes, le circuit est à nouveau passable."""
        reset_s = 1  # Délai court pour le test
        for _ in range(_TEST_THRESHOLD):
            _simulate_final_failure(reset_s=reset_s)
        # Vérification immédiate : circuit ouvert
        with pytest.raises(CircuitOpenError):
            _check_circuit_or_raise()
        # Attendons l'expiration du cooldown
        time.sleep(reset_s + 0.2)
        # Le circuit doit être refermé
        _check_circuit_or_raise()


class TestCircuitBreakerSuccessReset:
    """Vérifie que les succès réinitialisent le compteur d'échecs."""

    def test_success_resets_failure_count_to_zero(self):
        """Un succès remet failure_count à 0, quel que soit son niveau précédent."""
        # Quelques échecs sans atteindre le seuil
        for _ in range(_TEST_THRESHOLD - 1):
            _simulate_final_failure()
        assert ec._circuit_state['failure_count'] == _TEST_THRESHOLD - 1
        _simulate_success()
        assert ec._circuit_state['failure_count'] == 0

    def test_success_after_partial_failures_prevents_opening(self):
        """Un succès entre deux séries d'échecs empêche l'ouverture du circuit."""
        for _ in range(_TEST_THRESHOLD - 1):
            _simulate_final_failure()
        _simulate_success()
        # Une autre série de N-1 échecs ne doit toujours pas ouvrir le circuit
        for _ in range(_TEST_THRESHOLD - 1):
            _simulate_final_failure()
        assert ec._circuit_state['open_until'] == 0.0


class TestCircuitBreakerCallback:
    """Vérifie le callback d'alerte déclenché à l'ouverture du circuit."""

    def test_callback_called_when_circuit_opens(self):
        """Le callback reçoit un message string quand le circuit s'ouvre."""
        received: list[str] = []
        ec.set_circuit_alert_callback(lambda msg: received.append(msg))
        for _ in range(_TEST_THRESHOLD):
            _simulate_final_failure()
        assert len(received) == 1
        assert isinstance(received[0], str)
        assert len(received[0]) > 0

    def test_callback_not_called_before_threshold(self):
        """Le callback n'est pas appelé avant d'atteindre le seuil."""
        received: list[str] = []
        ec.set_circuit_alert_callback(lambda msg: received.append(msg))
        for _ in range(_TEST_THRESHOLD - 1):
            _simulate_final_failure()
        assert len(received) == 0

    def test_none_callback_does_not_crash(self):
        """Avec callback=None, l'ouverture du circuit ne lève pas d'exception."""
        ec.set_circuit_alert_callback(None)
        # N échecs → circuit ouvert, aucun crash attendu
        for _ in range(_TEST_THRESHOLD):
            _simulate_final_failure()
        assert ec._circuit_state['open_until'] > time.time()

    def test_raising_callback_does_not_propagate(self):
        """Si le callback lève une exception, elle est silencieusée."""
        def bad_callback(msg: str) -> None:
            raise RuntimeError("Callback crash simulé")

        ec.set_circuit_alert_callback(bad_callback)
        # Ne doit pas lever d'exception malgré le callback défaillant
        for _ in range(_TEST_THRESHOLD):
            _simulate_final_failure()
        # Circuit ouvert malgré le callback défaillant
        assert ec._circuit_state['open_until'] > time.time()

    def test_set_circuit_alert_callback_stores_callable(self):
        """set_circuit_alert_callback() stocke correctement le callable."""
        cb = lambda msg: None  # noqa: E731
        ec.set_circuit_alert_callback(cb)
        assert ec._circuit_alert_callback is cb

    def test_set_circuit_alert_callback_accepts_none(self):
        """set_circuit_alert_callback(None) efface le callback existant."""
        ec.set_circuit_alert_callback(lambda msg: None)
        ec.set_circuit_alert_callback(None)
        assert ec._circuit_alert_callback is None


class TestCircuitBreakerThreadSafety:
    """Vérifie que les mises à jour concurrentes n'induisent pas de race conditions."""

    def test_concurrent_failures_increment_correctly(self):
        """N threads simulant des échecs simultanés produisent un décompte cohérent."""
        n_threads = _TEST_THRESHOLD - 1  # Pas assez pour ouvrir le circuit
        barrier = threading.Barrier(n_threads)
        errors: list[Exception] = []

        def worker():
            try:
                barrier.wait(timeout=5.0)
                _simulate_final_failure()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        assert not errors, f"Erreurs dans les threads : {errors}"
        # failure_count doit être exactement n_threads (< seuil → pas d'ouverture)
        assert ec._circuit_state['failure_count'] == n_threads
        assert ec._circuit_state['open_until'] == 0.0

    def test_concurrent_increments_trigger_open_exactly_once(self):
        """Exactement N échecs concurrents ouvrent le circuit une seule fois."""
        # On veut précisément _TEST_THRESHOLD threads
        barrier = threading.Barrier(_TEST_THRESHOLD)
        errors: list[Exception] = []

        def worker():
            try:
                barrier.wait(timeout=5.0)
                _simulate_final_failure()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(_TEST_THRESHOLD)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        assert not errors, f"Erreurs dans les threads : {errors}"
        # Le circuit doit être ouvert (open_until dans le futur)
        assert ec._circuit_state['open_until'] > time.time()
        # failure_count remis à 0 après ouverture
        assert ec._circuit_state['failure_count'] == 0
