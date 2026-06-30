"""Media player platform for Axium amplifier zones and zone groups."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
import logging
from statistics import fmean

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import slugify

from .const import (
    CMD_MUTE,
    CMD_POWER,
    CMD_SOURCE,
    CMD_VOLUME,
    CMD_VOLUME_DOWN,
    CMD_VOLUME_UP,
    DEFAULT_SOURCE_LIST,
    DOMAIN,
    MUTE_OFF,
    MUTE_ON,
    NAME_KEY,
    POWER_OFF,
    POWER_ON,
    SOURCE_BYTE_TO_NAME,
    SOURCE_FLAG_TURN_ON,
    SOURCE_NAME_TO_BYTE,
    ZONE_KEY,
    ZONES_KEY,
)
from .controller import AxiumController
from .helpers import get_groups, get_zones
from .protocol import level_to_volume

_LOGGER = logging.getLogger(__name__)

SUPPORT_AXIUM = (
    MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.TURN_OFF
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.SELECT_SOURCE
)

SOURCE_NAMES = DEFAULT_SOURCE_LIST


def _source_byte(source: str) -> int | None:
    """Return the Source Selection data byte for a source name."""
    return SOURCE_NAME_TO_BYTE.get(source)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Axium zone and group media players from a config entry."""
    controller: AxiumController = hass.data[DOMAIN][entry.entry_id]

    entities: list[MediaPlayerEntity] = [
        AxiumZone(controller, entry, item[ZONE_KEY], item[NAME_KEY])
        for item in get_zones(entry)
    ]
    entities.extend(
        AxiumGroup(controller, entry, group[NAME_KEY], group[ZONES_KEY])
        for group in get_groups(entry)
    )

    async_add_entities(entities)


class _AxiumBase(MediaPlayerEntity):
    """Shared behaviour for Axium zone and group entities."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = SUPPORT_AXIUM
    _attr_should_poll = False
    _attr_source_list = SOURCE_NAMES

    def __init__(self, controller: AxiumController) -> None:
        """Store the controller."""
        self._controller = controller

    @property
    def _zones(self) -> list[int]:
        """Return the zone numbers this entity controls."""
        raise NotImplementedError

    @property
    def available(self) -> bool:
        """Return whether the amplifier connection is up."""
        return self._controller.available

    async def async_added_to_hass(self) -> None:
        """Subscribe to controller updates for every controlled zone."""
        for zone in self._zones:
            self.async_on_remove(
                self._controller.register_listener(zone, self._handle_update)
            )

    @callback
    def _handle_update(self) -> None:
        """Write state when a controlled zone changes."""
        self.async_write_ha_state()

    async def _send_all(self, command: int, *data: int) -> None:
        """Send a command to every controlled zone."""
        await asyncio.gather(
            *(self._controller.async_send(command, zone, *data) for zone in self._zones)
        )

    async def async_turn_on(self) -> None:
        """Turn the controlled zone(s) on."""
        await self._send_all(CMD_POWER, POWER_ON)

    async def async_turn_off(self) -> None:
        """Turn the controlled zone(s) off."""
        await self._send_all(CMD_POWER, POWER_OFF)

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute or unmute the controlled zone(s)."""
        await self._send_all(CMD_MUTE, MUTE_ON if mute else MUTE_OFF)

    async def async_set_volume_level(self, volume: float) -> None:
        """Set the volume of the controlled zone(s)."""
        await self._send_all(CMD_VOLUME, level_to_volume(volume))

    async def async_volume_up(self) -> None:
        """Step the volume up on the controlled zone(s)."""
        await self._send_all(CMD_VOLUME_UP)

    async def async_volume_down(self) -> None:
        """Step the volume down on the controlled zone(s)."""
        await self._send_all(CMD_VOLUME_DOWN)

    async def async_select_source(self, source: str) -> None:
        """Select an input source (and turn the zone(s) on)."""
        source_byte = _source_byte(source)
        if source_byte is None:
            _LOGGER.warning("Unknown Axium source: %s", source)
            return
        await self._send_all(CMD_SOURCE, source_byte | SOURCE_FLAG_TURN_ON)


class AxiumZone(_AxiumBase):
    """Representation of a single named Axium amplifier zone."""

    def __init__(
        self,
        controller: AxiumController,
        entry: ConfigEntry,
        zone: int,
        name: str,
    ) -> None:
        """Initialise the zone entity."""
        super().__init__(controller)
        self._zone = zone
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_zone_{zone}")},
            name=name,
            manufacturer="Axium",
            model="Zone",
            via_device=(DOMAIN, entry.entry_id),
        )

    @property
    def _zones(self) -> list[int]:
        """Return the single controlled zone."""
        return [self._zone]

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
        return None if byte is None else SOURCE_BYTE_TO_NAME.get(byte)


class AxiumGroup(_AxiumBase):
    """A user-defined group of zones controlled together."""

    def __init__(
        self,
        controller: AxiumController,
        entry: ConfigEntry,
        name: str,
        zones: Iterable[int],
    ) -> None:
        """Initialise the group entity."""
        super().__init__(controller)
        self._entry_id = entry.entry_id
        self._group_zones = list(zones)
        slug = slugify(name)
        self._attr_unique_id = f"{entry.entry_id}_group_{slug}"
        self._attr_group_members: list[str] = []
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_group_{slug}")},
            name=name,
            manufacturer="Axium",
            model="Zone Group",
            via_device=(DOMAIN, entry.entry_id),
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to member zones and resolve their entity IDs."""
        await super().async_added_to_hass()
        registry = er.async_get(self.hass)
        self._attr_group_members = [
            entity_id
            for zone in self._group_zones
            if (
                entity_id := registry.async_get_entity_id(
                    "media_player", DOMAIN, f"{self._entry_id}_zone_{zone}"
                )
            )
            is not None
        ]

    @property
    def _zones(self) -> list[int]:
        """Return the member zone numbers."""
        return self._group_zones

    def _states(self) -> list:
        """Return the cached state of every member zone."""
        return [self._controller.zone_state(zone) for zone in self._group_zones]

    @property
    def state(self) -> MediaPlayerState | None:
        """ON if any member is on, OFF if all known members are off."""
        powers = [s.power for s in self._states() if s.power is not None]
        if not powers:
            return None
        return MediaPlayerState.ON if any(powers) else MediaPlayerState.OFF

    @property
    def volume_level(self) -> float | None:
        """Return the average volume of members reporting one."""
        volumes = [s.volume for s in self._states() if s.volume is not None]
        return fmean(volumes) if volumes else None

    @property
    def is_volume_muted(self) -> bool | None:
        """Muted only when every reporting member is muted."""
        mutes = [s.muted for s in self._states() if s.muted is not None]
        return all(mutes) if mutes else None

    @property
    def source(self) -> str | None:
        """Return the source only when all members agree."""
        sources = {s.source for s in self._states() if s.source is not None}
        if len(sources) == 1:
            return SOURCE_BYTE_TO_NAME.get(sources.pop())
        return None

    @property
    def extra_state_attributes(self) -> dict[str, list[int]]:
        """Expose the member zone numbers."""
        return {"zones": self._group_zones}
