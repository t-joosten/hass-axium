/**
 * Axium Source Card — a Lovelace card for the hass-axium integration.
 *
 * One card represents one source (e.g. "Apple TV"). It shows every zone as a
 * tappable chip: tapping a zone assigns that source to it (atomically moving it
 * off whatever source it was on), tapping an active zone turns it off. It also
 * provides previous / play-pause / next, mute and volume controls for the zones
 * currently playing the source.
 *
 * Config:
 *   type: custom:axium-source-card
 *   source: 20                # required — the source id (protocol byte) this
 *                             #            card controls. Stored as the stable
 *                             #            id, not the name, so renaming the
 *                             #            source on the amp doesn't break the
 *                             #            card. (A legacy name string still
 *                             #            works.) The visual editor sets this.
 *   hub: <config_entry_id>    # optional — the amplifier this source belongs to,
 *                             #            set automatically by the visual editor
 *                             #            and only needed to disambiguate a
 *                             #            source shared across multiple hubs
 *   name: Apple TV            # optional — header text; defaults to the source,
 *                             #            prefixed with the amp name when there
 *                             #            is more than one Axium hub
 *   entities:                 # optional — zone media_players; auto-detected
 *     - media_player.kitchen  #            from the source list when omitted
 *     - media_player.living_room
 */

const SUPPORT_PAUSE = 1;
const SUPPORT_VOLUME_MUTE = 8;
const SUPPORT_PREVIOUS_TRACK = 16;
const SUPPORT_NEXT_TRACK = 32;
const SUPPORT_VOLUME_STEP = 1024;
const SUPPORT_PLAY = 16384;
const OFF_STATES = ["off", "unavailable", "unknown", "standby"];
// Source ids at/above this are the amp's internal media/stream player (0x12…),
// which the matrix shows as one column per amp instead of a single "Media Player".
const STREAM_SOURCE_MIN = 0x10;

/**
 * The config-entry id (hub) an entity belongs to. The frontend's lightweight
 * entity registry (`hass.entities`) often omits `config_entry_id`, so fall back
 * to the entity's device (`device_id → device.config_entries`), which the
 * frontend *does* carry. Returns undefined if it can't be determined.
 */
function entityHub(hass, id) {
  const entry = hass && hass.entities && hass.entities[id];
  if (!entry) return undefined;
  if (entry.config_entry_id) return entry.config_entry_id;
  const device =
    entry.device_id && hass.devices && hass.devices[entry.device_id];
  if (device && Array.isArray(device.config_entries) && device.config_entries.length) {
    return device.config_entries[0];
  }
  return undefined;
}

/** Escape a string for safe interpolation into innerHTML. */
function escHtml(value) {
  return String(value == null ? "" : value).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

/**
 * Return the media_player entity_ids that belong to the Axium integration.
 * Uses the entity registry (`hass.entities[id].platform`); if the registry is
 * unavailable it falls back to every media player. When `hubId` (a config
 * entry id) is given, only zones belonging to that specific amplifier are
 * returned.
 */
function axiumMediaPlayers(hass, hubId) {
  const states = (hass && hass.states) || {};
  const registry = hass && hass.entities;
  return Object.keys(states).filter((id) => {
    if (!id.startsWith("media_player.")) return false;
    const entry = registry && registry[id];
    if (entry) {
      if (entry.platform !== "axium") return false;
      if (hubId && entityHub(hass, id) !== hubId) return false;
      return true;
    }
    // No registry: can't confirm the hub, so only fall back when unfiltered.
    return !registry && !hubId;
  });
}

// A zone media_player's physical zone number (1..16, and beyond for more amps),
// from the `zone_number` attribute; 9999 when unknown so it sorts last.
function axiumZoneNumber(hass, id) {
  const st = hass && hass.states && hass.states[id];
  const n = st && st.attributes && st.attributes.zone_number;
  return typeof n === "number" ? n : 9999;
}

// Order zone entity_ids by physical zone number (entity id as a stable tiebreak),
// so every card lists zones 1..16 in the same, expected order.
function axiumSortZones(hass, ids) {
  return [...ids].sort(
    (a, b) =>
      axiumZoneNumber(hass, a) - axiumZoneNumber(hass, b) ||
      String(a).localeCompare(String(b))
  );
}

// Zone options for a card editor's "zones to show" select — the hub's zone
// media_players in physical zone-number order. The label carries the STACK-WIDE
// zone number (1..16+) so it's unambiguous across amps (the entity picker's
// per-amp "Zone 4" secondary text is confusing with multiple amps).
function axiumZoneSelectOptions(hass, hubId) {
  return axiumSortZones(hass, axiumMediaPlayers(hass, hubId)).map((id) => {
    const name = (hass.states[id] && hass.states[id].attributes.friendly_name) || id;
    const num = axiumZoneNumber(hass, id);
    const label =
      num >= 9999 || name === `Zone ${num}` ? name : `${name} (Zone ${num})`;
    return { value: id, label };
  });
}

/**
 * Enumerate the Axium amplifiers (config entries) that have media_player
 * entities, returning `{ id, name }` for each. The name is taken from the hub
 * device (identifiers include `["axium", <config_entry_id>]`).
 */
function axiumHubs(hass) {
  const states = (hass && hass.states) || {};
  const registry = (hass && hass.entities) || {};
  const devices = (hass && hass.devices) || {};
  const ids = new Set();
  for (const id of Object.keys(states)) {
    if (!id.startsWith("media_player.")) continue;
    const entry = registry[id];
    if (entry && entry.platform === "axium") {
      const hub = entityHub(hass, id);
      if (hub) ids.add(hub);
    }
  }
  return [...ids]
    .map((cid) => {
      let name = cid;
      for (const dev of Object.values(devices)) {
        const match = (dev.identifiers || []).some(
          (t) => t[0] === "axium" && t[1] === cid
        );
        if (match) {
          name = dev.name_by_user || dev.name || cid;
          break;
        }
      }
      return { id: cid, name };
    })
    .sort((a, b) => a.name.localeCompare(b.name));
}

// Separator embedded in a picker token. A hub id (a hex config-entry id) never
// contains it, so splitting on the FIRST occurrence recovers the hub id even if
// a source name happens to include the character.
const TOKEN_SEP = "|";

/**
 * Every (hub, source) pair across all Axium amplifiers, as pickable choices.
 *
 * The card stores the amplifier's stable **source id** (the protocol byte),
 * never the display name — so renaming a source on the amp doesn't break a
 * card. Each choice's `token` is "<hub id>|<source id>"; the `label` shows the
 * current name, prefixed with the amplifier name only when more than one hub is
 * present ("[hub] [name]"). Source ids come from each media_player's
 * `source_ids` attribute (parallel to `source_list`).
 */
// The amp device names on a hub (master first), e.g. ["Axium 1", "Axium 2"].
// Used to show the stack-wide internal Media Player source under the amp/stream
// names the matrix uses as columns, rather than the bare "Media Player".
// The amps on a hub (master first), each `{id, name, master, zones:[entity_id]}`,
// from the device tree (zone media_player → via_device amp device).
function axiumAmps(hass, hubId) {
  const devs = (hass && hass.devices) || {};
  const ents = (hass && hass.entities) || {};
  const byId = new Map();
  const order = [];
  for (const id of axiumMediaPlayers(hass, hubId)) {
    const ent = ents[id];
    const zdev = ent && devs[ent.device_id];
    if (!zdev) continue;
    const amp = devs[zdev.via_device_id] || zdev;
    if (!byId.has(amp.id)) {
      // The primary amp device identifier has no "_unit_" (the expansions do)
      // and no "_zone_" (zone devices do) — it is the master. The "_zone_"
      // exclusion matters because `amp` falls back to the zone device when the
      // via_device amp isn't resolvable yet (a registry-sync window), and a
      // zone id also lacks "_unit_" — without it a lone zone is mis-flagged
      // master. (Since the split the primary amp is its own "…_amp_primary"
      // device; the bare hub id still matches for a pre-split entry.)
      const master = (amp.identifiers || []).some(
        (t) =>
          t[0] === "axium" &&
          !String(t[1]).includes("_unit_") &&
          !String(t[1]).includes("_zone_")
      );
      byId.set(amp.id, {
        id: amp.id,
        name: amp.name_by_user || amp.name || "Amp",
        master,
        zones: [],
      });
      order.push(amp.id);
    }
    byId.get(amp.id).zones.push(id);
  }
  return order
    .map((id) => byId.get(id))
    .sort((a, b) => (a.master === b.master ? 0 : a.master ? -1 : 1));
}

function axiumAmpNames(hass, hubId) {
  return axiumAmps(hass, hubId)
    .map((a) => a.name)
    .filter(Boolean);
}

// Search a Music Assistant player; returns the flat result list (or []). Shared
// by the matrix stream panel and the alarms card so the WS shape lives in one
// place.
async function axiumMaSearch(hass, entityId, query) {
  const res = await hass.callWS({
    type: "media_player/search_media",
    entity_id: entityId,
    search_query: query,
  });
  return (res && res.result) || [];
}

// Browse a Music Assistant player at an item (or its root); returns the raw
// response ({children, …}) or {}.
async function axiumMaBrowse(hass, entityId, contentId, contentType) {
  const res = await hass.callWS({
    type: "media_player/browse_media",
    entity_id: entityId,
    ...(contentId
      ? { media_content_id: contentId, media_content_type: contentType }
      : {}),
  });
  return res || {};
}

// A zone's max-volume cap (0..100) from its `number.*_max_volume` entity. 100
// when there's no cap. Fast path derives the number id from the zone id (O(1),
// avoids scanning all entities every tick); falls back to a device+platform scan
// only if that id is absent (e.g. renamed). Caches the last KNOWN value per zone
// so a transient `unavailable`/`unknown` doesn't briefly uncap the slider.
const _axiumMaxVolCache = new Map();
function axiumMaxVolume(hass, zoneId) {
  try {
    const states = hass.states || {};
    // Fast path: media_player.axium_…_zone_N → number.axium_…_zone_N_max_volume.
    let st = states["number." + zoneId.slice(zoneId.indexOf(".") + 1) + "_max_volume"];
    if (!st) {
      const ents = hass.entities || {};
      const dev = ents[zoneId] && ents[zoneId].device_id;
      if (dev) {
        for (const id of Object.keys(ents)) {
          if (!id.startsWith("number.") || !id.endsWith("_max_volume")) continue;
          const e = ents[id];
          if (e.device_id !== dev || e.platform !== "axium") continue;
          st = states[id];
          break;
        }
      }
    }
    const v = st && Number(st.state);
    if (Number.isFinite(v)) {
      const capped = Math.max(0, Math.min(100, v));
      _axiumMaxVolCache.set(zoneId, capped);
      return capped;
    }
    // Entity is unavailable/unknown right now — reuse the last known cap.
    if (_axiumMaxVolCache.has(zoneId)) return _axiumMaxVolCache.get(zoneId);
  } catch (e) {
    /* fall through */
  }
  return 100;
}

// Size a greyed "cap" overlay (`prop` is "height" for a vertical slider or
// "width" for a horizontal one) to the region above a zone's max volume.
function axiumApplyVolCap(el, hass, zoneId, prop) {
  if (el) el.style[prop] = Math.max(0, 100 - axiumMaxVolume(hass, zoneId)) + "%";
}

function axiumSourceChoices(hass) {
  const hubs = axiumHubs(hass);
  const multi = hubs.length > 1;
  const states = (hass && hass.states) || {};
  const out = [];
  for (const hub of hubs) {
    const byId = new Map(); // source id -> current name
    for (const entity of axiumMediaPlayers(hass, hub.id)) {
      const attrs = states[entity].attributes;
      const ids = attrs.source_ids;
      const names = attrs.source_list;
      if (Array.isArray(ids) && Array.isArray(names)) {
        ids.forEach((sid, i) => {
          if (!byId.has(sid)) byId.set(sid, names[i]);
        });
      }
    }
    const amps = axiumAmps(hass, hub.id);
    [...byId.entries()]
      .sort((a, b) => String(a[1]).localeCompare(String(b[1])))
      .forEach(([sid, rawName]) => {
        if (sid >= STREAM_SOURCE_MIN && amps.length) {
          // The internal Media Player is a SEPARATE stream per amp — emit one
          // choice per amp ("Axium 1", "Axium 2"), each amp-scoped by its token.
          for (const amp of amps) {
            out.push({
              hub: hub.id,
              hubName: hub.name,
              id: sid,
              ampId: amp.id,
              name: amp.name,
              token: `${hub.id}${TOKEN_SEP}${sid}${TOKEN_SEP}${amp.id}`,
              label: multi ? `${hub.name} ${amp.name}` : amp.name,
            });
          }
        } else {
          out.push({
            hub: hub.id,
            hubName: hub.name,
            id: sid,
            name: rawName,
            token: `${hub.id}${TOKEN_SEP}${sid}`,
            label: multi ? `${hub.name} ${rawName}` : rawName,
          });
        }
      });
  }
  return out;
}

class AxiumSourceCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._built = false;
    this._zoneIds = [];
  }

  setConfig(config) {
    this._config = config || {};
    this._built = false;
    this.shadowRoot.innerHTML = "";
  }

  set hass(hass) {
    this._hass = hass;
    if (!hass || !this._config) return;
    const src = this._config.source;
    if (src === undefined || src === null || src === "") {
      this._renderPlaceholder();
      return;
    }
    const zones = this._zones();
    // (Re)build if the structure has not been created or the zone set changed.
    if (!this._built || zones.join() !== this._zoneIds.join()) {
      this._zoneIds = zones;
      this._build();
    }
    this._update();
  }

  getCardSize() {
    return 3;
  }

  // Enable the visual (UI) editor.
  static getConfigElement() {
    return document.createElement("axium-source-card-editor");
  }

  // Provide a default config so the card picker can render a preview.
  static getStubConfig(hass) {
    const choices = axiumSourceChoices(hass);
    if (choices.length) return { hub: choices[0].hub, source: choices[0].id };
    return { source: 0 };
  }

  _renderPlaceholder() {
    this.shadowRoot.innerHTML =
      `<style>${AxiumSourceCard.styles}</style>` +
      `<ha-card><div class="placeholder">` +
      `Set a <b>source</b> for this Axium Source Card.</div></ha-card>`;
    this._built = false;
  }

  // -- data helpers ----------------------------------------------------

  /**
   * Resolve the configured source to its CURRENT display name for one zone's
   * state. The config stores the stable source id (a protocol byte); a legacy
   * card may still store the name. Returns null if the zone doesn't offer it.
   */
  _sourceNameFor(st) {
    if (!st) return null;
    const cfg = this._config.source;
    const attrs = st.attributes || {};
    const ids = attrs.source_ids;
    const names = attrs.source_list;
    if (typeof cfg === "number" && Array.isArray(ids) && Array.isArray(names)) {
      const i = ids.indexOf(cfg);
      return i >= 0 ? names[i] : null;
    }
    // Legacy: config.source is a name string.
    if (typeof cfg === "string" && Array.isArray(names) && names.includes(cfg)) {
      return cfg;
    }
    return null;
  }

  /** The source's current name, resolved from any zone that carries it. */
  _sourceName() {
    const ids = this._zoneIds.length ? this._zoneIds : this._zones();
    for (const id of ids) {
      const name = this._sourceNameFor(this._state(id));
      if (name != null) return name;
    }
    return typeof this._config.source === "string" ? this._config.source : "";
  }

  _zones() {
    let auto = axiumMediaPlayers(this._hass, this._config.hub).filter(
      (id) => this._sourceNameFor(this._state(id)) != null
    );
    // A per-amp stream source (config carries `ampId`) scopes to that amp's zones.
    if (this._config.ampId) {
      auto = auto.filter((id) => this._zoneAmpId(id) === this._config.ampId);
    }
    // Optional whitelist (`zones`, or legacy `entities`): show only these,
    // dropping any that no longer offer the source.
    const pick = this._config.zones || this._config.entities;
    if (Array.isArray(pick) && pick.length) {
      auto = auto.filter((id) => pick.includes(id));
    }
    // Always ordered by physical zone number (1..16+).
    return axiumSortZones(this._hass, auto);
  }

  /** The device id of a zone's owning amp (via_device), for amp-scoping. */
  _zoneAmpId(id) {
    const ent = (this._hass.entities || {})[id];
    const devs = this._hass.devices || {};
    const zdev = ent && devs[ent.device_id];
    if (!zdev) return null;
    const amp = devs[zdev.via_device_id] || zdev;
    return amp ? amp.id : null;
  }

  _state(id) {
    return this._hass.states[id];
  }

  _isActive(st) {
    const name = this._sourceNameFor(st);
    return (
      st &&
      name != null &&
      st.attributes.source === name &&
      !OFF_STATES.includes(st.state)
    );
  }

  _activeIds() {
    return this._zoneIds.filter((id) => this._isActive(this._state(id)));
  }

  _leader() {
    const active = this._activeIds();
    return active.length ? this._state(active[0]) : null;
  }

  _name(id) {
    const st = this._state(id);
    const n = st && st.attributes.friendly_name;
    return n ? n : id.split(".")[1].replace(/_/g, " ");
  }

  /**
   * Header text: an explicit `name`, else the source — prefixed with the
   * amplifier name when more than one Axium hub exists, so cards from different
   * amps stay distinguishable ("[hub] [source]").
   */
  _title() {
    if (this._config.name) return this._config.name;
    const name = this._sourceName();
    const hubs = axiumHubs(this._hass);
    if (this._config.hub && hubs.length > 1) {
      const hub = hubs.find((h) => h.id === this._config.hub);
      if (hub) return `${hub.name} ${name}`;
    }
    return name;
  }

  // -- services --------------------------------------------------------

  _call(service, data) {
    this._hass.callService("media_player", service, data);
  }

  /**
   * Wire a chip for tap (toggle the zone) and hold (open the zone's device page,
   * where its volume, tone, gains and other settings live). Uses pointer events
   * so it works for mouse and touch; the hold suppresses the following click.
   */
  _attachChipHandlers(chip, id) {
    let timer = null;
    let held = false;
    const cancel = () => {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
    };
    chip.addEventListener("pointerdown", () => {
      held = false;
      cancel();
      timer = setTimeout(() => {
        held = true;
        this._openZoneDevice(id);
      }, 500);
    });
    chip.addEventListener("pointerup", cancel);
    chip.addEventListener("pointerleave", cancel);
    chip.addEventListener("pointercancel", cancel);
    chip.addEventListener("click", (ev) => {
      if (held) {
        ev.preventDefault();
        ev.stopPropagation();
        held = false;
        return;
      }
      this._toggleZone(id);
    });
    // Suppress the touch/right-click context menu during a hold.
    chip.addEventListener("contextmenu", (ev) => ev.preventDefault());
  }

  /** Navigate to a zone's device page (fallback: its more-info dialog). */
  _openZoneDevice(id) {
    const entry = this._hass.entities && this._hass.entities[id];
    const deviceId = entry && entry.device_id;
    if (deviceId) {
      history.pushState(null, "", `/config/devices/device/${deviceId}`);
      this.dispatchEvent(
        new CustomEvent("location-changed", { bubbles: true, composed: true })
      );
      return;
    }
    this.dispatchEvent(
      new CustomEvent("hass-more-info", {
        detail: { entityId: id },
        bubbles: true,
        composed: true,
      })
    );
  }

  _toggleZone(id) {
    const st = this._state(id);
    if (this._isActive(st)) {
      this._call("turn_off", { entity_id: id });
      return;
    }
    // select_source moves the zone onto this source, leaving its old one. HA's
    // media_player API takes the source *name*, so resolve the id to its
    // current name for this zone.
    const name = this._sourceNameFor(st);
    if (name != null) this._call("select_source", { entity_id: id, source: name });
  }

  // -- presets ---------------------------------------------------------

  /** The hub's zone presets (read from any hub zone's attribute). */
  _presets() {
    for (const id of axiumMediaPlayers(this._hass, this._config.hub)) {
      const st = this._state(id);
      const p = st && st.attributes.axium_presets;
      if (Array.isArray(p)) return p;
    }
    return [];
  }

  /**
   * Apply a preset "set exactly": its zones (that offer this source) start
   * playing this source, and any zone currently on this source but not in the
   * preset is turned off — so the active set becomes exactly the preset.
   */
  _applyPreset(index) {
    const preset = this._presets()[Number(index)];
    if (!preset) return;
    // Only zones this card knows about (its hub, offering this source).
    const known = new Set(this._zones());
    const target = (preset.zones || []).filter((z) => known.has(z));
    const targetSet = new Set(target);
    for (const z of target) {
      const name = this._sourceNameFor(this._state(z));
      if (name != null) this._call("select_source", { entity_id: z, source: name });
    }
    for (const z of this._activeIds()) {
      if (!targetSet.has(z)) this._call("turn_off", { entity_id: z });
    }
  }

  _transport(service) {
    const leader = this._leader();
    if (leader) this._call(service, { entity_id: leader.entity_id });
  }

  _volume(service) {
    const ids = this._activeIds();
    if (ids.length) this._call(service, { entity_id: ids });
  }

  _toggleMute() {
    const ids = this._activeIds();
    if (!ids.length) return;
    const allMuted = ids.every((id) => this._state(id).attributes.is_volume_muted);
    this._call("volume_mute", { entity_id: ids, is_volume_muted: !allMuted });
  }

  // -- rendering -------------------------------------------------------

  _build() {
    this.shadowRoot.innerHTML = `
      <style>${AxiumSourceCard.styles}</style>
      <ha-card>
        <div class="header">
          <div class="art" id="art"></div>
          <div class="titles">
            <div class="title" id="title"></div>
            <div class="subtitle" id="subtitle"></div>
          </div>
          <select class="presets" id="presets" title="Apply a zone preset"
                  aria-label="Apply a zone preset" hidden></select>
        </div>
        <div class="chips" id="chips" role="group" aria-label="Zones"></div>
        <div class="controls" id="controls">
          <button class="ctrl" data-act="prev" title="Previous">
            <ha-icon icon="mdi:skip-previous"></ha-icon></button>
          <button class="ctrl play" data-act="play" title="Play/Pause">
            <ha-icon icon="mdi:play"></ha-icon></button>
          <button class="ctrl" data-act="next" title="Next">
            <ha-icon icon="mdi:skip-next"></ha-icon></button>
          <span class="spacer"></span>
          <button class="ctrl" data-act="mute" title="Mute">
            <ha-icon icon="mdi:volume-high"></ha-icon></button>
          <button class="ctrl" data-act="voldown" title="Volume down">
            <ha-icon icon="mdi:volume-minus"></ha-icon></button>
          <button class="ctrl" data-act="volup" title="Volume up">
            <ha-icon icon="mdi:volume-plus"></ha-icon></button>
        </div>
      </ha-card>
    `;

    const chips = this.shadowRoot.getElementById("chips");
    chips.innerHTML = "";
    this._chipEls = {};
    for (const id of this._zoneIds) {
      const chip = document.createElement("button");
      chip.className = "chip";
      chip.setAttribute("role", "switch");
      chip.innerHTML =
        `<ha-icon class="tick" icon="mdi:check"></ha-icon>` +
        `<span class="label"></span>`;
      chip.title = "Tap to toggle · hold for zone settings";
      this._attachChipHandlers(chip, id);
      chips.appendChild(chip);
      this._chipEls[id] = chip;
    }

    this.shadowRoot.getElementById("controls").addEventListener("click", (ev) => {
      const btn = ev.target.closest("button[data-act]");
      if (!btn) return;
      const act = btn.dataset.act;
      if (act === "prev") this._transport("media_previous_track");
      else if (act === "play") this._transport("media_play_pause");
      else if (act === "next") this._transport("media_next_track");
      else if (act === "mute") this._toggleMute();
      else if (act === "voldown") this._volume("volume_down");
      else if (act === "volup") this._volume("volume_up");
    });

    const presetSel = this.shadowRoot.getElementById("presets");
    presetSel.addEventListener("change", (ev) => {
      const idx = ev.target.value;
      ev.target.value = ""; // it's an action trigger, not persistent state
      if (idx !== "") this._applyPreset(idx);
    });

    this._built = true;
  }

  _updatePresets() {
    const sel = this.shadowRoot.getElementById("presets");
    if (!sel) return;
    const presets = this._presets();
    if (!presets.length) {
      sel.hidden = true;
      return;
    }
    sel.hidden = false;
    const sig = presets.map((p) => p.name).join("|");
    if (sel._sig !== sig) {
      sel._sig = sig;
      sel.textContent = "";
      const ph = document.createElement("option");
      ph.value = "";
      ph.textContent = "Preset…";
      sel.appendChild(ph);
      presets.forEach((p, i) => {
        const o = document.createElement("option");
        o.value = String(i);
        o.textContent = p.name; // textContent — safe against HTML in names
        sel.appendChild(o);
      });
    }
    sel.value = "";
  }

  _update() {
    const root = this.shadowRoot;
    root.getElementById("title").textContent = this._title();
    this._updatePresets();

    const leader = this._leader();
    const active = this._activeIds();

    // Chips: active state.
    for (const id of this._zoneIds) {
      const chip = this._chipEls[id];
      if (!chip) continue;
      const st = this._state(id);
      const on = this._isActive(st);
      chip.classList.toggle("active", on);
      chip.setAttribute("aria-checked", on ? "true" : "false");
      chip.classList.toggle("unavailable", !st || st.state === "unavailable");
      chip.querySelector(".label").textContent = this._name(id);
    }

    // Now playing + art from the leading active zone.
    const sub = root.getElementById("subtitle");
    const art = root.getElementById("art");
    if (leader && leader.attributes.media_title) {
      const parts = [leader.attributes.media_title, leader.attributes.media_artist];
      sub.textContent = parts.filter(Boolean).join(" — ");
    } else {
      sub.textContent = active.length
        ? `${active.length} zone${active.length > 1 ? "s" : ""} playing`
        : "No zones playing";
    }
    const pic = leader && leader.attributes.entity_picture;
    if (pic) {
      art.style.backgroundImage = `url("${pic}")`;
      art.classList.add("has-art");
    } else {
      art.style.backgroundImage = "";
      art.classList.remove("has-art");
    }

    // Transport availability + play/pause icon.
    const feat = leader ? leader.attributes.supported_features || 0 : 0;
    const setEnabled = (act, ok) => {
      const b = root.querySelector(`button[data-act="${act}"]`);
      if (b) b.toggleAttribute("disabled", !ok);
    };
    setEnabled("prev", !!(feat & SUPPORT_PREVIOUS_TRACK));
    setEnabled("next", !!(feat & SUPPORT_NEXT_TRACK));
    setEnabled("play", !!(feat & (SUPPORT_PLAY | SUPPORT_PAUSE)));
    setEnabled("mute", active.length && !!(feat & SUPPORT_VOLUME_MUTE));
    setEnabled("voldown", active.length && !!(feat & SUPPORT_VOLUME_STEP));
    setEnabled("volup", active.length && !!(feat & SUPPORT_VOLUME_STEP));

    const playIcon = root.querySelector('button[data-act="play"] ha-icon');
    if (playIcon) {
      playIcon.setAttribute(
        "icon",
        leader && leader.state === "playing" ? "mdi:pause" : "mdi:play"
      );
    }
    const muteIcon = root.querySelector('button[data-act="mute"] ha-icon');
    if (muteIcon) {
      const muted =
        active.length &&
        active.every((id) => this._state(id).attributes.is_volume_muted);
      muteIcon.setAttribute("icon", muted ? "mdi:volume-off" : "mdi:volume-high");
    }
  }
}

