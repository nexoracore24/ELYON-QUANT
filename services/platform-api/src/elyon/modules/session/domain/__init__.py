"""The application layer: configuration and the loop that runs everything.

Each engine works on its own; this is what makes them a system you can run.
Ticks go in, candles form, and on every confirmed candle the pipeline runs in
the order ENG-011 fixed -- context gate, strategy, score, risk, OMS, position
management -- with every stage able to stop it and each recording why.
"""

from .config import Mode, RiskSettings, SessionConfig
from .runner import BarOutcome, TradingSession

__all__ = [
    "BarOutcome", "Mode", "RiskSettings", "SessionConfig", "TradingSession",
]
