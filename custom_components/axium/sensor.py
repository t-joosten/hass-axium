"""Sensor platform for Axium amplifier diagnostics (temperature)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .controller import AxiumController


@dataclass(frozen=True, kw_only=True)
class AxiumSensorDescription:
    """Describes a diagnostic sensor."""

    key: str
    name: str
    getter: Callable[[AxiumController], int | None]


SENSORS: tuple[AxiumSensorDescription, ...] = (
    AxiumSensorDescription(
        key="temperature", name="Temperature", getter=lambda c: c.temperature
    ),
    AxiumSensorDescription(
        key="peak_temperature",
        name="Peak temperature",
        getter=lambda c: c.peak_temperature,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the diagnostic sensors on the amplifier device."""
    controller: AxiumController = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(AxiumSensor(controller, entry, desc) for desc in SENSORS)


class AxiumSensor(SensorEntity):
    """An amplifier diagnostic sensor (shown in the device's Diagnostics)."""

    _attr_has_entity_name = True
    _attr_should_poll = True  # poll to refresh the temperature periodically
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        controller: AxiumController,
        entry: ConfigEntry,
        desc: AxiumSensorDescription,
    ) -> None:
        """Initialise the sensor."""
        self._controller = controller
        self._desc = desc
        self._attr_name = desc.name
        self._attr_unique_id = f"{entry.entry_id}_{desc.key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    async def async_added_to_hass(self) -> None:
        """Subscribe to diagnostic updates."""
        self.async_on_remove(
            self._controller.register_diagnostic_listener(self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        """Write state on change."""
        self.async_write_ha_state()

    async def async_update(self) -> None:
        """Request fresh diagnostics from the amplifier."""
        await self._controller.async_request_extended_info()

    @property
    def available(self) -> bool:
        """Return whether the amplifier connection is up."""
        return self._controller.available

    @property
    def native_value(self) -> int | None:
        """Return the sensor value."""
        return self._desc.getter(self._controller)
