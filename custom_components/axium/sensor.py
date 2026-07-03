"""Sensor platform for Axium: diagnostics, alarm next-fire and sleep end times."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .const import (
    DATA_ALARMS_ENABLED,
    DATA_SLEEP_DEADLINES,
    DOMAIN,
    SIGNAL_ALARM_UPDATE,
    SIGNAL_SLEEP_UPDATE,
    ZONE_KEY,
)
from .controller import AxiumController
from .helpers import get_alarms, get_zones, next_alarm_fire


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
    """Set up diagnostic, alarm and sleep-timer sensors."""
    controller: AxiumController = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [
        AxiumSensor(controller, entry, desc) for desc in SENSORS
    ]
    entities.extend(
        AxiumSleepSensor(hass, entry, item[ZONE_KEY]) for item in get_zones(entry)
    )
    entities.append(
        AxiumSleepSensor(
            hass, entry, "all", name="All zones sleep ends", hub_device=True
        )
    )
    entities.extend(
        AxiumAlarmSensor(hass, entry, alarm) for alarm in get_alarms(entry)
    )
    async_add_entities(entities)


class AxiumSleepSensor(SensorEntity):
    """When a zone's sleep timer will power it off (usable in automations)."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:timer-sand"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        zone: int | str,
        name: str = "Sleep ends",
        hub_device: bool = False,
    ) -> None:
        """Initialise the sleep-end sensor (per zone, or the hub's all-zones one)."""
        self._hass = hass
        self._entry_id = entry.entry_id
        self._zone = zone
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone}_sleep_ends"
        identifier = (
            (DOMAIN, entry.entry_id)
            if hub_device
            else (DOMAIN, f"{entry.entry_id}_zone_{zone}")
        )
        self._attr_device_info = DeviceInfo(identifiers={identifier})

    async def async_added_to_hass(self) -> None:
        """Update when the zone's sleep deadline changes."""
        self.async_on_remove(
            async_dispatcher_connect(
                self._hass,
                f"{SIGNAL_SLEEP_UPDATE}_{self._entry_id}",
                self._update,
            )
        )

    @callback
    def _update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> datetime | None:
        """Return the timer's end time, or None when no timer is running."""
        return (
            self._hass.data.get(DATA_SLEEP_DEADLINES, {})
            .get(self._entry_id, {})
            .get(self._zone)
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Mark the sensor kind so the sleep card can find it."""
        return {"axium_kind": "sleep"}


class AxiumAlarmSensor(SensorEntity):
    """The next time an alarm will fire (usable in automations and the card)."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:alarm"

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, alarm: dict
    ) -> None:
        """Initialise the alarm next-fire sensor."""
        self._hass = hass
        self._entry = entry
        self._entry_id = entry.entry_id
        self._name = alarm["name"]
        self._attr_name = f"Alarm {alarm['name']}"
        # Alarm names are unique keys; use the raw name so two names that would
        # slugify to the same string (e.g. "Wake up" / "Wake-up") don't collide.
        self._attr_unique_id = f"{entry.entry_id}_alarm_{alarm['name']}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    def _alarm(self) -> dict | None:
        """The alarm's current config, read fresh (in-place edits, no reload)."""
        return next(
            (a for a in get_alarms(self._entry) if a["name"] == self._name), None
        )

    async def async_added_to_hass(self) -> None:
        """Recompute each minute (rolls over after firing) and on arm/disarm."""
        self.async_on_remove(
            async_track_time_change(self._hass, self._tick, second=0)
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self._hass,
                f"{SIGNAL_ALARM_UPDATE}_{self._entry_id}",
                self._tick,
            )
        )

    @callback
    def _tick(self, *_: Any) -> None:
        self.async_write_ha_state()

    def _armed(self, alarm: dict) -> bool:
        master = self._hass.data.get(DATA_ALARMS_ENABLED, {}).get(
            self._entry_id, True
        )
        return bool(master and alarm.get("enabled", True))

    @property
    def native_value(self) -> datetime | None:
        """Return the next fire time, or None when disarmed/disabled."""
        alarm = self._alarm()
        if alarm is None or not self._armed(alarm):
            return None
        return next_alarm_fire(alarm, dt_util.now())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the schedule so the alarms card can render it."""
        alarm = self._alarm()
        if alarm is None:
            return {"axium_kind": "alarm", "alarm_name": self._name}
        return {
            "axium_kind": "alarm",
            "alarm_name": alarm["name"],
            "alarm_time": alarm["time"],
            "alarm_days": alarm["days"],
            "alarm_zones": alarm["zones"],
            "alarm_source": alarm["source"],
            "alarm_volume": alarm["volume"],
            "alarm_enabled": alarm.get("enabled", True),
            "armed": self._armed(alarm),
        }


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
