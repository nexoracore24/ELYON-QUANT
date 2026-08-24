"""Market DNA -- the personality profile of an instrument.

An ATR of 0.0008 is a dead market in gold and an ordinary one in EURUSD. Every
threshold in the engine is therefore expressed in DNA-relative units, never in
absolute prices, and this module is where those units come from.

**The inviolable rule: DNA adapts filters, never rules.** What a BOS *is*, what
an order block *is*, how the score is composed -- identical on every instrument.
What changes per asset is how wide "equal" is, how much penetration counts as a
sweep, what spread is tolerable. Anything that would change *which* detector
runs, or what it means, does not belong in a profile.

That rule is enforced structurally: a profile can only carry numbers, and the
engine reads them through :meth:`MarketDna.sensitivity`, which resolves
``dna.override ?? engine_default``. There is no hook for a profile to supply
behaviour.

Second rule, inherited from the tier system: **a hand-written profile is a
guess.** The reference profiles below are starting points, marked as such, and
``is_calibrated`` is False for every one of them. :func:`learn_dna` derives a
real profile from real bars.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import Enum
from typing import Final, Mapping

from elyon.modules.market_data.domain.series import CandleSeries
from elyon.modules.market_data.domain.atr import AtrProvider, true_range
from elyon.modules.strategy.domain import Killzone
from elyon.shared_kernel.edcs.canonical import config_hash
from elyon.shared_kernel.edcs.numeric import ZERO, DeterminismError, dec, quantize

# Below this many bars, a learned profile is describing noise.
MIN_DNA_SAMPLE: Final[int] = 200


class AssetClass(str, Enum):
    FX_MAJOR = "FX_MAJOR"
    METAL = "METAL"
    INDEX = "INDEX"
    CRYPTO = "CRYPTO"


class Provenance(str, Enum):
    """Where a profile's numbers came from.

    The distinction matters for the same reason a declared tier does: a guess
    that looks like a measurement will be trusted like one.
    """

    REFERENCE = "REFERENCE"    # hand-written starting point
    LEARNED = "LEARNED"        # derived from historical bars


# Engine defaults. A profile overrides these; it never replaces the logic that
# consumes them.
ENGINE_DEFAULTS: Final[Mapping[str, Decimal]] = {
    "equal_level_tol_atr": dec("0.10"),
    "sweep_min_penetration_atr": dec("0.05"),
    "sweep_wick_ratio": dec("0.50"),
    "displacement_atr_mult": dec("1.50"),
    "fvg_min_size_atr": dec("0.20"),
    "fib_min_leg_atr": dec("1.00"),
    "stop_buffer_atr": dec("0.30"),
}


@dataclass(frozen=True, slots=True)
class VolatilityBands:
    """Where this instrument's ATR sits relative to its own normal.

    Multipliers of ``typical_atr``, not prices -- which is the entire point:
    "quiet" means quiet *for this instrument*.
    """

    dead: Decimal = dec("0.40")
    low: Decimal = dec("0.70")
    high: Decimal = dec("1.60")
    extreme: Decimal = dec("2.50")

    def __post_init__(self) -> None:
        thresholds = [self.dead, self.low, self.high, self.extreme]
        if thresholds != sorted(thresholds):
            raise DeterminismError(
                f"volatility bands must ascend, got {thresholds}"
            )


@dataclass(frozen=True, slots=True)
class MarketDna:
    """One instrument's profile."""

    symbol: str
    asset_class: AssetClass
    tick_size: Decimal
    typical_atr: Decimal
    typical_spread: Decimal
    max_spread: Decimal
    efficiency_hours: tuple[Killzone, ...]
    bands: VolatilityBands = field(default_factory=VolatilityBands)
    overrides: Mapping[str, Decimal] = field(default_factory=dict)
    context_threshold: int = 60
    provenance: Provenance = Provenance.REFERENCE
    sample_bars: int = 0
    dna_version: str = "0.1"

    def __post_init__(self) -> None:
        if self.typical_atr <= ZERO:
            raise DeterminismError(f"{self.symbol}: typical ATR must be positive")
        if self.max_spread < self.typical_spread:
            raise DeterminismError(
                f"{self.symbol}: max spread {self.max_spread} below typical "
                f"{self.typical_spread}"
            )
        for name in self.overrides:
            if name not in ENGINE_DEFAULTS:
                raise DeterminismError(
                    f"{self.symbol}: {name!r} is not a tunable filter. A DNA "
                    f"profile adapts filters, never rules; known filters are: "
                    f"{', '.join(sorted(ENGINE_DEFAULTS))}"
                )

    # -- reading filters --------------------------------------------------

    def sensitivity(self, name: str) -> Decimal:
        """The effective value of a filter: ``dna.override ?? engine_default``."""
        if name not in ENGINE_DEFAULTS:
            raise DeterminismError(f"unknown filter {name!r}")
        return self.overrides.get(name, ENGINE_DEFAULTS[name])

    # -- normalising to this instrument -----------------------------------

    def atr_ratio(self, atr: Decimal) -> Decimal:
        """Current ATR as a multiple of this instrument's normal."""
        return quantize(atr / self.typical_atr, 4)

    def spread_ratio(self, spread: Decimal) -> Decimal:
        if self.typical_spread == ZERO:
            return ZERO
        return quantize(spread / self.typical_spread, 4)

    def spread_is_blown(self, spread: Decimal) -> bool:
        return spread > self.max_spread

    @property
    def is_calibrated(self) -> bool:
        """Whether these numbers were measured rather than guessed."""
        return (
            self.provenance is Provenance.LEARNED
            and self.sample_bars >= MIN_DNA_SAMPLE
        )

    # -- provenance -------------------------------------------------------

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "assetClass": self.asset_class.value,
            "tickSize": str(self.tick_size),
            "typicalAtr": str(self.typical_atr),
            "typicalSpread": str(self.typical_spread),
            "maxSpread": str(self.max_spread),
            "efficiencyHours": sorted(k.value for k in self.efficiency_hours),
            "bands": [
                str(self.bands.dead), str(self.bands.low),
                str(self.bands.high), str(self.bands.extreme),
            ],
            "overrides": {k: str(v) for k, v in sorted(self.overrides.items())},
            "contextThreshold": self.context_threshold,
            "dnaVersion": self.dna_version,
        }

    @property
    def dna_hash(self) -> str:
        """Travels in every decision's provenance, per ENG-011 §8.4."""
        return config_hash(self.to_canonical_dict())

    def describe(self) -> str:
        mark = "✓ calibrated" if self.is_calibrated else "⚠ reference profile"
        return (
            f"{self.symbol} ({self.asset_class.value})  {mark}\n"
            f"  typical ATR    {self.typical_atr}\n"
            f"  spread         {self.typical_spread} (max {self.max_spread})\n"
            f"  efficient in   {', '.join(k.value for k in self.efficiency_hours)}\n"
            f"  overrides      {len(self.overrides)} filter(s)\n"
            f"  dna hash       {self.dna_hash[:16]}…"
        )


