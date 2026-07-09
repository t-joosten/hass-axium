"""Persistent TCP connection and state cache for an Axium amplifier.

A single :class:`AxiumController` owns the socket to the amplifier. It keeps the
connection open, sends commands, and parses the notifications the amplifier
emits whenever a zone changes state (the protocol re-uses command bytes as
notifications). Entities register a callback per zone and are pushed updates as
they arrive, giving a ``local_push`` integration.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
import logging
import math

from . import protocol
from .const import (
    AUDIO_DELAY_STEP,
    AUTO_POWER_ON_BIT,
    AUTO_STANDBY_BIT,
    CLIP_CLIPPED,
    CMD_AUDIO_DELAY,
    CMD_AUTO_POWER,
    CMD_BALANCE,
    CMD_BASS,
    CMD_CLIPPING,
    CMD_LINK_ZONES,
    CMD_MAX_VOLUME,
    CMD_POWER_ON_VOLUME,
    CMD_MEDIA_CONTROL,
    CMD_MEDIA_STATUS,
    CMD_MEDIA_STATUS_REQUEST,
    CMD_NETWORK_SETTINGS,
    NET_FLAG_STATIC,
    NET_SETTING_IP_FLAGS,
    NET_SETTING_IP_FLAGS_REQUEST,
    CMD_MUTE,
    CMD_NO_OP,
    CMD_POWER,
    CMD_PRESET,
    CMD_PRESET_NAME,
    CMD_PRESET_NAME_REQUEST,
    CMD_REQUEST_DEVICE_INFO,
    CMD_REQUEST_EXTENDED_INFO,
    CMD_SOURCE,
    CMD_SOURCE_GAIN,
    CMD_SOURCE_NAME,
    CMD_SPECIAL_FEATURES,
    CMD_TREBLE,
    CMD_VOLUME,
    CMD_ZONE_GAIN,
    CMD_ZONE_NAME,
    DEVICE_INFO_LIST_ZONES,
    DEVICE_INFO_NO_EXPANSION_REPLY,
    DEVICE_INFO_REPLY_ON_PORT_ONLY,
    DEVICE_MODELS,
    DEVICE_TYPES,
    LINK_OPTIONS_DEFAULT,
    LINK_REQUEST_GROUPED,
    MEDIA_REPEAT,
    MEDIA_SOURCE_BYTES,
    MS_ALBUM,
    MS_ART,
    MS_ARTIST,
    MS_FLAG_ACTIVE,
    MS_FLAG_AVAILABLE,
    MS_FLAG_PAUSED,
    MS_FLAG_REPEAT_DISC,
    MS_FLAG_REPEAT_TRACK,
    MS_FLAG_SHUFFLE,
    MS_FLAGS,
    MS_LENGTH,
    MS_POSITION,
    MS_TITLE,
    POWER_OFF_VALUES,
    POWER_ON_VALUES,
    PRESET_COUNT,
    REPEAT_ALL,
    REPEAT_OFF,
    REPEAT_TRACK,
    RESP_DEVICE_INFO,
    RESP_EXTENDED_DEVICE_INFO,
    SOURCE_ID_MASK,
    SOURCE_NAME_FLAG_DISABLED,
    SPECIAL_LOUDNESS_BIT,
    SPECIAL_MONO_BIT,
    VOLUME_MAX,
    ZONE_ALL,
)

_LOGGER = logging.getLogger(__name__)

_RECONNECT_DELAY = 5.0
_MAX_RECONNECT_DELAY = 60.0
_CONNECT_TIMEOUT = 10.0


@dataclass
class ZoneState:
    """Cached state for a single amplifier zone."""

    power: bool | None = None
    muted: bool | None = None
    volume: float | None = None
    source: int | None = None  # masked source data byte (see SOURCE_BYTE_TO_NAME)
    name: str | None = None
    available: bool = False
    bass: int | None = None
    treble: int | None = None
    balance: int | None = None
    max_volume: int | None = None  # percent
    power_on_volume: int | None = None  # percent
    audio_delay: int | None = None  # milliseconds
    zone_gain: int | None = None  # dB
    loudness: bool | None = None
    mono: bool | None = None
    special: tuple[int, int] = (0, 0)  # cached 0x0C bytes (byte1, byte2)


@dataclass
class MediaState:
    """Cached now-playing state for a media source."""

    available: bool = False
    playing: bool = False
    paused: bool = False
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    art: str | None = None
    position: int | None = None
    duration: int | None = None
    shuffle: bool = False
    repeat: str = "off"  # off | one | all


@dataclass
class NetworkConfig:
    """The amplifier's network settings (command 0x3A, setting 0x03)."""

    flags: int = 0
    ip: bytes = b"\x00\x00\x00\x00"
    subnet: bytes = b"\x00\x00\x00\x00"
    dns: bytes = b"\x00\x00\x00\x00"
    router: bytes = b"\x00\x00\x00\x00"