AxiumSourceCard.styles = `
  ha-card { padding: 12px 16px 8px; }
  .placeholder { padding: 16px; color: var(--secondary-text-color); }
  .header { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
  .art {
    width: 44px; height: 44px; border-radius: 8px; flex: 0 0 auto;
    background: var(--secondary-background-color) center/cover no-repeat;
  }
  .art:not(.has-art) { display: none; }
  .titles { min-width: 0; flex: 1 1 auto; }
  .presets {
    flex: 0 0 auto; max-width: 45%;
    padding: 6px 8px; border-radius: 8px;
    border: 1px solid var(--divider-color);
    background: var(--card-background-color, var(--ha-card-background));
    color: var(--primary-text-color);
    font: inherit; font-size: 0.85rem; cursor: pointer;
  }
  .presets[hidden] { display: none; }
  .title { font-size: 1.1rem; font-weight: 600; color: var(--primary-text-color); }
  .subtitle {
    font-size: 0.85rem; color: var(--secondary-text-color);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .chips { display: flex; flex-wrap: wrap; gap: 8px; }
  .chip {
    display: inline-flex; align-items: center; gap: 4px;
    min-height: 40px; padding: 6px 14px; border-radius: 20px;
    border: 1px solid var(--divider-color);
    background: var(--card-background-color);
    color: var(--primary-text-color);
    font: inherit; font-size: 0.95rem; cursor: pointer;
    transition: background 0.15s, border-color 0.15s, color 0.15s, transform 0.05s;
    touch-action: manipulation; user-select: none; -webkit-user-select: none;
    -webkit-touch-callout: none;
  }
  .chip .tick { --mdc-icon-size: 18px; width: 0; opacity: 0; transition: width 0.15s, opacity 0.15s; }
  .chip:hover { border-color: var(--primary-color); }
  .chip:active { transform: scale(0.96); }
  .chip.active {
    background: var(--primary-color);
    border-color: var(--primary-color);
    color: var(--text-primary-color, #fff);
  }
  .chip.active .tick { width: 18px; opacity: 1; }
  .chip.unavailable { opacity: 0.45; pointer-events: none; }
  .controls {
    display: flex; align-items: center; gap: 4px;
    margin-top: 12px; padding-top: 8px;
    border-top: 1px solid var(--divider-color);
  }
  .spacer { flex: 1 1 auto; }
  .ctrl {
    display: inline-flex; align-items: center; justify-content: center;
    width: 48px; height: 48px; border-radius: 50%;
    border: none; background: none; cursor: pointer;
    color: var(--primary-text-color);
    transition: background 0.15s, transform 0.05s;
  }
  .ctrl:hover { background: var(--secondary-background-color); }
  .ctrl:active { transform: scale(0.92); }
  .ctrl[disabled] { opacity: 0.3; pointer-events: none; }
  .ctrl.play { color: var(--primary-color); }
  .ctrl.play ha-icon { --mdc-icon-size: 32px; }
`;

/** Visual (UI) editor — pick an amplifier, then a source, plus an optional name. */
class AxiumSourceCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  /** The picker token for the currently configured (hub, source id, amp), if any. */
  _currentToken(choices) {
    const { hub, source, ampId } = this._config;
    if (source === undefined || source === null || source === "") return undefined;
    if (typeof source === "number") {
      // New id-based config: match by source id + amp (prefer the same hub).
      const match = (c) =>
        c.id === source && (ampId ? c.ampId === ampId : !c.ampId);
      return (
        (hub && choices.find((c) => c.hub === hub && match(c))) ||
        choices.find(match) ||
        {}
      ).token;
    }
    // Legacy cards stored the source *name* — match by name so opening + saving
    // the editor migrates them to the id.
    return (
      (hub && choices.find((c) => c.hub === hub && c.name === source)) ||
      choices.find((c) => c.name === source) ||
      {}
    ).token;
  }

  async _ensureHaForm() {
    if (customElements.get("ha-form")) return;
    try {
      const helpers = await window.loadCardHelpers();
      const card = await helpers.createCardElement({
        type: "entities",
        entities: [],
      });
      await card.constructor.getConfigElement();
    } catch (err) {
      /* ha-form will still upgrade once available */
    }
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.addEventListener("value-changed", (ev) => this._changed(ev));
      this.appendChild(this._form);
      this._ensureHaForm();
    }
    this._choices = axiumSourceChoices(this._hass);
    const options = this._choices.map((c) => ({ value: c.token, label: c.label }));

    // A single "Source" dropdown listing every amplifier's sources. The chosen
    // token encodes both the hub and the source; `name` overrides the header.
    // With no choices (registry unavailable) fall back to a raw source field.
    const data = {
      ...this._config,
      source: options.length
        ? this._currentToken(this._choices)
        : this._config.source,
    };
    // Zones (of the chosen source's hub) in physical zone-number order.
    const srcHub = String((data && data.source) || "").split(TOKEN_SEP)[0];
    const zoneOptions = axiumZoneSelectOptions(this._hass, srcHub || undefined);

    this._form.hass = this._hass;
    this._form.data = data;
    this._form.schema = [
      {
        name: "source",
        required: true,
        selector: options.length
          ? { select: { mode: "dropdown", options } }
          : { text: {} },
      },
      {
        name: "zones",
        selector: zoneOptions.length
          ? { select: { multiple: true, mode: "list", options: zoneOptions } }
          : { entity: { integration: "axium", domain: "media_player", multiple: true } },
      },
      { name: "name", selector: { text: {} } },
    ];
    this._form.computeLabel = (s) =>
      ({
        source: "Source",
        zones: "Zones to show (empty = all)",
        name: "Card name (optional)",
      }[s.name] || s.name);
  }

  _changed(ev) {
    ev.stopPropagation();
    const value = { ...ev.detail.value };
    // Decode the picked token back into an explicit hub + source id. The card
    // stores the stable id (a number), never the display name.
    const token = value.source;
    const choice = (this._choices || []).find((c) => c.token === token);
    if (choice) {
      value.hub = choice.hub;
      value.source = choice.id;
      // Stream sources are per-amp — scope the card to that amp's zones.
      if (choice.ampId) value.ampId = choice.ampId;
      else delete value.ampId;
    } else if (typeof token === "string" && token.includes(TOKEN_SEP)) {
      const parts = token.split(TOKEN_SEP);
      value.hub = parts[0];
      const n = Number(parts[1]);
      value.source = Number.isNaN(n) ? parts[1] : n;
      if (parts[2]) value.ampId = parts[2];
      else delete value.ampId;
    }
    this._config = value;
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: value },
        bubbles: true,
        composed: true,
      })
    );
    this._render();
  }
}

/**
 * Axium Hub Card — a compact status line for one amplifier.
 *
 * Shows the amp's name, model + firmware, how many zones are on, temperature and
 * a clipping warning. An "all off" button turns every zone off; tapping the card
 * opens the hub's device page (auto power/standby, presets, gains, diagnostics).
 *
 * Config:
 *   type: custom:axium-hub-card
 *   hub: <config_entry_id>   # optional — defaults to the only Axium hub
 *   name: Amplifier          # optional — header text (defaults to the hub name)
 */
class AxiumHubCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._built = false;
  }

  setConfig(config) {
    this._config = config || {};
    this._built = false;
    this.shadowRoot.innerHTML = "";
  }

  set hass(hass) {
    this._hass = hass;
    if (!hass || !this._config) return;
    if (!this._built) this._build();
    this._update();
  }

  getCardSize() {
    return 1;
  }

  static getConfigElement() {
    return document.createElement("axium-hub-card-editor");
  }

  static getStubConfig(hass) {
    const hubs = axiumHubs(hass);
    return hubs.length ? { hub: hubs[0].id } : {};
  }

  _hubId() {
    return this._config.hub || (axiumHubs(this._hass)[0] || {}).id;
  }

  _hub() {
    const id = this._hubId();
    return axiumHubs(this._hass).find((h) => h.id === id);
  }

  // The hub device carries identifiers ["axium", <config entry id>].
  _hubDevice() {
    const id = this._hubId();
    if (!id) return null;
    const devices = (this._hass && this._hass.devices) || {};
    for (const dev of Object.values(devices)) {
      if ((dev.identifiers || []).some((t) => t[0] === "axium" && t[1] === id)) {
        return dev;
      }
    }
    return null;
  }

  _zonesOn() {
    return axiumMediaPlayers(this._hass, this._hubId()).filter((id) => {
      const st = this._hass.states[id];
      return st && !OFF_STATES.includes(st.state);
    });
  }

  // Find a hub-owned axium entity whose state matches a predicate.
  _hubEntity(pred) {
    const id = this._hubId();
    const reg = (this._hass && this._hass.entities) || {};
    for (const eid of Object.keys(reg)) {
      if (reg[eid].platform !== "axium") continue;
      if (entityHub(this._hass, eid) !== id) continue;
      const st = this._hass.states[eid];
      if (st && pred(eid, st)) return st;
    }
    return null;
  }

  _temperature() {
    return this._hubEntity(
      (eid, st) =>
        eid.startsWith("sensor.") &&
        st.attributes.device_class === "temperature" &&
        !eid.includes("peak")
    );
  }

  _clipping() {
    return this._hubEntity(
      (eid, st) =>
        eid.startsWith("binary_sensor.") &&
        st.attributes.device_class === "problem"
    );
  }

  // Every amplifier device in this hub: the hub (primary) first, then each
  // stacked expansion amp (identifier "<hub id>_unit_<uid>", nested via_device).
  _amps() {
    const hubId = this._hubId();
    if (!hubId) return [];
    const devices = (this._hass && this._hass.devices) || {};
    // The primary amp is its own device ("…_amp_primary") since the hub/amp split;
    // fall back to the hub device on a pre-split integration.
    const primary =
      Object.values(devices).find((d) =>
        (d.identifiers || []).some(
          (t) => t[0] === "axium" && t[1] === `${hubId}_amp_primary`
        )
      ) || this._hubDevice();
    const amps = primary ? [primary] : [];
    for (const dev of Object.values(devices)) {
      if (primary && dev.id === primary.id) continue;
      if (
        (dev.identifiers || []).some(
          (t) => t[0] === "axium" && String(t[1]).startsWith(`${hubId}_unit_`)
        )
      ) {
        amps.push(dev);
      }
    }
    return amps;
  }

  // The (non-peak) temperature sensor sitting on a given amp device.
  _ampTemp(deviceId) {
    const reg = (this._hass && this._hass.entities) || {};
    for (const eid of Object.keys(reg)) {
      if (reg[eid].platform !== "axium" || reg[eid].device_id !== deviceId) continue;
      if (!eid.startsWith("sensor.") || eid.includes("peak")) continue;
      const st = this._hass.states[eid];
      if (st && st.attributes.device_class === "temperature") return st;
    }
    return null;
  }

  _openHub() {
    const dev = this._hubDevice();
    if (!dev) return;
    history.pushState(null, "", `/config/devices/device/${dev.id}`);
    this.dispatchEvent(
      new CustomEvent("location-changed", { bubbles: true, composed: true })
    );
  }

  _allOff() {
    const on = this._zonesOn();
    if (on.length) {
      this._hass.callService("media_player", "turn_off", { entity_id: on });
    }
  }

  _build() {
    this.shadowRoot.innerHTML = `
      <style>${AxiumHubCard.styles}</style>
      <ha-card class="hub" role="button" tabindex="0" title="Open amplifier settings">
        <ha-icon class="hicon" icon="mdi:amplifier"></ha-icon>
        <div class="info">
          <div class="hname" id="hname"></div>
          <div class="hsub" id="hsub"></div>
          <div class="amps" id="amps"></div>
        </div>
        <button class="alloff" id="alloff" title="Turn all zones off">
          <ha-icon icon="mdi:power"></ha-icon>
        </button>
      </ha-card>
    `;
    const card = this.shadowRoot.querySelector(".hub");
    card.addEventListener("click", (ev) => {
      if (ev.target.closest("#alloff")) return;
      this._openHub();
    });
    card.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        this._openHub();
      }
    });
    this.shadowRoot.getElementById("alloff").addEventListener("click", (ev) => {
      ev.stopPropagation();
      this._allOff();
    });
    this._built = true;
  }

  _update() {
    const root = this.shadowRoot;
    const hub = this._hub();
    root.getElementById("hname").textContent =
      this._config.name || (hub ? hub.name : "Axium");

    const on = this._zonesOn().length;
    const clip = this._clipping();
    const clipping = clip && clip.state === "on";
    const sub = [`${on} zone${on === 1 ? "" : "s"} on`];
    if (clipping) sub.push("⚠ Clipping");
    root.getElementById("hsub").textContent = sub.join(" · ");

    // One row per amp: [its name when there's more than one] model · fw · temp.
    const amps = this._amps();
    const multi = amps.length > 1;
    root.getElementById("amps").innerHTML = amps
      .map((dev, i) => {
        const parts = [];
        if (dev.model && dev.model !== "Amplifier") parts.push(escHtml(dev.model));
        if (dev.sw_version) parts.push(escHtml(dev.sw_version));
        const temp = this._ampTemp(dev.id);
        if (temp && temp.state && !isNaN(Number(temp.state))) {
          const u = temp.attributes.unit_of_measurement || "°C";
          parts.push(`${Math.round(Number(temp.state))}${escHtml(u)}`);
        }
        const label = multi
          ? `<span class="alabel">${escHtml(
              dev.name_by_user || dev.name || "Amp"
            )}</span> `
          : "";
        return `<div class="amprow">${label}${parts.join(" · ") || "—"}</div>`;
      })
      .join("");

    const icon = root.querySelector(".hicon");
    if (icon) icon.style.color = clipping ? "var(--error-color)" : "";
    const alloff = root.getElementById("alloff");
    if (alloff) alloff.toggleAttribute("disabled", on === 0);
  }
}

AxiumHubCard.styles = `
  .hub {
    display: flex; align-items: center; gap: 12px;
    padding: 12px 16px; cursor: pointer;
  }
  .hub:focus-visible { outline: 2px solid var(--primary-color); outline-offset: -2px; }
  .hicon { --mdc-icon-size: 28px; color: var(--primary-color); flex: 0 0 auto; }
  .info { flex: 1 1 auto; min-width: 0; }
  .hname { font-size: 1.05rem; font-weight: 600; color: var(--primary-text-color); }
  .hsub {
    font-size: 0.85rem; color: var(--secondary-text-color);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .amps { margin-top: 1px; }
  .amprow {
    font-size: 0.82rem; color: var(--secondary-text-color);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .alabel { color: var(--primary-text-color); font-weight: 500; }
  .alloff {
    display: inline-flex; align-items: center; justify-content: center;
    width: 40px; height: 40px; border-radius: 50%;
    border: none; background: none; cursor: pointer;
    color: var(--primary-text-color); flex: 0 0 auto;
    transition: background 0.15s, transform 0.05s;
  }
  .alloff:hover { background: var(--secondary-background-color); }
  .alloff:active { transform: scale(0.92); }
  .alloff[disabled] { opacity: 0.3; pointer-events: none; }
`;

/** Visual (UI) editor for the hub card — pick an amplifier, plus an optional name. */
class AxiumHubCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  async _ensureHaForm() {
    if (customElements.get("ha-form")) return;
    try {
      const helpers = await window.loadCardHelpers();
      const card = await helpers.createCardElement({ type: "entities", entities: [] });
      await card.constructor.getConfigElement();
    } catch (err) {
      /* ha-form will still upgrade once available */
    }
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.addEventListener("value-changed", (ev) => this._changed(ev));
      this.appendChild(this._form);
      this._ensureHaForm();
    }
    const hubs = axiumHubs(this._hass);
    const options = hubs.map((h) => ({ value: h.id, label: h.name }));
    const data = { ...this._config };
    if (!data.hub && hubs.length) data.hub = hubs[0].id;

    this._form.hass = this._hass;
    this._form.data = data;
    this._form.schema = [
      {
        name: "hub",
        selector: options.length
          ? { select: { mode: "dropdown", options } }
          : { text: {} },
      },
      { name: "name", selector: { text: {} } },
    ];
    this._form.computeLabel = (s) =>
      ({ hub: "Amplifier", name: "Card name (optional)" }[s.name] || s.name);
  }

  _changed(ev) {
    ev.stopPropagation();
    this._config = { ...ev.detail.value };
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: this._config },
        bubbles: true,
        composed: true,
      })
    );
  }
}

/**
 * Axium Matrix Card — the whole-system routing grid for one amplifier.
 *
 * Zones are rows, sources are columns. Each cell shows whether that zone is on
 * that source; tapping a cell routes the zone there, tapping its active cell
 * turns the zone off. Beyond the grid the headers are interactive:
 *   - tap a zone name  → quick volume + transport controls for that zone
 *   - hold a zone name → open the zone's device page
 *   - tap a source name → pick a preset to route onto that source
 * It reads/writes only through the zones' media_player state, storing nothing
 * itself.
 *
 * Config:
/**
 * Reusable Music Assistant search/browse UI (own shadow DOM). Both the matrix
 * stream panel and the alarms wake-song picker embed it, so the search
 * experience is identical. Set-once properties (no attributes):
 *   .hass    — the hass object (set on every parent update; used at query time)
 *   .player  — the MA media_player entity_id to search/browse
 *   .mode    — "play" (tap a row → play it now) or "pick" (tap → fire a `pick`
 *              CustomEvent with the item; nothing is played)
 *   .startBrowse — true → show the library root on open (for the picker)
 * Auto-searches ~1s after typing stops; Enter / the button search immediately.
 */
