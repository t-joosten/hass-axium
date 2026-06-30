# Axium Amplifier — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Validate](https://github.com/t-joosten/hass-axium/actions/workflows/validate.yml/badge.svg)](https://github.com/t-joosten/hass-axium/actions/workflows/validate.yml)

A custom [Home Assistant](https://www.home-assistant.io/) integration to control
**[Axium](https://www.axiumcontrol.com/)** multi-zone amplifiers over Ethernet
using the official Axium Communications Protocol.

Each amplifier zone is exposed as a `media_player` entity, giving you power,
volume, mute and source-selection control. The integration keeps a persistent
TCP connection open and listens for the notifications the amplifier emits when
its state changes, so Home Assistant stays in sync even when zones are changed
from a keypad or the front panel (a `local_push` integration — no polling).

> The full protocol specification is included in this repository:
> [`AxiumCommsProtocol.pdf`](AxiumCommsProtocol.pdf).

## Features

- 🔌 Power on/off per zone (command `0x01`)
- 🔇 Mute / unmute (command `0x02`)
- 🔊 Volume set and step up/down (commands `0x04`, `0x11`, `0x12`)
- 🎚️ Source selection, S1–S16 (command `0x03`)
- 📡 Live state updates pushed from the amplifier (notifications)
- ♻️ Automatic reconnection with backoff

## Requirements

- An Ethernet-equipped Axium amplifier (e.g. AX-400-X, AX-800-X, AX-1250,
  AX-Mini series) reachable on your network.
- The amplifier listens for the protocol on **TCP port 17037**.
- Home Assistant 2024.1.0 or newer.

You can verify connectivity before installing by opening a telnet session to the
amplifier on port 17037 (the protocol explicitly supports this for testing).

## Installation

### Via HACS (recommended)

1. In HACS, go to **Integrations → ⋮ → Custom repositories**.
2. Add `https://github.com/t-joosten/hass-axium` with category **Integration**.
3. Search for **Axium Amplifier** and install it.
4. Restart Home Assistant.

### Manual

1. Copy the `custom_components/axium` folder into your Home Assistant
   `config/custom_components` directory.
2. Restart Home Assistant.

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **Axium Amplifier**.
3. Enter:
   - **Host** – the amplifier's IP address or hostname.
   - **Port** – defaults to `17037`.
   - **Name** – a friendly name for the amplifier.
   - **Zones** – a comma-separated list of zone numbers to control, e.g.
     `1,2,3,4`.

To change the controlled zones later, use the integration's **Configure**
(options) dialog.

### Sources

Sources are exposed as `Source 1` … `Source 8` and map to the amplifier's
physical inputs S1–S16. Selecting a source also powers the zone on. You can
rename sources from the Home Assistant entity settings.

## How it works

Commands use the frame format `<command><zone>[<data>...]`. Every byte is sent
as two ASCII-hex characters, terminated by a line feed. For example, *power on
zone 1* is the bytes `01 01 01`, transmitted as:

```
010101\n
```

Volume (`0x04`) uses a `v1` byte spanning `0x00`–`0xA0` (0–160), an 80 dB range
in 0.5 dB steps, which the integration maps onto Home Assistant's `0.0`–`1.0`
volume level. The amplifier re-uses the same command bytes as notifications, so
the integration updates entity state directly from those messages.

## Disclaimer

This is an unofficial, community-built integration and is not affiliated with or
endorsed by Axium. The bundled protocol document remains the property of Axium
and is included for convenience and reproducibility.

## License

[MIT](LICENSE)
