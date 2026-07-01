"""Parsing and accessor helpers for Axium zone configuration.

Zones are stored as a list of ``{"zone": int, "name": str}`` dictionaries. The
UI accepts zones as a comma-separated ``number=Name`` string (the name is
optional), e.g. ``11=Kitchen, 12=Living room, 13``. Grouping is handled live on
the amplifier (native media-player grouping), not in configuration.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_ADVANCED,
    CONF_PRESETS,
    CONF_SOURCES,
    CONF_ZONES,
    DEFAULT_SOURCE_COUNT,
    ID_KEY,
    NAME_KEY,
    SOURCE_AIRPLAY_BYTE,
    SOURCE_BYTE_TO_NAME,
    SOURCE_NUMBER_TO_BYTE,
    ZONE_KEY,
)

ZONE_MIN = 0
ZONE_MAX = 95
SOURCE_ID_MIN = 0
SOURCE_ID_MAX = 63


def default_zone_name(zone: int) -> str:
    """Return the fallback name for a zone with no explicit label."""
    return f"Zone {zone}"


def parse_zone_spec(raw: Any) -> list[dict[str, Any]]:
    """Normalise a zone specification into a sorted list of zone dicts.

    Accepts the UI ``number=Name`` string form, a list of ints (legacy), or a
    list of ``{"zone", "name"}`` dicts. Raises ``ValueError`` on invalid input.
    """
    zones: list[dict[str, Any]] = []

    if isinstance(raw, str):
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            if "=" in part:
                number_text, name = part.split("=", 1)
                zone = int(number_text.strip())
                name = name.strip() or default_zone_name(zone)
            else:
                zone = int(part)
                name = default_zone_name(zone)
            zones.append({ZONE_KEY: zone, NAME_KEY: name})
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                zone = int(item[ZONE_KEY])
                name = str(item.get(NAME_KEY) or default_zone_name(zone))
            else:
                zone = int(item)
                name = default_zone_name(zone)
            zones.append({ZONE_KEY: zone, NAME_KEY: name})
    else:
        raise ValueError("unsupported zone specification")

    seen: set[int] = set()
    for item in zones:
        zone = item[ZONE_KEY]
        if not ZONE_MIN <= zone <= ZONE_MAX:
            raise ValueError(f"zone {zone} out of range {ZONE_MIN}..{ZONE_MAX}")
        if zone in seen:
            raise ValueError(f"duplicate zone {zone}")
        seen.add(zone)

    if not zones:
        raise ValueError("no zones specified")

    return sorted(zones, key=lambda item: item[ZONE_KEY])


def zones_from_numbers(numbers: list[int]) -> list[dict[str, Any]]:
    """Build default-named zone dicts from a list of zone numbers."""
    unique = sorted({int(n) for n in numbers if ZONE_MIN <= int(n) <= ZONE_MAX})
    return [{ZONE_KEY: n, NAME_KEY: default_zone_name(n)} for n in unique]


def get_zones(entry: ConfigEntry) -> list[dict[str, Any]]:
    """Return the effective zone list for a config entry (options win)."""
    raw = entry.options.get(CONF_ZONES, entry.data.get(CONF_ZONES))
    if raw is None:
        return []
    try:
        return parse_zone_spec(raw)
    except ValueError:
        return []


def default_source_name(source_id: int) -> str:
    """Return a readable default name for a source data byte."""
    return SOURCE_BYTE_TO_NAME.get(source_id, f"Source {source_id}")


def default_sources() -> list[dict[str, Any]]:
    """Return the fallback source list (S1..S8 plus AirPlay)."""
    sources = [
        {ID_KEY: SOURCE_NUMBER_TO_BYTE[n], NAME_KEY: f"Source {n}"}
        for n in range(1, DEFAULT_SOURCE_COUNT + 1)
    ]
    sources.append({ID_KEY: SOURCE_AIRPLAY_BYTE, NAME_KEY: "AirPlay"})
    return sources


def parse_source_spec(raw: Any) -> list[dict[str, Any]]:
    """Normalise a source specification into a list of source dicts.

    Accepts the UI ``id=Name`` string form or a list of ``{"id", "name"}``
    dicts. Order is preserved; duplicate ids raise. Raises ``ValueError`` on
    invalid input.
    """
    sources: list[dict[str, Any]] = []
    if isinstance(raw, str):
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            if "=" in part:
                id_text, name = part.split("=", 1)
                source_id = int(id_text.strip())
                name = name.strip() or default_source_name(source_id)
            else:
                source_id = int(part)
                name = default_source_name(source_id)
            sources.append({ID_KEY: source_id, NAME_KEY: name})
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                source_id = int(item[ID_KEY])
                name = str(item.get(NAME_KEY) or default_source_name(source_id))
            else:
                source_id = int(item)
                name = default_source_name(source_id)
            sources.append({ID_KEY: source_id, NAME_KEY: name})
    else:
        raise ValueError("unsupported source specification")

    seen: set[int] = set()
    for item in sources:
        source_id = item[ID_KEY]
        if not SOURCE_ID_MIN <= source_id <= SOURCE_ID_MAX:
            raise ValueError(f"source id {source_id} out of range")
        if source_id in seen:
            raise ValueError(f"duplicate source id {source_id}")
        seen.add(source_id)

    if not sources:
        raise ValueError("no sources specified")

    return sources


def sources_from_detection(detected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build a source list from detected sources, keeping only enabled ones."""
    sources: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in detected:
        source_id = int(item[ID_KEY])
        if not item.get("enabled", True) or source_id in seen:
            continue
        seen.add(source_id)
        name = str(item.get(NAME_KEY) or "").strip() or default_source_name(source_id)
        sources.append({ID_KEY: source_id, NAME_KEY: name})
    return sources


def get_advanced(entry: ConfigEntry) -> bool:
    """Return whether advanced (risky level/gain) controls are enabled."""
    return bool(entry.options.get(CONF_ADVANCED, False))


def get_presets(entry: ConfigEntry) -> list[dict[str, Any]]:
    """Return the configured zone presets as ``[{"name", "zones": [...]}]``.

    Each preset names a set of zone media_player entity_ids that a source card
    can activate at once. Malformed entries are dropped.
    """
    raw = entry.options.get(CONF_PRESETS, [])
    if not isinstance(raw, list):
        return []
    presets: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        zones = item.get("zones")
        if not name or not isinstance(zones, list):
            continue
        presets.append({"name": name, "zones": [str(z) for z in zones]})
    return presets


def get_sources(entry: ConfigEntry) -> list[dict[str, Any]]:
    """Return the effective source list for a config entry (options win)."""
    raw = entry.options.get(CONF_SOURCES, entry.data.get(CONF_SOURCES))
    if raw is None:
        return default_sources()
    try:
        return parse_source_spec(raw)
    except ValueError:
        return default_sources()