class AxiumMaSearch extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._mode = "play";
    this._startBrowse = false;
    this._state = { seq: 0 };
    this._debTimer = null;
    this._built = false;
  }
  set hass(h) {
    this._hass = h;
  }
  get hass() {
    return this._hass;
  }
  set player(p) {
    const changed = p !== this._player;
    this._player = p;
    if (changed && this._built && this._startBrowse && p) this._drillRoot();
  }
  set mode(m) {
    this._mode = m;
  }
  set startBrowse(v) {
    this._startBrowse = !!v;
  }

  connectedCallback() {
    this._build();
  }
  disconnectedCallback() {
    if (this._debTimer) clearTimeout(this._debTimer);
    this._cancelPlay();
  }

  _build() {
    if (this._built) return;
    this._built = true;
    this.shadowRoot.innerHTML =
      `<style>${AxiumMaSearch.styles}</style>` +
      `<div class="ssrow">` +
      `<input type="search" class="ssin" placeholder="Search Music Assistant…">` +
      `<button type="button" class="ssbtn" title="Search"><ha-icon icon="mdi:magnify"></ha-icon></button>` +
      `</div>` +
      `<div class="sstabs" hidden></div>` +
      `<div class="ssresults"></div>`;
    const input = this.shadowRoot.querySelector(".ssin");
    const run = () => {
      if (this._debTimer) {
        clearTimeout(this._debTimer);
        this._debTimer = null;
      }
      const q = input.value.trim();
      if (q) this._search(q);
      else if (this._startBrowse) this._drillRoot();
      else this._clear();
    };
    // Auto-search ~1s after typing stops.
    input.addEventListener("input", () => {
      if (this._debTimer) clearTimeout(this._debTimer);
      this._debTimer = setTimeout(run, 1000);
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        run();
      }
    });
    this.shadowRoot.querySelector(".ssbtn").addEventListener("click", run);
    if (this._startBrowse && this._player) this._drillRoot();
  }

  _q(sel) {
    return this.shadowRoot.querySelector(sel);
  }
  _clear() {
    const tabs = this._q(".sstabs");
    if (tabs) tabs.hidden = true;
    const res = this._q(".ssresults");
    if (res) res.innerHTML = "";
  }
  _spinner() {
    const tabs = this._q(".sstabs");
    if (tabs) tabs.hidden = true;
    const res = this._q(".ssresults");
    if (res) res.innerHTML = `<div class="ssspin"></div>`;
  }

  async _search(query) {
    if (!this._hass || !this._player) return;
    this._spinner();
    const seq = ++this._state.seq;
    let hits;
    try {
      hits = await axiumMaSearch(this._hass, this._player, query);
    } catch (e) {
      if (this._state.seq === seq) {
        const r = this._q(".ssresults");
        if (r) r.textContent = "Search failed.";
      }
      return;
    }
    if (this._state.seq !== seq || !this.isConnected) return;
    // Group into tabs; an "All" tab (default) plus a tab per type.
    const groups = {};
    for (const it of hits) {
      const b = this._bucket(it);
      (groups[b] = groups[b] || []).push(it);
    }
    const catOrder = this._tabOrder(Object.keys(groups));
    groups.all = hits;
    const prevTab = this._state.tab;
    this._state.groups = groups;
    this._state.order = hits.length ? ["all", ...catOrder] : [];
    // Keep the user's selected tab across a new search when the new results
    // still have that category; otherwise fall back to All.
    this._state.tab =
      prevTab && this._state.order.includes(prevTab)
        ? prevTab
        : this._state.order[0] || null;
    this._renderTabs();
  }

  // Which tab an item belongs to. Radio stations come back as a generic
  // `media_class: "music"` (radiobrowser/TuneIn), so classify them by the raw
  // provider prefix of the content id (NOT the display label — that would couple
  // bucketing to a human-facing string) into a proper "radio" tab.
  _bucket(it) {
    if (it.media_class === "radio") return "radio";
    if (/^(radiobrowser|tunein|radionet|radio_?browser)(--[^:]*)?:\/\//i.test(
      String(it.media_content_id || "")
    ))
      return "radio";
    return it.media_class || "other";
  }
  _tabOrder(present) {
    const pref = [
      "track", "album", "playlist", "artist", "radio", "music", "podcast",
      "audiobook", "directory", "episode", "genre", "composer",
    ];
    return [
      ...pref.filter((k) => present.includes(k)),
      ...present.filter((k) => !pref.includes(k)).sort(),
    ];
  }
  _tabLabel(mc) {
    const map = {
      all: "All", track: "Tracks", album: "Albums", playlist: "Playlists",
      artist: "Artists", radio: "Radio", music: "Music", podcast: "Podcasts",
      directory: "Audiobooks", audiobook: "Audiobooks", episode: "Episodes",
      genre: "Genres", composer: "Composers", movie: "Movies", video: "Videos",
      tv_show: "Shows", season: "Seasons", channel: "Channels", app: "Apps",
      other: "Other",
    };
    if (map[mc]) return map[mc];
    const t = String(mc || "").replace(/_/g, " ");
    const label = t ? t.charAt(0).toUpperCase() + t.slice(1) : "Other";
    return label.endsWith("s") ? label : label + "s";
  }

  _renderTabs() {
    const tabsEl = this._q(".sstabs");
    if (!tabsEl || !this._state.groups) return;
    const order = this._state.order || [];
    if (!order.includes(this._state.tab)) this._state.tab = order[0] || null;
    tabsEl.hidden = false;
    tabsEl.innerHTML = "";
    for (const t of order) {
      const b = document.createElement("button");
      b.className = "sstab" + (t === this._state.tab ? " on" : "");
      b.textContent = `${this._tabLabel(t)} (${this._state.groups[t].length})`;
      b.addEventListener("click", () => {
        this._state.tab = t;
        this._renderTabs();
      });
      tabsEl.appendChild(b);
    }
    const items = (this._state.tab && this._state.groups[this._state.tab]) || [];
    this._renderItems(items, null, () => this._renderTabs());
  }

  // Browse the library root (picker's initial view).
  async _drillRoot() {
    if (!this._hass || !this._player) return;
    this._spinner();
    const seq = ++this._state.seq;
    let children;
    try {
      const r = await axiumMaBrowse(this._hass, this._player);
      children = r.children || [];
    } catch (e) {
      if (this._state.seq === seq) this._renderError("Couldn't browse.", null);
      return;
    }
    if (this._state.seq !== seq || !this.isConnected) return;
    const tabs = this._q(".sstabs");
    if (tabs) tabs.hidden = true;
    this._renderItems(children, null, () => this._drillRoot());
  }

  // Browse into an expandable item. `back` re-renders the PARENT level, so Back
  // steps up exactly one level at any depth.
  async _drill(item, back) {
    this._spinner();
    const seq = ++this._state.seq;
    let children;
    try {
      const r = await axiumMaBrowse(
        this._hass, this._player, item.media_content_id, item.media_content_type
      );
      children = r.children || [];
    } catch (e) {
      if (this._state.seq === seq) this._renderError("Couldn't open.", back);
      return;
    }
    if (this._state.seq !== seq || !this.isConnected) return;
    const tabs = this._q(".sstabs");
    if (tabs) tabs.hidden = true;
    this._renderItems(children, back, () => this._drill(item, back));
  }

  // `back` = go up one level (null at the top). `rerenderSelf` re-renders THIS
  // list; it becomes the parent-back target when drilling into a child.
  _renderItems(items, back, rerenderSelf) {
    const res = this._q(".ssresults");
    if (!res) return;
    res.innerHTML = "";
    if (back) res.appendChild(this._backButton(back));
    for (const it of items || []) {
      const row = document.createElement("div");
      row.className = "srow";
      const main = document.createElement("button");
      main.className = "sr-play";
      const art = document.createElement("span");
      art.className = "sr-art";
      if (it.thumbnail) art.style.backgroundImage = `url("${it.thumbnail}")`;
      else art.innerHTML = `<ha-icon icon="${this._typeIcon(it.media_class)}"></ha-icon>`;
      const body = document.createElement("span");
      body.className = "sr-body";
      const title = document.createElement("span");
      title.className = "sr-title";
      title.textContent = it.title;
      const sub = document.createElement("span");
      sub.className = "sr-sub";
      sub.textContent = [
        this._providerLabel(it.media_content_id),
        this._typeLabel(it.media_class),
      ]
        .filter(Boolean)
        .join(" · ");
      body.appendChild(title);
      body.appendChild(sub);
      main.appendChild(art);
      main.appendChild(body);
      const canPlay = it.can_play !== false && !!it.media_content_id;
      main.addEventListener("click", () => {
        if (canPlay) this._activate(it);
        else if (it.can_expand) this._drill(it, rerenderSelf);
      });
      row.appendChild(main);
      if (it.can_expand) {
        const exp = document.createElement("button");
        exp.className = "sr-exp";
        exp.title = "Browse";
        exp.innerHTML = `<ha-icon icon="mdi:chevron-right"></ha-icon>`;
        exp.addEventListener("click", () => this._drill(it, rerenderSelf));
        row.appendChild(exp);
      }
      res.appendChild(row);
    }
    if (!(items || []).length) {
      const e = document.createElement("div");
      e.className = "empty";
      e.textContent = "No results.";
      res.appendChild(e);
    }
  }

  _backButton(back) {
    const b = document.createElement("button");
    b.className = "ssback";
    b.textContent = "‹ Back";
    b.addEventListener("click", () => back());
    return b;
  }

  // Show an error but keep the Back button so navigation isn't lost.
  _renderError(msg, back) {
    const res = this._q(".ssresults");
    if (!res) return;
    res.innerHTML = "";
    if (back) res.appendChild(this._backButton(back));
    const e = document.createElement("div");
    e.className = "empty";
    e.textContent = msg;
    res.appendChild(e);
  }

  // Row tap: play now, or (pick mode) fire a `pick` event with the item.
  _activate(it) {
    if (this._mode === "pick") {
      this.dispatchEvent(
        new CustomEvent("pick", { detail: it, bubbles: true, composed: true })
      );
      return;
    }
    if (!this._hass || !this._player) return;
    const play = () => {
      if (!this.isConnected || !this._hass || !this._player) return;
      this._hass.callService("media_player", "play_media", {
        entity_id: this._player,
        media_content_id: it.media_content_id,
        media_content_type: it.media_content_type,
        enqueue: "play",
      });
    };
    const st = this._hass.states[this._player];
    this._cancelPlay(); // drop a pending double-play from an earlier tap
    play();
    // Let the embedder reflect that playback started (optimistic stop button).
    this.dispatchEvent(new CustomEvent("play", { bubbles: true, composed: true }));
    // Verified on hardware: `play_media` while the renderer is already PLAYING
    // stops it (goes idle) instead of switching — a second `play_media` from the
    // now-idle state actually plays it (the "tap twice" the user hit). Store the
    // handle so it can be cancelled on close / a newer tap / disconnect.
    if (st && st.state === "playing") this._playTimer = setTimeout(play, 1500);
  }

  _cancelPlay() {
    if (this._playTimer) {
      clearTimeout(this._playTimer);
      this._playTimer = null;
    }
  }

  // Called by the embedding card when its popover closes — cancel any pending
  // deferred play so it can't fire after the panel is dismissed.
  cancelPending() {
    this._cancelPlay();
    if (this._debTimer) {
      clearTimeout(this._debTimer);
      this._debTimer = null;
    }
  }

  _providerLabel(id) {
    const m = /^([a-z0-9_]+)(?:--[^:]*)?:\/\//i.exec(String(id || ""));
    if (!m) return "";
    const p = m[1].toLowerCase();
    return (
      {
        spotify: "Spotify", radiobrowser: "Radio", tunein: "TuneIn",
        library: "Library", filesystem_local: "Local", filesystem_smb: "Local",
        qobuz: "Qobuz", tidal: "Tidal", ytmusic: "YT Music", deezer: "Deezer",
        apple_music: "Apple Music", soundcloud: "SoundCloud", plex: "Plex",
        jellyfin: "Jellyfin",
      }[p] || p.charAt(0).toUpperCase() + p.slice(1)
    );
  }
  _typeIcon(c) {
    return (
      {
        track: "mdi:music-note", album: "mdi:album", playlist: "mdi:playlist-music",
        artist: "mdi:account-music", podcast: "mdi:podcast",
        directory: "mdi:book-music", music: "mdi:radio",
      }[c] || "mdi:music"
    );
  }
  _typeLabel(c) {
    return (
      {
        track: "Track", album: "Album", playlist: "Playlist", artist: "Artist",
        podcast: "Podcast", directory: "Audiobook", music: "Radio",
      }[c] || ""
    );
  }
}

AxiumMaSearch.styles = `
  :host { display: flex; flex-direction: column; min-height: 0; }
  .ssrow { display: flex; gap: 6px; }
  .ssin {
    flex: 1 1 auto; min-width: 0; font: inherit; padding: 7px 9px; border-radius: 8px;
    border: 1px solid var(--divider-color); background: var(--card-background-color);
    color: var(--primary-text-color);
  }
  .ssbtn {
    display: inline-flex; align-items: center; justify-content: center; cursor: pointer;
    border: 1px solid var(--divider-color); border-radius: 8px; padding: 0 12px;
    background: var(--card-background-color); color: var(--primary-text-color); --mdc-icon-size: 20px;
  }
  .ssbtn:hover { border-color: var(--primary-color); color: var(--primary-color); }
  .sstabs { display: flex; gap: 4px; margin-top: 8px; flex-wrap: wrap; flex: 0 0 auto; }
  .sstabs[hidden] { display: none; }
  .sstab {
    font: inherit; font-size: 0.8rem; padding: 4px 10px; border-radius: 14px; cursor: pointer;
    border: 1px solid var(--divider-color); background: none; color: var(--secondary-text-color);
  }
  .sstab.on { border-color: var(--primary-color); color: var(--text-primary-color, #fff); background: var(--primary-color); }
  .ssresults { display: flex; flex-direction: column; margin-top: 6px; flex: 1 1 auto; min-height: 0; overflow-y: auto; }
  .ssresults:empty { display: none; }
  .ssspin {
    align-self: center; margin: 22px auto; width: 30px; height: 30px; border-radius: 50%;
    border: 3px solid var(--divider-color); border-top-color: var(--primary-color);
    animation: axium-spin 0.8s linear infinite;
  }
  @keyframes axium-spin { to { transform: rotate(360deg); } }
  .ssback {
    align-self: flex-start; background: none; border: none; cursor: pointer; font: inherit;
    color: var(--secondary-text-color); padding: 4px 2px; margin-bottom: 2px;
  }
  .ssback:hover { color: var(--primary-color); }
  .srow { display: flex; align-items: center; gap: 2px; }
  .sr-play {
    flex: 1 1 auto; min-width: 0; display: flex; align-items: center; gap: 10px;
    text-align: left; background: none; border: none; cursor: pointer; font: inherit;
    color: var(--primary-text-color); padding: 6px; border-radius: 8px;
  }
  .sr-play:hover { background: var(--secondary-background-color); }
  .sr-art {
    flex: 0 0 auto; width: 40px; height: 40px; border-radius: 6px;
    background: var(--secondary-background-color) center/cover no-repeat;
    display: flex; align-items: center; justify-content: center;
    color: var(--secondary-text-color); --mdc-icon-size: 22px;
  }
  .sr-body { min-width: 0; display: flex; flex-direction: column; }
  .sr-title { font-size: 0.92rem; color: var(--primary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .sr-sub { font-size: 0.78rem; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .sr-exp {
    flex: 0 0 auto; display: inline-flex; align-items: center; justify-content: center;
    width: 34px; height: 34px; border-radius: 50%; border: none; background: none;
    cursor: pointer; color: var(--secondary-text-color); --mdc-icon-size: 22px;
  }
  .sr-exp:hover { background: var(--secondary-background-color); color: var(--primary-color); }
  .empty { color: var(--secondary-text-color); padding: 8px 2px; }
`;

/**
 *   type: custom:axium-matrix-card
 *   hub: <config_entry_id>   # optional — defaults to the only Axium hub
 *   name: Matrix             # optional — header text
 *   entities: [...]          # optional — zone media_players (rows); auto if omitted
 */
class AxiumMatrixCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._built = false;
    this._structSig = "";
  }

  setConfig(config) {
    this._config = config || {};
    this._built = false;
    this.shadowRoot.innerHTML = "";
  }

  set hass(hass) {
    this._hass = hass;
    if (!hass || !this._config) return;
    const sig = this._signature();
    if (!this._built || sig !== this._structSig) {
      this._structSig = sig;
      this._build();
    }
    this._update();
  }

  getCardSize() {
    return 3;
  }

  disconnectedCallback() {
    // Cancel a pending debounced volume_set and drop the open popover so a stale
    // service call can't fire after the card is removed.
    if (this._volTimer) {
      clearTimeout(this._volTimer);
      this._volTimer = null;
    }
    this._panel = null;
  }

  static getConfigElement() {
    return document.createElement("axium-matrix-card-editor");
  }

  static getStubConfig(hass) {
    const hubs = axiumHubs(hass);
    return hubs.length ? { hub: hubs[0].id } : {};
  }

  _hubId() {
    return this._config.hub || (axiumHubs(this._hass)[0] || {}).id;
  }

  _zones() {
    let auto = axiumMediaPlayers(this._hass, this._hubId());
    const pick = this._config.zones || this._config.entities;
    if (Array.isArray(pick) && pick.length) {
      auto = auto.filter((id) => pick.includes(id));
    }
    // Order rows by physical zone number (1..16, and beyond for more amps).
    return auto.sort((a, b) => this._zoneNum(a) - this._zoneNum(b));
  }

  _zoneNum(id) {
    const st = this._hass.states[id];
    const n = st && st.attributes.zone_number;
    return typeof n === "number" ? n : 9999;
  }

  // Every source offered across the hub's zones, as {id, name} (unfiltered — the
  // `sources` whitelist is applied per column in _columns(), since stream sources
  // are filtered per amp, not by the single Media Player id).
  _sources() {
    const byId = new Map();
    for (const id of axiumMediaPlayers(this._hass, this._hubId())) {
      const st = this._hass.states[id];
      const ids = st && st.attributes.source_ids;
      const names = st && st.attributes.source_list;
      if (Array.isArray(ids) && Array.isArray(names)) {
        ids.forEach((sid, i) => {
          if (!byId.has(sid)) byId.set(sid, names[i]);
        });
      }
    }
    return [...byId.entries()]
      .map(([id, name]) => ({ id, name }))
      .sort((a, b) => String(a.name).localeCompare(String(b.name)));
  }

  // The configured `sources` whitelist as a Set of string values (analog ids as
  // "5", per-amp streams as "stream:<ampId>"); null when unset (show all).
  _sourceFilter() {
    const wl = this._config.sources;
    return Array.isArray(wl) && wl.length ? new Set(wl.map(String)) : null;
  }

  /** The amps in this card's zone set, each with the zones it owns and its
   *  device name (e.g. "Axium 1"), from the device tree (zone → via_device amp). */
  _amps() {
    const devs = this._hass.devices || {};
    const reg = this._hass.entities || {};
    const order = [];
    const byAmp = new Map();
    for (const zoneId of this._zones()) {
      const ent = reg[zoneId];
      const zdev = ent && devs[ent.device_id];
      if (!zdev) continue;
      const amp = devs[zdev.via_device_id] || zdev;
      if (!byAmp.has(amp.id)) {
        // The master amp's identifier has no "_unit_" (expansions) and no
        // "_zone_" (zone devices) — see axiumAmps for why the "_zone_" guard
        // matters (zone-device fallback during a registry-sync window).
        const master = (amp.identifiers || []).some(
          (t) =>
            t[0] === "axium" &&
            !String(t[1]).includes("_unit_") &&
            !String(t[1]).includes("_zone_")
        );
        byAmp.set(amp.id, {
          id: amp.id,
          name: (amp.name_by_user || amp.name) || "Amp",
          zones: new Set(),
          master,
        });
        order.push(amp.id);
      }
      byAmp.get(amp.id).zones.add(zoneId);
    }
    return order.map((id) => byAmp.get(id));
  }

  /** Ordered matrix columns: analog sources, then one STREAM column per amp for
   *  the internal media player (each amp's stream is a column). Each amp's column
   *  owns ONLY its own zones (streams are per-amp). Falls back to plain media
   *  columns if amps unknown. */
  _columns() {
    const srcs = this._sources();
    const filter = this._sourceFilter();
    const analogOk = (s) => !filter || filter.has(String(s.id));
    const analog = srcs.filter((s) => s.id < STREAM_SOURCE_MIN);
    const streams = srcs.filter((s) => s.id >= STREAM_SOURCE_MIN);
    const cols = analog
      .filter(analogOk)
      .map((s) => ({ kind: "source", id: s.id, name: s.name }));
    const amps = this._amps();
    if (streams.length && amps.length) {
      const sid = streams[0].id;
      // Each amp's Media Player stream drives ONLY its own zones — the streams
      // are independent per amp (an amp can't play another amp's zones). The
      // `sources` whitelist targets a stream per amp ("stream:<ampId>"); a legacy
      // numeric Media-Player id whitelists all streams (migration).
      for (const amp of amps) {
        if (!filter || filter.has(`stream:${amp.id}`) || filter.has(String(sid)))
          cols.push({
            kind: "stream",
            id: sid,
            ampId: amp.id,
            name: amp.name,
            zones: amp.zones,
          });
      }
      for (const s of streams.slice(1))
        if (analogOk(s)) cols.push({ kind: "source", id: s.id, name: s.name });
    } else {
      for (const s of streams)
        if (analogOk(s)) cols.push({ kind: "source", id: s.id, name: s.name });
    }
    return cols;
  }

  // Rebuild only when the zone/column set (incl. amp names) changes, not per tick.
  _signature() {
    return (
      this._zones().join(",") +
      "|" +
      this._columns()
        .map((c) => (c.kind === "stream" ? `A:${c.ampId}:${c.name}` : c.id))
        .join(",")
    );
  }

  _sourceNameFor(st, sid) {
    const ids = st && st.attributes.source_ids;
    const names = st && st.attributes.source_list;
    if (!Array.isArray(ids) || !Array.isArray(names)) return null;
    const i = ids.indexOf(sid);
    return i >= 0 ? names[i] : null;
  }

  _currentSourceId(st) {
    const names = st && st.attributes.source_list;
    const ids = st && st.attributes.source_ids;
    if (!Array.isArray(names) || !Array.isArray(ids)) return null;
    const i = names.indexOf(st.attributes.source);
    return i >= 0 ? ids[i] : null;
  }

  _zoneName(id) {
    const st = this._hass.states[id];
    const n = st && st.attributes.friendly_name;
    return n ? n : id.split(".")[1].replace(/_/g, " ");
  }

  _route(zoneId, src, ampId) {
    const st = this._hass.states[zoneId];
    const sid = Number(src);
    const on = st && !OFF_STATES.includes(st.state);
    const play = (id, svc) =>
      this._hass.callService("media_player", svc, { entity_id: id });
    const enable = () => {
      // Put the zone on the Media Player source (select_source powers it on).
      if (!(on && this._currentSourceId(st) === sid)) {
        const name = this._sourceNameFor(st, sid);
        if (name != null)
          this._hass.callService("media_player", "select_source", {
            entity_id: zoneId,
            source: name,
          });
      }
    };
    if (ampId) {
      // A stream (amp) cell — each amp drives only its own zones. Tapping the
      // stream a room is already on turns it off; otherwise put the room on the
      // Media Player and resume that amp's stream so sound actually starts.
      if (this._streamCellActive(zoneId, ampId)) {
        this._turnZoneOff(zoneId);
        return;
      }
      const col = this._amps().find((a) => a.id === ampId);
      const ma = col && this._ampStreamPlayerByName(col.name);
      if (ma && ma.state !== "playing") play(ma.entity_id, "media_play");
      enable();
      return;
    }
    // Analog source cell: tap the active source to turn the zone off.
    if (on && this._currentSourceId(st) === sid) {
      this._turnZoneOff(zoneId);
      return;
    }
    enable();
  }

  _build() {
    const zones = this._zones();
    const columns = this._columns();
    const head =
      `<div class="corner"><button class="allpower" title="All zones on/off">` +
      `<ha-icon icon="mdi:power"></ha-icon></button></div>` +
      columns
        .map((c) => {
          const key = this._colKey(c);
          const pwr =
            `<button class="srcpwr" data-colkey="${escHtml(key)}"` +
            ` title="Turn ${escHtml(c.name)} on/off"><ha-icon icon="mdi:power"></ha-icon></button>`;
          const attrs =
            c.kind === "stream"
              ? `class="colhead stream" data-amp="${c.ampId}"`
              : `class="colhead" data-src="${c.id}"`;
          return (
            `<div ${attrs} role="button" tabindex="0" data-colkey="${escHtml(key)}"` +
            ` title="${escHtml(c.name)}">${pwr}<span>${escHtml(c.name)}</span></div>`
          );
        })
        .join("");
    const rows = zones
      .map((z) => {
        const cells = columns
          .map((c) => {
            if (c.kind === "stream" && !c.zones.has(z)) return `<div class="cell blank"></div>`;
            const amp = c.kind === "stream" ? ` data-amp="${c.ampId}"` : "";
            return (
              `<button class="cell" data-zone="${z}" data-src="${c.id}"${amp}>` +
              `<ha-icon icon="mdi:check"></ha-icon></button>`
            );
          })
          .join("");
        return (
          `<div class="rowhead" role="button" tabindex="0" data-zone="${z}"` +
          ` title="${escHtml(this._zoneName(z))}">${escHtml(this._zoneName(z))}</div>` +
          cells
        );
      })
      .join("");

    this.shadowRoot.innerHTML = `
      <style>${AxiumMatrixCard.styles}</style>
      <ha-card>
        ${this._config.name ? `<div class="title">${escHtml(this._config.name)}</div>` : ""}
        <div class="scroll">
          <div class="matrix" style="grid-template-columns: minmax(72px,auto) repeat(${
            columns.length
          }, minmax(44px,1fr));">
            ${head}${rows}
          </div>
        </div>
        <div class="overlay" id="overlay" hidden><div class="sheet" id="sheet"></div></div>
      </ha-card>
    `;

    this.shadowRoot.querySelector(".matrix").addEventListener("click", (ev) => {
      const cell = ev.target.closest("button.cell");
      if (!cell) return;
      this._route(cell.dataset.zone, cell.dataset.src, cell.dataset.amp);
    });
    // Zone header: tap for quick volume/transport, hold for the device page.
    for (const rh of this.shadowRoot.querySelectorAll(".rowhead[data-zone]")) {
      this._attachHold(
        rh,
        () => this._openZonePanel(rh.dataset.zone, rh),
        () => this._openZoneDevice(rh.dataset.zone)
      );
    }
    // Column header: an amp (stream) header opens that amp's stream panel; a
    // plain source header opens its preset picker.
    for (const ch of this.shadowRoot.querySelectorAll(".colhead")) {
      const open = ch.dataset.amp
        ? () => this._openStreamPanel(ch.dataset.amp, ch)
        : () => this._openPresetPanel(ch.dataset.src, ch);
      ch.addEventListener("click", open);
      ch.addEventListener("keydown", (ev) => {
        // Only when the header itself is focused — not the nested power button
        // (Enter on it should toggle the source, not also open the panel).
        if (ev.target !== ch) return;
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          open();
        }
      });
    }
    // Per-source power toggle (nested in the header; stop the click from also
    // opening the header's preset/stream panel).
    for (const pw of this.shadowRoot.querySelectorAll(".srcpwr")) {
      pw.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const col = this._columns().find((c) => this._colKey(c) === pw.dataset.colkey);
        if (col) this._toggleSourcePower(col);
      });
    }
    const allpwr = this.shadowRoot.querySelector(".allpower");
    if (allpwr)
      allpwr.addEventListener("click", () => this._toggleAllPower());
    const overlay = this.shadowRoot.getElementById("overlay");
    overlay.addEventListener("click", (ev) => {
      if (ev.target === overlay) this._closePanel();
    });
    this._panel = null;
    this._built = true;
  }

  /** Show the popover overlay covering the DASHBOARD content, but not HA's
   *  sidebar/header (best-effort: measure the sidebar for the left offset, and
   *  offset the top by the header height). Falls back to full-viewport if the
   *  HA layout can't be measured. The sheet is a centered near-full sheet on
   *  mobile; on desktop it's small and anchored just below the tapped element
   *  (`anchorEl`) so the cursor barely has to move — clamped into view. */
  _showOverlay(anchorEl) {
    const overlay = this.shadowRoot.getElementById("overlay");
    if (!overlay) return;
    let left = 0;
    try {
      const ha = document.querySelector("home-assistant");
      const main = ha && ha.shadowRoot && ha.shadowRoot.querySelector("home-assistant-main");
      const sb = main && main.shadowRoot && main.shadowRoot.querySelector("ha-sidebar");
      if (sb) {
        const r = sb.getBoundingClientRect();
        // A docked sidebar sits at the left edge; ignore it when it's an
        // off-screen mobile drawer or spans most of the width.
        if (r.width > 0 && r.left <= 1 && r.right > 0 && r.right < window.innerWidth * 0.5)
          left = Math.round(r.right);
      }
    } catch (e) {
      /* HA internals moved — fall back to full-viewport */
    }
    overlay.style.left = left + "px";
    overlay.style.top = "var(--header-height, 56px)";
    overlay.hidden = false;

    // Reset to the CSS default (centered) so mobile / no-anchor stays centered.
    const sheet = overlay.querySelector(".sheet");
    if (sheet) {
      sheet.style.left = "";
      sheet.style.top = "";
      sheet.style.transform = "";
      const desktop =
        window.matchMedia && window.matchMedia("(min-width: 768px)").matches;
      if (desktop && anchorEl) {
        // Measure now that it's visible (synchronous, before paint = no flicker),
        // then place it just under the tapped header and clamp inside the overlay.
        const oR = overlay.getBoundingClientRect();
        const aR = anchorEl.getBoundingClientRect();
        const sR = sheet.getBoundingClientRect();
        const l = Math.max(8, Math.min(aR.left - oR.left, Math.max(8, oR.width - sR.width - 8)));
        const t = Math.max(8, Math.min(aR.bottom - oR.top + 6, Math.max(8, oR.height - sR.height - 8)));
        sheet.style.left = l + "px";
        sheet.style.top = t + "px";
        sheet.style.transform = "none";
      }
    }
  }

  /** Corner power button: if any zone is on, turn them all off; else all on. */
  _toggleAllPower() {
    const zones = this._zones();
    const anyOn = zones.some((z) => {
      const st = this._hass.states[z];
      return st && !OFF_STATES.includes(st.state);
    });
    this._hass.callService("media_player", anyOn ? "turn_off" : "turn_on", {
      entity_id: zones,
    });
  }

  /** Stable key for a matrix column: an analog source id, or "stream:<ampId>". */
  _colKey(col) {
    return col.kind === "stream" ? `stream:${col.ampId}` : String(col.id);
  }

  /** The zones currently powered on and routed to this column's source. */
  _activeZonesForColumn(col, zonesArg) {
    const all = zonesArg || this._zones();
    const zones = col.kind === "stream" ? all.filter((z) => col.zones.has(z)) : all;
    return zones.filter((z) => {
      if (col.kind === "stream") return this._streamCellActive(z, col.ampId);
      const st = this._hass.states[z];
      return (
        st && !OFF_STATES.includes(st.state) && this._currentSourceId(st) === col.id
      );
    });
  }

  /**
   * Per-source on/off. OFF (source has active zones): remember those zones, then
   * power them off. ON (no active zones): re-enable the remembered zones on this
   * source. The remembered set is persisted (localStorage, per hub) so it
   * survives a page reload; an empty memory (never turned off here) means ON has
   * nothing to restore and is a no-op.
   */
  _toggleSourcePower(col) {
    const key = this._colKey(col);
    const active = this._activeZonesForColumn(col);
    if (active.length) {
      this._rememberZones(key, active);
      for (const z of active) this._turnZoneOff(z);
    } else {
      const ampId = col.kind === "stream" ? col.ampId : undefined;
      for (const z of this._recallZones(key)) {
        if (this._hass.states[z]) this._route(z, col.id, ampId);
      }
    }
  }

  _srcMemKey() {
    return `axium-matrix-srcmem:${this._hubId() || "x"}`;
  }
  _srcMemAll() {
    try {
      return JSON.parse(window.localStorage.getItem(this._srcMemKey()) || "{}") || {};
    } catch (e) {
      return {};
    }
  }
  _rememberZones(colKey, zones) {
    const all = this._srcMemAll();
    all[colKey] = zones;
    try {
      window.localStorage.setItem(this._srcMemKey(), JSON.stringify(all));
    } catch (e) {
      /* storage unavailable — degrade to no persistence */
    }
  }
  _recallZones(colKey) {
    const z = this._srcMemAll()[colKey];
    return Array.isArray(z) ? z : [];
  }

  /**
   * Wire an element for tap vs. hold using pointer events (works for mouse and
   * touch). A 500ms hold fires `onHold` and suppresses the following click;
   * a plain tap (or Enter/Space) fires `onTap`.
   */
  _attachHold(el, onTap, onHold) {
    let timer = null;
    let held = false;
    const cancel = () => {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
    };
    el.addEventListener("pointerdown", () => {
      held = false;
      cancel();
      timer = setTimeout(() => {
        held = true;
        onHold();
      }, 500);
    });
    el.addEventListener("pointerup", cancel);
    el.addEventListener("pointerleave", cancel);
    el.addEventListener("pointercancel", cancel);
    el.addEventListener("click", (ev) => {
      if (held) {
        ev.preventDefault();
        ev.stopPropagation();
        held = false;
        return;
      }
      onTap();
    });
    el.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        onTap();
      }
    });
    el.addEventListener("contextmenu", (ev) => ev.preventDefault());
  }

  /** Navigate to a zone's device page (fallback: its more-info dialog). */
  _openZoneDevice(id) {
    const entry = this._hass.entities && this._hass.entities[id];
    const deviceId = entry && entry.device_id;
    if (deviceId) {
      history.pushState(null, "", `/config/devices/device/${deviceId}`);
      this.dispatchEvent(
        new CustomEvent("location-changed", { bubbles: true, composed: true })
      );
      return;
    }
    this.dispatchEvent(
      new CustomEvent("hass-more-info", {
        detail: { entityId: id },
        bubbles: true,
        composed: true,
      })
    );
  }

  // -- presets & popover ----------------------------------------------

  /** The hub's zone presets (read from any hub zone's attribute). */
  _presets() {
    for (const id of axiumMediaPlayers(this._hass, this._hubId())) {
      const st = this._hass.states[id];
      const p = st && st.attributes.axium_presets;
      if (Array.isArray(p)) return p;
    }
    return [];
  }

  /**
   * Apply a preset onto one source column "set exactly": its zones (that offer
   * the source) start playing it, and any zone currently on that source but not
   * in the preset is turned off — so the source's active set becomes exactly the
   * preset. Mirrors the source card's preset behaviour.
   */
  _applyPresetToSource(index, sid) {
    const preset = this._presets()[Number(index)];
    if (!preset) return;
    const known = new Set(this._zones());
    const target = (preset.zones || []).filter((z) => known.has(z));
    const targetSet = new Set(target);
    for (const z of target) {
      const name = this._sourceNameFor(this._hass.states[z], sid);
      if (name != null) {
        this._hass.callService("media_player", "select_source", {
          entity_id: z,
          source: name,
        });
      }
    }
    for (const z of this._zones()) {
      if (targetSet.has(z)) continue;
      const st = this._hass.states[z];
      const on = st && !OFF_STATES.includes(st.state);
      if (on && this._currentSourceId(st) === sid) {
        this._hass.callService("media_player", "turn_off", { entity_id: z });
      }
    }
  }

  /** Play an amp's stream in exactly a preset's rooms: start/resume the amp's MA
   *  stream, put the preset's rooms (within the amp's reach — all for the master,
   *  its own for an expansion) on Media Player, and drop the amp's other rooms. */
  _applyPresetToStream(idx, ampId) {
    const preset = this._presets()[Number(idx)];
    const amp = this._amps().find((a) => a.id === ampId);
    const streamCol = this._columns().find((c) => c.kind === "stream");
    if (!preset || !amp || !streamCol) return;
    const sid = streamCol.id;
    const ma = this._ampStreamPlayerByName(amp.name);
    if (ma) this._hass.callService("media_player", "media_play", { entity_id: ma.entity_id });
    // Per-amp streams: a preset only affects this amp's own zones.
    const scope = [...amp.zones];
    const target = new Set(preset.zones || []);
    for (const z of scope) {
      if (target.has(z)) {
        const name = this._sourceNameFor(this._hass.states[z], sid);
        if (name != null)
          this._hass.callService("media_player", "select_source", { entity_id: z, source: name });
      } else if (this._streamCellActive(z, ampId)) {
        this._turnZoneOff(z);
      }
    }
  }

  _closePanel() {
    const overlay = this.shadowRoot.getElementById("overlay");
    if (overlay) overlay.hidden = true;
    if (this._volTimer) {
      clearTimeout(this._volTimer);
      this._volTimer = null;
    }
    // Cancel any pending deferred play in the search component so it can't fire
    // after the popover is dismissed.
    const search = this.shadowRoot.querySelector("axium-ma-search");
    if (search && search.cancelPending) search.cancelPending();
    this._panel = null;
  }

  /** Quick per-zone volume slider + mute + transport, in the popover. */
  /** The per-zone tone (EQ) entities on a zone's device: bass/treble/balance
   *  number entities and the loudness switch. */
  _toneEntities(zoneId) {
    const reg = this._hass.entities || {};
    const devId = reg[zoneId] && reg[zoneId].device_id;
    const out = {};
    if (!devId) return out;
    for (const id of Object.keys(reg)) {
      if (reg[id].device_id !== devId || reg[id].platform !== "axium") continue;
      if (id.startsWith("number.") && id.endsWith("_bass")) out.bass = id;
      else if (id.startsWith("number.") && id.endsWith("_treble")) out.treble = id;
      else if (id.startsWith("number.") && id.endsWith("_balance")) out.balance = id;
      else if (id.startsWith("switch.") && id.endsWith("_loudness")) out.loudness = id;
      else if (id.startsWith("switch.") && id.endsWith("_mono")) out.mono = id;
    }
    return out;
  }

  _openZonePanel(zoneId, anchorEl) {
    const sheet = this.shadowRoot.getElementById("sheet");
    const tone = this._toneEntities(zoneId);
    const row = (key, label, icon) =>
      tone[key]
        ? `<label class="tonerow"><ha-icon icon="${icon}"></ha-icon>` +
          `<span class="tonelbl">${label}</span>` +
          `<input class="toneslider" type="range" data-eid="${tone[key]}">` +
          `<span class="toneval"></span></label>`
        : "";
    const toggle = (key, label, icon) =>
      tone[key]
        ? `<label class="tonerow"><ha-icon icon="${icon}"></ha-icon>` +
          `<span class="tonelbl">${label}</span>` +
          `<input class="toneswitch" type="checkbox" data-eid="${tone[key]}"></label>`
        : "";
    sheet.innerHTML = `
      <div class="sheet-head">
        <span class="sheet-title"></span>
        <span class="sheet-actions">
          <button class="iconbtn power" title="Power on/off"><ha-icon icon="mdi:power"></ha-icon></button>
          <button class="iconbtn close" title="Close"><ha-icon icon="mdi:close"></ha-icon></button>
        </span>
      </div>
      <div class="volrow">
        <button class="iconbtn mute" title="Mute"><ha-icon icon="mdi:volume-high"></ha-icon></button>
        <div class="slidwrap">
          <input class="slider" type="range" min="0" max="100" step="1" aria-label="Volume">
          <div class="slidcap"></div>
        </div>
        <span class="volval"></span>
      </div>
      <div class="tone">
        ${row("bass", "Bass", "mdi:music-clef-bass")}
        ${row("treble", "Treble", "mdi:music-note")}
        ${row("balance", "Balance", "mdi:pan-horizontal")}
        ${toggle("loudness", "Loudness", "mdi:sine-wave")}
        ${toggle("mono", "Mono", "mdi:merge")}
      </div>
    `;
    sheet.querySelector(".sheet-title").textContent = this._zoneName(zoneId);
    sheet.querySelector(".power").addEventListener("click", () =>
      this._togglePower(zoneId)
    );
    const slider = sheet.querySelector(".slider");
    slider.addEventListener("input", () => {
      const maxv = axiumMaxVolume(this._hass, zoneId);
      if (Number(slider.value) > maxv) slider.value = maxv;
      this._panel.dragging = true;
      sheet.querySelector(".volval").textContent = `${slider.value}%`;
      this._scheduleVolume(zoneId, Number(slider.value));
    });
    slider.addEventListener("change", () => {
      this._setVolume(zoneId, Number(slider.value));
      this._panel.dragging = false;
    });
    sheet.querySelector(".mute").addEventListener("click", () =>
      this._toggleMute(zoneId)
    );
    for (const el of sheet.querySelectorAll(".toneslider")) {
      const a = (this._hass.states[el.dataset.eid] || {}).attributes || {};
      el.min = a.min != null ? a.min : -12;
      el.max = a.max != null ? a.max : 12;
      el.step = a.step != null ? a.step : 1;
      el.addEventListener("input", () => {
        this._panel.toneDrag = el.dataset.eid;
        el.parentElement.querySelector(".toneval").textContent = el.value;
      });
      el.addEventListener("change", () => {
        this._hass.callService("number", "set_value", {
          entity_id: el.dataset.eid,
          value: Number(el.value),
        });
        this._panel.toneDrag = null;
      });
    }
    for (const sw of sheet.querySelectorAll(".toneswitch")) {
      sw.addEventListener("change", () =>
        this._hass.callService("switch", "toggle", { entity_id: sw.dataset.eid })
      );
    }
    sheet.querySelector(".close").addEventListener("click", () => this._closePanel());
    this._panel = { type: "zone", zoneId, dragging: false, toneDrag: null };
    this._showOverlay(anchorEl);
    this._refreshPanel();
  }

  /** Amp stream popover: now-playing + transport + volume for the amp's Music
   *  Assistant stream, and a button to browse MA for playlists. */
  _openStreamPanel(ampId, anchorEl) {
    const amp = this._amps().find((a) => a.id === ampId);
    if (!amp) return;
    const ma = this._ampStreamPlayerByName(amp.name);
    const maId = ma ? ma.entity_id : null;
    const sheet = this.shadowRoot.getElementById("sheet");
    const presets = this._presets();
    sheet.innerHTML = `
      <div class="sheet-head">
        <span class="sheet-title"></span>
        <button class="iconbtn close" title="Close"><ha-icon icon="mdi:close"></ha-icon></button>
      </div>
      <div class="nowplaying" hidden>
        <div class="np-art"></div>
        <div class="np-meta">
          <div class="np-title"></div>
          <div class="np-artist"></div>
        </div>
      </div>
      ${
        presets.length
          ? `<select class="presetsel"><option value="">Play in preset…</option>` +
            presets.map((p, i) => `<option value="${i}">${escHtml(p.name)}</option>`).join("") +
            `</select>`
          : ""
      }
      <div class="transport">
        <button class="iconbtn" data-v="down" title="Volume down"><ha-icon icon="mdi:volume-minus"></ha-icon></button>
        <button class="iconbtn" data-t="prev" title="Previous"><ha-icon icon="mdi:skip-previous"></ha-icon></button>
        <button class="iconbtn pauseplay" data-t="pauseplay" title="Pause / resume the rooms"><ha-icon icon="mdi:pause"></ha-icon></button>
        <button class="iconbtn play" data-t="play" title="Stop / start the stream"><ha-icon icon="mdi:play"></ha-icon></button>
        <button class="iconbtn" data-t="next" title="Next"><ha-icon icon="mdi:skip-next"></ha-icon></button>
        <button class="iconbtn" data-v="up" title="Volume up"><ha-icon icon="mdi:volume-plus"></ha-icon></button>
      </div>
      <axium-ma-search class="masearch" style="flex:1 1 auto; min-height:0;"></axium-ma-search>
      <button class="browse"><ha-icon icon="mdi:playlist-music"></ha-icon><span>Browse Music Assistant</span></button>
      <div class="empty" hidden></div>
    `;
    sheet.querySelector(".sheet-title").textContent = amp.name;
    sheet.querySelector(".close").addEventListener("click", () => this._closePanel());
    const psel = sheet.querySelector(".presetsel");
    if (psel)
      psel.addEventListener("change", () => {
        if (psel.value !== "") {
          this._applyPresetToStream(Number(psel.value), ampId);
          psel.value = "";
        }
      });
    // Volume is relative across the rooms playing this stream — the amp has no
    // master volume, and the MA renderer's own volume is decoupled from output.
    for (const b of sheet.querySelectorAll("button[data-v]")) {
      b.addEventListener("click", () =>
        this._streamVolume(ampId, b.dataset.v === "up" ? 1 : -1)
      );
    }
    if (!maId) {
      // No MA player for this amp — hide transport/search/browse but keep the
      // volume buttons (they act on the amp's zones, not the MA player).
      for (const el of sheet.querySelectorAll(
        "button[data-t], .browse, axium-ma-search"
      ))
        el.style.display = "none";
      const empty = sheet.querySelector(".empty");
      empty.hidden = false;
      empty.textContent =
        `No Music Assistant player named "${amp.name}". Rename this amp's ` +
        `MA player to "${amp.name}" to control its stream here.`;
    } else {
      for (const b of sheet.querySelectorAll("button[data-t]")) {
        b.addEventListener("click", () => {
          if (b.dataset.t === "prev")
            this._hass.callService("media_player", "media_previous_track", { entity_id: maId });
          else if (b.dataset.t === "next")
            this._hass.callService("media_player", "media_next_track", { entity_id: maId });
          // "Pause"/resume the ROOMS by powering the amp's stream zones off/on
          // (reuses the per-source power logic). The MA stream keeps running, so
          // resuming is instant — a true transport pause isn't possible (this
          // amp's DLNA renderer ignores pause).
          else if (b.dataset.t === "pauseplay") this._toggleStreamRooms(ampId);
          // media_stop halts the actual stream (restart loses the position).
          else this._togglePlayStop(maId);
        });
      }
      sheet.querySelector(".browse").addEventListener("click", () => {
        this.dispatchEvent(
          new CustomEvent("hass-more-info", {
            detail: { entityId: maId },
            bubbles: true,
            composed: true,
          })
        );
        this._closePanel();
      });
      // Shared Music Assistant search component, in "play" mode.
      const search = sheet.querySelector("axium-ma-search");
      if (search) {
        search.mode = "play";
        search.hass = this._hass;
        search.player = maId;
        // When a search result starts playing, reflect it on the play/stop
        // button (the component can't touch this panel's optimistic flag).
        search.addEventListener("play", () => {
          if (this._panel && this._panel.type === "stream") {
            this._panel.streamPlaying = true;
            this._setStreamPlayIcon();
          }
        });
      }
    }
    const st0 = this._hass.states[maId];
    this._panel = {
      type: "stream",
      ampId,
      maId,
      dragging: false,
      // Optimistic play/stop state — the reported state is unreliable (this
      // player reports "playing" even when stopped externally), so we track
      // intent and only trust a definite off/idle state to clear it.
      streamPlaying: st0 ? !OFF_STATES.includes(st0.state) : false,
    };
    this._showOverlay(anchorEl);
    this._refreshPanel();
  }

  /** Toggle the amp stream between playing and stopped. Pause is a no-op on this
   *  amp's renderer, so "stop" is the working halt; play resumes the queue. */
  _togglePlayStop(maId) {
    if (!this._panel) return;
    const playing = this._panel.streamPlaying !== false;
    this._hass.callService(
      "media_player",
      playing ? "media_stop" : "media_play",
      { entity_id: maId }
    );
    this._panel.streamPlaying = !playing;
    this._setStreamPlayIcon();
  }

  /** "Pause"/resume the rooms on an amp's stream by powering its zones off/on
   *  (reuses the per-source power memory in `_toggleSourcePower`). The MA stream
   *  keeps running, so resuming is instant — unlike Stop, which tears it down. */
  _streamColumn(ampId) {
    return this._columns().find((c) => c.kind === "stream" && c.ampId === ampId);
  }

  _toggleStreamRooms(ampId) {
    const col = this._streamColumn(ampId);
    if (!col) return;
    const wasActive = this._activeZonesForColumn(col).length > 0;
    this._toggleSourcePower(col);
    // Optimistic: if rooms were on we just turned them off (show play), else on.
    this._setStreamPauseIcon(ampId, !wasActive);
  }

  /** Pause icon when the amp's rooms are on, play icon when off. Pass `playing`
   *  to set it optimistically; otherwise it's derived from current state. */
  _setStreamPauseIcon(ampId, playing) {
    const sheet = this.shadowRoot.getElementById("sheet");
    if (!sheet) return;
    const icon = sheet.querySelector('button[data-t="pauseplay"] ha-icon');
    if (!icon) return;
    if (playing === undefined) {
      const col = this._streamColumn(ampId);
      playing = col ? this._activeZonesForColumn(col).length > 0 : false;
    }
    icon.setAttribute("icon", playing ? "mdi:pause" : "mdi:play");
  }

  /** Reflect the optimistic play/stop state on the transport button. */
  _setStreamPlayIcon() {
    const sheet = this.shadowRoot.getElementById("sheet");
    if (!sheet || !this._panel) return;
    const icon = sheet.querySelector('button[data-t="play"] ha-icon');
    if (icon)
      icon.setAttribute(
        "icon",
        this._panel.streamPlaying === false ? "mdi:play" : "mdi:stop"
      );
  }

  /** Keep an open amp-stream popover in step with its MA player. */
  _refreshStreamPanel() {
    const sheet = this.shadowRoot.getElementById("sheet");
    if (!sheet) return;
    const st = this._panel.maId && this._hass.states[this._panel.maId];
    if (!st) return;
    const a = st.attributes || {};
    const np =
      !OFF_STATES.includes(st.state) && a.media_title
        ? {
            title: a.media_title,
            artist: a.media_artist || a.media_album_name || "",
            art: a.entity_picture || "",
          }
        : null;
    const npEl = sheet.querySelector(".nowplaying");
    if (npEl) {
      npEl.hidden = !np;
      if (np) {
        const art = npEl.querySelector(".np-art");
        if (art) {
          art.style.backgroundImage = np.art ? `url("${np.art}")` : "";
          art.classList.toggle("has-art", !!np.art);
        }
        npEl.querySelector(".np-title").textContent = np.title;
        npEl.querySelector(".np-artist").textContent = np.artist;
      }
    }
    const slider = sheet.querySelector(".slider");
    const volval = sheet.querySelector(".volval");
    const lvl = a.volume_level;
    if (slider && !this._panel.dragging && typeof lvl === "number") {
      const pct = Math.round(lvl * 100);
      slider.value = String(pct);
      if (volval) volval.textContent = `${pct}%`;
    }
    const muteIcon = sheet.querySelector(".mute ha-icon");
    if (muteIcon) {
      muteIcon.setAttribute(
        "icon",
        a.is_volume_muted ? "mdi:volume-off" : "mdi:volume-high"
      );
    }
    const feat = a.supported_features || 0;
    const setT = (t, ok) => {
      const b = sheet.querySelector(`button[data-t="${t}"]`);
      if (b) b.toggleAttribute("disabled", !ok);
    };
    setT("prev", !!(feat & SUPPORT_PREVIOUS_TRACK));
    setT("next", !!(feat & SUPPORT_NEXT_TRACK));
    setT("play", true);
    // A definite off/idle state is trustworthy (e.g. the stream ended or was
    // stopped elsewhere); a reported "playing" is NOT (this renderer reports
    // playing even when stopped/paused), so keep the optimistic flag otherwise.
    if (st.state && OFF_STATES.includes(st.state)) this._panel.streamPlaying = false;
    this._setStreamPlayIcon();
    this._setStreamPauseIcon(this._panel.ampId);
    const search = sheet.querySelector("axium-ma-search");
    if (search) search.hass = this._hass;
  }

  _scheduleVolume(zoneId, pct) {
    if (this._volTimer) clearTimeout(this._volTimer);
    this._volTimer = setTimeout(() => this._setVolume(zoneId, pct), 120);
  }

  _setVolume(zoneId, pct) {
    if (this._volTimer) {
      clearTimeout(this._volTimer);
      this._volTimer = null;
    }
    const maxv = axiumMaxVolume(this._hass, zoneId) / 100;
    this._hass.callService("media_player", "volume_set", {
      entity_id: zoneId,
      volume_level: Math.max(0, Math.min(maxv, pct / 100)),
    });
  }

  /** Step the volume of every room currently playing an amp's stream. */
  _streamVolume(ampId, dir) {
    const zones = this._zones().filter((z) => this._streamCellActive(z, ampId));
    if (zones.length)
      this._hass.callService(
        "media_player",
        dir > 0 ? "volume_up" : "volume_down",
        { entity_id: zones }
      );
  }

  _toggleMute(zoneId) {
    const st = this._hass.states[zoneId];
    if (!st) return;
    this._hass.callService("media_player", "volume_mute", {
      entity_id: zoneId,
      is_volume_muted: !st.attributes.is_volume_muted,
    });
  }

  /**
   * Toggle a zone on/off. Reachable from the popover so a zone can always be
   * turned off, even when its current source isn't shown as a matrix column.
   */
  _togglePower(zoneId) {
    const st = this._hass.states[zoneId];
    const on = st && !OFF_STATES.includes(st.state);
    if (on) {
      this._turnZoneOff(zoneId);
    } else {
      this._hass.callService("media_player", "turn_on", { entity_id: zoneId });
    }
  }

  /**
   * The amp-device name a zone belongs to (e.g. "Axium 1"), via the device tree:
   * a zone device nests under its amp device (`via_device`). Used to link a zone
   * to its amp's single Music Assistant stream by amp — NOT by the zone's own
   * name, which collides with unrelated devices (e.g. a TV also "Woonkamer").
   */
  _ampNameFor(zoneId) {
    const ent = (this._hass.entities || {})[zoneId];
    const devs = this._hass.devices || {};
    const zdev = ent && devs[ent.device_id];
    if (!zdev) return null;
    const amp = devs[zdev.via_device_id] || zdev;
    return (amp && (amp.name_by_user || amp.name)) || null;
  }

  /** The Music Assistant player whose name exactly equals `name` (the amp's
   *  stream). Exact match, so it never links to an unrelated same-named device. */
  _ampStreamPlayerByName(name) {
    const want = (name || "").trim().toLowerCase();
    if (!want) return null;
    const reg = (this._hass && this._hass.entities) || {};
    for (const id of Object.keys(this._hass.states)) {
      if (!id.startsWith("media_player.")) continue;
      const e = reg[id];
      if (!e || e.platform !== "music_assistant") continue;
      const st = this._hass.states[id];
      const fn = st && (st.attributes.friendly_name || "").trim().toLowerCase();
      if (fn && fn === want) return st;
    }
    return null;
  }

  /**
   * The Music Assistant player whose stream this zone hears — its OWN amp's
   * stream (streams are per-amp; a zone only ever plays its amp's Media Player).
   * The MA players should be renamed (in MA) to the amp device names ("Axium 1").
   */
  _ampStreamPlayerFor(zoneId) {
    return this._ampStreamPlayerByName(this._ampNameFor(zoneId));
  }

  /**
   * Whether a zone is currently hearing a given amp's stream (for stream-cell
   * highlighting). True when the zone is on Media Player and: it's the MASTER
   * column and the zone's own expansion amp isn't overriding, OR it's that
   * expansion's column and it is overriding (its MA player is playing). So a
   * zone lights up under exactly one stream column.
   */
  _streamCellActive(zoneId, ampId) {
    // Streams are per-amp: a stream cell only exists for the zone's own amp, so
    // the zone lights there whenever it's powered on and on the Media Player.
    const st = this._hass.states[zoneId];
    if (!st || OFF_STATES.includes(st.state)) return false;
    return this._currentSourceId(st) >= STREAM_SOURCE_MIN;
  }

  /**
   * Where transport/play controls should go for a zone: its amp's Music Assistant
   * stream when one is playing (so play/pause resumes it instead of the amp's
   * internal player restarting the track); otherwise the amp zone itself.
   */
  _playbackTarget(zoneId) {
    const ma = this._ampStreamPlayerFor(zoneId);
    if (ma && !OFF_STATES.includes(ma.state) && ma.attributes.media_title) {
      return ma.entity_id;
    }
    return zoneId;
  }

  /**
   * Turn a zone off — only powers off the amp zone, never the Music Assistant
   * stream. Each amp has ONE shared media stream (verified: its 8 per-amp DLNA
   * renderers all alias a single stream), so stopping it would silence every
   * other zone on that amp. Toggling a zone just adds/removes it from its amp's
   * still-running stream.
   */
  _turnZoneOff(zoneId) {
    this._hass.callService("media_player", "turn_off", { entity_id: zoneId });
  }

  /**
   * What's playing on a zone: prefer the amp zone's own now-playing (reported
   * for the internal Media Player source), else its amp's Music Assistant stream
   * (all zones on an amp share one stream). Returns null when nothing is playing.
   */
  _zoneNowPlaying(zoneId) {
    const pick = (st) => {
      if (!st || OFF_STATES.includes(st.state)) return null;
      const a = st.attributes || {};
      if (!a.media_title) return null;
      return {
        title: a.media_title,
        artist: a.media_artist || a.media_album_name || "",
        art: a.entity_picture || "",
      };
    };
    return pick(this._hass.states[zoneId]) || pick(this._ampStreamPlayerFor(zoneId));
  }

  /** Preset picker for one source column, in the popover. */
  _openPresetPanel(sourceId, anchorEl) {
    const sid = Number(sourceId);
    const sheet = this.shadowRoot.getElementById("sheet");
    const presets = this._presets();
    const srcName = (this._sources().find((s) => s.id === sid) || {}).name || "";
    const body = presets.length
      ? `<div class="preset-list">` +
        presets
          .map(
            (p, i) =>
              `<button class="preset-item" data-idx="${i}">` +
              `<ha-icon icon="mdi:speaker-multiple"></ha-icon><span></span></button>`
          )
          .join("") +
        `</div>`
      : `<div class="empty">No presets configured. Add them in the Axium options.</div>`;
    sheet.innerHTML = `
      <div class="sheet-head">
        <span class="sheet-title"></span>
        <button class="iconbtn close" title="Close"><ha-icon icon="mdi:close"></ha-icon></button>
      </div>
      ${body}
    `;
    sheet.querySelector(".sheet-title").textContent = srcName
      ? `${srcName} · preset`
      : "Preset";
    for (const b of sheet.querySelectorAll(".preset-item")) {
      b.querySelector("span").textContent = presets[Number(b.dataset.idx)].name;
      b.addEventListener("click", () => {
        this._applyPresetToSource(Number(b.dataset.idx), sid);
        this._closePanel();
      });
    }
    sheet.querySelector(".close").addEventListener("click", () => this._closePanel());
    this._panel = { type: "preset", sourceId: sid };
    this._showOverlay(anchorEl);
  }

  /** Keep an open popover (zone or amp stream) in step with live state. */
  _refreshPanel() {
    if (!this._panel) return;
    if (this._panel.type === "stream") {
      this._refreshStreamPanel();
      return;
    }
    if (this._panel.type !== "zone") return;
    const sheet = this.shadowRoot.getElementById("sheet");
    if (!sheet) return;
    const st = this._hass.states[this._panel.zoneId];
    if (!st) return;
    const slider = sheet.querySelector(".slider");
    const volval = sheet.querySelector(".volval");
    const lvl = st.attributes.volume_level;
    if (slider && !this._panel.dragging && typeof lvl === "number") {
      const pct = Math.round(lvl * 100);
      slider.value = String(pct);
      if (volval) volval.textContent = `${pct}%`;
    }
    // Grey out the range above the zone's max volume (zone panel only).
    axiumApplyVolCap(sheet.querySelector(".slidcap"), this._hass, this._panel.zoneId, "width");
    const muteIcon = sheet.querySelector(".mute ha-icon");
    if (muteIcon) {
      muteIcon.setAttribute(
        "icon",
        st.attributes.is_volume_muted ? "mdi:volume-off" : "mdi:volume-high"
      );
    }
    const powerBtn = sheet.querySelector(".power");
    if (powerBtn) powerBtn.classList.toggle("on", !OFF_STATES.includes(st.state));
    // Tone (EQ) sliders + loudness reflect their live entity values.
    for (const el of sheet.querySelectorAll(".toneslider")) {
      if (this._panel.toneDrag === el.dataset.eid) continue;
      const ts = this._hass.states[el.dataset.eid];
      if (ts && !OFF_STATES.includes(ts.state) && ts.state !== "") {
        el.value = ts.state;
        const v = el.parentElement.querySelector(".toneval");
        if (v) v.textContent = ts.state;
      }
    }
    for (const sw of sheet.querySelectorAll(".toneswitch")) {
      const ss = this._hass.states[sw.dataset.eid];
      sw.checked = !!ss && ss.state === "on";
    }
  }

  _update() {
    const cells = this.shadowRoot.querySelectorAll("button.cell");
    for (const cell of cells) {
      const zoneId = cell.dataset.zone;
      const sid = Number(cell.dataset.src);
      const st = this._hass.states[zoneId];
      const on = st && !OFF_STATES.includes(st.state);
      const sname = this._sourceNameFor(st, sid);
      // Stream (amp) cell: active only when the zone is actually hearing THIS
      // amp's stream — master when its own expansion isn't overriding, expansion
      // when it is — so a zone never lights up under both stream columns.
      const active = cell.dataset.amp
        ? this._streamCellActive(zoneId, cell.dataset.amp)
        : on && this._currentSourceId(st) === sid;
      const unavailable = !st || st.state === "unavailable" || sname == null;
      cell.classList.toggle("active", !!active);
      cell.classList.toggle("unavailable", !!unavailable);
      cell.title = active
        ? `${this._zoneName(zoneId)}: tap to turn off`
        : sname
        ? `${this._zoneName(zoneId)} → ${sname}`
        : "";
    }
    // Refresh header labels so source/zone renames appear without a rebuild — a
    // rename doesn't change the structural signature, so _build() isn't re-run.
    const srcNames = new Map(this._sources().map((s) => [String(s.id), s.name]));
    for (const h of this.shadowRoot.querySelectorAll(".colhead[data-src]")) {
      const name = srcNames.get(h.dataset.src);
      if (name != null) {
        h.title = name;
        const span = h.querySelector("span");
        if (span) span.textContent = name;
      }
    }
    for (const h of this.shadowRoot.querySelectorAll(".rowhead[data-zone]")) {
      const name = this._zoneName(h.dataset.zone);
      h.title = name;
      h.textContent = name;
    }
    // Per-source power toggle: lit when the source has any active zone. Compute
    // the zone list once and reuse it for every column (avoids re-sorting zones
    // per column, per tick).
    const colByKey = new Map(this._columns().map((c) => [this._colKey(c), c]));
    const zones = this._zones();
    for (const pw of this.shadowRoot.querySelectorAll(".srcpwr")) {
      const col = colByKey.get(pw.dataset.colkey);
      pw.classList.toggle("on", !!col && this._activeZonesForColumn(col, zones).length > 0);
    }
    const allpwr = this.shadowRoot.querySelector(".allpower");
    if (allpwr) {
      const anyOn = this._zones().some((z) => {
        const st = this._hass.states[z];
        return st && !OFF_STATES.includes(st.state);
      });
      allpwr.classList.toggle("on", anyOn);
    }
    this._refreshPanel();
  }
}

