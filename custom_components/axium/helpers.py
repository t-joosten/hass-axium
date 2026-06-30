"""Parsing and accessor helpers for Axium zone configuration.

Zones are stored as a list of ``{"zone": int, "name": str}`` dictionaries. The
UI accepts zones as a comma-separated ``number=Name`` string (the name is
optional), e.g. ``11=Kitchen, 12=Living room, 13``. Grouping is handled live on
the amplifier (native media-player grouping), not in configuration.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry

from .const import CONF_ZONES, NAME_KEY, ZONE_KEY

ZONE_MIN = 0
ZONE_MAX = 95


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


def format_zone_spec(zones: list[dict[str, Any]]) -> str:
    """Render a list of zone dicts back into the ``number=Name`` UI string."""
    return ", ".join(f"{item[ZONE_KEY]}={item[NAME_KEY]}" for item in zones)


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
