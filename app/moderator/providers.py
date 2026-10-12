"""Chat model for the AI moderator, behind one interface so the engine never talks to a vendor library directly.

Switching model = changing MODERATOR_* in .env only (app/config.py); no code changes.
- mock: scripted replies, offline; every test uses it (tests never call a real model).
- openai: any OpenAI-compatible /chat/completions, e.g. local Ollama (http://127.0.0.1:11434/v1) or a cloud provider.
Every reply must be one JSON object; the engine validates it against app/moderator/schemas.py (Sprint 2).
"""
import json
import logging
import time
from collections.abc import Callable
from typing import Protocol
from urllib.parse import urlparse

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..config import Settings, settings
from .compliance import moderator_runs_locally
from .constants import (CHAT_COMPLETIONS_PATH, CHAT_DEFAULT_TEMPERATURE, CHAT_RETRY_ATTEMPTS, CHAT_RETRY_FIRST_WAIT_S,
                        CHAT_RETRY_MAX_WAIT_S, HTTP_SERVER_ERROR_MIN, JSON_RESPONSE_FORMAT, MOCK_DEFAULT_REPLY,
                        MSG_UNKNOWN_PROVIDER, VENDOR_REQUEST_DEFAULTS, ModeratorProviderName)

log = logging.getLogger(__name__)

ChatMessage = dict[str, str]  # {"role": "system" | "user" | "assistant", "content": str}
MockScript = list[dict | Exception] | Callable[[list[ChatMessage]], dict]


class ChatError(Exception):
    """The model gave no usable answer: network or HTTP failure after retries, or output that is not one JSON object.

    The message never contains the model's output or the conversation: both may hold the participant's words.
    """


class _TransientChatError(ChatError):
    """A failure worth retrying: the request never reached the model, timed out, or the server answered 5xx."""


class ModeratorChatProvider(Protocol):
    """What the engine needs from a chat model. Injected, so tests pass a MockModeratorChat."""

    name: str  # recorded in every eval report as the model version

    def complete(self, messages: list[ChatMessage], temperature: float = CHAT_DEFAULT_TEMPERATURE, seed: int | None = None) -> dict:
        """Send the conversation and return the model's reply as one JSON object.

        Args:
            messages: chat history in OpenAI format.
            temperature: sampling temperature.
            seed: fixed seed for repeatable eval runs, when the server supports it.

        Returns:
            The reply parsed into a dict.

        Raises:
            ChatError: no usable answer.
        """
        ...


class MockModeratorChat:
    """Scripted replies for tests and offline development.

    Args:
        script: replies returned in order (the last one repeats; an Exception item is raised instead), or a function of the messages.
    """

    name = ModeratorProviderName.MOCK.value

    def __init__(self, script: MockScript | None = None) -> None:
        self.script: MockScript = script if script is not None else [MOCK_DEFAULT_REPLY]
        self.calls: list[list[ChatMessage]] = []

    def complete(self, messages: list[ChatMessage], temperature: float = CHAT_DEFAULT_TEMPERATURE, seed: int | None = None) -> dict:
        """Return the next scripted reply. Raises whatever Exception the script holds at this position."""
        self.calls.append(messages)
        if callable(self.script):
            return self.script(messages)
        reply = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return reply


