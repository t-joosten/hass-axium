"""Number platform for Axium zone controls.

Exposes per-zone bass, treble and balance (tone), plus a maximum-volume limit,
power-on (startup) volume, audio (lip-sync) delay and a sleep timer.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

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
    CMD_POWER,
    CMD_POWER_ON_VOLUME,
    CMD_SOURCE_GAIN,
    CMD_TREBLE,
    CMD_VOLUME,
    CMD_ZONE_GAIN,
    DATA_SLEEP_DEADLINES,
    DOMAIN,
    ID_KEY,
    POWER_OFF,
    SIGNAL_SLEEP_UPDATE,
    SOURCE_BYTE_TO_NAME,
    SOURCE_GAIN_MAX,
    SOURCE_GAIN_MIN,
    TREBLE_MAX,
    TREBLE_MIN,
    VOLUME_MAX,
    ZONE_ALL,
    ZONE_GAIN_MAX,
    ZONE_GAIN_MIN,
    ZONE_KEY,
)
from .controller import AxiumController, ZoneState
from .protocol import level_to_volume, to_signed_byte
from .helpers import get_advanced, get_sources, get_zones

# Sleep-timer fade: ramp the volume down over the final part of the countdown
# (capped) before turning the zone off, so it doesn't cut out abruptly.
_SLEEP_MAX_MIN = 180
_SLEEP_FADE_SECONDS = 30
_SLEEP_FADE_STEPS = 6


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
    advanced: bool = False


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
        advanced=True,
    ),
    AxiumNumberDescription(
        key="audio_delay", name="Audio delay", command=CMD_AUDIO_DELAY,
        min_value=0, max_value=AUDIO_DELAY_MAX, step=AUDIO_DELAY_STEP, unit="ms",
        getter=lambda s: s.audio_delay, to_byte=_ms_to_delay,
    ),
    AxiumNumberDescription(
        key="zone_gain", name="Zone gain", command=CMD_ZONE_GAIN,
        min_value=ZONE_GAIN_MIN, max_value=ZONE_GAIN_MAX, step=1, unit="dB",
        getter=lambda s: s.zone_gain, to_byte=_signed_byte, advanced=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the per-zone number controls, source gains and standby timer.

    Risky level/gain controls are only created when advanced settings are on.
    """
    controller: AxiumController = hass.data[DOMAIN][entry.entry_id]
    advanced = get_advanced(entry)
    entities: list[NumberEntity] = [
        AxiumNumber(controller, entry, item[ZONE_KEY], desc)
        for item in get_zones(entry)
        for desc in NUMBERS
        if advanced or not desc.advanced
    ]
    entities.extend(
        AxiumSleepTimer(controller, entry, item[ZONE_KEY]) for item in get_zones(entry)
    )
    entities.append(
        AxiumAllZonesSleepTimer(
            controller, entry, [item[ZONE_KEY] for item in get_zones(entry)]
        )
    )
    if advanced:
        entities.extend(
            AxiumSourceGain(controller, entry, item[ID_KEY])
            for item in get_sources(entry)
        )
    entities.append(AxiumStandbyTime(controller, entry))
    async_add_entities(entities)


