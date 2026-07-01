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
 *   source: Apple TV          # required — the source this card controls
 *   name: Apple TV            # optional — header text (defaults to source)
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
    if (!this._config.source) {
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
    let source = "Source 1";
    const states = (hass && hass.states) || {};
    for (const id of Object.keys(states)) {
      if (id.startsWith("media_player.")) {
        const list = states[id].attributes.source_list;
        if (Array.isArray(list) && list.length) {
          source = list[0];
          break;
        }
      }
    }
    return { source };
  }

  _renderPlaceholder() {
    this.shadowRoot.innerHTML =
      `<style>${AxiumSourceCard.styles}</style>` +
      `<ha-card><div class="placeholder">` +
      `Set a <b>source</b> for this Axium Source Card.</div></ha-card>`;
    this._built = false;
  }

  // -- data helpers ----------------------------------------------------

  _zones() {
    if (Array.isArray(this._config.entities)) return this._config.entities;
    const src = this._config.source;
    return Object.keys(this._hass.states)
      .filter(
        (id) =>
          id.startsWith("media_player.") &&
          Array.isArray(this._hass.states[id].attributes.source_list) &&
          this._hass.states[id].attributes.source_list.includes(src)
      )
      .sort();
  }

  _state(id) {
    return this._hass.states[id];
  }

  _isActive(st) {
    return (
      st &&
      st.attributes.source === this._config.source &&
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

  // -- services --------------------------------------------------------

  _call(service, data) {
    this._hass.callService("media_player", service, data);
  }

  _toggleZone(id) {
    if (this._isActive(this._state(id))) {
      this._call("turn_off", { entity_id: id });
    } else {
      // select_source moves the zone onto this source, leaving its old one.
      this._call("select_source", { entity_id: id, source: this._config.source });
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
    root.getElementById("title").textContent =
      this._config.name || this._config.source;

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

/** Visual (UI) editor — a Source dropdown plus an optional card name. */
class AxiumSourceCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _sourceOptions() {
    const set = new Set();
    const states = (this._hass && this._hass.states) || {};
    for (const id of Object.keys(states)) {
      if (id.startsWith("media_player.")) {
        const list = states[id].attributes.source_list;
        if (Array.isArray(list)) list.forEach((s) => set.add(s));
      }
    }
    return [...set].sort();
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
    const options = this._sourceOptions().map((s) => ({ value: s, label: s }));
    this._form.hass = this._hass;
    this._form.data = this._config;
    this._form.schema = [
      {
        name: "source",
        required: true,
        selector: options.length
          ? { select: { mode: "dropdown", custom_value: true, options } }
          : { text: {} },
      },
      { name: "name", selector: { text: {} } },
    ];
    this._form.computeLabel = (s) =>
      ({ source: "Source", name: "Card name (optional)" }[s.name] || s.name);
  }

  _changed(ev) {
    ev.stopPropagation();
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: ev.detail.value },
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