AxiumMatrixCard.styles = `
  ha-card { padding: 12px; position: relative; }
  .title { font-size: 1.1rem; font-weight: 600; margin-bottom: 8px; color: var(--primary-text-color); }
  .scroll { overflow-x: auto; }
  .matrix { display: grid; gap: 4px; align-items: stretch; min-width: min-content; }
  .corner { display: flex; align-items: center; justify-content: flex-start; }
  .allpower {
    display: inline-flex; align-items: center; justify-content: center;
    width: 34px; height: 34px; border-radius: 50%; cursor: pointer;
    border: 1px solid var(--divider-color); background: var(--card-background-color);
    color: var(--secondary-text-color);
  }
  .allpower:hover { border-color: var(--primary-color); color: var(--primary-color); }
  .allpower.on { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
  .allpower ha-icon { --mdc-icon-size: 20px; }
  .presetsel {
    font: inherit; padding: 6px 8px; border-radius: 8px; width: 100%;
    box-sizing: border-box; margin-bottom: 8px;
    border: 1px solid var(--divider-color);
    background: var(--secondary-background-color); color: var(--primary-text-color);
  }
  .colhead, .rowhead {
    font-size: 0.8rem; color: var(--secondary-text-color);
    display: flex; align-items: center; overflow: hidden;
    cursor: pointer;
  }
  .colhead:hover span, .rowhead:hover { color: var(--primary-color); }
  .colhead:focus-visible, .rowhead:focus-visible {
    outline: 2px solid var(--primary-color); outline-offset: 1px; border-radius: 4px;
  }
  .colhead { flex-direction: column; justify-content: flex-start; text-align: center; padding: 4px 2px 6px; gap: 10px; }
  .srcpwr {
    flex: 0 0 auto; display: inline-flex; align-items: center; justify-content: center;
    width: 30px; height: 30px; padding: 0; border-radius: 50%; cursor: pointer;
    border: 1px solid var(--divider-color); background: var(--card-background-color);
    color: var(--secondary-text-color); touch-action: manipulation;
  }
  .srcpwr:hover { border-color: var(--primary-color); color: var(--primary-color); }
  .srcpwr.on {
    background: var(--primary-color); border-color: var(--primary-color);
    color: var(--text-primary-color, #fff);
  }
  .srcpwr ha-icon { --mdc-icon-size: 18px; }
  /* Give the name its own clear tap zone, set apart from the power button. */
  .colhead > span { display: block; width: 100%; padding: 4px 0; }
  .colhead span, .rowhead {
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%;
  }
  .rowhead {
    font-size: 0.9rem; color: var(--primary-text-color); padding-right: 6px;
    user-select: none; -webkit-user-select: none; -webkit-touch-callout: none;
    touch-action: manipulation;
  }
  .cell {
    display: inline-flex; align-items: center; justify-content: center;
    min-height: 40px; border-radius: 8px;
    border: 1px solid var(--divider-color);
    background: var(--card-background-color); cursor: pointer;
    color: var(--primary-text-color);
    transition: background 0.15s, border-color 0.15s, transform 0.05s;
  }
  .cell:hover { border-color: var(--primary-color); }
  .cell:active { transform: scale(0.94); }
  .cell ha-icon { --mdc-icon-size: 18px; opacity: 0; transition: opacity 0.15s; }
  .cell.active {
    background: var(--primary-color); border-color: var(--primary-color);
    color: var(--text-primary-color, #fff);
  }
  .cell.active ha-icon { opacity: 1; }
  .cell.unavailable { opacity: 0.3; pointer-events: none; }
  /* Non-owned zone under an amp's stream column (that amp can't reach it). */
  .cell.blank { border: 1px dashed var(--divider-color); opacity: 0.25; }
  .colhead.stream span { font-weight: 600; }
  .browse {
    display: inline-flex; align-items: center; justify-content: center; gap: 8px;
    margin-top: 10px; padding: 10px; width: 100%; box-sizing: border-box;
    border: 1px solid var(--divider-color); border-radius: 10px; cursor: pointer;
    background: var(--secondary-background-color); color: var(--primary-text-color);
    font-size: 0.95rem;
  }
  .browse:hover { border-color: var(--primary-color); }
  .browse ha-icon { --mdc-icon-size: 20px; }
  .overlay {
    position: fixed; inset: 0; z-index: 9999;
    background: rgba(0, 0, 0, 0.5);
  }
  .overlay[hidden] { display: none; }
  /* Mobile: an almost-full-screen sheet, centered, with a backdrop margin to tap
     outside and close. Desktop (below) shrinks it and JS anchors it near the tap. */
  .sheet {
    position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%);
    width: 94%; max-width: 94%; max-height: 88vh;
    background: var(--card-background-color, var(--ha-card-background, #fff));
    border-radius: 16px; box-shadow: 0 8px 40px rgba(0, 0, 0, 0.45);
    box-sizing: border-box;
    padding: max(14px, env(safe-area-inset-top)) 16px 16px;
    display: flex; flex-direction: column; overflow-y: auto;
  }
  @media (min-width: 768px) {
    .sheet {
      width: 360px; max-width: 92%; max-height: 72vh; border-radius: 12px;
    }
  }
  .sheet-head {
    display: flex; align-items: center; justify-content: space-between;
    gap: 8px; margin-bottom: 10px;
  }
  .sheet-title {
    font-weight: 600; color: var(--primary-text-color);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .sheet-actions { display: inline-flex; align-items: center; gap: 2px; flex: 0 0 auto; }
  .iconbtn.power { width: 36px; height: 36px; color: var(--secondary-text-color); }
  .iconbtn.power.on { color: var(--primary-color); }
  .nowplaying {
    display: flex; align-items: center; gap: 10px;
    margin-bottom: 10px; padding-bottom: 10px;
    border-bottom: 1px solid var(--divider-color);
  }
  .nowplaying[hidden] { display: none; }
  .np-art {
    width: 40px; height: 40px; flex: 0 0 auto; border-radius: 6px;
    background: var(--secondary-background-color) center/cover no-repeat;
  }
  .np-art:not(.has-art) { display: none; }
  .np-meta { min-width: 0; }
  .np-title {
    font-size: 0.9rem; color: var(--primary-text-color);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .np-artist {
    font-size: 0.8rem; color: var(--secondary-text-color);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .volrow { display: flex; align-items: center; gap: 10px; }
  .slidwrap { position: relative; flex: 1 1 auto; min-width: 120px; display: flex; align-items: center; }
  .slider {
    flex: 1 1 auto; min-width: 0; height: 24px;
    accent-color: var(--primary-color); cursor: pointer;
  }
  /* Greyed-out region above the zone's max volume. */
  .slidcap {
    position: absolute; top: 0; bottom: 0; right: 0; width: 0;
    background: var(--divider-color); opacity: 0.5; border-radius: 4px;
    pointer-events: none;
  }
  .volval {
    width: 40px; text-align: right; font-size: 0.85rem;
    color: var(--secondary-text-color);
  }
  .tone { display: flex; flex-direction: column; gap: 8px; margin-top: 10px; }
  .tonerow { display: flex; align-items: center; gap: 10px; }
  .tonerow ha-icon { --mdc-icon-size: 20px; color: var(--secondary-text-color); }
  .tonelbl { width: 66px; font-size: 0.9rem; color: var(--primary-text-color); }
  .toneslider {
    flex: 1 1 auto; min-width: 90px; height: 24px;
    accent-color: var(--primary-color); cursor: pointer;
  }
  .toneval {
    width: 34px; text-align: right; font-size: 0.85rem;
    color: var(--secondary-text-color);
  }
  .toneswitch { width: 40px; height: 22px; accent-color: var(--primary-color); cursor: pointer; }
  .transport {
    display: flex; align-items: center; justify-content: center;
    gap: 8px; margin-top: 8px;
  }
  .iconbtn {
    display: inline-flex; align-items: center; justify-content: center;
    width: 44px; height: 44px; border-radius: 50%;
    border: none; background: none; cursor: pointer;
    color: var(--primary-text-color);
    transition: background 0.15s, transform 0.05s;
  }
  .iconbtn:hover { background: var(--secondary-background-color); }
  .iconbtn:active { transform: scale(0.92); }
  .iconbtn[disabled] { opacity: 0.3; pointer-events: none; }
  .iconbtn.close { width: 36px; height: 36px; color: var(--secondary-text-color); }
  .iconbtn.play { color: var(--primary-color); }
  .iconbtn.play ha-icon { --mdc-icon-size: 30px; }
  .preset-list { display: flex; flex-direction: column; gap: 6px; }
  .preset-item {
    display: flex; align-items: center; gap: 8px;
    padding: 8px 10px; border-radius: 8px;
    border: 1px solid var(--divider-color);
    background: var(--card-background-color);
    color: var(--primary-text-color);
    font: inherit; text-align: left; cursor: pointer;
  }
  .preset-item:hover { border-color: var(--primary-color); }
  .preset-item ha-icon { --mdc-icon-size: 20px; color: var(--primary-color); }
  .empty { color: var(--secondary-text-color); font-size: 0.9rem; padding: 4px 0; }
`;

