"""The interview times offered to a candidate in the last question of the recruiting questionnaire: the next three days in Singapore time, four slots a day.

The question is added by the system after the questions the research team wrote, so it never counts among them and the AI never edits it. What the
candidate picks is kept as ``Participant.availability`` (UTC, earliest first) and fills in the invitation: the first time is the one to book, the others
are backups.
"""
from datetime import date, datetime, time, timedelta

from .models import utcnow

SGT = timedelta(hours=8)
HOURS = (10, 14, 16, 19)  # local hours of the four slots
DAYS = 3
LEAD_HOURS = 2  # a slot that starts sooner than this is not offered
MAX_PICKS = DAYS * len(HOURS)


def _utc(day: date, hour: int) -> datetime:
    return datetime.combine(day, time(hour, 0)) - SGT


def offered(now: datetime | None = None) -> list[dict]:
    """The days and times to show: [{"date": "2026-10-27", "weekday": 1, "times": [{"at": "<UTC ISO>", "hhmm": "16:00"}]}], past or too-soon slots left out."""
    now = now or utcnow()
    today = (now + SGT).date()
    out = []
    for d in range(DAYS):
        day = today + timedelta(days=d)
        times = [{"at": _utc(day, h).isoformat(), "hhmm": f"{h:02d}:00"} for h in HOURS if _utc(day, h) >= now + timedelta(hours=LEAD_HOURS)]
        if times:
            out.append({"date": day.isoformat(), "weekday": day.weekday(), "times": times})
    return out


def parse(picks: list[str], now: datetime | None = None) -> list[datetime]:
    """The picked times that are real slots of the grid (any day from today to the day after the last offered one), earliest first, without repeats.

    Anything else in the list (a made-up time, a time long past) is ignored rather than refused: the candidate cannot fix it, and the team can set any time by hand.
    """
    now = now or utcnow()
    today = (now + SGT).date()
    grid = {_utc(today + timedelta(days=d), h) for d in range(DAYS + 1) for h in HOURS}
    seen = set()
    for raw in picks[:MAX_PICKS]:
        try:
            t = datetime.fromisoformat(str(raw).replace("Z", "")).replace(tzinfo=None)
        except ValueError:
            continue
        if t in grid and t >= now - timedelta(hours=1):
            seen.add(t)
    return sorted(seen)
