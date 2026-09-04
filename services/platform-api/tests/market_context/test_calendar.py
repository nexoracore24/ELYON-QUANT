"""Economic calendar tests.

The rule the calendar encodes: a high-impact release is not a market, it is a
lottery. Spreads triple, liquidity disappears, and a stop is a suggestion. So it
vetoes rather than scoring -- it does not lower the number, it stops the scan.

Getting the currency mapping wrong is expensive in both directions, and the
blackout window is asymmetric for a reason, so both get explicit tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from elyon.modules.backtesting.domain import GeneratorConfig, generate
from elyon.modules.market_context.domain import (
    CONTEXT_WEIGHTS,
    BlackoutPolicy,
    ContextConfig,
    ContextFactor,
    ContextVeto,
    Event,
    GateResult,
    Impact,
    NoCalendar,
    ScheduledCalendar,
    currencies_for,
    learn_dna,
    profile_for,
    read_context,
)
from elyon.modules.market_data.domain.atr import AtrProvider
from elyon.shared_kernel.edcs.numeric import DeterminismError, dec

MINUTE = 60 * 1_000_000_000
# 2026-01-15 13:30 UTC -- a plausible US CPI slot.
RELEASE = 1768483800_000_000_000

MARKET = generate(GeneratorConfig(cycles=40))
EUR = learn_dna(MARKET, profile_for("EURUSD"))


def atr_of(series):
    provider = AtrProvider(period=14, output_scale=6)
    for candle in series:
        provider.update(candle)
    return provider.value or dec("0.001")


ATR = atr_of(MARKET)


def calendar(*events, **policy_kwargs) -> ScheduledCalendar:
    rows = events or (
        {"time": str(RELEASE), "currency": "USD", "impact": "HIGH",
         "title": "CPI"},
    )
    return ScheduledCalendar.from_rows(
        rows, policy=BlackoutPolicy(**policy_kwargs) if policy_kwargs else None
    )


class TestBlackoutWindows:
    def test_the_moment_of_release_is_blocked(self):
        assert calendar().is_blocked("EURUSD", RELEASE)

    def test_the_window_before_is_blocked(self):
        # Not being caught holding into a print.
        assert calendar().is_blocked("EURUSD", RELEASE - 10 * MINUTE)

    def test_the_window_after_is_longer(self):
        # The damage is rarely the first tick -- it is the reversal ten minutes
        # later, when the initial move turns out to have been wrong.
        policy = BlackoutPolicy()
        assert policy.after_minutes > policy.before_minutes

        after = calendar()
        assert after.is_blocked("EURUSD", RELEASE + 25 * MINUTE)
        assert not after.is_blocked("EURUSD", RELEASE - 25 * MINUTE)

    def test_well_clear_of_it_is_not_blocked(self):
        assert not calendar().is_blocked("EURUSD", RELEASE + 3 * 60 * MINUTE)

    def test_the_window_is_configurable(self):
        tight = calendar(before_minutes=1, after_minutes=1)
        assert not tight.is_blocked("EURUSD", RELEASE - 5 * MINUTE)
        assert tight.is_blocked("EURUSD", RELEASE)

    def test_low_impact_events_do_not_block_by_default(self):
        quiet = calendar(
            {"time": str(RELEASE), "currency": "USD", "impact": "LOW",
             "title": "housing starts"},
        )
        assert not quiet.is_blocked("EURUSD", RELEASE)

    def test_medium_impact_can_be_made_to_block(self):
        strict = ScheduledCalendar.from_rows(
            [{"time": str(RELEASE), "currency": "USD", "impact": "MEDIUM",
              "title": "PMI"}],
            policy=BlackoutPolicy(blocks=(Impact.HIGH, Impact.MEDIUM)),
        )
        assert strict.is_blocked("EURUSD", RELEASE)


class TestCurrencyMapping:
    def test_a_dollar_release_blocks_a_dollar_pair(self):
        assert calendar().is_blocked("EURUSD", RELEASE)

    def test_a_dollar_release_blocks_gold(self):
        # Priced in dollars, so the dollar moves it whatever else happens.
        assert calendar().is_blocked("XAUUSD", RELEASE)

    def test_an_unrelated_currency_does_not_block(self):
        # Blocking EURUSD on an Australian release wastes good setups.
        aussie = calendar(
            {"time": str(RELEASE), "currency": "AUD", "impact": "HIGH",
             "title": "RBA"},
        )
        assert not aussie.is_blocked("EURUSD", RELEASE)

    def test_both_legs_of_a_cross_matter(self):
        assert set(currencies_for("EURUSD")) == {"EUR", "USD"}

    def test_an_unmapped_instrument_is_refused_not_guessed(self):
        # Failing to block the right currency is how an account meets slippage.
        with pytest.raises(DeterminismError, match="no currency mapping"):
            currencies_for("SOLUSD")

    def test_every_profiled_instrument_is_mapped(self):
        from elyon.modules.market_context.domain import REFERENCE_PROFILES
        for symbol in REFERENCE_PROFILES:
            assert currencies_for(symbol)


class TestReporting:
    def test_an_active_blackout_names_the_event(self):
        detail = calendar().describe("EURUSD", RELEASE + 5 * MINUTE)
        assert "CPI" in detail
        assert "min ago" in detail

    def test_an_upcoming_event_is_announced(self):
        detail = calendar().describe("EURUSD", RELEASE - 90 * MINUTE)
        assert "next is" in detail
        assert "CPI" in detail

    def test_a_clear_calendar_says_so(self):
        assert "no high-impact event" in calendar().describe(
            "EURUSD", RELEASE + 5 * 60 * MINUTE
        )

    def test_events_sort_regardless_of_input_order(self):
        rows = [
            {"time": str(RELEASE + MINUTE), "currency": "USD",
             "impact": "HIGH", "title": "second"},
            {"time": str(RELEASE), "currency": "USD",
             "impact": "HIGH", "title": "first"},
        ]
        events = ScheduledCalendar.from_rows(rows).events
        assert [e.title for e in events] == ["first", "second"]


class TestLoading:
    def test_a_csv_loads(self, tmp_path: Path):
        path = tmp_path / "cal.csv"
        path.write_text(
            "time,currency,impact,title\n"
            "2026-01-15T13:30:00+00:00,USD,HIGH,CPI\n"
        )
        loaded = ScheduledCalendar.load(path)
        assert len(loaded) == 1
        assert loaded.is_blocked("EURUSD", RELEASE)

    def test_json_loads(self, tmp_path: Path):
        path = tmp_path / "cal.json"
        path.write_text(json.dumps([
            {"time": "2026-01-15T13:30:00+00:00", "currency": "USD",
             "impact": "HIGH", "title": "CPI"},
        ]))
        assert ScheduledCalendar.load(path).is_blocked("EURUSD", RELEASE)

    def test_a_naive_timestamp_is_read_as_utc(self):
        # Interpreting it in the machine's local zone would block the wrong
        # hour on a server in another country -- a bug that only shows up in
        # production.
        naive = ScheduledCalendar.from_rows(
            [{"time": "2026-01-15T13:30:00", "currency": "USD",
              "impact": "HIGH", "title": "CPI"}]
        )
        assert naive.events[0].at_ns == RELEASE

    def test_a_missing_column_says_which(self):
        with pytest.raises(DeterminismError, match="impact"):
            ScheduledCalendar.from_rows([{"time": "1", "currency": "USD"}])

    def test_an_unknown_impact_lists_the_valid_ones(self):
        with pytest.raises(DeterminismError, match="HIGH"):
            ScheduledCalendar.from_rows(
                [{"time": "1", "currency": "USD", "impact": "CATASTROPHIC"}]
            )

    def test_an_unparseable_time_names_the_row(self):
        with pytest.raises(DeterminismError, match="row 2"):
            ScheduledCalendar.from_rows(
                [{"time": "whenever", "currency": "USD", "impact": "HIGH"}]
            )


class TestTheContextGateUsesIt:
    def _context(self, at_offset_ns: int, cal):
        # Shift the series so its last bar closes at the requested offset.
        shift = RELEASE + at_offset_ns - MARKET[-1].close_time_ns
        from dataclasses import replace
        from elyon.modules.market_data.domain.series import CandleSeries
        moved = CandleSeries.of([
            replace(c, open_time_ns=c.open_time_ns + shift,
                    close_time_ns=c.close_time_ns + shift)
            for c in MARKET
        ])
        return read_context(moved, ATR, EUR, calendar=cal,
                            config=ContextConfig(min_bars=20))

    def test_a_release_vetoes_the_gate(self):
        reading = self._context(0, calendar())
        assert ContextVeto.NEWS_BLACKOUT in reading.blocking_vetoes
        assert reading.gate is GateResult.FAIL

    def test_the_veto_names_the_event(self):
        assert "CPI" in self._context(0, calendar()).gate_reason

    def test_a_clear_window_awards_the_factor(self):
        reading = self._context(5 * 60 * MINUTE, calendar())
        news = next(
            f for f in reading.factors if f.factor is ContextFactor.NEWS_CLEAR
        )
        assert news.satisfied
        assert news.awarded == CONTEXT_WEIGHTS[ContextFactor.NEWS_CLEAR]

    def test_a_calendar_removes_the_ninety_two_ceiling(self):
        # Without one the score cannot exceed 92/100, so that a missing data
        # source stays visible rather than being worth eight free points.
        without = self._context(5 * 60 * MINUTE, NoCalendar())
        with_cal = self._context(5 * 60 * MINUTE, calendar())
        assert with_cal.score == without.score + 8
