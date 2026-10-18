"""Limits against guessing: failed logins, invitation / interview-link probing, and sign-up floods (switch on with RATE_LIMIT=1).

What is counted is *failures*, not traffic: a whole focus group may sit behind one office address and poll every second, so a plain
requests-per-minute limit would lock out honest participants. A wrong password, a link that does not exist, an invitation token that
does not exist: those are what an attacker produces, and a person produces a handful at most.

- login: 10 failures per 5 minutes per address, and 5 failures per 15 minutes per account (the account is then paused for the rest of
  that window, which also stops a slow guess spread over many addresses).
- /api/public/join/* and /api/public/invite/*: 30 "no such link" answers per minute per address.
- /api/auth/register: 10 attempts per hour per address.

The counters live in memory of this one process (fine for the single-server deployment; a second server needs a shared store).
The address is the real client address: uvicorn runs with proxy headers behind Caddy, which replaces any address the client claims.
"""
import logging
import threading
import time
from collections import deque

from starlette.responses import JSONResponse

from .config import settings

log = logging.getLogger(__name__)

LOGIN_IP = (10, 300)         # failures, seconds
LOGIN_ACCOUNT = (5, 900)
LINK_PROBES = (30, 60)
REGISTER = (10, 3600)
FORGOT_IP = (5, 3600)       # "forgot password" requests per address per hour
FORGOT_ACCOUNT = (3, 3600)  # and per account, so one inbox cannot be flooded
MAX_KEYS = 20000             # counters kept; when over, the quietest are dropped so memory cannot be filled by random keys
MESSAGE = "尝试次数太多，请稍后再试 / Too many attempts, please try again later"

_lock = threading.Lock()
_hits: dict[str, deque[float]] = {}


def reset() -> None:
    """Forget every counter (tests)."""
    with _lock:
        _hits.clear()


def _count(key: str, window: int, now: float) -> int:
    q = _hits.get(key)
    if not q:
        return 0
    while q and q[0] <= now - window:
        q.popleft()
    return len(q)


def blocked(key: str, rule: tuple[int, int], now: float | None = None) -> int:
    """Seconds to wait if this key already has too many failures in the window, else 0."""
    now = now or time.time()
    with _lock:
        if _count(key, rule[1], now) < rule[0]:
            return 0
        return max(1, int(_hits[key][0] + rule[1] - now) + 1)


def record(key: str, window: int, now: float | None = None) -> None:
    """Count one failure (or attempt) for this key."""
    now = now or time.time()
    with _lock:
        _hits.setdefault(key, deque()).append(now)
        _count(key, window, now)
        if len(_hits) > MAX_KEYS:
            for stale in sorted(_hits, key=lambda k: _hits[k][-1] if _hits[k] else 0)[:MAX_KEYS // 10]:
                del _hits[stale]


def clear(key: str) -> None:
    """Forget a key (a successful login clears the account's failures)."""
    with _lock:
        _hits.pop(key, None)


def retry_after_for_login(email: str, ip: str) -> int:
    """Seconds the login must wait (0 = go ahead). Checked before the password is."""
    if not settings.rate_limit:
        return 0
    return max(blocked(f"login-ip:{ip}", LOGIN_IP), blocked(f"login-acct:{email.lower()}", LOGIN_ACCOUNT))


def login_failed(email: str, ip: str) -> None:
    """A wrong email or password."""
    if settings.rate_limit:
        record(f"login-ip:{ip}", LOGIN_IP[1])
        record(f"login-acct:{email.lower()}", LOGIN_ACCOUNT[1])
        log.warning("failed login from %s", ip)


def login_ok(email: str) -> None:
    """A right password: the account's failures are forgotten (the address's are not: one good account must not wash away a guesser)."""
    if settings.rate_limit:
        clear(f"login-acct:{email.lower()}")


def forgot_allowed(email: str, ip: str) -> bool:
    """Count one "forgot password" request; False when this address or this account has asked too often (the caller then quietly does nothing)."""
    if not settings.rate_limit:
        return True
    if blocked(f"forgot-ip:{ip}", FORGOT_IP) or blocked(f"forgot-acct:{email.lower()}", FORGOT_ACCOUNT):
        return False
    record(f"forgot-ip:{ip}", FORGOT_IP[1])
    record(f"forgot-acct:{email.lower()}", FORGOT_ACCOUNT[1])
    return True


def too_many(seconds: int) -> JSONResponse:
    """The 429 answer, with Retry-After."""
    return JSONResponse({"detail": MESSAGE}, status_code=429, headers={"Retry-After": str(seconds)})


async def middleware(request, call_next):  # noqa: ANN001 - Starlette middleware signature
    """Count "no such link" answers and sign-up attempts per address; refuse an address that is probing."""
    if not settings.rate_limit:
        return await call_next(request)
    path, ip = request.url.path, (request.client.host if request.client else "")
    probing = path.startswith(("/api/public/join/", "/api/public/invite/"))
    if probing and (wait := blocked(f"probe:{ip}", LINK_PROBES)):
        return too_many(wait)
    if path == "/api/auth/register" and request.method == "POST":
        if wait := blocked(f"register:{ip}", REGISTER):
            return too_many(wait)
        record(f"register:{ip}", REGISTER[1])
    response = await call_next(request)
    if probing and response.status_code == 404:
        record(f"probe:{ip}", LINK_PROBES[1])
        log.warning("unknown link probed from %s", ip)
    return response
