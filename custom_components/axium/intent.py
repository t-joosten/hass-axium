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
from homeassistant.helpers import config_validation as cv, entity_registry as er, intent

from .const import DOMAIN, ID_KEY, NAME_KEY
from .helpers import get_presets, get_sources
from .voice_sentences import (
    INTENT_ANNOUNCE,
    INTENT_PRESET,
    INTENT_SET_SOURCE,
    INTENT_SLEEP,
    LANGUAGES,
    SLEEP_MAX_MIN,
    build_language_doc,
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
def _axium_media_players(hass: HomeAssistant) -> list[str]:
    """Every Axium zone media_player entity id."""
    reg = er.async_get(hass)
    return [
        ent.entity_id
        for ent in reg.entities.values()
        if ent.platform == DOMAIN and ent.domain == "media_player"
    ]


async def _match_zones(
    intent_obj: intent.Intent, name: str | None, area: str | None
) -> list[str]:
    """Resolve the Axium zone media_players named/located by the command."""
    if not name and not area:
        return []
    constraints = intent.MatchTargetsConstraints(
        name=name,
        area_name=area,
        domains=["media_player"],
        assistant=intent_obj.assistant,
    )
    result = intent.async_match_targets(intent_obj.hass, constraints)
    if not result.is_match:
        return []
    reg = er.async_get(intent_obj.hass)
    matched = []
    for state in result.states:
        ent = reg.async_get(state.entity_id)
        if ent and ent.platform == DOMAIN:
            matched.append(state.entity_id)
    return matched


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
    description = "Route an Axium zone or area to a named audio source"
    slot_schema = {
        vol.Optional("name"): cv.string,
        vol.Optional("area"): cv.string,
        vol.Required("axium_source"): cv.string,
    }

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        hass = intent_obj.hass
        source = _slot(intent_obj, "axium_source")
        zones = await _match_zones(
            intent_obj, _slot(intent_obj, "name"), _slot(intent_obj, "area")
        )
        if not zones:
            return self._speak(intent_obj, _t(intent_obj.language, "no_zone"))
        await hass.services.async_call(
            "media_player",
            "select_source",
            {"entity_id": zones, "source": source},
            blocking=True,
        )
        return self._speak(
            intent_obj,
            _t(
                intent_obj.language,
                "set_source",
                zones=_friendly(hass, zones),
                source=source,
            ),
        )


class SleepIntent(_AxiumIntent):
    """Set a zone (or all-zones) sleep timer."""

    intent_type = INTENT_SLEEP
    description = "Set an Axium sleep timer for a zone, area, or all zones"
    slot_schema = {
        vol.Optional("name"): cv.string,
        vol.Optional("area"): cv.string,
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

        zones = await _match_zones(
            intent_obj, _slot(intent_obj, "name"), _slot(intent_obj, "area")
        )
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
        vol.Optional("name"): cv.string,
        vol.Optional("area"): cv.string,
        vol.Required("message"): cv.string,
    }

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        hass = intent_obj.hass
        message = str(_slot(intent_obj, "message")).strip()
        name = _slot(intent_obj, "name")
        area = _slot(intent_obj, "area")
        if name or area:
            zones = await _match_zones(intent_obj, name, area)
            if not zones:
                return self._speak(intent_obj, _t(intent_obj.language, "no_zone"))
        else:
            zones = _axium_media_players(hass)
            if not zones:
                return self._speak(intent_obj, _t(intent_obj.language, "no_zone"))
        # The announcement itself may take a while (play + restore); don't block
        # the spoken confirmation on it.
        await hass.services.async_call(
            DOMAIN,
            "play_notification",
            {
                "zones": zones,
                "message": message,
                "language": _lang(intent_obj.language),
            },
            blocking=False,
        )
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


@callback
def async_register_intents(hass: HomeAssistant) -> None:
    """Register the Axium voice intents (idempotent)."""
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
def _collect_names(hass: HomeAssistant) -> tuple[list[str], list[str]]:
    """Live source names and preset names across all loaded Axium entries."""
    sources: list[str] = []
    presets: list[str] = []
    controllers = hass.data.get(DOMAIN, {})
    for entry in hass.config_entries.async_entries(DOMAIN):
        controller = controllers.get(entry.entry_id)
        for src in get_sources(entry):
            name = None
            if controller is not None:
                name = controller.source_name(src[ID_KEY])
            name = name or src[NAME_KEY]
            if name and name not in sources:
                sources.append(name)
        for preset in get_presets(entry):
            if preset["name"] not in presets:
                presets.append(preset["name"])
    # A zone's live source_list also carries the per-amp stream names.
    for eid in _axium_media_players(hass):
        state = hass.states.get(eid)
        for name in (state.attributes.get("source_list") or []) if state else []:
            if name and name not in sources:
                sources.append(name)
    return sources, presets


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
    sources, presets = _collect_names(hass)
    signature = (tuple(sources), tuple(presets))
    store = hass.data.setdefault(f"{DOMAIN}_voice", {})
    if store.get("signature") == signature:
        return
    store["signature"] = signature

    changed = False
    for language in LANGUAGES:
        doc = build_language_doc(language, sources, presets)
        text = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)
        path = hass.config.path("custom_sentences", language, "axium.yaml")
        try:
            if await hass.async_add_executor_job(_write_if_changed, path, text):
                changed = True
        except OSError as err:
            LOGGER.warning("Could not write Axium voice sentences to %s: %s", path, err)

    if changed and hass.services.has_service("conversation", "reload"):
        await hass.services.async_call("conversation", "reload", {}, blocking=False)
        LOGGER.debug("Reloaded conversation agent with updated Axium sentences")
