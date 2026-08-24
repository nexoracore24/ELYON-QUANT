"""Catalog, tiers, activation and sessions.

The property under test in most of this file is one claim: **a probability tier
is earned, never declared**. A system that lets an author's opinion size a
position has replaced research with confidence, and the tests below are what
stop that from silently becoming true.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from elyon.modules.strategy.domain import (
    CATALOG,
    MIN_CALIBRATION_SAMPLE,
    Activation,
    Calibration,
    Killzone,
    ProbabilityTier,
    SessionClock,
    StrategyFamily,
    StrategyId,
    StrategyRegistry,
    UnavailableStrategyError,
    by_family,
    calibrated,
    profile,
    registry_from_names,
    session_config,
)
from elyon.shared_kernel.edcs.numeric import DeterminismError, dec


def at(iso: str) -> int:
    """Nanoseconds since epoch for an ISO instant."""
    return int(datetime.fromisoformat(iso).timestamp() * 1_000_000_000)


class TestTiersAreEarnedNotDeclared:
    """The single most important rule in the module."""

    def test_a_strategy_with_no_calibration_is_unproven(self):
        for strategy, prof in CATALOG.items():
            assert prof.effective_tier is ProbabilityTier.UNPROVEN, strategy

    def test_nothing_ships_pre_blessed(self):
        # Every declared tier in the catalog is a hypothesis. If one of them
        # ever arrives with calibration attached at import time, someone has
        # blessed a strategy without running it.
        assert all(p.calibration is None for p in CATALOG.values())

    def test_the_declared_tier_never_reaches_the_engine(self):
        declared_high = [
            p for p in CATALOG.values()
            if p.declared_tier is ProbabilityTier.HIGH
        ]
        assert declared_high  # the catalog does contain confident claims
        for prof in declared_high:
            assert prof.effective_tier is ProbabilityTier.UNPROVEN

    def test_the_gap_between_belief_and_evidence_is_reported(self):
        prof = profile(StrategyId.SIX_PILLARS)
        assert prof.tier_drift == "declared HIGH, never calibrated"

    def test_evidence_moves_the_tier(self):
        proven = calibrated(
            StrategyId.SIX_PILLARS,
            Calibration(180, 92, dec("0.42"), dataset="eurusd-2024"),
        )
        assert proven.effective_tier is ProbabilityTier.HIGH
        assert proven.tier_drift is None  # belief and evidence now agree

    def test_evidence_can_also_demote(self):
        # The most useful thing a research log can tell you.
        measured = calibrated(
            StrategyId.ICT_UNICORN,
            Calibration(120, 40, dec("0.02"), dataset="eurusd-2024"),
        )
        assert measured.declared_tier is ProbabilityTier.HIGH
        assert measured.effective_tier is ProbabilityTier.LOW
        assert measured.tier_drift == "declared HIGH, measured LOW"


class TestCalibration:
    def test_a_small_sample_is_unproven_however_good_it_looks(self):
        # Twelve trades is a story, not evidence.
        golden = Calibration(12, 11, dec("2.5"))
        assert golden.win_rate > dec("0.9")
        assert golden.expectancy_r > dec("2")
        assert golden.tier is ProbabilityTier.UNPROVEN

    def test_the_sample_floor_is_where_it_says_it_is(self):
        just_under = Calibration(MIN_CALIBRATION_SAMPLE - 1, 20, dec("0.5"))
        just_over = Calibration(MIN_CALIBRATION_SAMPLE, 20, dec("0.5"))
        assert just_under.tier is ProbabilityTier.UNPROVEN
        assert just_over.tier is ProbabilityTier.HIGH

    def test_a_high_win_rate_with_negative_expectancy_is_low(self):
        # Picking up pennies in front of a steamroller. Winning often and
        # making money are different claims, and only the second one pays.
        steamroller = Calibration(200, 180, dec("-0.30"))
        assert steamroller.win_rate == dec("0.9")
        assert steamroller.tier is ProbabilityTier.LOW

    def test_break_even_is_not_an_edge(self):
        assert Calibration(200, 100, dec("0")).tier is ProbabilityTier.LOW

    def test_a_low_win_rate_with_strong_expectancy_is_high(self):
        # The shape of most trend-following edges: wrong often, paid well.
        runner = Calibration(150, 45, dec("0.60"))
        assert runner.win_rate == dec("0.3")
        assert runner.tier is ProbabilityTier.HIGH

    def test_the_tiers_are_ordered_by_expectancy(self):
        tiers = [
            Calibration(100, 50, dec(e)).tier
            for e in ("0.50", "0.20", "0.05", "-0.10")
        ]
        assert tiers == [
            ProbabilityTier.HIGH, ProbabilityTier.MEDIUM,
            ProbabilityTier.LOW, ProbabilityTier.LOW,
        ]

    def test_more_wins_than_trades_is_refused(self):
        with pytest.raises(ValueError, match="impossible"):
            Calibration(10, 11, dec("0.5"))

    def test_negative_counts_are_refused(self):
        with pytest.raises(ValueError, match="negative"):
            Calibration(-1, 0, dec("0.5"))

    def test_an_empty_record_does_not_divide_by_zero(self):
        assert Calibration(0, 0, dec("0")).win_rate == dec("0")


class TestTierPrivileges:
    def test_only_high_may_act_alone(self):
        assert ProbabilityTier.HIGH.corroboration_required == 0
        assert ProbabilityTier.MEDIUM.corroboration_required >= 1
        assert ProbabilityTier.LOW.corroboration_required >= 2

    def test_unproven_can_never_act_alone(self):
        # 99 is a sentinel for "never" -- the number just has to exceed the
        # number of families that could possibly agree.
        assert ProbabilityTier.UNPROVEN.corroboration_required > len(StrategyFamily)

    def test_weight_falls_with_trust(self):
        weights = [t.weight for t in ProbabilityTier]
        assert weights == sorted(weights, reverse=True)

    def test_every_tier_has_a_distinct_badge(self):
        badges = [t.badge for t in ProbabilityTier]
        assert len(set(badges)) == len(badges)


class TestCatalogShape:
    def test_every_strategy_has_a_profile(self):
        assert set(CATALOG) == set(StrategyId)

    def test_every_profile_explains_its_thesis(self):
        for prof in CATALOG.values():
            assert len(prof.thesis) > 40, prof.id
            assert prof.title

    def test_families_group_the_catalog(self):
        covered = set()
        for family in StrategyFamily:
            covered |= {p.id for p in by_family(family)}
        assert covered == set(StrategyId)

    def test_more_than_one_family_is_represented(self):
        # Confluence is meaningless if every strategy reads the same thing.
        families = {p.family for p in CATALOG.values()}
        assert len(families) >= 5

    def test_smt_is_honest_about_needing_data_we_lack(self):
        prof = profile(StrategyId.SMT_DIVERGENCE)
        assert prof.requires_correlated_feed
        assert not prof.available


class TestRegistry:
    def test_the_default_runs_one_model_and_watches_the_rest(self):
        # Shipping thirteen strategies all switched on would be a trade-count
        # maximiser wearing a catalog.
        reg = StrategyRegistry.default()
        assert reg.live_ids == (StrategyId.SIX_PILLARS,)
        assert len(reg.shadow_ids) == len(StrategyId) - 1

    def test_everything_is_evaluated_even_in_shadow(self):
        reg = StrategyRegistry.default()
        assert len(reg.evaluated_ids) == len(StrategyId)

    def test_off_strategies_are_not_evaluated(self):
        reg = StrategyRegistry.all_off()
        assert reg.evaluated_ids == ()
        assert len(reg) == 0

    def test_enabling_returns_a_new_registry(self):
        # Immutability is what pins the activation state to the decision it
        # produced, instead of it being a global that has already moved on.
        base = StrategyRegistry.all_off()
        changed = base.live(StrategyId.ICT_2022_MODEL)
        assert base.live_ids == ()
        assert changed.live_ids == (StrategyId.ICT_2022_MODEL,)

    def test_only_replaces_the_whole_selection(self):
        reg = StrategyRegistry.default().only(StrategyId.ICT_OTE)
        assert reg.live_ids == (StrategyId.ICT_OTE,)
        assert reg.shadow_ids == ()

    def test_a_family_can_be_switched_on_together(self):
        reg = StrategyRegistry.all_off().live_family(StrategyFamily.SESSION_TIMING)
        assert set(reg.live_ids) == {
            p.id for p in by_family(StrategyFamily.SESSION_TIMING)
        }

    def test_evaluation_order_does_not_depend_on_how_it_was_built(self):
        a = StrategyRegistry.all_off().live(
            StrategyId.ICT_OTE, StrategyId.SIX_PILLARS
        )
        b = StrategyRegistry.all_off().live(
            StrategyId.SIX_PILLARS, StrategyId.ICT_OTE
        )
        assert a.live_ids == b.live_ids

    def test_a_strategy_needing_data_we_lack_cannot_go_live(self):
        with pytest.raises(UnavailableStrategyError, match="correlated feed"):
            StrategyRegistry.all_off().live(StrategyId.SMT_DIVERGENCE)

    def test_but_it_may_still_be_watched(self):
        # Shadow is how it collects evidence for the day the feed arrives.
        reg = StrategyRegistry.all_off().shadow(StrategyId.SMT_DIVERGENCE)
        assert reg.mode(StrategyId.SMT_DIVERGENCE) is Activation.SHADOW

    def test_live_unproven_strategies_are_reportable(self):
        reg = StrategyRegistry.default()
        assert StrategyId.SIX_PILLARS in reg.unproven_live()

    def test_the_summary_lists_every_strategy(self):
        lines = StrategyRegistry.default().summary().splitlines()
        assert len(lines) == len(StrategyId)


class TestRegistryProvenance:
    def test_the_hash_pins_the_activation_state(self):
        a = StrategyRegistry.default().config_hash
        b = StrategyRegistry.default().config_hash
        assert a == b

    def test_turning_a_strategy_on_changes_the_hash(self):
        # Otherwise a replay cannot tell which strategies were switched on when
        # the trade was taken.
        base = StrategyRegistry.default()
        changed = base.live(StrategyId.ICT_TURTLE_SOUP)
        assert base.config_hash != changed.config_hash

    def test_shadow_and_live_hash_differently(self):
        shadowed = StrategyRegistry.all_off().shadow(StrategyId.ICT_OTE)
        living = StrategyRegistry.all_off().live(StrategyId.ICT_OTE)
        assert shadowed.config_hash != living.config_hash

    def test_the_hash_ignores_insertion_order(self):
        forward = StrategyRegistry(
            {StrategyId.SIX_PILLARS: Activation.LIVE,
             StrategyId.ICT_OTE: Activation.SHADOW}
        )
        backward = StrategyRegistry(
            {StrategyId.ICT_OTE: Activation.SHADOW,
             StrategyId.SIX_PILLARS: Activation.LIVE}
        )
        assert forward.config_hash == backward.config_hash


class TestRegistryFromConfig:
    def test_named_strategies_go_live(self):
        reg = registry_from_names(["SIX_PILLARS", "ICT_TURTLE_SOUP"])
        assert set(reg.live_ids) == {
            StrategyId.SIX_PILLARS, StrategyId.ICT_TURTLE_SOUP
        }

    def test_a_typo_fails_loudly(self):
        # A typo that silently disables a strategy shows up as a quiet drop in
        # performance months later.
        with pytest.raises(DeterminismError, match="unknown strategy"):
            registry_from_names(["SIX_PILARS"])

    def test_the_error_lists_what_was_valid(self):
        with pytest.raises(DeterminismError, match="ICT_2022_MODEL"):
            registry_from_names(["nonsense"])


class TestKillzones:
    def setup_method(self):
        self.clock = SessionClock()

    def test_the_silver_bullet_hour_is_labelled_as_such(self):
        assert self.clock.killzone(at("2026-01-15T15:30:00+00:00")) \
            is Killzone.SILVER_BULLET_AM

    def test_daylight_saving_is_handled(self):
        # 10:30 New York is 15:30 UTC in January and 14:30 UTC in July. A
        # system that hardcodes UTC windows is wrong for half the year.
        winter = self.clock.killzone(at("2026-01-15T15:30:00+00:00"))
        summer = self.clock.killzone(at("2026-07-15T14:30:00+00:00"))
        assert winter is summer is Killzone.SILVER_BULLET_AM

    def test_the_same_utc_hour_means_different_things_across_the_year(self):
        winter = self.clock.local_minutes(at("2026-01-15T15:30:00+00:00"))
        summer = self.clock.local_minutes(at("2026-07-15T15:30:00+00:00"))
        assert winter != summer  # 10:30 vs 11:30 local

    def test_a_specific_window_outranks_the_one_containing_it(self):
        # Silver Bullet sits inside NY_AM. Labelling it NY_AM would make the
        # model unreachable.
        assert self.clock.killzone(at("2026-01-15T15:30:00+00:00")) \
            is Killzone.SILVER_BULLET_AM

    def test_containment_and_labelling_are_different_questions(self):
        moment = at("2026-01-15T15:30:00+00:00")
        assert self.clock.killzone(moment) is Killzone.SILVER_BULLET_AM
        assert self.clock.in_killzone(moment, Killzone.NY_AM)

    def test_quiet_hours_belong_to_no_model(self):
        assert self.clock.killzone(at("2026-01-15T22:00:00+00:00")) \
            is Killzone.OUTSIDE

    def test_windows_are_half_open(self):
        # A bar closing exactly at 11:00 belongs to the next window, not both.
        eleven = at("2026-01-15T16:00:00+00:00")   # 11:00 EST
        assert not self.clock.in_killzone(eleven, Killzone.SILVER_BULLET_AM)
        assert self.clock.in_killzone(eleven, Killzone.LONDON_CLOSE)

    def test_the_local_date_is_the_local_date(self):
        # 02:00 UTC is still the previous evening in New York, and session
        # models that get this wrong shift by several hours.
        assert self.clock.local_date(at("2026-01-16T02:00:00+00:00")) \
            == "2026-01-15"

    def test_the_timezone_travels_with_the_decision(self):
        assert session_config(self.clock) == {
            "sessionTimezone": "America/New_York"
        }
