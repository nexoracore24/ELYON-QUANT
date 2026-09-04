"""The application layer: configuration and the loop that runs everything.

Each engine works on its own; this is what makes them a system you can run.
Ticks go in, candles form, and on every confirmed candle the pipeline runs in
the order ENG-011 fixed -- context gate, strategy, score, risk, OMS, position
management -- with every stage able to stop it and each recording why.
"""

from .config import Mode, RiskSettings, SessionConfig
from .live import (
    FeedState,
    LiveConfig,
    LiveRunner,
    ReplayFeed,
    TickFeed,
)
from .runner import BarOutcome, TradingSession
from .settings import (
    SETTINGS,
    Kind,
    Scope,
    Setting,
    apply_changes,
    changed_keys,
    describe,
)

__all__ = [
    "SETTINGS", "BarOutcome", "FeedState", "Kind", "LiveConfig",
    "LiveRunner", "Mode", "ReplayFeed", "RiskSettings", "Scope",
    "SessionConfig", "Setting", "TickFeed", "TradingSession",
    "apply_changes", "changed_keys", "describe",
]