@dataclass
class AxiumDeviceInfo:
    """Identity reported by the amplifier (command 0x14 response)."""

    device_type: str | None = None
    model: str | None = None
    model_code: int | None = None
    firmware_major: int | None = None
    unit_id: int | None = None
    zones: list[int] = field(default_factory=list)
    link_groups: list[list[int]] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)  # {id, name, enabled}


CallbackType = Callable[[], None]
DeviceInfoCallback = Callable[[AxiumDeviceInfo], None]


def parse_device_info(data: bytes) -> AxiumDeviceInfo | None:
    """Parse the data bytes of a Request Device information response (0x94).

    Layout: device type, firmware major version, device-specific model code,
    then an optional two-byte unit ID. Returns ``None`` if too short.
    """
    if len(data) < 3:
        return None
    # For amplifiers (device type 0x00) any bytes past the unit ID are the
    # optional zone list (each zone is 0..95), present when the request asked
    # for it. Filter to valid zone numbers defensively.
    zones: list[int] = []
    if data[0] == 0x00 and len(data) > 5:
        zones = [b for b in data[5:] if 0 <= b <= 95]
    return AxiumDeviceInfo(
        device_type=DEVICE_TYPES.get(data[0]),
        model=DEVICE_MODELS.get(data[2]),
        model_code=data[2],
        firmware_major=data[1],
        unit_id=(data[3] << 8 | data[4]) if len(data) >= 5 else None,
        zones=zones,
    )


def parse_source_name(data: bytes) -> dict | None:
    """Parse a Source Name and Options (0x29) report into a dict.

    Layout: source ID, a (legacy) byte, device-specific byte, flags byte, then
    the UTF-8 name. Returns ``{id, name, enabled, device, flags}`` or ``None``
    if it is not a full report (a 0/1-byte frame is a request, not a report).
    """
    if len(data) < 4:
        return None
    flags = data[3]
    name = data[4:].decode("utf-8", errors="replace").rstrip("\x00")
    return {
        "id": data[0],
        "name": name,
        "enabled": not flags & SOURCE_NAME_FLAG_DISABLED,
        "device": data[2],
        "flags": flags,
    }


def parse_link_group(data: bytes) -> list[int] | None:
    """Parse a Link zones (0x30) frame into its member zone numbers.

    Layout: an options byte, optionally a 4-byte group identifier (when the
    options byte has bit 7 set), then the zone numbers (0..95). Returns the
    members only for real groups (2+ zones), else ``None``.
    """
    if not data:
        return None
    options = data[0]
    rest = data[5:] if options & 0x80 else data[1:]
    zones = sorted({b for b in rest if 0 <= b <= 95})
    return zones if len(zones) >= 2 else None


