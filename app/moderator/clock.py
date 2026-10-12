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
