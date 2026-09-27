"""The GigaChat integration."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .coordinator import GigaChatConfigEntry, GigaChatCoordinator, async_create_client

PLATFORMS = (Platform.AI_TASK, Platform.CONVERSATION, Platform.SENSOR)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup_entry(hass: HomeAssistant, entry: GigaChatConfigEntry) -> bool:
    """Set up GigaChat from a config entry."""
    client = await async_create_client(hass, entry.data)
    coordinator = GigaChatCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GigaChatConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_update_listener(
    hass: HomeAssistant, entry: GigaChatConfigEntry
) -> None:
    """Reload the entry when it or one of its subentries changes."""
    await hass.config_entries.async_reload(entry.entry_id)
