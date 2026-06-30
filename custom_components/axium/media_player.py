"""Media player platform for Axium amplifier zones.

Each zone is a ``media_player`` with power, mute, volume and source control,
plus native Home Assistant grouping: joining zones links them on the amplifier
(``Link zones``), so you group/ungroup directly from the player card and the amp
keeps the members in sync.
"""

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
    DOMAIN,
    ID_KEY,
    MUTE_OFF,
    MUTE_ON,
    NAME_KEY,
    POWER_OFF,
    POWER_ON,
    SOURCE_FLAG_TURN_ON,
    ZONE_KEY,
)
from .controller import AxiumController
from .helpers import get_sources, get_zones
from .protocol import level_to_volume

_LOGGER = logging.getLogger(__name__)

SUPPORT_AXIUM = (
    MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.TURN_OFF
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.SELECT_SOURCE
    | MediaPlayerEntityFeature.GROUPING
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Axium zone media players from a config entry."""
    controller: AxiumController = hass.data[DOMAIN][entry.entry_id]
    sources = get_sources(entry)
    source_names = [item[NAME_KEY] for item in sources]
    name_by_byte = {item[ID_KEY]: item[NAME_KEY] for item in sources}
    byte_by_name = {item[NAME_KEY]: item[ID_KEY] for item in sources}
    async_add_entities(
        AxiumZone(
            controller,
            entry,
            item[ZONE_KEY],
            item[NAME_KEY],
            source_names,
            name_by_byte,
            byte_by_name,
        )
        for item in get_zones(entry)
    )


class AxiumZone(MediaPlayerEntity):
    """A single Axium amplifier zone."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = SUPPORT_AXIUM
    _attr_should_poll = False

    def __init__(
        self,
        controller: AxiumController,
        entry: ConfigEntry,
        zone: int,
        name: str,
        source_names: list[str],
        name_by_byte: dict[int, str],
        byte_by_name: dict[str, int],
    ) -> None:
        """Initialise the zone entity."""
        self._controller = controller
        self._zone = zone
        self._attr_source_list = source_names
        self._name_by_byte = name_by_byte
        self._byte_by_name = byte_by_name
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_zone_{zone}")},
            name=name,
            manufacturer="Axium",
            model="Zone",
            via_device=(DOMAIN, entry.entry_id),
        )

    async def async_added_to_hass(self) -> None:
        """Register for updates and expose this zone's entity_id."""
        self._controller.register_zone_entity(self._zone, self.entity_id)
        self.async_on_remove(
            self._controller.register_listener(self._zone, self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        """Write state when the zone (or its group) changes."""
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
    def source(self) -> str | None:
        """Return the currently selected source."""
        byte = self._controller.zone_state(self._zone).source
        return None if byte is None else self._name_by_byte.get(byte)

    @property
    def group_members(self) -> list[str]:
        """Return the entity_ids of the zones linked with this one."""
        return [
            entity_id
            for zone in self._controller.group_members(self._zone)
            if (entity_id := self._controller.zone_entity_id(zone))
        ]

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
        source_byte = self._byte_by_name.get(source)
        if source_byte is None:
            _LOGGER.warning("Unknown Axium source: %s", source)
            return
        await self._controller.async_send(
            CMD_SOURCE, self._zone, source_byte | SOURCE_FLAG_TURN_ON
        )

    async def async_join_players(self, group_members: list[str]) -> None:
        """Link this zone with the given zones into a group on the amplifier."""
        zones = {self._zone}
        for entity_id in group_members:
            zone = self._controller.zone_for_entity_id(entity_id)
            if zone is not None:
                zones.add(zone)
        await self._controller.async_join(zones)

    async def async_unjoin_player(self) -> None:
        """Remove this zone from its group on the amplifier."""
        await self._controller.async_unjoin(self._zone)