class AxiumSleepTimer(NumberEntity):
    """Per-zone sleep timer: after N minutes, fade the zone down and power off."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_name = "Sleep timer"
    _attr_native_min_value = 0
    _attr_native_max_value = _SLEEP_MAX_MIN
    _attr_native_step = 5
    _attr_native_unit_of_measurement = "min"
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:timer-sand"

    def __init__(
        self, controller: AxiumController, entry: ConfigEntry, zone: int
    ) -> None:
        """Initialise the sleep-timer number."""
        self._controller = controller
        self._zone = zone
        self._entry_id = entry.entry_id
        self._minutes = 0
        self._task: asyncio.Task | None = None
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone}_sleep"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_zone_{zone}")}
        )

    def _publish_deadline(self, deadline: datetime | None) -> None:
        """Share the timer's end time so the sensor/card can read it."""
        store = self.hass.data.setdefault(DATA_SLEEP_DEADLINES, {}).setdefault(
            self._entry_id, {}
        )
        store[self._zone] = deadline
        async_dispatcher_send(self.hass, f"{SIGNAL_SLEEP_UPDATE}_{self._entry_id}")

    @property
    def available(self) -> bool:
        """Return whether the amplifier connection is up."""
        return self._controller.available

    _attr_extra_state_attributes = {"axium_kind": "sleep_timer"}

    @property
    def native_value(self) -> int:
        """Return the minutes remaining on the timer (0 = off)."""
        return self._minutes

    async def async_set_native_value(self, value: float) -> None:
        """Start (or cancel, when 0) the sleep timer."""
        self._cancel()
        self._minutes = int(value)
        if self._minutes > 0:
            self._publish_deadline(
                dt_util.utcnow() + timedelta(minutes=self._minutes)
            )
            self._task = self.hass.async_create_task(self._run(self._minutes))
        else:
            self._publish_deadline(None)
        self.async_write_ha_state()

    def _cancel(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _run(self, minutes: int) -> None:
        """Wait out the timer, fade the zone down, then power it off."""
        fade = min(_SLEEP_FADE_SECONDS, minutes * 60 * 0.5)
        await asyncio.sleep(max(0.0, minutes * 60 - fade))

        original = self._controller.zone_state(self._zone).volume
        if original and original > 0 and fade > 0:
            for i in range(1, _SLEEP_FADE_STEPS + 1):
                level = original * (_SLEEP_FADE_STEPS - i) / _SLEEP_FADE_STEPS
                await self._controller.async_send(
                    CMD_VOLUME, self._zone, level_to_volume(level)
                )
                await asyncio.sleep(fade / _SLEEP_FADE_STEPS)

        await self._controller.async_send(CMD_POWER, self._zone, POWER_OFF)
        # Restore the pre-fade volume so the next power-on isn't silent.
        if original and original > 0:
            await self._controller.async_send(
                CMD_VOLUME, self._zone, level_to_volume(original)
            )

        self._minutes = 0
        self._task = None
        self._publish_deadline(None)
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel any running timer and clear its deadline when removed."""
        self._cancel()
        self._publish_deadline(None)


class AxiumAllZonesSleepTimer(NumberEntity):
    """Hub-level sleep timer: fade and power off every zone at once.

    After the zones are off the amplifier idles into standby (subject to its
    Auto standby setting), so this effectively sleeps the whole system.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_name = "All-zone sleep timer"
    _attr_native_min_value = 0
    _attr_native_max_value = _SLEEP_MAX_MIN
    _attr_native_step = 5
    _attr_native_unit_of_measurement = "min"
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:power-sleep"
    _attr_extra_state_attributes = {"axium_kind": "sleep_timer", "sleep_all": True}

    def __init__(
        self, controller: AxiumController, entry: ConfigEntry, zones: list[int]
    ) -> None:
        """Initialise the all-zone sleep-timer number (on the hub device)."""
        self._controller = controller
        self._entry_id = entry.entry_id
        self._zones = zones
        self._minutes = 0
        self._task: asyncio.Task | None = None
        self._attr_unique_id = f"{entry.entry_id}_sleep_all"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    def _publish_deadline(self, deadline: datetime | None) -> None:
        store = self.hass.data.setdefault(DATA_SLEEP_DEADLINES, {}).setdefault(
            self._entry_id, {}
        )
        store["all"] = deadline
        async_dispatcher_send(self.hass, f"{SIGNAL_SLEEP_UPDATE}_{self._entry_id}")

    @property
    def available(self) -> bool:
        """Return whether the amplifier connection is up."""
        return self._controller.available

    @property
    def native_value(self) -> int:
        """Return the minutes remaining on the timer (0 = off)."""
        return self._minutes

    async def async_set_native_value(self, value: float) -> None:
        """Start (or cancel, when 0) the all-zone sleep timer."""
        self._cancel()
        self._minutes = int(value)
        if self._minutes > 0:
            self._publish_deadline(
                dt_util.utcnow() + timedelta(minutes=self._minutes)
            )
            self._task = self.hass.async_create_task(self._run(self._minutes))
        else:
            self._publish_deadline(None)
        self.async_write_ha_state()

    def _cancel(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _run(self, minutes: int) -> None:
        """Wait out the timer, fade every zone down, then power them all off."""
        fade = min(_SLEEP_FADE_SECONDS, minutes * 60 * 0.5)
        await asyncio.sleep(max(0.0, minutes * 60 - fade))

        originals = {
            zone: self._controller.zone_state(zone).volume for zone in self._zones
        }
        if fade > 0:
            for i in range(1, _SLEEP_FADE_STEPS + 1):
                for zone, original in originals.items():
                    if original and original > 0:
                        level = original * (_SLEEP_FADE_STEPS - i) / _SLEEP_FADE_STEPS
                        await self._controller.async_send(
                            CMD_VOLUME, zone, level_to_volume(level)
                        )
                await asyncio.sleep(fade / _SLEEP_FADE_STEPS)

        # Power off every zone in one command; the amp idles into standby.
        await self._controller.async_send(CMD_POWER, ZONE_ALL, POWER_OFF)
        for zone, original in originals.items():
            if original and original > 0:
                await self._controller.async_send(
                    CMD_VOLUME, zone, level_to_volume(original)
                )

        self._minutes = 0
        self._task = None
        self._publish_deadline(None)
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel any running timer and clear its deadline when removed."""
        self._cancel()
        self._publish_deadline(None)


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
        """Set the value on the amplifier, then read it back.

        Real amplifiers send no notification after a set, so request the value
        again (a command with no data byte) to refresh our cache.
        """
        await self._controller.async_send(
            self._desc.command, self._zone, self._desc.to_byte(value)
        )
        await self._controller.async_send(self._desc.command, self._zone)
