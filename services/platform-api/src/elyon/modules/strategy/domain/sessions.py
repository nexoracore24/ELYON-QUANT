"""Sessions and killzones.

ICT's models are not timeless: Silver Bullet, Judas Swing and Power of 3 all
say "this pattern, *in this window*". Strip the window and you are left with a
pattern that appears everywhere and predicts nothing, so the clock is part of
the strategy rather than a filter bolted on afterwards.

Windows are defined in **New York local time**, which is how they were authored.
That matters: London's killzone sits at 07:00 UTC in winter and 06:00 UTC in
summer, so a system that hardcodes UTC is wrong for roughly half the year.
Converting through a real timezone costs a dependency on the system tzdata, and
that dependency is recorded in :func:`session_config` so a replay can prove
which rules it ran under.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Final
from zoneinfo import ZoneInfo

NY_TZ: Final[str] = "America/New_York"
NANOS_PER_SECOND: Final[int] = 1_000_000_000


class Killzone(str, Enum):
    """The windows ICT models are defined inside, in New York local time."""

    ASIA = "ASIA"                      # 20:00-00:00 -- the accumulation range
    LONDON_OPEN = "LONDON_OPEN"        # 02:00-05:00 -- the first raid
    NY_AM = "NY_AM"                    # 08:30-11:00 -- the main session
    SILVER_BULLET_AM = "SILVER_BULLET_AM"   # 10:00-11:00 -- the hour
    SILVER_BULLET_PM = "SILVER_BULLET_PM"   # 14:00-15:00
    LONDON_CLOSE = "LONDON_CLOSE"      # 11:00-13:00 -- the reversal window
    OUTSIDE = "OUTSIDE"                # no model claims this time


# Half-open [start, end) in New York local minutes-from-midnight. Half-open is
# the same convention candles use: a bar closing exactly at 11:00 belongs to the
# next window, not to both.
_WINDOWS: Final[tuple[tuple[Killzone, int, int], ...]] = (
    (Killzone.SILVER_BULLET_AM, 10 * 60, 11 * 60),
    (Killzone.SILVER_BULLET_PM, 14 * 60, 15 * 60),
    (Killzone.LONDON_OPEN, 2 * 60, 5 * 60),
    (Killzone.NY_AM, 8 * 60 + 30, 11 * 60),
    (Killzone.LONDON_CLOSE, 11 * 60, 13 * 60),
    (Killzone.ASIA, 20 * 60, 24 * 60),
)

# Windows that overlap resolve by this order -- the most specific claim wins.
# Silver Bullet sits inside NY_AM, and a bar at 10:30 is a Silver Bullet bar
# first; ranking it the other way would make the model unreachable.
_PRECEDENCE: Final[tuple[Killzone, ...]] = tuple(kz for kz, _, _ in _WINDOWS)


@dataclass(frozen=True, slots=True)
class SessionClock:
    """Reads wall-clock meaning out of a candle's close time."""

    tz_name: str = NY_TZ

    @property
    def _tz(self) -> ZoneInfo:
        return ZoneInfo(self.tz_name)

    def local_minutes(self, epoch_ns: int) -> int:
        """Minutes past local midnight for an event time."""
        moment = datetime.fromtimestamp(
            epoch_ns / NANOS_PER_SECOND, tz=timezone.utc
        ).astimezone(self._tz)
        return moment.hour * 60 + moment.minute

    def local_date(self, epoch_ns: int) -> str:
        """The local calendar day, which is what "today's range" means."""
        moment = datetime.fromtimestamp(
            epoch_ns / NANOS_PER_SECOND, tz=timezone.utc
        ).astimezone(self._tz)
        return moment.date().isoformat()

    def killzone(self, epoch_ns: int) -> Killzone:
        """Which window this moment falls in, most specific first."""
        minutes = self.local_minutes(epoch_ns)
        for zone in _PRECEDENCE:
            for candidate, start, end in _WINDOWS:
                if candidate is zone and start <= minutes < end:
                    return zone
        return Killzone.OUTSIDE

    def in_killzone(self, epoch_ns: int, *zones: Killzone) -> bool:
        """Whether a moment falls in any of ``zones``.

        NY_AM contains both Silver Bullet hours, so asking "is this NY_AM?"
        must answer yes at 10:30 even though the killzone *label* is the more
        specific one. Containment and labelling are different questions.
        """
        minutes = self.local_minutes(epoch_ns)
        for zone in zones:
            for candidate, start, end in _WINDOWS:
                if candidate is zone and start <= minutes < end:
                    return True
        return False


def session_config(clock: SessionClock) -> dict[str, str]:
    """What a replay needs to reproduce these window decisions."""
    return {"sessionTimezone": clock.tz_name}
