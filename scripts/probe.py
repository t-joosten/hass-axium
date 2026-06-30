#!/usr/bin/env python3
"""Standalone probe for an Axium amplifier (no Home Assistant required).

Run this from any PC on the same network as the amplifier to confirm that it
responds and to inspect the raw protocol framing before installing the Home
Assistant integration. It connects over TCP, asks the amplifier to identify
itself, and prints every frame it receives — both the raw ASCII-hex line and a
decoded interpretation.

Usage:
    python scripts/probe.py 192.168.1.50
    python scripts/probe.py 192.168.1.50 --port 17037 --duration 15
    python scripts/probe.py 192.168.1.50 --send 2FFF        # send extra raw cmd

Stdlib only; works on Python 3.9+.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib

DEFAULT_PORT = 17037
TERMINATOR = b"\n"

# Response command bytes are the request command with bit 7 set.
RESP_DEVICE_INFO = 0x94

DEVICE_TYPES = {
    0x00: "Amplifier",
    0x03: "Video matrix",
    0x04: "Media manager",
    0x05: "Virtual zone host",
}

DEVICE_MODELS = {
    0x80: "AX4750",
    0x81: "AX4752",
    0x83: "AX-451/452-AV",
    0x84: "AX-800DAV",
    0x86: "AX-400DA",
    0x89: "AX-400DA",
    0x8A: "AX-1250",
    0x8F: "AX-Mini4",
    0x90: "AX-800-X",
    0x91: "AX-400-X",
    0x92: "AX-400-X",
    0x96: "AX-Mini1",
    0x97: "AX-Mini4",
    0x9C: "AX-Mini4",
}

COMMAND_NAMES = {
    0x01: "Standby / Power",
    0x02: "Mute",
    0x03: "Source Selection",
    0x04: "Volume",
    0x05: "Bass",
    0x06: "Treble",
    0x07: "Balance",
    0x08: "Request Protocol Version",
    0x11: "Volume Up",
    0x12: "Volume Down",
    0x14: "Request Device information",
    0x1C: "Zone name",
    0x2F: "Request zone assignments",
    0x38: "Zone name request",
    0x39: "Request extended device information",
    0x88: "Response: Protocol Version",
    0x94: "Response: Device information",
    0xAF: "Response: zone assignments",
    0xB9: "Response: extended device information",
}

# Source data-byte (lower 6 bits) -> friendly name.
_SOURCE_NUMBER = {
    0x05: 1, 0x06: 2, 0x07: 3, 0x03: 4, 0x00: 5, 0x01: 6, 0x02: 7, 0x04: 8,
    0x08: 9, 0x09: 10, 0x0A: 11, 0x0B: 12, 0x0C: 13, 0x0D: 14, 0x0E: 15, 0x0F: 16,
}
SOURCE_BYTE_TO_NAME = {byte: f"Source {n}" for byte, n in _SOURCE_NUMBER.items()}
SOURCE_BYTE_TO_NAME[0x10] = "AirPlay"
SOURCE_BYTE_TO_NAME[0x12] = "Media Player"

POWER_TEXT = {
    0x00: "A Standby (off)", 0x01: "A Power On", 0x02: "B Standby",
    0x03: "B Power On", 0x04: "Toggle A", 0x06: "A+B Standby", 0x07: "A+B Power On",
}
MUTE_TEXT = {0x00: "mute", 0x01: "unmute", 0x02: "toggle"}


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
    """Format raw bytes as space-separated uppercase hex."""
    return " ".join(f"{b:02X}" for b in frame)


def zone_text(zone: int) -> str:
    """Describe a zone byte."""
    specials = {0xFF: "all zones", 0xFE: "all local", 0xFD: "interface", 0x00: "zone 96"}
    if zone in specials:
        return f"{zone} ({specials[zone]})"
    return str(zone)


def describe(frame: bytes) -> str:
    """Return a human-readable interpretation of a decoded frame."""
    if len(frame) < 2:
        return "(too short)"
    command, zone, data = frame[0], frame[1], frame[2:]
    name = COMMAND_NAMES.get(command, f"command 0x{command:02X}")
    head = f"{name}  zone={zone_text(zone)}"

    if command == RESP_DEVICE_INFO and len(data) >= 3:
        model = DEVICE_MODELS.get(data[2], f"unknown code 0x{data[2]:02X}")
        dtype = DEVICE_TYPES.get(data[0], f"0x{data[0]:02X}")
        unit = f"0x{(data[3] << 8 | data[4]):04X}" if len(data) >= 5 else "?"
        zones = (
            ", ".join(str(b) for b in data[5:])
            if data[0] == 0x00 and len(data) > 5
            else "(not reported)"
        )
        return (
            f"{head}\n        device={dtype}  model={model}  fw=v{data[1]}  "
            f"unit_id={unit}\n        zones: {zones}"
        )
    if command == 0x01 and data:
        return f"{head}  ->  {POWER_TEXT.get(data[0], f'0x{data[0]:02X}')}"
    if command == 0x02 and data:
        return f"{head}  ->  {MUTE_TEXT.get(data[0], f'0x{data[0]:02X}')}"
    if command == 0x04 and data:
        return f"{head}  ->  {round(data[0] / 160 * 100)}% (v1=0x{data[0]:02X})"
    if command == 0x03 and data:
        masked = data[0] & 0x3F
        src = SOURCE_BYTE_TO_NAME.get(masked, f"source 0x{masked:02X}")
        flags = []
        if data[0] & 0x80:
            flags.append("turn-on")
        if data[0] & 0x40:
            flags.append("audio-only")
        suffix = f" [{', '.join(flags)}]" if flags else ""
        return f"{head}  ->  {src}{suffix}"
    if command == 0x1C and data:
        return f"{head}  ->  '{data.decode('utf-8', errors='replace').rstrip(chr(0))}'"
    if data:
        return f"{head}  data={hexbytes(data)}"
    return head


async def run(host: str, port: int, duration: float, extra: list[str]) -> int:
    """Connect, send requests, and print received frames for ``duration`` s."""
    print(f"Connecting to {host}:{port} ...")
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=10
        )
    except (OSError, asyncio.TimeoutError) as err:
        print(f"  ERROR: could not connect: {err}")
        return 2
    print("  connected.\n")

    async def send(frame: bytes, label: str) -> None:
        writer.write(frame)
        await writer.drain()
        print(f">> sent  {hexbytes(decode(frame) or b''):<24} {label}")

    # Identify the amplifier and list its zones (bit0 = no expansion reply,
    # bit1 = reply on this port, bit2 = include zone list).
    await send(encode(0x14, 0xFF, 0x07), "Request Device information + zones")
    # Ask which zones the directly-connected device owns.
    await send(encode(0x2F, 0xFF), "Request zone assignments")
    for raw in extra:
        try:
            data = bytes.fromhex(raw)
        except ValueError:
            print(f"  (skipping invalid --send value: {raw!r})")
            continue
        await send(encode(*data), "custom --send")

    print(f"\nListening for {duration:.0f}s (Ctrl+C to stop early)...\n")
    loop = asyncio.get_event_loop()
    deadline = loop.time() + duration
    seen = 0
    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            if not line:
                print("  (connection closed by amplifier)")
                break
            frame = decode(line)
            if frame is None:
                print(f"<< recv  (unparsed) {line!r}")
                continue
            seen += 1
            print(f"<< recv  {hexbytes(frame):<24} {describe(frame)}")
    finally:
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()

    print(f"\nDone. {seen} frame(s) received.")
    if seen == 0:
        print(
            "No frames received. Either this is not an Axium amplifier, it does "
            "not answer 0x14, or a firewall is blocking the reply."
        )
    return 0 if seen else 1


def main() -> int:
    """Parse arguments and run the probe."""
    parser = argparse.ArgumentParser(description="Probe an Axium amplifier over TCP.")
    parser.add_argument("host", help="Amplifier IP address or hostname")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="TCP port")
    parser.add_argument(
        "--duration", type=float, default=10.0, help="Seconds to listen for frames"
    )
    parser.add_argument(
        "--send",
        action="append",
        default=[],
        metavar="HEX",
        help="Extra raw command to send as hex, e.g. 38FF (repeatable)",
    )
    args = parser.parse_args()
    try:
        return asyncio.run(run(args.host, args.port, args.duration, args.send))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
