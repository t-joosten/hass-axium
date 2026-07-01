"""Number platform for Axium zone controls.

Exposes per-zone bass, treble and balance (tone), plus a maximum-volume limit,
power-on (startup) volume and audio (lip-sync) delay.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    AUDIO_DELAY_MAX,
    AUDIO_DELAY_STEP,
    BALANCE_MAX,
    BALANCE_MIN,
    BASS_MAX,
    BASS_MIN,
    CMD_AUDIO_DELAY,
    CMD_BALANCE,
    CMD_BASS,
    CMD_MAX_VOLUME,
    CMD_POWER_ON_VOLUME,
    CMD_SOURCE_GAIN,
    CMD_TREBLE,
    CMD_ZONE_GAIN,
    DOMAIN,
    ID_KEY,
    SOURCE_BYTE_TO_NAME,
    SOURCE_GAIN_MAX,
    SOURCE_GAIN_MIN,
    TREBLE_MAX,
    TREBLE_MIN,
    VOLUME_MAX,
    ZONE_GAIN_MAX,
    ZONE_GAIN_MIN,
    ZONE_KEY,
)
from .controller import AxiumController, ZoneState
from .protocol import to_signed_byte
from .helpers import get_sources, get_zones


def _signed_byte(value: float) -> int:
    """Encode a tone value as a signed byte."""
    return to_signed_byte(int(value))


def _percent_to_volume(value: float) -> int:
    """Encode a 0-100 percentage as a 0-160 volume byte."""
    return max(0, min(VOLUME_MAX, round(value / 100 * VOLUME_MAX)))


def _ms_to_delay(value: float) -> int:
    """Encode milliseconds as a 5 ms-step byte."""
    return max(0, min(255, round(value / AUDIO_DELAY_STEP)))


@dataclass(frozen=True, kw_only=True)
class AxiumNumberDescription:
    """Describes a per-zone number entity."""

    key: str
    name: str
    command: int
    min_value: float
    max_value: float
    step: float
    unit: str | None
    getter: Callable[[ZoneState], int | None]
    to_byte: Callable[[float], int]


NUMBERS: tuple[AxiumNumberDescription, ...] = (
    AxiumNumberDescription(
        key="bass", name="Bass", command=CMD_BASS, min_value=BASS_MIN,
        max_value=BASS_MAX, step=1, unit="dB", getter=lambda s: s.bass,
        to_byte=_signed_byte,
    ),
    AxiumNumberDescription(
        key="treble", name="Treble", command=CMD_TREBLE, min_value=TREBLE_MIN,
        max_value=TREBLE_MAX, step=1, unit="dB", getter=lambda s: s.treble,
        to_byte=_signed_byte,
    ),
    AxiumNumberDescription(
        key="balance", name="Balance", command=CMD_BALANCE, min_value=BALANCE_MIN,
        max_value=BALANCE_MAX, step=1, unit=None, getter=lambda s: s.balance,
        to_byte=_signed_byte,
    ),
    AxiumNumberDescription(
        key="max_volume", name="Maximum volume", command=CMD_MAX_VOLUME,
        min_value=0, max_value=100, step=1, unit="%",
        getter=lambda s: s.max_volume, to_byte=_percent_to_volume,
    ),
    AxiumNumberDescription(
        key="power_on_volume", name="Power-on volume", command=CMD_POWER_ON_VOLUME,
        min_value=0, max_value=100, step=1, unit="%",
        getter=lambda s: s.power_on_volume, to_byte=_percent_to_volume,
    ),
    AxiumNumberDescription(
        key="audio_delay", name="Audio delay", command=CMD_AUDIO_DELAY,
        min_value=0, max_value=AUDIO_DELAY_MAX, step=AUDIO_DELAY_STEP, unit="ms",
        getter=lambda s: s.audio_delay, to_byte=_ms_to_delay,
    ),
    AxiumNumberDescription(
        key="zone_gain", name="Zone gain", command=CMD_ZONE_GAIN,
        min_value=ZONE_GAIN_MIN, max_value=ZONE_GAIN_MAX, step=1, unit="dB",
        getter=lambda s: s.zone_gain, to_byte=_signed_byte,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the per-zone number controls, source gains and standby timer."""
    controller: AxiumController = hass.data[DOMAIN][entry.entry_id]
    entities: list[NumberEntity] = [
        AxiumNumber(controller, entry, item[ZONE_KEY], desc)
        for item in get_zones(entry)
        for desc in NUMBERS
    ]
    entities.extend(
        AxiumSourceGain(controller, entry, item[ID_KEY]) for item in get_sources(entry)
    )
    entities.append(AxiumStandbyTime(controller, entry))
    async_add_entities(entities)


class AxiumSourceGain(NumberEntity):
    """Input-gain trim for one source (0..18 dB)."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = SOURCE_GAIN_MIN
    _attr_native_max_value = SOURCE_GAIN_MAX
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "dB"
    _attr_icon = "mdi:tune-vertical"

    def __init__(
        self, controller: AxiumController, entry: ConfigEntry, source_id: int
    ) -> None:
        """Initialise the source-gain number."""
        self._controller = controller
        self._source_id = source_id
        label = SOURCE_BYTE_TO_NAME.get(source_id, f"Source {source_id}")
        self._attr_name = f"{label} gain"
        self._attr_unique_id = f"{entry.entry_id}_source_{source_id}_gain"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    async def async_added_to_hass(self) -> None:
        """Subscribe to updates and request the current gain."""
        self.async_on_remove(
            self._controller.register_diagnostic_listener(self._handle_update)
        )
        await self._controller.async_send(CMD_SOURCE_GAIN, 0xFF, self._source_id)

    @callback
    def _handle_update(self) -> None:
        """Write state on change."""
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return whether the amplifier connection is up."""
        return self._controller.available

    @property
    def native_value(self) -> int | None:
        """Return the current source gain in dB."""
        return self._controller.source_gain(self._source_id)

    async def async_set_native_value(self, value: float) -> None:
        """Set the source gain on the amplifier."""
        await self._controller.async_set_source_gain(self._source_id, int(value))


class AxiumStandbyTime(NumberEntity):
    """Auto-standby timeout for the amplifier (snaps to the nearest 2^n s)."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_name = "Auto standby time"
    _attr_native_min_value = 1
    _attr_native_max_value = 7200
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "s"
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:timer-outline"

    def __init__(self, controller: AxiumController, entry: ConfigEntry) -> None:
        """Initialise the standby-time number."""
        self._controller = controller
        self._attr_unique_id = f"{entry.entry_id}_standby_time"
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
    def native_value(self) -> int:
        """Return the current standby timeout in seconds."""
        return self._controller.standby_seconds

    async def async_set_native_value(self, value: float) -> None:
        """Set the standby timeout."""
        await self._controller.async_set_standby_seconds(value)


class AxiumNumber(NumberEntity):
    """A per-zone numeric control."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        controller: AxiumController,
        entry: ConfigEntry,
        zone: int,
        desc: AxiumNumberDescription,
    ) -> None:
        """Initialise the number entity."""
        self._controller = controller
        self._zone = zone
        self._desc = desc
        self._attr_name = desc.name
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone}_{desc.key}"
        self._attr_native_min_value = desc.min_value
        self._attr_native_max_value = desc.max_value
        self._attr_native_step = desc.step
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
        """Return the current value."""
        return self._desc.getter(self._controller.zone_state(self._zone))

    async def async_set_native_value(self, value: float) -> None:
        """Set the value on the amplifier."""
        await self._controller.async_send(
            self._desc.command, self._zone, self._desc.to_byte(value)
        )
