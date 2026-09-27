"""Tests for setting up the GigaChat integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.gigachat.api import (
    GigaChatAuthError,
    GigaChatConnectionError,
    GigaChatServerError,
)
from custom_components.gigachat.const import (
    CONF_CHAT_MODEL,
    DOMAIN,
    SCOPE_PERS,
    UPDATE_INTERVAL,
)
from custom_components.gigachat.coordinator import (
    async_create_client,
    create_ssl_context,
)

from .common import AUTH_KEY


async def test_setup_and_unload(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test setting up and unloading the entry."""
    assert init_integration.state is ConfigEntryState.LOADED
    entity_registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(
        entity_registry, init_integration.entry_id
    )
    assert {entity.domain for entity in entities} == {
        "conversation",
        "ai_task",
        "sensor",
    }

    device_registry = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(
        device_registry, init_integration.entry_id
    )
    assert {device.name for device in devices} == {"GigaChat", "GigaChat AI Task"}
    assert {device.model for device in devices} == {"GigaChat-3-Ultra", "GigaChat API"}

    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    assert init_integration.state is ConfigEntryState.NOT_LOADED


@pytest.mark.parametrize(
    ("side_effect", "state"),
    [
        (GigaChatAuthError("Unauthorized", 401), ConfigEntryState.SETUP_ERROR),
        (GigaChatConnectionError("timeout"), ConfigEntryState.SETUP_RETRY),
        (GigaChatServerError("Internal", 500), ConfigEntryState.SETUP_RETRY),
    ],
)
async def test_setup_errors(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client: AsyncMock,
    side_effect: Exception,
    state: ConfigEntryState,
) -> None:
    """Test errors during setup."""
    mock_client.async_get_models.side_effect = side_effect
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is state
    flows = hass.config_entries.flow.async_progress()
    assert bool(flows) is (state is ConfigEntryState.SETUP_ERROR)


async def test_balance_errors_are_tolerated(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    freezer,
) -> None:
    """Test that a failing balance keeps the last value and the entry available."""
    mock_client.async_get_balance.side_effect = GigaChatServerError("Internal", 500)
    freezer.tick(UPDATE_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.gigachat_lite_token_balance").state == "250000.0"
    assert hass.states.get("conversation.gigachat").state != "unavailable"

    mock_client.async_get_balance.side_effect = GigaChatAuthError("Unauthorized", 401)
    freezer.tick(UPDATE_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_subentry_change_reloads(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Test that changing a subentry reloads the entry."""
    subentry = next(iter(init_integration.subentries.values()))
    hass.config_entries.async_update_subentry(
        init_integration,
        subentry,
        data={**subentry.data, CONF_CHAT_MODEL: "GigaChat-2"},
    )
    await hass.async_block_till_done()
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, subentry.subentry_id), init_integration.entry_id
    )
    assert device.model == "GigaChat-2"


async def test_create_client(hass: HomeAssistant) -> None:
    """Test the SSL context and client creation."""
    context = await hass.async_add_executor_job(create_ssl_context, True)
    subjects = [cert.get("subject") for cert in context.get_ca_certs()]
    assert any(
        ("commonName", "Russian Trusted Root CA") in [item[0] for item in subject]
        for subject in subjects
    )
    assert create_ssl_context(False) is False

    client = await async_create_client(
        hass, {"credentials": AUTH_KEY, "scope": SCOPE_PERS, CONF_VERIFY_SSL: False}
    )
    assert client.scope == SCOPE_PERS
    assert client._semaphore._value == 1  # individuals get a single stream


async def test_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
) -> None:
    """Test that diagnostics do not leak credentials."""
    with patch(
        "homeassistant.components.diagnostics.async_get_custom_components",
        return_value={},
    ):
        diagnostics = await get_diagnostics_for_config_entry(
            hass, hass_client, init_integration
        )
    assert diagnostics["entry"]["data"]["credentials"] == "**REDACTED**"
    assert diagnostics["entry"]["unique_id"] == "**REDACTED**"
    assert AUTH_KEY not in str(diagnostics)
    assert diagnostics["balance"]["GigaChat"] == 250_000.0
    assert "GigaChat-3-Ultra" in [model["id"] for model in diagnostics["models"]]
    assert {sub["type"] for sub in diagnostics["subentries"]} == {
        "conversation",
        "ai_task_data",
    }
