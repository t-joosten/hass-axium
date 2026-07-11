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
- ▶️ **Transport + now-playing** on media sources — play/pause/next/previous, shuffle/repeat, and title/artist/album/cover-art/position
- 🎛️ Per-zone **bass, treble, balance, gain, max-volume limit, power-on volume, lip-sync delay**, **loudness** and **mono** toggles; per-source **gain**
- 🌙 **Auto power-on / auto-standby**, **preset/scene recall**, and **temperature + firmware + MAC + clipping diagnostics**
- 🃏 **Source-centric dashboard card** — tap zone chips to assign them to a source, with transport/volume/mute
- 🔎 Automatic **model and firmware detection** — no need to pick your amp
- 🔌 Power on/off per zone (command `0x01`)
- 🔇 Mute / unmute (command `0x02`)
- 🔊 Volume set and step up/down (commands `0x04`, `0x11`, `0x12`)
- 🎚️ Source selection with **auto-detected source names** (rename inline in HA; written back to the amp)
- 🎵 Works with **Music Assistant** for streaming via the amplifier's AirPlay input
- 📡 State kept in sync over a persistent local connection — HA re-reads a zone
  right after each command, and polls every zone every 30s so changes made **on
  the amp itself** (front panel, IR) also show up (Axium amplifiers don't reliably
  push their own control changes)
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

> **To rename the hub or change its address**, open the entry and choose
> **Reconfigure**. It offers a **Name** (renames the hub — the whole stack is
> reached over one connection, so there is a single host/port) plus **Host** and
> **Port** for when the amplifier's IP changes (e.g. a new DHCP lease after a
> reboot). Your zones, sources and settings are kept. A DHCP reservation on your
> router avoids the change in the first place. You can also rename the hub — and
> each stacked amp — straight from its device page (the pencil). Renaming an amp
> also **writes its own network name back to the amplifier** (slugified to the
> amp's hostname charset, `a-z 0-9 - _`), so the amp's network identity matches.
>
> To stop the IP moving at all, the amplifier device has a **Static IP address**
> switch: turn it on to pin the amp's current address as static (so a reboot
> can't hand it a new one).
>
> ⚠️ **Turn Static IP back to DHCP _before_ moving an amp to a different
> network/subnet.** A static address is only valid on the subnet it was set on —
> move the amp to another router/subnet with static still on and it keeps the old
> address, which is invalid there, so it becomes **unreachable** (it won't get a
> DHCP lease and won't appear on the new network).
>
> **If you already moved it and it vanished:** plug the amp back into the
> **original** network (where its static address is valid), turn the **Static IP
> address** switch **off** (→ DHCP), then move it to the new network — it'll pull
> a fresh DHCP lease there. (It keeps its address on a DHCP renewal, so turning
> the switch off doesn't drop the connection while it's still on the old network.)
> Then use **Reconfigure** to point the integration at the amp's new IP.

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
follows the total zone count (16 zones → up to 8 groups). Zones across the full
0–95 range are supported (the higher zone numbers use the protocol's extended
zone-byte encoding).

**A logical hub, and a device per amplifier.** The integration creates a logical
**hub** device — the container for stack-wide things (the alarms switch, the
all-zones sleep timer) — and, nested under it, **one device per amplifier**: the
primary amp you connect to, plus every expansion amp. Each amp device carries its
own **firmware, model, temperature, peak temperature and MAC**, has its zones
nested under it, and can be **named independently** of the hub (so the hub can be
"Axium Hub" while the amps are "Axium 1" / "Axium 2"). So you can see each amp's
health on its own device and rename each part separately.

**Adding a second amp later:** stack it (both amps on the **same network** — these
amps stack over Ethernet, not the old expansion bus) and it's picked up
**automatically** — the integration discovers the whole stack, adds the new amp's
zones and device, and reloads.

**Zone conflicts are auto-resolved.** A factory expansion amp also claims zones
1–8, which collides with the first amp. The integration detects the clash and
**reassigns the expansion amp to a free range** (e.g. 9–16) for you — no app or
DIP switches needed. It re-applies on each connect, so it's self-healing even if
an amp forgets the setting across a reboot. (Your first/primary amp is never
touched.)

### Renaming zones

Each zone is its own device, so rename one with the **pencil icon on the zone's
device page** (built-in Home Assistant rename — instant, per zone). The new name
is **written back to the amplifier** (stored on the amp itself, so it also shows
on the front panel and to other controllers), truncated to ~15 characters. There
is no separate settings dialog; everything is discovered from the amplifier.

Each zone device also shows its **physical amp channel** as its model (its
subtitle in the devices list) — *Zone 1*…*Zone 8* per amp, matching the
amplifier's own "Amp Zone" label (a stacked amp's zones show 1–8 under that amp).
On AX‑800 / AX‑800‑X, the two **pre‑out** channels (7 and 8) are marked
*Zone 7 · Pre‑out* / *Zone 8 · Pre‑out*. (Zone type isn't in the control
protocol — the amp lists its zones as plain numbers — so this reflects the fixed
AX‑800 layout, the same way the amp's web app hides "Source Gain" on those
channels.) Sources likewise show their physical label (*Source 1*…*Source 8*) as
the editable name field's title.

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

Sources are **auto-detected from the amplifier**, just like zones: on setup the
integration asks for each input's name and which inputs are enabled (disabled
inputs are hidden), so your real source names (e.g. *Apple TV*, *Turntable*)
appear in each zone's source list instead of generic labels. If the amplifier
reports nothing, it falls back to `Source 1`…`Source 8` plus `AirPlay`.

