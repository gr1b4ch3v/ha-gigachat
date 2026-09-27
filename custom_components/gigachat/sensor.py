"""Token balance and usage sensors for the GigaChat integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import DOMAIN, STUDIO_URL
from .coordinator import GigaChatConfigEntry, GigaChatCoordinator

PARALLEL_UPDATES = 0

UNIT_TOKENS = "tokens"

# Balance buckets reported by the API and the model families they pay for.
BALANCE_NAMES = {
    "GigaChat": "Lite",
    "GigaChat-Pro": "Pro",
    "GigaChat-Max": "Max",
    "GigaChat-Ultra": "Ultra",
}

USAGE_ATTRIBUTES = (
    "requests",
    "prompt_tokens",
    "completion_tokens",
    "precached_prompt_tokens",
)


def _device_info(entry: GigaChatConfigEntry) -> DeviceInfo:
    """Return the device representing the GigaChat account."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Sber",
        model="GigaChat API",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=STUDIO_URL,
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GigaChatConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator = entry.runtime_data
    async_add_entities([GigaChatTokensUsedSensor(entry)])

    known: set[str] = set()

    @callback
    def async_add_balance_sensors() -> None:
        """Add a sensor for every balance bucket the account has."""
        new = [key for key in (coordinator.data.balance or {}) if key not in known]
        if new:
            known.update(new)
            async_add_entities(GigaChatBalanceSensor(coordinator, key) for key in new)

    async_add_balance_sensors()
    entry.async_on_unload(coordinator.async_add_listener(async_add_balance_sensors))


class GigaChatBalanceSensor(CoordinatorEntity[GigaChatCoordinator], SensorEntity):
    """Remaining prepaid tokens of a model family."""

    _attr_has_entity_name = True
    _attr_translation_key = "balance"
    _attr_native_unit_of_measurement = UNIT_TOKENS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: GigaChatCoordinator, key: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._key = key
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_balance_{slugify(key)}"
        self._attr_translation_placeholders = {"model": BALANCE_NAMES.get(key, key)}
        self._attr_device_info = _device_info(entry)

    @property
    def available(self) -> bool:
        """Return if the balance of this bucket is known."""
        return (
            super().available
            and self.coordinator.data.balance is not None
            and self._key in self.coordinator.data.balance
        )

    @property
    def native_value(self) -> float | None:
        """Return the remaining tokens."""
        return (self.coordinator.data.balance or {}).get(self._key)


class GigaChatTokensUsedSensor(RestoreSensor):
    """Tokens billed for the requests made by this integration."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "tokens_used"
    _attr_native_unit_of_measurement = UNIT_TOKENS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 0

    def __init__(self, entry: GigaChatConfigEntry) -> None:
        """Initialize the sensor."""
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_tokens_used"
        self._attr_device_info = _device_info(entry)
        self._attr_native_value = 0
        self._counters = dict.fromkeys(USAGE_ATTRIBUTES, 0)

    async def async_added_to_hass(self) -> None:
        """Restore the counters and start listening for usage."""
        await super().async_added_to_hass()
        if (data := await self.async_get_last_sensor_data()) is not None:
            try:
                self._attr_native_value = int(float(data.native_value or 0))
            except (TypeError, ValueError):
                self._attr_native_value = 0
        if (state := await self.async_get_last_state()) is not None:
            for key in USAGE_ATTRIBUTES:
                try:
                    self._counters[key] = int(state.attributes.get(key, 0))
                except (TypeError, ValueError):
                    self._counters[key] = 0
        self.async_on_remove(
            self._entry.runtime_data.usage.async_add_listener(self._async_handle_usage)
        )

    @callback
    def _async_handle_usage(self, increment: dict[str, int]) -> None:
        """Add the usage of one response."""
        self._attr_native_value = (
            int(self._attr_native_value or 0) + increment["total_tokens"]
        )
        self._counters["requests"] += 1
        for key in ("prompt_tokens", "completion_tokens", "precached_prompt_tokens"):
            self._counters[key] += increment[key]
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the detailed counters."""
        return dict(self._counters)
