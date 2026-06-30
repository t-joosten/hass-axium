"""Number platform for Axium zone tone controls (bass, treble, balance)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    BALANCE_MAX,
    BALANCE_MIN,
    BASS_MAX,
    BASS_MIN,
    CMD_BALANCE,
    CMD_BASS,
    CMD_TREBLE,
    DOMAIN,
    NAME_KEY,
    TREBLE_MAX,
    TREBLE_MIN,
    ZONE_KEY,
)
from .controller import AxiumController, ZoneState
from .helpers import get_zones
from .protocol import to_signed_byte


@dataclass(frozen=True, kw_only=True)
class AxiumToneDescription:
    """Describes a tone control number entity."""

    key: str
    name: str
    command: int
    min_value: int
    max_value: int
    unit: str | None
    getter: Callable[[ZoneState], int | None]


TONES: tuple[AxiumToneDescription, ...] = (
    AxiumToneDescription(
        key="bass",
        name="Bass",
        command=CMD_BASS,
        min_value=BASS_MIN,
        max_value=BASS_MAX,
        unit="dB",
        getter=lambda state: state.bass,
    ),
    AxiumToneDescription(
        key="treble",
        name="Treble",
        command=CMD_TREBLE,
        min_value=TREBLE_MIN,
        max_value=TREBLE_MAX,
        unit="dB",
        getter=lambda state: state.treble,
    ),
    AxiumToneDescription(
        key="balance",
        name="Balance",
        command=CMD_BALANCE,
        min_value=BALANCE_MIN,
        max_value=BALANCE_MAX,
        unit=None,
        getter=lambda state: state.balance,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the tone-control numbers for each zone."""
    controller: AxiumController = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        AxiumTone(controller, entry, item[ZONE_KEY], desc)
        for item in get_zones(entry)
        for desc in TONES
    )


class AxiumTone(NumberEntity):
    """A bass/treble/balance control for one zone."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1

    def __init__(
        self,
        controller: AxiumController,
        entry: ConfigEntry,
        zone: int,
        desc: AxiumToneDescription,
    ) -> None:
        """Initialise the tone entity."""
        self._controller = controller
        self._zone = zone
        self._desc = desc
        self._attr_name = desc.name
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone}_{desc.key}"
        self._attr_native_min_value = desc.min_value
        self._attr_native_max_value = desc.max_value
        self._attr_native_unit_of_measurement = desc.unit
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_zone_{zone}")}
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to updates and request the current value."""
        self.async_on_remove(
            self._controller.register_listener(self._zone, self._handle_update)
        )
        # A command with no data byte is treated as a request by the amplifier.
        await self._controller.async_send(self._desc.command, self._zone)

    @callback
    def _handle_update(self) -> None:
        """Write state when the zone changes."""
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return whether the amplifier connection is up."""
        return self._controller.available

    @property
    def native_value(self) -> int | None:
        """Return the current tone value."""
        return self._desc.getter(self._controller.zone_state(self._zone))

    async def async_set_native_value(self, value: float) -> None:
        """Set the tone value on the amplifier."""
        await self._controller.async_send(
            self._desc.command, self._zone, to_signed_byte(int(value))
        )
