"""Voice-assistant (Assist) support for Axium.

Two halves that work together:

* **Intent handlers** (this module, registered in ``async_setup``) that act on a
  matched voice command: route a zone to a source, set a sleep timer, activate a
  preset, or announce a spoken message on some zones.
* **Sentence generation** (:func:`async_update_sentences`) that writes the phrase
  templates the built-in (hassil) conversation agent matches against. The agent
  only reads sentences from ``<config>/custom_sentences/<lang>/``, never from a
  custom integration's own folder, so we generate one file per language there —
  with the live zone/source/preset names baked in — and reload the agent.

The built-in intents (power/volume/transport) are already localized by Home
Assistant; we only add the Axium-specific verbs, in Dutch and English.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import voluptuous as vol
import yaml

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
    intent,
)

from .const import (
    DOMAIN,
    ID_KEY,
    NAME_KEY,
    SOURCE_BYTE_TO_NAME,
    SOURCE_MEDIA_PLAYER_BYTE,
)
from .helpers import get_presets, get_sources
from .voice_sentences import (
    INTENT_ANNOUNCE,
    INTENT_PRESET,
    INTENT_SET_SOURCE,
    INTENT_SLEEP,
    LANGUAGES,
    SLEEP_MAX_MIN,
    _spoken,
    build_language_doc,
    build_whisper_prompt,
)

LOGGER = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Spoken responses (kept out of the sentence files so they can't drift).
# --------------------------------------------------------------------------- #
_RESPONSES: dict[str, dict[str, str]] = {
    "set_source": {
        "nl": "Ik zet {zones} op {source}.",
        "en": "Switching {zones} to {source}.",
    },
    "sleep": {
        "nl": "Slaaptimer van {minutes} minuten ingesteld voor {zones}.",
        "en": "Sleep timer set for {minutes} minutes in {zones}.",
    },
    "sleep_all": {
        "nl": "Slaaptimer van {minutes} minuten ingesteld voor alle zones.",
        "en": "Sleep timer set for {minutes} minutes in all zones.",
    },
    "preset": {
        "nl": "Preset {preset} geactiveerd.",
        "en": "Activated the {preset} preset.",
    },
    "announce": {
        "nl": "Omgeroepen in {zones}.",
        "en": "Announced in {zones}.",
    },
    "no_zone": {
        "nl": "Ik kon die zone niet vinden.",
        "en": "I couldn't find that zone.",
    },
    "no_preset": {
        "nl": "Die preset ken ik niet.",
        "en": "I don't know that preset.",
    },
    "no_all_sleep": {
        "nl": "Er is geen slaaptimer voor alle zones.",
        "en": "There's no all-zones sleep timer.",
    },
}


def _lang(language: str | None) -> str:
    """Normalise a pipeline language (e.g. ``nl-NL``) to ``nl``/``en``."""
    base = (language or "en").split("-")[0].lower()
    return base if base in LANGUAGES else "en"


def _t(language: str | None, key: str, **kwargs: Any) -> str:
    """Localised response text for the pipeline language."""
    return _RESPONSES[key][_lang(language)].format(**kwargs)


def _slot(intent_obj: intent.Intent, key: str) -> Any:
    """Return a matched slot's value, or ``None``."""
    data = intent_obj.slots.get(key)
    return data.get("value") if data else None


def _friendly(hass: HomeAssistant, entity_ids: list[str]) -> str:
    """A human list of zone names for the spoken reply."""
    names = []
    for eid in entity_ids:
        state = hass.states.get(eid)
        names.append(state.name if state else eid)
    if len(names) <= 1:
        return names[0] if names else ""
    return ", ".join(names[:-1]) + " & " + names[-1]


@callback
def _axium_zone_entries(hass: HomeAssistant) -> list[Any]:
    """Registry entries for the enabled Axium zone media_players."""
    reg = er.async_get(hass)
    return [
        ent
        for ent in reg.entities.values()
        if ent.platform == DOMAIN
        and ent.domain == "media_player"
        and not ent.disabled_by
    ]


@callback
def _axium_media_players(hass: HomeAssistant) -> list[str]:
    """Every enabled Axium zone media_player entity id."""
    return [ent.entity_id for ent in _axium_zone_entries(hass)]


