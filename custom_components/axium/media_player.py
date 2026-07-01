"""Media player platform for Axium amplifier zones.

Each zone is a ``media_player`` with power, mute, volume and source control,
plus native Home Assistant grouping: joining zones links them on the amplifier
(``Link zones``), so you group/ungroup directly from the player card and the amp
keeps the members in sync.
"""

from __future__ import annotations

import logging

from datetime import datetime

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    RepeatMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    CMD_MUTE,
    CMD_POWER,
    CMD_SOURCE,
    CMD_VOLUME,
    CMD_VOLUME_DOWN,
    CMD_VOLUME_UP,
    DOMAIN,
    ID_KEY,
    MEDIA_NEXT,
    MEDIA_PAUSE,
    MEDIA_PLAY,
    MEDIA_PREVIOUS,
    MEDIA_SHUFFLE,
    MEDIA_SOURCE_BYTES,
    MEDIA_STOP,
    MUTE_OFF,
    MUTE_ON,
    NAME_KEY,
    POWER_OFF,
    POWER_ON,
    SOURCE_BYTE_TO_NAME,
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

# Added only while the zone is on a media-player source (AirPlay / Media Player).
SUPPORT_MEDIA = (
    MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.SHUFFLE_SET
    | MediaPlayerEntityFeature.REPEAT_SET
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Axium zone media players from a config entry."""
    controller: AxiumController = hass.data[DOMAIN][entry.entry_id]
    sources = get_sources(entry)
    source_ids = [item[ID_KEY] for item in sources]
    seed_names = {item[ID_KEY]: item[NAME_KEY] for item in sources}
    async_add_entities(
        AxiumZone(controller, entry, item[ZONE_KEY], item[NAME_KEY], source_ids, seed_names)
        for item in get_zones(entry)
    )


class AxiumZone(MediaPlayerEntity):
    """A single Axium amplifier zone."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False

    def __init__(
        self,
        controller: AxiumController,
        entry: ConfigEntry,
        zone: int,
        name: str,
        source_ids: list[int],
        seed_names: dict[int, str],
    ) -> None:
        """Initialise the zone entity."""
        self._controller = controller
        self._zone = zone
        self._source_ids = source_ids
        self._seed_names = seed_names
        self._media_position: int | None = None
        self._media_position_updated: datetime | None = None
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_zone_{zone}")},
            name=name,
            manufacturer="Axium",
            model="Zone",
            via_device=(DOMAIN, entry.entry_id),
        )

    async def async_added_to_hass(self) -> None:
        """Register for updates, expose the entity_id, and read current state."""
        self._controller.register_zone_entity(self._zone, self.entity_id)
        self.async_on_remove(
            self._controller.register_listener(self._zone, self._handle_update)
        )
        await self._controller.async_request_zone_state(self._zone)

    @callback
    def _handle_update(self) -> None:
        """Write state when the zone (or its media) changes."""
        source = self._media_source
        position = (
            self._controller.media_state(source).position
            if source is not None
            else None
        )
        if position != self._media_position:
            self._media_position = position
            self._media_position_updated = dt_util.utcnow()
        self.async_write_ha_state()

    @property
    def _media_source(self) -> int | None:
        """Return the current source byte if it is a media-player source."""
        byte = self._controller.zone_state(self._zone).source
        return byte if byte in MEDIA_SOURCE_BYTES else None

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        """Add transport features while a media source is selected."""
        if self._media_source is not None:
            return SUPPORT_AXIUM | SUPPORT_MEDIA
        return SUPPORT_AXIUM

    @property
    def available(self) -> bool:
        """Return whether the amplifier connection is up."""
        return self._controller.available

    @property
    def state(self) -> MediaPlayerState | None:
        """Return the playback/power state of the zone."""
        source = self._media_source
        if source is not None:
            media = self._controller.media_state(source)
            if media.playing:
                return MediaPlayerState.PLAYING
            if media.paused:
                return MediaPlayerState.PAUSED
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

    def _source_display(self, byte: int) -> str:
        """Return the display name for a source byte (live name preferred)."""
        return (
            self._controller.source_name(byte)
            or self._seed_names.get(byte)
            or SOURCE_BYTE_TO_NAME.get(byte)
            or f"Source 0x{byte:02X}"
        )

    @property
    def source_list(self) -> list[str]:
        """Return the selectable sources with their current names."""
        return [self._source_display(byte) for byte in self._source_ids]

    @property
    def source(self) -> str | None:
        """Return the currently selected source."""
        byte = self._controller.zone_state(self._zone).source
        return None if byte is None else self._source_display(byte)

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
        source_byte = next(
            (byte for byte in self._source_ids if self._source_display(byte) == source),
            None,
        )
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

    # -- now playing -----------------------------------------------------

    @property
    def media_title(self) -> str | None:
        """Title of the current track."""
        source = self._media_source
        return None if source is None else self._controller.media_state(source).title

    @property
    def media_artist(self) -> str | None:
        """Artist of the current track."""
        source = self._media_source
        return None if source is None else self._controller.media_state(source).artist

    @property
    def media_album_name(self) -> str | None:
        """Album of the current track."""
        source = self._media_source
        return None if source is None else self._controller.media_state(source).album

    @property
    def media_image_url(self) -> str | None:
        """Cover art URL for the current track."""
        source = self._media_source
        if source is None:
            return None
        art = self._controller.media_state(source).art
        if not art:
            return None
        if art.startswith(("http://", "https://")):
            return art
        return f"http://{self._controller.host}/artwork/{art}"

    @property
    def media_position(self) -> int | None:
        """Playback position in seconds."""
        source = self._media_source
        return None if source is None else self._controller.media_state(source).position

    @property
    def media_position_updated_at(self) -> datetime | None:
        """When the playback position was last updated."""
        return self._media_position_updated if self._media_source is not None else None

    @property
    def media_duration(self) -> int | None:
        """Track length in seconds."""
        source = self._media_source
        return None if source is None else self._controller.media_state(source).duration

    @property
    def shuffle(self) -> bool | None:
        """Whether shuffle is enabled."""
        source = self._media_source
        return None if source is None else self._controller.media_state(source).shuffle

    @property
    def repeat(self) -> RepeatMode | None:
        """Current repeat mode."""
        source = self._media_source
        if source is None:
            return None
        mode = self._controller.media_state(source).repeat
        return {
            "one": RepeatMode.ONE,
            "all": RepeatMode.ALL,
        }.get(mode, RepeatMode.OFF)

    async def async_media_play(self) -> None:
        """Resume playback."""
        await self._async_media_control(MEDIA_PLAY)

    async def async_media_pause(self) -> None:
        """Pause playback."""
        await self._async_media_control(MEDIA_PAUSE)

    async def async_media_stop(self) -> None:
        """Stop playback."""
        await self._async_media_control(MEDIA_STOP)

    async def async_media_next_track(self) -> None:
        """Skip to the next track."""
        await self._async_media_control(MEDIA_NEXT)

    async def async_media_previous_track(self) -> None:
        """Return to the previous track."""
        await self._async_media_control(MEDIA_PREVIOUS)

    async def async_set_shuffle(self, shuffle: bool) -> None:
        """Enable or disable shuffle."""
        await self._async_media_control(MEDIA_SHUFFLE, 1 if shuffle else 0)

    async def async_set_repeat(self, repeat: RepeatMode) -> None:
        """Set the repeat mode."""
        source = self._media_source
        if source is not None:
            await self._controller.async_set_repeat(source, str(repeat))

    async def _async_media_control(self, control: int, *extra: int) -> None:
        """Send a media control command for the active media source."""
        source = self._media_source
        if source is not None:
            await self._controller.async_media_control(source, control, *extra)
