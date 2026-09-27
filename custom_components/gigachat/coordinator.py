"""Coordinator and client factory for the GigaChat integration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
import ssl
import time
from typing import Any, override

import certifi
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_VERIFY_SSL, __version__ as HA_VERSION
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.loader import async_get_integration

from .api import GigaChatAuthError, GigaChatClient, GigaChatError, GigaChatModel
from .const import (
    BALANCE_REFRESH_COOLDOWN,
    CONF_AUTH_URL,
    CONF_BASE_URL,
    CONF_CREDENTIALS,
    CONF_SCOPE,
    DEFAULT_AUTH_URL,
    DEFAULT_BASE_URL,
    DOMAIN,
    FALLBACK_MODEL,
    LOGGER,
    MAX_CONCURRENT_REQUESTS,
    MODELS_REFRESH_INTERVAL,
    PREFERRED_MODELS,
    UPDATE_INTERVAL,
    UPDATE_INTERVAL_ERROR,
)

CA_BUNDLE = Path(__file__).parent / "certs" / "russian_trusted_root_ca.pem"

type GigaChatConfigEntry = ConfigEntry[GigaChatCoordinator]


def create_ssl_context(verify: bool) -> ssl.SSLContext | bool:
    """Create an SSL context that also trusts the Russian Trusted Root CA.

    The GigaChat endpoints use certificates issued by the Russian Ministry of
    Digital Development, which is not part of the public trust stores.
    This does blocking I/O, run it in the executor.
    """
    if not verify:
        return False
    context = ssl.create_default_context(cafile=certifi.where())
    context.load_verify_locations(cafile=CA_BUNDLE)
    return context


async def async_create_client(
    hass: HomeAssistant, data: Mapping[str, Any]
) -> GigaChatClient:
    """Create an API client from config entry data."""
    ssl_context = await hass.async_add_executor_job(
        create_ssl_context, data.get(CONF_VERIFY_SSL, True)
    )
    integration = await async_get_integration(hass, DOMAIN)
    scope = data[CONF_SCOPE]
    return GigaChatClient(
        async_get_clientsession(hass),
        data[CONF_CREDENTIALS],
        scope,
        base_url=data.get(CONF_BASE_URL) or DEFAULT_BASE_URL,
        auth_url=data.get(CONF_AUTH_URL) or DEFAULT_AUTH_URL,
        ssl_context=ssl_context,
        user_agent=f"HomeAssistant/{HA_VERSION} ha-gigachat/{integration.version}",
        max_concurrent_requests=MAX_CONCURRENT_REQUESTS.get(scope, 1),
    )


def chat_model_ids(models: list[GigaChatModel]) -> list[str]:
    """Return the IDs of the text generation models, best first."""
    ids = [model.id for model in models if model.type in (None, "chat")]
    order = {model_id: index for index, model_id in enumerate(PREFERRED_MODELS)}
    return sorted(ids, key=lambda model_id: (order.get(model_id, len(order)), model_id))


def pick_default_model(models: list[GigaChatModel]) -> str:
    """Return the preferred model among those available to the account."""
    available = set(chat_model_ids(models))
    return next(
        (model_id for model_id in PREFERRED_MODELS if model_id in available),
        FALLBACK_MODEL,
    )


@dataclass(slots=True)
class GigaChatData:
    """Data fetched by the coordinator."""

    models: list[GigaChatModel]
    balance: dict[str, float] | None


@dataclass(slots=True)
class UsageTracker:
    """Collects token usage reported by the API and notifies listeners."""

    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    precached_prompt_tokens: int = 0
    total_tokens: int = 0
    _listeners: list[Callable[[dict[str, int]], None]] = field(default_factory=list)

    @callback
    def async_add_listener(
        self, listener: Callable[[dict[str, int]], None]
    ) -> CALLBACK_TYPE:
        """Listen for usage increments."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def async_add(self, usage: Mapping[str, Any]) -> None:
        """Record the usage of one API response."""
        increment = {
            key: int(usage.get(key) or 0)
            for key in (
                "prompt_tokens",
                "completion_tokens",
                "precached_prompt_tokens",
                "total_tokens",
            )
        }
        self.requests += 1
        self.prompt_tokens += increment["prompt_tokens"]
        self.completion_tokens += increment["completion_tokens"]
        self.precached_prompt_tokens += increment["precached_prompt_tokens"]
        self.total_tokens += increment["total_tokens"]
        for listener in list(self._listeners):
            listener(increment)

    def as_dict(self) -> dict[str, int]:
        """Return the totals since start."""
        return {
            "requests": self.requests,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "precached_prompt_tokens": self.precached_prompt_tokens,
            "total_tokens": self.total_tokens,
        }


class GigaChatCoordinator(DataUpdateCoordinator[GigaChatData]):
    """Keeps the model list and the token balance up to date."""

    config_entry: GigaChatConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: GigaChatConfigEntry,
        client: GigaChatClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
            always_update=False,
            # Balance refreshes requested after chat requests are coalesced.
            request_refresh_debouncer=Debouncer(
                hass, LOGGER, cooldown=BALANCE_REFRESH_COOLDOWN, immediate=False
            ),
        )
        self.client = client
        self.usage = UsageTracker()
        self._models_fetched_at = 0.0

    @property
    def chat_models(self) -> list[str]:
        """Return the IDs of the available text generation models."""
        return chat_model_ids(self.data.models) if self.data else []

    @override
    async def _async_update_data(self) -> GigaChatData:
        """Fetch the model list (every few hours) and the balance."""
        models = self.data.models if self.data else []
        balance = self.data.balance if self.data else None
        try:
            if (
                not models
                or time.monotonic() - self._models_fetched_at
                > MODELS_REFRESH_INTERVAL.total_seconds()
            ):
                models = await self.client.async_get_models()
                self._models_fetched_at = time.monotonic()
        except GigaChatAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
                translation_placeholders={"message": err.message},
            ) from err
        except GigaChatError as err:
            self.update_interval = UPDATE_INTERVAL_ERROR
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"message": err.message},
            ) from err

        try:
            balance = await self.client.async_get_balance()
        except GigaChatAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
                translation_placeholders={"message": err.message},
            ) from err
        except GigaChatError as err:
            # The balance is informational, keep the last known value.
            LOGGER.debug("Could not update the token balance: %s", err)

        self.update_interval = UPDATE_INTERVAL
        return GigaChatData(models=models, balance=balance)

    @callback
    def async_record_usage(self, usage: Mapping[str, Any]) -> None:
        """Record token usage and refresh the balance a bit later."""
        self.usage.async_add(usage)
        if self.data and self.data.balance is not None:
            self.config_entry.async_create_background_task(
                self.hass, self.async_request_refresh(), "gigachat_balance_refresh"
            )
