"""Low-level encoding/decoding for the Axium Communications Protocol.

On Ethernet each command is sent to TCP port 17037. Every byte of the command
is encoded as two ASCII hexadecimal characters and a line-feed character
terminates the command (a leading/trailing carriage return is ignored).

A command has the form ``<command><zone>[<data>...]`` so the smallest valid
frame is two bytes (four hex characters).

See ``AxiumCommsProtocol.pdf`` sections 1.4 and 2.
"""

from __future__ import annotations

from collections.abc import Iterable

TERMINATOR: bytes = b"\n"


def encode(command: int, zone: int, *data: int) -> bytes:
    """Encode a command into an ASCII-hex frame terminated by a line feed.

    Each integer is masked to a single byte before being hex-encoded.
    """
    payload = bytes(b & 0xFF for b in (command, zone, *data))
    return payload.hex().upper().encode("ascii") + TERMINATOR


def decode(line: bytes | str) -> bytes | None:
    """Decode a single received line into raw command bytes.

    Returns ``None`` for blank lines or anything that is not valid hex (for
    example XON/XOFF flow-control characters that may slip through).
    """
    if isinstance(line, bytes):
        text = line.decode("ascii", errors="ignore")
    else:
        text = line
    # Strip whitespace, CR and any stray flow-control characters.
    text = text.strip().strip("\x11\x13")
    if not text:
        return None
    try:
        return bytes.fromhex(text)
    except ValueError:
        return None


def split_frames(buffer: bytes) -> tuple[list[bytes], bytes]:
    """Split a byte buffer on line feeds.

    Returns the list of complete (still ASCII-hex) lines and any trailing
    partial line that should be retained for the next read.
    """
    *complete, remainder = buffer.split(TERMINATOR)
    return complete, remainder


def volume_to_level(v1: int) -> float:
    """Convert an Axium v1 volume byte (0..160) to a 0.0..1.0 level."""
    from .const import VOLUME_MAX

    return max(0.0, min(1.0, v1 / VOLUME_MAX))


def level_to_volume(level: float) -> int:
    """Convert a 0.0..1.0 level to an Axium v1 volume byte (0..160)."""
    from .const import VOLUME_MAX

    return max(0, min(VOLUME_MAX, round(level * VOLUME_MAX)))


def encode_zone(zone: int) -> int:
    """Encode a logical zone number (0..95) into the protocol zone byte.

    Zones 0..31 are the number as-is; 32..63 use the ``100`` range prefix and
    64..95 use ``110``. Values outside 0..95 (special zone bytes such as 0xFF
    for "all zones") pass through unchanged.
    """
    if 32 <= zone <= 63:
        return 0x80 | (zone - 32)
    if 64 <= zone <= 95:
        return 0xC0 | (zone - 64)
    return zone


def decode_zone(byte: int) -> int:
    """Decode a protocol zone byte back to a logical zone number (0..95).

    The inverse of :func:`encode_zone`; obsolete sub-zone encodings and special
    zone bytes are returned unchanged.
    """
    if 0x80 <= byte <= 0x9F:
        return 32 + (byte & 0x1F)
    if 0xC0 <= byte <= 0xDF:
        return 64 + (byte & 0x1F)
    if byte <= 0x1F:
        return byte
    return byte


def to_signed_byte(value: int) -> int:
    """Encode a signed integer (-128..127) as an unsigned byte (two's comp)."""
    return value & 0xFF


def from_signed_byte(byte: int) -> int:
    """Decode an unsigned byte as a signed integer (-128..127)."""
    return byte - 0x100 if byte >= 0x80 else byte


def describe(frame: Iterable[int]) -> str:
    """Return a human-readable hex representation of a frame for logging."""
    return " ".join(f"{b:02X}" for b in frame)
