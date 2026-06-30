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

- 🏷️ **All zones auto-created** (discovered from the amp), each its own device — just rename them (e.g. *Kitchen*, *Living room*)
- 🧩 **Group zones from the player card** (Sonos-style join/unjoin) — linked on the amplifier and kept in sync by the amp
- 🔎 Automatic **model and firmware detection** — no need to pick your amp
- 🔌 Power on/off per zone (command `0x01`)
- 🔇 Mute / unmute (command `0x02`)
- 🔊 Volume set and step up/down (commands `0x04`, `0x11`, `0x12`)
- 🎚️ Source selection with **auto-detected source names** (rename in HA, written back to the amp)
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
by running the bundled [probe script](#probe-script).

## Testing without hardware

You can develop and test the integration with no amplifier present.

### Simulator

[`scripts/simulator.py`](scripts/simulator.py) emulates an **AX-800DAV**. It
listens on TCP 17037, identifies itself as an AX-800DAV, tracks per-zone state,
and pushes notifications back — so you can point the Home Assistant integration
at it exactly as you would a real amplifier.

Run it on any PC on your network (stdlib only, Python 3.9+):

```bash
python scripts/simulator.py
python scripts/simulator.py --zones "1=Kitchen, 2=Living room, 3=Bedroom"
# emulate a two-amp stack (16 zones):
python scripts/simulator.py --zones "1=A,...,8=H" --peer-zones "9=I,...,16=P"
```

Then add the integration in Home Assistant using that PC's IP address and port
`17037`. The amplifier device will show up as an **AX-800DAV**.

It logs all traffic and gives you an interactive console to simulate
front-panel/keypad changes (so you can verify Home Assistant updates live):

```
power 1 on            # turn zone 1 on
vol 2 40              # set zone 2 to 40%
source 1 airplay      # switch zone 1 to AirPlay
mute 3 on             # mute zone 3
status                # show all zones
quit                  # stop
```

> The simulator emulates the **Axium control protocol only** (power, volume,
> source, etc.). It is **not** an AirPlay receiver, so it will not appear in
> Music Assistant — AirPlay streaming can only be tested against real hardware.

### Probe script

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
>> sent  14 FF 07                 Request Device information + zones
<< recv  94 00 00 02 8A 00 07 ... Response: Device information  zone=0 (zone 96)
        device=Amplifier  model=AX-1250  fw=v2  unit_id=0x0007
        zones: 1, 2, 3, 4, 5, 6, 7, 8
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
3. Enter just the connection details:
   - **Host** – the amplifier's IP address or hostname.
   - **Port** – defaults to `17037`.
   - **Name** – a friendly name for the amplifier.

That's it — **all of the amplifier's zones are added automatically.** On
connection the integration asks the amplifier which zones it has and creates a
`media_player` for each one (named `Zone N` by default), so every zone is
available out of the box. You then rename them to suit your home.

During setup the integration verifies that an **actual Axium amplifier
responds** at the address (it sends a *Request Device information* command and
waits for the reply), so you get a clear error instead of a silent failure:

- **Failed to connect** – nothing accepted the connection (wrong host/port, or
  the amplifier is offline).
- **No Axium amplifier responded** – something answered on the port, but it was
  not an Axium amplifier (or it did not reply in time).

Each zone becomes its own device, nested under the amplifier device.

#### Multi-amplifier stacks

Axium amplifiers can be stacked, sharing one system-wide zone space (e.g. two
8-zone **AX-800DAV**s give zones 1–16). You only connect to **one** amplifier —
it bridges commands to the rest of the stack — and the integration **discovers
the whole stack**, creating zones for every unit (16 in that example). Zone
groups are also system-wide, so a group can span amplifiers, and the group limit
follows the total zone count (16 zones → up to 8 groups). If a stacked unit is
not discovered, you can add its zones manually in the options (see below).

### Renaming zones

Each zone is its own device, so the simplest way to rename one is the **pencil
icon on the zone's device page** (built-in Home Assistant rename — instant, per
zone).

You can also edit the whole list at once in the integration's **Configure**
dialog: every zone is pre-filled as `number=Name`; change the names (and add any
zones the amplifier did not report), e.g. `11=Kitchen, 12=Living room`, and save.

### Zone groups

Group zones **directly from the media player card** — open a zone, use the
grouping control, and pick the other zones to join (the same UI you'd use to
group Sonos/Chromecast speakers). No settings dialog, no separate save: each
join/unjoin is applied to the amplifier immediately.

Grouping uses the amplifier's own *Link zones* command (`0x30`), so the **amp
keeps the joined zones in sync** for volume, source and power (volume changes
preserve each zone's relative offset). Groups are read live from the amplifier,
so links that already exist on the amp show up automatically as grouped — and a
group can span multiple amplifiers in a stack.

Two protocol rules apply:

- **A zone can be in only one group.**
- **Linked zones are coupled:** controlling any member also controls the rest of
  the group — that is how the amplifier's linking works.

### Sources

Sources are **auto-detected from the amplifier**: on setup the integration asks
for each input's name and which inputs are enabled (disabled inputs are hidden),
so your real source names (e.g. *Apple TV*, *Turntable*) appear instead of
generic labels. If the amplifier reports nothing, it falls back to
`Source 1`…`Source 8` plus `AirPlay`.

You can **rename sources** in the integration's **Configure** dialog (the
**Sources** field, `id=Name`). Renamed sources are **written back to the
amplifier**, so the new names persist there too. Selecting a source also powers
the zone on.

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

**Why don't the Axium zones appear as Music Assistant players?**

This is expected. Music Assistant only lists players that can **play a media
URL** (`play_media`); an Axium zone only switches physical inputs and controls
volume/power, so it is filtered out. The amplifier reaches Music Assistant
**only through Music Assistant's own AirPlay provider**, not through this
integration — and:

- It must be the **real amplifier** with AirPlay enabled. The
  [simulator](#simulator) speaks only the control protocol and is **not** an
  AirPlay device, so it will never show up in Music Assistant.
- The Music Assistant server and the amplifier must be on the **same subnet**
  (AirPlay discovery uses mDNS/Bonjour, which does not cross VLANs/subnets by
  default).
- Add and enable the **AirPlay** provider in Music Assistant; the amp appears
  there, not in the Home Assistant player list.

This integration's job is the amplifier side (power, volume, source). Music
Assistant carries the audio over AirPlay.

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
