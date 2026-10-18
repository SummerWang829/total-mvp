"""Saving and loading a running AI moderator, so a participant can leave and come back (or the server can restart) without the AI losing its place.

The saved bytes are a pickle of our own engine objects, signed with the server's secret key: a blob that was not written by this server (or was
altered in the database) is refused before anything is unpickled.
"""
import hashlib
import hmac
import logging
import pickle

from ..config import settings
from .engine import ModeratorEngine
from .providers import ModeratorChatProvider

log = logging.getLogger(__name__)

_MAC_BYTES = 32


class SnapshotError(Exception):
    """A saved engine cannot be loaded (altered, from an incompatible version of the code, or damaged)."""


def _mac(blob: bytes) -> bytes:
    return hmac.new(settings.secret_key.encode(), blob, hashlib.sha256).digest()


def dump(engine: ModeratorEngine) -> bytes:
    """The engine as signed bytes. Takes the memory lock, so a background fact-extraction cannot change the lists half-way through."""
    with engine.memory._lock:  # noqa: SLF001 - the memory's own lock, the only safe way to read its lists while the background thread writes
        blob = pickle.dumps(engine, protocol=pickle.HIGHEST_PROTOCOL)
    return _mac(blob) + blob


def load(data: bytes, chat: ModeratorChatProvider) -> ModeratorEngine:
    """Bring a saved engine back and connect it to a chat model.

    Raises:
        SnapshotError: the signature is wrong, or the bytes no longer fit this version of the code.
    """
    mac, blob = data[:_MAC_BYTES], data[_MAC_BYTES:]
    if not hmac.compare_digest(mac, _mac(blob)):
        raise SnapshotError("signature mismatch")
    try:
        engine = pickle.loads(blob)  # noqa: S301 - signed by this server just above
        if not isinstance(engine, ModeratorEngine):
            raise SnapshotError("not an engine")
        engine.rebind(chat)
    except SnapshotError:
        raise
    except Exception as e:  # noqa: BLE001 - any unpickling / version problem means "cannot resume", and the caller hands over to a human
        log.exception("saved moderator engine cannot be loaded")
        raise SnapshotError(type(e).__name__) from e
    return engine
