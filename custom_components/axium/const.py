"""Constants for the Axium amplifier integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "axium"

# Default TCP port for the Axium Communications Protocol over Ethernet.
DEFAULT_PORT: Final = 17037
DEFAULT_NAME: Final = "Axium"

# Configuration / option keys.
CONF_ZONES: Final = "zones"
CONF_SOURCES: Final = "sources"

# Keys within a stored zone / source definition.
ZONE_KEY: Final = "zone"
NAME_KEY: Final = "name"
ID_KEY: Final = "id"

# Axium command bytes (see AxiumCommsProtocol.pdf, section 2).
CMD_POWER: Final = 0x01
CMD_MUTE: Final = 0x02
CMD_SOURCE: Final = 0x03
CMD_VOLUME: Final = 0x04
CMD_BASS: Final = 0x05
CMD_TREBLE: Final = 0x06
CMD_BALANCE: Final = 0x07
CMD_MAX_VOLUME: Final = 0x0D
CMD_AUDIO_DELAY: Final = 0x31
CMD_POWER_ON_VOLUME: Final = 0x48

# Audio delay (0x31) is in 5 ms steps, one byte (max 255 -> 1275 ms).
AUDIO_DELAY_STEP: Final = 5
AUDIO_DELAY_MAX: Final = 255 * AUDIO_DELAY_STEP
CMD_REQUEST_PROTOCOL: Final = 0x08
CMD_AUTO_POWER: Final = 0x16
CMD_PRESET: Final = 0x1E
CMD_PRESET_NAME: Final = 0x2A
CMD_PRESET_NAME_REQUEST: Final = 0x2B
CMD_CLIPPING: Final = 0x34
CMD_REQUEST_EXTENDED_INFO: Final = 0x39
RESP_EXTENDED_DEVICE_INFO: Final = 0xB9
CMD_VOLUME_UP: Final = 0x11
CMD_VOLUME_DOWN: Final = 0x12
CMD_REQUEST_DEVICE_INFO: Final = 0x14
CMD_ZONE_NAME: Final = 0x1C
CMD_SOURCE_NAME: Final = 0x29  # Source Name and Options (report/request/set)
CMD_LINK_ZONES: Final = 0x30
CMD_ZONE_NAME_REQUEST: Final = 0x38
CMD_MEDIA_CONTROL: Final = 0x3D
CMD_MEDIA_STATUS: Final = 0x3E
CMD_MEDIA_STATUS_REQUEST: Final = 0x3F

# Media Control (0x3D) sub-commands (2nd data byte).
MEDIA_PLAY: Final = 0x01
MEDIA_PAUSE: Final = 0x02
MEDIA_STOP: Final = 0x03
MEDIA_PREVIOUS: Final = 0x04
MEDIA_NEXT: Final = 0x05
MEDIA_REPEAT: Final = 0x06
MEDIA_SHUFFLE: Final = 0x08

# Media Status (0x3E) parameters (2nd data byte).
MS_FLAGS: Final = 0x00
MS_ARTIST: Final = 0x05
MS_ALBUM: Final = 0x06
MS_TITLE: Final = 0x07
MS_ART: Final = 0x08
MS_POSITION: Final = 0x09
MS_LENGTH: Final = 0x0A

# Media Status flag bits (parameter 0x00 value).
MS_FLAG_AVAILABLE: Final = 0x01
MS_FLAG_ACTIVE: Final = 0x04
MS_FLAG_PAUSED: Final = 0x08
MS_FLAG_REPEAT_TRACK: Final = 0x20
MS_FLAG_REPEAT_DISC: Final = 0x40
MS_FLAG_SHUFFLE: Final = 0x80

# Repeat sub-command values (Media Control 0x06).
REPEAT_OFF: Final = 0x00
REPEAT_TRACK: Final = 0x01
REPEAT_ALL: Final = 0x02

# Source data bytes that are internal media players (AirPlay + Media Player 1-8)
# — zones on these sources show transport controls and now-playing metadata.
MEDIA_SOURCE_BYTES: Final = frozenset(
    {0x10, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x19}
)

# Auto power on/off (0x16) option bits.
AUTO_POWER_ON_BIT: Final = 0x01
AUTO_STANDBY_BIT: Final = 0x02

# Clipping notification (0x34) event types.
CLIP_CLIPPED: Final = 0x01
CLIP_UNCLIPPED: Final = 0x02

# Number of selectable presets (A..O).
PRESET_COUNT: Final = 15

# Tone control ranges (signed), per the protocol.
BASS_MIN: Final = -12
BASS_MAX: Final = 12
TREBLE_MIN: Final = -12
TREBLE_MAX: Final = 12
BALANCE_MIN: Final = -20
BALANCE_MAX: Final = 20

# Source Name and Options (0x29) flag byte (4th data byte) bits.
SOURCE_NAME_FLAG_DISABLED: Final = 0x04  # source is disabled when set

# Link zones (command 0x30) option bits. The amplifier keeps the linked zones
# in sync for whichever of these are set.
LINK_OPT_SOURCE: Final = 0x01
LINK_OPT_VOLUME: Final = 0x02  # implies mute and volume-offset tracking
LINK_OPT_STANDBY: Final = 0x04  # power on/off
# Default: link source, volume and power together.
LINK_OPTIONS_DEFAULT: Final = LINK_OPT_SOURCE | LINK_OPT_VOLUME | LINK_OPT_STANDBY
# Request the amplifier's current groups (bit 5 suppresses ungrouped zones):
# sending `30 FF 20` returns the grouped zones.
LINK_REQUEST_GROUPED: Final = 0x20

# Power (command 0x01) data bytes.
POWER_OFF: Final = 0x00
POWER_ON: Final = 0x01
POWER_TOGGLE: Final = 0x04
POWER_ON_VALUES: Final = {0x01, 0x03, 0x07}
POWER_OFF_VALUES: Final = {0x00, 0x02, 0x06}

# Mute (command 0x02) data bytes.
MUTE_ON: Final = 0x00
MUTE_OFF: Final = 0x01
MUTE_TOGGLE: Final = 0x02

# Source selection (command 0x03) flag bits.
SOURCE_FLAG_TURN_ON: Final = 0x80  # bit 7: turn the zone on
SOURCE_FLAG_AUDIO_ONLY: Final = 0x40  # bit 6: do not switch video
SOURCE_ID_MASK: Final = 0x3F

# Volume (command 0x04). v1 spans 0x00..0xA0 (0..160), an 80 dB range in
# 0.5 dB steps. Home Assistant volume_level (0.0..1.0) maps onto this range.
VOLUME_MIN: Final = 0x00
VOLUME_MAX: Final = 0xA0

# Special zone value meaning "all zones".
ZONE_ALL: Final = 0xFF

# Response command bytes are the request command with bit 7 set.
RESP_DEVICE_INFO: Final = 0x94  # response to Request Device information (0x14)

# Request Device information (0x14) data-byte option bits. Setting both keeps a
# single reply on our connection: bit 0 = don't reply on the expansion bus,
# bit 1 = only reply on the port the command was received on.
DEVICE_INFO_NO_EXPANSION_REPLY: Final = 0x01
DEVICE_INFO_REPLY_ON_PORT_ONLY: Final = 0x02
DEVICE_INFO_LIST_ZONES: Final = 0x04  # append the unit's zone list to the reply

# Fallback number of zones to create if the amplifier does not report its zones.
DEFAULT_ZONE_COUNT: Final = 8

# Device-type byte (first data byte of a 0x94 response).
DEVICE_TYPES: Final[dict[int, str]] = {
    0x00: "Amplifier",
    0x03: "Video matrix",
    0x04: "Media manager",
    0x05: "Virtual zone host",
}

# Device-specific model code (third data byte of a 0x94 response). See
# AxiumCommsProtocol.pdf, command 0x14 response.
DEVICE_MODELS: Final[dict[int, str]] = {
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

# Mapping of physical source number (S1..S16, as labelled on the amplifier) to
# the data byte used by the Source Selection command. The protocol's data-byte
# ordering does not follow the S-number ordering, so this lookup is required.
# See AxiumCommsProtocol.pdf, command 0x03.
SOURCE_NUMBER_TO_BYTE: Final[dict[int, int]] = {
    1: 0x05,  # S1 (SAT)
    2: 0x06,  # S2 (DVD)
    3: 0x07,  # S3 (Video)
    4: 0x03,  # S4 (Aux)
    5: 0x00,  # S5 (CD)
    6: 0x01,  # S6 (Tape)
    7: 0x02,  # S7 (Tuner)
    8: 0x04,  # S8 (Utility)
    9: 0x08,  # S9
    10: 0x09,  # S10
    11: 0x0A,  # S11
    12: 0x0B,  # S12
    13: 0x0C,  # S13
    14: 0x0D,  # S14
    15: 0x0E,  # S15
    16: 0x0F,  # S16
}

# Number of physical sources (S1..Sn) exposed by default. Most amps have 8.
DEFAULT_SOURCE_COUNT: Final = 8

# AirPlay is a dedicated source on Ethernet amplifiers (e.g. AX-800DAV).
SOURCE_AIRPLAY_BYTE: Final = 0x10
# Media Player 1 (the amplifier's internal media player) per Source Selection.
SOURCE_MEDIA_PLAYER_BYTE: Final = 0x12

# Friendly display name for each source data byte (after masking off the
# turn-on / audio-only flag bits).
SOURCE_BYTE_TO_NAME: Final[dict[int, str]] = {
    byte: f"Source {number}" for number, byte in SOURCE_NUMBER_TO_BYTE.items()
}
SOURCE_BYTE_TO_NAME[SOURCE_AIRPLAY_BYTE] = "AirPlay"
SOURCE_BYTE_TO_NAME[SOURCE_MEDIA_PLAYER_BYTE] = "Media Player"

SOURCE_NAME_TO_BYTE: Final[dict[str, int]] = {
    name: byte for byte, name in SOURCE_BYTE_TO_NAME.items()
}

# Sources offered in the Home Assistant source dropdown.
DEFAULT_SOURCE_LIST: Final[list[str]] = [
    f"Source {number}" for number in range(1, DEFAULT_SOURCE_COUNT + 1)
] + ["AirPlay"]
