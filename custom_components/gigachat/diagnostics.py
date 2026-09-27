"""Diagnostics support for the GigaChat integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_CREDENTIALS
from .coordinator import GigaChatConfigEntry

TO_REDACT = {CONF_CREDENTIALS, "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GigaChatConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(
            {
                "title": entry.title,
                "unique_id": entry.unique_id,
                "version": f"{entry.version}.{entry.minor_version}",
                "data": dict(entry.data),
            },
            TO_REDACT,
        ),
        "subentries": [
            {
                "type": subentry.subentry_type,
                "title": subentry.title,
                "data": dict(subentry.data),
            }
            for subentry in entry.subentries.values()
        ],
        "models": [asdict(model) for model in coordinator.data.models],
        "balance": coordinator.data.balance,
        "usage_since_start": coordinator.usage.as_dict(),
        "last_update_success": coordinator.last_update_success,
    }