class OpenAICompatModeratorChat:
    """Any OpenAI-compatible chat endpoint (local Ollama or a cloud provider).

    Args:
        config: settings to read the MODERATOR_* values from; defaults to the app's live settings.
    """

    def __init__(self, config: Settings = settings) -> None:
        self.config = config
        where = "local" if moderator_runs_locally(config) else (config.moderator_vendor or "cloud")
        self.name = f"{config.moderator_model}@{where}"
        self.last_latency_s = 0.0  # of the last attempt; the eval runner reports p95 latency from it
        self.extra_body = request_extras(config)

    def complete(self, messages: list[ChatMessage], temperature: float = CHAT_DEFAULT_TEMPERATURE, seed: int | None = None) -> dict:
        """See ModeratorChatProvider.complete. Transient failures are retried with exponential backoff.

        Raises:
            ChatError: still failing after CHAT_RETRY_ATTEMPTS, or the reply is not one JSON object (not retried here:
                the engine decides whether to ask again, task doc 4.4).
        """
        body: dict = {**self.extra_body, "model": self.config.moderator_model, "messages": messages, "temperature": temperature,
                      "response_format": JSON_RESPONSE_FORMAT}
        if seed is not None:
            body["seed"] = seed
        content = self._post_with_retry(body)
        return self._parse_json_object(content)

    @retry(retry=retry_if_exception_type(_TransientChatError), stop=stop_after_attempt(CHAT_RETRY_ATTEMPTS),
           wait=wait_exponential(multiplier=CHAT_RETRY_FIRST_WAIT_S, max=CHAT_RETRY_MAX_WAIT_S), reraise=True)
    def _post_with_retry(self, body: dict) -> str:
        """POST once (tenacity repeats it) and return the reply text.

        Raises:
            _TransientChatError: network error, timeout or 5xx (retried).
            ChatError: 4xx (bad key, unknown model) or an unexpected response shape (not retried: asking again cannot help).
        """
        url = self.config.moderator_base_url.rstrip("/") + CHAT_COMPLETIONS_PATH
        started = time.monotonic()
        try:
            response = httpx.post(url, headers={"Authorization": f"Bearer {self.config.moderator_api_key or 'none'}"}, json=body,
                                  timeout=self.config.moderator_timeout_s, trust_env=self.config.moderator_use_system_proxy)
        except httpx.HTTPError as e:
            log.warning("moderator model unreachable at %s (%s)", url, type(e).__name__, exc_info=True)
            raise _TransientChatError(type(e).__name__) from e
        finally:
            self.last_latency_s = time.monotonic() - started
        if response.status_code >= HTTP_SERVER_ERROR_MIN:
            log.warning("moderator model at %s answered HTTP %s", url, response.status_code)
            raise _TransientChatError(f"HTTP {response.status_code}")
        if response.is_error:
            log.error("moderator model at %s refused the request: HTTP %s (check MODERATOR_API_KEY / MODERATOR_MODEL)", url, response.status_code)
            raise ChatError(f"HTTP {response.status_code}")
        try:
            return response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as e:
            log.error("moderator model at %s returned an unexpected response shape", url, exc_info=True)
            raise ChatError("unexpected response shape") from e

    @staticmethod
    def _parse_json_object(content: str | None) -> dict:
        """Parse the reply text into a dict.

        Raises:
            ChatError: empty, not JSON, or JSON that is not an object. The text itself is never logged (participant data).
        """
        try:
            parsed = json.loads(content or "")
        except ValueError as e:
            log.warning("moderator model reply is not JSON (%d characters)", len(content or ""))
            raise ChatError("reply is not JSON") from e
        if not isinstance(parsed, dict):
            log.warning("moderator model reply is JSON but not an object (%s)", type(parsed).__name__)
            raise ChatError("reply is not a JSON object")
        return parsed


def request_extras(config: Settings) -> dict:
    """Extra request fields: MODERATOR_EXTRA_BODY if set, otherwise the vendor's defaults (VENDOR_REQUEST_DEFAULTS).

    Args:
        config: settings to read.

    Returns:
        Fields merged into every request (empty for vendors that need none).

    Raises:
        ValueError: MODERATOR_EXTRA_BODY is not a JSON object (fails at start-up, not on the first interview).
    """
    if config.moderator_extra_body.strip():
        try:
            extra = json.loads(config.moderator_extra_body)
        except ValueError as e:
            raise ValueError("MODERATOR_EXTRA_BODY must be a JSON object") from e
        if not isinstance(extra, dict):
            raise ValueError("MODERATOR_EXTRA_BODY must be a JSON object")
        return extra
    host = urlparse(config.moderator_base_url).hostname or ""
    return dict(VENDOR_REQUEST_DEFAULTS.get(host, {}))


def get_moderator_chat(config: Settings = settings) -> ModeratorChatProvider:
    """Build the chat model chosen in settings.

    Args:
        config: settings to read; defaults to the app's live settings.

    Returns:
        A provider implementing ModeratorChatProvider.

    Raises:
        NotImplementedError: MODERATOR_PROVIDER is neither mock nor openai.
    """
    if config.moderator_provider == ModeratorProviderName.MOCK.value:
        return MockModeratorChat()
    if config.moderator_provider == ModeratorProviderName.OPENAI.value:
        return OpenAICompatModeratorChat(config)
    raise NotImplementedError(MSG_UNKNOWN_PROVIDER.format(value=config.moderator_provider))
