"""Time source for the state machine. Injected, so tests and 60-minute simulations do not wait for real time."""
import time
from typing import Protocol


class Clock(Protocol):
    """Anything that tells the current time in seconds (monotonic: never jumps back)."""

    def now(self) -> float:
        """Seconds since an arbitrary fixed point."""
        ...


class SystemClock:
    """Real time, for live interviews."""

    def now(self) -> float:
        """time.monotonic(): unaffected by the computer's clock being changed during an interview."""
        return time.monotonic()


class ManualClock:
    """Time that moves only when told to: unit tests, and simulated interviews (Sprint 3) that add each turn's speaking time.

    Args:
        start: the starting time in seconds.
    """

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def now(self) -> float:
        """The current simulated time."""
        return self._now

    def advance(self, seconds: float) -> None:
        """Move time forward.

        Raises:
            ValueError: negative seconds (time never runs backwards).
        """
        if seconds < 0:
            raise ValueError("time cannot go backwards")
        self._now += seconds


class ActiveClock:
    """Interview time that only runs while the participant is actually there, for studies done at the participant's own pace.

    Each stretch between two readings counts at most `idle_cap_s`: a participant who answers after a coffee break (or leaves for hours and
    comes back) loses no interview time for the gap beyond that, so the time budget is not used up while nobody is answering. The clock can be
    saved with the engine and carried on after a restart (`resume`).

    Args:
        idle_cap_s: the longest gap that counts as interview time.
    """

    def __init__(self, idle_cap_s: float = 180.0) -> None:
        self.idle_cap_s = idle_cap_s
        self._total = 0.0
        self._last = time.time()

    def now(self) -> float:
        """Interview seconds so far (never goes back)."""
        wall = time.time()
        self._total += min(max(0.0, wall - self._last), self.idle_cap_s)
        self._last = wall
        return self._total

    def resume(self) -> None:
        """Called when a saved clock is loaded again: the time away does not count."""
        self._last = time.time()