/** Visual (UI) editor for the matrix card — amplifier, zones, sources, name. */
class AxiumMatrixCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  async _ensureHaForm() {
    if (customElements.get("ha-form")) return;
    try {
      const helpers = await window.loadCardHelpers();
      const card = await helpers.createCardElement({ type: "entities", entities: [] });
      await card.constructor.getConfigElement();
    } catch (err) {
      /* ha-form will still upgrade once available */
    }
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.addEventListener("value-changed", (ev) => this._changed(ev));
      this.appendChild(this._form);
      this._ensureHaForm();
    }
    const hubs = axiumHubs(this._hass);
    const hubOptions = hubs.map((h) => ({ value: h.id, label: h.name }));
    const data = { ...this._config };
    if (!data.hub && hubs.length) data.hub = hubs[0].id;

    // Source columns available on the selected hub. Stream sources are per-amp,
    // so they get an amp-scoped value ("stream:<ampId>") — they are separate
    // filter entries ("Axium 1", "Axium 2"), not one combined "Media Player".
    const sourceOptions = axiumSourceChoices(this._hass)
      .filter((c) => c.hub === data.hub)
      .map((c) => ({
        value: c.ampId ? `stream:${c.ampId}` : String(c.id),
        label: c.name,
      }));
    // Zones in physical zone-number order (a sorted select, not the entity
    // picker, so the config lists them 1..16 like the cards do).
    const zoneOptions = axiumZoneSelectOptions(this._hass, data.hub);

    this._form.hass = this._hass;
    this._form.data = data;
    this._form.schema = [
      {
        name: "hub",
        selector: hubOptions.length
          ? { select: { mode: "dropdown", options: hubOptions } }
          : { text: {} },
      },
      {
        name: "zones",
        selector: zoneOptions.length
          ? { select: { multiple: true, mode: "list", options: zoneOptions } }
          : { entity: { integration: "axium", domain: "media_player", multiple: true } },
      },
      {
        name: "sources",
        selector: sourceOptions.length
          ? { select: { multiple: true, options: sourceOptions } }
          : { text: {} },
      },
      { name: "name", selector: { text: {} } },
    ];
    this._form.computeLabel = (s) =>
      ({
        hub: "Amplifier",
        zones: "Zones to show (empty = all)",
        sources: "Sources to show (empty = all)",
        name: "Card name (optional)",
      }[s.name] || s.name);
  }

  _changed(ev) {
    ev.stopPropagation();
    this._config = { ...ev.detail.value };
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: this._config },
        bubbles: true,
        composed: true,
      })
    );
    this._render(); // hub change updates the available source columns
  }
}

// Axium sensor entity_ids on a hub carrying a given axium_kind attribute.
function axiumKindSensors(hass, hubId, kind) {
  const states = (hass && hass.states) || {};
  const reg = (hass && hass.entities) || {};
  return Object.keys(states)
    .filter((id) => id.startsWith("sensor."))
    .filter((id) => reg[id] && reg[id].platform === "axium")
    .filter((id) => states[id].attributes.axium_kind === kind)
    .filter((id) => !hubId || entityHub(hass, id) === hubId);
}

// Human "time left" from an ISO timestamp to now (e.g. "7h 12m", "45s").
function axiumCountdown(iso) {
  const target = Date.parse(iso);
  if (isNaN(target)) return null;
  let s = Math.round((target - Date.now()) / 1000);
  if (s <= 0) return "now";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  s = s % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

const _DAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function axiumDaysLabel(days) {
  if (!Array.isArray(days) || days.length === 0 || days.length === 7) {
    return "Every day";
  }
  const set = [...days].sort();
  if (set.join() === "0,1,2,3,4") return "Weekdays";
  if (set.join() === "5,6") return "Weekends";
  return set.map((d) => _DAY_ABBR[d]).join(", ");
}

/**
 * Axium Alarms Card — add, remove, enable/disable and edit alarms inline.
 *
 * Reads the per-alarm timestamp sensors (for the live countdown and current
 * schedule) and writes changes through the axium.set_alarm / axium.remove_alarm
 * services, so everything stays in sync and is also automation-usable.
 */
class AxiumAlarmsCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._timer = null;
    this._rowEls = {};
    this._sig = null;
    this._addOpen = false;
  }
  setConfig(config) {
    this._config = config || {};
    this.shadowRoot.innerHTML = "";
    this._sig = null;
  }
  set hass(hass) {
    this._hass = hass;
    if (hass && this._config) this._render();
    // Keep an open wake-song picker's hass current (its subtree isn't rebuilt
    // by _refresh).
    const s = this.shadowRoot.querySelector("axium-ma-search");
    if (s) s.hass = hass;
  }
  getCardSize() {
    return 3;
  }
  static getConfigElement() {
    return document.createElement("axium-matrix-card-editor");
  }
  static getStubConfig(hass) {
    const hubs = axiumHubs(hass);
    return hubs.length ? { hub: hubs[0].id } : {};
  }
  connectedCallback() {
    this._timer = setInterval(() => this._tickCountdowns(), 1000);
  }
  disconnectedCallback() {
    if (this._timer) clearInterval(this._timer);
    this._timer = null;
  }
  _hub() {
    return this._config.hub || (axiumHubs(this._hass)[0] || {}).id;
  }
  _alarmIds() {
    return axiumKindSensors(this._hass, this._hub(), "alarm").sort((a, b) =>
      (this._hass.states[a].attributes.alarm_name || "").localeCompare(
        this._hass.states[b].attributes.alarm_name || ""
      )
    );
  }
  _svc(service, data) {
    this._hass.callService("axium", service, { hub: this._hub(), ...data });
  }
  // Zones offered in the Add form (config `zones` whitelist, else all), ordered
  // by physical zone number (1..16+).
  _addZones() {
    const all = axiumMediaPlayers(this._hass, this._hub());
    const pick = this._config.zones;
    const list =
      Array.isArray(pick) && pick.length
        ? all.filter((id) => pick.includes(id))
        : all;
    return axiumSortZones(this._hass, list);
  }
  // Sources offered in the Add form (config `sources` whitelist, else all).
  _sources() {
    const map = new Map();
    for (const z of axiumMediaPlayers(this._hass, this._hub())) {
      const a = this._hass.states[z].attributes;
      const ids = a.source_ids;
      const names = a.source_list;
      if (Array.isArray(ids) && Array.isArray(names)) {
        ids.forEach((sid, i) => {
          if (!map.has(sid)) map.set(sid, names[i]);
        });
      }
    }
    let list = [...map.entries()].map(([id, name]) => ({ id, name }));
    const pick = this._config.sources;
    if (Array.isArray(pick) && pick.length) {
      const wanted = new Set(pick.map(Number));
      list = list.filter((s) => wanted.has(s.id));
    }
    return list.sort((a, b) => String(a.name).localeCompare(String(b.name)));
  }

  _render() {
    const ids = this._alarmIds();
    const sig = ids.join(",");
    if (sig !== this._sig) {
      this._sig = sig;
      this._build(ids);
    }
    this._refresh(ids);
  }

  _build(ids) {
    this._rowEls = {};
    const title = this._config.name || "Alarms";
    this.shadowRoot.innerHTML = `
      <style>${AxiumAlarmsCard.styles}</style>
      <ha-card>
        <div class="title">${title}</div>
        <div class="rows" id="rows"></div>
        <div class="addbar"><button class="link" id="addtoggle">+ Add alarm</button></div>
        <div class="addform" id="addform" hidden></div>
      </ha-card>`;
    const rows = this.shadowRoot.getElementById("rows");
    if (!ids.length) rows.innerHTML = `<div class="empty">No alarms yet.</div>`;
    for (const id of ids) {
      const name = this._hass.states[id].attributes.alarm_name;
      const row = document.createElement("div");
      row.className = "row";
      row.innerHTML = `
        <label class="tog"><input type="checkbox" class="en"><span class="track"></span></label>
        <div class="mid">
          <div class="n"></div>
          <div class="sub"><input type="time" class="time"><span class="days"></span></div>
          <div class="zn"></div>
          <div class="src"></div>
        </div>
        <div class="cd" data-id="${id}"></div>
        <button class="x" title="Remove">&#10005;</button>`;
      row.querySelector(".en").addEventListener("change", (e) =>
        this._svc("set_alarm", { name, enabled: e.target.checked })
      );
      row.querySelector(".time").addEventListener("change", (e) => {
        if (e.target.value) this._svc("set_alarm", { name, time: e.target.value });
      });
      row.querySelector(".x").addEventListener("click", () =>
        this._svc("remove_alarm", { name })
      );
      const days = row.querySelector(".days");
      _DAY_ABBR.forEach((lbl, idx) => {
        const c = document.createElement("button");
        c.className = "daychip";
        c.textContent = lbl[0];
        c.dataset.d = idx;
        c.title = lbl;
        c.addEventListener("click", () => this._toggleDay(name, idx));
        days.appendChild(c);
      });
      rows.appendChild(row);
      this._rowEls[id] = row;
    }
    this.shadowRoot
      .getElementById("addtoggle")
      .addEventListener("click", () => this._toggleAdd());
    this._buildAddForm();
  }

  _toggleDay(name, idx) {
    const id = this._alarmIds().find(
      (i) => this._hass.states[i].attributes.alarm_name === name
    );
    const cur = new Set(
      (id && this._hass.states[id].attributes.alarm_days) || []
    );
    if (cur.has(idx)) cur.delete(idx);
    else cur.add(idx);
    this._svc("set_alarm", { name, days: [...cur].sort((a, b) => a - b) });
  }

  _refresh(ids) {
    for (const id of ids) {
      const row = this._rowEls[id];
      if (!row) continue;
      const a = this._hass.states[id].attributes;
      row.querySelector(".n").textContent = a.alarm_name || id;
      row.querySelector(".en").checked = a.alarm_enabled !== false;
      const t = row.querySelector(".time");
      if (this.shadowRoot.activeElement !== t && a.alarm_time) t.value = a.alarm_time;
      const set = new Set(a.alarm_days || []);
      const everyDay = !a.alarm_days || a.alarm_days.length === 0;
      for (const chip of row.querySelectorAll(".daychip")) {
        chip.classList.toggle("on", everyDay || set.has(Number(chip.dataset.d)));
      }
      const zn = row.querySelector(".zn");
      const zones = Array.isArray(a.alarm_zones) ? a.alarm_zones : [];
      zn.textContent = zones.length
        ? zones
            .map((z) => {
              const st = this._hass.states[z];
              return (st && st.attributes.friendly_name) || z;
            })
            .join(", ")
        : "";
      row.querySelector(".src").textContent = this._alarmSourceLabel(a);
    }
    this._tickCountdowns();
  }

  _tickCountdowns() {
    if (!this._hass) return;
    for (const el of this.shadowRoot.querySelectorAll(".cd[data-id]")) {
      const st = this._hass.states[el.dataset.id];
      if (!st) {
        el.textContent = "";
        continue;
      }
      const a = st.attributes;
      if (a.alarm_enabled === false) el.innerHTML = `<span class="off">Off</span>`;
      else if (!a.armed) el.innerHTML = `<span class="off">Disarmed</span>`;
      else {
        const left = axiumCountdown(st.state);
        el.innerHTML = left ? `<span class="in">in ${left}</span>` : "";
      }
    }
  }

  _toggleAdd() {
    this._addOpen = !this._addOpen;
    this.shadowRoot.getElementById("addform").hidden = !this._addOpen;
  }

  /** Amp stream options for the alarm source select: each amp device by name
   *  (Axium 1 / Axium 2) with its Music Assistant stream player, if found. */
  _ampStreams() {
    const hubId = this._hub();
    const devs = this._hass.devices || {};
    const out = [];
    for (const d of Object.values(devs)) {
      // The primary amp is its own "…_amp_primary" device since the hub/amp
      // split (NOT the bare hub id, which is now an empty logical container with
      // no MA player); expansions are "…_unit_<uid>".
      const isAmp = (d.identifiers || []).some(
        (t) =>
          t[0] === "axium" &&
          (t[1] === `${hubId}_amp_primary` ||
            String(t[1]).startsWith(`${hubId}_unit_`))
      );
      if (!isAmp) continue;
      const name = d.name_by_user || d.name;
      if (name) out.push({ name, player: this._maByName(name) || "" });
    }
    return out;
  }

  _maByName(name) {
    const want = (name || "").trim().toLowerCase();
    if (!want) return null;
    const reg = this._hass.entities || {};
    for (const id of Object.keys(this._hass.states)) {
      if (!id.startsWith("media_player.")) continue;
      const e = reg[id];
      if (!e || e.platform !== "music_assistant") continue;
      const fn = (this._hass.states[id].attributes.friendly_name || "").trim().toLowerCase();
      if (fn === want) return id;
    }
    return null;
  }

  _buildAddForm() {
    const form = this.shadowRoot.getElementById("addform");
    const sources = this._sources();
    const analog = sources.filter((s) => s.id < STREAM_SOURCE_MIN);
    const mediaSid = (sources.find((s) => s.id >= STREAM_SOURCE_MIN) || {}).id;
    const streams = mediaSid != null ? this._ampStreams() : [];
    form.dataset.mediaSid = mediaSid != null ? String(mediaSid) : "";
    form.innerHTML = `
      <div class="af-row2">
        <label class="af-field af-name-field">
          <span class="af-label">Name</span>
          <input type="text" class="f-name" placeholder="e.g. Weekday wake-up">
        </label>
        <label class="af-field">
          <span class="af-label">Time</span>
          <input type="time" class="f-time" value="07:00">
        </label>
      </div>
      <div class="af-field">
        <span class="af-label">Repeat</span>
        <div class="af-quickdays">
          <button type="button" class="qd" data-days="everyday">Every day</button>
          <button type="button" class="qd" data-days="weekdays">Weekdays</button>
          <button type="button" class="qd" data-days="weekend">Weekend</button>
        </div>
        <div class="chips f-days"></div>
      </div>
      <div class="af-field">
        <span class="af-label">Rooms</span>
        <div class="chips f-zones"></div>
      </div>
      <div class="af-field">
        <span class="af-label">Wake to</span>
        <select class="f-source">${analog
          .map((s) => `<option value="src:${s.id}">${escHtml(s.name)}</option>`)
          .join("")}${streams
          .map((a) => `<option value="stream:${escHtml(a.player)}">${escHtml(a.name)}</option>`)
          .join("")}</select>
        <div class="f-media">
          <button type="button" class="mediabtn link">♪ Pick a Music Assistant track…</button>
          <span class="mediasel"></span>
          <div class="mediabrowse" hidden></div>
        </div>
      </div>
      <div class="af-field">
        <span class="af-label">Volume <span class="volval">30%</span></span>
        <input type="range" class="f-volume" min="0" max="100" value="30">
      </div>
      <div class="af-field">
        <span class="af-label">Auto turn-off</span>
        <div class="af-dur-row">
          <input type="number" class="f-duration" min="0" max="1440" step="1" value="0" inputmode="numeric">
          <span class="af-unit">min</span>
          <span class="durhint">0 = stay on</span>
        </div>
      </div>
      <div class="af-actions">
        <button type="button" class="af-cancel link">Cancel</button>
        <button type="button" class="addbtn">Add alarm</button>
      </div>`;
    form.querySelector(".mediabtn").addEventListener("click", () =>
      this._openMediaBrowse(form)
    );
    form.querySelector(".af-cancel").addEventListener("click", () =>
      this._toggleAdd()
    );
    const daysEl = form.querySelector(".f-days");
    _DAY_ABBR.forEach((lbl, idx) => {
      const c = document.createElement("button");
      c.type = "button";
      c.className = "daychip on";
      c.textContent = lbl.slice(0, 2);
      c.dataset.d = idx;
      c.title = lbl;
      c.addEventListener("click", () => c.classList.toggle("on"));
      daysEl.appendChild(c);
    });
    // Quick day presets (0=Mon..6=Sun).
    const dayGroups = {
      everyday: new Set([0, 1, 2, 3, 4, 5, 6]),
      weekdays: new Set([0, 1, 2, 3, 4]),
      weekend: new Set([5, 6]),
    };
    for (const q of form.querySelectorAll(".qd"))
      q.addEventListener("click", () => {
        const want = dayGroups[q.dataset.days];
        for (const c of daysEl.querySelectorAll(".daychip"))
          c.classList.toggle("on", want.has(Number(c.dataset.d)));
      });
    const zonesEl = form.querySelector(".f-zones");
    for (const z of this._addZones()) {
      const st = this._hass.states[z];
      const c = document.createElement("button");
      c.type = "button";
      c.className = "zonechip";
      c.textContent = (st && st.attributes.friendly_name) || z;
      c.dataset.z = z;
      c.addEventListener("click", () => c.classList.toggle("on"));
      zonesEl.appendChild(c);
    }
    const vol = form.querySelector(".f-volume");
    const volval = form.querySelector(".volval");
    vol.addEventListener("input", () => (volval.textContent = `${vol.value}%`));
    form.querySelector(".addbtn").addEventListener("click", () =>
      this._submitAdd(form)
    );
  }

  _submitAdd(form) {
    const nameEl = form.querySelector(".f-name");
    const name = nameEl.value.trim();
    if (!name) {
      nameEl.focus();
      return;
    }
    const zones = [...form.querySelectorAll(".f-zones .zonechip.on")].map(
      (c) => c.dataset.z
    );
    if (!zones.length) return;
    const days = [...form.querySelectorAll(".f-days .daychip.on")].map((c) =>
      Number(c.dataset.d)
    );
    const sv = form.querySelector(".f-source").value || "";
    let source = 0;
    let mediaPlayer = "";
    if (sv.startsWith("stream:")) {
      source = Number(form.dataset.mediaSid || 0);
      mediaPlayer = sv.slice(7);
    } else if (sv.startsWith("src:")) {
      source = Number(sv.slice(4));
    }
    this._svc("set_alarm", {
      name,
      time: form.querySelector(".f-time").value || "07:00",
      days,
      zones,
      source,
      volume: Number(form.querySelector('input[type="range"]').value),
      enabled: true,
      duration: Math.max(
        0,
        Math.round(Number(form.querySelector(".f-duration").value) || 0)
      ),
      media: form.dataset.media || "",
      media_type: form.dataset.mediaType || "",
      media_title: form.dataset.mediaTitle || "",
      media_player: mediaPlayer,
    });
    nameEl.value = "";
    this._toggleAdd();
  }

  /** The primary (master) amp's display name — since the hub/amp split this is the
   *  "…_amp_primary" device, NOT the hub. Wake media plays on this amp's stream. */
  _primaryAmpName() {
    const amps = axiumAmps(this._hass, this._hub());
    const master = amps.find((a) => a.master) || amps[0];
    return (master && master.name) || "";
  }

  /** The Music Assistant player named after the primary (master) amp — the stream a
   *  wake playlist plays on. Null until the MA player is renamed to match. */
  _masterStreamPlayer() {
    return this._maByName(this._primaryAmpName());
  }

  /** One-line "what this alarm plays": a wake song (with its amp stream) or the
   *  configured source name — never the raw protocol byte or content id. */
  _alarmSourceLabel(a) {
    let label;
    if (a.alarm_media) {
      const title = a.alarm_media_title || "Music Assistant";
      const mp = a.alarm_media_player || "";
      let amp = "";
      if (mp && this._hass.states[mp])
        amp = this._hass.states[mp].attributes.friendly_name || "";
      if (!amp) amp = this._primaryAmpName();
      label = "♪ " + title + (amp ? " · " + amp : "");
    } else {
      const s = (this._sources() || []).find((x) => x.id === a.alarm_source);
      label = s ? s.name : "";
    }
    const dur = Number(a.alarm_duration) || 0;
    if (dur > 0) label = (label ? label + " · " : "") + `off after ${dur}m`;
    return label;
  }

  _openMediaBrowse(form) {
    const box = form.querySelector(".mediabrowse");
    const player = this._masterStreamPlayer();
    if (!player) {
      box.hidden = false;
      box.innerHTML = `<div class="empty">No Music Assistant stream player found — rename the amp's MA player to the amp's name first.</div>`;
      return;
    }
    box.hidden = false;
    // Let the component own its scrolling (its results list scrolls internally).
    box.style.maxHeight = "none";
    box.style.overflow = "visible";
    // The SAME shared search component as the matrix stream panel, in "pick"
    // mode: tapping a result stores it as the wake song (`pick` event) rather
    // than playing it. Opens on the library root; searches auto-run ~1s after
    // typing stops.
    box.innerHTML = `<axium-ma-search style="max-height:340px;"></axium-ma-search>`;
    const search = box.querySelector("axium-ma-search");
    search.mode = "pick";
    search.startBrowse = true;
    search.hass = this._hass;
    search.player = player;
    search.addEventListener("pick", (ev) => this._pickMedia(form, ev.detail));
  }

  _pickMedia(form, ch) {
    if (!ch) return;
    form.dataset.media = ch.media_content_id;
    form.dataset.mediaType = ch.media_content_type || "playlist";
    form.dataset.mediaTitle = ch.title || "";
    form.querySelector(".mediasel").textContent = "♪ " + ch.title;
    form.querySelector(".mediabrowse").hidden = true;
    form.querySelector(".mediabtn").textContent = "♪ Change music…";
  }
}

