"""The Context Score and the gate that runs before anything else.

ENG-011 turns the retail question -- *is there an entry?* -- into the
professional one: *is this a market we should be looking in at all?* If the
answer is no, the Smart Money engine never runs and no entry score is computed.
The reason is recorded either way, because "we did not look" needs to be as
explainable as "we looked and declined".

Two boundaries this module is careful about:

**Context never scores the entry.** Per ADR-0008 the context factors and the
entry factors are disjoint sets: counting killzone or volatility in both places
would pay twice for one piece of evidence. Context gates; it does not add points
to a setup.

**A veto is not a low score.** Blown spread, ungovernable volatility, stale
data: these fail the gate at any score, because they are conditions under which
the numbers themselves stop meaning anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Final, Mapping, Protocol

from elyon.modules.market_data.domain.series import CandleSeries
from elyon.modules.smart_money.domain.structure import Trend, build_structure
from elyon.modules.strategy.domain import Killzone, SessionClock
from elyon.shared_kernel.edcs.numeric import ZERO, DeterminismError, dec

from .dna import MarketDna
from .regime import MarketRegime, RegimeReading, VolatilityRegime, read_regime


class ContextFactor(str, Enum):
    """What makes a market worth looking in.

    Disjoint from the entry-scoring factors by design (ADR-0008): nothing here
    appears in the Entry Score, and nothing there appears here.
    """

    REGIME = "REGIME"                    # trend / clean range / expansion
    MTF_ALIGNMENT = "MTF_ALIGNMENT"      # higher and lower timeframes agree
    MARKET_QUALITY = "MARKET_QUALITY"    # efficiency healthy, no broken bars
    VOLATILITY = "VOLATILITY"            # inside the instrument's usable band
    SESSION = "SESSION"                  # inside this asset's efficient hours
    LIQUIDITY = "LIQUIDITY"              # spread behaving
    NEWS_CLEAR = "NEWS_CLEAR"            # no high-impact event imminent
    NO_MANIPULATION = "NO_MANIPULATION"  # no raid-and-reverse in progress


CONTEXT_WEIGHTS: Final[Mapping[ContextFactor, int]] = {
    ContextFactor.REGIME: 22,
    ContextFactor.MTF_ALIGNMENT: 16,
    ContextFactor.MARKET_QUALITY: 16,
    ContextFactor.VOLATILITY: 12,
    ContextFactor.SESSION: 12,
    ContextFactor.LIQUIDITY: 10,
    ContextFactor.NEWS_CLEAR: 8,
    ContextFactor.NO_MANIPULATION: 4,
}

DEFAULT_THRESHOLD: Final[int] = 60
# Crossing back below the threshold takes more than crossing it did, so the gate
# does not flicker open and shut on a market sitting exactly on the line.
DEFAULT_HYSTERESIS: Final[int] = 5


class ContextVeto(str, Enum):
    """Conditions under which the score stops meaning anything."""

    SPREAD_BLOWOUT = "SPREAD_BLOWOUT"
    EXTREME_VOLATILITY = "EXTREME_VOLATILITY"
    DEAD_MARKET = "DEAD_MARKET"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    STALE_DATA = "STALE_DATA"
    NEWS_BLACKOUT = "NEWS_BLACKOUT"


class GateResult(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


class ContextBand(str, Enum):
    """How good a passing context is -- which Risk may use, within limits."""

    POOR = "POOR"            # < 45
    MARGINAL = "MARGINAL"    # 45-59
    TRADEABLE = "TRADEABLE"  # 60-79
    EXCELLENT = "EXCELLENT"  # >= 80


class NewsCalendar(Protocol):
    """Whether a high-impact event is imminent for this instrument."""

    def is_blocked(self, symbol: str, at_ns: int) -> bool: ...
    def describe(self, symbol: str, at_ns: int) -> str: ...


class NoCalendar:
    """The honest default: there is no calendar feed.

    It does not claim the window is clear -- it says it cannot know. The
    NEWS_CLEAR factor is withheld rather than awarded, which caps the reachable
    context score at 92 and makes the missing feed visible in every reading
    instead of silently granting eight free points.
    """

    def is_blocked(self, symbol: str, at_ns: int) -> bool:
        return False

    def describe(self, symbol: str, at_ns: int) -> str:
        return "no economic calendar connected; news risk unknown"


@dataclass(frozen=True, slots=True)
class FactorReading:
    factor: ContextFactor
    awarded: int
    weight: int
    satisfied: bool
    detail: str

    def __str__(self) -> str:
        mark = "✓" if self.satisfied else "·"
        return (
            f"{mark} {self.factor.value:<16} {self.awarded:>3}/{self.weight:<3} "
            f"{self.detail}"
        )


@dataclass(frozen=True, slots=True)
class MarketContext:
    """The verdict, and everything behind it."""

    symbol: str
    bar_close_time_ns: int
    score: int
    threshold: int
    gate: GateResult
    factors: tuple[FactorReading, ...]
    vetoes: tuple[tuple[ContextVeto, str], ...]
    regime: RegimeReading
    dna_hash: str
    dna_calibrated: bool

    @property
    def band(self) -> ContextBand:
        if self.score < 45:
            return ContextBand.POOR
        if self.score < self.threshold:
            return ContextBand.MARGINAL
        if self.score < 80:
            return ContextBand.TRADEABLE
        return ContextBand.EXCELLENT

    @property
    def should_scan(self) -> bool:
        """Whether the Smart Money engine runs at all."""
        return self.gate is GateResult.PASS

    @property
    def blocking_vetoes(self) -> tuple[ContextVeto, ...]:
        return tuple(v for v, _ in self.vetoes)

    @property
    def gate_reason(self) -> str:
        """Why the engine did or did not look -- recorded either way."""
        if self.vetoes:
            veto, detail = self.vetoes[0]
            return f"veto:{veto.value.lower()} ({detail})"
        if self.gate is GateResult.PASS:
            return f"context {self.score}/{self.threshold} ({self.band.value})"
        missing = [f.factor.value for f in self.factors if not f.satisfied]
        return (
            f"context {self.score} below {self.threshold}; "
            f"missing {', '.join(missing) or 'nothing'}"
        )

    @property
    def max_achievable(self) -> int:
        """The ceiling given what the platform can actually observe.

        Below 100 whenever a data source is missing -- which is worth surfacing:
        a system quietly scoring out of 92 while reporting out of 100 is
        understating how much it does not know.
        """
        return sum(CONTEXT_WEIGHTS.values())

    def summary(self) -> str:
        lines = [str(f) for f in self.factors]
        lines.append(f"  {'':<16} {self.score:>3}/{self.max_achievable}   "
                     f"threshold {self.threshold} → {self.gate.value}")
        for veto, detail in self.vetoes:
            lines.append(f"⛔ {veto.value}: {detail}")
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class ContextConfig:
    swing_grade: int = 1
    window: int = 20
    min_bars: int = 30
    hysteresis: int = DEFAULT_HYSTERESIS
    # Bars of silence after which the feed is considered stale.
    max_staleness_bars: int = 3
    allow_extreme_volatility: bool = False


def _award(
    factor: ContextFactor, satisfied: bool, detail: str, *, fraction: str = "1"
) -> FactorReading:
    weight = CONTEXT_WEIGHTS[factor]
    awarded = int(weight * dec(fraction)) if satisfied else 0
    return FactorReading(factor, awarded, weight, satisfied, detail)


def read_context(
    series: CandleSeries,
    atr: Decimal,
    dna: MarketDna,
    *,
    spread: Decimal | None = None,
    clock: SessionClock | None = None,
    calendar: NewsCalendar | None = None,
    config: ContextConfig | None = None,
    previous: MarketContext | None = None,
) -> MarketContext:
    """Decide whether this is a market worth looking in.

    ``previous`` supplies the hysteresis: a gate that is already open stays open
    a little below the threshold, so a market resting on the line does not
    switch the engine on and off bar after bar.
    """
    settings = config or ContextConfig()
    session = clock or SessionClock()
    news = calendar or NoCalendar()
    observed_spread = spread if spread is not None else dna.typical_spread
    now_ns = series[-1].close_time_ns if len(series) else 0

    vetoes: list[tuple[ContextVeto, str]] = []

    # Insufficient data fails before anything is computed: a score derived from
    # half a window is a number, not information.
    if len(series) < settings.min_bars:
        vetoes.append((
            ContextVeto.INSUFFICIENT_DATA,
            f"{len(series)} bars, need {settings.min_bars}",
        ))
        return MarketContext(
            symbol=dna.symbol, bar_close_time_ns=now_ns, score=0,
            threshold=dna.context_threshold, gate=GateResult.FAIL,
            factors=(), vetoes=tuple(vetoes),
            regime=read_regime(series, atr, dna) if len(series) else _no_regime(dna, atr),
            dna_hash=dna.dna_hash, dna_calibrated=dna.is_calibrated,
        )

    regime = read_regime(
        series, atr, dna, swing_grade=settings.swing_grade, window=settings.window
    )

    # -- hard vetoes ------------------------------------------------------
    if dna.spread_is_blown(observed_spread):
        vetoes.append((
            ContextVeto.SPREAD_BLOWOUT,
            f"spread {observed_spread} over max {dna.max_spread} "
            f"({dna.spread_ratio(observed_spread)}× typical)",
        ))
    if regime.volatility is VolatilityRegime.EXTREME and not settings.allow_extreme_volatility:
        vetoes.append((
            ContextVeto.EXTREME_VOLATILITY,
            f"ATR {regime.atr_ratio}× typical; any sane stop is noise",
        ))
    if regime.volatility is VolatilityRegime.DEAD:
        vetoes.append((
            ContextVeto.DEAD_MARKET,
            f"ATR {regime.atr_ratio}× typical; nothing to capture",
        ))
    if _is_stale(series, settings):
        vetoes.append((
            ContextVeto.STALE_DATA,
            "no price movement across the staleness window; the feed may be dead",
        ))
    if news.is_blocked(dna.symbol, now_ns):
        vetoes.append((ContextVeto.NEWS_BLACKOUT, news.describe(dna.symbol, now_ns)))

    # -- factors ----------------------------------------------------------
    factors = [
        _score_regime(regime),
        _score_alignment(series, settings),
        _score_quality(series, regime, settings),
        _score_volatility(regime),
        _score_session(series, dna, session),
        _score_liquidity(observed_spread, dna),
        _score_news(dna, news, now_ns),
        _score_manipulation(regime),
    ]
    score = sum(f.awarded for f in factors)

    # -- gate -------------------------------------------------------------
    threshold = dna.context_threshold
    effective = threshold
    if previous is not None and previous.gate is GateResult.PASS:
        effective = threshold - settings.hysteresis

    gate = (
        GateResult.PASS
        if score >= effective and not vetoes
        else GateResult.FAIL
    )

    return MarketContext(
        symbol=dna.symbol,
        bar_close_time_ns=now_ns,
        score=score,
        threshold=threshold,
        gate=gate,
        factors=tuple(factors),
        vetoes=tuple(vetoes),
        regime=regime,
        dna_hash=dna.dna_hash,
        dna_calibrated=dna.is_calibrated,
    )


def _no_regime(dna: MarketDna, atr: Decimal) -> RegimeReading:
    return RegimeReading(
        volatility=VolatilityRegime.DEAD, regime=MarketRegime.UNDETERMINED,
        atr_ratio=ZERO, efficiency=ZERO, range_ratio=ZERO,
        trend=Trend.UNDETERMINED, detail="no data",
    )


def _is_stale(series: CandleSeries, config: ContextConfig) -> bool:
    """A feed that has stopped moving is indistinguishable from one that died."""
    recent = list(series)[-config.max_staleness_bars:]
    if len(recent) < config.max_staleness_bars:
        return False
    return all(c.high == c.low for c in recent)


def _score_regime(regime: RegimeReading) -> FactorReading:
    if regime.regime is MarketRegime.TREND:
        return _award(ContextFactor.REGIME, True, f"TREND, ER {regime.efficiency}")
    if regime.regime in (MarketRegime.EXPANSION, MarketRegime.RANGE):
        # Tradeable, but not the regime the strategy is built for.
        return _award(
            ContextFactor.REGIME, True,
            f"{regime.regime.value}, ER {regime.efficiency}", fraction="0.7",
        )
    if regime.regime is MarketRegime.CHURN:
        return _award(
            ContextFactor.REGIME, False,
            f"CHURN (ER {regime.efficiency}) -- movement without direction",
        )
    return _award(
        ContextFactor.REGIME, False, f"{regime.regime.value}, not tradeable"
    )


def _score_alignment(series: CandleSeries, config: ContextConfig) -> FactorReading:
    """Do the slower and faster reads of structure agree?

    A proxy for multi-timeframe alignment built from one series: the whole
    window is the higher-timeframe read, its last third the lower. It is not the
    same as a real H4-versus-M5 comparison, and the detail says so rather than
    implying a resolution the data does not have.
    """
    htf = build_structure(series, grade=config.swing_grade).trend
    tail = max(config.window, len(series) // 3)
    ltf = build_structure(
        series.window(len(series) - 1, tail), grade=config.swing_grade
    ).trend

    directional = {Trend.BULLISH, Trend.BEARISH}
    if htf in directional and htf is ltf:
        return _award(
            ContextFactor.MTF_ALIGNMENT, True,
            f"slow and fast structure both {htf.value} (single-series proxy)",
        )
    if htf in directional and ltf not in directional:
        return _award(
            ContextFactor.MTF_ALIGNMENT, True,
            f"slow {htf.value}, fast {ltf.value} -- partial", fraction="0.5",
        )
    if htf in directional and ltf in directional:
        return _award(
            ContextFactor.MTF_ALIGNMENT, False,
            f"slow {htf.value} against fast {ltf.value} -- conflict",
        )
    return _award(
        ContextFactor.MTF_ALIGNMENT, False,
        f"no directional structure (slow {htf.value}, fast {ltf.value})",
    )


def _score_quality(
    series: CandleSeries, regime: RegimeReading, config: ContextConfig
) -> FactorReading:
    """Is the tape clean enough to read?

    Broken bars -- zero-range prints in a market that is supposed to be moving
    -- mean the feed is unreliable, and every level derived from it is suspect.
    """
    recent = list(series)[-config.window:]
    broken = sum(1 for c in recent if c.high == c.low)
    if broken > len(recent) // 4:
        return _award(
            ContextFactor.MARKET_QUALITY, False,
            f"{broken}/{len(recent)} zero-range bars -- the tape is unreliable",
        )
    if regime.efficiency < dec("0.15"):
        return _award(
            ContextFactor.MARKET_QUALITY, False,
            f"efficiency {regime.efficiency}: price retraces nearly every step",
        )
    if regime.efficiency < dec("0.30"):
        return _award(
            ContextFactor.MARKET_QUALITY, True,
            f"efficiency {regime.efficiency}, workable", fraction="0.6",
        )
    return _award(
        ContextFactor.MARKET_QUALITY, True, f"efficiency {regime.efficiency}, clean"
    )


def _score_volatility(regime: RegimeReading) -> FactorReading:
    if regime.volatility.is_ideal:
        return _award(
            ContextFactor.VOLATILITY, True,
            f"{regime.volatility.value} ({regime.atr_ratio}× typical)",
        )
    if regime.volatility is VolatilityRegime.QUIET:
        return _award(
            ContextFactor.VOLATILITY, True,
            f"QUIET ({regime.atr_ratio}× typical), thin but tradeable",
            fraction="0.5",
        )
    return _award(
        ContextFactor.VOLATILITY, False,
        f"{regime.volatility.value} ({regime.atr_ratio}× typical)",
    )


def _score_session(
    series: CandleSeries, dna: MarketDna, clock: SessionClock
) -> FactorReading:
    """Is this one of the hours this instrument actually moves in?

    The hours come from the profile, not from a global constant: index cash
    hours are not FX killzones, and crypto has neither.
    """
    at = series[-1].close_time_ns
    zone = clock.killzone(at)
    if clock.in_killzone(at, *dna.efficiency_hours):
        return _award(
            ContextFactor.SESSION, True,
            f"{zone.value}, an efficient window for {dna.symbol}",
        )
    return _award(
        ContextFactor.SESSION, False,
        f"{zone.value} is outside {dna.symbol}'s efficient hours",
    )


def _score_liquidity(spread: Decimal, dna: MarketDna) -> FactorReading:
    ratio = dna.spread_ratio(spread)
    if ratio <= dec("1.5"):
        return _award(
            ContextFactor.LIQUIDITY, True, f"spread {spread} ({ratio}× typical)"
        )
    if ratio <= dec("2.5"):
        return _award(
            ContextFactor.LIQUIDITY, True,
            f"spread {spread} ({ratio}× typical), widening", fraction="0.5",
        )
    return _award(
        ContextFactor.LIQUIDITY, False,
        f"spread {spread} at {ratio}× typical -- the cost of being wrong has risen",
    )


def _score_news(dna: MarketDna, calendar: NewsCalendar, at_ns: int) -> FactorReading:
    """Only awarded when something actually checked.

    Awarding it by default would hand out eight free points for a question
    nobody asked, and make an unconnected calendar invisible.
    """
    if isinstance(calendar, NoCalendar):
        return _award(
            ContextFactor.NEWS_CLEAR, False, calendar.describe(dna.symbol, at_ns)
        )
    if calendar.is_blocked(dna.symbol, at_ns):
        return _award(
            ContextFactor.NEWS_CLEAR, False, calendar.describe(dna.symbol, at_ns)
        )
    return _award(ContextFactor.NEWS_CLEAR, True, "no high-impact event in window")


def _score_manipulation(regime: RegimeReading) -> FactorReading:
    """Churn at high volatility is what a stop-hunt looks like from outside."""
    violent_churn = (
        regime.regime is MarketRegime.CHURN
        and regime.volatility in (VolatilityRegime.ACTIVE, VolatilityRegime.EXTREME)
    )
    if violent_churn:
        return _award(
            ContextFactor.NO_MANIPULATION, False,
            f"violent churn (ER {regime.efficiency} at {regime.atr_ratio}× ATR)",
        )
    return _award(ContextFactor.NO_MANIPULATION, True, "no raid pattern in progress")
