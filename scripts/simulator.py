#!/usr/bin/env python3
"""Axium AX-800DAV simulator for testing the Home Assistant integration.

Runs a TCP server that speaks the Axium Communications Protocol on port 17037,
so you can point the Home Assistant integration at this program instead of a
real amplifier. It:

- identifies itself as an AX-800DAV (Request Device information -> 0x94),
- keeps per-zone state (power, mute, volume, source),
- echoes state changes back as notifications (the integration is push-based),
- pushes a full state snapshot to each client on connect,
- offers an interactive console to simulate front-panel/keypad changes.

Run it on any PC on your network, then add the Axium integration in Home
Assistant using that PC's IP address and port 17037.

    python scripts/simulator.py
    python scripts/simulator.py --zones "1=Kitchen, 2=Living room, 3=Bedroom"

Stdlib only; works on Python 3.9+.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
import time

DEFAULT_PORT = 17037
TERMINATOR = b"\n"

# Identify as an AX-800DAV: device type 0x00 (amplifier), firmware major 2,
# model code 0x84 (AX-800DAV), unit id 0x1234.
DEVICE_TYPE = 0x00
FIRMWARE_MAJOR = 2
MODEL_CODE = 0x84
UNIT_ID = 0x1234

# Source name <-> data byte (subset; matches the integration).
SOURCE_NUMBER_TO_BYTE = {
    1: 0x05, 2: 0x06, 3: 0x07, 4: 0x03, 5: 0x00, 6: 0x01, 7: 0x02, 8: 0x04,
    9: 0x08, 10: 0x09, 11: 0x0A, 12: 0x0B, 13: 0x0C, 14: 0x0D, 15: 0x0E, 16: 0x0F,
}
SOURCE_BYTE_TO_NAME = {b: f"Source {n}" for n, b in SOURCE_NUMBER_TO_BYTE.items()}
SOURCE_BYTE_TO_NAME[0x10] = "AirPlay"
SOURCE_BYTE_TO_NAME[0x12] = "Media Player"
SOURCE_NAME_TO_BYTE = {"airplay": 0x10, "mediaplayer": 0x12, "media": 0x12}
SOURCE_NAME_TO_BYTE.update({str(n): b for n, b in SOURCE_NUMBER_TO_BYTE.items()})

POWER_ON_VALUES = {0x01, 0x03, 0x07}
POWER_OFF_VALUES = {0x00, 0x02, 0x06}

# Link zones (0x30) option bits.
LINK_OPT_SOURCE = 0x01
LINK_OPT_VOLUME = 0x02
LINK_OPT_STANDBY = 0x04


def opts_text(opts: int) -> str:
    """Describe the enabled link options."""
    names = []
    if opts & LINK_OPT_SOURCE:
        names.append("source")
    if opts & LINK_OPT_VOLUME:
        names.append("volume")
    if opts & LINK_OPT_STANDBY:
        names.append("power")
    return "+".join(names) or "none"


def encode(*data_bytes: int) -> bytes:
    """Encode bytes into an ASCII-hex frame terminated by a line feed."""
    return bytes(b & 0xFF for b in data_bytes).hex().upper().encode("ascii") + TERMINATOR


def decode(line: bytes) -> bytes | None:
    """Decode a received ASCII-hex line into raw bytes (or None if invalid)."""
    text = line.decode("ascii", errors="ignore").strip().strip("\x11\x13")
    if not text:
        return None
    try:
        return bytes.fromhex(text)
    except ValueError:
        return None


def hexbytes(frame: bytes) -> str:
    """Format bytes as space-separated uppercase hex."""
    return " ".join(f"{b:02X}" for b in frame)


def log(direction: str, frame: bytes, note: str = "") -> None:
    """Print a timestamped protocol log line."""
    stamp = time.strftime("%H:%M:%S")
    tail = f"  {note}" if note else ""
    print(f"{stamp} {direction} {hexbytes(frame):<20}{tail}")


class Zone:
    """Mutable state for a single simulated zone."""

    def __init__(self, number: int, name: str) -> None:
        self.number = number
        self.name = name
        self.power = False
        self.muted = False
        self.volume = 80  # v1 (0..160), ~50%
        self.source = 0x05  # Source 1
        # Single-byte per-zone settings keyed by command byte (raw values):
        # bass/treble/balance, max volume, audio delay, power-on volume, gain.
        self.settings = {
            0x05: 0, 0x06: 0, 0x07: 0, 0x0D: 0xA0, 0x31: 0, 0x48: 0x40, 0x44: 0,
        }
        self.special = [0, 0]  # 0x0C special-feature bytes (loudness, mono, ...)

    def describe(self) -> str:
        """Return a one-line human summary of the zone state."""
        pct = round(self.volume / 160 * 100)
        src = SOURCE_BYTE_TO_NAME.get(self.source, f"0x{self.source:02X}")
        state = "ON " if self.power else "off"
        mute = " [muted]" if self.muted else ""
        return f"  zone {self.number:<2} {self.name:<16} {state}  vol {pct:>3}%  {src}{mute}"


class Simulator:
    """Holds zone state and the set of connected clients."""

    def __init__(
        self, zones: dict[int, str], peer_zones: dict[int, str] | None = None
    ) -> None:
        peer_zones = peer_zones or {}
        merged = {**zones, **peer_zones}
        self.zones = {n: Zone(n, name) for n, name in merged.items()}
        # Own zones vs a simulated second amp's zones (for stack discovery).
        self.own_zone_numbers = sorted(zones)
        self.peer_zone_numbers = sorted(peer_zones)
        self.clients: set[asyncio.StreamWriter] = set()
        # Active zone links: list of (set of zone numbers, options byte).
        self.links: list[tuple[set[int], int]] = []
        # Enabled sources reported by this amp: source data byte -> name.
        # Physical inputs S1..S8 (per the protocol's Source Selection encoding)
        # plus AirPlay and the internal Media Player.
        self.source_names: dict[int, str] = {
            0x05: "SAT",       # S1
            0x06: "DVD",       # S2
            0x07: "Video",     # S3
            0x03: "Aux",       # S4
            0x00: "CD",        # S5
            0x01: "Tape",      # S6
            0x02: "Tuner",     # S7
            0x04: "Utility",   # S8
            0x10: "AirPlay",
            0x12: "Media Player",
        }
        # Now-playing state for media sources (source byte -> dict).
        self.media: dict[int, dict] = {}
        # Per-source gain (source byte -> dB).
        self.source_gain: dict[int, int] = {}
        # Auto power/standby (0x16), presets (0x1E/0x2A), diagnostics (0x39/0x34).
        self.auto_power_options = 0
        self.auto_power_standby_n = 8  # 2^8 = 256 s
        self.preset_current = 0
        self.preset_names = {1: "Movie", 2: "Party", 3: "Night"}
        self.temperature = 42
        self.peak_temperature = 55

    @staticmethod
    def _new_media() -> dict:
        """Return a default now-playing record."""
        return {
            "playing": False,
            "paused": False,
            "title": "Test Track",
            "artist": "Test Artist",
            "album": "Test Album",
            "position": 0,
            "duration": 180,
            "shuffle": False,
            "repeat": "off",
        }

    def _media_status_frames(self, source: int) -> list[bytes]:
        """Build Media Status (0x3E) frames describing a source's now-playing."""
        m = self.media.setdefault(source, self._new_media())
        flags = 0x01  # available
        if m["playing"]:
            flags |= 0x04
        if m["paused"]:
            flags |= 0x04 | 0x08
        if m["shuffle"]:
            flags |= 0x80
        if m["repeat"] == "one":
            flags |= 0x20
        elif m["repeat"] == "all":
            flags |= 0x40
        return [
            encode(0x3E, 0xFF, source, 0x00, flags),
            encode(0x3E, 0xFF, source, 0x05, *m["artist"].encode("utf-8")),
            encode(0x3E, 0xFF, source, 0x06, *m["album"].encode("utf-8")),
            encode(0x3E, 0xFF, source, 0x07, *m["title"].encode("utf-8")),
            encode(0x3E, 0xFF, source, 0x09, (m["position"] >> 8) & 0xFF, m["position"] & 0xFF),
            encode(0x3E, 0xFF, source, 0x0A, (m["duration"] >> 8) & 0xFF, m["duration"] & 0xFF),
        ]

    # -- frame production -------------------------------------------------

    def device_info_frame(
        self, zone_list: list[int] | None = None, unit_id: int = UNIT_ID
    ) -> bytes:
        """Build a Request Device information response (0x94) for one unit.

        ``zone_list`` (when given, i.e. request option bit 2 was set) is appended
        after the unit ID.
        """
        payload = [
            0x94, 0x00, DEVICE_TYPE, FIRMWARE_MAJOR, MODEL_CODE,
            (unit_id >> 8) & 0xFF, unit_id & 0xFF,
        ]
        if zone_list:
            payload.extend(zone_list)
        return encode(*payload)

    def snapshot_frames(self) -> list[bytes]:
        """Build notifications describing the current state of every zone."""
        frames: list[bytes] = []
        for z in self.zones.values():
            frames.append(encode(0x04, z.number, z.volume))
            frames.append(encode(0x03, z.number, z.source))  # no turn-on bit
            frames.append(encode(0x02, z.number, 0x00 if z.muted else 0x01))
            frames.append(encode(0x01, z.number, 0x01 if z.power else 0x00))
        return frames

    # -- broadcasting ----------------------------------------------------

    async def broadcast(self, frame: bytes, note: str = "") -> None:
        """Send a notification frame to all connected clients."""
        log("-> notify ", frame, note)
        for writer in list(self.clients):
            with contextlib.suppress(OSError):
                writer.write(frame)
        await asyncio.gather(
            *(self._safe_drain(w) for w in list(self.clients)), return_exceptions=True
        )

    @staticmethod
    async def _safe_drain(writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(OSError):
            await writer.drain()

    # -- state changes (used by both the protocol and the console) -------
    #
    # Real Axium amplifiers send NO notification after a control client's set —
    # they only reply to an explicit request. So a set from a connected client
    # (Home Assistant) updates state silently (echo=False); only an *unsolicited*
    # change, i.e. one typed at this simulator's console (echo=True, the
    # default, emulating a front-panel action), broadcasts to clients.

    async def set_power(self, zone: Zone, on: bool, echo: bool = True) -> None:
        zone.power = on
        if echo:
            await self.broadcast(
                encode(0x01, zone.number, 0x01 if on else 0x00),
                f"{zone.name} power {'on' if on else 'off'}",
            )

    async def set_mute(self, zone: Zone, muted: bool, echo: bool = True) -> None:
        zone.muted = muted
        if echo:
            await self.broadcast(
                encode(0x02, zone.number, 0x00 if muted else 0x01),
                f"{zone.name} {'muted' if muted else 'unmuted'}",
            )

    async def set_volume(self, zone: Zone, v1: int, echo: bool = True) -> None:
        zone.volume = max(0, min(160, v1))
        if echo:
            await self.broadcast(
                encode(0x04, zone.number, zone.volume),
                f"{zone.name} volume {round(zone.volume / 160 * 100)}%",
            )

    async def set_source(
        self, zone: Zone, byte: int, turn_on: bool, echo: bool = True
    ) -> None:
        zone.source = byte & 0x3F
        if turn_on:
            zone.power = True
        flags = 0x80 if turn_on else 0x00
        if echo:
            await self.broadcast(
                encode(0x03, zone.number, zone.source | flags),
                f"{zone.name} source "
                f"{SOURCE_BYTE_TO_NAME.get(zone.source, hex(zone.source))}",
            )

    # -- incoming protocol handling --------------------------------------

    async def on_frame(self, frame: bytes, writer: asyncio.StreamWriter) -> None:
        """Handle a frame received from a client (e.g. Home Assistant)."""
        command, zone_byte, data = frame[0], frame[1], frame[2:]
        log("<- recv   ", frame)

        if command == 0x14:  # Request Device information
            include_zones = bool(data and data[0] & 0x04)
            suffix = " + zones" if include_zones else ""
            reply = self.device_info_frame(
                self.own_zone_numbers if include_zones else None, UNIT_ID
            )
            writer.write(reply)
            await self._safe_drain(writer)
            log("-> reply  ", reply, "AX-800DAV" + suffix)
            if self.peer_zone_numbers:  # emulate a second amp in the stack
                reply2 = self.device_info_frame(
                    self.peer_zone_numbers if include_zones else None, UNIT_ID + 1
                )
                writer.write(reply2)
                await self._safe_drain(writer)
                log("-> reply  ", reply2, "AX-800DAV (peer)" + suffix)
            return
        if command == 0x08:  # Request Protocol Version
            writer.write(encode(0x88, zone_byte, 0x01))
            await self._safe_drain(writer)
            return
        if command == 0x2F:  # Request zone assignments
            writer.write(encode(0xAF, 0xFF, *self.zones))
            await self._safe_drain(writer)
            return
        if command == 0x1C and data:  # Zone name set (silent; the client re-reads)
            z = self.zones.get(zone_byte)
            if z:
                z.name = bytes(data).decode("utf-8", "replace").rstrip("\x00")
                print(f"   zone {zone_byte} renamed to '{z.name}'")
            return
        if command == 0x38:  # Zone name request
            z = self.zones.get(zone_byte)
            if z:
                writer.write(encode(0x1C, zone_byte, *z.name.encode("utf-8")))
                await self._safe_drain(writer)
            return
        if command == 0x0C:  # Amplifier special features (loudness/mono)
            for z in self._resolve_zones(zone_byte):
                if data:  # set (silent; the client re-reads)
                    z.special[0] = data[0]
                    if len(data) >= 2:
                        z.special[1] = data[1]
                else:  # request -> reply with current value
                    reply = encode(0x0C, z.number, z.special[0], z.special[1])
                    writer.write(reply)
                    log("-> reply  ", reply, f"{z.name} special")
            if not data:
                await self._safe_drain(writer)
            return
        if command == 0x32:  # Source gain (set: id+gain, request: id)
            if len(data) >= 2:  # set (silent; the client re-reads)
                self.source_gain[data[0]] = data[1]
            elif data:  # request -> reply with current value
                reply = encode(0x32, 0xFF, data[0], self.source_gain.get(data[0], 0))
                writer.write(reply)
                log("-> reply  ", reply, f"source {data[0]} gain")
                await self._safe_drain(writer)
            return
        # Single-byte per-zone settings: bass/treble/balance, max volume,
        # audio delay, power-on volume, zone gain.
        if command in (0x05, 0x06, 0x07, 0x0D, 0x31, 0x48, 0x44):
            for z in self._resolve_zones(zone_byte):
                if data:  # set (silent; the client re-reads)
                    z.settings[command] = data[0]
                else:  # request -> reply with current value
                    reply = encode(command, z.number, z.settings.get(command, 0))
                    writer.write(reply)
                    log("-> reply  ", reply, f"{z.name} {command:02X}")
            if not data:
                await self._safe_drain(writer)
            return
        if command == 0x3F and data:  # Media Status request
            for frame_out in self._media_status_frames(data[0]):
                writer.write(frame_out)
                log("-> reply  ", frame_out, "media status")
            await self._safe_drain(writer)
            return
        if command == 0x3D and len(data) >= 2:  # Media Control
            source, ctrl = data[0], data[1]
            m = self.media.setdefault(source, self._new_media())
            if ctrl == 0x01:  # play
                m["playing"], m["paused"] = True, False
            elif ctrl == 0x02:  # pause/resume
                if m["playing"]:
                    m["playing"], m["paused"] = False, True
                else:
                    m["playing"], m["paused"] = True, False
            elif ctrl == 0x03:  # stop
                m["playing"], m["paused"], m["position"] = False, False, 0
            elif ctrl == 0x04:  # previous
                m["title"], m["position"] = "Previous Track", 0
            elif ctrl == 0x05:  # next
                m["title"], m["position"] = "Next Track", 0
            elif ctrl == 0x06 and len(data) >= 3:  # repeat
                m["repeat"] = {0: "off", 1: "one", 2: "all"}.get(data[2], "off")
            elif ctrl == 0x08:  # shuffle
                m["shuffle"] = (
                    not m["shuffle"] if len(data) < 3 or data[2] == 2 else bool(data[2])
                )
            for frame_out in self._media_status_frames(source):
                await self.broadcast(frame_out, "media")
            return
        if command == 0x16:  # Auto power on/off (set when 4+ bytes, else request)
            if len(data) >= 4:
                self.auto_power_options = data[2]
                self.auto_power_standby_n = data[3]
            reply = encode(
                0x16, 0xFF, data[0] if data else 0, data[1] if len(data) > 1 else 0,
                self.auto_power_options, self.auto_power_standby_n,
            )
            await self.broadcast(reply, "auto power")
            return
        if command == 0x1E:  # Preset selection / status
            if data:
                self.preset_current = data[0] & 0x0F
            reply = encode(0x1E, 0xFF, self.preset_current, self.preset_current)
            await self.broadcast(reply, f"preset {self.preset_current}")
            return
        if command == 0x2B and data:  # Request preset name
            index = data[0]
            if index in self.preset_names:
                frame_out = encode(0x2A, 0xFF, index, *self.preset_names[index].encode("utf-8"))
                writer.write(frame_out)
                log("-> reply  ", frame_out, f"preset name {index}")
                await self._safe_drain(writer)
            return
        if command == 0x39:  # Request extended device information
            reply = encode(
                0xB9, 0x00,
                0x00, 0x00, 0x12, 0x34,  # 32-bit unit id
                FIRMWARE_MAJOR, 0x00, 0x00,  # fw major, minor, beta
                self.temperature & 0xFF, self.peak_temperature & 0xFF,
                192, 168, 1, 50,  # IP
                0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF,  # MAC
                26, 6, 30,  # manufacture date
                0x02,  # flags
            )
            writer.write(reply)
            log("-> reply  ", reply, f"ext info temp={self.temperature}")
            await self._safe_drain(writer)
            return
        if command == 0x29:  # Source Name and Options (request or set)
            if len(data) <= 1:  # request: all sources, or one if an id is given
                ids = [data[0]] if data else sorted(self.source_names)
                for sid in ids:
                    if sid in self.source_names:
                        reply = self._source_name_frame(sid)
                        writer.write(reply)
                        log("-> reply  ", reply, f"source {sid}")
                await self._safe_drain(writer)
            elif len(data) >= 4:  # set the source name (silent; the client re-reads)
                sid = data[0]
                name = bytes(data[4:]).decode("utf-8", "replace").rstrip("\x00")
                self.source_names[sid] = name
                print(f"   source {sid} renamed to '{name}'")
            return
        if command == 0x30:  # Link zones (command, or request when 0x30 FF 20)
            if len(data) <= 1:  # request for the current groups
                for grp, opts in self.links:
                    reply = encode(0x30, 0xFF, opts, *sorted(grp))
                    writer.write(reply)
                    log("-> reply  ", reply, "group")
                await self._safe_drain(writer)
            else:
                self._handle_link(data)
                # Notify all clients of the change so they refresh grouping.
                await self.broadcast(encode(0x30, 0xFF, *data), "link update")
            return

        base = self._resolve_zones(zone_byte)
        if not base:
            return
        primary = base[0]
        # No-data power/mute/volume/source is a request: reply with current state.
        if command in (0x01, 0x02, 0x03, 0x04) and not data:
            for z in base:
                if command == 0x01:
                    reply = encode(0x01, z.number, 0x01 if z.power else 0x00)
                elif command == 0x02:
                    reply = encode(0x02, z.number, 0x00 if z.muted else 0x01)
                elif command == 0x04:
                    reply = encode(0x04, z.number, z.volume)
                else:
                    reply = encode(0x03, z.number, z.source)
                writer.write(reply)
                log("-> reply  ", reply, f"{z.name} state")
            await self._safe_drain(writer)
            return
        # A set from a control client updates state silently (echo=False): real
        # amplifiers only report state when explicitly requested afterwards.
        if command == 0x01 and data:  # Power
            on = self._power_target(data[0], primary)
            for z in self._expand(base, LINK_OPT_STANDBY):
                await self.set_power(z, on, echo=False)
        elif command == 0x02 and data:  # Mute
            muted = self._mute_target(data[0], primary)
            for z in self._expand(base, LINK_OPT_VOLUME):
                await self.set_mute(z, muted, echo=False)
        elif command == 0x04 and data:  # Volume
            for z in self._expand(base, LINK_OPT_VOLUME):
                await self.set_volume(z, data[0], echo=False)
        elif command == 0x11:  # Volume up (relative keeps per-zone offsets)
            step = data[0] if data else 4
            for z in self._expand(base, LINK_OPT_VOLUME):
                await self.set_volume(z, z.volume + step, echo=False)
        elif command == 0x12:  # Volume down
            step = data[0] if data else 4
            for z in self._expand(base, LINK_OPT_VOLUME):
                await self.set_volume(z, z.volume - step, echo=False)
        elif command == 0x03 and data:  # Source select
            turn_on = bool(data[0] & 0x80)
            for z in self._expand(base, LINK_OPT_SOURCE):
                await self.set_source(z, data[0], turn_on, echo=False)

    def _source_name_frame(self, source_id: int) -> bytes:
        """Build a Source Name and Options (0x29) report for one source."""
        name = self.source_names[source_id].encode("utf-8")
        return encode(0x29, 0xFF, source_id, 0x00, 0x00, 0x00, *name)

    def _group_for(self, number: int) -> tuple[set[int], int]:
        """Return the link group and options for a zone, or (empty, 0)."""
        for zones, opts in self.links:
            if number in zones:
                return zones, opts
        return set(), 0

    def _expand(self, base: list[Zone], opt_bit: int) -> list[Zone]:
        """Expand addressed zones to their linked group for a given option."""
        result: dict[int, Zone] = {}
        for z in base:
            result[z.number] = z
            zones, opts = self._group_for(z.number)
            if opts & opt_bit:
                for n in zones:
                    if n in self.zones:
                        result[n] = self.zones[n]
        return list(result.values())

    def _handle_link(self, data: bytes) -> None:
        """Apply a Link zones (0x30) command: <options><zone>[<zone>...]."""
        if not data:
            return
        opts = data[0]
        zones = [b for b in data[1:] if b in self.zones]
        zoneset = set(zones)
        # Remove these zones from any existing group; keep groups of 2+.
        self.links = [
            (grp - zoneset, o)
            for grp, o in self.links
            if len(grp - zoneset) >= 2
        ]
        if len(zones) >= 2:
            self.links.append((zoneset, opts))
            names = ", ".join(self.zones[n].name for n in sorted(zoneset))
            print(f"   linked group ({opts_text(opts)}): {names}")
        elif zones:
            print(f"   ungrouped zone: {self.zones[zones[0]].name}")

    def _resolve_zones(self, zone_byte: int) -> list[Zone]:
        if zone_byte in (0xFF, 0xFE):  # all / all-local zones
            return list(self.zones.values())
        z = self.zones.get(zone_byte)
        return [z] if z else []

    @staticmethod
    def _power_target(value: int, zone: Zone) -> bool:
        if value in POWER_ON_VALUES:
            return True
        if value in POWER_OFF_VALUES:
            return False
        return not zone.power  # toggle (0x04/0x05)

    @staticmethod
    def _mute_target(value: int, zone: Zone) -> bool:
        if value == 0x00:
            return True
        if value == 0x01:
            return False
        return not zone.muted  # toggle (0x02)

    # -- client lifecycle ------------------------------------------------

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        print(f"** client connected: {peer}")
        self.clients.add(writer)
        # Push the current state so Home Assistant populates immediately.
        for frame in self.snapshot_frames():
            writer.write(frame)
        await self._safe_drain(writer)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                frame = decode(line)
                if frame is not None and len(frame) >= 2:
                    await self.on_frame(frame, writer)
        finally:
            self.clients.discard(writer)
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
            print(f"** client disconnected: {peer}")

    # -- interactive console ---------------------------------------------

    def print_status(self) -> None:
        print(f"-- {len(self.clients)} client(s) connected --")
        for z in self.zones.values():
            print(z.describe())

    @staticmethod
    def print_help() -> None:
        print(
            "\nConsole commands (simulate front-panel/keypad changes):\n"
            "  status                  show all zones\n"
            "  power <zone> on|off     set zone power\n"
            "  mute <zone> on|off      set zone mute\n"
            "  vol <zone> <0-100>      set zone volume %\n"
            "  source <zone> <name>    e.g. airplay, mediaplayer, 1..16\n"
            "  preset <0-15>           recall a preset (0 = standard)\n"
            "  clip [source] / unclip  raise/clear a clipping alert\n"
            "  temp <celsius>          set the reported temperature\n"
            "  help                    show this help\n"
            "  quit                    stop the simulator\n"
        )

    async def handle_console(self, line: str) -> bool:
        """Process one console line. Returns False to stop the simulator."""
        parts = line.split()
        if not parts:
            return True
        cmd = parts[0].lower()
        if cmd in ("quit", "exit"):
            return False
        if cmd == "help":
            self.print_help()
            return True
        if cmd == "status":
            self.print_status()
            return True
        if cmd == "clip":
            source = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            await self.broadcast(encode(0x34, 0xFF, 0x01, source), f"clip src {source}")
            return True
        if cmd == "unclip":
            await self.broadcast(encode(0x34, 0xFF, 0x02), "unclip")
            return True
        if cmd == "temp" and len(parts) > 1 and parts[1].lstrip("-").isdigit():
            self.temperature = int(parts[1])
            print(f"   temperature set to {self.temperature}")
            return True
        if cmd == "preset" and len(parts) > 1 and parts[1].isdigit():
            self.preset_current = int(parts[1]) & 0x0F
            await self.broadcast(
                encode(0x1E, 0xFF, self.preset_current, self.preset_current),
                f"preset {self.preset_current}",
            )
            return True

        if cmd in ("power", "mute", "vol", "source") and len(parts) >= 3:
            try:
                zone = self.zones[int(parts[1])]
            except (ValueError, KeyError):
                print(f"  unknown zone: {parts[1]}")
                return True
            arg = parts[2].lower()
            if cmd == "power":
                await self.set_power(zone, arg in ("on", "1", "true"))
            elif cmd == "mute":
                await self.set_mute(zone, arg in ("on", "1", "true"))
            elif cmd == "vol":
                try:
                    await self.set_volume(zone, round(int(arg) / 100 * 160))
                except ValueError:
                    print("  volume must be 0-100")
            elif cmd == "source":
                byte = SOURCE_NAME_TO_BYTE.get(arg)
                if byte is None:
                    print("  unknown source (use airplay, mediaplayer, or 1..16)")
                else:
                    await self.set_source(zone, byte, turn_on=True)
            return True

        print("  ? type 'help'")
        return True


async def console_loop(sim: Simulator, stop: asyncio.Event) -> None:
    """Read console commands until 'quit'. EOF leaves the server running."""
    sim.print_help()
    loop = asyncio.get_event_loop()
    while not stop.is_set():
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:  # EOF (no interactive terminal / piped input ended)
            print("(console input closed; server still running, Ctrl+C to stop)")
            return
        if not await sim.handle_console(line.strip()):
            stop.set()
            return


def parse_zones(spec: str) -> dict[int, str]:
    """Parse a 'number=Name, ...' spec into an ordered zone dict."""
    zones: dict[int, str] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            num, name = part.split("=", 1)
            zones[int(num)] = name.strip() or f"Zone {num.strip()}"
        else:
            zones[int(part)] = f"Zone {part}"
    return zones or {n: f"Zone {n}" for n in range(1, 9)}


async def main_async(args: argparse.Namespace) -> int:
    """Start the server and the console, run until stopped."""
    peer = parse_zones(args.peer_zones) if args.peer_zones else {}
    sim = Simulator(parse_zones(args.zones), peer)
    stop = asyncio.Event()
    server = await asyncio.start_server(sim.handle_client, args.host, args.port)
    addrs = ", ".join(str(s.getsockname()) for s in server.sockets)
    print(f"Axium AX-800DAV simulator listening on {addrs}")
    print("Point the Home Assistant integration here (port 17037).")

    console = asyncio.ensure_future(console_loop(sim, stop))
    async with server:
        await stop.wait()
    console.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await console
    print("Simulator stopped.")
    return 0


def main() -> int:
    """Parse arguments and run the simulator."""
    parser = argparse.ArgumentParser(description="Simulate an Axium AX-800DAV.")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="TCP port")
    parser.add_argument(
        "--zones",
        default="1=Kitchen, 2=Living room, 3=Bedroom, 4=Office, "
        "5=Bathroom, 6=Patio, 7=Garage, 8=Kids room",
        help="Zones as 'number=Name, ...'",
    )
    parser.add_argument(
        "--peer-zones",
        default="",
        help="Second amp's zones (e.g. '9=Den, 10=Loft') to emulate a 2-amp stack",
    )
    args = parser.parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\nSimulator stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
