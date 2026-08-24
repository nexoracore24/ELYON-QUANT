"""Backtesting: turning strategies into evidence.

This is the module that closes the loop the strategy catalog depends on. A
strategy ships UNPROVEN and cannot trade alone; a backtest produces a
``Calibration``; the calibration -- not anyone's opinion -- decides the tier.

Three lies the simulator structurally refuses: look-ahead (a play is only ever
handed ``series.upto(i)``), intrabar optimism (a bar containing both stop and
target resolves as a stop), and costless fills (spread and slippage always move
against the trade). The fourth -- measuring on the data you designed on -- it
cannot detect, so it makes you declare it and refuses to certify an in-sample
run.
"""

from .costs import DEFAULT_COSTS, FREE, CostModel
from .report import (
    BacktestReport,
    Sample,
    calibration_from,
    report_from,
    tier_of,
)
from .synthetic import GeneratorConfig, generate
from .simulator import SimulationConfig, research_config, simulate
from .trade import ExitReason, FillModel, SimulatedTrade, TradeIntent

__all__ = [
    "BacktestReport", "CostModel", "GeneratorConfig", "generate", "DEFAULT_COSTS", "ExitReason", "FILL_MODELS",
    "FREE", "FillModel", "Sample", "SimulatedTrade", "SimulationConfig",
    "TradeIntent", "calibration_from", "report_from", "research_config",
    "simulate", "tier_of",
]

FILL_MODELS = tuple(FillModel)
