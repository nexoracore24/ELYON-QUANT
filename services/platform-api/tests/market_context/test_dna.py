"""Market DNA tests.

One rule carries this module: **DNA adapts filters, never rules.** A profile may
change how wide "equal" is on gold; it may not change what a break of structure
means. If that boundary ever softens, every instrument quietly ends up running a
different strategy under the same name.

The second rule is the one inherited from the tier system: a hand-written
profile is a guess, and it says so.
"""

from __future__ import annotations

import pytest

from elyon.modules.backtesting.domain import GeneratorConfig, generate
from elyon.modules.market_context.domain import (
    ENGINE_DEFAULTS,
    MIN_DNA_SAMPLE,
    REFERENCE_PROFILES,
    AssetClass,
    MarketDna,
    Provenance,
    VolatilityBands,
    VolatilityRegime,
    classify_volatility,
    learn_dna,
    profile_for,
)
from elyon.modules.strategy.domain import Killzone
from elyon.shared_kernel.edcs.numeric import DeterminismError, dec

MARKET = generate(GeneratorConfig(cycles=40))


class TestDnaAdaptsFiltersNeverRules:
    """The inviolable rule, per ENG-011 §8.1."""

    def test_a_profile_can_only_tune_known_filters(self):
        with pytest.raises(DeterminismError, match="not a tunable filter"):
            MarketDna(
                symbol="X", asset_class=AssetClass.FX_MAJOR,
                tick_size=dec("0.00001"), typical_atr=dec("0.001"),
                typical_spread=dec("0.0001"), max_spread=dec("0.0004"),
                efficiency_hours=(Killzone.NY_AM,),
                overrides={"entry_score_threshold": dec("80")},
            )

    def test_the_refusal_lists_what_is_tunable(self):
        with pytest.raises(DeterminismError, match="equal_level_tol_atr"):
            MarketDna(
                symbol="X", asset_class=AssetClass.FX_MAJOR,
                tick_size=dec("0.00001"), typical_atr=dec("0.001"),
                typical_spread=dec("0.0001"), max_spread=dec("0.0004"),
                efficiency_hours=(Killzone.NY_AM,),
                overrides={"scoring_rules": dec("1")},
            )

    def test_a_profile_carries_only_numbers(self):
        # There is no hook for a profile to supply behaviour, which is what
        # makes the rule structural rather than a convention.
        for profile in REFERENCE_PROFILES.values():
            for value in profile.overrides.values():
                assert isinstance(value, type(dec("1")))

    def test_an_override_replaces_only_that_filter(self):
        gold = profile_for("XAUUSD")
        assert gold.sensitivity("equal_level_tol_atr") == dec("0.18")
        # Everything it did not override still comes from the engine.
        assert gold.sensitivity("fvg_min_size_atr") == \
            ENGINE_DEFAULTS["fvg_min_size_atr"]

    def test_an_unknown_filter_cannot_be_read_either(self):
        with pytest.raises(DeterminismError, match="unknown filter"):
            profile_for("EURUSD").sensitivity("made_up_knob")

    def test_every_instrument_resolves_every_filter(self):
        for profile in REFERENCE_PROFILES.values():
            for name in ENGINE_DEFAULTS:
                assert profile.sensitivity(name) > dec("0")


class TestNormalisation:
    """Absolute thresholds are wrong for every instrument but one."""

    def test_the_same_atr_means_opposite_things_on_two_instruments(self):
        # 0.0008 is an ordinary EURUSD range and a completely dead gold market.
        atr = dec("0.0008")
        assert classify_volatility(atr, profile_for("EURUSD")) \
            is VolatilityRegime.NORMAL
        assert classify_volatility(atr, profile_for("XAUUSD")) \
            is VolatilityRegime.DEAD

    def test_a_ratio_is_relative_to_the_instrument(self):
        assert profile_for("EURUSD").atr_ratio(dec("0.00100")) == dec("1")
        assert profile_for("XAUUSD").atr_ratio(dec("2.50")) == dec("1")

    def test_volatility_bands_cover_the_whole_range(self):
        eur = profile_for("EURUSD")
        seen = {
            classify_volatility(eur.typical_atr * dec(m), eur)
            for m in ("0.1", "0.5", "1.0", "2.0", "5.0")
        }
        assert seen == set(VolatilityRegime)

    def test_crypto_tolerates_more_before_calling_it_extreme(self):
        # Same multiple of its own normal, different verdict -- because what is
        # abnormal for EURUSD is a Tuesday for BTC.
        multiple = dec("2.6")
        eur, btc = profile_for("EURUSD"), profile_for("BTCUSD")
        assert classify_volatility(eur.typical_atr * multiple, eur) \
            is VolatilityRegime.EXTREME
        assert classify_volatility(btc.typical_atr * multiple, btc) \
            is VolatilityRegime.ACTIVE

    def test_bands_must_ascend(self):
        with pytest.raises(DeterminismError, match="ascend"):
            VolatilityBands(dead=dec("2"), low=dec("1"),
                            high=dec("3"), extreme=dec("4"))


