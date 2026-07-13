"""Pure builders for the Axium voice (Assist) sentence files.

Kept free of Home Assistant imports so the sentence grammar can be unit-tested
on its own. :mod:`intent.py` wraps this with the live name lookup, YAML dump and
file writing. See that module for how the built-in conversation agent consumes
these (``<config>/custom_sentences/<lang>/axium.yaml``).
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

# hassil template metacharacters — stripped from the spoken ("in") form of a
# baked list value so a source/preset name can't break the sentence grammar.
_HASSIL_SPECIALS = str.maketrans({c: " " for c in "()[]{}<>|:"})

# Sentence templates per language. Braces reference hassil lists/builtins:
# {name}/{area} are provided by the agent from exposed entities/areas;
# {axium_source}/{axium_preset}/{everywhere}/{minutes}/{message} are baked below.
_SENTENCES: dict[str, dict[str, list[str]]] = {
    "nl": {
        INTENT_SET_SOURCE: [
            "(zet|schakel|verander) {name} (op|naar) [de|het] {axium_source}",
            "(zet|schakel) [de|het] {axium_source} in {area}",
            "kies [de|het] {axium_source} (voor|in) {name}",
        ],
        INTENT_SLEEP: [
            "(zet|start|stel in) [een|de] (slaaptimer|slaap timer) "
            "(voor|in) {name} (over|van) {minutes} (minuten|minuut)",
            "laat {name} (over|na) {minutes} (minuten|minuut) slapen",
            "(zet|start|stel in) [een|de] (slaaptimer|slaap timer) "
            "(voor|in) {everywhere} (over|van) {minutes} (minuten|minuut)",
        ],
        INTENT_PRESET: [
            "(activeer|start) [de|het] preset {axium_preset}",
            "(activeer|zet) [de|het] preset {axium_preset} (op|naar) "
            "[de|het] {axium_source}",
        ],
        INTENT_ANNOUNCE: [
            "(roep om|omroepen) in {name} {message}",
            "(roep om|omroepen) {message}",
        ],
    },
    "en": {
        INTENT_SET_SOURCE: [
            "(set|switch|change) {name} to [the] {axium_source}",
            "(set|switch) [the] {axium_source} in {area}",
            "select [the] {axium_source} (for|in) {name}",
        ],
        INTENT_SLEEP: [
            "(set|start) [a|the] sleep timer (for|in) {name} "
            "(for|in) {minutes} (minutes|minute)",
            "sleep {name} (in|after) {minutes} (minutes|minute)",
            "(set|start) [a|the] sleep timer (for|in) {everywhere} "
            "(for|in) {minutes} (minutes|minute)",
        ],
        INTENT_PRESET: [
            "(activate|start|enable) [the] {axium_preset} preset",
            "(activate|start) preset {axium_preset}",
            "(activate|set) [the] {axium_preset} preset (on|to) "
            "[the] {axium_source}",
        ],
        INTENT_ANNOUNCE: [
            "(announce|broadcast) in {name} {message}",
            "(announce|broadcast) {message}",
        ],
    },
}

_EVERYWHERE = {
    "nl": "(overal|alle zones|het hele huis|alle kamers|overal in huis)",
    "en": "(everywhere|all zones|the whole house|all rooms)",
}


def _list_value(name: str) -> dict[str, str]:
    """A hassil list value: exact ``out`` name, sanitised lowercase ``in`` form."""
    spoken = " ".join(name.translate(_HASSIL_SPECIALS).lower().split())
    return {"in": spoken or name.lower(), "out": name}


def build_language_doc(
    language: str, sources: list[str], presets: list[str]
) -> dict[str, Any]:
    """Build the custom-sentences document for one language.

    Intents that need a baked list (sources, presets) are omitted when that list
    is empty, so hassil never sees a dangling ``{list}`` reference.
    """
    lang = language if language in _SENTENCES else "en"
    templates = _SENTENCES[lang]
    have_sources = bool(sources)
    have_presets = bool(presets)

    intents: dict[str, Any] = {}
    for name, sentences in templates.items():
        if name == INTENT_PRESET and not have_presets:
            continue
        if name == INTENT_SET_SOURCE and not have_sources:
            continue
        usable = [
            s
            for s in sentences
            if ("{axium_source}" not in s or have_sources)
            and ("{axium_preset}" not in s or have_presets)
        ]
        if usable:
            intents[name] = {"data": [{"sentences": usable}]}

    lists: dict[str, Any] = {
        "minutes": {"range": {"from": 0, "to": SLEEP_MAX_MIN}},
        "everywhere": {"values": [{"in": _EVERYWHERE[lang], "out": "all"}]},
        "message": {"wildcard": True},
    }
    if have_sources:
        lists["axium_source"] = {"values": [_list_value(s) for s in sources]}
    if have_presets:
        lists["axium_preset"] = {"values": [_list_value(p) for p in presets]}

    return {"language": lang, "intents": intents, "lists": lists}
