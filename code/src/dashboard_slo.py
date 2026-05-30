"""Dashboard SLO safety metrics (P2-4).

4 SLO objectives for display on UI:
1. SaveFailures: Never ≥3 consecutive save failures
2. DailyLoss: Daily loss < daily_loss_limit_pct
3. Drawdown: Drawdown tracking < max_drawdown_pct
4. CapitalInvariants: No double exposure + emergency halt blocks
"""

from typing import TypedDict, Dict
from dataclasses import dataclass


class SLOStatus(TypedDict):
    """SLO status for dashboard."""
    healthy: bool
    message: str
    current_value: float
    threshold: float


@dataclass
class SLOMetrics:
    """SLO metrics tracking."""

    consecutive_save_failures: int = 0
    daily_loss_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    capital_invariant_violations: int = 0


class DashboardSLO:
    """Dashboard SLO validator."""

    # Thresholds
    CONSECUTIVE_SAVE_FAILURE_LIMIT = 3
    DAILY_LOSS_LIMIT_PCT = 5.0  # 5%
    MAX_DRAWDOWN_LIMIT_PCT = 10.0  # 10%

    def __init__(self):
        """Initialize SLO tracker."""
        self.metrics = SLOMetrics()

    def update_save_failure_count(self, count: int) -> None:
        """Update consecutive save failures."""
        self.metrics.consecutive_save_failures = count

    def update_daily_loss(self, loss_pct: float) -> None:
        """Update daily loss percentage."""
        self.metrics.daily_loss_pct = loss_pct

    def update_drawdown(self, drawdown_pct: float) -> None:
        """Update max drawdown percentage."""
        self.metrics.max_drawdown_pct = drawdown_pct

    def update_capital_violations(self, count: int) -> None:
        """Update capital invariant violation count."""
        self.metrics.capital_invariant_violations = count

    def check_save_failures(self) -> SLOStatus:
        """Check SLO: consecutive save failures < 3."""
        healthy = self.metrics.consecutive_save_failures < self.CONSECUTIVE_SAVE_FAILURE_LIMIT
        message = (
            f"Save failures: {self.metrics.consecutive_save_failures}/{self.CONSECUTIVE_SAVE_FAILURE_LIMIT}"
        )
        return SLOStatus(
            healthy=healthy,
            message=message,
            current_value=float(self.metrics.consecutive_save_failures),
            threshold=float(self.CONSECUTIVE_SAVE_FAILURE_LIMIT),
        )

    def check_daily_loss(self) -> SLOStatus:
        """Check SLO: daily loss < daily_loss_limit_pct."""
        healthy = self.metrics.daily_loss_pct < self.DAILY_LOSS_LIMIT_PCT
        message = (
            f"Daily loss: {self.metrics.daily_loss_pct:.2f}% / {self.DAILY_LOSS_LIMIT_PCT:.2f}%"
        )
        return SLOStatus(
            healthy=healthy,
            message=message,
            current_value=self.metrics.daily_loss_pct,
            threshold=self.DAILY_LOSS_LIMIT_PCT,
        )

    def check_drawdown(self) -> SLOStatus:
        """Check SLO: drawdown < max_drawdown_pct."""
        healthy = self.metrics.max_drawdown_pct < self.MAX_DRAWDOWN_LIMIT_PCT
        message = (
            f"Drawdown: {self.metrics.max_drawdown_pct:.2f}% / {self.MAX_DRAWDOWN_LIMIT_PCT:.2f}%"
        )
        return SLOStatus(
            healthy=healthy,
            message=message,
            current_value=self.metrics.max_drawdown_pct,
            threshold=self.MAX_DRAWDOWN_LIMIT_PCT,
        )

    def check_capital_invariants(self) -> SLOStatus:
        """Check SLO: no capital invariant violations."""
        healthy = self.metrics.capital_invariant_violations == 0
        message = (
            f"Capital violations: {self.metrics.capital_invariant_violations}"
        )
        return SLOStatus(
            healthy=healthy,
            message=message,
            current_value=float(self.metrics.capital_invariant_violations),
            threshold=0.0,
        )

    def check_all_slos(self) -> Dict[str, SLOStatus]:
        """Check all 4 SLOs."""
        return {
            "save_failures": self.check_save_failures(),
            "daily_loss": self.check_daily_loss(),
            "drawdown": self.check_drawdown(),
            "capital_invariants": self.check_capital_invariants(),
        }

    def all_healthy(self) -> bool:
        """All SLOs healthy?"""
        all_slos = self.check_all_slos()
        return all(slo["healthy"] for slo in all_slos.values())
