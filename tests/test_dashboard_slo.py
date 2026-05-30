"""Tests dashboard SLO (P2-4)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'code', 'src'))

from dashboard_slo import DashboardSLO


class TestSLOSaveFailures:
    """SLO-SaveFailures: Never ≥3 consecutive failures."""

    def test_zero_failures_healthy(self):
        """Zéro failures → healthy."""
        slo = DashboardSLO()
        slo.update_save_failure_count(0)
        status = slo.check_save_failures()
        assert status["healthy"] is True

    def test_one_failure_healthy(self):
        """1 failure → healthy."""
        slo = DashboardSLO()
        slo.update_save_failure_count(1)
        status = slo.check_save_failures()
        assert status["healthy"] is True

    def test_two_failures_healthy(self):
        """2 failures → healthy."""
        slo = DashboardSLO()
        slo.update_save_failure_count(2)
        status = slo.check_save_failures()
        assert status["healthy"] is True

    def test_three_failures_unhealthy(self):
        """3 failures → UNHEALTHY."""
        slo = DashboardSLO()
        slo.update_save_failure_count(3)
        status = slo.check_save_failures()
        assert status["healthy"] is False

    def test_failure_message(self):
        """Message inclut valeur/seuil."""
        slo = DashboardSLO()
        slo.update_save_failure_count(2)
        status = slo.check_save_failures()
        assert "2" in status["message"]
        assert "3" in status["message"]


class TestSLODailyLoss:
    """SLO-DailyLoss: Daily loss < 5%."""

    def test_zero_loss_healthy(self):
        """0% loss → healthy."""
        slo = DashboardSLO()
        slo.update_daily_loss(0.0)
        status = slo.check_daily_loss()
        assert status["healthy"] is True

    def test_three_percent_healthy(self):
        """3% loss → healthy."""
        slo = DashboardSLO()
        slo.update_daily_loss(3.0)
        status = slo.check_daily_loss()
        assert status["healthy"] is True

    def test_five_percent_unhealthy(self):
        """5% loss = seuil → UNHEALTHY."""
        slo = DashboardSLO()
        slo.update_daily_loss(5.0)
        status = slo.check_daily_loss()
        assert status["healthy"] is False

    def test_six_percent_unhealthy(self):
        """6% loss → UNHEALTHY."""
        slo = DashboardSLO()
        slo.update_daily_loss(6.0)
        status = slo.check_daily_loss()
        assert status["healthy"] is False

    def test_loss_message(self):
        """Message format."""
        slo = DashboardSLO()
        slo.update_daily_loss(4.5)
        status = slo.check_daily_loss()
        assert "4.50" in status["message"]
        assert "5.00" in status["message"]


class TestSLODrawdown:
    """SLO-Drawdown: Drawdown < 10%."""

    def test_zero_drawdown_healthy(self):
        """0% drawdown → healthy."""
        slo = DashboardSLO()
        slo.update_drawdown(0.0)
        status = slo.check_drawdown()
        assert status["healthy"] is True

    def test_five_percent_healthy(self):
        """5% drawdown → healthy."""
        slo = DashboardSLO()
        slo.update_drawdown(5.0)
        status = slo.check_drawdown()
        assert status["healthy"] is True

    def test_ten_percent_unhealthy(self):
        """10% drawdown = seuil → UNHEALTHY."""
        slo = DashboardSLO()
        slo.update_drawdown(10.0)
        status = slo.check_drawdown()
        assert status["healthy"] is False

    def test_eleven_percent_unhealthy(self):
        """11% drawdown → UNHEALTHY."""
        slo = DashboardSLO()
        slo.update_drawdown(11.0)
        status = slo.check_drawdown()
        assert status["healthy"] is False


class TestSLOCapitalInvariants:
    """SLO-CapitalInvariants: No violations."""

    def test_zero_violations_healthy(self):
        """0 violations → healthy."""
        slo = DashboardSLO()
        slo.update_capital_violations(0)
        status = slo.check_capital_invariants()
        assert status["healthy"] is True

    def test_one_violation_unhealthy(self):
        """1 violation → UNHEALTHY."""
        slo = DashboardSLO()
        slo.update_capital_violations(1)
        status = slo.check_capital_invariants()
        assert status["healthy"] is False

    def test_multiple_violations_unhealthy(self):
        """Multiple violations → UNHEALTHY."""
        slo = DashboardSLO()
        slo.update_capital_violations(5)
        status = slo.check_capital_invariants()
        assert status["healthy"] is False


class TestSLOCheckAll:
    """check_all_slos() retourne tous les 4 SLOs."""

    def test_all_four_slos_present(self):
        """Les 4 SLOs présents."""
        slo = DashboardSLO()
        all_slos = slo.check_all_slos()
        assert "save_failures" in all_slos
        assert "daily_loss" in all_slos
        assert "drawdown" in all_slos
        assert "capital_invariants" in all_slos

    def test_all_slos_structure(self):
        """Chaque SLO a healthy/message/values."""
        slo = DashboardSLO()
        all_slos = slo.check_all_slos()
        for slo_name, status in all_slos.items():
            assert "healthy" in status
            assert "message" in status
            assert "current_value" in status
            assert "threshold" in status


class TestSLOAllHealthy:
    """all_healthy() fonction globale."""

    def test_all_healthy_true(self):
        """Tous SLOs OK → True."""
        slo = DashboardSLO()
        slo.update_save_failure_count(0)
        slo.update_daily_loss(1.0)
        slo.update_drawdown(2.0)
        slo.update_capital_violations(0)
        assert slo.all_healthy() is True

    def test_all_healthy_false_one_fail(self):
        """Un SLO fail → False."""
        slo = DashboardSLO()
        slo.update_save_failure_count(0)
        slo.update_daily_loss(1.0)
        slo.update_drawdown(2.0)
        slo.update_capital_violations(1)  # ← fail
        assert slo.all_healthy() is False

    def test_all_healthy_false_multiple_fail(self):
        """Plusieurs SLOs fail → False."""
        slo = DashboardSLO()
        slo.update_save_failure_count(3)  # ← fail
        slo.update_daily_loss(6.0)  # ← fail
        slo.update_drawdown(2.0)
        slo.update_capital_violations(0)
        assert slo.all_healthy() is False


class TestSLOThresholds:
    """Vérifier les constantes de seuil."""

    def test_save_failure_limit(self):
        """CONSECUTIVE_SAVE_FAILURE_LIMIT = 3."""
        assert DashboardSLO.CONSECUTIVE_SAVE_FAILURE_LIMIT == 3

    def test_daily_loss_limit(self):
        """DAILY_LOSS_LIMIT_PCT = 5.0."""
        assert DashboardSLO.DAILY_LOSS_LIMIT_PCT == 5.0

    def test_max_drawdown_limit(self):
        """MAX_DRAWDOWN_LIMIT_PCT = 10.0."""
        assert DashboardSLO.MAX_DRAWDOWN_LIMIT_PCT == 10.0


class TestSLOEdgeCases:
    """Cas limites."""

    def test_negative_loss_treated_as_positive(self):
        """Loss négative → 0 (pas de flip)."""
        slo = DashboardSLO()
        slo.update_daily_loss(-1.0)
        status = slo.check_daily_loss()
        assert status["healthy"] is True
        assert status["current_value"] == -1.0

    def test_very_large_loss(self):
        """Loss massive → unhealthy."""
        slo = DashboardSLO()
        slo.update_daily_loss(50.0)
        status = slo.check_daily_loss()
        assert status["healthy"] is False

    def test_multiple_updates_reflect(self):
        """Updates multiples se reflètent."""
        slo = DashboardSLO()

        slo.update_daily_loss(2.0)
        assert slo.check_daily_loss()["healthy"] is True

        slo.update_daily_loss(5.0)
        assert slo.check_daily_loss()["healthy"] is False

        slo.update_daily_loss(3.0)
        assert slo.check_daily_loss()["healthy"] is True
