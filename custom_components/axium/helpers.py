"""Parsing and accessor helpers for Axium zone configuration.

Zones are stored as a list of ``{"zone": int, "name": str}`` dictionaries. The
UI accepts zones as a comma-separated ``number=Name`` string (the name is
optional), e.g. ``11=Kitchen, 12=Living room, 13``. Grouping is handled live on
the amplifier (native media-player grouping), not in configuration.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_ADVANCED,
    CONF_ALARMS,
    CONF_PRESETS,
    CONF_QUICKPLAY,
    CONF_SOURCES,
    CONF_UNITS,
    CONF_ZONES,
    DEFAULT_SOURCE_COUNT,
    DOMAIN,
    ID_KEY,
    NAME_KEY,
    PREAMP_ZONES_BY_MODEL,
    SOURCE_AIRPLAY_BYTE,
    SOURCE_BYTE_TO_NAME,
    SOURCE_NUMBER_TO_BYTE,
    UNIT_KEY,
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
            unit_id = None
            if isinstance(item, dict):
                zone = int(item[ZONE_KEY])
                name = str(item.get(NAME_KEY) or default_zone_name(zone))
                if item.get(UNIT_KEY) is not None:
                    unit_id = int(item[UNIT_KEY])
            else:
                zone = int(item)
                name = default_zone_name(zone)
            entry = {ZONE_KEY: zone, NAME_KEY: name}
            if unit_id is not None:
                entry[UNIT_KEY] = unit_id
            zones.append(entry)
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


def zones_from_units(
    units: list[Any], existing_zones: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """Build zone dicts (tagged with their amp's unit_id) from stack units.

    ``units`` items expose ``unit_id`` and ``zones``. Existing zone names are
    preserved so a re-scan (e.g. after stacking a second amp) keeps user names.
    """
    name_by_zone = {
        int(z[ZONE_KEY]): z.get(NAME_KEY) for z in (existing_zones or [])
    }
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    for unit in units:
        for zone in sorted(unit.zones):
            # Never emit the same zone number twice — a still-clashing stacked amp
            # would otherwise produce two entities with the same unique_id and HA
            # would drop one. Units are primary-first, so the primary's claim wins.
            if not ZONE_MIN <= zone <= ZONE_MAX or zone in seen:
                continue
            seen.add(zone)
            result.append(
                {
                    ZONE_KEY: zone,
                    NAME_KEY: name_by_zone.get(zone) or default_zone_name(zone),
                    UNIT_KEY: unit.unit_id,
                }
            )
    return sorted(result, key=lambda z: z[ZONE_KEY])


def amp_zone_positions(zones: list[dict[str, Any]]) -> dict[int, int]:
    """Map each zone id to its 1-based physical channel within its owning amp.

    The amplifier lists a stacked expansion amp's zones with stack-wide numbers
    (e.g. 9-16), but each amp's physical channels are 1..N — this recovers that
    per-amp position so the zone can be labelled "Zone 1".."Zone 8" like the
    amp's own "Amp Zone" field, regardless of stack numbering.
    """
    by_unit: dict[Any, list[int]] = {}
    for item in zones:
        by_unit.setdefault(item.get(UNIT_KEY), []).append(item[ZONE_KEY])
    positions: dict[int, int] = {}
    for nums in by_unit.values():
        # Physical channel = the zone's offset from its amp's lowest zone, so a
        # gap in the configured zones doesn't shift the mapping (a sorted index
        # would mislabel e.g. channel 7 when zone 4 is absent).
        base = min(nums)
        for zone in nums:
            positions[zone] = zone - base + 1
    return positions


def zone_type_label(model_code: int | None, amp_zone: int) -> str | None:
    """Return "Pre-out" for a physical channel that has no power amp, else None.

    Zone type isn't in the control protocol; this is the fixed per-model layout
    (see PREAMP_ZONES_BY_MODEL).
    """
    if amp_zone in PREAMP_ZONES_BY_MODEL.get(model_code, frozenset()):
        return "Pre-out"
    return None


def zone_device_model(model_code: int | None, amp_zone: int) -> str:
    """Device model/subtitle for a zone: its physical channel + any type tag."""
    label = zone_type_label(model_code, amp_zone)
    return f"Zone {amp_zone}" + (f" · {label}" if label else "")


def units_config(units: list[Any], primary_unit_id: int | None) -> list[dict[str, Any]]:
    """Build the CONF_UNITS list [{unit_id, primary}] from stack units."""
    return [
        {UNIT_KEY: unit.unit_id, "primary": unit.unit_id == primary_unit_id}
        for unit in units
    ]


def primary_amp_identifier(entry_id: str) -> tuple[str, str]:
    """Device identifier for the PRIMARY AMP device.

    Since the hub/amp split the primary amp is its own device ("…_amp_primary"),
    separate from the logical hub (whose identifier is the bare entry id). Every
    amp-hardware entity lives on this device; call this instead of rebuilding the
    literal so a future scheme change touches one place (and so a copy-paste of
    the old ``(DOMAIN, entry_id)`` idiom can't silently strand an entity on the
    hub container).
    """
    return (DOMAIN, f"{entry_id}_amp_primary")


def get_units(entry: ConfigEntry) -> list[dict[str, Any]]:
    """Return the configured stack units [{unit_id, primary}] (may be empty)."""
    raw = entry.options.get(CONF_UNITS) or entry.data.get(CONF_UNITS)
    return list(raw) if raw else []


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


def get_quickplay(entry: ConfigEntry) -> list[dict[str, Any]]:
    """Return the saved Quick Play favourites (synced across devices).

    Each item is ``{title, media_content_id, media_content_type, media_class,
    thumbnail}`` — a Music Assistant song/album/playlist the Quick Play card can
    start on an amp stream. Order is preserved (it's the button order); malformed
    entries and any without a ``media_content_id`` are dropped. Rebuilt from a
    fixed key list so unknown keys can't leak into options (same must-preserve
    discipline as ``get_alarms``).
    """
    raw = entry.options.get(CONF_QUICKPLAY, [])
    if not isinstance(raw, list):
        return []
    items: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("media_content_id", "") or "").strip()
        if not cid:
            continue
        items.append(
            {
                "title": str(item.get("title", "") or "Music"),
                "media_content_id": cid,
                "media_content_type": str(item.get("media_content_type", "") or "playlist"),
                "media_class": str(item.get("media_class", "") or ""),
                "thumbnail": str(item.get("thumbnail", "") or ""),
            }
        )
    return items


def get_alarms(entry: ConfigEntry) -> list[dict[str, Any]]:
    """Return the configured wake-to-music alarms.

    Each alarm is ``{name, time: "HH:MM", days: [0..6 Mon..Sun], zones:
    [entity_id], source: int, volume: 0..100, enabled: bool}`` plus the optional
    Music Assistant wake fields ``media``/``media_type``/``media_title``/
    ``media_player`` (empty strings when unset). Malformed entries are dropped.
    """
    raw = entry.options.get(CONF_ALARMS, [])
    if not isinstance(raw, list):
        return []
    alarms: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        time_str = str(item.get("time", "")).strip()
        zones = item.get("zones")
        if not name or ":" not in time_str or not isinstance(zones, list):
            continue
        try:
            days = [int(d) for d in item.get("days", []) if 0 <= int(d) <= 6]
            source = int(item["source"])
            volume = max(0, min(100, int(item.get("volume", 30))))
            duration = max(0, int(item.get("duration", 0) or 0))
        except (KeyError, ValueError, TypeError):
            continue
        alarms.append(
            {
                "name": name,
                "time": time_str[:5],
                "days": days,
                "zones": [str(z) for z in zones],
                "source": source,
                "volume": volume,
                "enabled": bool(item.get("enabled", True)),
                # Auto turn-off X minutes after firing (0 = stay on).
                "duration": duration,
                # Optional Music Assistant wake fields — preserved so the alarm
                # scheduler and the card can see them (dropping them here meant a
                # wake song was stored but never played).
                "media": str(item.get("media", "") or ""),
                "media_type": str(item.get("media_type", "") or ""),
                "media_title": str(item.get("media_title", "") or ""),
                "media_player": str(item.get("media_player", "") or ""),
            }
        )
    return alarms


def next_alarm_fire(alarm: dict[str, Any], now: datetime) -> datetime | None:
    """Return the next datetime an alarm will fire at or after ``now``.

    ``now`` must be timezone-aware; the result carries the same tzinfo. Empty
    ``days`` means every day. Returns None if the time can't be parsed.
    """
    try:
        hour, minute = (int(part) for part in alarm["time"].split(":")[:2])
    except (KeyError, ValueError):
        return None
    days = alarm.get("days") or list(range(7))  # empty = every day
    for offset in range(8):  # today plus a full week
        candidate_date = (now + timedelta(days=offset)).date()
        if candidate_date.weekday() not in days:
            continue
        candidate = datetime.combine(
            candidate_date, time(hour, minute), tzinfo=now.tzinfo
        )
        if candidate > now:
            return candidate
    return None


def get_sources(entry: ConfigEntry) -> list[dict[str, Any]]:
    """Return the effective source list for a config entry (options win)."""
    raw = entry.options.get(CONF_SOURCES, entry.data.get(CONF_SOURCES))
    if raw is None:
        return default_sources()
    try:
        return parse_source_spec(raw)
    except ValueError:
        return default_sources()