@callback
def _pipeline_tts_engine(hass: HomeAssistant, language: str | None = None) -> str | None:
    """An Assist pipeline's TTS engine (e.g. Piper), matching ``language`` if we can.

    A spoken announcement should use the same local voice as the assistant's own
    replies — not whatever ``tts.*`` happens to be first (which may be a cloud
    engine like Google Translate). The *preferred* pipeline may have no TTS engine
    (the user's Whisper+Piper pipeline isn't always the default), so fall back to
    any pipeline that has one, preferring a language match. Best-effort:
    assist_pipeline is optional and its API can drift.
    """
    try:
        from homeassistant.components import assist_pipeline
    except Exception:  # noqa: BLE001
        return None

    def _engine(pipeline: Any) -> str | None:
        engine = getattr(pipeline, "tts_engine", None) if pipeline else None
        return engine if isinstance(engine, str) and engine else None

    try:
        preferred = assist_pipeline.async_get_pipeline(hass)
    except Exception:  # noqa: BLE001
        preferred = None
    if _engine(preferred):
        return _engine(preferred)

    try:
        pipelines = list(assist_pipeline.async_get_pipelines(hass))
    except Exception:  # noqa: BLE001
        return None
    wanted = _lang(language)
    # Language-matching pipelines first, then any — return the first with a TTS engine.
    pipelines.sort(key=lambda p: _lang(getattr(p, "language", None)) != wanted)
    for pipeline in pipelines:
        engine = _engine(pipeline)
        if engine:
            return engine
    return None


@callback
def _zone_target(intent_obj: intent.Intent) -> list[str]:
    """The zone entity id named by the command (baked into the axium_zone slot)."""
    eid = _slot(intent_obj, "axium_zone")
    if not eid:
        return []
    ent = er.async_get(intent_obj.hass).async_get(eid)
    if ent and ent.platform == DOMAIN and ent.domain == "media_player":
        return [eid]
    return []


@callback
def _is_stream_source(hass: HomeAssistant, zone_entity: str, source: str) -> bool:
    """Whether ``source`` selects the zone's (Media Player) stream, not an input."""
    state = hass.states.get(zone_entity)
    if not state:
        return False
    names = state.attributes.get("source_list") or []
    ids = state.attributes.get("source_ids") or []
    return any(
        nm == source and sid == SOURCE_MEDIA_PLAYER_BYTE
        for nm, sid in zip(names, ids)
    )


@callback
def _amp_device_for_entity(hass: HomeAssistant, zone_entity: str):
    """The amp device a zone media_player hangs off (its ``via_device``)."""
    reg = er.async_get(hass)
    ent = reg.async_get(zone_entity)
    if not ent or not ent.device_id:
        return None
    dev_reg = dr.async_get(hass)
    dev = dev_reg.async_get(ent.device_id)
    if not dev or not dev.via_device_id:
        return None
    return dev_reg.async_get(dev.via_device_id)


@callback
def _ma_player_by_name(hass: HomeAssistant, name: str) -> str | None:
    """The Music Assistant player whose friendly name equals ``name``."""
    want = (name or "").strip().lower()
    if not want:
        return None
    reg = er.async_get(hass)
    for ent in reg.entities.values():
        if ent.domain != "media_player" or ent.platform != "music_assistant":
            continue
        state = hass.states.get(ent.entity_id)
        if state and (state.name or "").strip().lower() == want:
            return ent.entity_id
    return None


# --------------------------------------------------------------------------- #
# Intent handlers
# --------------------------------------------------------------------------- #
class _AxiumIntent(intent.IntentHandler):
    """Base with a helper to build a spoken response."""

    @callback
    def _speak(self, intent_obj: intent.Intent, text: str) -> intent.IntentResponse:
        response = intent_obj.create_response()
        response.async_set_speech(text)
        return response


