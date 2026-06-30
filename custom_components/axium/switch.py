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
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import AUTO_POWER_ON_BIT, AUTO_STANDBY_BIT, DOMAIN
from .controller import AxiumController


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
    """Set up the auto-power switches on the amplifier device."""
    controller: AxiumController = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(AxiumSwitch(controller, entry, desc) for desc in SWITCHES)


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
