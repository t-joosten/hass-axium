"""Media player platform for Axium amplifier zones."""

from __future__ import annotations

import logging

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CMD_MUTE,
    CMD_POWER,
    CMD_SOURCE,
    CMD_VOLUME,
    CMD_VOLUME_DOWN,
    CMD_VOLUME_UP,
    CONF_ZONES,
    DEFAULT_SOURCE_COUNT,
    DOMAIN,
    MUTE_OFF,
    MUTE_ON,
    POWER_OFF,
    POWER_ON,
    SOURCE_FLAG_TURN_ON,
    SOURCE_NUMBER_TO_BYTE,
)
from .controller import AxiumController

_LOGGER = logging.getLogger(__name__)

SUPPORT_AXIUM = (
    MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.TURN_OFF
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.SELECT_SOURCE
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Axium media player zones from a config entry."""
    controller: AxiumController = hass.data[DOMAIN][entry.entry_id]

    zones: list[int] = entry.options.get(
        CONF_ZONES, entry.data.get(CONF_ZONES, [1])
    )

    entities = [AxiumZone(controller, entry, zone) for zone in zones]

    # Ask the amplifier for each zone's name so entities can self-label.
    for zone in zones:
        await controller.async_request_zone_name(zone)

    async_add_entities(entities)


class AxiumZone(MediaPlayerEntity):
    """Representation of a single Axium amplifier zone."""

    _attr_has_entity_name = True
    _attr_supported_features = SUPPORT_AXIUM
    _attr_should_poll = False

    def __init__(
        self, controller: AxiumController, entry: ConfigEntry, zone: int
    ) -> None:
        """Initialise the zone entity."""
        self._controller = controller
        self._entry = entry
        self._zone = zone
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone}"
        self._attr_name = f"Zone {zone}"
        self._source_names = [f"Source {n}" for n in range(1, DEFAULT_SOURCE_COUNT + 1)]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Axium",
            model="Amplifier",
        )

    async def async_added_to_hass(self) -> None:
        """Register for push updates from the controller."""
        self.async_on_remove(
            self._controller.register_listener(self._zone, self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        """Handle a state update pushed from the controller."""
        # Adopt the amplifier-reported zone name if one is available.
        state = self._controller.zone_state(self._zone)
        if state.name:
            self._attr_name = state.name
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return whether the amplifier connection is up."""
        return self._controller.available

    @property
    def state(self) -> MediaPlayerState | None:
        """Return the power state of the zone."""
        power = self._controller.zone_state(self._zone).power
        if power is None:
            return None
        return MediaPlayerState.ON if power else MediaPlayerState.OFF

    @property
    def volume_level(self) -> float | None:
        """Return the current volume (0.0..1.0)."""
        return self._controller.zone_state(self._zone).volume

    @property
    def is_volume_muted(self) -> bool | None:
        """Return whether the zone is muted."""
        return self._controller.zone_state(self._zone).muted

    @property
    def source_list(self) -> list[str]:
        """Return the list of selectable sources."""
        return self._source_names

    @property
    def source(self) -> str | None:
        """Return the currently selected source."""
        number = self._controller.zone_state(self._zone).source
        if number is None:
            return None
        return f"Source {number}"

    async def async_turn_on(self) -> None:
        """Turn the zone on."""
        await self._controller.async_send(CMD_POWER, self._zone, POWER_ON)

    async def async_turn_off(self) -> None:
        """Turn the zone off."""
        await self._controller.async_send(CMD_POWER, self._zone, POWER_OFF)

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute or unmute the zone."""
        await self._controller.async_send(
            CMD_MUTE, self._zone, MUTE_ON if mute else MUTE_OFF
        )

    async def async_set_volume_level(self, volume: float) -> None:
        """Set the zone volume (0.0..1.0)."""
        from .protocol import level_to_volume

        await self._controller.async_send(
            CMD_VOLUME, self._zone, level_to_volume(volume)
        )

    async def async_volume_up(self) -> None:
        """Step the zone volume up."""
        await self._controller.async_send(CMD_VOLUME_UP, self._zone)

    async def async_volume_down(self) -> None:
        """Step the zone volume down."""
        await self._controller.async_send(CMD_VOLUME_DOWN, self._zone)

    async def async_select_source(self, source: str) -> None:
        """Select an input source (and turn the zone on)."""
        try:
            number = int(source.split()[-1])
        except (ValueError, IndexError):
            _LOGGER.warning("Unknown Axium source: %s", source)
            return
        source_byte = SOURCE_NUMBER_TO_BYTE.get(number)
        if source_byte is None:
            _LOGGER.warning("Unsupported Axium source number: %s", number)
            return
        await self._controller.async_send(
            CMD_SOURCE, self._zone, source_byte | SOURCE_FLAG_TURN_ON
        )
