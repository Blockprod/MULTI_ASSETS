"""Broker-neutral runtime models for crypto spot adapters."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict


@dataclass(frozen=True)
class BrokerConfig:
    """Configuration needed to create a spot broker client."""

    broker: str
    api_key: str
    api_secret: str
    api_url: str
    ws_url: str
    enable_native_trailing: bool = False
    strict_pair_validation: bool = True


@dataclass(frozen=True)
class PairConfig:
    """Resolved exchange metadata for one internal trading pair."""

    pair_key: str
    broker_symbol: str
    broker_pair_id: str
    base_asset: str
    quote_asset: str
    quote_for_accounting: str
    min_qty: Decimal
    step_size: Decimal
    tick_size: Decimal
    min_notional: Decimal
    status: str = "online"


@dataclass(frozen=True)
class KrakenHistoryStatus:
    """Diagnostic for one Kraken OHLC history request."""

    pair_key: str
    broker_symbol: str
    timeframe: str
    requested_start: str
    oldest_available: str
    newest_available: str
    bars_available: int
    bars_required: int
    source: str
    eligible: bool
    history_depth_limited: bool

    def as_dict(self) -> Dict[str, Any]:
        return {
            "pair_key": self.pair_key,
            "broker_symbol": self.broker_symbol,
            "timeframe": self.timeframe,
            "requested_start": self.requested_start,
            "oldest_available": self.oldest_available,
            "newest_available": self.newest_available,
            "bars_available": self.bars_available,
            "bars_required": self.bars_required,
            "source": self.source,
            "eligible": self.eligible,
            "history_depth_limited": self.history_depth_limited,
        }
