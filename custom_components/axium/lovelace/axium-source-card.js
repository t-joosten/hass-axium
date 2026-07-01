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
      if (hubId && entry.config_entry_id !== hubId) return false;
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
    if (entry && entry.platform === "axium" && entry.config_entry_id) {
      ids.add(entry.config_entry_id);
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
    if (Array.isArray(this._config.entities)) return this._config.entities;
    return axiumMediaPlayers(this._hass, this._config.hub)
      .filter((id) => this._sourceNameFor(this._state(id)) != null)
      .sort();
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
      chip.addEventListener("click", () => this._toggleZone(id));
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

    this._built = true;
  }

  _update() {
    const root = this.shadowRoot;
    root.getElementById("title").textContent = this._title();

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
  .titles { min-width: 0; }
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
      { name: "name", selector: { text: {} } },
    ];
    this._form.computeLabel = (s) =>
      ({ source: "Source", name: "Card name (optional)" }[s.name] || s.name);
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

// Guard against the module being loaded twice (e.g. a manually-added resource
// plus the integration's auto-registration), which would otherwise throw.
if (!customElements.get("axium-source-card")) {
  customElements.define("axium-source-card", AxiumSourceCard);
  customElements.define("axium-source-card-editor", AxiumSourceCardEditor);

  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "axium-source-card",
    name: "Axium Source Card",
    description: "Assign zones to a source and control playback (hass-axium).",
    documentationURL: "https://github.com/t-joosten/hass-axium",
  });

  // eslint-disable-next-line no-console
  console.info(
    "%c AXIUM-SOURCE-CARD ",
    "background:#3949ab;color:#fff;border-radius:3px"
  );
}
