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

    def describe(self) -> str:
        """Return a one-line human summary of the zone state."""
        pct = round(self.volume / 160 * 100)
        src = SOURCE_BYTE_TO_NAME.get(self.source, f"0x{self.source:02X}")
        state = "ON " if self.power else "off"
        mute = " [muted]" if self.muted else ""
        return f"  zone {self.number:<2} {self.name:<16} {state}  vol {pct:>3}%  {src}{mute}"


class Simulator:
    """Holds zone state and the set of connected clients."""

    def __init__(self, zones: dict[int, str]) -> None:
        self.zones = {n: Zone(n, name) for n, name in zones.items()}
        self.clients: set[asyncio.StreamWriter] = set()

    # -- frame production -------------------------------------------------

    def device_info_frame(self, include_zones: bool = False) -> bytes:
        """Build the Request Device information response (0x94).

        When ``include_zones`` is set (request option bit 2), the unit's zone
        numbers are appended after the unit ID.
        """
        payload = [
            0x94, 0x00, DEVICE_TYPE, FIRMWARE_MAJOR, MODEL_CODE,
            (UNIT_ID >> 8) & 0xFF, UNIT_ID & 0xFF,
        ]
        if include_zones:
            payload.extend(self.zones)
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

    async def set_power(self, zone: Zone, on: bool) -> None:
        zone.power = on
        await self.broadcast(
            encode(0x01, zone.number, 0x01 if on else 0x00),
            f"{zone.name} power {'on' if on else 'off'}",
        )

    async def set_mute(self, zone: Zone, muted: bool) -> None:
        zone.muted = muted
        await self.broadcast(
            encode(0x02, zone.number, 0x00 if muted else 0x01),
            f"{zone.name} {'muted' if muted else 'unmuted'}",
        )

    async def set_volume(self, zone: Zone, v1: int) -> None:
        zone.volume = max(0, min(160, v1))
        await self.broadcast(
            encode(0x04, zone.number, zone.volume),
            f"{zone.name} volume {round(zone.volume / 160 * 100)}%",
        )

    async def set_source(self, zone: Zone, byte: int, turn_on: bool) -> None:
        zone.source = byte & 0x3F
        if turn_on:
            zone.power = True
        flags = 0x80 if turn_on else 0x00
        await self.broadcast(
            encode(0x03, zone.number, zone.source | flags),
            f"{zone.name} source {SOURCE_BYTE_TO_NAME.get(zone.source, hex(zone.source))}",
        )

    # -- incoming protocol handling --------------------------------------

    async def on_frame(self, frame: bytes, writer: asyncio.StreamWriter) -> None:
        """Handle a frame received from a client (e.g. Home Assistant)."""
        command, zone_byte, data = frame[0], frame[1], frame[2:]
        log("<- recv   ", frame)

        if command == 0x14:  # Request Device information
            include_zones = bool(data and data[0] & 0x04)
            reply = self.device_info_frame(include_zones=include_zones)
            writer.write(reply)
            await self._safe_drain(writer)
            note = "AX-800DAV" + (" + zones" if include_zones else "")
            log("-> reply  ", reply, note)
            return
        if command == 0x08:  # Request Protocol Version
            writer.write(encode(0x88, zone_byte, 0x01))
            await self._safe_drain(writer)
            return
        if command == 0x2F:  # Request zone assignments
            writer.write(encode(0xAF, 0xFF, *self.zones))
            await self._safe_drain(writer)
            return
        if command == 0x38:  # Zone name request
            z = self.zones.get(zone_byte)
            if z:
                writer.write(encode(0x1C, zone_byte, *z.name.encode("utf-8")))
                await self._safe_drain(writer)
            return

        targets = self._resolve_zones(zone_byte)
        if not targets:
            return
        for z in targets:
            if command == 0x01 and data:  # Power
                await self.set_power(z, self._power_target(data[0], z))
            elif command == 0x02 and data:  # Mute
                await self.set_mute(z, self._mute_target(data[0], z))
            elif command == 0x04 and data:  # Volume
                await self.set_volume(z, data[0])
            elif command == 0x11:  # Volume up
                await self.set_volume(z, z.volume + (data[0] if data else 4))
            elif command == 0x12:  # Volume down
                await self.set_volume(z, z.volume - (data[0] if data else 4))
            elif command == 0x03 and data:  # Source select
                await self.set_source(z, data[0], bool(data[0] & 0x80))

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
    sim = Simulator(parse_zones(args.zones))
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
    args = parser.parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\nSimulator stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