class AxiumController:
    """Manage the connection to an Axium amplifier and dispatch updates."""

    def __init__(self, host: str, port: int) -> None:
        """Initialise the controller for ``host``/``port``."""
        self._host = host
        self._port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._write_lock = asyncio.Lock()
        self._run_task: asyncio.Task | None = None
        self._closing = False
        self._connected = asyncio.Event()
        self._states: dict[int, ZoneState] = {}
        self._listeners: dict[int, list[CallbackType]] = {}
        self._device_info: AxiumDeviceInfo | None = None
        self._device_info_callback: DeviceInfoCallback | None = None
        # Live zone-link state: list of (member zones, options). Mirrors the
        # amplifier's groups, populated on connect and kept up to date.
        self._links: list[tuple[set[int], int]] = []
        # Map of zone number -> media_player entity_id, for group membership.
        self._zone_entity_ids: dict[int, str] = {}
        # Live source names (id -> name) and per-source (device, flags) so name
        # writes preserve options.
        self._source_names: dict[int, str] = {}
        self._source_meta: dict[int, tuple[int, int]] = {}
        # Per-source gain (id -> dB).
        self._source_gain: dict[int, int] = {}
        # Zones for which the amp's special-features bytes (0x0C) are known, so
        # a loudness/mono toggle never clobbers other bits it hasn't read yet.
        self._special_known: set[int] = set()
        # Extended device info (firmware string, MAC) callback + cache.
        self._extended_info_callback: Callable[[str | None, str | None], None] | None = (
            None
        )
        self._firmware: str | None = None
        self._mac: str | None = None
        # Now-playing state keyed by media source data byte.
        self._media: dict[int, MediaState] = {}
        # Media-player source bytes the amp actually reports (it answers a media
        # status request for a real internal player, e.g. 0x12; not for absent
        # ones like AirPlay 0x10). Exposed as selectable sources.
        self._media_sources: set[int] = set()
        # The amp's network settings (0x3A/03), read at connect; lets HA pin a
        # static IP so a reboot's new DHCP lease can't break the connection.
        self._network: NetworkConfig | None = None
        # Diagnostics / amp-wide state.
        self._diag_listeners: list[CallbackType] = []
        self._temperature: int | None = None
        self._peak_temperature: int | None = None
        self._clipping: bool = False
        self._clipping_source: int | None = None
        # Auto power on/off (0x16): cached options bitfield + standby exponent.
        self._auto_power_options: int = 0
        self._auto_power_standby_n: int = 0
        # Preset (0x1E) selection + names (index 1..15 -> name).
        self._preset_current: int = 0
        self._preset_names: dict[int, str] = {}

    @property
    def host(self) -> str:
        """Return the amplifier host."""
        return self._host

    @property
    def device_info(self) -> AxiumDeviceInfo | None:
        """Return the last reported amplifier identity, if known."""
        return self._device_info

    def set_device_info_callback(self, callback: DeviceInfoCallback) -> None:
        """Register a callback invoked when device identity is reported."""
        self._device_info_callback = callback

    @property
    def available(self) -> bool:
        """Return whether the socket is currently connected."""
        return self._connected.is_set()

    def zone_state(self, zone: int) -> ZoneState:
        """Return (creating if needed) the cached state for ``zone``."""
        return self._states.setdefault(zone, ZoneState())

    def register_listener(self, zone: int, callback: CallbackType) -> Callable[[], None]:
        """Register ``callback`` to be invoked when ``zone`` changes.

        Returns a function that unregisters the callback.
        """
        self._listeners.setdefault(zone, []).append(callback)

        def _remove() -> None:
            listeners = self._listeners.get(zone)
            if listeners and callback in listeners:
                listeners.remove(callback)

        return _remove

    async def async_start(self) -> None:
        """Start the background connection loop and wait for first connect."""
        self._closing = False
        self._run_task = asyncio.ensure_future(self._run())
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=_CONNECT_TIMEOUT)
        except asyncio.TimeoutError as err:
            await self.async_stop()
            raise ConnectionError(
                f"Timed out connecting to Axium amplifier at {self._host}:{self._port}"
            ) from err

    async def async_stop(self) -> None:
        """Close the connection and stop the background loop."""
        self._closing = True
        if self._run_task:
            self._run_task.cancel()
            try:
                await self._run_task
            except asyncio.CancelledError:
                pass
            self._run_task = None
        await self._close_socket()

    async def _run(self) -> None:
        """Connect, read, and reconnect with backoff until stopped."""
        delay = _RECONNECT_DELAY
        while not self._closing:
            try:
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self._host, self._port),
                    timeout=_CONNECT_TIMEOUT,
                )
            except (OSError, asyncio.TimeoutError) as err:
                _LOGGER.debug(
                    "Axium connection to %s:%s failed: %s; retrying in %.0fs",
                    self._host,
                    self._port,
                    err,
                    delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, _MAX_RECONNECT_DELAY)
                continue

            _LOGGER.info("Connected to Axium amplifier at %s:%s", self._host, self._port)
            delay = _RECONNECT_DELAY
            self._connected.set()
            self._links = []
            self._notify_all()
            # Reset the amplifier's receive parser in case of stray bytes.
            await self.async_send(CMD_NO_OP, 0x00, 0x00)
            await self._request_device_info()
            await self._request_link_groups()
            await self._request_source_names()
            await self._request_preset_names()
            await self._request_media_sources()
            # Network config needs the unit id, so it is requested from
            # _handle_device_info once the device-info reply has been parsed.
            # Re-read known zones' state (covers reconnects; on first connect
            # entities request it themselves as they are added).
            for zone in list(self._zone_entity_ids):
                await self.async_request_zone_state(zone)
            try:
                await self._read_loop()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - log and reconnect
                _LOGGER.debug("Axium read loop ended: %s", err)
            finally:
                self._connected.clear()
                self._mark_unavailable()
                await self._close_socket()
                self._notify_all()

            if not self._closing:
                await asyncio.sleep(delay)

    async def _read_loop(self) -> None:
        """Read newline-terminated frames and dispatch them."""
        assert self._reader is not None
        while not self._closing:
            line = await self._reader.readline()
            if not line:
                raise ConnectionError("Connection closed by amplifier")
            frame = protocol.decode(line)
            if frame is not None and len(frame) >= 2:
                self._handle_frame(frame)

    def _handle_frame(self, frame: bytes) -> None:
        """Update the state cache from an incoming command/notification."""
        command = frame[0]
        zone = protocol.decode_zone(frame[1])
        data = frame[2:]
        state = self.zone_state(zone)
        changed = False

        if command == CMD_POWER and data:
            value = data[0]
            if value in POWER_ON_VALUES:
                state.power, changed = True, True
            elif value in POWER_OFF_VALUES:
                state.power, changed = False, True
        elif command == CMD_MUTE and data:
            state.muted = data[0] == 0x00
            changed = True
        elif command == CMD_VOLUME and data:
            state.volume = protocol.volume_to_level(data[0])
            changed = True
        elif command == CMD_SOURCE and data:
            new_source = data[0] & SOURCE_ID_MASK
            if new_source != state.source and new_source in MEDIA_SOURCE_BYTES:
                # Pull now-playing details when a media source is selected.
                asyncio.ensure_future(self.async_request_media_status(new_source))
            state.source = new_source
            if data[0] & 0x80:  # bit 7 set means the zone is turned on
                state.power = True
            changed = True
        elif command == CMD_BASS and data:
            state.bass = protocol.from_signed_byte(data[0])
            changed = True
        elif command == CMD_TREBLE and data:
            state.treble = protocol.from_signed_byte(data[0])
            changed = True
        elif command == CMD_BALANCE and data:
            state.balance = protocol.from_signed_byte(data[0])
            changed = True
        elif command == CMD_MAX_VOLUME and data:
            state.max_volume = round(data[0] / VOLUME_MAX * 100)
            changed = True
        elif command == CMD_POWER_ON_VOLUME and data:
            state.power_on_volume = round(data[0] / VOLUME_MAX * 100)
            changed = True
        elif command == CMD_AUDIO_DELAY and data:
            state.audio_delay = data[0] * AUDIO_DELAY_STEP
            changed = True
        elif command == CMD_ZONE_GAIN and data:
            state.zone_gain = protocol.from_signed_byte(data[0])
            changed = True
        elif command == CMD_SPECIAL_FEATURES and data:
            byte2 = data[1] if len(data) >= 2 else state.special[1]
            state.special = (data[0], byte2)
            state.loudness = bool(data[0] & SPECIAL_LOUDNESS_BIT)
            state.mono = bool(byte2 & SPECIAL_MONO_BIT)
            self._special_known.add(zone)
            changed = True
        elif command == CMD_SOURCE_GAIN and len(data) >= 2:
            self._source_gain[data[0]] = data[1]
            self._notify_diagnostics()
            return
        elif command == CMD_ZONE_NAME and data:
            state.name = data.decode("utf-8", errors="replace").rstrip("\x00") or None
            changed = True
        elif command == RESP_DEVICE_INFO:
            self._handle_device_info(data)
            return
        elif command == CMD_LINK_ZONES:
            self._update_link(data)
            return
        elif command == CMD_SOURCE_NAME:
            info = parse_source_name(data)
            if info is not None:
                self._source_meta[info["id"]] = (info["device"], info["flags"])
                if info["name"]:
                    self._source_names[info["id"]] = info["name"]
                self._notify_all()
                self._notify_diagnostics()
            return
        elif command == CMD_MEDIA_STATUS and len(data) >= 2:
            self._handle_media_status(data)
            return
        elif command == CMD_AUTO_POWER and len(data) >= 4:
            self._auto_power_options = data[2]
            self._auto_power_standby_n = data[3]
            self._notify_diagnostics()
            return
        elif command == CMD_PRESET and data:
            self._preset_current = data[1] if len(data) >= 2 else data[0] & 0x0F
            self._notify_diagnostics()
            return
        elif command == CMD_PRESET_NAME and len(data) >= 2:
            name = data[1:].decode("utf-8", errors="replace").rstrip("\x00")
            if name:
                self._preset_names[data[0]] = name
            self._notify_diagnostics()
            return
        elif command == CMD_CLIPPING and data:
            self._clipping = data[0] == CLIP_CLIPPED
            self._clipping_source = data[1] if len(data) >= 2 else None
            self._notify_diagnostics()
            return
        elif command == RESP_EXTENDED_DEVICE_INFO and len(data) >= 9:
            self._temperature = protocol.from_signed_byte(data[7])
            self._peak_temperature = protocol.from_signed_byte(data[8])
            self._firmware = f"{data[4]}.{data[5]}.{data[6]}"
            if len(data) >= 19:
                self._mac = ":".join(f"{b:02X}" for b in data[13:19])
            if self._extended_info_callback is not None:
                self._extended_info_callback(self._firmware, self._mac)
            self._notify_diagnostics()
            return
        elif (
            command == CMD_NETWORK_SETTINGS
            and len(data) >= 4
            and data[2] == NET_SETTING_IP_FLAGS
        ):
            self._handle_network_settings(data)
            return

        if changed:
            state.available = True
            self._notify(zone)

    def _handle_device_info(self, data: bytes) -> None:
        """Parse a Request Device information response (command 0x94).

        Layout (data bytes, after command + zone): device type, firmware major
        version, device-specific model code, then a two-byte unit ID.
        """
        info = parse_device_info(data)
        if info is None:
            return
        self._device_info = info
        _LOGGER.debug(
            "Axium device info: type=%s model=%s fw=%s",
            info.device_type,
            info.model or f"code 0x{info.model_code:02X}",
            info.firmware_major,
        )
        if self._device_info_callback is not None:
            self._device_info_callback(info)
        # Now that the unit id is known, fetch unit-scoped details.
        asyncio.ensure_future(self._request_auto_power())
        asyncio.ensure_future(self.async_request_extended_info())
        asyncio.ensure_future(self._request_network_config())

    def _handle_media_status(self, data: bytes) -> None:
        """Update now-playing state from a Media Status (0x3E) notification."""
        source = data[0]
        param = data[1]
        value = data[2:]
        media = self._media.setdefault(source, MediaState())
        # The amp only replies here for media players it actually has, so a
        # reply reveals a real, selectable internal source.
        new_media_source = (
            source in MEDIA_SOURCE_BYTES and source not in self._media_sources
        )
        if new_media_source:
            self._media_sources.add(source)

        def _text() -> str | None:
            return value.decode("utf-8", errors="replace").rstrip("\x00") or None

        if param == MS_FLAGS and value:
            flags = value[0]
            media.available = bool(flags & MS_FLAG_AVAILABLE)
            media.paused = bool(flags & MS_FLAG_PAUSED)
            media.playing = bool(flags & MS_FLAG_ACTIVE) and not media.paused
            media.shuffle = bool(flags & MS_FLAG_SHUFFLE)
            if flags & MS_FLAG_REPEAT_TRACK:
                media.repeat = "one"
            elif flags & MS_FLAG_REPEAT_DISC:
                media.repeat = "all"
            else:
                media.repeat = "off"
        elif param == MS_ARTIST:
            media.artist = _text()
        elif param == MS_ALBUM:
            media.album = _text()
        elif param == MS_TITLE:
            media.title = _text()
        elif param == MS_ART:
            media.art = _text()
        elif param == MS_POSITION and len(value) >= 2:
            media.position = value[0] << 8 | value[1]
        elif param == MS_LENGTH and len(value) >= 2:
            media.duration = value[0] << 8 | value[1]

        if new_media_source:
            # A newly-discovered media player must appear in every zone's
            # source_list, so refresh all of them.
            self._notify_all()
        else:
            for zone, state in self._states.items():
                if state.source == source:
                    self._notify(zone)

    def media_state(self, source: int) -> MediaState:
        """Return (creating if needed) the cached media state for a source."""
        return self._media.setdefault(source, MediaState())

    def media_sources(self) -> list[int]:
        """Return the internal media-player source bytes the amp reports."""
        return sorted(self._media_sources)

    def _update_link(self, data: bytes) -> None:
        """Apply a Link zones (0x30) frame to the live link state.

        ``data`` is the frame payload: an options byte, an optional 4-byte group
        identifier (when bit 7 of options is set), then the member zones. A list
        of 2+ zones forms/replaces a group; a single zone is removed from any
        group. Affected zones are notified so their group membership refreshes.
        """
        if not data:
            return
        options = data[0]
        rest = data[5:] if options & 0x80 else data[1:]
        zones = sorted({b for b in rest if 0 <= b <= 95})
        zoneset = set(zones)
        if not zoneset:
            return
        affected = set(zoneset)
        new_links: list[tuple[set[int], int]] = []
        for members, opts in self._links:
            if members & zoneset:
                affected |= members
            remaining = members - zoneset
            if len(remaining) >= 2:
                new_links.append((remaining, opts))
        if len(zones) >= 2:
            new_links.append((zoneset, options))
        self._links = new_links
        for zone in affected:
            self._notify(zone)

    def group_members(self, zone: int) -> list[int]:
        """Return the sorted member zones of ``zone``'s group, else empty."""
        for members, _ in self._links:
            if zone in members and len(members) >= 2:
                return sorted(members)
        return []

    def register_zone_entity(self, zone: int, entity_id: str) -> None:
        """Record the entity_id for a zone (used to report group members)."""
        self._zone_entity_ids[zone] = entity_id

    def zone_entity_id(self, zone: int) -> str | None:
        """Return the entity_id for a zone, if known."""
        return self._zone_entity_ids.get(zone)

    def zone_for_entity_id(self, entity_id: str) -> int | None:
        """Return the zone number for an entity_id, if known."""
        for zone, eid in self._zone_entity_ids.items():
            if eid == entity_id:
                return zone
        return None

    async def async_join(self, zones: set[int] | list[int]) -> None:
        """Link the given zones into one group on the amplifier."""
        members = sorted(set(zones))
        if len(members) < 2:
            return
        self._update_link(bytes([LINK_OPTIONS_DEFAULT, *members]))
        await self.async_link_zones(members, LINK_OPTIONS_DEFAULT)

    async def async_unjoin(self, zone: int) -> None:
        """Remove a zone from its group on the amplifier."""
        self._update_link(bytes([LINK_OPTIONS_DEFAULT, zone]))
        await self.async_link_zones([zone], LINK_OPTIONS_DEFAULT)

    def _notify(self, zone: int) -> None:
        """Invoke listeners registered for ``zone``."""
        for callback in list(self._listeners.get(zone, [])):
            callback()

    def _notify_all(self) -> None:
        """Invoke every registered listener (e.g. on (dis)connect)."""
        for zone in list(self._listeners):
            self._notify(zone)
        self._notify_diagnostics()

    def register_diagnostic_listener(
        self, callback: CallbackType
    ) -> Callable[[], None]:
        """Register a callback for amp-wide diagnostic/preset/auto-power changes."""
        self._diag_listeners.append(callback)

        def _remove() -> None:
            if callback in self._diag_listeners:
                self._diag_listeners.remove(callback)

        return _remove

    def _notify_diagnostics(self) -> None:
        """Invoke diagnostic listeners."""
        for callback in list(self._diag_listeners):
            callback()

    # -- diagnostic / preset / auto-power accessors ----------------------

    @property
    def temperature(self) -> int | None:
        """Current amplifier temperature in °C, if known."""
        return self._temperature

    @property
    def peak_temperature(self) -> int | None:
        """Peak amplifier temperature in °C, if known."""
        return self._peak_temperature

    @property
    def clipping(self) -> bool:
        """Whether an analogue input is currently clipping."""
        return self._clipping

    @property
    def clipping_source(self) -> int | None:
        """The source that is clipping, if any."""
        return self._clipping_source

    @property
    def auto_power_on(self) -> bool:
        """Whether auto power-on (on audio) is enabled."""
        return bool(self._auto_power_options & AUTO_POWER_ON_BIT)

    @property
    def auto_standby(self) -> bool:
        """Whether auto standby (on silence) is enabled."""
        return bool(self._auto_power_options & AUTO_STANDBY_BIT)

    @property
    def standby_seconds(self) -> int:
        """Auto standby timeout in seconds."""
        return 2 ** min(self._auto_power_standby_n, 30)

    def source_name(self, source_id: int) -> str | None:
        """Return the amplifier-reported name for a source, if known."""
        return self._source_names.get(source_id)

    def source_gain(self, source_id: int) -> int | None:
        """Return the gain (dB) for a source, if known."""
        return self._source_gain.get(source_id)

    def set_extended_info_callback(
        self, callback: Callable[[str | None, str | None], None]
    ) -> None:
        """Register a callback invoked with (firmware, mac) from ext info."""
        self._extended_info_callback = callback

    async def async_set_source_gain(self, source_id: int, gain: int) -> None:
        """Set a source's gain in dB (0..18), then read it back.

        Real amplifiers send no notification after a set (only the simulator
        does), so we request the value afterwards to refresh our cache.
        """
        await self.async_send(CMD_SOURCE_GAIN, ZONE_ALL, source_id, gain)
        await self.async_send(CMD_SOURCE_GAIN, ZONE_ALL, source_id)

    async def async_set_zone_gain(self, zone: int, gain: int) -> None:
        """Set a zone's gain in dB (-12..12), then read it back."""
        await self.async_send(CMD_ZONE_GAIN, zone, protocol.to_signed_byte(gain))
        await self.async_send(CMD_ZONE_GAIN, zone)

    async def async_set_special_bit(
        self, zone: int, byte_index: int, bit: int, enabled: bool
    ) -> None:
        """Set a bit in a zone's special-features bytes (0x0C), preserving rest.

        Refuses to write until the amplifier's current special-features bytes
        have been read, so a toggle never clobbers other bits (e.g. a low-pass
        filter) that we haven't seen yet.
        """
        if zone not in self._special_known:
            _LOGGER.warning(
                "Ignoring special-features change for zone %s: current value "
                "not yet read from the amplifier",
                zone,
            )
            return
        current = list(self.zone_state(zone).special)
        if enabled:
            current[byte_index] |= bit
        else:
            current[byte_index] &= ~bit
        await self.async_send(CMD_SPECIAL_FEATURES, zone, current[0], current[1])
        await self.async_send(CMD_SPECIAL_FEATURES, zone)

    @property
    def preset_names(self) -> dict[int, str]:
        """Map of preset index (1..15) -> name."""
        return dict(self._preset_names)

    @property
    def preset_current(self) -> int:
        """Currently active preset index (0 = standard)."""
        return self._preset_current

    def _mark_unavailable(self) -> None:
        """Flag all known zones as unavailable."""
        for state in self._states.values():
            state.available = False

    async def _close_socket(self) -> None:
        """Close the writer if open."""
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except OSError:
                pass
            self._writer = None
            self._reader = None

    async def async_send(self, command: int, zone: int, *data: int) -> None:
        """Send a command frame to the amplifier."""
        if self._writer is None or not self._connected.is_set():
            _LOGGER.warning(
                "Cannot send Axium command %02X to zone %s: not connected",
                command,
                zone,
            )
            return
        frame = protocol.encode(command, protocol.encode_zone(zone), *data)
        async with self._write_lock:
            try:
                self._writer.write(frame)
                await self._writer.drain()
            except OSError as err:
                _LOGGER.warning("Failed to send Axium command: %s", err)
                return
        _LOGGER.debug(
            "Sent to zone %s: %s", zone, protocol.describe(protocol.decode(frame) or b"")
        )

    async def async_request_zone_name(self, zone: int) -> None:
        """Ask the amplifier for a zone's name (command 0x38)."""
        from .const import CMD_ZONE_NAME_REQUEST

        await self.async_send(CMD_ZONE_NAME_REQUEST, zone)

    async def async_set_zone_name(self, zone: int, name: str) -> None:
        """Write a zone's name to the amplifier (command 0x1C), then read back.

        The name is stored on the amplifier itself (so it survives on the front
        panel and for other controllers). Names are limited to ~15 UTF-8 bytes,
        truncated on a character boundary so multi-byte names are not split.
        Real amplifiers send no notification after a set, so we request the name
        afterwards to refresh our cache.
        """
        from .const import CMD_ZONE_NAME_REQUEST

        encoded = name.encode("utf-8")[:15]
        while encoded:
            try:
                encoded.decode("utf-8")
                break
            except UnicodeDecodeError:
                encoded = encoded[:-1]
        await self.async_send(CMD_ZONE_NAME, zone, *encoded)
        await self.async_send(CMD_ZONE_NAME_REQUEST, zone)

    async def async_link_zones(self, zones: list[int], options: int) -> None:
        """Link a set of zones into a group on the amplifier (command 0x30).

        Sent to all zones (zone byte 0xFF). A single-zone list effectively
        leaves that zone ungrouped. The amplifier then keeps the linked zones
        in sync for the enabled options.
        """
        await self.async_send(CMD_LINK_ZONES, ZONE_ALL, options, *zones)

    async def _request_link_groups(self) -> None:
        """Ask the amplifier for its current zone groups (command 0x30)."""
        await self.async_send(CMD_LINK_ZONES, ZONE_ALL, LINK_REQUEST_GROUPED)

    async def _request_source_names(self) -> None:
        """Ask the amplifier for all source names (command 0x29, no data)."""
        await self.async_send(CMD_SOURCE_NAME, ZONE_ALL)

    async def async_set_source_name(self, source_id: int, name: str) -> None:
        """Write a source name to the amplifier (command 0x29).

        Preserves the source's device byte and flags from the last report so the
        write does not change enabled state or other options.
        """
        device, flags = self._source_meta.get(source_id, (0x00, 0x00))
        # Older Axium firmware limits names to ~15 UTF-8 bytes; truncate on a
        # character boundary so multi-byte names are not split.
        encoded = name.encode("utf-8")[:15]
        while encoded:
            try:
                encoded.decode("utf-8")
                break
            except UnicodeDecodeError:
                encoded = encoded[:-1]
        await self.async_send(
            CMD_SOURCE_NAME, ZONE_ALL, source_id, 0x00, device, flags, *encoded
        )
        # Real amplifiers do not echo a set; re-read so our cached name (and the
        # media_player source_list the cards render) reflects the change.
        await self._request_source_names()

    # -- presets ---------------------------------------------------------

    async def async_select_preset(self, index: int) -> None:
        """Select a preset (0 = standard, 1..15 = preset A..O). Sent to all."""
        await self.async_send(CMD_PRESET, ZONE_ALL, index & 0x0F)

    async def _request_preset_names(self) -> None:
        """Request the name of every preset (1..15)."""
        for index in range(1, PRESET_COUNT + 1):
            await self.async_send(CMD_PRESET_NAME_REQUEST, ZONE_ALL, index)

    # -- auto power / standby (0x16, per unit) ---------------------------

    def _unit_bytes(self) -> tuple[int, int] | None:
        """Return the directly-connected unit id as two bytes, if known."""
        if self._device_info is None or self._device_info.unit_id is None:
            return None
        unit = self._device_info.unit_id
        return (unit >> 8) & 0xFF, unit & 0xFF

    async def _write_auto_power(self) -> None:
        """Write the cached auto power options + standby time to the amp."""
        unit = self._unit_bytes()
        if unit is None:
            return
        await self.async_send(
            CMD_AUTO_POWER,
            ZONE_ALL,
            unit[0],
            unit[1],
            self._auto_power_options,
            self._auto_power_standby_n,
        )
        # Real amplifiers do not echo a set; re-read the applied configuration.
        await self._request_auto_power()

    async def async_set_auto_power_bit(self, bit: int, enabled: bool) -> None:
        """Enable/disable an auto-power option bit, preserving the others."""
        if enabled:
            self._auto_power_options |= bit
        else:
            self._auto_power_options &= ~bit
        await self._write_auto_power()

    async def async_set_standby_seconds(self, seconds: float) -> None:
        """Set the auto standby timeout (snapped to the nearest 2^n seconds)."""
        seconds = max(1, seconds)
        self._auto_power_standby_n = max(0, min(30, round(math.log2(seconds))))
        await self._write_auto_power()

    async def _request_auto_power(self) -> None:
        """Request the current auto power configuration (unit id only)."""
        unit = self._unit_bytes()
        if unit is not None:
            await self.async_send(CMD_AUTO_POWER, ZONE_ALL, unit[0], unit[1])

    # -- extended device info / diagnostics ------------------------------

    def _handle_network_settings(self, data: bytes) -> None:
        """Cache the amp's network settings from a 0x3A/03 report."""
        ips = data[4:20]
        self._network = NetworkConfig(
            flags=data[3],
            ip=bytes(ips[0:4]),
            subnet=bytes(ips[4:8]),
            dns=bytes(ips[8:12]),
            router=bytes(ips[12:16]),
        )
        self._notify_diagnostics()

    async def _request_network_config(self) -> None:
        """Request the amp's IP addresses and DHCP/static flag."""
        unit = self._unit_bytes()
        if unit is not None:
            await self.async_send(
                CMD_NETWORK_SETTINGS,
                ZONE_ALL,
                unit[0],
                unit[1],
                NET_SETTING_IP_FLAGS_REQUEST,
            )

    async def async_set_network_static(self, static: bool) -> None:
        """Switch the amp between static IP and DHCP, keeping its addresses.

        Writing static with the *current* addresses pins the working IP so a
        reboot's new DHCP lease can't move it. Other flag bits (time server etc.)
        are preserved. Reads the config back afterwards (the amp doesn't echo).
        """
        unit = self._unit_bytes()
        if unit is None or self._network is None:
            return
        n = self._network
        flags = (n.flags | NET_FLAG_STATIC) if static else (n.flags & ~NET_FLAG_STATIC)
        await self.async_send(
            CMD_NETWORK_SETTINGS,
            ZONE_ALL,
            unit[0],
            unit[1],
            NET_SETTING_IP_FLAGS,
            flags,
            *n.ip,
            *n.subnet,
            *n.dns,
            *n.router,
        )
        await self._request_network_config()

    @property
    def network_known(self) -> bool:
        """Whether the amp's network settings have been read."""
        return self._network is not None

    @property
    def network_is_static(self) -> bool:
        """Whether the amp is on a static IP (else DHCP)."""
        return bool(self._network and self._network.flags & NET_FLAG_STATIC)

    @property
    def network_ip(self) -> str | None:
        """The amp's current IP address as a dotted string, if known."""
        if self._network is None:
            return None
        return ".".join(str(b) for b in self._network.ip)

    async def async_request_extended_info(self) -> None:
        """Request extended device info (temperature etc.) for the unit."""
        unit = self._unit_bytes()
        if unit is not None:
            await self.async_send(CMD_REQUEST_EXTENDED_INFO, ZONE_ALL, unit[0], unit[1])

    async def async_request_zone_state(self, zone: int) -> None:
        """Read a zone's power, mute, volume and source (no data = request)."""
        for command in (CMD_POWER, CMD_MUTE, CMD_VOLUME, CMD_SOURCE):
            await self.async_send(command, zone)

    async def async_poll_zones(self) -> None:
        """Re-read every known zone's state and the source names.

        Changes made on the amplifier itself (front panel, IR, or another
        controller that we miss) are not always pushed to us, so a periodic
        poll keeps HA — and the cards — in sync with the hardware. Source names
        are re-read too, so a rename done on the amp shows up without needing a
        reconnect.
        """
        if not self.available:
            return
        for zone in list(self._zone_entity_ids):
            await self.async_request_zone_state(zone)
        await self._request_source_names()

    async def async_media_control(
        self, source: int, control: int, *extra: int
    ) -> None:
        """Send a Media Control command (0x3D) for a media source."""
        await self.async_send(CMD_MEDIA_CONTROL, ZONE_ALL, source, control, *extra)

    async def async_set_repeat(self, source: int, repeat: str) -> None:
        """Set the repeat mode for a media source."""
        value = {"one": REPEAT_TRACK, "all": REPEAT_ALL}.get(repeat, REPEAT_OFF)
        await self.async_media_control(source, MEDIA_REPEAT, value)

    async def _request_media_sources(self) -> None:
        """Probe each possible internal media player (they answer if present).

        A media-status request to a source that exists returns a Media Status
        (0x3E) reply, which reveals it as a selectable source; absent players
        (e.g. AirPlay on a non-AirPlay amp) stay silent.
        """
        for source in sorted(MEDIA_SOURCE_BYTES):
            await self.async_request_media_status(source)

    async def async_request_media_status(self, source: int) -> None:
        """Request now-playing details for a media source (0x3F).

        The two-byte parameter bitfield selects play flags (bit 0), artist,
        album, title, cover art, position and length (bits 5-10) → 0x07E1.
        """
        await self.async_send(CMD_MEDIA_STATUS_REQUEST, ZONE_ALL, source, 0x07, 0xE1)

    async def _request_device_info(self) -> None:
        """Ask the directly-connected amplifier to identify itself (0x14)."""
        await self.async_send(
            CMD_REQUEST_DEVICE_INFO,
            ZONE_ALL,
            DEVICE_INFO_NO_EXPANSION_REPLY
            | DEVICE_INFO_REPLY_ON_PORT_ONLY
            | DEVICE_INFO_LIST_ZONES,
        )
