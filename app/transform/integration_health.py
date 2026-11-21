"""Is sales data flowing? (T3.09; POS-FR-27, CMD-FR-08, AC-CMD-05, OBS-06). Pure.

A connection is healthy while its reads succeed and its webhook events are acted on. A read that
has not succeeded for more than two hours means sales since then may be missing, not zero
(AC-CMD-05); a connection the POS revoked is critical.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

STALE_AFTER = timedelta(hours=2)  # AC-CMD-05, OBS-06's founder threshold
EVENT_DELAY = timedelta(minutes=15)  # a webhook event waiting longer is behind
OK, SETUP, WARNING, CRITICAL, OFF = "ok", "setup", "warning", "critical", "off"


@dataclass(frozen=True)
class Reads:
    """A connection's order reads: the last of each outcome, and the last 24 hours' tally."""

    last_success: datetime | None = None
    last_failure: datetime | None = None
    runs_24h: int = 0
    failures_24h: int = 0


@dataclass(frozen=True)
class ConnectionHealth:
    provider: str
    status: str  # the connection's: active, revoked, disconnected
    tracked: int  # the business's locations taking its sales
    reads: Reads
    waiting_events: int  # webhook events received over EVENT_DELAY ago, not yet acted on
    token_expires_at: datetime | None
    unmapped: int  # POS products sold with no product size
    now: datetime

    @property
    def lag(self) -> timedelta | None:
        last = self.reads.last_success
        return self.now - last if last else None

    @property
    def failing(self) -> bool:
        failed, worked = self.reads.last_failure, self.reads.last_success
        return failed is not None and (worked is None or failed > worked)

    @property
    def stale(self) -> bool:
        lag = self.lag
        return self.status == "active" and self.tracked > 0 and (lag is None or lag > STALE_AFTER)

    @property
    def error_rate(self) -> float | None:
        if not self.reads.runs_24h:
            return None
        return self.reads.failures_24h / self.reads.runs_24h

    @property
    def state(self) -> str:
        if self.status == "revoked":
            return CRITICAL
        if self.status != "active":
            return OFF
        if self.tracked == 0:
            return SETUP
        if self.stale or self.failing or self.waiting_events:
            return WARNING
        return OK