AxiumAlarmsCard.styles = `
  ha-card { padding: 12px 16px; position: relative; }
  .title { font-size: 1.1rem; font-weight: 600; margin-bottom: 8px; color: var(--primary-text-color); }
  .empty { color: var(--secondary-text-color); padding: 4px 0; }
  .f-media { display: flex; flex-direction: column; gap: 4px; margin: 4px 0; }
  .mediabtn { align-self: flex-start; }
  .mediasel { color: var(--primary-color); font-size: 0.9rem; }
  .mediabrowse {
    border: 1px solid var(--divider-color); border-radius: 8px; padding: 6px;
    background: var(--secondary-background-color);
  }
  .rows { display: flex; flex-direction: column; gap: 10px; }
  .row { display: flex; align-items: center; gap: 10px; }
  .mid { flex: 1 1 auto; min-width: 0; }
  .n { font-weight: 600; color: var(--primary-text-color); }
  .sub { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 2px; }
  .zn { font-size: 0.78rem; color: var(--secondary-text-color); margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .src { font-size: 0.78rem; color: var(--primary-color); margin-top: 1px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .src:empty { display: none; }
  .cd { flex: 0 0 auto; text-align: right; font-size: 0.85rem; }
  .in { font-weight: 600; color: var(--primary-color); }
  .off { color: var(--secondary-text-color); }
  input[type="time"] {
    font: inherit; font-size: 0.85rem; padding: 2px 4px; border-radius: 6px;
    border: 1px solid var(--divider-color);
    background: var(--card-background-color); color: var(--primary-text-color);
  }
  .days { display: inline-flex; gap: 2px; }
  .daychip {
    min-width: 40px; padding: 7px 0; text-align: center; border-radius: 10px;
    border: 1px solid var(--divider-color); background: none; cursor: pointer;
    font: inherit; font-size: 0.82rem; color: var(--primary-text-color);
  }
  .daychip.on { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
  .daychip:hover, .qd:hover { border-color: var(--primary-color); }
  .x { border: none; background: none; cursor: pointer; color: var(--secondary-text-color); font-size: 0.9rem; flex: 0 0 auto; }
  .x:hover { color: var(--error-color); }
  .tog { position: relative; display: inline-block; width: 36px; height: 20px; flex: 0 0 auto; }
  .tog input { opacity: 0; width: 0; height: 0; }
  .track { position: absolute; inset: 0; border-radius: 20px; background: var(--divider-color); transition: background 0.15s; }
  .track::before { content: ""; position: absolute; width: 16px; height: 16px; left: 2px; top: 2px; border-radius: 50%; background: #fff; transition: transform 0.15s; }
  .tog input:checked + .track { background: var(--primary-color); }
  .tog input:checked + .track::before { transform: translateX(16px); }
  .addbar { margin-top: 10px; }
  .link { border: none; background: none; color: var(--primary-color); cursor: pointer; font: inherit; padding: 0; }
  .addform { display: flex; flex-direction: column; gap: 16px; margin-top: 10px; padding-top: 14px; border-top: 1px solid var(--divider-color); }
  .addform[hidden] { display: none; }
  .af-field { display: flex; flex-direction: column; gap: 7px; }
  .af-row2 { display: grid; grid-template-columns: 1fr 132px; gap: 12px; align-items: end; }
  .af-label {
    font-size: 0.72rem; font-weight: 600; letter-spacing: 0.04em;
    text-transform: uppercase; color: var(--secondary-text-color);
  }
  .af-label .volval { color: var(--primary-color); font-weight: 700; letter-spacing: 0; }
  .addform input[type="text"], .addform input[type="time"], .addform select, .f-duration {
    font: inherit; padding: 9px 10px; border-radius: 8px; box-sizing: border-box;
    border: 1px solid var(--divider-color);
    background: var(--card-background-color); color: var(--primary-text-color);
  }
  .addform input[type="text"], .addform input[type="time"], .addform select { width: 100%; }
  .addform input:focus, .addform select:focus { outline: none; border-color: var(--primary-color); }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .zonechip {
    padding: 7px 12px; border-radius: 16px; border: 1px solid var(--divider-color);
    background: none; cursor: pointer; font: inherit; font-size: 0.85rem;
    color: var(--primary-text-color);
  }
  .zonechip:hover { border-color: var(--primary-color); }
  .zonechip.on { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
  .af-quickdays { display: flex; flex-wrap: wrap; gap: 6px; }
  .qd {
    padding: 4px 11px; border-radius: 14px; border: 1px solid var(--divider-color);
    background: none; cursor: pointer; font: inherit; font-size: 0.78rem;
    color: var(--secondary-text-color);
  }
  .f-volume { width: 100%; height: 26px; accent-color: var(--primary-color); cursor: pointer; }
  .af-dur-row { display: flex; align-items: center; gap: 8px; }
  .f-duration { width: 84px; }
  .af-unit { color: var(--primary-text-color); font-size: 0.9rem; }
  .durhint { color: var(--secondary-text-color); font-size: 0.8rem; }
  .af-actions { display: flex; justify-content: flex-end; align-items: center; gap: 16px; margin-top: 2px; }
  .addbtn {
    padding: 10px 22px; border-radius: 10px; border: none;
    background: var(--primary-color); color: var(--text-primary-color, #fff);
    cursor: pointer; font: inherit; font-weight: 600;
  }
  .addbtn:hover { filter: brightness(1.08); }
  .quick { display: inline-flex; gap: 4px; margin-top: 4px; }
  .q, .custom {
    padding: 3px 10px; border-radius: 14px; border: 1px solid var(--divider-color);
    background: none; cursor: pointer; font: inherit; font-size: 0.8rem;
    color: var(--primary-text-color);
  }
  .custom { border-style: dashed; }
  .q:hover, .custom:hover { border-color: var(--primary-color); }
  .overlay {
    position: absolute; inset: 0; z-index: 5; display: flex;
    align-items: center; justify-content: center;
    background: rgba(0, 0, 0, 0.45); border-radius: var(--ha-card-border-radius, 12px);
  }
  .overlay[hidden] { display: none; }
  .sheet {
    width: min(320px, 92%); box-sizing: border-box; padding: 16px;
    border-radius: 14px; background: var(--card-background-color, #fff);
    box-shadow: 0 8px 30px rgba(0, 0, 0, 0.35);
  }
  .sheet-head {
    display: flex; align-items: center; justify-content: space-between;
    gap: 8px; margin-bottom: 12px;
  }
  .sheet-title { font-weight: 600; color: var(--primary-text-color); }
  .iconbtn {
    background: none; border: none; cursor: pointer; font: inherit;
    color: var(--secondary-text-color); padding: 2px 6px; border-radius: 8px;
  }
  .iconbtn:hover { color: var(--primary-color); }
  .cust-row { display: flex; align-items: baseline; gap: 8px; margin-bottom: 10px; }
  .cust-input {
    flex: 1 1 auto; width: 100%; box-sizing: border-box; font: inherit;
    font-size: 1.4rem; padding: 8px 10px; border-radius: 10px;
    border: 1px solid var(--divider-color);
    background: var(--secondary-background-color); color: var(--primary-text-color);
  }
  .cust-unit { color: var(--secondary-text-color); }
  .cust-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px; }
  .cust-actions { display: flex; justify-content: flex-end; gap: 8px; }
  .cust-cancel, .cust-set {
    font: inherit; padding: 8px 14px; border-radius: 10px; cursor: pointer;
    border: 1px solid var(--divider-color); background: none;
    color: var(--primary-text-color);
  }
  .cust-set {
    border-color: var(--primary-color); background: var(--primary-color);
    color: var(--text-primary-color, #fff);
  }
`;

/**
 * Axium Sleep Timers Card — start, adjust and cancel a per-zone sleep timer,
 * with the live time left. Writes via the zone's sleep-timer number entity.
 */
class AxiumSleepCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._timer = null;
    this._rowEls = {};
    this._sig = null;
  }
  setConfig(config) {
    this._config = config || {};
    this.shadowRoot.innerHTML = "";
    this._sig = null;
  }
  set hass(hass) {
    this._hass = hass;
    if (hass && this._config) this._render();
  }
  getCardSize() {
    return 3;
  }
  static getConfigElement() {
    return document.createElement("axium-sleep-card-editor");
  }
  static getStubConfig(hass) {
    const hubs = axiumHubs(hass);
    return hubs.length ? { hub: hubs[0].id } : {};
  }
  connectedCallback() {
    this._timer = setInterval(() => this._tick(), 1000);
  }
  disconnectedCallback() {
    if (this._timer) clearInterval(this._timer);
    this._timer = null;
  }
  _hub() {
    return this._config.hub || (axiumHubs(this._hass)[0] || {}).id;
  }
  // Which sections to show: any of "all" | "zones" | "presets" (default all).
  _sections() {
    const s = this._config.sections;
    return Array.isArray(s) && s.length ? s : ["all", "zones", "presets"];
  }
  _device(id) {
    const reg = (this._hass && this._hass.entities) || {};
    return reg[id] && reg[id].device_id;
  }
  _sleepNumbers() {
    const states = this._hass.states || {};
    const reg = this._hass.entities || {};
    return Object.keys(states)
      .filter((id) => id.startsWith("number."))
      .filter((id) => reg[id] && reg[id].platform === "axium")
      .filter((id) => states[id].attributes.axium_kind === "sleep_timer")
      .filter((id) => !this._hub() || entityHub(this._hass, id) === this._hub());
  }
  _allNumberId() {
    return this._sleepNumbers().find(
      (id) => this._hass.states[id].attributes.sleep_all
    );
  }
  _zoneNumberIds() {
    const pick = this._config.zones;
    const wl = Array.isArray(pick) && pick.length ? new Set(pick) : null;
    return this._sleepNumbers()
      .filter((id) => !this._hass.states[id].attributes.sleep_all)
      .filter((id) => !wl || wl.has(this._numberZone(id)))
      .sort(
        (a, b) =>
          this._sleepZoneNum(a) - this._sleepZoneNum(b) ||
          this._zoneName(a).localeCompare(this._zoneName(b))
      );
  }

  // Physical zone number from a sleep-timer number's entity id (…_zone_<n>_…).
  _sleepZoneNum(id) {
    const m = /_zone_(\d+)/.exec(id);
    return m ? Number(m[1]) : 9999;
  }

  // The zone media_player entity id sharing a sleep-timer number's device.
  _numberZone(numId) {
    const reg = (this._hass && this._hass.entities) || {};
    const dev = this._device(numId);
    if (!dev) return null;
    for (const eid of Object.keys(reg)) {
      if (
        eid.startsWith("media_player.") &&
        reg[eid].device_id === dev &&
        reg[eid].platform === "axium"
      )
        return eid;
    }
    return null;
  }
  _presets() {
    for (const id of axiumMediaPlayers(this._hass, this._hub())) {
      const p = this._hass.states[id].attributes.axium_presets;
      if (Array.isArray(p)) return p;
    }
    return [];
  }
  _zoneName(numId) {
    const st0 = this._hass.states[numId];
    if (st0 && st0.attributes.sleep_all) return "All zones";
    const reg = (this._hass && this._hass.entities) || {};
    const dev = this._device(numId);
    if (dev) {
      for (const eid of Object.keys(reg)) {
        if (eid.startsWith("media_player.") && reg[eid].device_id === dev) {
          const st = this._hass.states[eid];
          if (st) return st.attributes.friendly_name || eid;
        }
      }
    }
    const fn = (st0 && st0.attributes.friendly_name) || numId;
    return fn.replace(/\s*Sleep timer$/i, "");
  }
  _sleepSensor(numId) {
    const reg = (this._hass && this._hass.entities) || {};
    const dev = this._device(numId);
    for (const eid of Object.keys(reg)) {
      if (
        eid.startsWith("sensor.") &&
        reg[eid].device_id === dev &&
        this._hass.states[eid] &&
        this._hass.states[eid].attributes.axium_kind === "sleep"
      ) {
        return eid;
      }
    }
    return null;
  }
  // The sleep-timer number + sleep sensor entity ids for a zone media_player.
  _zoneSleep(zoneEntityId) {
    const reg = (this._hass && this._hass.entities) || {};
    const dev = reg[zoneEntityId] && reg[zoneEntityId].device_id;
    let numberId = null;
    let sensorId = null;
    if (dev) {
      for (const eid of Object.keys(reg)) {
        if (reg[eid].device_id !== dev) continue;
        const st = this._hass.states[eid];
        const a = st && st.attributes;
        if (!a) continue;
        if (eid.startsWith("number.") && a.axium_kind === "sleep_timer" && !a.sleep_all)
          numberId = eid;
        if (eid.startsWith("sensor.") && a.axium_kind === "sleep") sensorId = eid;
      }
    }
    return { numberId, sensorId };
  }
  _setZones(zoneEntityIds, minutes) {
    for (const z of zoneEntityIds) {
      const { numberId } = this._zoneSleep(z);
      if (numberId) {
        this._hass.callService("number", "set_value", {
          entity_id: numberId,
          value: minutes,
        });
      }
    }
  }

  // Descriptors for the rows to show, from the configured sections.
  _rowDescriptors() {
    const out = [];
    const sections = this._sections();
    if (sections.includes("all")) {
      const allId = this._allNumberId();
      if (allId) out.push({ key: `num:${allId}`, kind: "number", numberId: allId });
    }
    if (sections.includes("zones")) {
      for (const id of this._zoneNumberIds()) {
        out.push({ key: `num:${id}`, kind: "number", numberId: id });
      }
    }
    if (sections.includes("presets")) {
      for (const p of this._presets()) {
        out.push({
          key: `preset:${p.name}`,
          kind: "preset",
          label: p.name,
          zones: p.zones || [],
        });
      }
    }
    return out;
  }

  _render() {
    const descs = this._rowDescriptors();
    const sig = descs.map((d) => d.key).join(",");
    if (sig !== this._sig) {
      this._sig = sig;
      this._build(descs);
    }
    this._tick();
  }

  _build(descs) {
    this._rowEls = {};
    this._descs = descs;
    const title = this._config.name || "Sleep timers";
    this.shadowRoot.innerHTML = `
      <style>${AxiumAlarmsCard.styles}</style>
      <ha-card>
        <div class="title">${title}</div>
        <div class="rows" id="rows"></div>
        <div class="overlay" id="overlay" hidden><div class="sheet" id="sheet"></div></div>
      </ha-card>`;
    const overlay = this.shadowRoot.getElementById("overlay");
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) overlay.hidden = true;
    });
    const rows = this.shadowRoot.getElementById("rows");
    if (!descs.length) rows.innerHTML = `<div class="empty">Nothing to show.</div>`;
    for (const d of descs) {
      const row = document.createElement("div");
      row.className = "row";
      const label = d.kind === "preset" ? d.label : this._zoneName(d.numberId);
      row.innerHTML = `
        <div class="mid">
          <div class="n">${label}</div>
          <div class="quick">${[15, 30, 60, 90]
            .map((m) => `<button class="q" data-m="${m}">${m}m</button>`)
            .join("")}<button class="custom" title="Custom minutes">Custom…</button></div>
        </div>
        <div class="cd"></div>
        <button class="x" title="Cancel" hidden>&#10005;</button>`;
      const apply = (minutes) => {
        if (d.kind === "preset") this._setZones(d.zones, minutes);
        else
          this._hass.callService("number", "set_value", {
            entity_id: d.numberId,
            value: minutes,
          });
      };
      row.querySelectorAll(".q").forEach((b) =>
        b.addEventListener("click", () => apply(Number(b.dataset.m)))
      );
      row.querySelector(".custom").addEventListener("click", () =>
        this._openCustom(label, apply)
      );
      row.querySelector(".x").addEventListener("click", () => apply(0));
      rows.appendChild(row);
      this._rowEls[d.key] = row;
    }
    this._tick();
  }

  /** A clean in-card popover to enter a custom sleep length in minutes. */
  _openCustom(label, apply) {
    const overlay = this.shadowRoot.getElementById("overlay");
    const sheet = this.shadowRoot.getElementById("sheet");
    if (!overlay || !sheet) return;
    sheet.innerHTML = `
      <div class="sheet-head">
        <span class="sheet-title">Sleep · ${escHtml(label)}</span>
        <button class="iconbtn close" title="Close">&#10005;</button>
      </div>
      <div class="cust-row">
        <input type="number" class="cust-input" min="1" max="1440" step="1" value="45" inputmode="numeric">
        <span class="cust-unit">minutes</span>
      </div>
      <div class="cust-chips">${[15, 30, 45, 60, 90, 120]
        .map((m) => `<button class="q" data-m="${m}">${m}m</button>`)
        .join("")}</div>
      <div class="cust-actions">
        <button class="cust-cancel">Cancel</button>
        <button class="cust-set">Set timer</button>
      </div>`;
    const input = sheet.querySelector(".cust-input");
    const close = () => {
      overlay.hidden = true;
    };
    const commit = () => {
      const m = Math.round(Number(input.value));
      if (Number.isFinite(m) && m > 0) {
        apply(m);
        close();
      } else {
        input.focus();
      }
    };
    sheet.querySelector(".close").addEventListener("click", close);
    sheet.querySelector(".cust-cancel").addEventListener("click", close);
    sheet.querySelector(".cust-set").addEventListener("click", commit);
    sheet.querySelectorAll(".cust-chips .q").forEach((b) =>
      b.addEventListener("click", () => {
        input.value = b.dataset.m;
        input.focus();
      })
    );
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") commit();
      else if (e.key === "Escape") close();
    });
    overlay.hidden = false;
    input.focus();
    input.select();
  }

  // The furthest-out running sleep deadline (ms) among a set of zones, or null.
  _presetDeadline(zoneEntityIds) {
    let best = null;
    for (const z of zoneEntityIds) {
      const { sensorId } = this._zoneSleep(z);
      if (!sensorId) continue;
      const st = this._hass.states[sensorId];
      if (st && st.state && st.state !== "unknown" && st.state !== "unavailable") {
        const t = Date.parse(st.state);
        if (!isNaN(t) && (best === null || t > best)) best = t;
      }
    }
    return best;
  }

  _tick() {
    if (!this._hass || !this._descs) return;
    for (const d of this._descs) {
      const row = this._rowEls[d.key];
      if (!row) continue;
      let iso = null;
      if (d.kind === "preset") {
        const best = this._presetDeadline(d.zones);
        iso = best === null ? null : new Date(best).toISOString();
      } else {
        const sensor = this._sleepSensor(d.numberId);
        const st = sensor ? this._hass.states[sensor] : null;
        if (st && st.state && st.state !== "unknown" && st.state !== "unavailable")
          iso = st.state;
      }
      const cd = row.querySelector(".cd");
      const x = row.querySelector(".x");
      const left = iso ? axiumCountdown(iso) : null;
      if (left) {
        cd.innerHTML = `<span class="in">${left} left</span>`;
        x.hidden = false;
      } else {
        cd.innerHTML = "";
        x.hidden = true;
      }
    }
  }
}

/** Visual editor for the sleep card — amplifier, name, and which sections to show. */
class AxiumSleepCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._render();
  }
  set hass(hass) {
    this._hass = hass;
    this._render();
  }
  async _ensureHaForm() {
    if (customElements.get("ha-form")) return;
    try {
      const helpers = await window.loadCardHelpers();
      const card = await helpers.createCardElement({ type: "entities", entities: [] });
      await card.constructor.getConfigElement();
    } catch (err) {
      /* ha-form upgrades once available */
    }
  }
  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.addEventListener("value-changed", (ev) => this._changed(ev));
      this.appendChild(this._form);
      this._ensureHaForm();
    }
    const hubs = axiumHubs(this._hass);
    const hubOptions = hubs.map((h) => ({ value: h.id, label: h.name }));
    const data = {
      sections: ["all", "zones", "presets"],
      ...this._config,
    };
    if (!data.hub && hubs.length) data.hub = hubs[0].id;
    const zoneOptions = axiumZoneSelectOptions(this._hass, data.hub);
    this._form.hass = this._hass;
    this._form.data = data;
    this._form.schema = [
      {
        name: "hub",
        selector: hubOptions.length
          ? { select: { mode: "dropdown", options: hubOptions } }
          : { text: {} },
      },
      {
        name: "sections",
        selector: {
          select: {
            multiple: true,
            options: [
              { value: "all", label: "All-zones timer" },
              { value: "zones", label: "Individual zones" },
              { value: "presets", label: "Presets" },
            ],
          },
        },
      },
      {
        name: "zones",
        selector: zoneOptions.length
          ? { select: { multiple: true, mode: "list", options: zoneOptions } }
          : { entity: { integration: "axium", domain: "media_player", multiple: true } },
      },
      { name: "name", selector: { text: {} } },
    ];
    this._form.computeLabel = (s) =>
      ({
        hub: "Amplifier",
        sections: "Show",
        zones: "Zones to show (empty = all)",
        name: "Card name (optional)",
      }[s.name] || s.name);
  }
  _changed(ev) {
    ev.stopPropagation();
    this._config = { ...ev.detail.value };
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: this._config },
        bubbles: true,
        composed: true,
      })
    );
  }
}

// Guard against the module being loaded twice (e.g. a manually-added resource
// plus the integration's auto-registration), which would otherwise throw.
/**
 * Axium Volumes Card — a vertical volume slider per zone (plus a mute button),
 * for quick whole-house level balancing. Reads/writes only the zones'
 * media_player state; nothing is stored on the card.
 *
 * Config:
 *   type: custom:axium-volumes-card
 *   hub: <config_entry_id>   # optional — defaults to the only Axium hub
 *   name: Volumes            # optional — header text
 *   zones: [...]             # optional — zone media_players to show; auto if omitted
 */
class AxiumVolumesCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._sig = "";
    this._drag = {};
    this._timers = {};
  }

  setConfig(config) {
    this._config = config || {};
    this._sig = "";
    this.shadowRoot.innerHTML = "";
  }

  set hass(hass) {
    this._hass = hass;
    if (!hass || !this._config) return;
    this._render();
  }

  getCardSize() {
    return 4;
  }

  disconnectedCallback() {
    for (const t of Object.values(this._timers)) if (t) clearTimeout(t);
    this._timers = {};
  }

  static getConfigElement() {
    return document.createElement("axium-volumes-card-editor");
  }

  static getStubConfig(hass) {
    const hubs = axiumHubs(hass);
    return hubs.length ? { hub: hubs[0].id } : {};
  }

  _hubId() {
    return this._config.hub || (axiumHubs(this._hass)[0] || {}).id;
  }

  _name(id) {
    const st = this._hass.states[id];
    const n = st && st.attributes.friendly_name;
    return n || id.split(".")[1].replace(/_/g, " ");
  }

  _zones() {
    let auto = axiumMediaPlayers(this._hass, this._hubId());
    const pick = this._config.zones || this._config.entities;
    if (Array.isArray(pick) && pick.length) {
      auto = auto.filter((id) => pick.includes(id));
    }
    // Always ordered by physical zone number (1..16+).
    return axiumSortZones(this._hass, auto);
  }

  _render() {
    const zones = this._zones();
    const sig = zones.join(",") + "|" + (this._config.name || "");
    if (sig !== this._sig) {
      this._sig = sig;
      this._build(zones);
    }
    this._update(zones);
  }

  _build(zones) {
    const title = this._config.name || "Volumes";
    this.shadowRoot.innerHTML = `
      <style>${AxiumVolumesCard.styles}</style>
      <ha-card>
        <div class="title">${escHtml(title)}</div>
        ${
          zones.length
            ? `<div class="cols"></div>`
            : `<div class="empty">No Axium zones.</div>`
        }
      </ha-card>`;
    const cols = this.shadowRoot.querySelector(".cols");
    this._cells = {};
    if (!cols) return;
    for (const z of zones) {
      const col = document.createElement("div");
      col.className = "col";
      col.innerHTML = `
        <div class="pct"></div>
        <div class="volwrap">
          <div class="volcap"></div>
          <input type="range" class="vol" min="0" max="100" step="1" orient="vertical">
        </div>
        <button class="mute iconbtn" title="Mute"><ha-icon icon="mdi:volume-high"></ha-icon></button>
        <div class="zn"></div>`;
      const slider = col.querySelector(".vol");
      slider.addEventListener("input", () => {
        // Can't drag past the zone's max volume (the greyed cap).
        const maxv = axiumMaxVolume(this._hass, z);
        if (Number(slider.value) > maxv) slider.value = maxv;
        this._drag[z] = true;
        col.querySelector(".pct").textContent = slider.value + "%";
        this._scheduleVolume(z, Number(slider.value) / 100);
      });
      slider.addEventListener("change", () => {
        this._drag[z] = false;
        this._setVolume(z, Number(slider.value) / 100);
      });
      col.querySelector(".mute").addEventListener("click", () => {
        const st = this._hass.states[z];
        this._hass.callService("media_player", "volume_mute", {
          entity_id: z,
          is_volume_muted: !(st && st.attributes.is_volume_muted),
        });
      });
      cols.appendChild(col);
      this._cells[z] = col;
    }
  }

  // Debounce live drags so we don't flood the amp; the final `change` still fires.
  _scheduleVolume(z, level) {
    if (this._timers[z]) clearTimeout(this._timers[z]);
    this._timers[z] = setTimeout(() => {
      this._timers[z] = null;
      this._setVolume(z, level);
    }, 200);
  }

  _setVolume(z, level) {
    if (this._timers[z]) {
      clearTimeout(this._timers[z]);
      this._timers[z] = null;
    }
    const maxv = axiumMaxVolume(this._hass, z) / 100;
    this._hass.callService("media_player", "volume_set", {
      entity_id: z,
      volume_level: Math.max(0, Math.min(maxv, level)),
    });
  }

  _update(zones) {
    for (const z of zones) {
      const col = this._cells[z];
      if (!col) continue;
      const st = this._hass.states[z];
      const off = !st || OFF_STATES.includes(st.state);
      const a = st ? st.attributes : {};
      // HA strips volume_level/is_volume_muted from an OFF media_player, so fall
      // back to axium_volume/axium_muted to still show an off zone's real level.
      const muted = !!(
        typeof a.is_volume_muted === "boolean" ? a.is_volume_muted : a.axium_muted
      );
      const lvl =
        typeof a.volume_level === "number"
          ? a.volume_level
          : typeof a.axium_volume === "number"
            ? a.axium_volume
            : 0;
      const pctv = Math.round(lvl * 100);
      col.classList.toggle("off", off);
      const slider = col.querySelector(".vol");
      if (!this._drag[z] && this.shadowRoot.activeElement !== slider) {
        slider.value = pctv;
        col.querySelector(".pct").textContent = pctv + "%";
      }
      // Grey out the range above the zone's max volume.
      axiumApplyVolCap(col.querySelector(".volcap"), this._hass, z, "height");
      const zn = col.querySelector(".zn");
      zn.textContent = this._name(z);
      zn.title = this._name(z);
      const mi = col.querySelector(".mute ha-icon");
      if (mi) mi.setAttribute("icon", muted ? "mdi:volume-off" : "mdi:volume-high");
      col.querySelector(".mute").classList.toggle("on", muted);
    }
  }
}

AxiumVolumesCard.styles = `
  ha-card { padding: 12px 16px; }
  .title { font-size: 1.1rem; font-weight: 600; margin-bottom: 12px; color: var(--primary-text-color); }
  .empty { color: var(--secondary-text-color); padding: 4px 0; }
  .cols { display: flex; flex-wrap: wrap; gap: 14px; align-items: flex-end; }
  .col { display: flex; flex-direction: column; align-items: center; gap: 6px; width: 58px; }
  .pct { font-size: 0.8rem; color: var(--secondary-text-color); min-height: 1em; }
  .volwrap { position: relative; width: 28px; height: 150px; }
  .vol {
    writing-mode: vertical-lr; direction: rtl;
    -webkit-appearance: slider-vertical; appearance: slider-vertical;
    width: 28px; height: 150px; margin: 0; accent-color: var(--primary-color); cursor: pointer;
  }
  /* Greyed-out region above the zone's max volume. */
  .volcap {
    position: absolute; top: 0; left: 0; right: 0; height: 0;
    background: var(--divider-color); opacity: 0.55; border-radius: 6px;
    pointer-events: none;
  }
  .col.off .vol { opacity: 0.45; }
  .zn {
    font-size: 0.78rem; color: var(--primary-text-color); text-align: center;
    max-width: 58px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .iconbtn {
    background: none; border: none; cursor: pointer; color: var(--secondary-text-color);
    padding: 2px; --mdc-icon-size: 20px;
  }
  .mute.on { color: var(--primary-color); }
`;

/** Visual editor for the volumes card: amplifier, zone whitelist, card name. */
class AxiumVolumesCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  async _ensureHaForm() {
    if (customElements.get("ha-form")) return;
    try {
      const helpers = await window.loadCardHelpers();
      const card = await helpers.createCardElement({ type: "entities", entities: [] });
      await card.constructor.getConfigElement();
    } catch (err) {
      /* ha-form will still upgrade once available */
    }
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.addEventListener("value-changed", (ev) => this._changed(ev));
      this.appendChild(this._form);
      this._ensureHaForm();
    }
    const hubs = axiumHubs(this._hass);
    const hubOptions = hubs.map((h) => ({ value: h.id, label: h.name }));
    const data = { ...this._config };
    if (!data.hub && hubs.length) data.hub = hubs[0].id;
    const zoneOptions = axiumZoneSelectOptions(this._hass, data.hub);
    this._form.hass = this._hass;
    this._form.data = data;
    this._form.schema = [
      {
        name: "hub",
        selector: hubOptions.length
          ? { select: { mode: "dropdown", options: hubOptions } }
          : { text: {} },
      },
      {
        name: "zones",
        selector: zoneOptions.length
          ? { select: { multiple: true, mode: "list", options: zoneOptions } }
          : { entity: { integration: "axium", domain: "media_player", multiple: true } },
      },
      { name: "name", selector: { text: {} } },
    ];
    this._form.computeLabel = (s) =>
      ({
        hub: "Amplifier",
        zones: "Zones to show (empty = all)",
        name: "Card name (optional)",
      }[s.name] || s.name);
  }

  _changed(ev) {
    ev.stopPropagation();
    this._config = { ...ev.detail.value };
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: this._config },
        bubbles: true,
        composed: true,
      })
    );
  }
}

/** Quick-play card: pick an amp stream (Music Assistant), then a grid of 10
 *  buttons each set to a saved MA song/album/playlist. Tapping a button plays it
 *  on the selected stream. Assignments persist in localStorage per hub. Reuses
 *  the shared <axium-ma-search> in "pick" mode to choose the media. */
class AxiumQuickPlayCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._sig = "";
    this._edit = false;
    this._sel = null;
  }

  setConfig(config) {
    this._config = config || {};
    this._sig = "";
    this.shadowRoot.innerHTML = "";
  }

  set hass(hass) {
    this._hass = hass;
    if (hass && this._config) this._render();
  }

  getCardSize() {
    return 5;
  }

  disconnectedCallback() {
    if (this._playTimer) clearTimeout(this._playTimer);
  }

  static getConfigElement() {
    return document.createElement("axium-hub-card-editor");
  }

  static getStubConfig(hass) {
    const hubs = axiumHubs(hass);
    return hubs.length ? { hub: hubs[0].id } : {};
  }

  _hubId() {
    return this._config.hub || (axiumHubs(this._hass)[0] || {}).id;
  }

  /** The Music Assistant player whose friendly name equals `name`. */
  _maByName(name) {
    const want = (name || "").trim().toLowerCase();
    if (!want) return null;
    const reg = this._hass.entities || {};
    for (const id of Object.keys(this._hass.states)) {
      if (!id.startsWith("media_player.")) continue;
      const e = reg[id];
      if (!e || e.platform !== "music_assistant") continue;
      const fn = (this._hass.states[id].attributes.friendly_name || "").trim().toLowerCase();
      if (fn === want) return id;
    }
    return null;
  }

  /** Amp streams (Axium 1/2) that have a resolvable MA player. */
  _amps() {
    return axiumAmps(this._hass, this._hubId())
      .map((a) => ({ id: a.id, name: a.name, player: this._maByName(a.name) }))
      .filter((a) => a.player);
  }

  _slotsKey() {
    return `axium-quickplay:${this._hubId()}`;
  }

  _slots() {
    let arr = [];
    try {
      arr = JSON.parse(localStorage.getItem(this._slotsKey()) || "[]");
    } catch (e) {
      arr = [];
    }
    if (!Array.isArray(arr)) arr = [];
    while (arr.length < 10) arr.push(null);
    return arr.slice(0, 10);
  }

  _saveSlots(arr) {
    try {
      localStorage.setItem(this._slotsKey(), JSON.stringify(arr.slice(0, 10)));
    } catch (e) {
      /* private mode / quota — buttons just won't persist */
    }
  }

  _selPlayer(amps) {
    if (this._sel && amps.some((a) => a.player === this._sel)) return this._sel;
    let remembered = null;
    try {
      remembered = localStorage.getItem(`axium-quickplay-src:${this._hubId()}`);
    } catch (e) {
      /* ignore */
    }
    if (remembered && amps.some((a) => a.player === remembered)) return remembered;
    return amps.length ? amps[0].player : null;
  }

  _render() {
    const amps = this._amps();
    this._sel = this._selPlayer(amps);
    const slots = this._slots();
    // Keep an open picker alive across hass updates — just refresh its search.
    const overlay = this.shadowRoot.getElementById("qpoverlay");
    if (overlay && !overlay.hidden) {
      const search = this.shadowRoot.querySelector("axium-ma-search");
      if (search) search.hass = this._hass;
      return;
    }
    const sig = JSON.stringify({
      a: amps.map((x) => x.name + "|" + x.player),
      s: this._sel,
      e: this._edit,
      slots,
      n: this._config.name || "",
    });
    if (sig !== this._sig) {
      this._sig = sig;
      this._build(amps, slots);
    }
  }

  _build(amps, slots) {
    const title = this._config.name || "Quick play";
    if (!amps.length) {
      this.shadowRoot.innerHTML = `<style>${AxiumQuickPlayCard.styles}</style>
        <ha-card><div class="title">${escHtml(title)}</div>
        <div class="empty">No Music Assistant stream player found. In Music Assistant, rename each amp's player to the amp's device name (e.g. "Axium 1").</div></ha-card>`;
      return;
    }
    const srcSel =
      amps.length > 1
        ? `<select class="src" title="Stream to play on">${amps
            .map(
              (a) =>
                `<option value="${escHtml(a.player)}"${a.player === this._sel ? " selected" : ""}>${escHtml(a.name)}</option>`
            )
            .join("")}</select>`
        : `<div class="srcone">${escHtml(amps[0].name)}</div>`;
    const grid = slots.map((s, i) => this._btnHtml(s, i)).join("");
    this.shadowRoot.innerHTML = `<style>${AxiumQuickPlayCard.styles}</style>
      <ha-card>
        <div class="head">
          <div class="title">${escHtml(title)}</div>
          <div class="headright">
            ${srcSel}
            <button class="editbtn iconbtn${this._edit ? " on" : ""}" title="Edit buttons">
              <ha-icon icon="mdi:pencil"></ha-icon></button>
          </div>
        </div>
        <div class="grid">${grid}</div>
        <div class="overlay" id="qpoverlay" hidden><div class="sheet" id="qpsheet"></div></div>
      </ha-card>`;

    const sel = this.shadowRoot.querySelector(".src");
    if (sel)
      sel.addEventListener("change", () => {
        this._sel = sel.value;
        try {
          localStorage.setItem(`axium-quickplay-src:${this._hubId()}`, this._sel);
        } catch (e) {
          /* ignore */
        }
      });
    this.shadowRoot.querySelector(".editbtn").addEventListener("click", () => {
      this._edit = !this._edit;
      this._sig = "";
      this._render();
    });
    for (const btn of this.shadowRoot.querySelectorAll(".qp")) {
      btn.addEventListener("click", (ev) => {
        if (ev.target.closest(".clr")) return;
        this._onButton(Number(btn.dataset.i));
      });
    }
    for (const x of this.shadowRoot.querySelectorAll(".clr")) {
      x.addEventListener("click", (ev) => {
        ev.stopPropagation();
        this._clear(Number(x.dataset.i));
      });
    }
    const overlay = this.shadowRoot.getElementById("qpoverlay");
    overlay.addEventListener("click", (ev) => {
      if (ev.target === overlay) this._closePicker();
    });
  }

  _btnHtml(slot, i) {
    const clr =
      this._edit && slot
        ? `<button class="clr" data-i="${i}" title="Clear"><ha-icon icon="mdi:close"></ha-icon></button>`
        : "";
    if (slot && slot.media_content_id) {
      const art = slot.thumbnail
        ? `style="background-image:url('${escHtml(slot.thumbnail)}')"`
        : "";
      return `<div class="qp filled" role="button" tabindex="0" data-i="${i}" title="${escHtml(slot.title || "Music")}">
        ${clr}
        <div class="art" ${art}></div>
        <div class="lbl">${escHtml(slot.title || "Music")}</div>
      </div>`;
    }
    return `<div class="qp empty" role="button" tabindex="0" data-i="${i}">
      <ha-icon icon="mdi:plus"></ha-icon>
      <div class="lbl">${this._edit ? "Set" : "Empty"}</div>
    </div>`;
  }

  _onButton(i) {
    const slot = this._slots()[i];
    if (this._edit || !slot || !slot.media_content_id) this._openPicker(i);
    else this._play(this._sel, slot);
  }

  _play(player, slot) {
    if (!player || !slot || !slot.media_content_id) return;
    const args = {
      entity_id: player,
      media_content_id: slot.media_content_id,
      media_content_type: slot.media_content_type || "playlist",
      enqueue: "play",
    };
    const st = this._hass.states[player];
    this._hass.callService("media_player", "play_media", args);
    // play_media while already PLAYING stops the amp renderer; a second play from
    // the now-idle state actually plays it (verified — same as the stream panel).
    if (st && st.state === "playing") {
      if (this._playTimer) clearTimeout(this._playTimer);
      this._playTimer = setTimeout(() => {
        if (this._hass) this._hass.callService("media_player", "play_media", args);
      }, 1500);
    }
  }

  _clear(i) {
    const slots = this._slots();
    slots[i] = null;
    this._saveSlots(slots);
    this._sig = "";
    this._render();
  }

  _openPicker(i) {
    const overlay = this.shadowRoot.getElementById("qpoverlay");
    const sheet = this.shadowRoot.getElementById("qpsheet");
    if (!overlay || !sheet || !this._sel) return;
    sheet.innerHTML = `
      <div class="sheet-head">
        <span class="sheet-title">Choose music for button ${i + 1}</span>
        <button class="close iconbtn"><ha-icon icon="mdi:close"></ha-icon></button>
      </div>
      <axium-ma-search></axium-ma-search>`;
    const search = sheet.querySelector("axium-ma-search");
    search.mode = "pick";
    search.startBrowse = true;
    search.hass = this._hass;
    search.player = this._sel;
    search.addEventListener("pick", (ev) => this._onPick(i, ev.detail));
    sheet.querySelector(".close").addEventListener("click", () => this._closePicker());
    overlay.hidden = false;
  }

  _onPick(i, it) {
    if (!it || !it.media_content_id) return;
    const slots = this._slots();
    slots[i] = {
      title: it.title || "Music",
      media_content_id: it.media_content_id,
      media_content_type: it.media_content_type || "playlist",
      thumbnail: it.thumbnail || "",
    };
    this._saveSlots(slots);
    this._closePicker();
    this._sig = "";
    this._render();
  }

  _closePicker() {
    const overlay = this.shadowRoot.getElementById("qpoverlay");
    if (overlay) overlay.hidden = true;
    const sheet = this.shadowRoot.getElementById("qpsheet");
    if (sheet) sheet.innerHTML = "";
  }
}

AxiumQuickPlayCard.styles = `
  ha-card { padding: 12px 16px; }
  .head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 12px; }
  .title { font-size: 1.1rem; font-weight: 600; color: var(--primary-text-color); }
  .headright { display: inline-flex; align-items: center; gap: 8px; }
  .empty { color: var(--secondary-text-color); padding: 4px 0; }
  .src {
    background: var(--secondary-background-color); color: var(--primary-text-color);
    border: 1px solid var(--divider-color); border-radius: 8px; padding: 5px 8px; font-size: 0.9rem;
  }
  .srcone { color: var(--secondary-text-color); font-size: 0.9rem; }
  .iconbtn { background: none; border: none; cursor: pointer; color: var(--secondary-text-color); padding: 4px; --mdc-icon-size: 20px; border-radius: 8px; }
  .editbtn.on { color: var(--primary-color); background: var(--secondary-background-color); }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(96px, 1fr)); gap: 10px; }
  .qp {
    position: relative; display: flex; flex-direction: column; gap: 6px;
    background: var(--secondary-background-color); border: 1px solid var(--divider-color);
    border-radius: 12px; padding: 8px; cursor: pointer; color: var(--primary-text-color);
  }
  .qp:hover { border-color: var(--primary-color); }
  .qp:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
  .qp .art { width: 100%; aspect-ratio: 1 / 1; border-radius: 8px; background: var(--divider-color) center/cover no-repeat; }
  .qp .lbl { font-size: 0.8rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .qp.empty { align-items: center; justify-content: center; min-height: 120px; color: var(--secondary-text-color); border-style: dashed; --mdc-icon-size: 26px; }
  .qp .clr {
    position: absolute; top: 4px; right: 4px; z-index: 2; background: rgba(0,0,0,0.55); color: #fff;
    border: none; border-radius: 50%; width: 24px; height: 24px; cursor: pointer; display: inline-flex;
    align-items: center; justify-content: center; --mdc-icon-size: 16px;
  }
  .overlay { position: fixed; inset: 0; z-index: 9999; background: rgba(0, 0, 0, 0.5); }
  .overlay[hidden] { display: none; }
  .sheet {
    position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%);
    width: 94%; max-width: 94%; height: 82vh; max-height: 88vh;
    background: var(--card-background-color, var(--ha-card-background, #fff));
    border-radius: 16px; box-shadow: 0 8px 40px rgba(0, 0, 0, 0.45);
    box-sizing: border-box; padding: 12px 14px 14px;
    display: flex; flex-direction: column; overflow: hidden;
  }
  @media (min-width: 768px) { .sheet { width: 480px; max-width: 92%; height: 70vh; } }
  .sheet-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 8px; }
  .sheet-title { font-weight: 600; color: var(--primary-text-color); }
  .sheet axium-ma-search { flex: 1 1 auto; min-height: 0; display: block; }
`;

if (!customElements.get("axium-source-card")) {
  customElements.define("axium-ma-search", AxiumMaSearch);
  customElements.define("axium-source-card", AxiumSourceCard);
  customElements.define("axium-source-card-editor", AxiumSourceCardEditor);
  customElements.define("axium-hub-card", AxiumHubCard);
  customElements.define("axium-hub-card-editor", AxiumHubCardEditor);
  customElements.define("axium-matrix-card", AxiumMatrixCard);
  customElements.define("axium-matrix-card-editor", AxiumMatrixCardEditor);
  customElements.define("axium-alarms-card", AxiumAlarmsCard);
  customElements.define("axium-sleep-card", AxiumSleepCard);
  customElements.define("axium-sleep-card-editor", AxiumSleepCardEditor);
  customElements.define("axium-volumes-card", AxiumVolumesCard);
  customElements.define("axium-volumes-card-editor", AxiumVolumesCardEditor);
  customElements.define("axium-quickplay-card", AxiumQuickPlayCard);

  window.customCards = window.customCards || [];
  window.customCards.push(
    {
      type: "axium-source-card",
      name: "Axium Source Card",
      description: "Assign zones to a source and control playback (hass-axium).",
      documentationURL: "https://github.com/t-joosten/hass-axium",
    },
    {
      type: "axium-hub-card",
      name: "Axium Hub Card",
      description: "Compact amplifier status with an all-off button (hass-axium).",
      documentationURL: "https://github.com/t-joosten/hass-axium",
    },
    {
      type: "axium-matrix-card",
      name: "Axium Matrix Card",
      description: "Zones × sources routing grid for the whole system (hass-axium).",
      documentationURL: "https://github.com/t-joosten/hass-axium",
    },
    {
      type: "axium-alarms-card",
      name: "Axium Alarms Card",
      description: "Wake-to-music alarms with a live time-left countdown (hass-axium).",
      documentationURL: "https://github.com/t-joosten/hass-axium",
    },
    {
      type: "axium-sleep-card",
      name: "Axium Sleep Timers Card",
      description: "Running sleep timers with time left per zone (hass-axium).",
      documentationURL: "https://github.com/t-joosten/hass-axium",
    },
    {
      type: "axium-volumes-card",
      name: "Axium Volumes Card",
      description: "A vertical volume slider per zone for quick balancing (hass-axium).",
      documentationURL: "https://github.com/t-joosten/hass-axium",
    },
    {
      type: "axium-quickplay-card",
      name: "Axium Quick Play Card",
      description: "Pick an amp stream, then 10 buttons for saved Music Assistant songs/playlists (hass-axium).",
      documentationURL: "https://github.com/t-joosten/hass-axium",
    }
  );

  // eslint-disable-next-line no-console
  console.info(
    "%c AXIUM-SOURCE-CARD ",
    "background:#3949ab;color:#fff;border-radius:3px"
  );
}
