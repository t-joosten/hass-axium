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

- 🏷️ Named zones (e.g. *Kitchen*, *Living room*), each its own device
- 🧩 User-defined **zone groups** to control several zones as one
- 🔎 Automatic **model and firmware detection** — no need to pick your amp
- 🔌 Power on/off per zone (command `0x01`)
- 🔇 Mute / unmute (command `0x02`)
- 🔊 Volume set and step up/down (commands `0x04`, `0x11`, `0x12`)
- 🎚️ Source selection, S1–S16 **and AirPlay** (command `0x03`)
- 🎵 Works with **Music Assistant** for streaming via the amplifier's AirPlay input
- 📡 Live state updates pushed from the amplifier (notifications)
- ♻️ Automatic reconnection with backoff

## Requirements

- An Ethernet-equipped Axium amplifier (e.g. AX-400-X, AX-800-X, AX-1250,
  AX-Mini series) reachable on your network.
- The amplifier listens for the protocol on **TCP port 17037**.
- Home Assistant 2024.1.0 or newer.

You can verify connectivity before installing by opening a telnet session to the
amplifier on port 17037 (the protocol explicitly supports this for testing), or
by running the bundled [probe script](#testing-with-the-probe-script).

## Testing with the probe script

[`scripts/probe.py`](scripts/probe.py) is a standalone, dependency-free tool
(Python 3.9+ stdlib only) you can run from any PC on the same network to confirm
the amplifier responds and to inspect the raw protocol framing — handy before
installing the integration.

```bash
python scripts/probe.py 192.168.1.50
```

It connects, asks the amplifier to identify itself, requests its zone
assignments, then prints every frame it receives — both the raw ASCII-hex and a
decoded interpretation:

```
>> sent  14 FF 03                 Request Device information
<< recv  94 00 00 02 8A 00 07     Response: Device information  zone=0 (zone 96)
        device=Amplifier  model=AX-1250  fw=v2  unit_id=0x0007
<< recv  01 0B 01                 Standby / Power  zone=11  ->  A Power On
<< recv  04 0B 50                 Volume  zone=11  ->  50% (v1=0x50)
```

Options:

- `--port 17037` – override the TCP port.
- `--duration 15` – how long to listen for frames (default 10s).
- `--send 38FF` – send an extra raw command as hex (repeatable), e.g. request a
  zone name.

If you see no frames, it is either not an Axium amplifier, does not answer the
identify command, or a firewall is blocking the reply.

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
   - **Zones** – a comma-separated list of zones as `number=Name`, for example
     `1=Kitchen, 2=Living room, 3=Bedroom`. The name is optional (defaults to
     `Zone N`), and zone numbers may be any value the amplifier uses (0–95).

During setup the integration verifies that an **actual Axium amplifier
responds** at the address (it sends a *Request Device information* command and
waits for the reply), so you get a clear error instead of a silent failure:

- **Failed to connect** – nothing accepted the connection (wrong host/port, or
  the amplifier is offline).
- **No Axium amplifier responded** – something answered on the port, but it was
  not an Axium amplifier (or it did not reply in time).

Each zone becomes its own `media_player` device named after the room, nested
under the amplifier device. Zones are always created, so they are available as
soon as the amplifier connects.

### Naming and editing zones

Use the integration's **Configure** dialog → **Edit zones and names** to rename
zones or change which zones exist at any time.

### Zone groups

A zone group is a single `media_player` that controls several zones together —
power, volume, mute and source are applied to every member zone. You can create
as many groups as you like.

In the **Configure** dialog:

- **Add a zone group** – give the group a name (e.g. `Downstairs`) and tick the
  zones it should control.
- **Remove a zone group** – delete groups you no longer need.

A group reports an aggregated state: it is *on* if any member is on, shows the
average member volume, is *muted* only when every member is muted, and shows a
source only when all members agree. The member zone numbers are exposed in the
`zones` attribute.

Remember to choose **Save and finish** in the menu to apply your changes.

### Sources

Sources are exposed as `Source 1` … `Source 8` (mapping to the amplifier's
physical inputs S1–S16) plus **`AirPlay`** on amplifiers that support it.
Selecting a source also powers the zone on. You can rename sources from the
Home Assistant entity settings.

### Amplifier model & firmware

You do **not** select your amplifier model during setup. On connection the
integration sends a *Request Device information* command (`0x14`) and reads the
amplifier's reply, which reports the device type, model code and firmware
version. The amplifier (hub) device in Home Assistant is then updated
automatically with the detected **model** (e.g. *AX-800-X*, *AX-1250*,
*AX-400-X*, *AX-Mini1*) and **firmware** version.

If the amplifier does not respond to the query, control still works and the
device simply remains labelled generically as *Amplifier*.

## Streaming music with Music Assistant (AirPlay)

[Music Assistant](https://www.music-assistant.io/) streams audio by handing a
player a URL to play, so an Axium zone — which only switches between physical
inputs and controls volume/power — cannot itself appear as a Music Assistant
player. The clean way to stream music to the amplifier is via **AirPlay**:

Ethernet Axium amplifiers such as the **AX-800DAV** include an internal media
player that is an **AirPlay receiver**. Music Assistant has a mature
[AirPlay player provider](https://www.music-assistant.io/player-support/airplay/)
that streams directly to it. So Music Assistant carries the audio, and this
integration handles the amplifier side — powering the zone on and selecting the
**AirPlay** source.

**Setup**

1. Make sure AirPlay is enabled on the amplifier.
2. In Music Assistant, enable the **AirPlay** provider. It should discover the
   amplifier as an AirPlay player.
3. Add an automation so that when Music Assistant starts playing to the
   amplifier's AirPlay player, the desired Axium zone powers on and switches to
   the AirPlay source (and optionally powers off when playback stops):

```yaml
alias: "Axium: follow Music Assistant AirPlay (Living room)"
triggers:
  - trigger: state
    entity_id: media_player.ax_800dav_airplay   # the AirPlay player in MA
    to: "playing"
    id: started
  - trigger: state
    entity_id: media_player.ax_800dav_airplay
    to: ["idle", "off"]
    for: "00:05:00"
    id: stopped
actions:
  - choose:
      - conditions: "{{ trigger.id == 'started' }}"
        sequence:
          # select_source also powers the zone on
          - action: media_player.select_source
            target:
              entity_id: media_player.living_room   # the Axium zone (or group)
            data:
              source: AirPlay
      - conditions: "{{ trigger.id == 'stopped' }}"
        sequence:
          - action: media_player.turn_off
            target:
              entity_id: media_player.living_room
mode: single
```

> Replace the entity IDs with your own. Point the Axium target at a **zone
> group** instead of a single zone to send the same AirPlay stream to several
> rooms at once.

**Good to know**

- The amplifier's internal player is effectively a single stream, fanned to the
  zone(s) that select AirPlay. For *different* music in different rooms at the
  same time, use a separate streamer per input (e.g. one MA-capable streamer per
  zone).
- The AX-800DAV is an early-firmware device, so its AirPlay is likely AirPlay 1
  (RAOP), which Music Assistant supports. If it does not appear in the AirPlay
  provider, confirm AirPlay is enabled and that the amplifier and Music
  Assistant server are on the same subnet.

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
