"""Read-only provider boundary consumed by the Dashboard API."""

from datetime import datetime
from typing import Protocol

from adaptive_trading_bot.dashboard.models import DashboardSnapshot


class DashboardSnapshotProvider(Protocol):
    """Return one coherent detached snapshot without changing trading state."""

    def snapshot(self, observed_at: datetime | None = None) -> DashboardSnapshot: ...
