"""Media player platform for Axium amplifier zones.

Each zone is a ``media_player`` with power, mute, volume and source control,
plus native Home Assistant grouping: joining zones links them on the amplifier
(``Link zones``), so you group/ungroup directly from the player card and the amp
keeps the members in sync.
"""

from __future__ import annotations

import logging

from datetime import datetime
from typing import Any

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
    UNIT_KEY,
    POWER_OFF,
    POWER_ON,
    SOURCE_BYTE_TO_NAME,
    SOURCE_FLAG_TURN_ON,
    ZONE_KEY,
)
from .controller import AxiumController
from .helpers import (
    amp_zone_positions,
    get_presets,
    get_sources,
    get_units,
    get_zones,
    zone_device_model,
)
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
    presets = get_presets(entry)
    units = get_units(entry)
    primary_uid = next((u[UNIT_KEY] for u in units if u.get("primary")), None)
    zones_cfg = get_zones(entry)
    # Physical amp channel (1..N within each amp) — matches the amp web app's
    # "Amp Zone", independent of the stack-wide zone number a stacked amp gets.
    positions = amp_zone_positions(zones_cfg)

    def _via(unit_id: int | None) -> tuple[str, str]:
        """The device a zone nests under — its owning amp. The primary amp is its
        own device ("…_amp_primary"), separate from the logical hub."""
        if unit_id is None or not units or unit_id == primary_uid:
            return (DOMAIN, f"{entry.entry_id}_amp_primary")
        return (DOMAIN, f"{entry.entry_id}_unit_{unit_id}")

    def _model_code(unit_id: int | None) -> int | None:
        """A zone's amp device-type code (fall back to the primary amp)."""
        unit = controller.unit(unit_id) if unit_id is not None else None
        if unit is None:
            unit = controller.unit(controller.primary_unit_id)
        return unit.model_code if unit else None

    def _zone_model(item: dict) -> str:
        amp_zone = positions.get(item[ZONE_KEY], item[ZONE_KEY])
        return zone_device_model(_model_code(item.get(UNIT_KEY)), amp_zone)

    async_add_entities(
        AxiumZone(
            controller,
            entry,
            item[ZONE_KEY],
            item[NAME_KEY],
            source_ids,
            seed_names,
            presets,
            _via(item.get(UNIT_KEY)),
            _zone_model(item),
        )
        for item in zones_cfg
    )