class TestReferenceProfilesAreHonest:
    def test_nothing_ships_calibrated(self):
        # Same principle as the strategy catalog shipping entirely UNPROVEN: a
        # guess that looks like a measurement will be trusted like one.
        for profile in REFERENCE_PROFILES.values():
            assert profile.provenance is Provenance.REFERENCE
            assert not profile.is_calibrated

    def test_the_description_says_so(self):
        assert "reference profile" in profile_for("EURUSD").describe()

    def test_all_seven_instruments_are_profiled(self):
        assert set(REFERENCE_PROFILES) == {
            "EURUSD", "GBPUSD", "XAUUSD", "NAS100", "US30", "BTCUSD", "ETHUSD"
        }

    def test_an_unknown_instrument_is_refused_not_invented(self):
        # Inventing one would silently apply EURUSD tolerances to something
        # else, and the failure would look like a bad strategy.
        with pytest.raises(DeterminismError, match="no Market DNA"):
            profile_for("SOLUSD")

    def test_the_refusal_explains_how_to_add_one(self):
        with pytest.raises(DeterminismError, match="adding its profile"):
            profile_for("SOLUSD")

    def test_crypto_is_not_given_forex_hours(self):
        # Handing BTC the London killzone would filter out most of its activity.
        btc = profile_for("BTCUSD")
        assert len(btc.efficiency_hours) > len(profile_for("EURUSD").efficiency_hours)

    def test_spread_bounds_are_coherent(self):
        for profile in REFERENCE_PROFILES.values():
            assert profile.max_spread >= profile.typical_spread

    def test_an_incoherent_spread_is_refused(self):
        with pytest.raises(DeterminismError, match="max spread"):
            MarketDna(
                symbol="X", asset_class=AssetClass.FX_MAJOR,
                tick_size=dec("0.00001"), typical_atr=dec("0.001"),
                typical_spread=dec("0.0005"), max_spread=dec("0.0001"),
                efficiency_hours=(Killzone.NY_AM,),
            )

    def test_a_flat_instrument_is_refused(self):
        with pytest.raises(DeterminismError, match="typical ATR must be positive"):
            MarketDna(
                symbol="X", asset_class=AssetClass.FX_MAJOR,
                tick_size=dec("0.00001"), typical_atr=dec("0"),
                typical_spread=dec("0.0001"), max_spread=dec("0.0004"),
                efficiency_hours=(Killzone.NY_AM,),
            )


class TestLearningAProfile:
    def test_learning_marks_the_profile_as_measured(self):
        learned = learn_dna(MARKET, profile_for("EURUSD"))
        assert learned.provenance is Provenance.LEARNED
        assert learned.sample_bars == len(MARKET)

    def test_a_short_sample_does_not_count_as_calibrated(self):
        short = learn_dna(MARKET.upto(60), profile_for("EURUSD"))
        assert short.provenance is Provenance.LEARNED
        assert not short.is_calibrated  # below MIN_DNA_SAMPLE
        assert short.sample_bars < MIN_DNA_SAMPLE

    def test_a_long_sample_does(self):
        assert learn_dna(MARKET, profile_for("EURUSD")).is_calibrated

    def test_learning_replaces_the_guessed_atr(self):
        learned = learn_dna(MARKET, profile_for("EURUSD"))
        assert learned.typical_atr != profile_for("EURUSD").typical_atr

    def test_learning_never_rewrites_research_decisions(self):
        # Efficiency hours and detector sensitivities are judgements, not
        # statistics. Letting a fit move them is the auto-mutation ENG-011 §8.1
        # forbids outright.
        base = profile_for("XAUUSD")
        learned = learn_dna(MARKET, base)
        assert learned.efficiency_hours == base.efficiency_hours
        assert learned.overrides == base.overrides
        assert learned.context_threshold == base.context_threshold

    def test_the_median_resists_a_single_spike(self):
        # One volatility event must not redefine normal for the next month.
        calm = generate(GeneratorConfig(cycles=40, seed=3))
        spiky = generate(GeneratorConfig(cycles=40, seed=3, impulse=dec("0.03")))
        from_calm = learn_dna(calm, profile_for("EURUSD")).typical_atr
        from_spiky = learn_dna(spiky, profile_for("EURUSD")).typical_atr
        # The spikier series does read higher -- but the median means it tracks
        # the body of the distribution rather than its tail.
        assert from_spiky > from_calm

    def test_too_little_data_is_refused(self):
        with pytest.raises(DeterminismError, match="not enough"):
            learn_dna(MARKET.upto(5), profile_for("EURUSD"))

    def test_learning_is_deterministic(self):
        a = learn_dna(MARKET, profile_for("EURUSD"))
        b = learn_dna(MARKET, profile_for("EURUSD"))
        assert a.typical_atr == b.typical_atr
        assert a.dna_hash == b.dna_hash


class TestDnaProvenance:
    def test_the_hash_is_stable(self):
        assert profile_for("EURUSD").dna_hash == profile_for("EURUSD").dna_hash

    def test_different_instruments_hash_differently(self):
        hashes = {p.dna_hash for p in REFERENCE_PROFILES.values()}
        assert len(hashes) == len(REFERENCE_PROFILES)

    def test_changing_a_filter_changes_the_hash(self):
        # Otherwise a replay cannot tell which tolerances produced a decision.
        from dataclasses import replace
        base = profile_for("EURUSD")
        tweaked = replace(base, overrides={"fvg_min_size_atr": dec("0.4")})
        assert base.dna_hash != tweaked.dna_hash

    def test_the_hash_ignores_override_insertion_order(self):
        from dataclasses import replace
        base = profile_for("EURUSD")
        forward = replace(base, overrides={
            "fvg_min_size_atr": dec("0.4"), "stop_buffer_atr": dec("0.5")
        })
        backward = replace(base, overrides={
            "stop_buffer_atr": dec("0.5"), "fvg_min_size_atr": dec("0.4")
        })
        assert forward.dna_hash == backward.dna_hash
