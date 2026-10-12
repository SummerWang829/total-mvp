"""Can AI-moderated interviews run with the current settings? (Task doc part 3: a cloud model is another transfer of
the participant's words, so the consent text must name who receives them.)"""
from urllib.parse import urlparse

from ..config import Settings, settings
from .constants import LOCAL_MODEL_HOSTS, MSG_CLOUD_VENDOR_MISSING, ModeratorProviderName


def moderator_runs_locally(config: Settings = settings) -> bool:
    """Whether moderator chats stay on this computer.

    Args:
        config: settings to read; defaults to the app's live settings (tests pass their own).

    Returns:
        True for the mock provider or a model server on localhost; False for anything else (a cloud provider).
    """
    if config.moderator_provider == ModeratorProviderName.MOCK.value:
        return True
    host = urlparse(config.moderator_base_url).hostname or ""
    return host in LOCAL_MODEL_HOSTS


def ai_moderation_blocker(config: Settings = settings) -> str | None:
    """Why AI-moderated interviews cannot run now, in plain Chinese for the research team.

    Args:
        config: settings to read; defaults to the app's live settings.

    Returns:
        None when AI interviews may run; otherwise the reason (a cloud model whose vendor is not named in MODERATOR_VENDOR).
    """
    if not moderator_runs_locally(config) and not config.moderator_vendor.strip():
        return MSG_CLOUD_VENDOR_MISSING
    return None