class SetSourceIntent(_AxiumIntent):
    """Route a zone/area to a named source (e.g. "zet de keuken op de pc")."""

    intent_type = INTENT_SET_SOURCE
    description = "Route an Axium zone to a named audio source"
    slot_schema = {
        vol.Required("axium_zone"): cv.string,
        vol.Required("axium_source"): cv.string,
    }

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        hass = intent_obj.hass
        source = _slot(intent_obj, "axium_source")
        zones = _zone_target(intent_obj)
        if not zones:
            return self._speak(intent_obj, _t(intent_obj.language, "no_zone"))
        await hass.services.async_call(
            "media_player",
            "select_source",
            {"entity_id": zones, "source": source},
            blocking=True,
        )
        # When we route a zone to its amp's Media Player stream, also START that
        # stream (like tapping a stream cell) so music actually plays, and answer
        # with the amp's name instead of the generic "Media Player".
        reply_source = source
        for zone_entity in zones:
            if not _is_stream_source(hass, zone_entity, source):
                continue
            amp = _amp_device_for_entity(hass, zone_entity)
            amp_name = (amp.name_by_user or amp.name) if amp else None
            if not isinstance(amp_name, str) or not amp_name:
                continue
            reply_source = amp_name
            player = _ma_player_by_name(hass, amp_name)
            if player:
                await hass.services.async_call(
                    "media_player", "media_play", {"entity_id": player}, blocking=False
                )
        return self._speak(
            intent_obj,
            _t(
                intent_obj.language,
                "set_source",
                zones=_friendly(hass, zones),
                source=reply_source,
            ),
        )


class SleepIntent(_AxiumIntent):
    """Set a zone (or all-zones) sleep timer."""

    intent_type = INTENT_SLEEP
    description = "Set an Axium sleep timer for a zone or all zones"
    slot_schema = {
        vol.Optional("axium_zone"): cv.string,
        vol.Optional("everywhere"): cv.string,
        vol.Required("minutes"): vol.All(vol.Coerce(int), vol.Range(0, SLEEP_MAX_MIN)),
    }

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        hass = intent_obj.hass
        minutes = int(_slot(intent_obj, "minutes"))
        reg = er.async_get(hass)

        if _slot(intent_obj, "everywhere"):
            targets = [
                ent.entity_id
                for ent in reg.entities.values()
                if ent.platform == DOMAIN
                and ent.domain == "number"
                and ent.unique_id.endswith("_sleep_all")
            ]
            if not targets:
                return self._speak(intent_obj, _t(intent_obj.language, "no_all_sleep"))
            await hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": targets, "value": minutes},
                blocking=True,
            )
            return self._speak(
                intent_obj, _t(intent_obj.language, "sleep_all", minutes=minutes)
            )

        zones = _zone_target(intent_obj)
        # Each zone's sleep-timer number shares the media_player's unique id + "_sleep".
        targets = []
        for eid in zones:
            ent = reg.async_get(eid)
            if not ent:
                continue
            nid = reg.async_get_entity_id("number", DOMAIN, f"{ent.unique_id}_sleep")
            if nid:
                targets.append(nid)
        if not targets:
            return self._speak(intent_obj, _t(intent_obj.language, "no_zone"))
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": targets, "value": minutes},
            blocking=True,
        )
        return self._speak(
            intent_obj,
            _t(
                intent_obj.language,
                "sleep",
                minutes=minutes,
                zones=_friendly(hass, zones),
            ),
        )


class PresetIntent(_AxiumIntent):
    """Activate a named zone preset, optionally onto a source."""

    intent_type = INTENT_PRESET
    description = "Activate a named Axium zone preset"
    slot_schema = {
        vol.Required("axium_preset"): cv.string,
        vol.Optional("axium_source"): cv.string,
    }

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        hass = intent_obj.hass
        name = _slot(intent_obj, "axium_preset")
        source = _slot(intent_obj, "axium_source")
        zones = _preset_zones(hass, name)
        if not zones:
            return self._speak(intent_obj, _t(intent_obj.language, "no_preset"))
        if source:
            await hass.services.async_call(
                "media_player",
                "select_source",
                {"entity_id": zones, "source": source},
                blocking=True,
            )
        else:
            await hass.services.async_call(
                "media_player", "turn_on", {"entity_id": zones}, blocking=True
            )
        return self._speak(
            intent_obj, _t(intent_obj.language, "preset", preset=name)
        )