class AxiumZone(MediaPlayerEntity):
    """A single Axium amplifier zone."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    # These list attributes are static config, not history worth recording.
    _unrecorded_attributes = frozenset({"source_ids", "axium_presets", "zone_number"})

    def __init__(
        self,
        controller: AxiumController,
        entry: ConfigEntry,
        zone: int,
        name: str,
        source_ids: list[int],
        seed_names: dict[int, str],
        presets: list[dict] | None = None,
        via_device: tuple[str, str] | None = None,
        model: str | None = None,
    ) -> None:
        """Initialise the zone entity."""
        self._controller = controller
        self._zone = zone
        self._source_ids = source_ids
        self._seed_names = seed_names
        self._presets = presets or []
        self._media_position: int | None = None
        self._media_position_updated: datetime | None = None
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone}"
        # The model is the physical amp channel (+ "Pre-out" for line-level
        # zones) — its subtitle in the devices list, matching the amp web app.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_zone_{zone}")},
            name=name,
            manufacturer="Axium",
            model=model or "Zone",
            via_device=via_device or (DOMAIN, f"{entry.entry_id}_amp_primary"),
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
        """Return the playback/power state of the zone.

        Power is checked first: the amp's single internal Media Player is a
        *shared* source, and while it plays the amp reports every zone's source
        as that player (turn-on bit clear, so they stay off). Reading the global
        media state before power would make those powered-off zones show PLAYING
        — which lit up every zone's Media Player cell in the matrix. An off zone
        is OFF regardless of what the shared player is doing.
        """
        power = self._controller.zone_state(self._zone).power
        if power is None:
            return None
        if not power:
            return MediaPlayerState.OFF
        source = self._media_source
        if source is not None:
            media = self._controller.media_state(source)
            if media.playing:
                return MediaPlayerState.PLAYING
            if media.paused:
                return MediaPlayerState.PAUSED
        return MediaPlayerState.ON

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

    def _effective_source_ids(self) -> list[int]:
        """Configured sources, plus any internal media player the amp reports.

        The internal media player (e.g. source 0x12) isn't in the amp's named
        source table, so it isn't discovered at setup — but it is selectable and
        streams (UPnP push / network shares / built-in services). Appending it
        here lets a zone be routed to it, which lights up now-playing/transport.
        """
        ids = list(self._source_ids)
        for byte in self._controller.media_sources():
            if byte not in ids:
                ids.append(byte)
        return ids

    @property
    def source_list(self) -> list[str]:
        """Return the selectable sources with their current names."""
        return [self._source_display(byte) for byte in self._effective_source_ids()]

    @property
    def source(self) -> str | None:
        """Return the currently selected source."""
        byte = self._controller.zone_state(self._zone).source
        return None if byte is None else self._source_display(byte)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the stable source ids and the hub's zone presets.

        ``source_ids`` are the amplifier's own source identifiers, parallel to
        ``source_list`` (``source_ids[i]`` matches ``source_list[i]``) — the
        dashboard card stores the id, not the display name, so renaming a source
        on the amp doesn't break a card. ``axium_presets`` are the hub-wide zone
        presets a source card can activate.
        """
        return {
            "source_ids": self._effective_source_ids(),
            "axium_presets": self._presets,
            "zone_number": self._zone,
        }

    @property
    def group_members(self) -> list[str]:
        """Return the entity_ids of the zones linked with this one."""
        return [
            entity_id
            for zone in self._controller.group_members(self._zone)
            if (entity_id := self._controller.zone_entity_id(zone))
        ]

    async def _refresh(self) -> None:
        """Re-read this zone's power/mute/volume/source from the amplifier.

        Real amplifiers send no notification after a set (only the simulator
        does), so without this an on/off/source change never reflects in HA and
        the card looks unresponsive. Also catches side effects (selecting a
        source turns the zone on).
        """
        await self._controller.async_request_zone_state(self._zone)

    async def async_turn_on(self) -> None:
        """Turn the zone on."""
        await self._controller.async_send(CMD_POWER, self._zone, POWER_ON)
        await self._refresh()

    async def async_turn_off(self) -> None:
        """Turn the zone off."""
        await self._controller.async_send(CMD_POWER, self._zone, POWER_OFF)
        await self._refresh()

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute or unmute the zone."""
        await self._controller.async_send(
            CMD_MUTE, self._zone, MUTE_ON if mute else MUTE_OFF
        )
        await self._refresh()

    async def async_set_volume_level(self, volume: float) -> None:
        """Set the zone volume (0.0..1.0)."""
        await self._controller.async_send(
            CMD_VOLUME, self._zone, level_to_volume(volume)
        )
        await self._refresh()

    async def async_volume_up(self) -> None:
        """Step the zone volume up."""
        await self._controller.async_send(CMD_VOLUME_UP, self._zone)
        await self._refresh()

    async def async_volume_down(self) -> None:
        """Step the zone volume down."""
        await self._controller.async_send(CMD_VOLUME_DOWN, self._zone)
        await self._refresh()

    async def async_select_source(self, source: str) -> None:
        """Select an input source (and turn the zone on)."""
        source_byte = next(
            (
                byte
                for byte in self._effective_source_ids()
                if self._source_display(byte) == source
            ),
            None,
        )
        if source_byte is None:
            _LOGGER.warning("Unknown Axium source: %s", source)
            return
        await self._controller.async_send(
            CMD_SOURCE, self._zone, source_byte | SOURCE_FLAG_TURN_ON
        )
        await self._refresh()

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
