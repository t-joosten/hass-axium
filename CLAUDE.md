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
- **What the control protocol can/can't do** (AxiumCommsProtocol.pdf, 25pp): network config IS
  settable — `0x3A`: setting `03h` = flags (bit0 0=DHCP/1=Static) + 16 bytes IP/subnet/DNS/router;
  `83h` reads it back; `08h`/`88h` = AirPlay enable/status (only meaningful on AirPlay hardware).
  **UPnP/DLNA/Pandora/TuneIn are NOT in the protocol** (amp web-UI/app only, at `http://<amp-ip>`);
  media servers (SMB shares) = `0x3B`; media control/status = `0x3D`/`0x3E`/`0x3F`.
- **Multi-amp stacks / per-unit devices**: the controller tracks a `UnitInfo` per amp
  (`controller._units`, keyed by unit id; `units()`, `unit(id)`, `primary_unit_id`). Device
  info (0x14) accumulates one unit per reply; extended info (0xB9) is keyed by the unit id at
  `data[2:4]` and requested **per unit** (`async_request_extended_info(unit_id)`). Legacy
  single-value props (`temperature`/`_firmware`/`_mac`) mirror the **primary**. Each amp is a
  device: primary = the hub, expansion = `(<entry>_unit_<uid>)` `via_device` the hub; zones
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
- **Zone rename → amp**: renaming a zone's device (HA pencil) is mirrored to the amp.
  `__init__` listens for `dr.EVENT_DEVICE_REGISTRY_UPDATED`; when a zone device's
  `name_by_user` changes it calls `async_set_zone_name` (`CMD_ZONE_NAME` 0x1C, ~15-byte
  cap). We never write the registry back, so it can't loop. (Source names have their own
  editable text entity; zone names use the device pencil.)
- **Hub rename**: the same `_handle_device_rename` listener also mirrors the **hub**
  device's `name_by_user` → the **config-entry title** (so the integrations page shows the
  chosen name). The **reconfigure flow** also carries a **Name** field (the whole stack is
  one TCP connection — a single host/port — so there's no per-amp connection to configure;
  renaming there updates the title *and* the hub device name unless it has a `name_by_user`
  override). `async_setup_entry` also syncs an existing hub `name_by_user` → title at start.
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
  The matrix headers are interactive too (an in-card popover overlay, `#overlay`/`#sheet`,
  closed by tapping the backdrop): **tap a zone name** → quick volume slider + mute +
  prev/play-pause/next for that zone (`_openZonePanel`; slider debounced via `_scheduleVolume`,
  live-refreshed by `_refreshPanel` from `_update` unless mid-drag); **hold a zone name** →
  open the zone device page (`_attachHold`, reused 500ms hold pattern); **tap a source name**
  → preset picker (`_openPresetPanel`) that applies a preset onto that column set-exactly
  (`_applyPresetToSource`: preset zones → that source, other zones on it → off), mirroring the
  source card's preset semantics.
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
  first, labels it "All zones").
- **Alarms (wake-to-music)**: HA-side (the amp's native alarm needs clock+preset+favourite
  config — too rigid/unverified). Stored in options (`CONF_ALARMS`, helper `get_alarms`):
  `{name,time,days[0=Mon..6=Sun],zones[entity_ids],source id,volume,enabled}`. Managed via
  options-flow steps `add_alarm`/`remove_alarm`. Scheduler `_async_setup_alarms` in
  __init__ registers `async_track_time_change(second=0)`; on a due minute it powers zones
  on, selects source (`| SOURCE_FLAG_TURN_ON`), and fades up to target. Master arm/disarm
  = `AxiumAlarmsSwitch` (switch.py, runtime flag `hass.data[DATA_ALARMS_ENABLED]`).
- **Time-left is exposed as `device_class: timestamp` sensors** (automation-usable):
  `AxiumAlarmSensor` (per alarm, next fire via `helpers.next_alarm_fire`; recomputes on a
  minute tick + `SIGNAL_ALARM_UPDATE` from the switch) and `AxiumSleepSensor` (per zone,
  reads `DATA_SLEEP_DEADLINES` written by the sleep-timer number, updated via
  `SIGNAL_SLEEP_UPDATE`). Both carry an `axium_kind` attribute ("alarm"/"sleep").
- Cards `axium-alarms-card` / `axium-sleep-card` (bundle now has FIVE cards) find those
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
  `zones`/`sources` config whitelist what the Add form offers (empty = all). The sleep
  card is section-configurable via its own
  editor (`axium-sleep-card-editor`): `sections` = subset of `["all","zones","presets"]`
  (default all). "presets" rows apply a sleep timer to every zone in a `axium_presets`
  preset (via each zone's sleep-timer number); preset countdown = furthest deadline among
  its zones. Editing an existing alarm's fields does NOT
  reload the entry — `_async_update_listener` reloads only when the alarm-name set or a
  non-alarm option changes (else dispatches `SIGNAL_ALARM_UPDATE`); the alarm sensor reads
  its config fresh by name so edits reflect without a reload.
- Show/hide: card config `zones` (zone entity_ids; source + matrix cards) and `sources`
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
- Restarting HA is disruptive; the user has delegated updates, but keep them informed.

## Other

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
