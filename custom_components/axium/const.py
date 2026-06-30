"""Constants for the Axium amplifier integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "axium"

# Default TCP port for the Axium Communications Protocol over Ethernet.
DEFAULT_PORT: Final = 17037
DEFAULT_NAME: Final = "Axium"

# Configuration / option keys.
CONF_ZONES: Final = "zones"

# Axium command bytes (see AxiumCommsProtocol.pdf, section 2).
CMD_POWER: Final = 0x01
CMD_MUTE: Final = 0x02
CMD_SOURCE: Final = 0x03
CMD_VOLUME: Final = 0x04
CMD_REQUEST_PROTOCOL: Final = 0x08
CMD_VOLUME_UP: Final = 0x11
CMD_VOLUME_DOWN: Final = 0x12
CMD_REQUEST_DEVICE_INFO: Final = 0x14
CMD_ZONE_NAME: Final = 0x1C
CMD_ZONE_NAME_REQUEST: Final = 0x38

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

BYTE_TO_SOURCE_NUMBER: Final[dict[int, int]] = {
    byte: number for number, byte in SOURCE_NUMBER_TO_BYTE.items()
}

# Number of sources exposed by default. Most Axium amplifiers expose 8.
DEFAULT_SOURCE_COUNT: Final = 8