FX_HOURS = (Killzone.LONDON_OPEN, Killzone.NY_AM, Killzone.LONDON_CLOSE)
INDEX_HOURS = (Killzone.NY_AM, Killzone.SILVER_BULLET_AM)
# Crypto trades around the clock, so no window is privileged. Handing it the FX
# killzones would filter out most of its actual activity.
CRYPTO_HOURS: tuple[Killzone, ...] = tuple(
    k for k in Killzone if k is not Killzone.OUTSIDE
)


# Reference profiles: relative starting points from ENG-011 §8.3, calibrated in
# backtesting. Every one is REFERENCE, so `is_calibrated` is False across the
# board -- exactly like the strategy catalog shipping entirely UNPROVEN.
REFERENCE_PROFILES: Final[Mapping[str, MarketDna]] = {
    p.symbol: p for p in (
        MarketDna(
            symbol="EURUSD", asset_class=AssetClass.FX_MAJOR,
            tick_size=dec("0.00001"), typical_atr=dec("0.00100"),
            typical_spread=dec("0.00010"), max_spread=dec("0.00040"),
            efficiency_hours=FX_HOURS,
        ),
        MarketDna(
            symbol="GBPUSD", asset_class=AssetClass.FX_MAJOR,
            tick_size=dec("0.00001"), typical_atr=dec("0.00140"),
            typical_spread=dec("0.00015"), max_spread=dec("0.00060"),
            efficiency_hours=(Killzone.LONDON_OPEN, Killzone.NY_AM),
            # More wick than EURUSD: equal levels need more room, and a poke has
            # to go further before it counts as a raid.
            overrides={
                "equal_level_tol_atr": dec("0.14"),
                "sweep_min_penetration_atr": dec("0.08"),
            },
        ),
        MarketDna(
            symbol="XAUUSD", asset_class=AssetClass.METAL,
            tick_size=dec("0.01"), typical_atr=dec("2.50"),
            typical_spread=dec("0.30"), max_spread=dec("1.20"),
            efficiency_hours=(Killzone.NY_AM, Killzone.LONDON_CLOSE),
            # Spiky. Wider tolerances and a wider stop buffer, same rules.
            overrides={
                "equal_level_tol_atr": dec("0.18"),
                "sweep_min_penetration_atr": dec("0.10"),
                "stop_buffer_atr": dec("0.45"),
            },
        ),
        MarketDna(
            symbol="NAS100", asset_class=AssetClass.INDEX,
            tick_size=dec("0.1"), typical_atr=dec("25.0"),
            typical_spread=dec("1.5"), max_spread=dec("6.0"),
            efficiency_hours=INDEX_HOURS,
            # Opening gaps are routine, so a gap has to be bigger to be a signal.
            overrides={"fvg_min_size_atr": dec("0.30")},
        ),
        MarketDna(
            symbol="US30", asset_class=AssetClass.INDEX,
            tick_size=dec("1"), typical_atr=dec("120.0"),
            typical_spread=dec("2.0"), max_spread=dec("10.0"),
            efficiency_hours=INDEX_HOURS,
            overrides={"fvg_min_size_atr": dec("0.28")},
        ),
        MarketDna(
            symbol="BTCUSD", asset_class=AssetClass.CRYPTO,
            tick_size=dec("0.01"), typical_atr=dec("350.0"),
            typical_spread=dec("8.0"), max_spread=dec("60.0"),
            efficiency_hours=CRYPTO_HOURS,
            bands=VolatilityBands(
                dead=dec("0.35"), low=dec("0.65"),
                high=dec("1.90"), extreme=dec("3.20"),
            ),
            overrides={
                "equal_level_tol_atr": dec("0.16"),
                "displacement_atr_mult": dec("1.70"),
            },
        ),
        MarketDna(
            symbol="ETHUSD", asset_class=AssetClass.CRYPTO,
            tick_size=dec("0.01"), typical_atr=dec("28.0"),
            typical_spread=dec("1.2"), max_spread=dec("9.0"),
            efficiency_hours=CRYPTO_HOURS,
            bands=VolatilityBands(
                dead=dec("0.35"), low=dec("0.60"),
                high=dec("2.00"), extreme=dec("3.50"),
            ),
            overrides={
                "equal_level_tol_atr": dec("0.20"),
                "displacement_atr_mult": dec("1.80"),
            },
        ),
    )
}


