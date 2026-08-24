"""Turn a six-pillar reading into a score.

Five pillars answer to one factor each. The sixth -- FIBONACCI -- is scored
through PRICING, because a measured leg is only worth points when price is on
the favourable side of it: a leg drawn under a setup bought at a premium is
evidence against the trade, not for it.

Everything is recorded, found or missing, so "no trade" comes back as specific
as "trade".
"""

from __future__ import annotations

from typing import Mapping, Sequence

from elyon.modules.trading.domain.scoring import Factor, Score, ScoreBuilder, Veto

from .six_pillars import Pillar, SixPillarSetup

# A veto as the caller supplies it: which rule, whether it fired, and the
# evidence. Inactive vetoes are passed too -- proving a rule was evaluated and
# passed is what makes the audit trail worth having.
VetoCheck = tuple[Veto, bool, str]

# The pillars that map one-to-one onto a factor.
PILLAR_FACTORS: Mapping[Pillar, Factor] = {
    Pillar.TENDENCIA: Factor.HTF_BIAS,
    Pillar.LIQUIDEZ: Factor.LIQUIDITY_SWEEP,
    Pillar.ORDER_BLOCK: Factor.POI_QUALITY,
    Pillar.FVG: Factor.IMBALANCE,
    Pillar.OTE: Factor.OTE_FIBONACCI,
}

# Scored from the pricing evidence rather than from the finding alone.
PRICING_PILLAR: Pillar = Pillar.FIBONACCI


def score_setup(
    setup: SixPillarSetup,
    *,
    threshold: int | None = None,
    vetoes: Sequence[VetoCheck] = (),
) -> Score:
    """Score a six-pillar reading.

    Pillars that stand earn their factor; pillars that do not are recorded with
    the reason they failed. Vetoes are checked last and block outright -- they
    never subtract from the total, so the score still reports what was seen even
    when the trade is refused.
    """
    builder = (
        ScoreBuilder(threshold=threshold) if threshold is not None else ScoreBuilder()
    )

    for pillar, factor in PILLAR_FACTORS.items():
        finding = setup.finding(pillar)
        if finding.found:
            builder.award(factor, f"{pillar.value}: {finding.detail}")
        else:
            builder.withhold(factor, f"{pillar.value}: {finding.detail}")

    # FIBONACCI, via pricing. The leg has to exist *and* price has to be on the
    # side of equilibrium the direction wants.
    fib = setup.finding(PRICING_PILLAR)
    if fib.found and setup.favourable_pricing:
        builder.award(
            Factor.PRICING,
            f"{PRICING_PILLAR.value}: {fib.detail}, price "
            f"{setup.pricing.value.lower() if setup.pricing else 'unclassified'}",
        )
    elif fib.found:
        builder.withhold(
            Factor.PRICING,
            f"{PRICING_PILLAR.value}: leg measured but price "
            f"{setup.pricing.value.lower() if setup.pricing else 'unclassified'}",
        )
    else:
        builder.withhold(Factor.PRICING, f"{PRICING_PILLAR.value}: {fib.detail}")

    # Displacement is what separates an impulse from drift; without it the
    # block and the gap are just shapes on a chart.
    if setup.displacement is not None:
        builder.award(
            Factor.STRUCTURE,
            f"displacement {setup.displacement.move} "
            f"({setup.displacement.direction.name})",
        )
    else:
        builder.withhold(Factor.STRUCTURE, "no displacement")

    # Somewhere worth travelling to: without a target the reward side of the
    # trade is guesswork.
    if setup.target is not None:
        builder.award(Factor.TARGET_LIQUIDITY, f"target liquidity at {setup.target}")
    else:
        builder.withhold(Factor.TARGET_LIQUIDITY, "no liquidity to travel to")

    # Volume is a supporting witness, not a pillar; it stays unscored until a
    # real feed makes it meaningful.
    builder.withhold(Factor.VOLUME, "not evaluated on this feed")

    for veto, active, evidence in vetoes:
        builder.check_veto(veto, active, evidence)

    return builder.build()


def pillar_summary(setup: SixPillarSetup) -> str:
    """One line per pillar, in strategy order -- what a trader would scan."""
    return "\n".join(str(f) for f in setup.findings)
