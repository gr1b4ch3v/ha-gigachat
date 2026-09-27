"""Fixtures for the GigaChat tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.const import CONF_LLM_HASS_API, CONF_PROMPT, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

# Import the integration before the hass fixture mounts the test config dir,
# so that the loader finds it in this repository.
import custom_components.gigachat  # noqa: F401
from custom_components.gigachat.api import GigaChatModel
from custom_components.gigachat.const import (
    CONF_AUTH_URL,
    CONF_BASE_URL,
    CONF_CHAT_MODEL,
    CONF_CREDENTIALS,
    CONF_RECOMMENDED,
    CONF_SCOPE,
    DEFAULT_AI_TASK_NAME,
    DEFAULT_AUTH_URL,
    DEFAULT_BASE_URL,
    DEFAULT_CONVERSATION_NAME,
    DEFAULT_PROMPT,
    DOMAIN,
    SCOPE_PERS,
    SUBENTRY_AI_TASK,
    SUBENTRY_CONVERSATION,
)

from .common import AUTH_KEY, CLIENT_ID

MODELS = [
    GigaChatModel("GigaChat-2", "chat", "salutedevices"),
    GigaChatModel("GigaChat-2-Max", "chat", "salutedevices"),
    GigaChatModel("GigaChat-2-Pro", "chat", "salutedevices"),
    GigaChatModel("GigaChat-3-Ultra", "chat", "salutedevices"),
    GigaChatModel("Embeddings", "embedder", "salutedevices"),
]
BALANCE = {"GigaChat": 250_000.0, "GigaChat-Max": 25_000.0, "GigaChat-Ultra": 50_000.0}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable custom integrations in all tests."""


@pytest.fixture
def conversation_options() -> dict[str, Any]:
    """Options of the conversation subentry."""
    return {
        CONF_RECOMMENDED: True,
        CONF_CHAT_MODEL: "GigaChat-3-Ultra",
        CONF_PROMPT: DEFAULT_PROMPT,
        CONF_LLM_HASS_API: [llm.LLM_API_ASSIST],
    }


@pytest.fixture
def ai_task_options() -> dict[str, Any]:
    """Options of the AI task subentry."""
    return {CONF_RECOMMENDED: True, CONF_CHAT_MODEL: "GigaChat-3-Ultra"}


@pytest.fixture
async def mock_config_entry(
    hass: HomeAssistant,
    conversation_options: dict[str, Any],
    ai_task_options: dict[str, Any],
) -> MockConfigEntry:
    """Return a GigaChat config entry with both subentries."""
    # The conversation dependency needs the core integration.
    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="GigaChat",
        unique_id=CLIENT_ID,
        version=1,
        minor_version=1,
        data={
            CONF_CREDENTIALS: AUTH_KEY,
            CONF_SCOPE: SCOPE_PERS,
            CONF_VERIFY_SSL: True,
            CONF_BASE_URL: DEFAULT_BASE_URL,
            CONF_AUTH_URL: DEFAULT_AUTH_URL,
        },
        subentries_data=[
            ConfigSubentryData(
                data=conversation_options,
                subentry_type=SUBENTRY_CONVERSATION,
                title=DEFAULT_CONVERSATION_NAME,
                unique_id=None,
            ),
            ConfigSubentryData(
                data=ai_task_options,
                subentry_type=SUBENTRY_AI_TASK,
                title=DEFAULT_AI_TASK_NAME,
                unique_id=None,
            ),
        ],
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def mock_client() -> Generator[AsyncMock]:
    """Mock the API client used by the integration."""
    with patch(
        "custom_components.gigachat.coordinator.GigaChatClient", autospec=True
    ) as client_class:
        client = client_class.return_value
        client.scope = SCOPE_PERS
        client.async_get_models.return_value = MODELS
        client.async_get_balance.return_value = dict(BALANCE)
        client.async_upload_file.return_value = "uploaded-file-id"
        client.async_download_file.return_value = (b"image-bytes", "image/jpeg")
        yield client


@pytest.fixture
async def init_integration(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: AsyncMock
) -> AsyncGenerator[MockConfigEntry]:
    """Set up the integration."""
    hass.config.language = "en"
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    yield mock_config_entry
