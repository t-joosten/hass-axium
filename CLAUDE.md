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
- The same JS file defines TWO cards: `axium-source-card` and `axium-hub-card` (compact
  amp status: model/fw/zones-on/temp/clipping + all-off button; tap opens the hub device
  page). Both + their editors are registered in one guard block and pushed to
  `window.customCards`. The hub card finds hub-owned entities via `entityHub` + the
  entity-registry `platform`, and the hub device by identifier `["axium", <hub id>]`.
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