def profile_for(symbol: str) -> MarketDna:
    """The profile for an instrument.

    Refuses to invent one. A made-up profile would silently apply EURUSD
    tolerances to gold, and the failure would look like a bad strategy rather
    than a missing configuration.
    """
    try:
        return REFERENCE_PROFILES[symbol]
    except KeyError:
        known = ", ".join(sorted(REFERENCE_PROFILES))
        raise DeterminismError(
            f"no Market DNA for {symbol!r}. Adding an instrument means adding "
            f"its profile, not touching the engine. Known: {known}"
        ) from None


def learn_dna(
    series: CandleSeries,
    base: MarketDna,
    *,
    atr_period: int = 14,
    observed_spread: Decimal | None = None,
) -> MarketDna:
    """Derive a profile's numbers from real bars.

    Only the *measurable* fields move: typical ATR and, when a spread series is
    supplied, the spread. Efficiency hours and detector sensitivities stay as
    configured, because those are research decisions rather than statistics --
    and letting a fit rewrite them would be exactly the auto-mutation ENG-011
    forbids.

    The median is used rather than the mean: one volatility spike should not
    redefine what normal looks like for the following month.
    """
    if len(series) < atr_period + 1:
        raise DeterminismError(
            f"{len(series)} bars is not enough to learn a profile from "
            f"(need more than {atr_period})"
        )

    provider = AtrProvider(period=atr_period, output_scale=8)
    readings: list[Decimal] = []
    for candle in series:
        provider.update(candle)
        if provider.value is not None:
            readings.append(provider.value)

    if not readings:
        raise DeterminismError("ATR never seeded; cannot learn a profile")

    typical = _median(readings)
    if typical <= ZERO:
        raise DeterminismError(
            f"{base.symbol}: measured a typical ATR of {typical}; the sample is "
            f"flat and describes no instrument"
        )

    spread = observed_spread if observed_spread is not None else base.typical_spread
    return replace(
        base,
        typical_atr=typical,
        typical_spread=spread,
        max_spread=max(base.max_spread, spread),
        provenance=Provenance.LEARNED,
        sample_bars=len(series),
    )


def _median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return quantize((ordered[middle - 1] + ordered[middle]) / dec(2), 8)