class AnnounceIntent(_AxiumIntent):
    """Speak a message on some zones (or all) via the notification service."""

    intent_type = INTENT_ANNOUNCE
    description = "Announce a spoken message on Axium zones"
    slot_schema = {
        vol.Optional("axium_zone"): cv.string,
        vol.Required("message"): cv.string,
    }

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        hass = intent_obj.hass
        message = str(_slot(intent_obj, "message")).strip()
        if _slot(intent_obj, "axium_zone"):
            zones = _zone_target(intent_obj)
        else:
            zones = _axium_media_players(hass)
        if not zones:
            return self._speak(intent_obj, _t(intent_obj.language, "no_zone"))
        # Speak with the pipeline's own engine (Piper) when we can find it, so an
        # announcement matches the assistant's local voice instead of a cloud
        # default. The announcement may take a while (play + restore); don't block.
        data: dict[str, Any] = {"zones": zones, "message": message}
        engine = _pipeline_tts_engine(hass, intent_obj.language)
        if engine:
            # Use the pipeline engine's OWN configured voice — do NOT force a
            # language. A bare code ("nl") makes engines like Piper fail
            # ("Language 'nl' not supported"; it wants a full locale "nl_NL"),
            # which resolves to no media -> the zones activate but stay silent.
            data["tts_engine"] = engine
        else:
            # A fallback default engine (e.g. Google Translate) does need it.
            data["language"] = _lang(intent_obj.language)
        await hass.services.async_call(DOMAIN, "play_notification", data, blocking=False)
        return self._speak(
            intent_obj,
            _t(intent_obj.language, "announce", zones=_friendly(hass, zones)),
        )


@callback
def _preset_zones(hass: HomeAssistant, name: str) -> list[str]:
    """Zone entity ids for a preset (matched case-insensitively) across entries."""
    wanted = name.strip().casefold()
    for entry in hass.config_entries.async_entries(DOMAIN):
        for preset in get_presets(entry):
            if preset["name"].casefold() == wanted:
                return list(preset["zones"])
    return []


async def async_setup_intents(hass: HomeAssistant) -> None:
    """Register the Axium voice intents.

    Named ``async_setup_intents`` because Home Assistant's ``intent`` component
    auto-discovers each integration's ``intent.py`` as an intent platform and
    awaits this hook — so it is the idiomatic registration point (and the module
    MUST expose it, or platform processing raises AttributeError). Idempotent.
    """
    for handler in (
        SetSourceIntent(),
        SleepIntent(),
        PresetIntent(),
        AnnounceIntent(),
    ):
        intent.async_register(hass, handler)


