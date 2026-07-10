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
    [...byId.entries()]
      .sort((a, b) => String(a[1]).localeCompare(String(b[1])))
      .forEach(([sid, name]) => {
        out.push({
          hub: hub.id,
          hubName: hub.name,
          id: sid,
          name,
          token: `${hub.id}${TOKEN_SEP}${sid}`,
          label: multi ? `${hub.name} ${name}` : name,
        });
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
    const auto = axiumMediaPlayers(this._hass, this._config.hub)
      .filter((id) => this._sourceNameFor(this._state(id)) != null)
      .sort();
    // Optional whitelist (`zones`, or legacy `entities`): show only these, in
    // the order given, dropping any that no longer offer the source.
    const pick = this._config.zones || this._config.entities;
    if (Array.isArray(pick) && pick.length) {
      return pick.filter((id) => auto.includes(id));
    }
    return auto;
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

  /** The picker token for the currently configured (hub, source id), if any. */
  _currentToken(choices) {
    const { hub, source } = this._config;
    if (source === undefined || source === null || source === "") return undefined;
    if (typeof source === "number") {
      // New id-based config: match by source id (prefer the same hub).
      return (
        (hub && choices.find((c) => c.hub === hub && c.id === source)) ||
        choices.find((c) => c.id === source) ||
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
        selector: {
          entity: { integration: "axium", domain: "media_player", multiple: true },
        },
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
    } else if (typeof token === "string" && token.includes(TOKEN_SEP)) {
      const sep = token.indexOf(TOKEN_SEP);
      value.hub = token.slice(0, sep);
      const idStr = token.slice(sep + 1);
      const n = Number(idStr);
      value.source = Number.isNaN(n) ? idStr : n;
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
    const hubDev = this._hubDevice();
    if (!hubId || !hubDev) return [];
    const amps = [hubDev];
    const devices = (this._hass && this._hass.devices) || {};
    for (const dev of Object.values(devices)) {
      if (dev.id === hubDev.id) continue;
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
          ? `<span class="alabel">${
              i === 0 ? "Main" : escHtml(dev.name_by_user || dev.name || "Amp")
            }</span> `
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
    const auto = axiumMediaPlayers(this._hass, this._hubId()).sort();
    const pick = this._config.zones || this._config.entities;
    if (Array.isArray(pick) && pick.length) {
      return pick.filter((id) => auto.includes(id));
    }
    return auto;
  }

  // Columns: every source offered across the hub's zones, as {id, name},
  // optionally narrowed to a configured `sources` whitelist (of ids).
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
    let list = [...byId.entries()].map(([id, name]) => ({ id, name }));
    const pick = this._config.sources;
    if (Array.isArray(pick) && pick.length) {
      const wanted = new Set(pick.map(Number));
      list = list.filter((s) => wanted.has(s.id));
    }
    return list.sort((a, b) => String(a.name).localeCompare(String(b.name)));
  }

  // Rebuild only when the zone or source set changes, not on every state tick.
  _signature() {
    return this._zones().join(",") + "|" + this._sources().map((s) => s.id).join(",");
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

  _route(zoneId, src) {
    const st = this._hass.states[zoneId];
    const sid = Number(src);
    // Tapping the zone's currently-active source turns the zone off (toggle).
    const on = st && !OFF_STATES.includes(st.state);
    if (on && this._currentSourceId(st) === sid) {
      this._hass.callService("media_player", "turn_off", { entity_id: zoneId });
      return;
    }
    const name = this._sourceNameFor(st, sid);
    if (name != null) {
      this._hass.callService("media_player", "select_source", {
        entity_id: zoneId,
        source: name,
      });
    }
  }

  _build() {
    const zones = this._zones();
    const sources = this._sources();
    const head =
      `<div class="corner"></div>` +
      sources
        .map(
          (s) =>
            `<div class="colhead" role="button" tabindex="0" data-src="${s.id}"` +
            ` title="${s.name}"><span>${s.name}</span></div>`
        )
        .join("");
    const rows = zones
      .map((z) => {
        const cells = sources
          .map(
            (s) =>
              `<button class="cell" data-zone="${z}" data-src="${s.id}">` +
              `<ha-icon icon="mdi:check"></ha-icon></button>`
          )
          .join("");
        return (
          `<div class="rowhead" role="button" tabindex="0" data-zone="${z}"` +
          ` title="${this._zoneName(z)}">${this._zoneName(z)}</div>` +
          cells
        );
      })
      .join("");

    this.shadowRoot.innerHTML = `
      <style>${AxiumMatrixCard.styles}</style>
      <ha-card>
        ${this._config.name ? `<div class="title">${this._config.name}</div>` : ""}
        <div class="scroll">
          <div class="matrix" style="grid-template-columns: minmax(72px,auto) repeat(${
            sources.length
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
      this._route(cell.dataset.zone, cell.dataset.src);
    });
    // Zone header: tap for quick volume/transport, hold for the device page.
    for (const rh of this.shadowRoot.querySelectorAll(".rowhead[data-zone]")) {
      this._attachHold(
        rh,
        () => this._openZonePanel(rh.dataset.zone),
        () => this._openZoneDevice(rh.dataset.zone)
      );
    }
    // Source header: tap to route a preset's zones onto that source.
    for (const ch of this.shadowRoot.querySelectorAll(".colhead[data-src]")) {
      const open = () => this._openPresetPanel(ch.dataset.src);
      ch.addEventListener("click", open);
      ch.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          open();
        }
      });
    }
    const overlay = this.shadowRoot.getElementById("overlay");
    overlay.addEventListener("click", (ev) => {
      if (ev.target === overlay) this._closePanel();
    });
    this._panel = null;
    this._built = true;
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

  _closePanel() {
    const overlay = this.shadowRoot.getElementById("overlay");
    if (overlay) overlay.hidden = true;
    if (this._volTimer) {
      clearTimeout(this._volTimer);
      this._volTimer = null;
    }
    this._panel = null;
  }

  /** Quick per-zone volume slider + mute + transport, in the popover. */
  _openZonePanel(zoneId) {
    const sheet = this.shadowRoot.getElementById("sheet");
    sheet.innerHTML = `
      <div class="sheet-head">
        <span class="sheet-title"></span>
        <span class="sheet-actions">
          <button class="iconbtn power" title="Power on/off"><ha-icon icon="mdi:power"></ha-icon></button>
          <button class="iconbtn close" title="Close"><ha-icon icon="mdi:close"></ha-icon></button>
        </span>
      </div>
      <div class="nowplaying" hidden>
        <div class="np-art"></div>
        <div class="np-meta">
          <div class="np-title"></div>
          <div class="np-artist"></div>
        </div>
      </div>
      <div class="volrow">
        <button class="iconbtn mute" title="Mute"><ha-icon icon="mdi:volume-high"></ha-icon></button>
        <input class="slider" type="range" min="0" max="100" step="1" aria-label="Volume">
        <span class="volval"></span>
      </div>
      <div class="transport">
        <button class="iconbtn" data-t="prev" title="Previous"><ha-icon icon="mdi:skip-previous"></ha-icon></button>
        <button class="iconbtn play" data-t="play" title="Play/Pause"><ha-icon icon="mdi:play"></ha-icon></button>
        <button class="iconbtn" data-t="next" title="Next"><ha-icon icon="mdi:skip-next"></ha-icon></button>
      </div>
    `;
    sheet.querySelector(".sheet-title").textContent = this._zoneName(zoneId);
    sheet.querySelector(".power").addEventListener("click", () =>
      this._togglePower(zoneId)
    );
    const slider = sheet.querySelector(".slider");
    slider.addEventListener("input", () => {
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
    for (const b of sheet.querySelectorAll("button[data-t]")) {
      b.addEventListener("click", () => {
        const svc =
          b.dataset.t === "prev"
            ? "media_previous_track"
            : b.dataset.t === "next"
            ? "media_next_track"
            : "media_play_pause";
        this._hass.callService("media_player", svc, { entity_id: zoneId });
      });
    }
    sheet.querySelector(".close").addEventListener("click", () => this._closePanel());
    this._panel = { type: "zone", zoneId, dragging: false };
    this.shadowRoot.getElementById("overlay").hidden = false;
    this._refreshPanel();
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
    this._hass.callService("media_player", "volume_set", {
      entity_id: zoneId,
      volume_level: Math.max(0, Math.min(1, pct / 100)),
    });
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
    this._hass.callService("media_player", on ? "turn_off" : "turn_on", {
      entity_id: zoneId,
    });
  }

  /** A Music Assistant player whose name matches this zone (best-effort link). */
  _maPlayerFor(zoneId) {
    const name = (this._zoneName(zoneId) || "").trim().toLowerCase();
    if (!name) return null;
    const reg = (this._hass && this._hass.entities) || {};
    for (const id of Object.keys(this._hass.states)) {
      if (!id.startsWith("media_player.")) continue;
      const e = reg[id];
      if (!e || e.platform !== "music_assistant") continue;
      const st = this._hass.states[id];
      const fn = st && (st.attributes.friendly_name || "").trim().toLowerCase();
      if (fn === name) return st;
    }
    return null;
  }

  /**
   * What's playing on a zone: prefer the amp zone's own now-playing (reported
   * for the internal Media Player source), else a same-named Music Assistant
   * player streaming to it. Returns null when nothing is playing.
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
    return pick(this._hass.states[zoneId]) || pick(this._maPlayerFor(zoneId));
  }

  /** Preset picker for one source column, in the popover. */
  _openPresetPanel(sourceId) {
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
    this.shadowRoot.getElementById("overlay").hidden = false;
  }

  /** Keep an open zone popover's now-playing/slider/mute/transport in step. */
  _refreshPanel() {
    if (!this._panel || this._panel.type !== "zone") return;
    const sheet = this.shadowRoot.getElementById("sheet");
    if (!sheet) return;
    const st = this._hass.states[this._panel.zoneId];
    if (!st) return;
    const np = this._zoneNowPlaying(this._panel.zoneId);
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
    const lvl = st.attributes.volume_level;
    if (slider && !this._panel.dragging && typeof lvl === "number") {
      const pct = Math.round(lvl * 100);
      slider.value = String(pct);
      if (volval) volval.textContent = `${pct}%`;
    }
    const muteIcon = sheet.querySelector(".mute ha-icon");
    if (muteIcon) {
      muteIcon.setAttribute(
        "icon",
        st.attributes.is_volume_muted ? "mdi:volume-off" : "mdi:volume-high"
      );
    }
    const powerBtn = sheet.querySelector(".power");
    if (powerBtn) powerBtn.classList.toggle("on", !OFF_STATES.includes(st.state));
    const feat = st.attributes.supported_features || 0;
    const setT = (t, ok) => {
      const b = sheet.querySelector(`button[data-t="${t}"]`);
      if (b) b.toggleAttribute("disabled", !ok);
    };
    setT("prev", !!(feat & SUPPORT_PREVIOUS_TRACK));
    setT("next", !!(feat & SUPPORT_NEXT_TRACK));
    setT("play", !!(feat & (SUPPORT_PLAY | SUPPORT_PAUSE)));
    const playIcon = sheet.querySelector('button[data-t="play"] ha-icon');
    if (playIcon) {
      playIcon.setAttribute(
        "icon",
        st.state === "playing" ? "mdi:pause" : "mdi:play"
      );
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
      const active = on && this._currentSourceId(st) === sid;
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
    this._refreshPanel();
  }
}

AxiumMatrixCard.styles = `
  ha-card { padding: 12px; position: relative; }
  .title { font-size: 1.1rem; font-weight: 600; margin-bottom: 8px; color: var(--primary-text-color); }
  .scroll { overflow-x: auto; }
  .matrix { display: grid; gap: 4px; align-items: stretch; min-width: min-content; }
  .corner { }
  .colhead, .rowhead {
    font-size: 0.8rem; color: var(--secondary-text-color);
    display: flex; align-items: center; overflow: hidden;
    cursor: pointer;
  }
  .colhead:hover span, .rowhead:hover { color: var(--primary-color); }
  .colhead:focus-visible, .rowhead:focus-visible {
    outline: 2px solid var(--primary-color); outline-offset: 1px; border-radius: 4px;
  }
  .colhead { justify-content: center; text-align: center; padding: 0 2px; }
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
  .overlay {
    position: absolute; inset: 0; z-index: 5;
    display: flex; align-items: center; justify-content: center;
    background: rgba(0, 0, 0, 0.35);
    border-radius: var(--ha-card-border-radius, 12px);
  }
  .overlay[hidden] { display: none; }
  .sheet {
    background: var(--card-background-color, var(--ha-card-background, #fff));
    border: 1px solid var(--divider-color);
    border-radius: 12px; padding: 12px 14px;
    min-width: 240px; max-width: 92%;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
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
  .slider {
    flex: 1 1 auto; min-width: 120px; height: 24px;
    accent-color: var(--primary-color); cursor: pointer;
  }
  .volval {
    width: 40px; text-align: right; font-size: 0.85rem;
    color: var(--secondary-text-color);
  }
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

    // Source columns available on the selected hub.
    const sourceOptions = axiumSourceChoices(this._hass)
      .filter((c) => c.hub === data.hub)
      .map((c) => ({ value: String(c.id), label: c.name }));

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
        selector: {
          entity: { integration: "axium", domain: "media_player", multiple: true },
        },
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
  // Zones offered in the Add form (config `zones` whitelist, else all).
  _addZones() {
    const all = axiumMediaPlayers(this._hass, this._hub());
    const pick = this._config.zones;
    return Array.isArray(pick) && pick.length
      ? all.filter((id) => pick.includes(id))
      : all;
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

  _buildAddForm() {
    const form = this.shadowRoot.getElementById("addform");
    const sources = this._sources();
    form.innerHTML = `
      <input type="text" class="f-name" placeholder="Name">
      <input type="time" class="f-time" value="07:00">
      <div class="chips f-days"></div>
      <div class="chips f-zones"></div>
      <select class="f-source">${sources
        .map((s) => `<option value="${s.id}">${s.name}</option>`)
        .join("")}</select>
      <label class="f-vol">Volume <input type="range" min="0" max="100" value="30"><span class="volval">30%</span></label>
      <button class="addbtn">Add</button>`;
    _DAY_ABBR.forEach((lbl, idx) => {
      const c = document.createElement("button");
      c.className = "daychip on";
      c.textContent = lbl[0];
      c.dataset.d = idx;
      c.title = lbl;
      c.addEventListener("click", () => c.classList.toggle("on"));
      form.querySelector(".f-days").appendChild(c);
    });
    for (const z of this._addZones()) {
      const st = this._hass.states[z];
      const c = document.createElement("button");
      c.className = "zonechip";
      c.textContent = (st && st.attributes.friendly_name) || z;
      c.dataset.z = z;
      c.addEventListener("click", () => c.classList.toggle("on"));
      form.querySelector(".f-zones").appendChild(c);
    }
    const vol = form.querySelector('input[type="range"]');
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
    this._svc("set_alarm", {
      name,
      time: form.querySelector(".f-time").value || "07:00",
      days,
      zones,
      source: Number(form.querySelector(".f-source").value),
      volume: Number(form.querySelector('input[type="range"]').value),
      enabled: true,
    });
    nameEl.value = "";
    this._toggleAdd();
  }
}

AxiumAlarmsCard.styles = `
  ha-card { padding: 12px 16px; }
  .title { font-size: 1.1rem; font-weight: 600; margin-bottom: 8px; color: var(--primary-text-color); }
  .empty { color: var(--secondary-text-color); padding: 4px 0; }
  .rows { display: flex; flex-direction: column; gap: 10px; }
  .row { display: flex; align-items: center; gap: 10px; }
  .mid { flex: 1 1 auto; min-width: 0; }
  .n { font-weight: 600; color: var(--primary-text-color); }
  .sub { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 2px; }
  .zn { font-size: 0.78rem; color: var(--secondary-text-color); margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
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
    width: 20px; height: 20px; border-radius: 50%; padding: 0;
    border: 1px solid var(--divider-color); background: none; cursor: pointer;
    font-size: 0.7rem; color: var(--secondary-text-color);
  }
  .daychip.on { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
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
  .addform { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--divider-color); }
  .addform input[type="text"], .addform select {
    font: inherit; padding: 6px 8px; border-radius: 6px;
    border: 1px solid var(--divider-color);
    background: var(--card-background-color); color: var(--primary-text-color);
  }
  .chips { display: flex; flex-wrap: wrap; gap: 4px; }
  .zonechip {
    padding: 4px 10px; border-radius: 16px; border: 1px solid var(--divider-color);
    background: none; cursor: pointer; font: inherit; font-size: 0.85rem;
    color: var(--primary-text-color);
  }
  .zonechip.on { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
  .f-vol { display: flex; align-items: center; gap: 8px; font-size: 0.85rem; color: var(--secondary-text-color); }
  .addbtn {
    align-self: flex-start; padding: 6px 16px; border-radius: 8px; border: none;
    background: var(--primary-color); color: var(--text-primary-color, #fff);
    cursor: pointer; font: inherit;
  }
  .quick { display: inline-flex; gap: 4px; margin-top: 4px; }
  .q {
    padding: 3px 10px; border-radius: 14px; border: 1px solid var(--divider-color);
    background: none; cursor: pointer; font: inherit; font-size: 0.8rem;
    color: var(--primary-text-color);
  }
  .q:hover { border-color: var(--primary-color); }
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
    return this._sleepNumbers()
      .filter((id) => !this._hass.states[id].attributes.sleep_all)
      .sort((a, b) => this._zoneName(a).localeCompare(this._zoneName(b)));
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
      </ha-card>`;
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
            .join("")}</div>
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
      row.querySelector(".x").addEventListener("click", () => apply(0));
      rows.appendChild(row);
      this._rowEls[d.key] = row;
    }
    this._tick();
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
      { name: "name", selector: { text: {} } },
    ];
    this._form.computeLabel = (s) =>
      ({
        hub: "Amplifier",
        sections: "Show",
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
if (!customElements.get("axium-source-card")) {
  customElements.define("axium-source-card", AxiumSourceCard);
  customElements.define("axium-source-card-editor", AxiumSourceCardEditor);
  customElements.define("axium-hub-card", AxiumHubCard);
  customElements.define("axium-hub-card-editor", AxiumHubCardEditor);
  customElements.define("axium-matrix-card", AxiumMatrixCard);
  customElements.define("axium-matrix-card-editor", AxiumMatrixCardEditor);
  customElements.define("axium-alarms-card", AxiumAlarmsCard);
  customElements.define("axium-sleep-card", AxiumSleepCard);
  customElements.define("axium-sleep-card-editor", AxiumSleepCardEditor);

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
    }
  );

  // eslint-disable-next-line no-console
  console.info(
    "%c AXIUM-SOURCE-CARD ",
    "background:#3949ab;color:#fff;border-radius:3px"
  );
}
