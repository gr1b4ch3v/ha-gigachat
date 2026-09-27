"""Tests for the GigaChat sensors."""

from __future__ import annotations

from unittest.mock import AsyncMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components import conversation
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import Context, HomeAssistant, State
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)

from custom_components.gigachat.const import UPDATE_INTERVAL

from .common import USAGE, mock_stream, text_chunks


async def test_balance_sensors(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the balance sensors and new buckets appearing later."""
    lite = hass.states.get("sensor.gigachat_lite_token_balance")
    assert lite.state == "250000.0"
    assert lite.attributes["unit_of_measurement"] == "tokens"
    assert lite.attributes["friendly_name"] == "GigaChat Lite token balance"
    assert hass.states.get("sensor.gigachat_max_token_balance").state == "25000.0"
    assert hass.states.get("sensor.gigachat_ultra_token_balance").state == "50000.0"
    assert hass.states.get("sensor.gigachat_pro_token_balance") is None

    mock_client.async_get_balance.return_value = {
        "GigaChat": 1000.0,
        "GigaChat-Pro": 10.0,
        "GigaChat-Ultra": 50_000.0,
    }
    freezer.tick(UPDATE_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.gigachat_lite_token_balance").state == "1000.0"
    assert hass.states.get("sensor.gigachat_pro_token_balance").state == "10.0"
    assert (
        hass.states.get("sensor.gigachat_max_token_balance").state == STATE_UNAVAILABLE
    )


async def test_tokens_used(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test that token usage is counted and the balance refreshed afterwards."""
    state = hass.states.get("sensor.gigachat_tokens_used")
    assert state.state == "0"

    mock_client.async_chat_stream, _ = mock_stream(text_chunks("Hi"), text_chunks("Hi"))
    for _ in range(2):
        await conversation.async_converse(
            hass, "Hi", None, Context(), agent_id="conversation.gigachat"
        )

    state = hass.states.get("sensor.gigachat_tokens_used")
    assert state.state == str(2 * USAGE["total_tokens"])
    assert state.attributes["requests"] == 2
    assert state.attributes["prompt_tokens"] == 2 * USAGE["prompt_tokens"]
    assert state.attributes["completion_tokens"] == 2 * USAGE["completion_tokens"]
    assert (
        state.attributes["precached_prompt_tokens"]
        == 2 * USAGE["precached_prompt_tokens"]
    )

    # The balance is refreshed once, a minute after the requests
    calls = mock_client.async_get_balance.call_count
    freezer.tick(61)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert mock_client.async_get_balance.call_count == calls + 1


async def test_tokens_used_restored(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: AsyncMock
) -> None:
    """Test that the usage counter survives restarts."""
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(
                    "sensor.gigachat_tokens_used",
                    "1234",
                    {"requests": 7, "prompt_tokens": 1000, "completion_tokens": "x"},
                ),
                {"native_value": 1234, "native_unit_of_measurement": "tokens"},
            )
        ],
    )
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.gigachat_tokens_used")
    assert state.state == "1234"
    assert state.attributes["requests"] == 7
    assert state.attributes["completion_tokens"] == 0