# --------------------------------------------------------------------------- #
# Sentence-file generation
# --------------------------------------------------------------------------- #
@callback
def _collect_vocab(
    hass: HomeAssistant,
) -> tuple[
    list[tuple[str, str]], list[str], list[tuple[str, str]], list[str], list[str]
]:
    """Live vocabulary for the sentence files and Whisper prompt.

    Returns ``(zones, zone_names, source_pairs, prompt_sources, presets)`` where
    ``zones``/``source_pairs`` are ``(spoken, out)`` pairs. A source's ``out`` is
    the ``select_source`` name; the amp stream names ("Axium 1"/"Axium 2") are
    added as spoken aliases pointing at the shared "Media Player" source, since
    the media_player only lists that one generic name.
    """
    presets: list[str] = []
    source_pairs: list[tuple[str, str]] = []
    prompt_sources: list[str] = []
    seen_source: set[str] = set()
    media_source_name: str | None = None

    def _add_source(display: str, out: str) -> None:
        spoken = _spoken(display)
        if spoken and spoken not in seen_source:
            seen_source.add(spoken)
            source_pairs.append((spoken, out))
        if display and display not in prompt_sources:
            prompt_sources.append(display)

    controllers = hass.data.get(DOMAIN, {})
    for entry in hass.config_entries.async_entries(DOMAIN):
        controller = controllers.get(entry.entry_id)
        for src in get_sources(entry):
            name = None
            if controller is not None:
                name = controller.source_name(src[ID_KEY])
            name = name or src[NAME_KEY]
            if name:
                _add_source(name, name)
        for preset in get_presets(entry):
            if preset["name"] not in presets:
                presets.append(preset["name"])

    # Zones are targeted by their (renameable) friendly name -> entity_id; each
    # zone's live source_list carries the selectable source names (incl. the
    # generic "Media Player"), and its amp device name is a stream alias.
    dev_reg = dr.async_get(hass)
    zones: list[tuple[str, str]] = []
    zone_names: list[str] = []
    amp_names: list[str] = []
    seen_spoken: set[str] = set()
    for ent in _axium_zone_entries(hass):
        state = hass.states.get(ent.entity_id)
        # The friendly name plus any aliases (so an English alias like "Kitchen"
        # works alongside the Dutch zone name) — all pointing at this entity.
        # Keep only real strings: registry `name` can be a computed-name sentinel,
        # not a str, in current HA.
        primary = state.name if state else ent.original_name
        if isinstance(primary, str) and primary and primary not in zone_names:
            zone_names.append(primary)
        for label in [primary, *(ent.aliases or [])]:
            if not isinstance(label, str) or not label:
                continue
            spoken = _spoken(label)
            if spoken and spoken not in seen_spoken:
                seen_spoken.add(spoken)
                zones.append((spoken, ent.entity_id))
        names = (state.attributes.get("source_list") or []) if state else []
        ids = (state.attributes.get("source_ids") or []) if state else []
        for name, sid in zip(names, ids):
            if media_source_name is None and sid == SOURCE_MEDIA_PLAYER_BYTE:
                media_source_name = name
            if name:
                _add_source(name, name)
        # The zone's amp device name (via_device) is a spoken alias for the stream.
        dev = dev_reg.async_get(ent.device_id) if ent.device_id else None
        amp = dev_reg.async_get(dev.via_device_id) if dev and dev.via_device_id else None
        amp_name = (amp.name_by_user or amp.name) if amp else None
        if isinstance(amp_name, str) and amp_name and amp_name not in amp_names:
            amp_names.append(amp_name)

    # Map each amp name (Axium 1/2) to the shared Media Player source so
    # "zet de keuken op axium 1" routes the zone to its stream.
    stream_out = media_source_name or SOURCE_BYTE_TO_NAME.get(SOURCE_MEDIA_PLAYER_BYTE)
    if stream_out:
        for amp_name in amp_names:
            _add_source(amp_name, stream_out)

    return zones, zone_names, source_pairs, prompt_sources, presets


def _write_if_changed(path: str, text: str) -> bool:
    """Write ``text`` to ``path`` (making its language folder) if it differs."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, encoding="utf-8") as handle:
            if handle.read() == text:
                return False
    except FileNotFoundError:
        pass
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return True


async def async_update_sentences(hass: HomeAssistant) -> None:
    """Regenerate the per-language sentence files and reload the agent if changed.

    Cheap to call often: it skips rebuilding when the (sources, presets) signature
    is unchanged, and only reloads the conversation agent when a file's contents
    actually change.
    """
    zones, zone_names, source_pairs, prompt_sources, presets = _collect_vocab(hass)
    signature = (
        tuple(zones),
        tuple(zone_names),
        tuple(source_pairs),
        tuple(presets),
    )
    store = hass.data.setdefault(f"{DOMAIN}_voice", {})
    if store.get("signature") == signature:
        return
    store["signature"] = signature

    changed = False
    for language in LANGUAGES:
        doc = build_language_doc(language, zones, source_pairs, presets)
        text = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)
        path = hass.config.path("custom_sentences", language, "axium.yaml")
        try:
            if await hass.async_add_executor_job(_write_if_changed, path, text):
                changed = True
        except OSError as err:
            LOGGER.warning("Could not write Axium voice sentences to %s: %s", path, err)

    # A suggested Whisper initial_prompt (STT config lives in another integration,
    # so we can't set it — write it where the user can copy it, kept current).
    prompt_path = hass.config.path("axium_whisper_prompt.txt")
    try:
        await hass.async_add_executor_job(
            _write_if_changed,
            prompt_path,
            build_whisper_prompt(zone_names, prompt_sources, presets),
        )
    except OSError as err:
        LOGGER.warning("Could not write Axium Whisper prompt to %s: %s", prompt_path, err)

    if changed and hass.services.has_service("conversation", "reload"):
        await hass.services.async_call("conversation", "reload", {}, blocking=False)
        LOGGER.debug("Reloaded conversation agent with updated Axium sentences")
