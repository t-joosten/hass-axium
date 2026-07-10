"""Switch platform for Axium auto power-on / auto-standby."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    AUTO_POWER_ON_BIT,
    AUTO_STANDBY_BIT,
    DATA_ALARMS_ENABLED,
    DOMAIN,
    SIGNAL_ALARM_UPDATE,
    SPECIAL_LOUDNESS_BIT,
    SPECIAL_MONO_BIT,
    UNIT_KEY,
    ZONE_KEY,
)
from .controller import AxiumController
from .helpers import get_units, get_zones, primary_amp_identifier


@dataclass(frozen=True, kw_only=True)
class AxiumSwitchDescription:
    """Describes an auto-power switch."""

    key: str
    name: str
    bit: int
    getter: Callable[[AxiumController], bool]


SWITCHES: tuple[AxiumSwitchDescription, ...] = (
    AxiumSwitchDescription(
        key="auto_power_on",
        name="Auto power on",
        bit=AUTO_POWER_ON_BIT,
        getter=lambda c: c.auto_power_on,
    ),
    AxiumSwitchDescription(
        key="auto_standby",
        name="Auto standby",
        bit=AUTO_STANDBY_BIT,
        getter=lambda c: c.auto_standby,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the auto-power switches and the per-zone loudness/mono switches."""
    controller: AxiumController = hass.data[DOMAIN][entry.entry_id]
    entities: list[SwitchEntity] = [
        AxiumSwitch(controller, entry, desc) for desc in SWITCHES
    ]
    entities.append(AxiumAlarmsSwitch(hass, entry))
    # A Static IP switch per amp in the stack (network settings are per-unit).
    units = get_units(entry)
    if units:
        for unit in units:
            uid = unit[UNIT_KEY]
            if unit.get("primary"):
                entities.append(AxiumStaticIPSwitch(controller, entry))
            else:
                entities.append(
                    AxiumStaticIPSwitch(
                        controller, entry, uid, (DOMAIN, f"{entry.entry_id}_unit_{uid}")
                    )
                )
    else:
        entities.append(AxiumStaticIPSwitch(controller, entry))
    for item in get_zones(entry):
        entities.append(
            AxiumZoneSwitch(controller, entry, item[ZONE_KEY], "loudness", "Loudness", 0, SPECIAL_LOUDNESS_BIT)
        )
        entities.append(
            AxiumZoneSwitch(controller, entry, item[ZONE_KEY], "mono", "Mono", 1, SPECIAL_MONO_BIT)
        )
    async_add_entities(entities)


class AxiumAlarmsSwitch(SwitchEntity):
    """Master enable for all wake-to-music alarms on this amplifier."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_name = "Alarms"
    _attr_icon = "mdi:alarm"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise the alarms master switch."""
        self._hass = hass
        self._entry_id = entry.entry_id
        self._attr_unique_id = f"{entry.entry_id}_alarms_enabled"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    def _flags(self) -> dict:
        return self._hass.data.setdefault(DATA_ALARMS_ENABLED, {})

    def _set(self, enabled: bool) -> None:
        self._flags()[self._entry_id] = enabled
        self.async_write_ha_state()
        # Nudge the per-alarm "next fire" sensors to recompute (None when off).
        async_dispatcher_send(
            self._hass, f"{SIGNAL_ALARM_UPDATE}_{self._entry_id}"
        )

    @property
    def is_on(self) -> bool:
        """Return whether alarms are armed."""
        return bool(self._flags().get(self._entry_id, True))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Arm all alarms."""
        self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disarm all alarms."""
        self._set(False)


class AxiumStaticIPSwitch(SwitchEntity):
    """Pin the amplifier's IP as static (on) or use DHCP (off).

    Turning this on writes the amp's *current* address back as static, so a
    reboot's new DHCP lease can't move it and break the connection. Turning it
    off returns to DHCP (which may change the IP).
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_name = "Static IP address"
    _attr_icon = "mdi:ip-network"

    def __init__(
        self,
        controller: AxiumController,
        entry: ConfigEntry,
        unit_id: int | None = None,
        device_ident: tuple[str, str] | None = None,
    ) -> None:
        """Initialise the static-IP switch for one amp (None = primary/hub)."""
        self._controller = controller
        self._unit_id = unit_id
        suffix = "" if unit_id is None else f"_unit_{unit_id}"
        self._attr_unique_id = f"{entry.entry_id}_static_ip{suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={device_ident or primary_amp_identifier(entry.entry_id)}
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to diagnostic updates (network config arrives there)."""
        self.async_on_remove(
            self._controller.register_diagnostic_listener(self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        """Write state on change."""
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Available once this amp's network settings have been read."""
        return self._controller.available and self._controller.network_known(
            self._unit_id
        )

    @property
    def is_on(self) -> bool:
        """Return whether this amp is on a static IP."""
        return self._controller.network_is_static(self._unit_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose this amp's current IP address."""
        return {"ip_address": self._controller.network_ip(self._unit_id)}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Pin the current IP as static."""
        await self._controller.async_set_network_static(True, self._unit_id)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Return to DHCP."""
        await self._controller.async_set_network_static(False, self._unit_id)


class AxiumZoneSwitch(SwitchEntity):
    """A per-zone special-features toggle (loudness or mono) via command 0x0C."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        controller: AxiumController,
        entry: ConfigEntry,
        zone: int,
        key: str,
        label: str,
        byte_index: int,
        bit: int,
    ) -> None:
        """Initialise the per-zone switch."""
        self._controller = controller
        self._zone = zone
        self._byte_index = byte_index
        self._bit = bit
        self._attr_name = label
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_zone_{zone}")}
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to updates and request the current special features."""
        from .const import CMD_SPECIAL_FEATURES

        self.async_on_remove(
            self._controller.register_listener(self._zone, self._handle_update)
        )
        await self._controller.async_send(CMD_SPECIAL_FEATURES, self._zone)

    @callback
    def _handle_update(self) -> None:
        """Write state on change."""
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return whether the amplifier connection is up."""
        return self._controller.available

    @property
    def is_on(self) -> bool | None:
        """Return whether the feature is enabled."""
        state = self._controller.zone_state(self._zone)
        return state.loudness if self._byte_index == 0 else state.mono

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the feature."""
        await self._controller.async_set_special_bit(
            self._zone, self._byte_index, self._bit, True
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the feature."""
        await self._controller.async_set_special_bit(
            self._zone, self._byte_index, self._bit, False
        )


class AxiumSwitch(SwitchEntity):
    """Auto power-on / auto-standby toggle for the amplifier."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        controller: AxiumController,
        entry: ConfigEntry,
        desc: AxiumSwitchDescription,
    ) -> None:
        """Initialise the switch."""
        self._controller = controller
        self._desc = desc
        self._attr_name = desc.name
        self._attr_unique_id = f"{entry.entry_id}_{desc.key}"
        # Amp-level control lives on the primary amp device, not the logical hub.
        self._attr_device_info = DeviceInfo(
            identifiers={primary_amp_identifier(entry.entry_id)}
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to diagnostic updates."""
        self.async_on_remove(
            self._controller.register_diagnostic_listener(self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        """Write state on change."""
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return whether the amplifier connection is up."""
        return self._controller.available

    @property
    def is_on(self) -> bool:
        """Return whether the option is enabled."""
        return self._desc.getter(self._controller)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the option."""
        await self._controller.async_set_auto_power_bit(self._desc.bit, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the option."""
        await self._controller.async_set_auto_power_bit(self._desc.bit, False)
