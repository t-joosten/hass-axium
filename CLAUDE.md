# Working conventions — hass-axium

Read this at the start of every session and apply it. **Keep it current:** when the
user establishes a new preference or the workflow changes, update this file in the
same pass (it's the durable, repo-shared record — see [Maintaining this file](#maintaining-this-file)).

`hass-axium` is a Home Assistant custom integration controlling Axium multi-zone
amplifiers over Ethernet (TCP 17037), distributed via HACS. Repo:
`t-joosten/hass-axium`. Integration code lives in `custom_components/axium/`.

## Release & versioning

- **Versioning is `v0.0.x` patch increments.** The next release after `v0.0.N` is
  `v0.0.(N+1)`. Do not jump minor/major without being asked.
- **Bump `manifest.json` `version` ONLY at release**, not per commit. One manifest
  bump per release, matching the tag.
- **All releases are pre-releases** until the integration is verified on real
  hardware. Cut them with `gh release create vX --prerelease`.
- **Hardware-verification checklist = GitHub issue #1.** Everything is currently
  simulator-verified only. Add any new unverified framing to issue #1. Reaching a
  stable (non-pre-release) `v0.1.0` means that checklist is done.

## Release flow (every change)

1. Make the change. **Update `README.md` in the same pass** — keep docs in sync with
   code, never as a follow-up.
2. **Keep `scripts/simulator.py` in sync** — it emulates the amp's *control protocol*
   (not AirPlay/audio). Every new command the integration sends must be handled by the
   simulator so features can be verified end-to-end.
3. Verify against the simulator (see below). For card JS also run `node --check` and,
   for non-trivial logic, a small standalone node harness with a fake `hass`.
4. Bump `manifest.json` `version` to the release version.
5. Commit. End the commit message with:
   `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
6. Push `main`, watch CI to green (`gh run watch`), then `gh release create vX --prerelease`.
   - CI = hassfest + HACS validate. Both must be green. (The HACS `brands` check is
     expected to be ignored since the domain isn't in home-assistant/brands.)
   - Pushing straight to `main` may get flagged by the auto-approver; if a combined
     `commit && push` is denied, commit first, then push as a separate step.

## Verifying against the simulator

- `python scripts/simulator.py` emulates an AX-800DAV on TCP 17037. Run it in the
  background and stop it with **TaskStop** (a user rule denies `powershell -Command`
  via Bash; do not use taskkill/powershell to stop it).
- `python scripts/probe.py <host>` is a standalone stdlib diagnostic — sends framing
  and prints decoded replies. Output is ASCII-only (Windows cp1252 consoles can't
  print Unicode arrows — use `>>`/`<<`).
- The integration package `__init__` imports HA, so unit-test submodules by loading
  them directly rather than importing the package.
- **Real amplifiers do NOT echo a *set*** — they only reply to an explicit *request*
  (verified on hardware, AX-800-X fw v5). So every write is followed by a read-back:
  media_player setters call `_refresh()` (→ `async_request_zone_state`); the controller
  name/gain/special/auto-power setters and the tone `number` re-request after writing;
  `async_set_source_name`/`async_set_zone_name` re-read the name. Without this, control
  changes never reflect in HA on real hardware and the cards look dead. The **simulator
  models this**: a client's set updates state silently (`echo=False`); only a change
  typed at the sim console broadcasts (front-panel emulation). The sim's old
  always-broadcast-on-set behavior *hid* this whole class of bug — keep sets silent.
- **Zone polling**: the amp broadcasts changes made by *other TCP controllers* (verified:
  a second client's source change reaches HA), but front-panel/IR changes aren't reliably
  pushed. `__init__` schedules `async_track_time_interval` (30s) → `controller.async_poll_zones`
  (re-reads power/mute/volume/source for every known zone, **plus source names** so an on-amp
  rename shows without a reconnect) so on-amp changes reach HA and the cards. Firmware/temp come
  from the temperature sensor's own extended-info poll. Poll re-requests are cheap and cause no
  state write when unchanged.
- **Reconfigure flow**: `config_flow.async_step_reconfigure` lets the user edit host/port on an
  existing entry (Settings → the entry → Reconfigure) without losing discovered zones/sources —
  needed because the ICS-bridge amp IP changes on reboot ([[axium-amp-network-setup]]). It
  re-probes to validate, then `async_update_reload_and_abort(data_updates=…)`.
- **Media Player source detection** (verified on the AX-800-X, fw 5.6.0): the amp HAS an internal
  **Media Player** (source `0x12`) — it answers media-status there and `0x12` is selectable — but
  NO **AirPlay** (`0x10` silent; `0x3A/88h` AirPlay-status also silent). So it streams via
  UPnP/DLNA/Pandora/TuneIn/USB, not AirPlay. The integration **auto-detects** internal players:
  `_request_media_sources` probes each `MEDIA_SOURCE_BYTES` with a media-status request at connect;
  a `0x3E` reply marks it present (`controller.media_sources()`), and the media_player appends them
  to `source_list`/`source_ids`/select via `_effective_source_ids`. now-playing/transport already
  lights up for `MEDIA_SOURCE_BYTES`. Simulator (a DAV) answers media-status only for `>=0x10` sources
  it has (0x10+0x12) — keep it that way so the integration doesn't over-detect Media Player 2-8.
- **One internal media player per *stack*, not per amp** (probed on the real 2-amp AX-800-X stack):
  only Media Player 1 (`0x12`) answers a media-status probe, and it answers for **every** zone on both
  amps (1-16); `0x13`-`0x19` and AirPlay `0x10` stay silent. `0x29` (Source Name and Options) lists only
  the 8 analog sources (S1-S8, device byte 0) — the media player is not a reassignable source slot there.
  So a second internal player would need enabling/assigning **on the expansion amp itself** (feature-
  unlock/setup, not in the control protocol; the expansion amp isn't even directly TCP-reachable — only
  via the master's relay). Nothing to change in code: `_request_media_sources` already probes `0x12`-`0x19`,
  so if a 2nd player were ever enabled it'd auto-appear. **CORRECTION — the Media Player stream is
  PER-AMP, NOT stack-wide** (an earlier "stack-wide" claim here was WRONG; disproved by direct
  hardware listening tests on 2026-07-10): each amp drives only ITS OWN zones' Media Player.
  Amp 1's MA stream → zones 1-8, amp 2's → zones 9-16; they are **independent** streams. Verified:
  playing on amp 2's MA player (`media_player.zone_16_2`) made zone 9 play *that* content while amp 1
  played something else; with amp 1 streaming and amp 2 idle, zone 9 was **silent**. The `0x12`
  media-status answering for all 16 zones (above) is about *source detection*, not audio routing —
  don't infer stack-wide playback from it. **The amps CANNOT be synced**: `media_player.join` → HTTP
  500 (GROUPING feature bit off), and pushing amp 1's live flow URL to amp 2's renderer makes amp 2
  play amp 1's *queue from its own start position* (different song), so there's no way to time-align
  them. **Whole-home audio = play the same content on BOTH amp MA players** (drifts slightly, no sync).
  Zones tap in/out via the Media Player source + power; toggling a zone must NOT stop its amp's stream.
- **`state` must check power FIRST** (media_player `AxiumMediaPlayer.state`): while the shared internal
  Media Player plays, the amp reports **every** zone's source as `0x12` with the **turn-on bit clear**
  (verified on hardware: powered-off zones show `POWER=OFF SOURCE=0x12`). `media_state(0x12)` is global,
  so reading it before power made those off zones report `PLAYING` — which lit up every zone's Media
  Player cell in the matrix. So: `power is None`→None, `not power`→OFF, else check the media source. An
  off zone is OFF regardless of the shared player.
- **Per-source audio delay is per-ZONE (0x31)** — verified on real hardware (probed the amp:
  each zone replies with a delay byte PER SOURCE; zone 2 had `[0,0,0,0,0,0,0,120]` = S8 600 ms,
  matching the amp web app's *Delays* screen). NOT a single per-zone value — the OLD `state.audio_delay`
  (one byte) was wrong. Now `ZoneState.source_delays` = `[ms per source, S1 first]`; the setter
  `async_set_source_delay(zone, index, ms)` resends the WHOLE 8-byte array (others from cache) with just
  one entry changed, then re-reads (the amp doesn't echo a set). Exposed as **`AxiumSourceDelay` numbers,
  one per source (S1..S8) per zone**, on the ZONE device (`number.py`, `EntityCategory.CONFIG`, **mode
  SLIDER**, **0-600 ms / 5 ms steps**). **MAX IS 600 ms (byte 120), NOT 1275** — verified on hardware: the
  AX-800(-X) firmware/web app cap at 600 ms and **silently reject** a set of 605 ms+ (the delay stays at
  its previous value, no error). A 1275 ms slider was the "after sliding I can't update again" bug: any
  drag above 600 ms was rejected so the value never moved. `AUDIO_DELAY_MAX_BYTE=120` caps the entity and
  clamps the outgoing bytes (`async_set_source_delay`); the sim models the reject (ignores per-source bytes
  >120, keeping the prior value). The entity **`name` is a live property** = `<source name> delay` —
  it maps its source number → byte (`SOURCE_NUMBER_TO_BYTE`) → `controller.source_name(byte)` (the amp's
  renamed name, e.g. "PC delay"), falling back to `SOURCE_BYTE_TO_NAME` ("Source N"); it registers a
  **diagnostic listener** (as well as the zone listener) so a source rename refreshes the label. The
  entity_id stays the `..._source_N_delay` original (registered in v0.0.103), so the rename-driven label
  only changes the friendly name. "No delay for the media player source" (protocol), so only the 8 analog
  sources. Simulator: `Zone.source_delays` (list of 8); 0x31 handled separately (reply lists all sources;
  a single-byte command sets all — the protocol's back-compat rule). **Replacing `audio_delay` left 16
  orphaned `..._zone_N_audio_delay` numbers** (permanently `unavailable`, one per zone) — `number.py`
  `_prune_obsolete_numbers` (called each `async_setup_entry`) removes any `number` whose unique id ends
  with a suffix in `_OBSOLETE_NUMBER_SUFFIXES` (`("_audio_delay",)`), so a removed number-key self-cleans
  on reload. Add future removed number keys to that tuple. (Same pattern as the alarm-sensor prune.)
- **What the control protocol can/can't do** (AxiumCommsProtocol.pdf, 25pp): network config IS
  settable — `0x3A`: setting `01h` = amp network name, `02h` = NTP time server, `03h` = flags
  (bit0 0=DHCP/1=Static) + 16 bytes IP/subnet/DNS/router; `83h` reads the IP config back;
  `08h`/`88h` = AirPlay enable/status (only meaningful on AirPlay hardware). We implement `01h`
  (name, mirrored from the hub rename) and `03h`/`83h` (IP). The amp's clock/DST + NTP (`02h`)
  and KNX/feature-unlock (`0x64`)/digital-IO are **intentionally not implemented** — the amp's
  native scheduler is unused (HA does alarms/sleep), and the rest are niche/model-specific.
  **UPnP/DLNA/Pandora/TuneIn are NOT in the protocol** (amp web-UI/app only, at `http://<amp-ip>`);
  media servers (SMB shares) = `0x3B`; media control/status = `0x3D`/`0x3E`/`0x3F`.
- **Multi-amp stacks / per-unit devices**: the controller tracks a `UnitInfo` per amp
  (`controller._units`, keyed by unit id; `units()`, `unit(id)`, `primary_unit_id`). Device
  info (0x14) accumulates one unit per reply; extended info (0xB9) is keyed by the unit id at
  `data[2:4]` and requested **per unit** (`async_request_extended_info(unit_id)`). Legacy
  single-value props (`temperature`/`_firmware`/`_mac`) mirror the **primary**. **Device model:
  the logical HUB and the PRIMARY AMP are SEPARATE devices** (so they carry independent names —
  hub "Axium Hub" vs primary amp "Main"): the hub is `(<entry>)` (model "Hub", a container), the
  primary amp is `(<entry>_amp_primary)` (`via_device` the hub) — its identifier has NO `_unit_`
  so the dashboard still treats it as the master stream; expansions are `(<entry>_unit_<uid>)`
  `via_device` the hub. `_amp_identifier(primary)` → `_amp_primary`. **The primary-amp identifier is
  `helpers.primary_amp_identifier(entry_id)` — use it, never the raw `f"{entry_id}_amp_primary"`
  literal** (a copy-paste of the old `(DOMAIN, entry_id)` idiom silently strands an entity on the hub;
  that's how clipping/preset-select/source-name got left behind and later moved). ALL amp-hardware
  entities go on the amp device (zones' `via_device`, temp/peak, **clipping binary_sensor**, static-IP,
  auto-power/gain switches, source-gain + standby numbers, **preset select, source-name texts**); only
  the stack-wide/system entities stay on the hub (alarm switch + alarm sensors, all-zones sleep number
  + sensor). Renaming the **hub** device syncs the entry title only;
  renaming the **primary amp** device (or an expansion) pushes the amp network name. **Migration
  note:** on upgrade, existing amp entities re-parent from the old hub device to `_amp_primary`
  (unique_ids unchanged, so no data loss); the old hub keeps its `name_by_user` (rename to "Axium
  Hub"), the amp defaults to "Axium 1" (matching the amp-stream MA player name), and expansion amps keep their own names. **MAC-collision trap (fixed):** the amp's MAC was still registered on the OLD hub
  device from before the split, so `_update_unit_extended` setting it on `_amp_primary` raised
  `DeviceConnectionCollisionError` on EVERY extended-info (0xB9) reply — which unwound the read loop
  and made the controller reconnect (a flapping connection: zones up, sources flashing then gone).
  Setup now clears the hub's leftover `connections` (`async_update_device(hub.id, new_connections=set())`)
  so the primary amp claims the MAC. **Defensive:** the controller wraps its device-info / extended-
  info / stack callbacks in try/except + `LOGGER.exception` — a registry callback must NEVER take down
  the amp link (that's what turned a one-line bug into a full outage). **The entity-listener dispatch is
  guarded too:** `_notify` (per-zone) and `_notify_diagnostics` both wrap `callback()` in try/except —
  they fire from the read loop just like the registry callbacks, so a raising `_handle_update` would flap
  the connection the same way; guarding only the registry callbacks left that gap. Zones
  nest under their owning amp (`media_player` `via_device`); per-unit temp/peak sensors
  (`AxiumSensor(unit_id=…)`, expansion ids suffixed `_unit_<uid>` so primary sensors aren't
  orphaned). Config: `CONF_UNITS=[{unit_id,primary}]` + `UNIT_KEY` on each `CONF_ZONES` entry
  (`helpers.zones_from_units`/`units_config`/`get_units`). **Auto-detect**: controller debounces
  a `set_stack_callback` ~2s after the stack settles; `__init__._handle_stack` reloads the entry
  when a new unit/zone appears (guarded to add-only; primary implicit for legacy config → no
  spurious reload). The **reconfigure flow re-scans** the stack too. Simulator: `--peer-zones`
  gives a 2nd unit; its 0x39 handler now replies **per requested unit** (distinct temp/fw/MAC).
- **Stack discovery + zone-conflict auto-resolve** (verified on real 2-amp hardware): the runtime
  `_request_device_info` must **NOT** set `NO_EXPANSION_REPLY` (0x01) — that flag makes real amps
  answer only for the connected unit (the sim ignores it, which hid the bug); use
  `REPLY_ON_PORT_ONLY|LIST_ZONES` (0x06) so the connected amp relays to and surfaces every stacked
  unit. A factory expansion amp also claims zones 1-8 (clash), so units won't merge; the controller
  auto-resolves in `_run_stack_check` → `_find_zone_conflict`/`_resolve_zone_conflict`: reassign the
  non-primary clashing unit to a free block via `async_set_zone_assignment` (**`CMD_ZONE_ASSIGN`
  0x2E** = dev id + zone list; **relays across the stack** — send it to the connected amp and it
  reaches the target), then re-discover. `_resolved_units` guards against loops; the primary is never
  touched. Self-healing: re-applies each connect (0x2E may not persist a reboot). Sim handles 0x2E
  (updates `peer_zone_numbers`); test the clash with `--peer-zones "1=..,..,8=.."`.
- **Per-amp diagnostics/network**: extended info (0xB9) also yields `UnitInfo.ip`, `manufacture_date`
  (→ device `hw_version`) and `locked`. Network settings (0x3A) are **per unit** (keyed by the unit id
  in the reply, requested per unit at connect, relayed like 0x2E): `network_known/is_static/ip(unit_id)`
  + `async_set_network_static(static, unit_id)`. So there's a **Static IP switch per amp** device (primary
  keeps the legacy unique id; expansion suffixed `_unit_<uid>`). media_player exposes `zone_number`
  (stack-wide physical zone).
- **Zone device model = physical channel (+ pre-out)**: each zone device's `model` is `Zone <N>` where
  `<N>` is the zone's 1-based **physical channel within its owning amp** (`helpers.amp_zone_positions`),
  matching the amp web app's "Amp Zone" (a stacked amp's zones 9-16 show as Zone 1-8 under that amp).
  Shown as the device's subtitle in the devices list. **Zone type is NOT in the control protocol** —
  verified by raw probe: device-info (0x94) lists zones as plain bytes (`01..08`), no pre-out marker,
  and the amp's own web app (which tunnels the *same* protocol over `/axium.cgi`) computes it from a
  fixed per-model layout. So `helpers.zone_type_label` mirrors that: `PREAMP_ZONES_BY_MODEL` (const)
  marks AX-800/AX-800-X (type `0x84`/`0x90`) physical channels **7-8 as `Pre-out`** (6 amplified + 2
  pre-out), appended to the model (`Zone 7 · Pre-out`). Derived from the unit's `model_code` (read over
  the protocol), so it needs no HTTP scraping and covers the whole stack. media_player sets it at setup
  (best-effort if `model_code` is known then); `__init__._enrich_zone_models` re-applies it idempotently
  when device info (with the type code) arrives, covering the setup/device-info race. Sources already
  carry their physical label: the source-name text entity is named `Source <N>` via `SOURCE_NUMBER_TO_BYTE`.
- **Static IP switch** (`AxiumStaticIPSwitch`, switch.py): reads the amp's network config at connect
  (`controller._request_network_config` → `_handle_network_settings` caches `NetworkConfig`); the hub
  switch toggles `async_set_network_static` which re-writes the *current* IP/subnet/DNS/router with the
  static flag set/cleared (pins the working IP; DHCP direction can move the IP → risky over the
  portproxy). Turn back to DHCP before relocating the amp to another subnet.
- **Static-IP-across-subnets trap** (hit on real hardware): a static address is only valid on the
  subnet it was set on. If both amps are static on the old subnet and you move them to a new
  router/subnet, they keep the invalid address and are **unreachable** (no DHCP lease, no mDNS/SSDP).
  Flag byte (`0x3A/03` `data[3]`): bit0 = static (`0x03` static, `0x02` DHCP; bit1 is an always-set
  status bit — mask with `NET_FLAG_STATIC`=0x01, don't compare the whole byte). **Recovery** (verified):
  reconnect the amps to the original subnet, clear static (switch off → `async_set_network_static(False)`,
  or write `0x3A/03` with bit0 cleared + current addresses directly to the amp), then move — they DHCP a
  new lease on the new subnet. The amp keeps its address on DHCP renewal, so clearing static on the old
  subnet doesn't drop the connection. HA's switch state can lag the amp; a `config_entries/reload`
  re-reads the config and syncs it. The primary keeps `.137.2` on the portproxy; the expansion is cleared
  via relay. When the HA switch write's read-back lags, verify the real state with `probe.py --send 3AFF<uid>83`.
- **Zone rename → amp**: renaming a zone's device (HA pencil) is mirrored to the amp.
  `__init__` listens for `dr.EVENT_DEVICE_REGISTRY_UPDATED`; when a zone device's
  `name_by_user` changes it calls `async_set_zone_name` (`CMD_ZONE_NAME` 0x1C, ~15-byte
  cap). We never write the registry back, so it can't loop. (Source names have their own
  editable text entity; zone names use the device pencil.)
- **Hub rename**: the same `_handle_device_rename` listener also mirrors the **hub**
  device's `name_by_user` → the **config-entry title** (so the integrations page shows the
  chosen name) **and pushes the amp's own network name** to the primary via
  `controller.async_set_amp_name` (`0x3A` setting `01h`; the amp name is hostname-style
  `a-z 0-9 - _`, so the display name is slugified by `_slug_amp_name`, cap `AMP_NAME_MAX=31`).
  Renaming an **expansion amp** device pushes the name to *that* unit (relayed). No read-back
  (nothing reflects the amp's own name; it just mirrors the HA name). The **reconfigure flow**
  also carries a **Name** field (the whole stack is one TCP connection — a single host/port —
  so there's no per-amp connection to configure; renaming there updates the title *and* the hub
  device name unless it has a `name_by_user` override). **`async_setup_entry` NO LONGER forces the
  title from the hub device's `name_by_user`** — that was reverting a user's own entry-title edit
  back to the device name on every reload ("Axium Hub" → "Axium 1"). The config-entry **title** and
  the hub **device name** are now independent HA names; renaming the hub *device* (pencil) still
  syncs the title via `_handle_device_rename` + pushes the amp network name, and the hub card shows
  the hub device name (`_hub().name` = `name_by_user||name`). (Note: the hub device IS the primary
  amp device — one device, one name; a separate hub-vs-"Main"-amp split would need re-parenting.)
  A title-only update does **not** reload the entry (`_async_update_listener` compares only
  `entry.options`). Expansion amps are separate devices renamed via their own pencils.

## Dashboard card (`custom_components/axium/lovelace/axium-source-card.js`)

- Vanilla-JS web component (shadow DOM, no build step). Validate with `node --check`
  and **check for stray control/null bytes** before committing:
  `perl -ne 'print "$." if /[\x00-\x08\x0b\x0c\x0e-\x1f]/'`. A null byte has twice
  slipped into a string literal that renders as a space (e.g. `.join(" ")`), making
  `file` report "data"/"binary" — replace the separator with a printable char.
- Chip interactions: **tap** toggles the zone, **long-press (500ms)** opens the zone's
  device page (`_attachChipHandlers`/`_openZoneDevice`, pointer events; navigates to
  `/config/devices/device/<device_id>` via a `location-changed` event, more-info
  fallback). Device id comes from `hass.entities[id].device_id`.
- The same JS file defines THREE cards, all registered in one guard block and pushed to
  `window.customCards`: `axium-source-card`; `axium-hub-card` (compact amp status:
  model/fw/zones-on/temp/clipping + all-off; tap opens the hub device page); and
  `axium-matrix-card` (zones × sources routing grid; tap a cell to route a zone to a
  source, tap the zone's currently-active cell to turn that zone off — no Off column).
  The matrix headers are interactive too (a popover overlay, `#overlay`/`#sheet`, covering the **dashboard
  content but NOT HA's sidebar/header**: `.overlay` is `position:fixed`, and `_showOverlay` measures
  `ha-sidebar` (`document.querySelector("home-assistant")…ha-sidebar` bounding rect) to set the overlay's
  `left`, and offsets `top` by `var(--header-height,56px)` — best-effort, falls back to full-viewport if
  HA internals moved. `.sheet` fills that region as a flex column whose `.ssresults` flexes; closed by the
  backdrop. It used to be `inset:0` full-viewport, which covered the menus — the user didn't want that.):
  **tap a zone name** → the zone popover (`_openZonePanel`): a
  **power** toggle (`_togglePower`), a **volume** slider + mute, and the zone's **tone/EQ** controls —
  **bass/treble/balance** sliders (`number.set_value`) + **loudness** and **mono** toggles
  (`switch.toggle`; both rendered by a generic `toggle()` and refreshed by iterating `.toneswitch`),
  found on the zone's device via `_toneEntities` (matched by entity-id suffix `_loudness`/`_mono`).
  **Mono is hardware-verified** (AX-800-X: toggling `..._mono` on/off is accepted and reflected in the
  0x0C special-features read-back) — it's the fix for a **single-speaker zone** (sums L+R so the one
  speaker plays the full mix; balance on such a zone only changes the connected side, which is expected,
  not a bug — the amp accepts balance −20..+20 both ways, also verified).
  Now-playing and transport
  were removed from the zone popover (they belong to the stream, not the room). Slider debounced via
  `_scheduleVolume`, live-refreshed by `_refreshPanel` from `_update` unless mid-drag (tone sliders
  skip refresh while `_panel.toneDrag`); `disconnectedCallback` clears the timer.
  **Amp streams link by amp — NOT by zone name** (names collide with unrelated devices, e.g. a "Woonkamer" TV):
  `_ampNameFor` walks the device tree (zone device → `via_device` amp device → `name_by_user||name`,
  e.g. "Axium 1"); `_ampStreamPlayerFor` returns the `music_assistant` player whose friendly_name
  **equals that amp name** (so the user renames the ~2 amp-stream MA players to the amp device names in
  MA; unmatched → null → falls back to the zone, never a wrong device). Turning a zone **off**
  (`_turnZoneOff`, used by both `_togglePower` and the cell-tap `_route`) **only powers off the amp zone
  — it must NOT stop the MA stream**: an amp's Media Player stream is shared by all its zones (its 8
  per-amp DLNA renderers alias one stream), so stopping it silences every other zone on that amp (the
  reported "disable one zone → all go silent" bug). The state fix
  (power-first) is what makes an off zone read OFF instead of the shared stream's PLAYING. **Amps
  advertise only
  1 MediaRenderer/amp via SSDP** (the first embedded zone) so MA/HA discover only a few of the 16
  per-zone renderers — an amp-firmware limit, not fixable card-side. **hold a zone name** →
  open the zone device page (`_attachHold`, reused 500ms hold pattern); **tap an analog source name**
  → preset picker (`_openPresetPanel`) that applies a preset onto that column set-exactly
  (`_applyPresetToSource`: preset zones → that source, other zones on it → off), mirroring the
  source card's preset semantics.
  **The "Media Player" source column is split into one STREAM column PER AMP** (`_columns()` +
  `_amps()`, which groups zones by their `via_device` amp → `Axium 1`/`Axium 2` and flags the **master**
  = the amp whose identifier has **neither `_unit_` nor `_zone_`**). The `_zone_` exclusion matters:
  `_amps()`/`axiumAmps` fall back to the zone device (`|| zdev`) when the `via_device` amp isn't
  resolvable yet (a registry-sync window, e.g. the split's re-parent) and a zone id also lacks `_unit_`,
  so `!includes("_unit_")` alone mis-flagged a lone zone as the master amp. Each stream column carries the media source id (0x12)
  so routing/toggle is unchanged. **Ownership is per-amp (the corrected, honest model):** each stream
  column owns **only its own amp's zones** (`Axium 1` → 1-8, `Axium 2` → 9-16; `.cell.blank` for the
  rest) — an amp's stream can NEVER play another amp's zones, so there's no "master spans all". **Zones
  rest) — an amp's stream can NEVER play another amp's zones, so there's no "master spans all".
  **Highlighting** (`_streamCellActive`) is now trivial: a stream cell only exists for the zone's own
  amp, so the zone lights there whenever it's powered on and on the Media Player. **Tapping a stream
  cell** (`_route(zone, src, ampId)`): the room's *active* cell → turn that room off; else put it on the
  Media Player and `media_play` (resume) that amp's stream. **Tapping a stream header** opens
  `_openStreamPanel(ampId)`: now-playing + transport + volume driving that amp's MA player
  (`_ampStreamPlayerByName(amp name)`), a **preset dropdown** (`_applyPresetToStream`: start the amp's
  stream, its own preset rooms → Media Player, drop the amp's others), an inline **Music Assistant
  search** — the **shared `<axium-ma-search>` custom element** (`AxiumMaSearch`, own class + shadow DOM),
  embedded by BOTH the matrix stream panel (`mode="play"` — a row tap plays it now via `enqueue:"play"`)
  AND the alarms wake-song picker (`mode="pick"` — a row tap fires a **`pick` CustomEvent** to store it and
  plays nothing; `startBrowse=true` opens on the library root). One implementation → identical search UX
  everywhere. Set-once props `.hass`/`.player`/`.mode`/`.startBrowse` (no attributes); parents push fresh
  `.hass` on every update (it's read at query time). **Searches auto-run ~1s after typing stops**
  (debounced) as well as on Enter / the button. Results group into an **All** tab (default, all hits) plus
  a tab per type (`_tabOrder`/`_tabLabel`: Tracks/Albums/Playlists/Artists/Radio/…), common types first
  then the rest alphabetically, so **nothing a search returns is dropped**; empty search → no tabs. **A new
  search keeps the currently-selected tab** if the new results still have that category (else falls back to
  All) — so refining a query from within e.g. the Tracks tab stays on Tracks.
  **Bucketing (`_bucket`) is NOT purely `media_class`:** radio stations come back with a generic
  `media_class: "music"` (radiobrowser/TuneIn) — detect them by the **raw content-id provider prefix**
  (`radiobrowser://`/`tunein://`/`radionet://`, or `media_class === "radio"`) and put them in a dedicated
  **Radio** tab; the leftover generic `"music"` is labelled **Music**. (Match the raw prefix, NOT
  `_providerLabel`'s display string — that would couple bucketing to a human-facing label.) Rows show
  cover art, title, and the **provider** (`_providerLabel`). A **"›"** drills in (`_drill(item, back)`),
  **one browse per tap, on demand**, with a spinner while loading (MA↔provider-bound). **Back steps up
  exactly one level at any depth:** each list is rendered with a `back` (parent re-render, null at top)
  and a `rerenderSelf` (passed as the child's `back` when drilling) — no `_state.home` single-level hack.
  Error paths use `_renderError(msg, back)` which KEEPS the Back button (don't overwrite `.ssresults` with
  bare text). **Do NOT re-add a `media_play` nudge** in play mode: this MA player reports `state:"playing"`
  even while paused (DLNA desync, verified via `media_position` freezing), so a nudge un-pauses a
  just-paused stream (the old "pause doesn't work" bug); `enqueue:"play"` reliably auto-starts without one
  (`"replace"` was flaky for a lone track). **Switch-from-playing double-fire** (`_activate`): verified on
  hardware that a `play_media` arriving while the amp's renderer is already PLAYING **stops it (→ idle)
  instead of switching** — and a second `play_media` from the now-idle state actually plays the new track
  (the user's "tap once → music stops, tap again → plays"). So `_activate` fires `play_media`, and **if the
  player state was "playing", fires it again ~1.5s later**. From idle it's a single fire (no double). Don't
  "simplify" this to a single call. **The deferred play is a stored, cancellable timer** (`_playTimer`,
  cleared by `_cancelPlay`): cancelled at the start of a new `_activate` (rapid taps don't stack), in
  `disconnectedCallback`, and via the public `cancelPending()` which the matrix `_closePanel` calls — else
  it would fire `play_media` ~1.5s AFTER the user closed the popover / hit Stop (the popover only hides,
  it doesn't remove the element). **`_activate` (play mode) dispatches a `play` CustomEvent** so the stream
  panel can set its optimistic `_panel.streamPlaying=true` + refresh the stop-button icon (the isolated
  component can't touch `_panel`, and `_refreshStreamPanel` never sets streamPlaying=true because reported
  "playing" is untrusted) — without it the Stop button stayed on ▶ and did `media_play` (resume) after a
  search-initiated play. **DO NOT prefetch per-row browses** (an old `_streamItemCount`
  fired a `browse_media` per album/playlist row for a track count — a burst of ~10-15 concurrent
  `browse_media` calls **hangs Music Assistant**; removed). The WS calls are the module fns
  `axiumMaSearch`/`axiumMaBrowse`. NB: `search_media`/`browse_media` return items whose
  `media_content_type` is a generic **`"music"`** for every class (Spotify etc.) — the `media_class` is
  what differentiates track/album/artist/playlist; pass the item's own `media_content_type` back to
  browse/play (it works). **Async guards:** every `await` in the search/browse path re-checks the panel is
  still the same object (`this._panel === panel`) and search re-checks `panel.searchSeq` — closing the
  popover, switching amps, or a slower older query can't render into the wrong/torn-down panel. NB: `search_media` returns only
  title/thumbnail/type/content_id/can_* — NOT duration, year, or a separate artist (baked into the
  title); MA exposes no per-item WS for the richer fields. There's also a **Browse Music Assistant** button
  (native `hass-more-info` on the amp's MA player). Shows a "rename the MA player to <amp name>"
  hint when unmatched. `_refreshPanel` dispatches to `_refreshStreamPanel` for `type:"stream"`.
  **NO "play on all amps"/whole-home** — it was built then REMOVED: the two amps can't be time-synced
  (see the media-player note), so playing the same content on both drifts badly and is useless. A single
  external streamer (e.g. WiiM) feeding all zone inputs is the real whole-home path, not the control
  integration.
  **Zone ordering:** every card lists zones by physical `zone_number` (1..16+) via module helpers
  `axiumZoneNumber`/`axiumSortZones` (matrix/source/volumes/sleep/alarm-add). Card editors' "zones to
  show" is a **sorted `select`** (not the HA entity picker, which can't be ordered) via
  `axiumZoneSelectOptions`, so the config lists zones 1..16 too. Sleep rows sort by the zone number
  parsed from the timer entity id (`_sleepZoneNum`).
  Matrix **corner power button** (`.allpower`, `_toggleAllPower`): if any zone is on → `turn_off` all,
  else `turn_on` all; highlighted `.on` when any is on. **Per-source power toggle** (`.srcpwr` in each
  `.colhead`, `_toggleSourcePower`): OFF (source has active zones) → remember them + `_turnZoneOff` each;
  ON (none active) → `_route` each remembered zone back onto that source (analog: `select_source`; stream:
  `media_play` + Media Player). The remembered set persists in **localStorage** keyed `axium-matrix-srcmem:
  <hubId>` (survives reload; empty memory → ON is a no-op). `_colKey` = analog id or `stream:<ampId>`;
  `_activeZonesForColumn` uses `_streamCellActive` for streams, `_currentSourceId === id` for analog;
  `.srcpwr.on` lit in `_update` when the column has any active zone. The button `stopPropagation`s so it
  doesn't also open the header's preset/stream panel. The **alarms card** Add form (collapsed until
  `+ Add alarm`; `.addform[hidden]` needs its own display:none rule since `.addform{display:flex}` beats
  the UA `[hidden]`) lists **amp streams** (`_ampStreams`/`_maByName`) in its source select — a `stream:`
  option sets `source`=media-player-byte + `media_player`=that amp's MA player; `src:` options are the
  analog sources. **Layout** (`_buildAddForm`): labelled fields (`.af-field` = `.af-label` over a control),
  Name+Time in a 2-col `.af-row2`, **quick day presets** (`.qd`: Every day / Weekdays / Weekend → set the
  `.f-days` chips; days are 0=Mon..6=Sun), 2-letter day chips, `Rooms`/`Wake to`/`Volume`/`Auto turn-off`
  sections, and a right-aligned `.af-actions` footer (Cancel → `_toggleAdd`, Add). Selectors `_submitAdd`
  reads are unchanged (`.f-name`/`.f-time`/`.f-days .daychip.on`/`.f-zones .zonechip.on`/`.f-source`/
  `.f-volume`→`input[type=range]`/`.f-duration`).
  The hub card finds hub-owned entities via `entityHub` + the entity-registry `platform`,
  and the hub device by identifier `["axium", <hub id>]`. The matrix + hub cards reuse
  `axium-hub-card-editor` (hub + name) for their visual editor.
- Source card volume: `+`/`−` send `volume_up`/`volume_down` (relative step) to all zones
  on that source — each moves by the same amount from its own level (not equalised to one
  absolute level). Axium has no master-volume command.
- **EQ is NOT implementable**: protocol command `0x21` (Equalisation) is marked
  "Unsupported by Axium products" and its Frequency/Gain/Q are "only stored and not used
  by the amplifier." Don't build a parametric EQ; the real tone stack is bass/treble/
  balance/loudness (already implemented). (Corrected an earlier wrong assessment.)
- **Sleep timer**: per-zone `AxiumSleepTimer` number (number.py) — fades volume down over
  the last ~30s then powers the zone off; restores the pre-fade volume. HA-side asyncio
  task, no protocol dependency. Also `AxiumAllZonesSleepTimer` (hub device) fades + powers
  off ALL zones (`CMD_POWER ZONE_ALL`); its deadline is stored under the `"all"` key and it
  has a hub-level `AxiumSleepSensor(zone="all", hub_device=True)`. Both numbers carry
  `axium_kind: "sleep_timer"`; the all-zones one adds `sleep_all: true` (sleep card sorts it
  first, labels it "All zones"). **Both `_run`s re-read zone state after the power-off**
  (`async_request_zone_state` per zone; the amp doesn't echo a set and a `ZONE_ALL` off isn't
  picked up per zone until the 30s poll), so HA and the matrix reflect the off immediately —
  without this the matrix kept showing the zones (and the all-power button) as on.
- **Alarms (wake-to-music)**: HA-side (the amp's native alarm needs clock+preset+favourite
  config — too rigid/unverified). Stored in options (`CONF_ALARMS`, helper `get_alarms`):
  `{name,time,days[0=Mon..6=Sun],zones[entity_ids],source id,volume,enabled,duration,media,media_type,media_title,media_player}`.
  Managed via options-flow steps `add_alarm`/`remove_alarm` — the `add_alarm` step also carries a
  **`duration`** number and a **`media`** `MediaSelector` (browse to a wake song; its dict
  `{entity_id, media_content_id, media_content_type, metadata.title}` maps to the alarm's
  media/media_type/media_player/media_title). Scheduler `_async_setup_alarms` in
  __init__ registers `async_track_time_change(second=0)`; on a due minute it activates each
  zone via `controller.async_activate_zone(zone, source, start)` (the shared power-on + source
  `| SOURCE_FLAG_TURN_ON` + volume primitive, also used by the notification service), then fades
  up to target. **`duration` = auto turn-off** (minutes; 0 = stay on): after the fade, `_fire`
  spawns a background task that sleeps `duration*60`s then `CMD_POWER`/`POWER_OFF`s the woken zones
  and re-reads them. Carried through the service schema + `get_alarms` (same must-preserve rule as
  the media fields) + the `alarm_duration` sensor attr; the Add form has an "Auto turn-off after
  <min>" number, and each alarm row shows "· off after Xm". **Wake to a Music Assistant playlist:** if the alarm has `media` (a MA
  media-content-id), it activates the zones on the **Media Player** source (0x12) and calls
  `media_player.play_media` on the **master** stream player (`_master_stream_player`) — `media_player`
  overrides the target. **`_master_stream_player` matches by name in priority order: the primary-amp
  device name (the convention, e.g. "Axium 1"), then the hub device name, then the entry title** — the
  last two are a fallback for entries that upgraded from before the hub/amp split, where the match key
  used to be the hub's display name / title; without it an upgrader who named their MA player after the
  old title matches nothing and the wake song silently never plays. **LIMITATION (per-amp reality):**
  the wake media plays on the master amp only, so a wake song reaches the **master amp's zones**;
  alarm zones on an expansion amp are activated + faded but won't hear the song (would need play_media
  on each activated zone's own amp — TODO if wake-on-expansion is wanted). The alarms card's Add form embeds the **shared `<axium-ma-search>` element in `mode="pick"`**
  (`_openMediaBrowse` mounts it; a `pick` event → `_pickMedia` stores `media`/`media_type`/`media_title`
  on the form dataset → `axium.set_alarm`). It's the SAME search UI as the matrix stream panel (tabs,
  spinner, provider labels, drill-in, 1s debounce) — the old bespoke `_browseTo`/`_searchMedia`/
  `_renderMediaItems`/`_renderCrumbs` flat browser was removed. The Add form's amp-stream source options come from **`_ampStreams`, which matches the primary amp
  by `<hub>_amp_primary` (NOT the bare hub id — that's the empty logical container with no MA player)**
  plus expansions `<hub>_unit_*`; matching the hub id there dropped the primary amp's stream and listed
  the empty hub instead. Master arm/disarm = `AxiumAlarmsSwitch` (switch.py, runtime flag
  `hass.data[DATA_ALARMS_ENABLED]`).
- **Notifications**: `axium.play_notification` service (services.py/.yaml) — plays a sound on
  `zones`/`presets`, then restores each zone **exactly** (power/source/volume/mute, or off). A
  spoken **`message`** (opt. `tts_engine`/`language`) is turned into a `media-source://tts/<engine>`
  id by `_tts_content_id` (engine defaults to the first `tts.*` entity) and flows through the same
  resolve+push path; it takes precedence over `media_content_id`.
  Snapshots `controller.zone_state` (inside a per-entry `asyncio.Lock` so queued calls capture
  the *restored* state), overrides via `controller.async_activate_zone(..., unmute=True)` (the
  shared primitive the alarm also uses). **Default playback = direct UPnP push** (`dlna.py`,
  `async_push` = `SetAVTransportURI`+`Play`) to each zone's own amp renderer at
  `http://<amp-ip>/upnp/av_transport_ctrl<index>` (`_renderer_url_for_zone`: index = per-amp
  channel from `amp_zone_positions` − 1; amp IP = `controller.host` for the primary else the
  expansion `UnitInfo.ip`). **No DLNA discovery needed** — the amp only advertises one renderer per
  amp over SSDP, so auto-discovery can't reach all 16; the push does. Media is resolved+signed via
  `_resolve_media` (`media_source.async_resolve_media` → `async_process_play_media_url`, mime from
  content-type-if-mimey else guessed) so the amp fetches it from HA. Waits with `_wait_dlna_done`
  (polls each renderer's `GetTransportInfo` — start grace, then a run of non-`ACTIVE_STATES`
  samples), or a fixed `duration`, or ~5s. **Loudness is the control protocol (0x04) on the Axium
  zone, NOT the renderer's RenderingControl** — the amp stores a DLNA volume but doesn't apply it to
  output (verified: `SetVolume` changes the reported value, not the sound), so notification volume
  is set on the zone. Optional `media_player` overrides the direct push to route through a given HA
  renderer / MA player (`_wait_media_done` for that path). Restore is in a `finally` (which also
  `dlna.async_stop`s every pushed renderer first, so audio halts before the source switches back):
  only `power is False` powers the zone back off (unknown `None` left on), and an off zone's
  source/volume/mute are restored too (source without the turn-on bit) so its next power-on is
  correct. **The amp can't mix audio** (a zone = one source), so it *overrides* — no true ducking.
  Uses only existing control commands for override/restore (no sim change; DLNA push is real-amp
  only — sim is control-protocol-only). Needs the amp on the main LAN so HA can reach its HTTP/UPnP
  port (the 17037-only bridge doesn't pass UPnP). Verified push on AX-800-X fw 5.6.0.
- **Time-left is exposed as `device_class: timestamp` sensors** (automation-usable):
  `AxiumAlarmSensor` (per alarm, next fire via `helpers.next_alarm_fire`; recomputes on a
  minute tick + `SIGNAL_ALARM_UPDATE` from the switch) and `AxiumSleepSensor` (per zone,
  reads `DATA_SLEEP_DEADLINES` written by the sleep-timer number, updated via
  `SIGNAL_SLEEP_UPDATE`). Both carry an `axium_kind` attribute ("alarm"/"sleep").
  **Deleting an alarm leaves a stale `unavailable` sensor** — its config drops so the sensor
  isn't recreated, but the entity-registry entry lingers on the hub device. `sensor.py`
  `_prune_orphan_alarm_sensors` (called each `async_setup_entry`) removes any `sensor` whose
  unique id is `<entry>_alarm_<name>` for a name no longer in `get_alarms` — so orphaned alarm
  sensors clean themselves up on reload. (Scoped to `domain=="sensor"`; the alarms master switch
  is `<entry>_alarms_enabled`, a different prefix, so it's never touched.)
- Cards `axium-alarms-card` / `axium-sleep-card` (bundle now has SIX cards) find those
  sensors via `axiumKindSensors(hass, hub, kind)` and render a live countdown
  (`axiumCountdown`, `setInterval` in connectedCallback, cleared in disconnectedCallback);
  reuse `axium-hub-card-editor`. Don't compute time-left only in the card — it must also
  be a sensor so automations can use it.
- Cards are **interactive**: the alarms card enables/disables, edits time/days, removes
  and adds alarms via the `axium.set_alarm` / `axium.remove_alarm` services (services.py +
  services.yaml; upsert/remove in options). The sleep card sets/cancels each zone's timer
  via `number.set_value` on the `axium_kind: "sleep_timer"` number. Rows rebuild only when
  the entity set changes (signature check) so in-progress inputs aren't clobbered; per-tick
  updates only the countdown/toggle/time. The alarms card also renders each alarm's target
  zones (from the `alarm_zones` attr) and reuses `axium-matrix-card-editor` so its
  `zones`/`sources` config whitelist what the Add form offers (empty = all). Each alarm row
  also shows a one-line **source/media label** (`_alarmSourceLabel`): a wake song `♪ <title> ·
  <amp>` when the alarm has `media`, else the configured source name — never the raw protocol
  byte or content id. This needs the alarm sensor to expose `alarm_media`/`alarm_media_title`/
  `alarm_media_player` (the picker stores `media_title` alongside `media`; `set_alarm`
  service + config carry it). **`helpers.get_alarms` must preserve the media fields** — it
  rebuilds each alarm dict from a fixed key list, so anything omitted there is silently dropped
  on every read (this bit us: a wake song was stored but stripped by `get_alarms` before the
  scheduler/sensor saw it, so the alarm only changed volume). The alarm scheduler plays the wake
  media with `enqueue: "replace"` so it interrupts current playback instead of queueing behind
  it. The sleep
  card is section-configurable via its own
  editor (`axium-sleep-card-editor`): `sections` = subset of `["all","zones","presets"]`
  (default all), plus a **`zones` whitelist** (sorted select) that narrows the individual-zone
  rows (`_zoneNumberIds` filters by `_numberZone` = the media_player sharing each sleep number's
  device). "presets" rows apply a sleep timer to every zone in a `axium_presets`
  preset (via each zone's sleep-timer number); preset countdown = furthest deadline among
  its zones. Editing an existing alarm's fields does NOT
  reload the entry — `_async_update_listener` reloads only when the alarm-name set or a
  non-alarm option changes (else dispatches `SIGNAL_ALARM_UPDATE`); the alarm sensor reads
  its config fresh by name so edits reflect without a reload.
- **Volumes card** (`axium-volumes-card`, sixth card): one **vertical** volume slider per zone
  (native range via `writing-mode: vertical-lr; direction: rtl` + `-webkit-appearance:
  slider-vertical` for WebKit) plus a mute button; drags are debounced (`_scheduleVolume`, 200ms)
  and the final `change` still fires. Filterable via its own editor (`axium-volumes-card-editor`:
  hub/zones/name) — the `zones` whitelist (empty = all). Reads/writes only the zones'
  media_player state. **Max-volume cap:** the region above a zone's max volume is **greyed out** on the
  slider (`axiumApplyVolCap` sizes a `.volcap`/`.slidcap` overlay to `100 − max`), and drags/sets are
  **clamped** to it. The max is `axiumMaxVolume(hass, zoneId)` (0-100) from the zone's `number.*_max_volume`
  entity. **Fast path** derives the number id from the zone id (`number.<zone-slug>_max_volume`, O(1) — it
  ran on every hass tick per zone, so a full `hass.entities` scan was too costly); falls back to a
  device+`platform==="axium"` scan only if that id is absent (renamed). **Caches the last known value per
  zone** (`_axiumMaxVolCache`) so a transient `unavailable`/`unknown` doesn't briefly UNCAP the slider
  (returning the 100 default). Same greying on the **matrix zone popover** slider (`_openZonePanel`/
  `_refreshPanel`).
- **The internal Media Player source is SPLIT per amp everywhere** (id ≥ `STREAM_SOURCE_MIN`):
  `axiumSourceChoices` emits **one choice per amp** ("Axium 1", "Axium 2" — NOT one combined
  "Media Player" or "Axium 1 / Axium 2"), each amp-scoped by a 3-part token
  `<hub>|<sid>|<ampId>` (analog sources keep the 2-part `<hub>|<sid>`). `axiumAmps(hass, hubId)`
  (free fn, master first, with `zones`) backs this and `axiumAmpNames`. **Matrix editor** Sources
  filter values are `stream:<ampId>` for streams (`String(id)` for analog); `_columns()` filters
  each per-amp stream column by `stream:<ampId>` (a legacy numeric Media-Player id whitelists all
  streams — migration), analog by id — the whitelist is applied in `_columns()`, not `_sources()`
  (`_sourceFilter()`). **Source-card editor**: picking a stream carries `ampId` into the config;
  the source card's `_zones()` then scopes to that amp's zones (`_zoneAmpId`), so a source card for
  "Axium 1" shows only zones 1-8. Editor `_changed`/`_currentToken` round-trip the `ampId`.
- **Stream-panel transport / PAUSE IS A HARDWARE NO-OP** (verified on real hardware, 2026-07-11):
  the amp's DLNA renderer **ignores pause even when Music Assistant owns the queue** — `media_pause`/
  `media_play_pause` leave both `state` and `media_position` unchanged (an earlier note here optimistically
  claimed transport works with an MA queue; it does NOT for pause). **`media_stop` DOES work** (→ `idle`),
  and `media_play` after a stop resumes the queue (restarts the current track, not exact-position). So the
  stream panel's middle transport button is **play/STOP, not play/pause** (`_togglePlayStop`: `media_stop`
  when playing, `media_play` when stopped). **The reported state is unreliable** — this MA player reports
  `state: "playing"` even when stopped/paused, and `media_position`/`..._updated_at` don't tick live — so
  the button icon is driven by an **optimistic `_panel.streamPlaying` flag** (`_setStreamPlayIcon`; set
  true on play, false on stop, and cleared only by a *definite* off/`idle` state in `_refreshStreamPanel`,
  never by a reported "playing"). Do NOT re-add a `media_play_pause` toggle or trust `st.state` for the
  icon. `prev`/`next` still call their services (next tends to stop the stream — hardware). **A
  `data-t="pauseplay"` button sits BEFORE the stop button** (`_toggleStreamRooms` →
  `_toggleSourcePower` on the amp's stream column): since a true transport pause is impossible, it
  "pauses"/resumes by powering the amp's stream ROOMS off/on (the MA stream keeps running, so resume is
  instant — but it rejoins live, not at the pre-pause position). Icon pause↔play via `_setStreamPauseIcon`
  (pause when any room is on). It shares the SAME localStorage memory (`stream:<ampId>`) as the matrix
  column power button. Reliable true transport is still via Music Assistant itself. **BOTH pause paths were tested and neither works
  (2026-07-11) — do not retry:** (1) `media_pause` on the MA player relays a DLNA pause the amp ignores
  (MA's own queue `elapsed_time` keeps ticking through it); (2) the Axium **native** `0x3D` `MEDIA_PAUSE`
  (via the zone media_player entity) controls the amp's *internal* media player, NOT the DLNA stream MA
  pushes — it doesn't pause the audio and its `MEDIA_PLAY`/`NEXT` can **change the track** (desyncs MA's
  queue). `media_seek` DOES work, but a stop→replay→seek "pause" is a dead end: the only position source
  (`get_queue` `elapsed_time`) is unreliable (sometimes cumulative across repeats, > track duration).
  Conclusion: **there is no working pause for a streamed source on this amp — `media_stop` is the
  ceiling.** Never wire native `0x3D` media control to the amp-stream panel.
- **Sleep card Custom… button**: each sleep row (zone and preset) has a "Custom…" button beside
  the 15/30/60/90m presets that opens a clean in-card popover (`_openCustom` → an `#overlay`/`#sheet`
  absolutely-positioned over the `ha-card`, closed by the backdrop/Cancel/Esc) with a minutes
  number field + quick chips, applying via the same `apply()` path (not `window.prompt`).
- Show/hide: card config `zones` (zone entity_ids; source + matrix + volumes cards) and `sources`
  (source ids; matrix card) are optional whitelists — empty/unset = show all. Editors use
  an axium-scoped entity selector for zones and a source-id select for sources. Matrix has
  its own editor (`axium-matrix-card-editor`: hub/zones/sources/name); the source editor
  gained a zones field. `entities` remains a legacy alias for `zones` on the source card.
- The integration serves it from a **version-stamped path**
  (`/axium/axium-source-card-<version>.js`) via `AxiumCardView.extra_urls`, not a `?v=`
  query — a new path defeats stale browser/service-worker caches on every release.
  The unversioned path stays for manual dashboard resources.
- It's registered as a **managed Lovelace module resource** (storage mode:
  `ResourceStorageCollection`; one resource kept, repointed to the current versioned
  URL each release), NOT `add_extra_js_url`. The card picker *awaits* resources but not
  `add_extra_js_url` imports — the latter races the gallery and shows a perpetual
  spinner / "Custom element not found". Falls back to `add_extra_js_url` only in
  YAML-dashboard mode. (`lovelace` is in `after_dependencies`.)
- Only ever list **Axium-owned** entities: filter media players by
  `hass.entities[id].platform === "axium"` (helper `axiumMediaPlayers`).
- The frontend's `hass.entities` is a *lightweight* display registry: it has
  `platform` and `device_id` but **NOT `config_entry_id`**. To find an entity's hub
  (config entry), go via the device: `hass.entities[id].device_id` →
  `hass.devices[device_id].config_entries[0]` (helper `entityHub`). Relying on
  `entity.config_entry_id` silently returns nothing in the frontend (that's what made
  the source dropdown fall back to a plain text field).
- The card stores the **stable source id** (protocol byte), not the display name,
  so renaming a source on the amp doesn't break a card. The media_player exposes a
  `source_ids` attribute parallel to `source_list`; the card resolves id→current
  name per zone (`_sourceNameFor`). Editor dropdown value = `"<hub id>|<source id>"`
  token, label = current name. Legacy name-based configs still resolve and migrate
  to the id on re-save.
- **Zone presets** (shared): named zone sets stored in entry options
  (`CONF_PRESETS = [{name, zones:[entity_id,...]}]`, helper `get_presets`), managed via
  the options flow menu (`init` menu → `settings` / `add_preset` / `remove_preset`;
  zones via `EntitySelector(integration=DOMAIN, domain=media_player, multiple)`).
  media_player exposes them as the `axium_presets` attribute (hub-wide, in
  `_unrecorded_attributes`). The card shows a top-corner dropdown (`_presets()`,
  `_applyPreset`) that applies **set-exactly**: preset zones → this source,
  other zones on this source → off. Changing presets reloads the entry (update listener).

## Deploying to / debugging the user's live HA

- Test instance: `http://192.168.1.119:8123` (LAN, reachable from the user's PC).
  The card view is `requires_auth=False`, so the card JS and the frontend index HTML
  can be fetched unauthenticated to check MIME/injection.
- For REST/WS actions the user provides a **temporary long-lived token** and revokes
  it afterward. Never store the token in the repo or memory.
- **HACS tracks this repo by main-branch commit SHA**, not release tags, *because all
  releases are pre-releases* (HACS ignores pre-releases). To push a commit to the box
  without waiting for HACS's schedule:
  1. WS `hacs/repository/refresh` `{repository: 1285095493}` — forces a GitHub re-fetch.
  2. REST `update/install` on `update.axium_amplifier_update` — downloads files.
  3. REST `homeassistant/restart` — applies (running code changes need a restart).
  Verify by fetching the new version-stamped card path (only exists in the new code)
  and grepping the index HTML for the injected `/axium/axium-source-card-<ver>.js`.
- **ALWAYS verify AFTER the restart that the entry actually loaded** — check
  `config_entries/get` shows the axium entry `state: "loaded"` AND the versioned card path
  returns 200, not just that `update.install` returned 200. **`update/install` can silently
  fail** (returns HTTP 200, `update` entity flips to `installed=<sha>`, but writes nothing —
  seen when the box had a DNS/GitHub hiccup mid-download). The symptom: after restart the entry
  is `not_loaded` (reason None), every `/axium/...js` path 404s, and the log shows **"Unable to
  get manifest for integration axium: Integration 'axium' not found"** — HA scanned
  `custom_components/` before/without the files. A plain restart does NOT fix it (files are
  genuinely absent). **Recovery: force a re-download** — WS `hacs/repository/download`
  `{repository: 1285095493}` (returns `success: True` when done), then restart. (HACS's
  `hacs/repository/info` may report `installed: None` in this broken state — trust the card-path
  200 + entry `loaded`, not HACS's own flags.)
- Restarting HA is disruptive; the user has delegated updates, but keep them informed.

## Other

- **Entity-id prefix migration** (`__init__._async_migrate_entity_ids`, guarded by
  `_ENTITY_ID_MIGRATION` in `entry.data`): a one-time rename of every Axium entity_id to
  `<domain>.axium_<primary unit id hex>_<suffix>` (e.g. `media_player.axium_0681_zone_1`), derived
  from each entity's `unique_id` (strip the entry-id prefix → `slugify`). Runs before platforms load
  so entities come up renamed; also rewrites zone entity_ids stored in **presets/alarms** options
  (`_zone_refs_migrated`). Skips until the primary unit id is known (from `get_units`), never
  overwrites an existing id, runs once. It does NOT touch external references (dashboards,
  user automations/scripts, templates) — those must be updated by hand after the rename.
- Prefer clean, self-explanatory UI over config dialogs and comma-separated inputs —
  the user has repeatedly asked to auto-detect and to avoid burying options in the
  settings wheel / global "save" flows.
- Risky level/gain settings (source gain 0x32 especially) stay behind the opt-in
  **Advanced settings** toggle with per-setting disclaimers. Nothing is auto-written
  to the amp on connect — reads only; writes are user-initiated and within documented
  ranges.

## Maintaining this file

This is the shared, per-session source of truth for *how to work in this repo*. When
the user gives new feedback, when the release process changes, or when you discover a
non-obvious operational fact, add it here in the same session. Keep entries terse and
factual. (Longer design history lives in the harness `MEMORY.md`; curated notes may
also live in the user's Obsidian vault.)
