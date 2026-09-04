"""The economic calendar.

Without one, ``NEWS_CLEAR`` is withheld and the context score is capped at
92/100 -- deliberately, so that a missing data source is visible rather than
worth eight free points. This is what removes that cap.

The rule it encodes is simple and unforgiving: **a high-impact release is not a
market, it is a lottery.** Spreads triple, liquidity disappears, and a stop is
a suggestion. No amount of structure makes that a good moment to enter, so the
calendar is a veto rather than a factor: it does not lower the score, it stops
the scan.

Which currencies matter depends on the instrument, and getting that mapping
wrong in either direction is expensive. Blocking EURUSD on a Reserve Bank of
Australia release wastes good setups; failing to block XAUUSD on US CPI is how
an account discovers slippage.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from elyon.shared_kernel.edcs.numeric import DeterminismError

MINUTE_NS = 60 * 1_000_000_000


class Impact(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass(frozen=True, slots=True)
class Event:
    """One scheduled release."""

    at_ns: int
    currency: str
    impact: Impact
    title: str

    def __str__(self) -> str:
        when = datetime.fromtimestamp(
            self.at_ns / 1_000_000_000, tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M UTC")
        return f"{when} {self.currency} {self.impact.value}: {self.title}"


@dataclass(frozen=True, slots=True)
class BlackoutPolicy:
    """How long around a release to stand down.

    Asymmetric on purpose. The window before is about not being caught holding
    into a print; the window after is longer because the damage is rarely the
    first tick -- it is the reversal ten minutes later when the initial move
    turns out to have been wrong.
    """

    before_minutes: int = 15
    after_minutes: int = 30
    blocks: tuple[Impact, ...] = (Impact.HIGH,)

    def covers(self, event: Event, at_ns: int) -> bool:
        if event.impact not in self.blocks:
            return False
        start = event.at_ns - self.before_minutes * MINUTE_NS
        end = event.at_ns + self.after_minutes * MINUTE_NS
        return start <= at_ns <= end


# Which currencies move which instrument. An index is driven by its own
# economy; a metal priced in dollars is driven by the dollar whatever else
# happens; a cross is driven by both legs.
INSTRUMENT_CURRENCIES: Mapping[str, tuple[str, ...]] = {
    "EURUSD": ("EUR", "USD"),
    "GBPUSD": ("GBP", "USD"),
    "XAUUSD": ("USD",),
    "NAS100": ("USD",),
    "US30": ("USD",),
    # Crypto is not driven by a central bank calendar, but it does react to US
    # macro through the risk channel, so dollar prints still matter.
    "BTCUSD": ("USD",),
    "ETHUSD": ("USD",),
}


def currencies_for(symbol: str) -> tuple[str, ...]:
    """Which releases can move this instrument.

    Refuses to guess. Blocking on the wrong currencies wastes setups; failing
    to block on the right ones is how an account meets slippage.
    """
    try:
        return INSTRUMENT_CURRENCIES[symbol]
    except KeyError:
        known = ", ".join(sorted(INSTRUMENT_CURRENCIES))
        raise DeterminismError(
            f"no currency mapping for {symbol!r}; the calendar cannot know "
            f"which releases move it. Known: {known}"
        ) from None


@dataclass(slots=True)
class ScheduledCalendar:
    """A calendar backed by a list of events.

    Events are sorted once at construction so lookups scan a bounded slice
    rather than the whole file. A year of high-impact releases is a few hundred
    rows, but this is consulted on every bar.
    """

    events: tuple[Event, ...]
    policy: BlackoutPolicy = field(default_factory=BlackoutPolicy)

    def __post_init__(self) -> None:
        self.events = tuple(sorted(self.events, key=lambda e: (e.at_ns, e.currency)))

    def relevant(self, symbol: str, at_ns: int) -> tuple[Event, ...]:
        """Blocking events in force at this moment, for this instrument."""
        wanted = set(currencies_for(symbol))
        return tuple(
            e for e in self.events
            if e.currency in wanted and self.policy.covers(e, at_ns)
        )

    def is_blocked(self, symbol: str, at_ns: int) -> bool:
        return bool(self.relevant(symbol, at_ns))

    def describe(self, symbol: str, at_ns: int) -> str:
        active = self.relevant(symbol, at_ns)
        if not active:
            upcoming = self.next_event(symbol, at_ns)
            if upcoming is None:
                return "no high-impact event scheduled"
            minutes = (upcoming.at_ns - at_ns) // MINUTE_NS
            return (
                f"clear; next is {upcoming.currency} {upcoming.title} "
                f"in {minutes} min"
            )
        first = active[0]
        offset = (first.at_ns - at_ns) // MINUTE_NS
        when = f"in {offset} min" if offset > 0 else f"{-offset} min ago"
        return f"{first.currency} {first.title} {when}"

    def next_event(self, symbol: str, at_ns: int) -> Event | None:
        wanted = set(currencies_for(symbol))
        for event in self.events:
            if event.at_ns > at_ns and event.currency in wanted:
                if event.impact in self.policy.blocks:
                    return event
        return None

    def __len__(self) -> int:
        return len(self.events)

    # -- loading ----------------------------------------------------------

    @classmethod
    def from_rows(
        cls, rows: Iterable[Mapping[str, str]], *, source: str = "calendar",
        policy: BlackoutPolicy | None = None,
    ) -> "ScheduledCalendar":
        events: list[Event] = []
        for number, row in enumerate(rows, start=2):
            missing = {"time", "currency", "impact"} - set(row)
            if missing:
                raise DeterminismError(
                    f"{source} row {number} is missing "
                    f"{', '.join(sorted(missing))}. Expected columns: "
                    f"time,currency,impact[,title]"
                )
            try:
                impact = Impact(row["impact"].strip().upper())
            except ValueError as exc:
                raise DeterminismError(
                    f"{source} row {number}: impact {row['impact']!r} is not "
                    f"one of {', '.join(i.value for i in Impact)}"
                ) from exc
            events.append(Event(
                at_ns=_parse_time(row["time"], source, number),
                currency=row["currency"].strip().upper(),
                impact=impact,
                title=(row.get("title") or "").strip() or "release",
            ))
        return cls(tuple(events), policy or BlackoutPolicy())

    @classmethod
    def load(
        cls, path: str | Path, *, policy: BlackoutPolicy | None = None
    ) -> "ScheduledCalendar":
        """Read a calendar from CSV or JSON, by extension."""
        target = Path(path)
        if target.suffix.lower() == ".json":
            raw = json.loads(target.read_text())
            return cls.from_rows(raw, source=str(target), policy=policy)
        with target.open() as handle:
            return cls.from_rows(
                list(csv.DictReader(handle)), source=str(target), policy=policy
            )


def _parse_time(raw: str, source: str, row: int) -> int:
    """Epoch nanoseconds. ISO timestamps are assumed UTC when naive.

    Assumed, and said so here: a calendar whose times are silently interpreted
    in the machine's local timezone will block the wrong hour on a server in a
    different country, which is exactly the sort of bug that only shows up in
    production.
    """
    value = raw.strip()
    if value.isdigit():
        number = int(value)
        if number < 10_000_000_000:
            return number * 1_000_000_000
        if number < 10_000_000_000_000:
            return number * 1_000_000
        return number
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise DeterminismError(
            f"{source} row {row}: {value!r} is not an epoch or an ISO timestamp"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1_000_000_000)