Each source is also an **editable text field** on the amplifier device (in its
*Configuration* section) — just type a new name inline and it's **written back
to the amplifier**, updating the source dropdowns everywhere. No dialog needed.
Selecting a source also powers the zone on.

### Now playing & transport

When a zone is on an internal **media-player** source (AirPlay or *Media Player
1–8*), the zone's card gains **transport controls and now-playing info**:
play / pause / stop, next / previous, shuffle and repeat, plus track title,
artist, album, cover art and a progress bar (Media Control `0x3D` / Media Status
`0x3E`). On other sources (e.g. a CD input) these simply don't appear.

### Per-zone controls (number entities)

Each zone's device page exposes these sliders:

- **Bass**, **Treble**, **Balance** (`0x05`/`0x06`/`0x07`)
- **Maximum volume** — a volume limit, e.g. for kids' rooms (`0x0D`)
- **Audio delay** — lip-sync delay in 5 ms steps, for TV zones (`0x31`)
- **Loudness** and **Mono** toggles (`0x0C`)

**Advanced (opt-in) controls** — hidden until you enable them (see below):
- **Zone gain** — a per-zone level trim, −12…+12 dB (`0x44`)
- **Power-on volume** — the volume a zone starts at (`0x48`)
- **Source gain** — per-source input trim, 0…+18 dB (`0x32`)

### Advanced settings (risky level/gain controls)

The gain and power-on-volume controls can drive the amplifier hard, so they are
**off by default**. Enable them in the integration's **Configure** dialog, which
lists the risk of each before you turn it on:

