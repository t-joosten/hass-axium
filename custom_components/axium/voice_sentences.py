"""Pure builders for the Axium voice (Assist) sentence files.

Kept free of Home Assistant imports so the sentence grammar can be unit-tested
on its own. :mod:`intent.py` wraps this with the live name lookup, YAML dump and
file writing. See that module for how the built-in conversation agent consumes
these (``<config>/custom_sentences/<lang>/axium.yaml``).

Zones are targeted through a **baked ``axium_zone`` list** (spoken name → zone
``entity_id``) rather than the agent's builtin ``{name}``/``{area}`` slots: those
lose to Home Assistant's own intents (a plain "zet de keuken …" gets grabbed by a
builtin, and a trailing "over N minuten" is eaten by the delayed-command feature),
whereas a fully custom-slot sentence — like the all-zones ``everywhere`` one —
matches cleanly. The trade-off is no area/alias targeting; a command names a zone.
"""

from __future__ import annotations

from typing import Any

LANGUAGES = ("nl", "en")

# Cap the sleep-timer minutes a voice command may set (matches the number entity).
SLEEP_MAX_MIN = 180

INTENT_SET_SOURCE = "AxiumSetSource"
INTENT_SLEEP = "AxiumSleep"
INTENT_PRESET = "AxiumPreset"
INTENT_ANNOUNCE = "AxiumAnnounce"

# hassil template metacharacters (so a baked name can't break the grammar) plus
# separators, all mapped to spaces so the spoken ("in") form is natural — e.g.
# "Slaapkamer - Groot" -> "slaapkamer groot", "Gang / WC" -> "gang wc".
_HASSIL_SPECIALS = str.maketrans({c: " " for c in "()[]{}<>|:-/._,"})

# Sentence templates per language. Braces reference baked hassil lists:
# {axium_zone} (spoken->entity_id), {axium_source}, {axium_preset}, {everywhere},
# {minutes} (0..SLEEP_MAX_MIN) and {message} (wildcard).
_SENTENCES: dict[str, dict[str, list[str]]] = {
    "nl": {
        INTENT_SET_SOURCE: [
            "(zet|schakel|verander) [de|het] {axium_zone} (op|naar) "
            "[de|het] {axium_source}",
            "kies [de|het] {axium_source} (voor|in) [de|het] {axium_zone}",
        ],
        INTENT_SLEEP: [
            "(zet|start|stel in) [een|de] (slaaptimer|slaap timer) voor "
            "[de|het] {axium_zone} (over|van) {minutes} (minuten|minuut)",
            "(zet|start|stel in) [een|de] (slaaptimer|slaap timer) van "
            "{minutes} (minuten|minuut) voor [de|het] {axium_zone}",
            "laat [de|het] {axium_zone} (over|na) {minutes} (minuten|minuut) slapen",
            "(zet|start|stel in) [een|de] (slaaptimer|slaap timer) voor "
            "{everywhere} (over|van) {minutes} (minuten|minuut)",
        ],
        INTENT_PRESET: [
            "(activeer|start) [de|het] preset {axium_preset}",
            "(activeer|zet) [de|het] preset {axium_preset} (op|naar) "
            "[de|het] {axium_source}",
        ],
        INTENT_ANNOUNCE: [
            "(roep om|omroepen) in [de|het] {axium_zone} {message}",
            "(roep om|omroepen) {message}",
        ],
    },
    "en": {
        INTENT_SET_SOURCE: [
            "(set|switch|change) [the] {axium_zone} to [the] {axium_source}",
            "select [the] {axium_source} (for|in) [the] {axium_zone}",
        ],
        INTENT_SLEEP: [
            "(set|start) [a|the] sleep timer for [the] {axium_zone} "
            "(for|of) {minutes} (minutes|minute)",
            "(set|start) [a|the] sleep timer of {minutes} (minutes|minute) "
            "for [the] {axium_zone}",
            "sleep [the] {axium_zone} (after|for) {minutes} (minutes|minute)",
            "(set|start) [a|the] sleep timer for {everywhere} "
            "(for|of) {minutes} (minutes|minute)",
        ],
        INTENT_PRESET: [
            "(activate|start|enable) [the] {axium_preset} preset",
            "(activate|start) preset {axium_preset}",
            "(activate|set) [the] {axium_preset} preset (on|to) "
            "[the] {axium_source}",
        ],
        INTENT_ANNOUNCE: [
            "(announce|broadcast) in [the] {axium_zone} {message}",
            "(announce|broadcast) {message}",
        ],
    },
}

_EVERYWHERE = {
    "nl": "(overal|alle zones|het hele huis|alle kamers|overal in huis)",
    "en": "(everywhere|all zones|the whole house|all rooms)",
}


def _spoken(name: str) -> str:
    """The sanitised, lowercase form a user would say for a baked name."""
    return " ".join(str(name).translate(_HASSIL_SPECIALS).lower().split())


def _list_value(name: str) -> dict[str, str]:
    """A hassil list value whose ``out`` is the exact name."""
    spoken = _spoken(name)
    return {"in": spoken or name.lower(), "out": name}


def build_language_doc(
    language: str,
    zones: list[tuple[str, str]],
    sources: list[str],
    presets: list[str],
) -> dict[str, Any]:
    """Build the custom-sentences document for one language.

    ``zones`` is a list of ``(spoken_name, entity_id)`` pairs; the ``entity_id`` is
    baked as the slot ``out`` so a handler gets the target directly. Intents whose
    baked list is empty are omitted so hassil never sees a dangling ``{list}``.
    """
    lang = language if language in _SENTENCES else "en"
    templates = _SENTENCES[lang]
    have_zones = bool(zones)
    have_sources = bool(sources)
    have_presets = bool(presets)

    def _usable(sentence: str) -> bool:
        return (
            ("{axium_zone}" not in sentence or have_zones)
            and ("{axium_source}" not in sentence or have_sources)
            and ("{axium_preset}" not in sentence or have_presets)
        )

    intents: dict[str, Any] = {}
    for name, sentences in templates.items():
        if name == INTENT_PRESET and not have_presets:
            continue
        if name == INTENT_SET_SOURCE and not (have_zones and have_sources):
            continue
        usable = [s for s in sentences if _usable(s)]
        if usable:
            intents[name] = {"data": [{"sentences": usable}]}

    lists: dict[str, Any] = {
        "minutes": {"range": {"from": 0, "to": SLEEP_MAX_MIN}},
        "everywhere": {"values": [{"in": _EVERYWHERE[lang], "out": "all"}]},
        "message": {"wildcard": True},
    }
    if have_zones:
        lists["axium_zone"] = {
            "values": [
                {"in": spoken, "out": entity_id} for spoken, entity_id in zones
            ]
        }
    if have_sources:
        lists["axium_source"] = {"values": [_list_value(s) for s in sources]}
    if have_presets:
        lists["axium_preset"] = {"values": [_list_value(p) for p in presets]}

    return {"language": lang, "intents": intents, "lists": lists}