> ⚠️ On AX‑400/AX‑800‑generation amplifiers (including the AX‑800DAV), high
> **source gain** can clip the analogue input (per Axium's protocol notes),
> which may stress the amplifier and speakers. All values stay within the
> amplifier's documented ranges — but raise gains and power‑on volume
> gradually, and watch the **Clipping** diagnostic sensor.

### Presets / scenes

If your amplifier has presets configured (A–O), a **Preset** select appears on
the amplifier device — pick one to recall it (command `0x1E`), or *Standard* to
return to normal. Preset names are read from the amplifier (`0x2A`/`0x2B`).

### Auto power & standby

On the amplifier device you'll find **Auto power on** and **Auto standby**
switches and an **Auto standby time** field (command `0x16`). With auto-standby
on, idle zones drop to standby after the timeout; with auto-power-on, a zone
wakes when audio is detected on its source.

### Diagnostics

The amplifier device's **Diagnostics** section (settings page) shows:

- **Temperature** and **Peak temperature** sensors (`0x39`)
- a **Clipping** problem sensor that turns on when an analogue input overloads,
  with the offending source in its attributes (`0x34`)

The device page also shows the full **firmware version** (`x.y.z`) and the
amplifier's **MAC address**, read from extended device info (`0x39`).

These are normal entities, so you can also add them to dashboard cards, graph
their history, or alert on them.

### Amplifier model & firmware

You do **not** select your amplifier model during setup. On connection the
integration sends a *Request Device information* command (`0x14`) and reads the
amplifier's reply, which reports the device type, model code and firmware
version. The amplifier (hub) device in Home Assistant is then updated
automatically with the detected **model** (e.g. *AX-800-X*, *AX-1250*,
*AX-400-X*, *AX-Mini1*) and **firmware** version.

If the amplifier does not respond to the query, control still works and the
device simply remains labelled generically as *Amplifier*.

## Streaming music to the amplifier

Ethernet Axium amplifiers with the streaming module have an **internal media
player** — a UPnP/DLNA renderer (plus Pandora/TuneIn, USB and network shares on
models that support them). The integration **auto-detects** it and exposes it as
a selectable source called **Media Player** (and **AirPlay** on the models that
actually have an AirPlay receiver — it's probed, so base units without it won't
show a phantom source). Select it on a zone like any other source and the zone
shows **now-playing and transport** (play/pause/next/prev).

> Not every amplifier has this. The integration only shows the Media Player /
> AirPlay source if the amp answers a media-status probe for it, so a base amp
> without the streaming hardware simply won't list it.

**How to feed it audio**

- **From a phone / Music Assistant (UPnP/DLNA):** the amp is a UPnP renderer —
  push to it from a DLNA-capable music app, or via **Music Assistant's DLNA
  provider**. Then select **Media Player** on the target zone(s).
- **Pandora / TuneIn / USB / network shares:** set these up in the amplifier's
  own web page (`http://<amp-ip>`) or the free Axium mobile app — they are
  **amp-side** features, not part of the TCP control protocol, so they can't be
  enabled from Home Assistant. Once configured they play through the same Media
  Player source.
- Within one amp, the internal player is a single stream fanned to whichever of
  **that amp's** zones select it — point a **zone group** at it to play in several
  of the amp's rooms at once.

> **Each amp has its OWN internal player (per-amp, not stack-wide).** On a multi-amp
> stack every amplifier drives only its own zones' Media Player: **Axium 1** → zones
> 1–8, **Axium 2** → zones 9–16, as **independent** streams. (An earlier version of
> this doc wrongly claimed one "stack-wide" stream; direct hardware listening tests
> disproved it — with amp 1 streaming and amp 2 idle, amp 2's zones were silent, and
> playing on amp 2's player made its zones play *that* content, not amp 1's.)
> Crucially the **two amps cannot be time-synced** (their DLNA renderers don't
> support grouping, and cross-feeding one amp's live stream to the other plays it
> from a different position). So there is **no whole-home "play on both amps"** in
> this integration — it drifts too badly to be useful. For genuine multi-room audio,
> feed all the zone inputs from **one external streamer** (e.g. a WiiM) so every zone
> shares a single physical source. A second internal player would have to be enabled
> on the expansion amp itself (a feature-unlock / setup matter on that amp, not in
> the TCP control protocol); if that were ever done, the integration probes
> `0x12`–`0x19` on every connect and would surface it automatically.

### AirPlay via Music Assistant (only on amps with AirPlay)

On an amp whose probe reveals **AirPlay**, [Music Assistant](https://www.music-assistant.io/)'s
[AirPlay provider](https://www.music-assistant.io/player-support/airplay/) streams
straight to it. Music Assistant carries the audio; this integration powers the
zone on and selects the **AirPlay** source. Add an automation so that when Music
Assistant starts playing, the zone switches to the source (and optionally powers
off when it stops):

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

### Sound notifications (doorbell, chime, TTS)

The **`axium.play_notification`** service plays a sound on the zones you choose
and then **puts every zone back exactly as it was** — same source, volume, mute
(or off if it was off). Because the amplifier can't *mix* audio (a zone plays one
source at a time), it **overrides** the source for the notification rather than
ducking under it; give the notification its own (louder) volume so it's heard,
and each zone's original volume is restored afterwards.

The sound is **pushed straight to each zone's built-in amp renderer** over UPnP —
no DLNA discovery, no Music Assistant, and it works on **every** zone (the amp
only advertises one of its per-zone renderers, so auto-discovery alone can't
reach them all). Home Assistant serves the media, so the amp just needs to reach
HA on your **main LAN**. This integration handles the amplifier side too (which
zones, volume, source, and the exact restore). Loudness is set on the Axium zone,
not the renderer — the amp stores a DLNA volume but doesn't apply it to output.

```yaml
# Doorbell → chime in the hallway + kitchen, then restore
alias: "Doorbell chime"
triggers:
  - trigger: state
    entity_id: binary_sensor.doorbell
    to: "on"
actions:
  - action: axium.play_notification
    data:
      zones:
        - media_player.hallway
        - media_player.kitchen
      volume: 55                       # notification volume (%)
      media_content_id: media-source://media_source/local/doorbell.mp3
```

**Spoken alerts (text-to-speech):** give a **`message`** instead of a sound and
it's spoken via text-to-speech — no URL to build:

```yaml
# Washing machine done → speak it in the kitchen
actions:
  - action: axium.play_notification
    data:
      zones: [media_player.kitchen]
      volume: 50
      message: The washing machine is done
      language: en          # optional; e.g. "nl" with Google Translate for Dutch
```

It uses your first TTS engine unless you set `tts_engine`. You can target
`presets` (by name) instead of / in addition to `zones`, set a fixed `duration`
instead of waiting for the sound to finish, or point `source` at an external
input if the sound comes from a device wired to one of S1–S8. Set the optional
`media_player` only if you want to route the sound through a specific renderer /
Music Assistant player instead of the direct push. Overlapping notifications are
serialised per amplifier so the save/restore never tangles.

## Dashboard card (Axium Source Card)

A custom Lovelace card is included for a **source-centric** view: one card per
source shows every zone as a tappable chip. Tap a zone to start it on that
source; because an Axium zone has a single source, this **automatically moves it
off whatever source it was on** — so a zone is only ever "active" on one source
card at a time. Tapping an active zone turns it off. The card also has
previous / play-pause / next, mute and volume controls for the zones currently
playing that source.

It follows touch/UX guidance: chips use a clear *selected* state (accent fill +
check), ≥40px tall with 8px spacing, and the transport buttons are 48px targets.

**Install** — nothing to do. The integration **registers the card
automatically**; just clear your browser cache / hard-refresh after installing
or updating. (If it doesn't appear, add the resource manually: Settings →
Dashboards → ⋮ → Resources → Add → URL `/axium/axium-source-card.js`, type
**JavaScript Module**.)

**Add a card** (one per source):

```yaml
type: custom:axium-source-card
source: Apple TV
```

Tap a zone chip to move that zone onto the source (tap again to turn it off).
**Long-press** a zone chip to open that zone's **device page**, where its
volume, bass/treble, gains and other per-zone settings live.

Zones are auto-detected (any Axium zone that offers the chosen source). The
visual editor's **Source** dropdown lists every source across all your Axium
amplifiers — only Axium sources, never those of other media players. Sources
without a name on the amplifier show up by their id (e.g. `Source 5`). When you
have more than one amplifier, each entry is prefixed with the amp name
(`[amp] [source]`) so they stay distinct, and the card header is prefixed the
same way; a single (even multi-amp) system just shows the source.

The card stores the source's **stable id** (its protocol byte), not the name —
so **renaming a source on the amp doesn't break the card**; it just follows the
new name. Selecting a source also records the owning amplifier in `hub:`
automatically. You can override the zones with explicit `entities:` and set a
custom `name:`. (Cards that stored a source *name* from older versions keep
working, and migrate to the id when you re-open and save them in the editor.)

### Zone presets

A **preset** is a named set of zones (e.g. *Downstairs* = Kitchen + Den).
Manage presets under **Settings → Devices & services → Axium → Configure →
Add a zone preset**. Every source card then shows a preset dropdown in its top
corner: picking one starts that card's source playing in **exactly** the
preset's zones — the preset's zones are switched to this source and any zone
currently on this source but not in the preset is turned off. Presets are
shared, so the same *Downstairs* preset works on your CD card, your Apple TV
card, and so on.

### Hub card

For a compact amplifier overview, add the **Axium Hub Card**
(`type: custom:axium-hub-card`). It shows the amp's name, model and firmware,
how many zones are on, the temperature and a clipping warning. The power button
turns **all** zones off, and tapping the card opens the hub's device page
(auto power/standby, presets, gains, diagnostics). With one amplifier it needs
no configuration; with several, set `hub:` (the visual editor's Amplifier
dropdown does this for you).

### Matrix card

The **Axium Matrix Card** (`type: custom:axium-matrix-card`) is the whole-system
routing grid: **zones are rows, sources are columns**. Each cell shows whether
that zone is on that source; tap a cell to route the zone there, or tap the
zone's currently-active (highlighted) cell to **turn that zone off**. It's the
fastest way to see and change what every room is playing at once. Auto-detects
the hub's zones and sources.

Each **source column has a power button** (the small ⏻ above its name): tap it to
turn that whole source **off** — it remembers which rooms were on that source and
switches them off — and tap again to turn it back **on**, restoring exactly those
rooms. The remembered set is kept per hub (survives a page reload). The button
lights up while any room is on that source. (For an amp *stream* column it toggles
the rooms hearing that amp's stream, resuming the stream when turned back on.)

The row and column **headers are interactive** too:

- **Tap a zone name** → quick controls for that room: **now playing** (track,
  artist and cover art when the zone is streaming), a **power** toggle, a
  **volume slider**, mute, and **previous / play-pause / next**. The power toggle
  means a zone can always be turned off here, even when its current source isn't
  shown as a column. Now-playing comes from the zone's own Media Player source, or
  from a **Music Assistant player of the same name** if one is streaming to it.
  When a Music Assistant player *is* feeding the zone, the **transport buttons
  drive that player** — so play/pause resumes the stream instead of restarting
  the amp's internal player, and next/previous act on the queue. Turning the zone
  **off** stops that stream first, so it can't immediately re-power the room.
- **Hold a zone name** (long-press) → open that zone's **device page** (tone,
  gains, sleep timer and other settings).
- **Tap an analog source name** (S1–S8) → pick a **preset**; its zones start
  playing that source (and any other zone currently on that source is turned off,
  so the source ends up playing in exactly the preset's rooms). Presets are
  managed in the Axium options.
- The **Media Player** column is shown as **one column per amplifier stream**
  (e.g. `Axium 1` / `Axium 2`). **Each amp's stream drives only its own zones** —
  `Axium 1` → zones 1–8, `Axium 2` → zones 9–16 — because the two amplifiers are
  **independent players that can't be audio‑synced**. So a zone appears (and can be
  toggled) only under **its own** amp's column; the other stream column is blank for
  it. Rows are ordered by zone number (1–16, and beyond for more amps). **Tap a stream
  header** → a panel with **now playing**, a **preset** picker, **volume**, transport,
  and an inline **Music Assistant search**. The transport row has **previous /
  pause-play / stop / next**: **pause-play** silences the amp's rooms and brings them
  back (it powers the rooms off/on — the stream keeps running, so resume is instant, and
  it reuses the per-source memory), while **stop** halts the stream itself. (This amp
  can't truly pause a stream — see below — so "pause" is really a room on/off.) Type to
  search that amp's stream player; results are grouped into an **All** tab plus a tab per
  result type (Tracks, Albums, Playlists, Artists, Radio, …), each row showing cover art
  and the provider (Spotify, Radio, Local…). Tap a row to **play it**, or **›** to browse
  into it. There's also a **Browse Music Assistant** button for the full library. For all of this the amp's MA player must be **named the
  same as the amplifier** (rename it to `Axium 1` / `Axium 2` in Music Assistant); the
  panel shows a reminder if it isn't. There is intentionally **no "play on both amps"** — the two amps
  can't be time‑synced, so it would drift badly; for genuine whole‑home audio feed all the
  zone inputs from **one external streamer** (e.g. a WiiM). *Transport (play/pause/next/previous) only works while the
  stream is playing **through Music Assistant** — the amp's DLNA renderer ignores
  pause/skip for playback started another way; control it from Music Assistant itself
  in that case.*

**Show/hide** what appears: both the source card and the matrix card have
**Zones to show** (and the matrix card, **Sources to show**) fields in the
visual editor — leave them empty for everything, or pick a subset to declutter
a card. In YAML these are `zones:` (zone entity_ids) and `sources:` (source
ids).

The `+`/`−` volume buttons on the **source** card send a relative step
(`volume_up`/`volume_down`) to every zone playing that source — each zone moves
by the same amount from its own level, so relative differences between rooms are
preserved (it does not force them to one absolute level). Absolute per-zone
volume lives on each zone's device page.

### Volumes card

The **Axium Volumes Card** (`type: custom:axium-volumes-card`) shows a **vertical
volume slider per zone** (plus a mute button and the live level) — a quick way to
balance the whole house at a glance. Drag a slider to set that room's absolute
volume. If a zone has a **max volume** set (its *Max volume* number), the range
above it is **greyed out** and the slider won't go past it — the same cap now
shows on the matrix zone popover's volume slider too. Its visual editor has a
**Zones to show** field to pick exactly which rooms appear (empty = all); in YAML
that's `zones:`.

## Sleep timer & alarms

- **Sleep timer** — each zone has a *Sleep timer* number (minutes). Set it and the
  zone fades down and powers off when it elapses; set it back to 0 to cancel.
  There's also a hub-level **All-zone sleep timer** that fades and powers off
  *every* zone at once (the amp then idles into standby per its Auto standby
  setting); it appears as the top **All zones** row in the Sleep Timers card.
- **Alarms (wake-to-music)** — add them under **Settings → Devices & services →
  Axium → Configure → Add a wake-to-music alarm**, or from the **alarms card**: a
  name, time, weekdays, zones, source and target volume. At the set time those
  zones power on, switch to the source and gently fade up to the target volume.
  You can also set **Auto turn-off after N minutes** — the woken zones power back
  off that many minutes after the alarm fires (0 = leave them on). On the alarms
  card you can also **"Wake to Music Assistant"** — the **same search** as the
  matrix stream panel opens right in the add form (type to search — it runs
  automatically about a second after you stop typing — with results tabbed by
  type, or browse the library and drill in), and you **pick** a
  track/playlist/album/radio as the wake song; the alarm streams it to the rooms
  via the amp's stream (needs the amp's MA player named after the amplifier, e.g.
  `Axium 1`). The **Alarms** switch on the
  amplifier device arms/disarms them all at once (e.g. while you're away).

Each alarm also gets a **next-fire timestamp sensor** (`sensor.axium_alarm_<name>`)
and each zone a **sleep-end timestamp sensor** (`sensor.<zone>_sleep_ends`). These
are `device_class: timestamp`, so the time left is usable in automations, e.g.
`{{ (states('sensor.axium_alarm_wake') | as_datetime - now()).total_seconds() }}`.
The **Axium Alarms Card** and **Axium Sleep Timers Card** are **interactive**:
the alarms card lets you enable/disable each alarm, edit its time and days,
remove it, and add new ones inline (and each alarm row shows its target zones plus
a one-line **what it plays** — the wake song `♪ <title> · <amp>` when set to Music
Assistant, otherwise the source name);
its editor also restricts which **zones** and **sources** are offered in the
Add form (empty = all), like the matrix card;
the sleep-timers card lets you start a timer per zone (15/30/60/90 min, or
**Custom…** for any number of minutes) and
cancel it — all with the live countdown. The sleep-timers card's editor has a
**Show** option to choose which sections appear — the **all-zones timer**,
**individual zones**, and/or **presets** (a per-preset sleep control that
applies to that preset's zones) — in any combination, plus a **Zones to show**
filter to limit the individual-zone rows to the rooms you pick (empty = all).
Behind the scenes the alarm edits use the `axium.set_alarm` / `axium.remove_alarm`
services (also callable from automations), and the sleep card uses each zone's
sleep-timer number entity.

> Note on EQ: the Axium protocol defines an equaliser command (`0x21`) but the
> spec marks it **"Unsupported by Axium products"**, so a parametric EQ can't be
> implemented. Use the per-zone **bass / treble / balance / loudness** controls,
> which the amplifier does honour.

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
